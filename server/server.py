#!/usr/bin/env python3
#
# TRLC VSCode Extension
# Copyright (C) 2023 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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

# This server is derived from the pygls example server, licensed under
# the Apache License, Version 2.0.

import asyncio
import sys
import time
import urllib.parse
import uuid
import threading
import logging

from lsprotocol.types import (
    TEXT_DOCUMENT_COMPLETION,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_CLOSE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    WORKSPACE_DID_CHANGE_WORKSPACE_FOLDERS,
)
from lsprotocol.types import (
    ConfigurationItem,
    DidChangeTextDocumentParams,
    DidCloseTextDocumentParams,
    DidOpenTextDocumentParams,
    MessageType,
    Registration,
    RegistrationParams,
    SemanticTokens,
    SemanticTokensLegend,
    SemanticTokensParams,
    Unregistration,
    UnregistrationParams,
    WorkspaceConfigurationParams,
    DidChangeWorkspaceFoldersParams,
)
from pygls.server import LanguageServer
from .trlc_utils import (
    Vscode_Message_Handler,
    Vscode_Source_Manager,
    File_Handler
)

import trlc.lexer
import trlc.errors

logger = logging.getLogger()

COUNT_DOWN_START_IN_SECONDS = 10
COUNT_DOWN_SLEEP_IN_SECONDS = 1


class TrlcValidator(threading.Thread):
    def __init__(self, server):
        super().__init__(name="TRLC Parser Thread")
        self.server = server

    def validate(self):
        while True:
            with self.server.queue_lock:
                if not self.server.queue:
                    return
                while self.server.queue:
                    action, uri, content = self.server.queue.pop()
                    if action == "change":
                        self.server.fh.update_files(uri, content)
                    elif action == "reparse":
                        pass
                    else:
                        self.server.fh.delete_files(uri)
            self.server.validate()

    def run(self):
        while True:
            self.server.trigger_parse.wait()
            self.server.trigger_parse.clear()
            self.validate()


class TrlcLanguageServer(LanguageServer):
    CMD_COUNT_DOWN_BLOCKING = "countDownBlocking"
    CMD_COUNT_DOWN_NON_BLOCKING = "countDownNonBlocking"
    CMD_REGISTER_COMPLETIONS = "registerCompletions"
    CMD_SHOW_CONFIGURATION_ASYNC = "showConfigurationAsync"
    CMD_SHOW_CONFIGURATION_CALLBACK = "showConfigurationCallback"
    CMD_SHOW_CONFIGURATION_THREAD = "showConfigurationThread"
    CMD_UNREGISTER_COMPLETIONS = "unregisterCompletions"

    CONFIGURATION_SECTION = "trlcServer"

    def __init__(self, *args):
        super().__init__(*args)
        self.fh            = File_Handler()
        self.queue_lock    = threading.Lock()
        self.queue         = []
        self.trigger_parse = threading.Event()
        self.validator     = TrlcValidator(self)
        self.validator.start()

    def validate(self):
        vmh = Vscode_Message_Handler()
        vsm = Vscode_Source_Manager(vmh, self.fh, self)

        for folder_uri in self.workspace.folders.keys():
            parsed_uri = urllib.parse.urlparse(folder_uri)
            folder_path = urllib.parse.unquote(parsed_uri.path)
            if (sys.platform.startswith("win32") and
                folder_path.startswith("/")):
                folder_path = folder_path[1:]
            vsm.register_workspace(folder_path)

        vsm.process()
        if self.workspace.documents:
            for uri in self.workspace.documents:
                self.publish_diagnostics(uri, [])
        for uri, diagnostics in vmh.diagnostics.items():
            self.publish_diagnostics(uri, diagnostics)
        self.show_message_log("Diagnostics published")

    def queue_event(self, kind, uri=None, content=None):
        with self.queue_lock:
            self.queue.insert(0, (kind, uri, content))
            self.trigger_parse.set()


trlc_server = TrlcLanguageServer("pygls-trlc", "v0.1")


@trlc_server.feature(WORKSPACE_DID_CHANGE_WORKSPACE_FOLDERS)
def on_workspace_folders_change(ls, _):
    """Workspace folders did change notification."""
    ls.show_message("Workspace folder did change!")
    ls.queue_event("reparse")


@trlc_server.feature(TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls, params: DidChangeTextDocumentParams):
    """Text document did change notification."""
    ls.show_message("Text Document did change!")
    uri = params.text_document.uri
    document = ls.workspace.get_document(uri)
    content = document.source
    ls.queue_event("change", uri, content)


@trlc_server.feature(TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls, params: DidCloseTextDocumentParams):
    """Text document did close notification."""
    ls.show_message("Text Document Did Close")
    uri = params.text_document.uri
    ls.queue_event("delete", uri)


@trlc_server.feature(TEXT_DOCUMENT_DID_OPEN)
def did_open(ls, params: DidOpenTextDocumentParams):
    """Text document did open notification."""
    ls.show_message("Text Document Did Open")
    uri = params.text_document.uri
    document = ls.workspace.get_document(uri)
    content = document.source
    ls.queue_event("change", uri, content)


@trlc_server.command(TrlcLanguageServer.CMD_COUNT_DOWN_BLOCKING)
def count_down_10_seconds_blocking(ls, *args):
    """Starts counting down and showing message synchronously.
    It will `block` the main thread, which can be tested by trying to show
    completion items.
    """
    for i in range(COUNT_DOWN_START_IN_SECONDS):
        ls.show_message(f"Counting down... {COUNT_DOWN_START_IN_SECONDS - i}")
        time.sleep(COUNT_DOWN_SLEEP_IN_SECONDS)


@trlc_server.command(TrlcLanguageServer.CMD_COUNT_DOWN_NON_BLOCKING)
async def count_down_10_seconds_non_blocking(ls, *args):
    """Starts counting down and showing message asynchronously.
    It won't `block` the main thread, which can be tested by trying to show
    completion items.
    """
    for i in range(COUNT_DOWN_START_IN_SECONDS):
        ls.show_message(f"Counting down... {COUNT_DOWN_START_IN_SECONDS - i}")
        await asyncio.sleep(COUNT_DOWN_SLEEP_IN_SECONDS)


@trlc_server.feature(
    TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    SemanticTokensLegend(token_types=["operator"], token_modifiers=[]),
)
def semantic_tokens(ls: TrlcLanguageServer, params: SemanticTokensParams):
    uri = params.text_document.uri
    doc = ls.workspace.get_document(uri)

    if not doc.source:
        return SemanticTokens(data=[])

    mh = trlc.errors.Message_Handler()
    lexer = trlc.lexer.TRLC_Lexer(mh, uri, doc.source)
    tokens = []
    while True:
        try:
            tok = lexer.token()
        except trlc.errors.TRLC_Error:
            tok = None
        if tok is None:
            break
        tokens.append(tok)

    tokens = [token
              for token in tokens
              if token.kind == "OPERATOR"]

    # https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#textDocument_semanticTokens

    cur_line = 1
    cur_col  = 0
    data     = []
    for token in tokens:
        delta_line = token.location.line_no - cur_line
        cur_line   = token.location.line_no
        if delta_line > 0:
            delta_start = token.location.col_no - 1
            cur_col     = token.location.col_no - 1
        else:
            delta_start = token.location.col_no - cur_col
            cur_col     = token.location.col_no - cur_col
        length = token.location.start_pos - token.location.end_pos + 1
        data += [delta_line, delta_start, length, 0, 0]

    return SemanticTokens(data=data)


@trlc_server.command(TrlcLanguageServer.CMD_REGISTER_COMPLETIONS)
async def register_completions(ls: TrlcLanguageServer, *args):
    """Register completions method on the client."""
    params = RegistrationParams(
        registrations=[
            Registration(
                id=str(uuid.uuid4()),
                method=TEXT_DOCUMENT_COMPLETION,
                register_options={"triggerCharacters": "[':']"},
            )
        ]
    )
    response = await ls.register_capability_async(params)
    if response is None:
        ls.show_message("Successfully registered completions method")
    else:
        ls.show_message(
            "Error happened during completions registration.",
            MessageType.Error
        )


@trlc_server.command(TrlcLanguageServer.CMD_SHOW_CONFIGURATION_ASYNC)
async def show_configuration_async(ls: TrlcLanguageServer, *args):
    """Gets exampleConfiguration from the client settings using coroutines."""
    try:
        config = await ls.get_configuration_async(
            WorkspaceConfigurationParams(
                items=[
                    ConfigurationItem(
                        scope_uri="",
                        section=TrlcLanguageServer.CONFIGURATION_SECTION
                    )
                ]
            )
        )

        example_config = config[0].get("exampleConfiguration")

        ls.show_message(f"jsonServer.exampleConfiguration value: "
                        "{example_config}")

    except Exception as e:
        ls.show_message_log(f"Error ocurred: {e}")


@trlc_server.command(TrlcLanguageServer.CMD_SHOW_CONFIGURATION_CALLBACK)
def show_configuration_callback(ls: TrlcLanguageServer, *args):
    """Gets exampleConfiguration from the client settings using callback."""

    def _config_callback(config):
        try:
            example_config = config[0].get("exampleConfiguration")

            ls.show_message(f"jsonServer.exampleConfiguration value: "
                            "{example_config}")

        except Exception as e:
            ls.show_message_log(f"Error ocurred: {e}")

    ls.get_configuration(
        WorkspaceConfigurationParams(
            items=[
                ConfigurationItem(
                    scope_uri="",
                    section=TrlcLanguageServer.CONFIGURATION_SECTION
                )
            ]
        ),
        _config_callback,
    )


@trlc_server.thread()
@trlc_server.command(TrlcLanguageServer.CMD_SHOW_CONFIGURATION_THREAD)
def show_configuration_thread(ls: TrlcLanguageServer, *args):
    """Gets exampleConfiguration from the client settings using thread pool."""
    try:
        config = ls.get_configuration(
            WorkspaceConfigurationParams(
                items=[
                    ConfigurationItem(
                        scope_uri="",
                        section=TrlcLanguageServer.CONFIGURATION_SECTION
                    )
                ]
            )
        ).result(2)

        example_config = config[0].get("exampleConfiguration")

        ls.show_message(f"jsonServer.exampleConfiguration value: "
                        "{example_config}")

    except Exception as e:
        ls.show_message_log(f"Error ocurred: {e}")


@trlc_server.command(TrlcLanguageServer.CMD_UNREGISTER_COMPLETIONS)
async def unregister_completions(ls: TrlcLanguageServer, *args):
    """Unregister completions method on the client."""
    params = UnregistrationParams(
        unregisterations=[
            Unregistration(id=str(uuid.uuid4()),
                           method=TEXT_DOCUMENT_COMPLETION)
        ]
    )
    response = await ls.unregister_capability_async(params)
    if response is None:
        ls.show_message("Successfully unregistered completions method")
    else:
        ls.show_message(
            "Error happened during completions unregistration.",
            MessageType.Error
        )
