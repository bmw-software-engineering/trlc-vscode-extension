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
"""Thin async helpers wrapping pygls' generated ``LanguageClient`` methods.

Mirrors the old hand-rolled ``LspTestClient``'s method names/shapes so the
test bodies that migrated from it stay close to their original form. Callers
get back the typed ``lsprotocol.types`` objects the client actually returns
(e.g. ``CompletionItem.label``, not a raw ``dict["label"]``).
"""

import asyncio
from typing import Any, List, Optional

from lsprotocol import types
from pytest_lsp import LanguageClient


def open_document(
    client: LanguageClient, uri: str, text: str, language_id: str = "trlc"
) -> None:
    """Send textDocument/didOpen."""
    client.text_document_did_open(
        types.DidOpenTextDocumentParams(
            text_document=types.TextDocumentItem(
                uri=uri, language_id=language_id, version=1, text=text
            )
        )
    )


def change_document(
    client: LanguageClient, uri: str, text: str, version: int = 2
) -> None:
    """Send textDocument/didChange (full content replacement)."""
    client.text_document_did_change(
        types.DidChangeTextDocumentParams(
            text_document=types.VersionedTextDocumentIdentifier(
                uri=uri, version=version
            ),
            content_changes=[
                types.TextDocumentContentChangeWholeDocument(text=text)
            ],
        )
    )


def close_document(client: LanguageClient, uri: str) -> None:
    """Send textDocument/didClose."""
    client.text_document_did_close(
        types.DidCloseTextDocumentParams(
            text_document=types.TextDocumentIdentifier(uri=uri)
        )
    )


def clear_diagnostics(client: LanguageClient, uri: str) -> None:
    """Discard cached diagnostics for *uri* so the next wait gets fresh data."""
    client.diagnostics.pop(uri, None)


async def wait_for_diagnostics(
    client: LanguageClient, uri: str, timeout: float = 15.0
) -> List[types.Diagnostic]:
    """Block until publishDiagnostics for *uri* arrives; return the list.

    ``client.diagnostics`` is populated synchronously by pytest-lsp's built-in
    ``textDocument/publishDiagnostics`` handler; this polls it, waking on
    each new notification (via ``wait_for_notification``) instead of
    busy-looping, bounded by an overall timeout.
    """

    async def _poll():
        while uri not in client.diagnostics:
            await client.wait_for_notification(
                "textDocument/publishDiagnostics"
            )

    await asyncio.wait_for(_poll(), timeout=timeout)
    return list(client.diagnostics[uri])


async def wait_for_message(
    client: LanguageClient, timeout: float = 10.0
) -> types.ShowMessageParams:
    """Block until a window/showMessage notification arrives; return it.

    ``client.messages`` is populated synchronously by pytest-lsp's built-in
    ``window/showMessage`` handler; this polls it, waking on each new
    notification (via ``wait_for_notification``) instead of busy-looping,
    bounded by an overall timeout — same pattern as :func:`wait_for_diagnostics`.
    """

    async def _poll():
        while not client.messages:
            await client.wait_for_notification("window/showMessage")

    await asyncio.wait_for(_poll(), timeout=timeout)
    return client.messages[-1]


async def completion(
    client: LanguageClient,
    uri: str,
    line: int,
    col: int,
    trigger_char: Optional[str] = None,
) -> List[types.CompletionItem]:
    """Request completions; return the list of CompletionItem objects."""
    if trigger_char is not None:
        context = types.CompletionContext(
            trigger_kind=types.CompletionTriggerKind.TriggerCharacter,
            trigger_character=trigger_char,
        )
    else:
        context = types.CompletionContext(
            trigger_kind=types.CompletionTriggerKind.Invoked
        )

    result = await client.text_document_completion_async(
        types.CompletionParams(
            text_document=types.TextDocumentIdentifier(uri=uri),
            position=types.Position(line=line, character=col),
            context=context,
        )
    )
    if result is None:
        return []
    if isinstance(result, types.CompletionList):
        return list(result.items)
    return list(result)


async def hover(client: LanguageClient, uri: str, line: int, col: int):
    """Request hover; return the Hover object or None."""
    return await client.text_document_hover_async(
        types.HoverParams(
            text_document=types.TextDocumentIdentifier(uri=uri),
            position=types.Position(line=line, character=col),
        )
    )


async def goto_definition(
    client: LanguageClient, uri: str, line: int, col: int
):
    """Request ``textDocument/definition`` (VS Code F12 / Ctrl+click);
    return a single Location or None.

    This is the *instance-level* navigation: for a ``Record_Object``
    reference it returns the object's own declaration location (in the
    ``.trlc`` file), not its type's location — use
    :func:`goto_type_definition` for that.
    """
    result = await client.text_document_definition_async(
        types.DefinitionParams(
            text_document=types.TextDocumentIdentifier(uri=uri),
            position=types.Position(line=line, character=col),
        )
    )
    if isinstance(result, list):
        return result[0] if result else None
    return result


async def goto_type_definition(
    client: LanguageClient, uri: str, line: int, col: int
):
    """Request ``textDocument/typeDefinition`` (VS Code "Go to Type
    Definition"); return a single Location or None.

    For a ``Record_Object`` reference this resolves one step *further*
    than :func:`goto_definition` — to the ``Record_Type`` that declares
    the object's schema (in the ``.rsl`` file).
    """
    result = await client.text_document_type_definition_async(
        types.TypeDefinitionParams(
            text_document=types.TextDocumentIdentifier(uri=uri),
            position=types.Position(line=line, character=col),
        )
    )
    if isinstance(result, list):
        return result[0] if result else None
    return result


async def references(client: LanguageClient, uri: str, line: int, col: int):
    """Request find references; return a list of Location objects."""
    result = await client.text_document_references_async(
        types.ReferenceParams(
            text_document=types.TextDocumentIdentifier(uri=uri),
            position=types.Position(line=line, character=col),
            context=types.ReferenceContext(include_declaration=True),
        )
    )
    return result if isinstance(result, list) else (result or [])


async def rename(
    client: LanguageClient, uri: str, line: int, col: int, new_name: str
):
    """Request rename; return the WorkspaceEdit object or None."""
    return await client.text_document_rename_async(
        types.RenameParams(
            text_document=types.TextDocumentIdentifier(uri=uri),
            position=types.Position(line=line, character=col),
            new_name=new_name,
        )
    )


async def code_action(
    client: LanguageClient,
    uri: str,
    diag_range: types.Range,
    diagnostics: List[types.Diagnostic],
) -> List[types.CodeAction]:
    """Request code actions for *diagnostics* in *diag_range*; return a list."""
    result = await client.text_document_code_action_async(
        types.CodeActionParams(
            text_document=types.TextDocumentIdentifier(uri=uri),
            range=diag_range,
            context=types.CodeActionContext(diagnostics=diagnostics),
        )
    )
    return result if isinstance(result, list) else (result or [])


async def semantic_tokens(client: LanguageClient, uri: str) -> List[int]:
    """Request full semantic tokens; return the flat data array."""
    result = await client.text_document_semantic_tokens_full_async(
        types.SemanticTokensParams(
            text_document=types.TextDocumentIdentifier(uri=uri)
        )
    )
    if result is None:
        return []
    return list(result.data)


async def execute_command(
    client: LanguageClient, command: str, arguments: Optional[list] = None
) -> Any:
    """Execute a workspace command."""
    return await client.workspace_execute_command_async(
        types.ExecuteCommandParams(command=command, arguments=arguments or [])
    )
