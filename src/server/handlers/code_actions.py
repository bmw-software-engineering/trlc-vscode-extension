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
"""Code action handler for quick-fixes.

"did you mean X?" suggestions, and opening an unopened .trlc file that
declares an unknown symbol (parse mode WORKSPACE parses open files only).
"""

import re
from typing import List, Optional

from lsprotocol.types import (
    TEXT_DOCUMENT_CODE_ACTION,
    CodeAction,
    CodeActionKind,
    CodeActionParams,
    Command,
    TextDocumentEdit,
    TextEdit,
    VersionedTextDocumentIdentifier,
    WorkspaceEdit,
)

from ..missing_symbols import declaring_files, scope_label
from ..server_protocol import ServerProtocol
from ..token_utils import uri_from_file

DID_YOU_MEAN_RE = re.compile(r"unknown symbol (\S+), did you mean (\S+)\?")
#: Client command that opens a file as a background editor tab.
OPEN_IN_BACKGROUND_COMMAND = "trlc.openFileInBackground"
#: Key in the client's ``experimental`` capabilities listing the
#: client-side commands it registered (see clientCommands.ts).
CLIENT_COMMANDS_CAPABILITY = "trlcCommands"


def _client_runs(ls, command: str) -> bool:
    """Tell whether the client announced that it can run *command*.

    The server also runs in editors other than VS Code (trlc-lsp wheel),
    which do not register the extension's commands; offering them a code
    action that runs one would only fail.
    """
    experimental = getattr(ls.client_capabilities, "experimental", None)
    if isinstance(experimental, dict):
        commands = experimental.get(CLIENT_COMMANDS_CAPABILITY)
    else:
        commands = getattr(experimental, CLIENT_COMMANDS_CAPABILITY, None)
    return isinstance(commands, (list, tuple)) and command in commands


def _open_file_actions(ls, uri: str, diagnostic) -> List[CodeAction]:
    """ "Open <file>" fixes for an unknown symbol declared in a closed file."""
    if not _client_runs(ls, OPEN_IN_BACKGROUND_COMMAND):
        return []
    try:
        text = ls.workspace.get_text_document(uri).source
    except OSError:
        return []
    actions = []
    for path, names in declaring_files(ls, uri, text, [diagnostic]).items():
        title = f"Open {scope_label(ls, uri, path)} (declares {names[0]})"
        actions.append(
            CodeAction(
                title=title,
                kind=CodeActionKind.QuickFix,
                diagnostics=[diagnostic],
                command=Command(
                    title=title,
                    command=OPEN_IN_BACKGROUND_COMMAND,
                    arguments=[uri_from_file(path)],
                ),
            )
        )
    return actions


def register(server: "ServerProtocol") -> None:
    """Register code action handlers on *server*."""

    @server.feature(TEXT_DOCUMENT_CODE_ACTION)
    def code_action(
        ls,
        params: CodeActionParams,
    ) -> Optional[List[CodeAction]]:
        """Provide code actions for diagnostics in the range."""
        actions: List[CodeAction] = []
        uri = params.text_document.uri

        if not params.context.diagnostics:
            return actions

        for diagnostic in params.context.diagnostics:
            actions.extend(_open_file_actions(ls, uri, diagnostic))
            match = DID_YOU_MEAN_RE.search(diagnostic.message)
            if not match:
                continue

            _unknown_sym = match.group(1)
            suggestion = match.group(2)

            action = CodeAction(
                title=f"Change to '{suggestion}'",
                kind=CodeActionKind.QuickFix,
                diagnostics=[diagnostic],
                edit=WorkspaceEdit(
                    document_changes=[
                        TextDocumentEdit(
                            text_document=VersionedTextDocumentIdentifier(
                                uri=uri,
                                version=0,
                            ),
                            edits=[
                                TextEdit(
                                    range=diagnostic.range,
                                    new_text=suggestion,
                                )
                            ],
                        )
                    ]
                ),
            )
            actions.append(action)

        return actions if actions else None
