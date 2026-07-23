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
"""Semantic tokens handler."""

import logging

from lsprotocol.types import (
    TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    SemanticTokens,
    SemanticTokensLegend,
    SemanticTokensParams,
)

from ..server_protocol import ServerProtocol
from ..token_utils import (
    SEMANTIC_TOKEN_TYPES,
    get_token_semantic_type,
    path_from_uri,
    resolve_document_tokens,
)

LOGGER = logging.getLogger(__name__)


def _encode_tokens(all_tokens: list) -> list:
    """Encode *all_tokens* in the LSP semantic token delta format.

    Returns the flat integer array for ``SemanticTokens.data``.
    See https://microsoft.github.io/language-server-protocol/specifications/
    lsp/3.17/specification/#textDocument_semanticTokens
    """
    cur_line = 1
    cur_col = 0
    data = []
    for token in all_tokens:
        sem_type = get_token_semantic_type(token)
        if sem_type is None:
            continue
        delta_line = token.location.line_no - cur_line
        cur_line = token.location.line_no
        if delta_line > 0:
            delta_start = token.location.col_no - 1
            cur_col = token.location.col_no - 1
        else:
            delta_start = token.location.col_no - 1 - cur_col
            cur_col = token.location.col_no - 1
        length = token.location.end_pos - token.location.start_pos + 1
        if length <= 0:
            # Malformed token span — skip it rather than aborting the whole
            # response, so the rest of the file still gets highlighted.
            LOGGER.warning(
                "Skipping token with invalid length %d at %s:%d",
                length,
                token.location.file_name,
                token.location.line_no,
            )
            continue
        data += [delta_line, delta_start, length, sem_type, 0]
    return data


def register(server: "ServerProtocol") -> None:
    """Register the semantic tokens handler on *server*."""

    @server.feature(
        TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
        SemanticTokensLegend(
            token_types=SEMANTIC_TOKEN_TYPES, token_modifiers=[]
        ),
    )
    def semantic_tokens(ls, params: SemanticTokensParams):
        """Return full semantic token data for the given document."""
        uri = params.text_document.uri
        file_path = path_from_uri(uri)

        # Reuse tokens from the last parse when available (provides AST-aware
        # type classification for IDENTIFIER tokens); falls back to a
        # lex-only pass over the live buffer otherwise. ParseContext.result
        # is replaced wholesale (never mutated in place) by the ParseEngine
        # worker thread, so reading it via snapshot() here is race-free
        # without a lock.
        resolved = resolve_document_tokens(ls, uri, file_path)
        if resolved is None or not resolved.tokens:
            return SemanticTokens(data=[])

        return SemanticTokens(data=_encode_tokens(resolved.tokens))
