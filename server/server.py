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

import logging
import os
import re
import sys
import threading
import urllib.parse

import trlc.lexer
import trlc.errors
import trlc.ast

from lsprotocol.types import (
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_CLOSE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    WORKSPACE_DID_CHANGE_WORKSPACE_FOLDERS,
    TEXT_DOCUMENT_TYPE_DEFINITION,
    TEXT_DOCUMENT_HOVER,
    TEXT_DOCUMENT_REFERENCES,
    TEXT_DOCUMENT_DEFINITION,
)
from lsprotocol.types import (
    DidChangeTextDocumentParams,
    DidCloseTextDocumentParams,
    DidOpenTextDocumentParams,
    SemanticTokens,
    SemanticTokensLegend,
    SemanticTokensParams,
    DidChangeWorkspaceFoldersParams,
    TypeDefinitionParams,
    Location,
    Range,
    Position,
    WorkspaceConfigurationParams,
    ConfigurationItem,
    TextDocumentPositionParams,
    Hover,
    ReferenceParams,
    DefinitionParams,
)
from pygls.server import LanguageServer

from .trlc_utils import (
    Vscode_Message_Handler,
    Vscode_Source_Manager,
    File_Handler
)


logger = logging.getLogger()

RE_END_WORD = re.compile("^[A-Za-z_0-9]*")
RE_START_WORD = re.compile("[A-Za-z_0-9]*$")
RE_END_WORD_QUALIFIED = re.compile("^[A-Za-z_0-9.]*")
RE_START_WORD_QUALIFIED = re.compile("[A-Za-z_0-9.]*$")


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
    CONFIGURATION_SECTION = "trlcServer"

    def __init__(self, *args):
        super().__init__(*args)
        self.diagnostic_history = {}
        self.fh                 = File_Handler()
        self.packages           = {}
        self.parse_partial      = True
        self.queue_lock         = threading.Lock()
        self.queue              = []
        self.symbols            = trlc.ast.Symbol_Table()
        self.trigger_parse      = threading.Event()
        self.validator          = TrlcValidator(self)
        self.all_files          = {}
        self.validator.start()

    def validate(self):
        vmh = Vscode_Message_Handler()
        vsm = Vscode_Source_Manager(vmh, self.fh, self)

        for folder_uri in self.workspace.folders.keys():
            folder_path = _get_path(folder_uri)

            if self.parse_partial is True:
                vsm.register_include(folder_path)
            else:
                vsm.register_workspace(folder_path)

        if self.parse_partial is True:
            for file_uri, file_content in self.fh.files.items():
                file_path = _get_path(file_uri)
                vsm.register_file(file_path, file_content)

        vsm.process()
        self.symbols = vsm.stab
        self.all_files = vsm.all_files

        # Save uri to package mapping to remember the current package
        for file_name, parser in vsm.rsl_files.items():
            if hasattr(parser.cu.package, "name"):
                self.packages[_get_uri(file_name)] = parser.cu.package.name
            else:
                self.packages[_get_uri(file_name)] = None
        for file_name, parser in vsm.trlc_files.items():
            if hasattr(parser.cu.package, "name"):
                self.packages[_get_uri(file_name)] = parser.cu.package.name
            else:
                self.packages[_get_uri(file_name)] = None
        for file_name, parser in vsm.check_files.items():
            if hasattr(parser.cu.package, "name"):
                self.packages[_get_uri(file_name)] = parser.cu.package.name
            else:
                self.packages[_get_uri(file_name)] = None

        if self.workspace.documents:
            for uri in self.diagnostic_history:
                self.publish_diagnostics(uri, [])
        for uri, diagnostics in vmh.diagnostics.items():
            self.publish_diagnostics(uri, diagnostics)
        self.diagnostic_history = vmh.diagnostics
        self.show_message_log("Diagnostics published")

    def queue_event(self, kind, uri=None, content=None):
        with self.queue_lock:
            self.queue.insert(0, (kind, uri, content))
            self.trigger_parse.set()


def _get_uri(file_name):
    abs_path = os.path.abspath(file_name)
    url = urllib.parse.quote(abs_path.replace('\\', '/'))
    uri = urllib.parse.urlunparse(('file', '', url, '', '', ''))
    return uri


def _get_path(uri):
    """
    Extracts and returns the path from a given URI.

    Parameters:
    - uri (str): The URI to extract the path from.

    Returns:
    - path (str): The decoded path.
    """
    parsed_uri = urllib.parse.urlparse(uri)
    path = urllib.parse.unquote(parsed_uri.path)

    # Adjust path for Windows platform if necessary
    if (sys.platform.startswith("win32") and path.startswith("/")):
        path = path[1:]

    return path


def _get_token(tokens, curser_line, curser_col):
    """
    Get the token located at the specified cursor position.

    Parameters:
    - tokens (list): A list of tokens to search through.
    - curser_line (int): The line number of the cursor position.
    - curser_col (int): The column number of the cursor position.

    Returns:
    - Token or None: The token found at the cursor position,
      or None if no matching token is found.
    """
    tok = None
    for token in tokens:
        tok_loc = _get_location(token)
        tok_rng = tok_loc.range
        if (tok_rng.start.character <= curser_col < tok_rng.end.character and
                tok_rng.start.line <= curser_line <= tok_rng.end.line):
            tok = token
            break
    return tok


def _get_ast_entity(token):
    """
    Extracts and returns the AST object of type 'trlc.ast.Entity' linked with
    the token or any references associated with it.

    Parameters:
    - token (trlc.lexer.Token): The token from which to extract the AST object.

    Returns:
    - trlc.ast.Entity or None if the token is not linked with the required
    types.
    """
    assert isinstance(token, trlc.lexer.Token)
    tok_lk = token.ast_link

    # Get the Entity type based on the type of the attribute token.ast_link
    return (
        tok_lk if isinstance(tok_lk, trlc.ast.Entity) else
        tok_lk.entity if isinstance(tok_lk, trlc.ast.Name_Reference) else
        tok_lk.target if isinstance(tok_lk, trlc.ast.Record_Reference) else
        tok_lk.value if isinstance(tok_lk, trlc.ast.Enumeration_Literal) else
        None)


def _get_location(obj):
    """
    Get the location details of the given object.

    Parameters:
    - obj: An object with location information either of type trlc.lexer.Token
      or trlc.ast.Node.

    Returns:
    - Location: A Location object containing URI and Range details.
    """
    assert isinstance(obj, (trlc.lexer.Token, trlc.ast.Node))
    end_location = obj.location.get_end_location()
    end_line = (0 if end_location.line_no is None
                else end_location.line_no - 1)
    end_col = 1 if end_location.col_no is None else end_location.col_no
    start_line = (0 if obj.location.line_no is None
                  else obj.location.line_no - 1)
    start_col = (0 if obj.location.col_no is None
                 else obj.location.col_no - 1)
    end_range = Position(line=end_line, character=end_col)
    start_range = Position(line=start_line, character=start_col)
    ref_range = Range(start=start_range, end=end_range)
    ref_uri = _get_uri(obj.location.file_name)

    return Location(uri=ref_uri, range=ref_range)


trlc_server = TrlcLanguageServer("pygls-trlc", "v0.1")


@trlc_server.feature(WORKSPACE_DID_CHANGE_WORKSPACE_FOLDERS)
def on_workspace_folders_change(ls, _: DidChangeWorkspaceFoldersParams):
    """Workspace folders did change notification."""
    ls.show_message("Workspace folder did change!")
    ls.queue_event("reparse")


@trlc_server.feature(TEXT_DOCUMENT_DID_CHANGE)
async def did_change(ls, params: DidChangeTextDocumentParams):
    """Text document did change notification."""
    try:
        config = await ls.get_configuration_async(WorkspaceConfigurationParams(
            items=[
                ConfigurationItem(
                    scope_uri="",
                    section=TrlcLanguageServer.CONFIGURATION_SECTION
                )
            ]
        ))
        parsing = config[0].get("parsing")
        if parsing == "full":
            ls.parse_partial = False
        elif parsing == "partial":
            ls.parse_partial = True
    except Exception:  # pylint: disable=W0718
        logger.error("Unable to get workspace configuration", exc_info=True)

    uri = params.text_document.uri
    document = ls.workspace.get_document(uri)
    content = document.source
    ls.queue_event("change", uri, content)


@trlc_server.feature(TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls, params: DidCloseTextDocumentParams):
    """Text document did close notification."""
    uri = params.text_document.uri
    ls.queue_event("delete", uri)


@trlc_server.command("extension.parseAll")
def cmd_parse_all(ls, *args):  # pylint: disable=W0613
    ls.parse_partial = False
    ls.queue_event("reparse")


@trlc_server.feature(TEXT_DOCUMENT_DID_OPEN)
async def did_open(ls, params: DidOpenTextDocumentParams):
    """Text document did open notification."""
    try:
        config = await ls.get_configuration_async(WorkspaceConfigurationParams(
            items=[
                ConfigurationItem(
                    scope_uri="",
                    section=TrlcLanguageServer.CONFIGURATION_SECTION
                )
            ]
        ))
        parsing = config[0].get("parsing")
        if parsing == "full":
            ls.parse_partial = False
        elif parsing == "partial":
            ls.parse_partial = True
    except Exception:  # pylint: disable=W0718
        logger.error("Unable to get workspace configuration", exc_info=True)

    uri = params.text_document.uri
    document = ls.workspace.get_document(uri)
    content = document.source
    ls.queue_event("change", uri, content)


@trlc_server.feature(TEXT_DOCUMENT_TYPE_DEFINITION)
def goto_type_definition(ls, params: TypeDefinitionParams):
    """
    Finds the location of the type definition for the identifier token at a
    given curser position linked to an AST object of type Entity
    
    Parameters:
    - ls: The language server instance.
    - params: TypeDefinitionParams object containing the curser position and
      the uri.

    Returns:
    - Location or None: The location of the type definition. Returns None if no
      type definition is found or if the cursor position does not point to an
      identifier or this identifier is not linked to an AST entity.
    """
    curser_line = params.position.line
    curser_col  = params.position.character
    uri         = params.text_document.uri
    file_path   = _get_path(uri)
    tokens      = ls.all_files[file_path].lexer.tokens
    cur_tok     = _get_token(tokens, curser_line, curser_col)
    ast_loc     = None

    # Exit condition: If there is no token at the cursor position
    # or the token lacks an ast_link.
    # We specifically consider only identifiers, excluding Builtins.
    if (cur_tok is None or
            cur_tok.ast_link is None or
            cur_tok.kind != "IDENTIFIER" or
            isinstance(cur_tok.ast_link, trlc.ast.Builtin_Type)):
        return None

    # Get the trlc.ast.Entity object at the cursor position or from another
    # location where the Entity is explicitly defined.
    ast_obj = _get_ast_entity(cur_tok)
    ast_loc = _get_location(ast_obj)

    return ast_loc if ast_loc else None


@trlc_server.feature(TEXT_DOCUMENT_REFERENCES)
def references(ls, params: ReferenceParams):
    """
    Finds all references for the identifier token at a given curser position
    linked to identical AST objects of types Entity, Record_Reference,
    Name_Reference or Enumeration_Literal.

    Parameters:
    - ls: The language server instance.
    - params: ReferenceParams object containing the curser position and the
      uri.

    Returns:
    - locations: A list of Location objects representing the references to the
      identifier. If no references are found, None is returned.
    """
    pars        = []
    locations   = []
    curser_line = params.position.line
    curser_col  = params.position.character
    uri         = params.text_document.uri
    file_path   = _get_path(uri)
    cur_pkg     = ls.all_files[file_path].cu.package
    imp_pkg     = ls.all_files[file_path].cu.imports
    tokens      = ls.all_files[file_path].lexer.tokens
    cur_tok     = _get_token(tokens, curser_line, curser_col)

    # Exit condition: If there is no token at the cursor position
    # or the token lacks an ast_link.
    # We specifically consider only identifiers, excluding Builtins.
    if (cur_tok is None or
            cur_tok.ast_link is None or
            cur_tok.kind != "IDENTIFIER" or
            isinstance(cur_tok.ast_link, trlc.ast.Builtin_Type)):
        return None

    # Get the trlc.ast.Entity object at the cursor position or from another
    # location where the Entity is explicitly defined.
    ast_obj = _get_ast_entity(cur_tok)

    # Filter for all relevant parsers
    for par in ls.all_files.values():
        if (par.lexer.tokens and
                (cur_pkg == par.cu.package or
                    par.cu.package in imp_pkg or
                    cur_pkg in par.cu.imports)):
            pars.append(par)

    # Iterate through all relevant Token_Streams and their tokens.
    for par in pars:
        for tok in par.lexer.tokens:
            # We proceed to the next iteration if we encounter a token that is
            # not an identifier.
            if tok.kind != "IDENTIFIER":
                continue
            # Retrive the trlc.ast.Entity object behind the link and check for
            # equality.
            if ast_obj == _get_ast_entity(tok):
                locations.append(_get_location(tok))

    return locations if locations else None


@trlc_server.feature(TEXT_DOCUMENT_HOVER)
def hover(ls, params: TextDocumentPositionParams):
    desc    = None
    curser_line = params.position.line
    curser_col = params.position.character
    uri = params.text_document.uri
    file_path = _get_path(uri)
    tokens = ls.all_files[file_path].lexer.tokens
    cur_tok = _get_token(tokens, curser_line, curser_col)

    # Exit condition: If there is no token at the cursor position
    # or the token lacks an ast_link.
    if (cur_tok is None or cur_tok.ast_link is None):
        return None

    ast_obj = cur_tok.ast_link
    tok_loc = _get_location(cur_tok)
    rng = tok_loc.range

    # Get the object's description depending on the ast type
    if isinstance(ast_obj, (trlc.ast.Concrete_Type,
                            trlc.ast.Composite_Component)):
        desc = ast_obj.description
    elif isinstance(ast_obj, trlc.ast.Name_Reference):
        desc = ast_obj.entity.description
    elif isinstance(ast_obj, trlc.ast.Enumeration_Literal):
        desc = ast_obj.value.description

    return Hover(contents=desc, range=rng)


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
