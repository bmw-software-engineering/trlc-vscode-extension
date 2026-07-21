# *******************************************************************************
# TRLC VSCode Extension
# Copyright (C) 2025 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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
"""Rename handler."""

from typing import Optional

import trlc.ast
from lsprotocol.types import (
    TEXT_DOCUMENT_RENAME,
    DiagnosticSeverity,
    MessageType,
    OptionalVersionedTextDocumentIdentifier,
    RenameParams,
    ShowMessageParams,
    TextDocumentEdit,
    TextEdit,
    WorkspaceEdit,
)

from ..server_config import ParseMode
from ..server_protocol import ServerProtocol
from ..token_utils import get_token, resolve_open_file


def _has_errors(diagnostic_history: dict) -> bool:
    """Return True if any file in the scope has at least one Error diagnostic."""
    return any(
        d.severity == DiagnosticSeverity.Error
        for diagnostics in diagnostic_history.values()
        for d in diagnostics
    )


def _build_workspace_edit(locs, new_text: str) -> WorkspaceEdit:
    """Convert a list of reference Locations into a WorkspaceEdit."""
    by_uri: dict = {}
    for loc in locs:
        by_uri.setdefault(loc.uri, []).append(
            TextEdit(range=loc.range, new_text=new_text)
        )
    return WorkspaceEdit(
        document_changes=[
            TextDocumentEdit(
                OptionalVersionedTextDocumentIdentifier(uri), changes
            )
            for uri, changes in by_uri.items()
        ]
    )


def _reject_if_workspace_mode(ls, file_config) -> Optional[WorkspaceEdit]:
    """Rename needs a full-repo view of references; WORKSPACE mode only
    parses open files + includes, so it can't be trusted to find every
    reference. Returns an empty edit (having already warned) when so, else
    ``None``."""
    if file_config.parse_mode is not ParseMode.WORKSPACE:
        return None
    ls.window_show_message(
        ShowMessageParams(
            type=MessageType.Warning,
            message="TRLC: Rename symbol is only available "
            "if parsing is set to 'directory', 'repo', "
            "or 'bazel' mode.",
        )
    )
    return WorkspaceEdit(document_changes=[])


def _reject_if_not_renameable(ls, cur_tok) -> Optional[WorkspaceEdit]:
    """Returns an empty edit (having already warned) when the cursor isn't on
    a renameable name, else ``None``."""
    if (
        cur_tok is not None
        and cur_tok.kind in ("IDENTIFIER", "DOT")
        and not isinstance(
            cur_tok.ast_link,
            (trlc.ast.Builtin_Type, trlc.ast.Builtin_Function),
        )
    ):
        return None
    ls.window_show_message(
        ShowMessageParams(
            type=MessageType.Warning,
            message="TRLC: Only names can be renamed excluding builtins.",
        )
    )
    return WorkspaceEdit(document_changes=[])


def _reject_if_scope_has_errors(
    ls, diagnostic_history: dict
) -> Optional[WorkspaceEdit]:
    """Returns an empty edit (having already warned) when any file in scope
    currently has an Error diagnostic, else ``None``."""
    if not _has_errors(diagnostic_history):
        return None
    ls.window_show_message(
        ShowMessageParams(
            type=MessageType.Warning,
            message="TRLC: Resolve errors or undo if errors occurred "
            "after renaming.",
        )
    )
    return WorkspaceEdit(document_changes=[])


def register(server: "ServerProtocol") -> None:
    """Register the rename handler on *server*."""

    @server.feature(TEXT_DOCUMENT_RENAME)
    def rename(ls, params: RenameParams):
        """
        Performs a rename action for a name that is not a Builtin_Type or
        Builtin_Function at a given cursor position. The renaming is only
        allowed if all files in the scope are syntactically valid TRLC.
        Renaming is only available if parsing is set to 'directory', 'repo',
        or 'bazel' mode (not 'workspace', which only has a partial view).
        """
        cursor_line = params.position.line
        cursor_col = params.position.character

        resolved = resolve_open_file(ls, params)
        if resolved is None:
            return None
        uri, file_path, file_obj = resolved

        # ParseContext.result is replaced wholesale (never mutated in place)
        # by the ParseEngine worker thread, so reading the current reference
        # via snapshot() here is race-free without a lock.
        context = ls.get_context_for_uri(uri)
        diagnostic_history = context.snapshot().diagnostics if context else {}

        cur_tok = get_token(
            file_obj.lexer.tokens, cursor_line, cursor_col, greedy=True
        )
        new_text = params.new_name
        file_config = ls.get_config_for_uri(uri)

        rejection = (
            _reject_if_workspace_mode(ls, file_config)
            or _reject_if_not_renameable(ls, cur_tok)
            or _reject_if_scope_has_errors(ls, diagnostic_history)
        )
        if rejection is not None:
            return rejection

        locs = ls.refs.resolve(ls, file_path, cursor_line, cursor_col) or []
        return _build_workspace_edit(locs, new_text)
