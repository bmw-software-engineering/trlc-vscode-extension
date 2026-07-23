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
"""Integration tests for textDocument/semanticTokens/full."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

from server.token_utils import SEMANTIC_TOKEN_TYPES

from .conftest import RSL_TEXT, TRLC_TEXT
from .lsp_helpers import open_document, semantic_tokens, wait_for_diagnostics


class TestSemanticTokens:
    async def test_returns_data_for_rsl_file(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: semantic tokens are requested for the schema file
        data = await semantic_tokens(client, types_uri)
        # Then: a non-empty, well-formed (5-tuples) token data array is returned
        assert isinstance(data, list)
        assert len(data) > 0
        assert len(data) % 5 == 0

    async def test_returns_data_for_trlc_file(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: semantic tokens are requested for the instance file
        data = await semantic_tokens(client, sample_uri)
        # Then: a well-formed (5-tuples) token data array is returned
        assert isinstance(data, list)
        assert len(data) % 5 == 0

    async def test_token_type_indices_are_in_range(
        self, client, types_uri, sample_uri
    ):
        """Every token type index in the encoded data must be a valid index."""
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: semantic tokens are requested for the schema file
        data = await semantic_tokens(client, types_uri)
        n = len(SEMANTIC_TOKEN_TYPES)
        # Then: every encoded token-type index falls within the legend's range
        for token_type in data[3::5]:
            assert 0 <= token_type < n, (
                f"Token type {token_type} is out of range [0, {n})"
            )

    async def test_first_token_is_keyword(self, client, types_uri, sample_uri):
        """First token in types.rsl is 'package' which should be a keyword."""
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: semantic tokens are requested for the schema file
        data = await semantic_tokens(client, types_uri)
        assert len(data) >= 5
        keyword_idx = SEMANTIC_TOKEN_TYPES.index("keyword")
        first_type = data[3]
        # Then: the first token ('package') is classified as a keyword
        assert first_type == keyword_idx, (
            f"First token should be keyword ({keyword_idx}), got {first_type}"
        )

    async def test_rsl_tokens_are_more_than_trlc_tokens(
        self, client, types_uri, sample_uri
    ):
        """types.rsl defines more tokens than the short sample.trlc."""
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: semantic tokens are requested for both files
        rsl_data = await semantic_tokens(client, types_uri)
        trlc_data = await semantic_tokens(client, sample_uri)
        # Then: the larger schema file yields more tokens than the short instance file
        assert len(rsl_data) > len(trlc_data)
