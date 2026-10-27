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
from typing import Optional

from lsprotocol.converters import get_converter
from lsprotocol.types import WorkspaceEdit

from ..parse_guard import normalize_fs_path
from ..preview_edits import (
    PreviewEditError,
    add_record_edits,
    set_field_edits,
)
from ..preview_model import build_preview_model, unavailable_model
from ..preview_scan import scan_file
from ..server_protocol import ServerProtocol
from ..token_utils import _ast_tokens_are_stale, path_from_uri

PREVIEW_MODEL_METHOD = "trlc/previewModel"
PREVIEW_SET_FIELD_METHOD = "trlc/previewSetField"
PREVIEW_ADD_RECORD_METHOD = "trlc/previewAddRecord"

_NO_SCOPE = "File is not part of an active TRLC scope."
_NOT_PARSED = "File has errors that prevent parsing; fix them in the editor."
_STALE = "Waiting for the parser to catch up with your last edit."

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


def _resolve(ls, uri: str):
    """Return ``(parsed, None)`` or ``(None, reason)``."""
    context = ls.get_context_for_uri(uri)
    if context is None:
        return None, _NO_SCOPE
    parsed = context.snapshot().vsm.all_files.get(
        normalize_fs_path(path_from_uri(uri))
    )
    if parsed is None:
        return None, _NOT_PARSED
    if _ast_tokens_are_stale(parsed, ls, uri):
        return None, _STALE
    return parsed, None


def _edit_result(uri: str, edits) -> dict:
    edit = WorkspaceEdit(changes={uri: edits})
    return {"edit": _CONVERTER.unstructure(edit)}


def register(server: "ServerProtocol") -> None:
    """Register the ``trlc/preview*`` requests on *server*."""

    @server.feature(PREVIEW_MODEL_METHOD)
    def preview_model(ls, params: PreviewModelParams):
        """Model of *params.uri*, or an "unavailable" model with a reason."""
        parsed, reason = _resolve(ls, params.uri)
        if parsed is None:
            return unavailable_model(params.uri, reason)
        return build_preview_model(params.uri, parsed)

    @server.feature(PREVIEW_SET_FIELD_METHOD)
    def preview_set_field(ls, params: PreviewSetFieldParams):
        """Edit that sets/unsets a record field, or ``{"error": ...}``."""
        parsed, reason = _resolve(ls, params.uri)
        if parsed is None:
            return {"error": reason}
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
            return {"error": str(e)}
        return _edit_result(params.uri, edits)

    @server.feature(PREVIEW_ADD_RECORD_METHOD)
    def preview_add_record(ls, params: PreviewAddRecordParams):
        """Edit that adds a record, or ``{"error": ...}``."""
        parsed, reason = _resolve(ls, params.uri)
        if parsed is None:
            return {"error": reason}
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
            return {"error": str(e)}
        return _edit_result(params.uri, edits)
