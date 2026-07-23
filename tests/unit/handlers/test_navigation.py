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
"""Unit tests for trlc_lsp.handlers.navigation (goto def + find references).

Token positions (0-based) in types.rsl:
  line=2 col=5  → 'MyRecord'  (Record_Type)      TRLC 1-based: line=3 col=6
  line=8 col=4  → 'Color'     (Enumeration_Type) TRLC 1-based: line=9 col=5

Token positions in sample.trlc:
  line=2 col=0  → 'MyRecord'  (Record_Type reference)
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=too-few-public-methods
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment —
# some groupings only need a single test.

# pylint: disable=protected-access  # tests legitimately access private members to verify internal state

# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

from unittest.mock import Mock, patch

import pytest
import trlc.ast
import trlc.lexer
from lsprotocol.types import (
    TEXT_DOCUMENT_DEFINITION,
    TEXT_DOCUMENT_REFERENCES,
    TEXT_DOCUMENT_TYPE_DEFINITION,
    DefinitionParams,
    Position,
    ReferenceContext,
    ReferenceParams,
    TextDocumentIdentifier,
    TypeDefinitionParams,
)

from server.handlers import navigation as nav_mod
from server.token_utils import uri_from_file


def _typedef_params(uri, line, col):
    return TypeDefinitionParams(
        text_document=TextDocumentIdentifier(uri=uri),
        position=Position(line=line, character=col),
    )


def _def_params(uri, line, col):
    return DefinitionParams(
        text_document=TextDocumentIdentifier(uri=uri),
        position=Position(line=line, character=col),
    )


def _ref_params(uri, line, col):
    return ReferenceParams(
        text_document=TextDocumentIdentifier(uri=uri),
        position=Position(line=line, character=col),
        context=ReferenceContext(include_declaration=True),
    )


@pytest.fixture
def nav_fns(fake_ls):
    """Nav fns."""
    nav_mod.register(fake_ls)
    return (
        fake_ls.get_handler(TEXT_DOCUMENT_TYPE_DEFINITION),
        fake_ls.get_handler(TEXT_DOCUMENT_REFERENCES),
        fake_ls.get_handler(TEXT_DOCUMENT_DEFINITION),
    )


class TestGotoTypeDefinition:
    def test_goto_record_type_returns_location(
        self, fake_ls, nav_fns, rsl_path
    ):
        # Given: a parsed schema file, cursor on the 'MyRecord' type reference
        goto_fn, _, _ = nav_fns
        uri = uri_from_file(rsl_path)
        params = _typedef_params(uri, line=2, col=5)  # 'MyRecord'
        # When: go-to-type-definition is requested
        result = goto_fn(fake_ls, params)
        # Then: a location is returned
        assert result is not None

    def test_goto_on_keyword_returns_none(self, fake_ls, nav_fns, rsl_path):
        # Given: a parsed schema file, cursor on the 'type' keyword
        goto_fn, _, _ = nav_fns
        uri = uri_from_file(rsl_path)
        params = _typedef_params(uri, line=2, col=0)  # 'type' keyword
        # When: go-to-type-definition is requested
        result = goto_fn(fake_ls, params)
        # Then: no location is returned
        assert result is None

    def test_goto_on_unparsed_file_returns_none(self, fake_ls, nav_fns):
        # Given: a uri that was never parsed
        goto_fn, _, _ = nav_fns
        params = _typedef_params("file:///unknown.rsl", line=0, col=0)
        # When: go-to-type-definition is requested against that uri
        result = goto_fn(fake_ls, params)
        # Then: no location is returned
        assert result is None


class TestGotoDefinition:
    """`textDocument/definition` — what VS Code's Ctrl+click/F12 actually
    send (distinct from "Go to Type Definition" above); registered
    alongside it this round, sharing the same resolution logic."""

    def test_goto_record_type_returns_location(
        self, fake_ls, nav_fns, rsl_path
    ):
        # Given: a parsed schema file, cursor on the 'MyRecord' type reference
        _, _, def_fn = nav_fns
        uri = uri_from_file(rsl_path)
        params = _def_params(uri, line=2, col=5)  # 'MyRecord'
        # When: go-to-definition (Ctrl+click/F12) is requested
        result = def_fn(fake_ls, params)
        # Then: a location is returned
        assert result is not None

    def test_goto_on_keyword_returns_none(self, fake_ls, nav_fns, rsl_path):
        # Given: a parsed schema file, cursor on the 'type' keyword
        _, _, def_fn = nav_fns
        uri = uri_from_file(rsl_path)
        params = _def_params(uri, line=2, col=0)  # 'type' keyword
        # When: go-to-definition is requested
        result = def_fn(fake_ls, params)
        # Then: no location is returned
        assert result is None

    def test_goto_on_unparsed_file_returns_none(self, fake_ls, nav_fns):
        # Given: a uri that was never parsed
        _, _, def_fn = nav_fns
        params = _def_params("file:///unknown.rsl", line=0, col=0)
        # When: go-to-definition is requested against that uri
        result = def_fn(fake_ls, params)
        # Then: no location is returned
        assert result is None


class TestGotoDefinitionUnresolvedReference:
    """get_ast_entity legitimately returns None (e.g. a Record_Reference
    whose .target never got resolved, such as an incomplete cross-package
    reference) — the caller must return None instead of letting
    get_location's TypeError ("obj must be Token or Node, got NoneType")
    propagate as a raw LSP protocol error."""

    def test_unresolved_reference_returns_none_without_crash(
        self, fake_ls, nav_fns, rsl_path
    ):
        # Given: a token whose ast_link resolves to no AST entity
        _, _, def_fn = nav_fns
        uri = uri_from_file(rsl_path)
        params = _def_params(uri, line=2, col=5)

        fake_tok = Mock(kind="IDENTIFIER")
        fake_tok.ast_link = Mock()

        # When: go-to-definition is requested, with the shared resolver
        # forced to the "token found but no AST entity" case it documents
        # (e.g. get_ast_entity returning None) but which is otherwise hard
        # to reach through a real (necessarily incomplete) parse fixture
        with patch.object(
            nav_mod,
            "resolve_identifier_at_position",
            return_value=(fake_tok, None),
        ):
            result = def_fn(fake_ls, params)

        # Then: no location, and no exception propagated
        assert result is None


class TestGotoDefinitionByNameFallback:
    """A Record_Reference whose ``.target`` never got filled in (TRLC skips
    its deferred cross-reference resolution pass for a whole file that had
    any parse error, even for statements with no error of their own — see
    _resolve_definition_location's comment) must still navigate when the
    referenced object genuinely exists in scope, resolved by name instead
    of via the unresolved .target link."""

    def test_unresolved_target_resolves_by_name_against_package_symbols(
        self, fake_ls, nav_fns, rsl_path, trlc_path
    ):
        # Given: the real "Types" package (from the parsed fixture) holds a
        # real Record_Object named "example" (declared in sample.trlc)
        _, _, def_fn = nav_fns
        rsl_uri = uri_from_file(rsl_path)
        context = fake_ls.get_context_for_uri(rsl_uri)
        types_pkg = context.snapshot().vsm.stab.table["types"]
        assert "example" in types_pkg.symbols.table

        # A Record_Reference-shaped stand-in whose .target was never filled
        # in, but whose .package/.name correctly identify "example" — same
        # shape TRLC leaves behind when the deferred resolution pass was
        # skipped for the referencing file.
        link = Mock()
        link.__class__ = trlc.ast.Record_Reference
        link.target = None
        link.package = types_pkg
        link.name = "example"

        fake_tok = Mock(kind="IDENTIFIER", ast_link=link)
        fake_tok.__class__ = trlc.lexer.Token
        trlc_uri = uri_from_file(trlc_path)
        params = _def_params(trlc_uri, line=2, col=0)

        # When: go-to-definition is requested with that token at the cursor
        with patch.object(
            nav_mod,
            "resolve_identifier_at_position",
            return_value=(fake_tok, None),
        ):
            result = def_fn(fake_ls, params)

        # Then: navigation succeeds via the by-name fallback, not None
        assert result is not None

    def test_unresolved_target_with_no_matching_name_returns_none(
        self, fake_ls, nav_fns, rsl_path, trlc_path
    ):
        # Given: the same unresolved shape, but naming something that
        # doesn't exist anywhere in the package
        _, _, def_fn = nav_fns
        rsl_uri = uri_from_file(rsl_path)
        context = fake_ls.get_context_for_uri(rsl_uri)
        types_pkg = context.snapshot().vsm.stab.table["types"]

        link = Mock()
        link.__class__ = trlc.ast.Record_Reference
        link.target = None
        link.package = types_pkg
        link.name = "NoSuchObject"

        fake_tok = Mock(kind="IDENTIFIER", ast_link=link)
        fake_tok.__class__ = trlc.lexer.Token
        trlc_uri = uri_from_file(trlc_path)
        params = _def_params(trlc_uri, line=2, col=0)

        with patch.object(
            nav_mod,
            "resolve_identifier_at_position",
            return_value=(fake_tok, None),
        ):
            result = def_fn(fake_ls, params)

        # Then: the dead-end lookup degrades to no navigation, not a crash
        assert result is None


class TestFindReferences:
    def test_references_for_record_type_returns_locations(
        self, fake_ls, nav_fns, rsl_path
    ):
        # Given: a parsed schema file, cursor on the 'MyRecord' type declaration
        _, refs_fn, _ = nav_fns
        uri = uri_from_file(rsl_path)
        params = _ref_params(uri, line=2, col=5)  # 'MyRecord'
        # When: find-references is requested
        result = refs_fn(fake_ls, params)
        # Then: at least the definition and its usage are returned
        # Should find at least the definition in types.rsl and the usage in sample.trlc
        assert result is not None
        assert len(result) >= 1

    def test_references_on_keyword_returns_none(
        self, fake_ls, nav_fns, rsl_path
    ):
        # Given: a parsed schema file, cursor on the 'type' keyword
        _, refs_fn, _ = nav_fns
        uri = uri_from_file(rsl_path)
        params = _ref_params(uri, line=2, col=0)  # 'type' keyword
        # When: find-references is requested
        result = refs_fn(fake_ls, params)
        # Then: no references are returned
        assert result is None

    def test_references_on_unparsed_file_returns_none(self, fake_ls, nav_fns):
        # Given: a uri that was never parsed
        _, refs_fn, _ = nav_fns
        params = _ref_params("file:///unknown.rsl", line=0, col=0)
        # When: find-references is requested against that uri
        result = refs_fn(fake_ls, params)
        # Then: no references are returned, and a "please wait" message is shown
        assert result is None
        # Should also show the "please wait" message
        assert len(fake_ls._msgs) > 0

    def test_references_never_cross_scope_boundary(
        self, fake_ls_two_scopes, rsl_path, other_rsl_path, other_trlc_path
    ):
        """A reference search in one scope must never return locations from
        a different, independently-parsed scope — even though both scopes
        are active in the same server at once."""
        # Given: two independently-parsed scopes active at once
        nav_mod.register(fake_ls_two_scopes)
        refs_fn = fake_ls_two_scopes.get_handler(TEXT_DOCUMENT_REFERENCES)

        uri = uri_from_file(rsl_path)
        params = _ref_params(
            uri, line=2, col=5
        )  # 'MyRecord' in scope "test-scope"
        # When: find-references is requested for a file in "test-scope"
        result = refs_fn(fake_ls_two_scopes, params)

        # Then: no locations from the other scope are ever returned
        assert result is not None
        other_uris = {
            uri_from_file(other_rsl_path),
            uri_from_file(other_trlc_path),
        }
        assert all(loc.uri not in other_uris for loc in result)
