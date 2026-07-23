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
"""Code action handler for quick-fixes (e.g., "did you mean X?" suggestions)."""

import re
from typing import List, Optional

from lsprotocol.types import (
    TEXT_DOCUMENT_CODE_ACTION,
    CodeAction,
    CodeActionKind,
    CodeActionParams,
    TextDocumentEdit,
    TextEdit,
    VersionedTextDocumentIdentifier,
    WorkspaceEdit,
)

from ..server_protocol import ServerProtocol

DID_YOU_MEAN_RE = re.compile(r"unknown symbol (\S+), did you mean (\S+)\?")


def register(server: "ServerProtocol") -> None:
    """Register code action handlers on *server*."""

    @server.feature(TEXT_DOCUMENT_CODE_ACTION)
    # pygls injects the server as the first arg *by parameter name* ("ls"),
    # so it can't be renamed to "_ls"; this handler just doesn't need it.
    def code_action(
        ls,  # pylint: disable=unused-argument
        params: CodeActionParams,
    ) -> Optional[List[CodeAction]]:
        """Provide code actions for diagnostics in the range."""
        actions: List[CodeAction] = []
        uri = params.text_document.uri

        if not params.context.diagnostics:
            return actions

        for diagnostic in params.context.diagnostics:
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
