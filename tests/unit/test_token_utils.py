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
"""Unit tests for trlc_lsp.token_utils.

Token positions use get_location() to discover real column/line values so
the tests are independent of lexer internals.  The content ``"foo bar baz"``
produces three IDENTIFIER tokens on line 0 (0-based) with a gap between each
pair, which covers all important get_token() scenarios.
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import trlc.ast
import trlc.lexer

from server.parse_guard import normalize_fs_path
from server.token_utils import (
    SEMANTIC_TOKEN_TYPES,
    folder_for_uri,
    get_ast_entity,
    get_location,
    get_token,
    get_token_semantic_type,
    path_from_uri,
    resolve_document_tokens,
    resolve_open_file,
    uri_from_file,
)

# ---------------------------------------------------------------------------
# uri_from_file / path_from_uri
# ---------------------------------------------------------------------------


class TestUriFromFile:
    def test_absolute_path_produces_file_scheme(self):
        # Given/When: an absolute path is converted to a uri
        # Then: it uses the file:// scheme
        assert uri_from_file("/tmp/foo.trlc").startswith("file://")

    def test_round_trip(self):
        # Given: a real absolute path for this platform (not a hardcoded
        # POSIX literal — "/tmp/some/file.trlc" isn't itself a real
        # Windows absolute path, so os.path.abspath() would silently
        # rewrite it, e.g. to "C:\tmp\some\file.trlc")
        path = os.path.abspath(
            os.path.join(os.sep, "tmp", "some", "file.trlc")
        )
        # When/Then: converting to uri and back yields the same path,
        # allowing for Windows' drive-letter case normalization (pygls
        # always lowercases it — see normalize_fs_path)
        assert normalize_fs_path(path_from_uri(uri_from_file(path))) == (
            normalize_fs_path(path)
        )

    def test_spaces_encoded(self):
        # Given/When: a path containing a space is converted to a uri
        uri = uri_from_file("/tmp/my file.trlc")
        # Then: the space is percent-encoded
        assert " " not in uri
        assert "%20" in uri

    def test_slashes_not_encoded(self):
        # Given/When/Then: path separators are preserved unencoded in the uri
        assert "/tmp/a/b/c.trlc" in uri_from_file("/tmp/a/b/c.trlc")


class TestPathFromUri:
    def test_simple_uri(self):
        # Given: a uri built from a real absolute path for this platform
        # (see TestUriFromFile.test_round_trip for why not a bare literal)
        path = os.path.abspath(os.path.join(os.sep, "tmp", "foo.trlc"))
        uri = uri_from_file(path)
        # When/Then: it decodes back to the same (normalized) path
        assert normalize_fs_path(path_from_uri(uri)) == normalize_fs_path(path)

    def test_encoded_space_decoded(self):
        # Given: a uri built from a real absolute path containing a space
        path = os.path.abspath(os.path.join(os.sep, "tmp", "my file.trlc"))
        uri = uri_from_file(path)
        # When: decoding it back
        decoded = path_from_uri(uri)
        # Then: the percent-encoded space is restored to a literal space,
        # and it's otherwise the same (normalized) path
        assert " " in decoded
        assert normalize_fs_path(decoded) == normalize_fs_path(path)


# ---------------------------------------------------------------------------
# get_token  (uses real TRLC lexer via the make_tokens fixture)
# ---------------------------------------------------------------------------


class TestGetToken:
    """Tests use real trlc.lexer.Token instances produced from simple content.

    "foo bar baz" gives three IDENTIFIER tokens on line 0 (0-based).
    We derive expected column numbers dynamically via get_location() so the
    tests are decoupled from lexer internals.
    """

    toks: list  # set by _setup fixture
    starts: list  # set by _setup fixture
    ends: list  # set by _setup fixture

    @pytest.fixture(autouse=True)
    def _setup(self, make_tokens):
        self.toks = make_tokens("foo bar baz")
        assert len(self.toks) == 3, "expected 3 tokens from 'foo bar baz'"
        # Discover 0-based column ranges from the real tokens.
        locs = [get_location(t).range for t in self.toks]
        self.starts = [r.start.character for r in locs]
        self.ends = [r.end.character for r in locs]

    def test_exact_match_first_token(self):
        # Given: three tokens on one line
        col = self.starts[0]
        # When/Then: cursor exactly on the first token's start returns it
        assert (
            get_token(self.toks, cursor_line=0, cursor_col=col) is self.toks[0]
        )

    def test_exact_match_middle_token(self):
        # Given: three tokens on one line
        col = self.starts[1]
        # When/Then: cursor exactly on the middle token's start returns it
        assert (
            get_token(self.toks, cursor_line=0, cursor_col=col) is self.toks[1]
        )

    def test_exact_match_last_token(self):
        # Given: three tokens on one line
        col = self.starts[2]
        # When/Then: cursor exactly on the last token's start returns it
        assert (
            get_token(self.toks, cursor_line=0, cursor_col=col) is self.toks[2]
        )

    def test_no_match_far_right_returns_none(self):
        # Given: three tokens on one line
        # When/Then: a cursor far past any token returns None
        assert get_token(self.toks, cursor_line=0, cursor_col=999) is None

    def test_empty_token_list_returns_none(self):
        # Given/When/Then: an empty token list always returns None
        assert get_token([], cursor_line=0, cursor_col=0) is None

    def test_wrong_line_returns_none(self):
        # Given: three tokens all on line 0
        col = self.starts[0]
        # When/Then: querying a different line returns None
        assert get_token(self.toks, cursor_line=5, cursor_col=col) is None

    def test_gap_between_tokens_non_greedy_returns_none(self):
        # Given: a cursor sitting in the whitespace gap between two tokens
        # There is a gap between tok[0] end and tok[1] start.
        gap_col = self.ends[0]  # one past end of first token = whitespace
        if gap_col >= self.starts[1]:
            pytest.skip("tokenizer produced no gap; cannot test gap behaviour")
        # When/Then: non-greedy lookup in the gap returns None
        assert (
            get_token(
                self.toks, cursor_line=0, cursor_col=gap_col, greedy=False
            )
            is None
        )

    def test_greedy_from_gap_finds_preceding_token(self):
        # Given: a cursor sitting in the whitespace gap between two tokens
        gap_col = self.ends[0]  # whitespace after first token
        if gap_col >= self.starts[1]:
            pytest.skip("tokenizer produced no gap; cannot test gap behaviour")
        # When: greedy lookup is requested in the gap
        result = get_token(
            self.toks, cursor_line=0, cursor_col=gap_col, greedy=True
        )
        # Then: it falls back to the preceding token
        assert result is self.toks[0]

    def test_tok_pre_zero_returns_matched_token(self):
        # Given: cursor on the middle token
        col = self.starts[1]
        # When/Then: tok_pre=0 returns the matched token itself
        assert (
            get_token(self.toks, cursor_line=0, cursor_col=col, tok_pre=0)
            is self.toks[1]
        )

    def test_tok_pre_one_returns_predecessor(self):
        # Given: cursor on the middle token
        col = self.starts[1]
        # When/Then: tok_pre=1 returns the token before it
        assert (
            get_token(self.toks, cursor_line=0, cursor_col=col, tok_pre=1)
            is self.toks[0]
        )

    def test_tok_pre_beyond_list_start_returns_none(self):
        # Given: cursor on the first token
        col = self.starts[0]
        # When/Then: tok_pre=1 has no predecessor to return, so None
        assert (
            get_token(self.toks, cursor_line=0, cursor_col=col, tok_pre=1)
            is None
        )

    def test_tok_pre_uses_enumerate_index_not_list_index(self, make_tokens):
        """Regression: must use enumerate() index, not list.index().

        Tokens with identical values would cause list.index() to always
        return 0, breaking tok_pre for the second and third token.
        Three tokens with the same identifier 'x' are used to reproduce
        the scenario.
        """
        # Given: three tokens with the identical value "x"
        toks = make_tokens("x x x")
        assert len(toks) == 3
        locs = [get_location(t).range for t in toks]
        starts = [r.start.character for r in locs]
        # When: cursor is on the third token with tok_pre=1
        # Cursor on third token, tok_pre=1 must give toks[1], not toks[0]
        result = get_token(
            toks, cursor_line=0, cursor_col=starts[2], tok_pre=1
        )
        # Then: the second token (by position, not by list.index()) is returned
        assert result is toks[1]


# ---------------------------------------------------------------------------
# SEMANTIC_TOKEN_TYPES ordering
# ---------------------------------------------------------------------------


class TestSemanticTokenTypes:
    _EXPECTED = [
        "keyword",  # 0
        "comment",  # 1
        "string",  # 2
        "number",  # 3
        "operator",  # 4
        "namespace",  # 5
        "type",  # 6
        "variable",  # 7
        "enumMember",  # 8
        "property",  # 9
    ]

    def test_has_ten_entries(self):
        # Given/When/Then: the legend has exactly the 10 expected entries
        assert len(SEMANTIC_TOKEN_TYPES) == 10

    @pytest.mark.parametrize("idx,name", list(enumerate(_EXPECTED)))
    def test_entry_at_index(self, idx, name):
        # Given/When/Then: each index maps to its expected token-type name
        assert SEMANTIC_TOKEN_TYPES[idx] == name


# ---------------------------------------------------------------------------
# Helpers shared by get_ast_entity and get_token_semantic_type tests
# ---------------------------------------------------------------------------

# Index map derived from the protocol-defined list so tests stay in sync.
_IDX = {name: i for i, name in enumerate(SEMANTIC_TOKEN_TYPES)}


def _real_tok(
    kind_content_map: dict, target_kind: str, ast_link=None
) -> "trlc.lexer.Token":
    """Return a real trlc.lexer.Token of the given kind, with ast_link set.

    We use the real TRLC lexer so that:
    * isinstance(tok, trlc.lexer.Token) is True (satisfies get_ast_entity assert)
    * tok.location and tok.location.lexer.content are real objects
    * Only ast_link is injected manually since the unit tests control it

    kind_content_map maps TRLC token kinds to example source strings.
    """
    content = kind_content_map.get(target_kind, "x")
    mh = trlc.errors.Message_Handler()
    lexer = trlc.lexer.TRLC_Lexer(mh, "test.rsl", content)
    tok = None
    while True:
        try:
            t = lexer.token()
        except Exception:  # pylint: disable=broad-exception-caught  # TRLC lexer may raise various parse errors
            break
        if t is None:
            break
        tok = t
        break  # take the first token
    if tok is None:
        # Fallback: wrap content as IDENTIFIER
        mh2 = trlc.errors.Message_Handler()
        lex2 = trlc.lexer.TRLC_Lexer(mh2, "test.rsl", "identifier_name")
        tok = lex2.token()
        tok.kind = target_kind  # type: ignore[attr-defined]
    tok.ast_link = ast_link  # type: ignore[attr-defined]
    return tok


# Source strings for each static kind so _real_tok can lex them
_KIND_CONTENT = {
    "KEYWORD": "package",
    "COMMENT": "// a comment",
    "STRING": '"hello"',
    "INTEGER": "42",
    "DECIMAL": "3.14",
    "OPERATOR": ">=",
    "IDENTIFIER": "my_name",
}


class TestGetAstEntity:
    """Covers all four ast_link indirection branches and the None fallback.

    Real Token objects (from the make_tokens fixture) are used so that the
    isinstance(token, trlc.lexer.Token) assert inside get_ast_entity passes
    without mocking the token itself.  Only ast_link is replaced.
    """

    def _tok(self, make_tokens):
        toks = make_tokens("foo")
        assert toks, "expected at least one token from 'foo'"
        return toks[0]

    def test_entity_link_returned_directly(self, make_tokens):
        # Given: a token whose ast_link is already an Entity
        tok = self._tok(make_tokens)
        entity = MagicMock(spec=trlc.ast.Entity)
        tok.ast_link = entity
        # When/Then: get_ast_entity returns that Entity directly
        assert get_ast_entity(tok) is entity

    def test_name_reference_returns_entity_attribute(self, make_tokens):
        # Given: a token whose ast_link is a Name_Reference pointing to an Entity
        tok = self._tok(make_tokens)
        entity = MagicMock(spec=trlc.ast.Entity)
        ref = MagicMock(spec=trlc.ast.Name_Reference)
        ref.entity = entity
        tok.ast_link = ref
        # When/Then: get_ast_entity follows the indirection to the Entity
        assert get_ast_entity(tok) is entity

    def test_record_reference_returns_target_attribute(self, make_tokens):
        # Given: a token whose ast_link is a Record_Reference pointing to a target
        tok = self._tok(make_tokens)
        target = MagicMock(spec=trlc.ast.Entity)
        ref = MagicMock(spec=trlc.ast.Record_Reference)
        ref.target = target
        tok.ast_link = ref
        # When/Then: get_ast_entity follows the indirection to the target
        assert get_ast_entity(tok) is target

    def test_enumeration_literal_returns_value_attribute(self, make_tokens):
        # Given: a token whose ast_link is an Enumeration_Literal wrapping a value
        tok = self._tok(make_tokens)
        value = MagicMock(spec=trlc.ast.Entity)
        lit = MagicMock(spec=trlc.ast.Enumeration_Literal)
        lit.value = value
        tok.ast_link = lit
        # When/Then: get_ast_entity follows the indirection to the value
        assert get_ast_entity(tok) is value

    def test_unrecognised_link_returns_none(self, make_tokens):
        # Given: a token whose ast_link is an unrecognized plain object
        tok = self._tok(make_tokens)
        tok.ast_link = object()  # plain object — no isinstance match
        # When/Then: get_ast_entity returns None
        assert get_ast_entity(tok) is None


# ---------------------------------------------------------------------------
# get_token_semantic_type
# ---------------------------------------------------------------------------


class TestGetTokenSemanticType:
    """Covers the static kind map, multi-line skip, and all IDENTIFIER
    AST-dispatch branches."""

    @pytest.mark.parametrize(
        "kind,name",
        [
            ("KEYWORD", "keyword"),
            ("COMMENT", "comment"),
            ("STRING", "string"),
            ("INTEGER", "number"),
            ("DECIMAL", "number"),
            ("OPERATOR", "operator"),
        ],
    )
    def test_static_kind_mapping(self, kind, name):
        # Given/When/Then: each static token kind maps to its expected semantic type
        assert (
            get_token_semantic_type(_real_tok(_KIND_CONTENT, kind))
            == _IDX[name]
        )

    def test_multiline_token_skipped(self):
        # Given: a STRING token whose content spans multiple lines
        content = '"start\ncontinuation"'
        tok = _real_tok(_KIND_CONTENT, "STRING")
        # Override location content to contain a newline
        tok.location.lexer.content = content
        tok.location.start_pos = 0
        tok.location.end_pos = len(content) - 1
        # When/Then: it is skipped (no semantic type), since LSP tokens are single-line
        assert get_token_semantic_type(tok) is None

    def test_unknown_kind_returns_none(self):
        # Given: a token with an unrecognized kind
        tok = _real_tok(_KIND_CONTENT, "IDENTIFIER")
        tok.kind = "PUNCTUATION"
        tok.ast_link = None
        # When/Then: no semantic type is returned
        assert get_token_semantic_type(tok) is None

    def test_identifier_without_ast_link_skipped(self):
        # Given/When: an IDENTIFIER token with no ast_link
        # Then: no semantic type can be determined, so None
        assert (
            get_token_semantic_type(
                _real_tok(_KIND_CONTENT, "IDENTIFIER", ast_link=None)
            )
            is None
        )

    def test_identifier_package_is_namespace(self):
        # Given: an IDENTIFIER token linked to a Package
        tok = _real_tok(
            _KIND_CONTENT,
            "IDENTIFIER",
            ast_link=MagicMock(spec=trlc.ast.Package),
        )
        # When/Then: it is classified as "namespace"
        assert get_token_semantic_type(tok) == _IDX["namespace"]

    def test_identifier_record_type_is_type(self):
        # Given: an IDENTIFIER token linked to a Record_Type
        tok = _real_tok(
            _KIND_CONTENT,
            "IDENTIFIER",
            ast_link=MagicMock(spec=trlc.ast.Record_Type),
        )
        # When/Then: it is classified as "type"
        assert get_token_semantic_type(tok) == _IDX["type"]

    def test_identifier_tuple_type_is_type(self):
        # Given: an IDENTIFIER token linked to a Tuple_Type
        tok = _real_tok(
            _KIND_CONTENT,
            "IDENTIFIER",
            ast_link=MagicMock(spec=trlc.ast.Tuple_Type),
        )
        # When/Then: it is classified as "type"
        assert get_token_semantic_type(tok) == _IDX["type"]

    def test_identifier_enumeration_type_is_type(self):
        # Given: an IDENTIFIER token linked to an Enumeration_Type
        tok = _real_tok(
            _KIND_CONTENT,
            "IDENTIFIER",
            ast_link=MagicMock(spec=trlc.ast.Enumeration_Type),
        )
        # When/Then: it is classified as "type"
        assert get_token_semantic_type(tok) == _IDX["type"]

    def test_identifier_builtin_type_is_type(self):
        # Given: an IDENTIFIER token linked to a Builtin_Type
        tok = _real_tok(
            _KIND_CONTENT,
            "IDENTIFIER",
            ast_link=MagicMock(spec=trlc.ast.Builtin_Type),
        )
        # When/Then: it is classified as "type"
        assert get_token_semantic_type(tok) == _IDX["type"]

    def test_identifier_enum_literal_spec_is_enum_member(self):
        # Given: an IDENTIFIER token linked to an Enumeration_Literal_Spec
        tok = _real_tok(
            _KIND_CONTENT,
            "IDENTIFIER",
            ast_link=MagicMock(spec=trlc.ast.Enumeration_Literal_Spec),
        )
        # When/Then: it is classified as "enumMember"
        assert get_token_semantic_type(tok) == _IDX["enumMember"]

    def test_identifier_composite_component_is_property(self):
        # Given: an IDENTIFIER token linked to a Composite_Component
        tok = _real_tok(
            _KIND_CONTENT,
            "IDENTIFIER",
            ast_link=MagicMock(spec=trlc.ast.Composite_Component),
        )
        # When/Then: it is classified as "property"
        assert get_token_semantic_type(tok) == _IDX["property"]

    def test_identifier_unclassified_entity_is_variable(self):
        # Given: an IDENTIFIER token linked to a plain Entity (no specific subtype)
        # Entity base class: not Package/Record_Type/etc., so falls to "variable".
        tok = _real_tok(
            _KIND_CONTENT,
            "IDENTIFIER",
            ast_link=MagicMock(spec=trlc.ast.Entity),
        )
        # When/Then: it falls back to "variable"
        assert get_token_semantic_type(tok) == _IDX["variable"]


class TestFolderForUri:
    """Tests for folder_for_uri() helper."""

    def test_single_folder_match(self):
        # Given: a single workspace folder, and a file uri inside it
        folders = {
            "file:///home/user/project": "project",
        }
        uri = "file:///home/user/project/src/main.trlc"
        # When/Then: it resolves to that folder
        assert folder_for_uri(uri, folders) == "file:///home/user/project"

    def test_multi_folder_longest_prefix_match(self):
        # Given: two nested workspace folders, and a file uri inside the inner one
        folders = {
            "file:///home/user": "user",
            "file:///home/user/project": "project",
        }
        uri = "file:///home/user/project/src/main.trlc"
        # When/Then: it resolves to the longest (most specific) matching folder
        assert folder_for_uri(uri, folders) == "file:///home/user/project"

    def test_no_match_returns_none(self):
        # Given: a workspace folder, and a file uri outside of it
        folders = {
            "file:///home/user/project": "project",
        }
        uri = "file:///tmp/main.trlc"
        # When/Then: no folder matches, so None is returned
        assert folder_for_uri(uri, folders) is None

    def test_untitled_doc_returns_none(self):
        # Given: a workspace folder, and an unsaved "untitled:" document uri
        folders = {
            "file:///home/user/project": "project",
        }
        uri = "untitled:Untitled-1"
        # When/Then: no folder matches an untitled document, so None is returned
        assert folder_for_uri(uri, folders) is None

    def test_sibling_directory_sharing_a_name_prefix_does_not_match(self):
        """A folder path must not match a sibling directory whose name
        merely starts with the same characters — '/home/foo' is not a
        prefix-match container for '/home/foo-bar/x.trlc'."""
        # Given: a workspace folder "/home/foo", and a file in the SIBLING
        # directory "/home/foo-bar" (name shares a string prefix, but is not
        # nested under it)
        folders = {
            "file:///home/foo": "foo",
        }
        uri = "file:///home/foo-bar/x.trlc"
        # When/Then: no folder matches — a bare string-prefix check would
        # wrongly say "/home/foo" contains this file
        assert folder_for_uri(uri, folders) is None

    def test_percent_encoded_spaces_are_decoded_before_matching(self):
        """A folder path containing a space, and a document URI with that
        space percent-encoded (as real editors send it), must still match —
        raw string comparison without decoding would never see them as
        equal."""
        # Given/When/Then: A folder path containing a space, and a document URI with that space
        # percent- encoded (as real editors send it), must still match — raw string comparison
        # without decoding would never see them as equal
        folders = {
            "file:///home/user/My%20Project": "My Project",
        }
        uri = "file:///home/user/My%20Project/src/main.trlc"
        assert folder_for_uri(uri, folders) == "file:///home/user/My%20Project"

    def test_document_at_exact_folder_root_matches(self):
        """A document whose path is exactly the folder root (not nested
        under it) still matches — the equality branch, not just the
        nested-under-a-separator branch."""
        # Given/When/Then: A document whose path is exactly the folder root (not nested under it)
        # still matches — the equality branch, not just the nested-under-a-separator branch
        folders = {
            "file:///home/user/project": "project",
        }
        uri = "file:///home/user/project"
        assert folder_for_uri(uri, folders) == "file:///home/user/project"


# ---------------------------------------------------------------------------
# resolve_document_tokens
# ---------------------------------------------------------------------------


def _fake_parsed(content: str, tokens=None):
    """Minimal stand-in for a vsm.all_files value: just enough (.lexer.content,
    .lexer.tokens) for resolve_document_tokens / _ast_tokens_are_stale."""
    return SimpleNamespace(
        lexer=SimpleNamespace(content=content, tokens=tokens or [MagicMock()])
    )


def _fake_ls(file_path: str, parsed, live_source: str):
    """Minimal ls stub: one active context with *parsed* registered under
    *file_path*, and a workspace whose live buffer for the uri is
    *live_source* (independently of what *parsed* was parsed from — the
    whole point of the staleness check).

    Keys ``all_files`` by :func:`normalize_fs_path` (*file_path*), same as
    the real ``Vscode_Source_Manager.register_file`` does — resolve_
    document_tokens looks up under that same normalized form, and on
    Windows a raw literal like ``"/a.trlc"`` would never match it.
    """
    context = SimpleNamespace(
        vsm=SimpleNamespace(all_files={normalize_fs_path(file_path): parsed})
    )
    context.snapshot = lambda: context
    doc = MagicMock(source=live_source)
    workspace = MagicMock()
    workspace.get_text_document.return_value = doc
    return SimpleNamespace(
        get_context_for_uri=lambda _uri: context,
        workspace=workspace,
    )


class TestResolveDocumentTokens:
    """The AST-available path must not be trusted once the live editor
    buffer has moved on from what was actually parsed — see the module
    docstring's rationale in _ast_tokens_are_stale. Regression coverage for
    a real observed bug: BAZEL mode's parse cycle can take many seconds
    (bazel query + CVC5 verify), during which further keystrokes make the
    last-parsed AST's token positions silently wrong for the live cursor."""

    def test_matching_content_uses_ast_tokens(self):
        # Given: the last parse's content matches the live buffer exactly
        content = "package A\n"
        parsed = _fake_parsed(content)
        ls = _fake_ls("/a.trlc", parsed, live_source=content)

        # When: resolving tokens
        result = resolve_document_tokens(ls, "file:///a.trlc", "/a.trlc")

        # Then: the AST-linked tokens are used
        assert result.ast_available is True
        assert result.tokens is parsed.lexer.tokens

    def test_stale_content_falls_back_to_lex_only(self):
        """The live buffer has been edited since the last completed parse
        (e.g. a keystroke landed while a slow BAZEL-mode parse cycle was
        still running) — the stale AST tokens' positions no longer line up
        with the live cursor, so a fresh lex of the current buffer must be
        used instead."""
        # Given: the last parse's content no longer matches the live buffer
        parsed = _fake_parsed("package A\n")
        live_source = "package A\n\nA.Foo foo { x = 1 }\n"
        ls = _fake_ls("/a.trlc", parsed, live_source=live_source)

        # When: resolving tokens
        result = resolve_document_tokens(ls, "file:///a.trlc", "/a.trlc")

        # Then: it falls back to a fresh lex of the live buffer, not the
        # stale (position-mismatched) AST tokens
        assert result.ast_available is False
        assert result.tokens is not parsed.lexer.tokens
        assert len(result.tokens) > 0

    def test_no_live_buffer_trusts_the_ast(self):
        """When the live buffer can't be read at all (e.g. a disk-backed
        uri with no in-memory copy), there's nothing to compare against —
        the AST is trusted rather than treated as stale."""
        # Given: get_text_document raises (no in-memory buffer for this uri)
        content = "package A\n"
        parsed = _fake_parsed(content)
        ls = _fake_ls("/a.trlc", parsed, live_source=content)
        ls.workspace.get_text_document.side_effect = OSError("no buffer")

        # When: resolving tokens
        result = resolve_document_tokens(ls, "file:///a.trlc", "/a.trlc")

        # Then: the AST-linked tokens are still used
        assert result.ast_available is True
        assert result.tokens is parsed.lexer.tokens


# ---------------------------------------------------------------------------
# resolve_open_file
# ---------------------------------------------------------------------------


def _open_file_params(uri="file:///a.trlc", line=0, character=0):
    return SimpleNamespace(
        text_document=SimpleNamespace(uri=uri),
        position=SimpleNamespace(line=line, character=character),
    )


def _ls_with_context(context):
    return SimpleNamespace(
        get_context_for_uri=lambda _uri: context,
        window_show_message=MagicMock(),
    )


class TestResolveOpenFile:
    """resolve_open_file — the shared entry point rename uses to get from an
    LSP position to a fully parsed file object; no lex-only fallback, unlike
    resolve_document_tokens (see that class's own docstring for why)."""

    def test_returns_none_and_shows_message_when_no_active_scope(self):
        # Given: a uri with no active parse context
        ls = _ls_with_context(None)
        # When: resolving the open file
        result = resolve_open_file(ls, _open_file_params())
        # Then: it returns None and shows the "please wait" message
        assert result is None
        ls.window_show_message.assert_called_once()

    def test_returns_none_and_shows_message_when_file_not_yet_parsed(self):
        # Given: an active scope that hasn't parsed this file yet
        context = SimpleNamespace(vsm=SimpleNamespace(all_files={}))
        context.snapshot = lambda: context
        ls = _ls_with_context(context)
        # When: resolving the open file
        result = resolve_open_file(ls, _open_file_params())
        # Then: it returns None and shows the "please wait" message
        assert result is None
        ls.window_show_message.assert_called_once()

    def test_returns_uri_file_path_and_file_obj_when_parsed(self, make_tokens):
        # Given: a scope whose vsm already parsed this file, registered
        # under the same normalized path resolve_open_file's lookup uses
        # (real registration always normalizes — see
        # Vscode_Source_Manager.register_file; "/a.trlc" is a POSIX-only
        # literal that wouldn't round-trip through path_from_uri on
        # Windows, so both the key and the expected return value are
        # derived from path_from_uri instead of hardcoded).
        uri = "file:///a.trlc"
        file_path = path_from_uri(uri)
        tokens = make_tokens("package A\n")
        file_obj = SimpleNamespace(lexer=SimpleNamespace(tokens=tokens))
        context = SimpleNamespace(
            vsm=SimpleNamespace(
                all_files={normalize_fs_path(file_path): file_obj}
            )
        )
        context.snapshot = lambda: context
        ls = _ls_with_context(context)
        # When: resolving the open file
        result = resolve_open_file(ls, _open_file_params(uri=uri))
        # Then: it returns the uri, filesystem path, and the parsed file object
        assert result == (uri, file_path, file_obj)
        ls.window_show_message.assert_not_called()
