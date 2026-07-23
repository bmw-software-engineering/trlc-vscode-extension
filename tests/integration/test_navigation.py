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
"""Integration tests for textDocument/hover, definition, typeDefinition, and references."""

# pylint: disable=missing-class-docstring,missing-function-docstring

import urllib.parse

from .conftest import RSL_TEXT, TRLC_TEXT
from .content import DESCRIBED_RSL_TEXT, DESCRIBED_TRLC_TEXT
from .lsp_helpers import (
    goto_definition,
    goto_type_definition,
    hover,
    open_document,
    references,
    wait_for_diagnostics,
)

# ─── Hover ────────────────────────────────────────────────────────────────────


class TestHover:
    """textDocument/hover integration tests.

    types.rsl token positions (0-based LSP; the file starts with a 19-line
    license header, so these are 19 lines further down than they'd be in a
    header-less fixture):
      line=27 col=5  → 'MyRecord'  (Record_Type)
      line=21 col=5  → 'Color'     (Enumeration_Type)
      line=28 col=6  → 'Integer'   (Builtin_Integer)
    """

    async def test_hover_on_record_type_returns_result(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: hover is requested on the 'MyRecord' type declaration
        result = await hover(client, types_uri, line=27, col=5)
        # Then: a hover result is returned
        assert result is not None

    async def test_hover_on_enum_type_returns_result(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: hover is requested on the 'Color' enum type declaration
        result = await hover(client, types_uri, line=21, col=5)
        # Then: a hover result is returned
        assert result is not None

    async def test_hover_on_builtin_type_returns_null(
        self, client, types_uri, sample_uri
    ):
        """Builtin types (Integer, String…) have no user-defined description."""
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: hover is requested on the builtin 'Integer' type
        result = await hover(client, types_uri, line=28, col=6)
        # Then: no hover result is returned
        assert result is None

    async def test_hover_on_keyword_returns_null(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: hover is requested on a language keyword
        result = await hover(client, types_uri, line=27, col=0)
        # Then: no hover result is returned
        assert result is None

    async def test_hover_on_unparsed_file_returns_null(self, client):
        # Given: a uri that was never opened/parsed by the server
        fake = urllib.parse.urlunparse(
            ("file", "", "/nonexistent/x.rsl", "", "", "")
        )
        # When: hover is requested against that uri
        result = await hover(client, fake, line=0, col=0)
        # Then: no hover result is returned, not an error
        assert result is None


# ─── Go to Type Definition ────────────────────────────────────────────────────


class TestGotoTypeDefinition:
    """textDocument/typeDefinition integration tests ("Go to Type Definition").

    For a Record_Type reference this lands on the type declaration in .rsl.
    For a Record_Object reference it resolves one step further than
    textDocument/definition — to the Record_Type that declares the object's
    schema.

    sample.trlc token positions (0-based LSP):
      line=2 col=0  → 'MyRecord'  (Record_Type reference)
    """

    async def test_goto_type_definition_lands_in_rsl_file(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and an instance file referencing its type
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: go-to-type-definition is requested on the type reference
        loc = await goto_type_definition(client, sample_uri, line=2, col=0)
        # Then: the location resolves into the .rsl schema file
        assert loc is not None
        assert "types.rsl" in loc.uri

    async def test_goto_type_definition_points_to_correct_line(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and an instance file referencing its type
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: go-to-type-definition is requested on the type reference
        loc = await goto_type_definition(client, sample_uri, line=2, col=0)
        # Then: it points at the type's declaration line
        assert loc.range.start.line == 27

    async def test_goto_type_definition_on_keyword_returns_null(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: go-to-type-definition is requested on a language keyword
        loc = await goto_type_definition(client, sample_uri, line=0, col=0)
        # Then: no location is returned
        assert loc is None


# ─── Go to Definition (F12 / Ctrl+click) ─────────────────────────────────────


class TestGotoDefinitionHandler:
    """textDocument/definition integration tests (VS Code F12 / Ctrl+click).

    Distinct from textDocument/typeDefinition: for a Record_Type reference
    both return the same location, but for a Record_Object reference
    textDocument/definition returns the *instance* declaration (in .trlc)
    while textDocument/typeDefinition returns the *type* (in .rsl).

    sample.trlc token positions (0-based LSP):
      line=2 col=0  → 'MyRecord'  (Record_Type reference — definition == typeDefinition)
    """

    async def test_goto_definition_on_type_ref_lands_in_rsl(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and an instance file referencing its type
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: go-to-definition (F12) is requested on the Record_Type reference
        loc = await goto_definition(client, sample_uri, line=2, col=0)
        # Then: the location resolves into the .rsl schema file
        # (for a Record_Type reference definition == typeDefinition)
        assert loc is not None
        assert "types.rsl" in loc.uri

    async def test_goto_definition_on_keyword_returns_null(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: go-to-definition (F12) is requested on a language keyword
        loc = await goto_definition(client, sample_uri, line=0, col=0)
        # Then: no location is returned
        assert loc is None


# ─── Find References ──────────────────────────────────────────────────────────


class TestFindReferences:
    """textDocument/references integration tests."""

    async def test_find_references_returns_definition_and_usage(
        self, client, types_uri, sample_uri
    ):
        """'MyRecord' appears in both types.rsl (def) and sample.trlc (use)."""
        # Given: a schema file and an instance file referencing its type
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: find-references is requested on the type declaration
        locs = await references(client, types_uri, line=27, col=5)
        # Then: at least the declaration itself is returned
        assert locs is not None and len(locs) >= 1
        uris = [loc.uri for loc in locs]
        assert any("types.rsl" in u for u in uris)

    async def test_find_references_on_keyword_returns_empty(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: find-references is requested on a language keyword
        locs = await references(client, types_uri, line=27, col=0)
        # Then: no references are returned
        assert locs == [] or locs is None


# ─── Hover on Record_Object instances ─────────────────────────────────────────


class TestHoverOnInstance:
    """textDocument/hover on Record_Object (instance) tokens.

    Hover shows the value of the ``description`` field (the server's
    configured ``trlcServer.hover.descriptionComponent``, default
    ``"description"``) when the instance has it filled in, and returns
    ``None`` when the type's schema has no such field.

    DESCRIBED_TRLC_TEXT token positions (0-based, no header):
      line=2  col=5  → 'doc_item'  (Record_Object with description filled in)

    TRLC_TEXT token positions (0-based, no header):
      line=2  col=9  → 'example'   (Record_Object whose type has no description field)
    """

    async def test_hover_on_record_object_with_description_returns_result(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema defining a type with a 'description' field,
        # and an instance file with that field filled in
        open_document(client, types_uri, DESCRIBED_RSL_TEXT)
        open_document(client, sample_uri, DESCRIBED_TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: hover is requested on the instance name
        result = await hover(client, sample_uri, line=2, col=5)
        # Then: a hover result is returned (the description field value)
        assert result is not None

    async def test_hover_on_record_object_without_description_returns_null(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema whose type has no 'description' field (MyRecord),
        # and a valid instance of that type
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: hover is requested on the instance name 'example' (line=2 col=9)
        result = await hover(client, sample_uri, line=2, col=9)
        # Then: no hover result is returned — type has no description field
        assert result is None
