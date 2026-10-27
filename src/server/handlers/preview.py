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

``trlc/previewModel`` returns the model of a parsed file;
``trlc/previewSetField``/``trlc/previewAddRecord`` return a
``WorkspaceEdit`` (or an error message) that the client applies, so the
text buffer stays the single source of truth.
"""

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
from ..server_protocol import ServerProtocol
from ..token_utils import path_from_uri

PREVIEW_MODEL_METHOD = "trlc/previewModel"
PREVIEW_SET_FIELD_METHOD = "trlc/previewSetField"
PREVIEW_ADD_RECORD_METHOD = "trlc/previewAddRecord"

_NO_SCOPE = "File is not part of an active TRLC scope."
_NOT_PARSED = "File has errors that prevent parsing; fix them in the editor."
_STALE = "Waiting for the parser to catch up with your last edit."
#: Machine-readable code of the stale rejection: the client retries it.
STALE_CODE = "stale"

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


def _model(uri: str, target: _Target) -> dict:
    if not target.locked:
        return build_preview_model(uri, target.parsed)
    try:
        model = build_preview_model(uri, target.parsed)
    # pylint: disable-next=broad-exception-caught  # error-recovered ASTs can be arbitrarily odd; degrade to "unavailable"
    except Exception:
        return unavailable_model(uri, _NOT_PARSED)
    model["editable"] = False
    model["reason"] = _NOT_PARSED
    return model


def register(server: "ServerProtocol") -> None:
    """Register the ``trlc/preview*`` requests on *server*."""

    @server.feature(PREVIEW_MODEL_METHOD)
    def preview_model(ls, params: PreviewModelParams):
        """Model of *params.uri*, or an "unavailable" model with a reason."""
        target, failure = _resolve(ls, params.uri)
        if target is None:
            return unavailable_model(params.uri, failure[0])
        return _model(params.uri, target)

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
            )
        except PreviewEditError as e:
            return _error(str(e))
        return _edit_result(params.uri, target.version, edits)

    @server.feature(PREVIEW_ADD_RECORD_METHOD)
    def preview_add_record(ls, params: PreviewAddRecordParams):
        """Edit that adds a record, or ``{"error": ...}``."""
        target, failure = _resolve(ls, params.uri)
        if target is None:
            return _error(*failure)
        if target.locked:
            return _error(_NOT_PARSED)
        parsed = target.parsed
        try:
            edits = add_record_edits(
                scan_file(parsed.lexer.tokens),
                parsed.lexer.content,
                parsed.cu,
                getattr(params, "sectionId", None),
                params.type,
                params.name,
            )
        except PreviewEditError as e:
            return _error(str(e))
        return _edit_result(params.uri, target.version, edits)
