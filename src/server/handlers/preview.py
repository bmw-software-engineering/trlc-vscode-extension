# *******************************************************************************
# TRLC VSCode Extension
# Copyright (C) 2026 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
#
# This file is part of the TRLC VSCode Extension.
#
# The TRLC VSCode Extension is free software: you can redistribute it
# and/or modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation, either version 3 of
# the License, or (at your option) any later version.
#
# The TRLC VSCode Extension is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty
# of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with TRLC. If not, see <https://www.gnu.org/licenses/>.
# *******************************************************************************

"""Custom ``trlc/preview*`` requests: feed the client's TRLC preview.

``trlc/previewModel`` returns the model of a parsed file; the edit
requests return a ``WorkspaceEdit`` (or an error message) that the client
applies, so the text buffer stays the single source of truth. Requests
that address sections by id (add record, the structure requests) carry
the ``modelVersion`` they were made from and are refused as outdated once
the parse has moved on.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from lsprotocol.converters import get_converter
from lsprotocol.types import (
    OptionalVersionedTextDocumentIdentifier,
    TextDocumentEdit,
    WorkspaceEdit,
)

from ..parse_guard import normalize_fs_path
from ..preview_edits import (
    PreviewEditError,
    add_record_edits,
    set_field_edits,
)
from ..preview_model import build_preview_model, unavailable_model
from ..preview_scan import scan_file
from ..preview_structure import (
    ItemRef,
    dissolve_section_edits,
    insert_section_edits,
    move_edits,
    rename_section_edits,
)
from ..server_protocol import ServerProtocol
from ..token_utils import path_from_uri

PREVIEW_MODEL_METHOD = "trlc/previewModel"
PREVIEW_SET_FIELD_METHOD = "trlc/previewSetField"
PREVIEW_ADD_RECORD_METHOD = "trlc/previewAddRecord"
PREVIEW_INSERT_SECTION_METHOD = "trlc/previewInsertSection"
PREVIEW_RENAME_SECTION_METHOD = "trlc/previewRenameSection"
PREVIEW_DISSOLVE_SECTION_METHOD = "trlc/previewDissolveSection"
PREVIEW_MOVE_METHOD = "trlc/previewMove"

_NO_SCOPE = "File is not part of an active TRLC scope."
_NOT_PARSED = "File has errors that prevent parsing; fix them in the editor."
_INVALID = "Invalid request."
_STALE = "Waiting for the parser to catch up with your last edit."
_OUTDATED = "The preview was out of date; try again."
_INTERNAL = "Internal error; see the TRLC output channel."
#: Machine-readable code of the stale rejection: the client retries it.
STALE_CODE = "stale"
#: Machine-readable code of an id-based request made from an older model
#: than the parse (section ids renumber after structural edits).
OUTDATED_CODE = "outdated"

LOGGER = logging.getLogger(__name__)

#: TRLC kind (``Diagnostic.data["kind"]``, set by Vscode_Message_Handler)
#: of the errors that lock the preview: syntax and semantic errors. Failing
#: user checks (USER_ERROR) do not lock.
_LOCKING_KIND = "SYS_ERROR"
#: Stable prefix of TRLC's "required component X (see ...) is not defined"
#: error; a record missing a required value must stay editable so the
#: preview can fill it in.
REQUIRED_COMPONENT_PREFIX = "required component "

_CONVERTER = get_converter()


@dataclass
class PreviewModelParams:
    """Params for :data:`PREVIEW_MODEL_METHOD`."""

    uri: str


@dataclass
class PreviewSetFieldParams:
    """Params for :data:`PREVIEW_SET_FIELD_METHOD`; ``value=None`` unsets."""

    uri: str
    record: str
    field: str
    value: Optional[str] = None


@dataclass
class PreviewAddRecordParams:
    """Params for :data:`PREVIEW_ADD_RECORD_METHOD`."""

    uri: str
    type: str
    name: str
    # camelCase on purpose: pygls hands custom-method params over as the
    # raw JSON object (attribute access by wire key, no snake_case
    # conversion), and keys that are absent are simply missing.
    sectionId: Optional[int] = None  # pylint: disable=invalid-name
    #: ``version`` of the model the request was made from (required).
    modelVersion: Optional[int] = None  # pylint: disable=invalid-name


@dataclass
class _Target:
    """A parsed file the preview can show, and whether it may be edited."""

    parsed: Any
    #: Version of the live buffer the parse matches (None: not open).
    version: Optional[int]
    #: True if the parse has errors that lock the preview.
    locked: bool


def _locks(diag) -> bool:
    data = diag.data if isinstance(diag.data, dict) else {}
    return data.get("kind") == _LOCKING_KIND and not diag.message.startswith(
        REQUIRED_COMPONENT_PREFIX
    )


def _has_locking_error(diagnostics: dict, path: str) -> bool:
    """True if *diagnostics* hold a locking error for the file at *path*."""
    for key, diags in diagnostics.items():
        if normalize_fs_path(path_from_uri(key)) == path and any(
            _locks(d) for d in diags
        ):
            return True
    return False


def _resolve(ls, uri: str):
    """Return ``(_Target, None)`` or ``(None, (reason, code))``."""
    context = ls.get_context_for_uri(uri)
    if context is None:
        return None, (_NO_SCOPE, None)
    # One snapshot: the parse and its diagnostics belong together.
    snapshot = context.snapshot()
    path = normalize_fs_path(path_from_uri(uri))
    parsed = snapshot.vsm.all_files.get(path)
    if parsed is None:
        return None, (_NOT_PARSED, None)
    try:
        document = ls.workspace.get_text_document(uri)
    except OSError:
        version = None  # no live buffer to compare against; trust the AST
    else:
        if parsed.lexer.content != document.source:
            return None, (_STALE, STALE_CODE)
        version = document.version
    locked = _has_locking_error(snapshot.diagnostics, path)
    return _Target(parsed, version, locked), None


def _error(reason: str, code: Optional[str] = None) -> dict:
    result = {"error": reason}
    if code is not None:
        result["code"] = code
    return result


def _edit_result(uri: str, version: Optional[int], edits) -> dict:
    # Versioned: the edits are only valid against the buffer version the
    # parse matched, so a client must reject them once the buffer moved on.
    edit = WorkspaceEdit(
        document_changes=[
            TextDocumentEdit(
                text_document=OptionalVersionedTextDocumentIdentifier(
                    uri=uri, version=version
                ),
                edits=edits,
            )
        ]
    )
    return {"edit": _CONVERTER.unstructure(edit)}


class _InvalidRequest(Exception):
    """Raised by the param readers for malformed request params."""


def _int_or_none(params, name: str, required: bool = True):
    """Read an ``int | None`` param (bool is not an int here)."""
    if required and not hasattr(params, name):
        raise _InvalidRequest()
    value = getattr(params, name, None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _InvalidRequest()
    return value


def _title(params) -> str:
    value = getattr(params, "title", None)
    if not isinstance(value, str):
        raise _InvalidRequest()
    return value


def _item_ref(raw) -> ItemRef:
    """Convert a wire ``ItemRefJson`` to an :class:`ItemRef`."""
    kind = getattr(raw, "kind", None)
    if kind == "record":
        name = getattr(raw, "name", None)
        if not isinstance(name, str):
            raise _InvalidRequest()
        return ItemRef("record", name=name)
    if kind == "section":
        section_id = _int_or_none(raw, "sectionId")
        if section_id is None:
            raise _InvalidRequest()
        return ItemRef("section", section_id=section_id)
    raise _InvalidRequest()


def _structure_request(ls, params, build) -> dict:
    """Shared path of the id-based requests (structure and add-record).

    *params.modelVersion* is the ``version`` of the model the request was
    made from: section ids are ordinals that renumber after structural
    edits, so a request made from another version is refused as outdated.
    *build(structure, parsed)* reads the remaining params and returns the
    edits; it may raise :class:`_InvalidRequest` or
    :class:`PreviewEditError`.
    """
    try:
        return _id_based_request(ls, params, build)
    # pylint: disable-next=broad-exception-caught  # answer with a message; the trace goes to the log
    except Exception:
        LOGGER.exception("Preview request failed")
        return _error(_INTERNAL)


def _id_based_request(ls, params, build) -> dict:
    uri = getattr(params, "uri", None)
    try:
        if not isinstance(uri, str):
            raise _InvalidRequest()
        model_version = _int_or_none(params, "modelVersion")
        target, failure = _resolve(ls, uri)
        if target is None:
            return _error(*failure)
        if model_version != target.version:
            failure = (_OUTDATED, OUTDATED_CODE)
        elif target.locked:
            failure = (_NOT_PARSED, None)
        if failure is not None:
            return _error(*failure)
        parsed = target.parsed
        edits = build(scan_file(parsed.lexer.tokens), parsed)
    except _InvalidRequest:
        return _error(_INVALID)
    except PreviewEditError as e:
        return _error(str(e))
    return _edit_result(uri, target.version, edits)


def _model(ls, uri: str, target: _Target) -> dict:
    markdown = ls.get_config_for_uri(uri).preview_markdown_components
    if not target.locked:
        model = build_preview_model(uri, target.parsed, markdown)
    else:
        try:
            model = build_preview_model(uri, target.parsed, markdown)
        # pylint: disable-next=broad-exception-caught  # error-recovered ASTs can be arbitrarily odd; degrade to "unavailable"
        except Exception:
            return unavailable_model(uri, _NOT_PARSED)
        model["editable"] = False
        model["reason"] = _NOT_PARSED
    # The buffer version the parse matches: id-based requests send it back.
    model["version"] = target.version
    return model


def register(server: "ServerProtocol") -> None:
    """Register the ``trlc/preview*`` requests on *server*."""

    @server.feature(PREVIEW_MODEL_METHOD)
    def preview_model(ls, params: PreviewModelParams):
        """Model of *params.uri*, or an "unavailable" model with a reason."""
        target, failure = _resolve(ls, params.uri)
        if target is None:
            return unavailable_model(params.uri, failure[0])
        return _model(ls, params.uri, target)

    @server.feature(PREVIEW_SET_FIELD_METHOD)
    def preview_set_field(ls, params: PreviewSetFieldParams):
        """Edit that sets/unsets a record field, or ``{"error": ...}``."""
        target, failure = _resolve(ls, params.uri)
        if target is None:
            return _error(*failure)
        if target.locked:
            return _error(_NOT_PARSED)
        parsed = target.parsed
        try:
            edits = set_field_edits(
                scan_file(parsed.lexer.tokens),
                parsed.lexer.content,
                parsed.cu.package,
                params.record,
                params.field,
                getattr(params, "value", None),
                ls.get_config_for_uri(params.uri).preview_markdown_components,
            )
        except PreviewEditError as e:
            return _error(str(e))
        return _edit_result(params.uri, target.version, edits)

    @server.feature(PREVIEW_ADD_RECORD_METHOD)
    def preview_add_record(ls, params: PreviewAddRecordParams):
        """Edit that adds a record, or ``{"error": ...}``."""

        def build(structure, parsed):
            return add_record_edits(
                structure,
                parsed.lexer.content,
                parsed.cu,
                getattr(params, "sectionId", None),
                params.type,
                params.name,
            )

        return _structure_request(ls, params, build)

    @server.feature(PREVIEW_INSERT_SECTION_METHOD)
    def preview_insert_section(ls, params):
        """Edit that inserts a section, or ``{"error": ...}``."""

        def build(structure, parsed):
            return insert_section_edits(
                structure,
                parsed.lexer.content,
                _int_or_none(params, "sectionId"),
                _title(params),
            )

        return _structure_request(ls, params, build)

    @server.feature(PREVIEW_RENAME_SECTION_METHOD)
    def preview_rename_section(ls, params):
        """Edit that renames a section, or ``{"error": ...}``."""

        def build(structure, parsed):
            section_id = _int_or_none(params, "sectionId")
            if section_id is None:
                raise _InvalidRequest()
            return rename_section_edits(
                structure, parsed.lexer.content, section_id, _title(params)
            )

        return _structure_request(ls, params, build)

    @server.feature(PREVIEW_DISSOLVE_SECTION_METHOD)
    def preview_dissolve_section(ls, params):
        """Edit that dissolves a section, or ``{"error": ...}``."""

        def build(structure, parsed):
            section_id = _int_or_none(params, "sectionId")
            if section_id is None:
                raise _InvalidRequest()
            return dissolve_section_edits(
                structure, parsed.lexer.content, section_id
            )

        return _structure_request(ls, params, build)

    @server.feature(PREVIEW_MOVE_METHOD)
    def preview_move(ls, params):
        """Edit that moves a record/section, or ``{"error": ...}``."""

        def build(structure, parsed):
            item = _item_ref(getattr(params, "item", None))
            target_id = _int_or_none(params, "targetSectionId")
            raw_before = getattr(params, "before", None)
            before = None if raw_before is None else _item_ref(raw_before)
            return move_edits(
                structure, parsed.lexer.content, item, target_id, before
            )

        return _structure_request(ls, params, build)
