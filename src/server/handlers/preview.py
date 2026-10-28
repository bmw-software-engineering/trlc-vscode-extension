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
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from lsprotocol.converters import get_converter
from lsprotocol.types import (
    OptionalVersionedTextDocumentIdentifier,
    TextDocumentEdit,
    WorkspaceEdit,
)

from ..missing_symbols import declaring_files, scope_label
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
from ..token_utils import path_from_uri, uri_from_file

PREVIEW_MODEL_METHOD = "trlc/previewModel"
PREVIEW_SET_FIELD_METHOD = "trlc/previewSetField"
PREVIEW_ADD_RECORD_METHOD = "trlc/previewAddRecord"
PREVIEW_INSERT_SECTION_METHOD = "trlc/previewInsertSection"
PREVIEW_RENAME_SECTION_METHOD = "trlc/previewRenameSection"
PREVIEW_DISSOLVE_SECTION_METHOD = "trlc/previewDissolveSection"
PREVIEW_MOVE_METHOD = "trlc/previewMove"

_NO_SCOPE = "File is not part of an active TRLC scope."
_NOT_PARSED = "File has errors that prevent parsing; fix them in the editor."
_NOT_OPEN = (
    "Records this file uses are declared in .trlc files that are not "
    'open. In parse mode "workspace" only open .trlc files are parsed: '
    'open them, or set trlcServer.parseMode to "repo".'
)
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
    """Params for :data:`PREVIEW_SET_FIELD_METHOD`.

    ``value=None`` unsets; array fields take a list of element inputs.
    """

    uri: str
    record: str
    field: str
    value: Optional[Union[str, "list[str]"]] = None


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
    #: The errors of the file that lock the preview (none: editable).
    locking: list = field(default_factory=list)

    @property
    def locked(self) -> bool:
        """Tell whether the preview is locked.

        Returns:
            True if the parse has errors that lock the preview.
        """
        return bool(self.locking)


def _locks(diag) -> bool:
    """Tell whether a diagnostic is one of the errors that lock the preview.

    Only syntax and semantic errors (``SYS_ERROR``) lock; a missing
    required component does not, so the preview can still fill it in.

    Args:
        diag: The LSP diagnostic; its ``data`` dict carries the TRLC kind.

    Returns:
        True if *diag* locks the preview.
    """
    data = diag.data if isinstance(diag.data, dict) else {}
    return data.get("kind") == _LOCKING_KIND and not diag.message.startswith(
        REQUIRED_COMPONENT_PREFIX
    )


def _locking_errors(diagnostics: dict, path: str) -> list:
    """The errors in *diagnostics* that lock the file at *path*.

    Args:
        diagnostics: Diagnostics of the scope, keyed by file URI.
        path: Normalized filesystem path of the file to inspect.

    Returns:
        The locking diagnostics that belong to *path* (empty: editable).
    """
    return [
        d
        for key, diags in diagnostics.items()
        if normalize_fs_path(path_from_uri(key)) == path
        for d in diags
        if _locks(d)
    ]


def _resolve(ls, uri: str):
    """Return ``(_Target, None)`` or ``(None, (reason, code))``.

    Args:
        ls: The language server.
        uri: URI of the file the request is about.

    Returns:
        ``(target, None)`` on success. Otherwise ``(None, (reason, code))``
        with a user-facing reason and a machine-readable code (``None``
        unless the parse is stale, see :data:`STALE_CODE`).
    """
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
    locking = _locking_errors(snapshot.diagnostics, path)
    return _Target(parsed, version, locking), None


def _error(reason: str, code: Optional[str] = None) -> dict:
    """Build the error result of a preview request.

    Args:
        reason: User-facing message.
        code: Optional machine-readable code the client reacts to.

    Returns:
        ``{"error": reason}``, plus ``"code"`` if *code* is given.
    """
    result = {"error": reason}
    if code is not None:
        result["code"] = code
    return result


def _edit_result(uri: str, version: Optional[int], edits) -> dict:
    """Wrap text edits into the success result of an edit request.

    Args:
        uri: URI of the edited document.
        version: Buffer version the edits are valid against (None: the
            document is not open).
        edits: The ``TextEdit`` list to apply.

    Returns:
        ``{"edit": <serialized WorkspaceEdit>}``.
    """
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


class _InvalidRequestError(Exception):
    """Raised by the param readers for malformed request params."""


def _int_or_none(params, name: str, required: bool = True):
    """Read an ``int | None`` param (bool is not an int here).

    Args:
        params: The raw request params object.
        name: Wire key of the param.
        required: If True, the key must be present (it may still be null).

    Returns:
        The integer value, or None if the key is null (or absent and not
        required).

    Raises:
        _InvalidRequestError: When a required key is missing or the value
            is neither an int nor None.
    """
    if required and not hasattr(params, name):
        raise _InvalidRequestError()
    value = getattr(params, name, None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _InvalidRequestError()
    return value


def _title(params) -> str:
    """Read the mandatory ``title`` string param.

    Args:
        params: The raw request params object.

    Returns:
        The title.

    Raises:
        _InvalidRequestError: When ``title`` is missing or not a string.
    """
    value = getattr(params, "title", None)
    if not isinstance(value, str):
        raise _InvalidRequestError()
    return value


def _item_ref(raw) -> ItemRef:
    """Convert a wire ``ItemRefJson`` to an :class:`ItemRef`.

    Args:
        raw: The raw item object with ``kind`` (``"record"`` or
            ``"section"``) and ``name`` or ``sectionId`` respectively.

    Returns:
        The matching record or section reference.

    Raises:
        _InvalidRequestError: When the object is missing, has an unknown
            kind or lacks/mistypes the field its kind needs.
    """
    kind = getattr(raw, "kind", None)
    if kind == "record":
        name = getattr(raw, "name", None)
        if not isinstance(name, str):
            raise _InvalidRequestError()
        return ItemRef("record", name=name)
    if kind == "section":
        section_id = _int_or_none(raw, "sectionId")
        if section_id is None:
            raise _InvalidRequestError()
        return ItemRef("section", section_id=section_id)
    raise _InvalidRequestError()


def _structure_request(ls, params, build) -> dict:
    """Shared path of the id-based requests (structure and add-record).

    *params.modelVersion* is the ``version`` of the model the request was
    made from: section ids are ordinals that renumber after structural
    edits, so a request made from another version is refused as outdated.
    *build(structure, parsed)* reads the remaining params and returns the
    edits; it may raise :class:`_InvalidRequestError` or
    :class:`PreviewEditError`.

    Unexpected exceptions are logged and answered with an internal error,
    so the client always gets a result.

    Args:
        ls: The language server.
        params: The raw request params object (needs ``uri`` and
            ``modelVersion``).
        build: Callback ``build(structure, parsed)`` that reads the
            remaining params and returns the text edits.

    Returns:
        The ``{"edit": ...}`` result, or ``{"error": ..., "code": ...}``
        (``code`` only for stale and outdated rejections).
    """
    try:
        return _id_based_request(ls, params, build)
    # pylint: disable-next=broad-exception-caught  # answer with a message; the trace goes to the log
    except Exception:
        LOGGER.exception("Preview request failed")
        return _error(_INTERNAL)


def _id_based_request(ls, params, build) -> dict:
    """Validate, resolve and run an id-based request.

    Args:
        ls: The language server.
        params: The raw request params object (needs ``uri`` and
            ``modelVersion``).
        build: Callback ``build(structure, parsed)`` returning the edits.

    Returns:
        The ``{"edit": ...}`` result, or ``{"error": ..., "code": ...}``
        for an invalid, stale, outdated or locked request and for a
        :class:`PreviewEditError` raised by *build*. An
        :class:`_InvalidRequestError` from the param readers or *build*
        is caught here and answered as an invalid request.
    """
    uri = getattr(params, "uri", None)
    try:
        if not isinstance(uri, str):
            raise _InvalidRequestError()
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
    except _InvalidRequestError:
        return _error(_INVALID)
    except PreviewEditError as e:
        return _error(str(e))
    return _edit_result(uri, target.version, edits)


def _unopened_declarations(ls, uri: str, target: _Target) -> list:
    """``openFiles`` entries for unknown symbols declared in closed files.

    Args:
        ls: The language server.
        uri: URI of the previewed file.
        target: The resolved target, whose locking errors name the symbols.

    Returns:
        One ``{"uri", "label", "symbols"}`` dict per closed file that
        declares unknown symbols (empty if there is none).
    """
    found = declaring_files(
        ls, uri, target.parsed.lexer.content, target.locking
    )
    return [
        {
            "uri": uri_from_file(path),
            "label": scope_label(ls, uri, path),
            "symbols": names,
        }
        for path, names in found.items()
    ]


def _model(ls, uri: str, target: _Target) -> dict:
    """Build the preview model of a resolved target.

    A locked target yields a read-only model with a reason (and the
    ``openFiles`` hint if closed files declare the unknown symbols), or an
    "unavailable" model if the broken AST cannot be modelled.

    Args:
        ls: The language server.
        uri: URI of the previewed file.
        target: The resolved target.

    Returns:
        The model dict, including the buffer ``version``.
    """
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
        open_files = _unopened_declarations(ls, uri, target)
        if open_files:
            model["reason"] = _NOT_OPEN
            model["openFiles"] = open_files
    # The buffer version the parse matches: id-based requests send it back.
    model["version"] = target.version
    return model


def register(server: "ServerProtocol") -> None:
    """Register the ``trlc/preview*`` requests on *server*.

    Args:
        server: The language server to register the requests on.
    """

    @server.feature(PREVIEW_MODEL_METHOD)
    def preview_model(ls, params: PreviewModelParams):
        """Model of *params.uri*, or an "unavailable" model with a reason.

        Args:
            ls: The language server.
            params: Request params; ``uri`` is the file to preview.

        Returns:
            The preview model dict.
        """
        target, failure = _resolve(ls, params.uri)
        if target is None:
            return unavailable_model(params.uri, failure[0])
        return _model(ls, params.uri, target)

    @server.feature(PREVIEW_SET_FIELD_METHOD)
    def preview_set_field(ls, params: PreviewSetFieldParams):
        """Edit that sets/unsets a record field, or ``{"error": ...}``.

        Args:
            ls: The language server.
            params: Request params: ``uri``, ``record``, ``field`` and
                optionally ``value`` (None unsets, a list for arrays).

        Returns:
            ``{"edit": ...}``, or ``{"error": ..., "code": ...}`` if the
            file is not resolvable, locked or the edit is refused.
        """
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
        """Edit that adds a record, or ``{"error": ...}``.

        Args:
            ls: The language server.
            params: Request params: ``uri``, ``modelVersion``, ``type``,
                ``name`` and optionally ``sectionId``.

        Returns:
            ``{"edit": ...}`` or ``{"error": ..., "code": ...}``.
        """

        def build(structure, parsed):
            """Compute the edits that add the record.

            Args:
                structure: Scanned structure of the file.
                parsed: The parsed file.

            Returns:
                The text edits.
            """
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
        """Edit that inserts a section, or ``{"error": ...}``.

        Args:
            ls: The language server.
            params: Request params: ``uri``, ``modelVersion``, ``title``
                and ``sectionId`` (the parent section, or null).

        Returns:
            ``{"edit": ...}`` or ``{"error": ..., "code": ...}``.
        """

        def build(structure, parsed):
            """Compute the edits that insert the section.

            Args:
                structure: Scanned structure of the file.
                parsed: The parsed file.

            Returns:
                The text edits.

            Raises:
                _InvalidRequestError: When ``sectionId`` or ``title`` is
                    malformed.
            """
            return insert_section_edits(
                structure,
                parsed.lexer.content,
                _int_or_none(params, "sectionId"),
                _title(params),
            )

        return _structure_request(ls, params, build)

    @server.feature(PREVIEW_RENAME_SECTION_METHOD)
    def preview_rename_section(ls, params):
        """Edit that renames a section, or ``{"error": ...}``.

        Args:
            ls: The language server.
            params: Request params: ``uri``, ``modelVersion``,
                ``sectionId`` and ``title``.

        Returns:
            ``{"edit": ...}`` or ``{"error": ..., "code": ...}``.
        """

        def build(structure, parsed):
            """Compute the edits that rename the section.

            Args:
                structure: Scanned structure of the file.
                parsed: The parsed file.

            Returns:
                The text edits.

            Raises:
                _InvalidRequestError: When ``sectionId`` is missing or null
                    or ``title`` is malformed.
            """
            section_id = _int_or_none(params, "sectionId")
            if section_id is None:
                raise _InvalidRequestError()
            return rename_section_edits(
                structure, parsed.lexer.content, section_id, _title(params)
            )

        return _structure_request(ls, params, build)

    @server.feature(PREVIEW_DISSOLVE_SECTION_METHOD)
    def preview_dissolve_section(ls, params):
        """Edit that dissolves a section, or ``{"error": ...}``.

        Args:
            ls: The language server.
            params: Request params: ``uri``, ``modelVersion`` and
                ``sectionId``.

        Returns:
            ``{"edit": ...}`` or ``{"error": ..., "code": ...}``.
        """

        def build(structure, parsed):
            """Compute the edits that dissolve the section.

            Args:
                structure: Scanned structure of the file.
                parsed: The parsed file.

            Returns:
                The text edits.

            Raises:
                _InvalidRequestError: When ``sectionId`` is missing or null.
            """
            section_id = _int_or_none(params, "sectionId")
            if section_id is None:
                raise _InvalidRequestError()
            return dissolve_section_edits(
                structure, parsed.lexer.content, section_id
            )

        return _structure_request(ls, params, build)

    @server.feature(PREVIEW_MOVE_METHOD)
    def preview_move(ls, params):
        """Edit that moves a record/section, or ``{"error": ...}``.

        Args:
            ls: The language server.
            params: Request params: ``uri``, ``modelVersion``, ``item``,
                ``targetSectionId`` and optionally ``before``.

        Returns:
            ``{"edit": ...}`` or ``{"error": ..., "code": ...}``.
        """

        def build(structure, parsed):
            """Compute the edits that move the item.

            Args:
                structure: Scanned structure of the file.
                parsed: The parsed file.

            Returns:
                The text edits.

            Raises:
                _InvalidRequestError: When ``item``, ``targetSectionId`` or
                    ``before`` is malformed.
            """
            item = _item_ref(getattr(params, "item", None))
            target_id = _int_or_none(params, "targetSectionId")
            raw_before = getattr(params, "before", None)
            before = None if raw_before is None else _item_ref(raw_before)
            return move_edits(
                structure, parsed.lexer.content, item, target_id, before
            )

        return _structure_request(ls, params, build)
