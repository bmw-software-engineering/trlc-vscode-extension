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
"""Unit tests for trlc_lsp.handlers.semantic_tokens."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

# pylint: disable=protected-access  # _encode_tokens is the module-private function under test

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from lsprotocol.types import (
    TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    SemanticTokensParams,
    TextDocumentIdentifier,
)

from server.handlers import semantic_tokens as st_mod
from server.token_utils import SEMANTIC_TOKEN_TYPES, uri_from_file


@pytest.fixture
def st_fn(fake_ls):
    """St fn."""
    st_mod.register(fake_ls)
    return fake_ls.get_handler(TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL)


def _params(uri):
    return SemanticTokensParams(
        text_document=TextDocumentIdentifier(uri=uri),
    )


class TestSemanticTokens:
    def test_returns_semantic_tokens_for_parsed_file(
        self, fake_ls, st_fn, rsl_path
    ):
        # Given: a parsed schema file
        uri = uri_from_file(rsl_path)
        params = _params(uri)
        # When: semantic tokens are requested
        result = st_fn(fake_ls, params)
        # Then: a well-formed (5-tuples) token data array is returned
        assert result is not None
        assert isinstance(result.data, list)
        assert len(result.data) > 0
        # 5 integers per token in LSP delta encoding
        assert len(result.data) % 5 == 0

    def test_returns_tokens_for_trlc_file(self, fake_ls, st_fn, trlc_path):
        # Given: a parsed instance file
        uri = uri_from_file(trlc_path)
        params = _params(uri)
        # When: semantic tokens are requested
        result = st_fn(fake_ls, params)
        # Then: a well-formed (5-tuples) token data array is returned
        assert result is not None
        assert len(result.data) > 0
        assert len(result.data) % 5 == 0

    def test_at_least_one_token_for_rsl(self, fake_ls, st_fn, rsl_path):
        # Given: a parsed schema file with keywords and identifiers
        uri = uri_from_file(rsl_path)
        # When: semantic tokens are requested
        result = st_fn(fake_ls, _params(uri))
        # Then: multiple tokens are returned
        # The fixture has keywords and identifiers — expect multiple tokens
        assert len(result.data) > 0

    def test_unparsed_file_returns_empty_token_data(self, fake_ls, st_fn):
        """An unknown URI has no context to read tokens from, and the fake
        workspace's get_text_document stub returns empty source, so the
        lexer fallback finds nothing to lex — the handler must return an
        empty (not None, not malformed) SemanticTokens rather than skip the
        response entirely."""
        # Given: a uri that was never parsed, and no editor buffer content
        # When: semantic tokens are requested against that uri
        result = st_fn(fake_ls, _params("file:///unknown.rsl"))
        # Then: a well-formed, empty token data array — not None
        assert result is not None
        assert result.data == []

    def test_unparsed_file_with_buffer_content_uses_lex_fallback(
        self, fake_ls, st_fn
    ):
        """An unknown URI that DOES have editor buffer content (open but not
        yet parsed) still gets real, well-formed token data via the lexer
        fallback — not an empty response just because there's no context."""
        # Given: a uri with no active parse context, but real source text
        # available through ls.workspace.get_text_document (the fallback's
        # only source of content for an unparsed file)
        fake_ls.workspace.get_text_document.side_effect = lambda _uri: Mock(
            source="package Types\n"
        )
        # When: semantic tokens are requested against that uri
        result = st_fn(fake_ls, _params("file:///unknown.rsl"))
        # Then: well-formed (5-tuples) token data, not empty
        assert result is not None
        assert len(result.data) > 0
        assert len(result.data) % 5 == 0

    def test_token_type_indices_are_in_range(self, fake_ls, st_fn, rsl_path):
        # Given: a parsed schema file
        uri = uri_from_file(rsl_path)
        # When: semantic tokens are requested
        result = st_fn(fake_ls, _params(uri))
        assert result is not None
        assert len(result.data) > 0
        num_types = len(SEMANTIC_TOKEN_TYPES)
        token_types = result.data[
            3::5
        ]  # index 3 of each 5-tuple is token type
        # Then: every encoded token-type index falls within the legend's range
        for tt in token_types:
            assert 0 <= tt < num_types, f"Token type index {tt} out of range"


def _fake_token(line_no, col_no, start_pos, end_pos):
    return SimpleNamespace(
        location=SimpleNamespace(
            line_no=line_no,
            col_no=col_no,
            start_pos=start_pos,
            end_pos=end_pos,
            file_name="fake.rsl",
        )
    )


class TestEncodeTokensSkipsInvalidLength:
    """`_encode_tokens` must degrade gracefully (skip the bad token, keep
    the rest) rather than raising on a malformed token span — a crash here
    used to abort the entire semantic-tokens response for the whole file."""

    def test_token_with_end_before_start_is_skipped_not_raised(
        self, monkeypatch
    ):
        """A malformed token span is dropped, not raised, from the batch."""
        # Given: every token classifies to semantic type 0, and one token
        # has end_pos < start_pos (invalid span)
        monkeypatch.setattr(st_mod, "get_token_semantic_type", lambda _t: 0)
        good_before = _fake_token(line_no=1, col_no=1, start_pos=0, end_pos=3)
        bad = _fake_token(line_no=2, col_no=1, start_pos=10, end_pos=5)
        good_after = _fake_token(line_no=3, col_no=1, start_pos=0, end_pos=2)

        # When: encoding the token list
        data = st_mod._encode_tokens([good_before, bad, good_after])

        # Then: no exception, and both valid tokens are encoded (2 * 5 ints)
        # — the invalid one contributes nothing, it isn't replaced by a
        # garbage entry.
        assert len(data) == 10

    def test_all_valid_tokens_are_still_encoded_normally(self, monkeypatch):
        """A batch with no invalid spans encodes every token as usual."""
        # Given/When/Then: A batch with no invalid spans encodes every token as usual
        monkeypatch.setattr(st_mod, "get_token_semantic_type", lambda _t: 2)
        tokens = [
            _fake_token(line_no=1, col_no=1, start_pos=0, end_pos=3),
            _fake_token(line_no=1, col_no=5, start_pos=4, end_pos=6),
        ]

        data = st_mod._encode_tokens(tokens)

        assert len(data) == 10
        assert data[3] == 2 and data[8] == 2  # semantic type slot
