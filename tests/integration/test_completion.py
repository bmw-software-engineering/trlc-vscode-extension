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
"""Integration tests for textDocument/completion.

Fixture token positions (0-based LSP line/col in types.rsl):
  line=0 col=8  → after 'package ' (trigger ' ' on 'package' keyword)
  line=8 col=4  → 'Color' (Enumeration_Type)

And in sample.trlc:
  line=5 col=18  → after 'c = Color.Red' (dot trigger on Color)
  line=3 col=18  → inside record body after '{'
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

import urllib.parse

from .conftest import TRLC_TEXT as TRLC_WITH_ENUM_FIELD
from .content import (
    RSL_TEXT,
    TRLC_ENUM_TRIGGER,
    TRLC_IMPORT_TRIGGER,
    TRLC_IN_STRING,
    TRLC_RECORD_BRACE_TRIGGER,
)
from .lsp_helpers import completion, open_document, wait_for_diagnostics


class TestCompletion:
    async def test_package_completion_after_space(
        self, client, types_uri, sample_uri
    ):
        """After 'package ', completions include known package names."""
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_WITH_ENUM_FIELD)
        await wait_for_diagnostics(client, sample_uri)

        # When: completion is requested right after 'package '
        items = await completion(
            client, sample_uri, line=0, col=8, trigger_char=" "
        )
        labels = [i.label for i in items]
        # Then: the known package name is suggested
        assert "Types" in labels, (
            f"Expected 'Types' in completion labels, got: {labels}"
        )

    async def test_import_completion_after_space(
        self, client, types_uri, sample_uri
    ):
        """After 'import ', completions include package names."""
        # Given: a schema file open, and an instance file mid-'import '
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_IMPORT_TRIGGER)
        await wait_for_diagnostics(client, sample_uri)

        # When: completion is requested right after 'import '
        items = await completion(
            client, sample_uri, line=1, col=7, trigger_char=" "
        )
        labels = [i.label for i in items]
        # Then: the known package name is suggested
        assert any("Types" in label for label in labels), (
            f"Expected 'Types' in import completion labels, got: {labels}"
        )

    async def test_dot_on_enumeration_type_completes_literals(
        self, client, types_uri, sample_uri
    ):
        """Dot-completion on an Enumeration_Type returns its literal names.

        This is the C-1 regression test: enum-dot completions were previously
        unreachable due to incorrect elif ordering.

        These coordinates are mirrored in tests/e2e/e2e_smoke.json
        (completionDotTrigger) for the VSCode e2e smoke suite — keep both
        in sync.
        """
        # Given: a schema file open, and an instance file mid-'c = Color.'
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_ENUM_TRIGGER)
        await wait_for_diagnostics(client, sample_uri)

        # When: completion is requested right after the dot on the enum type
        items = await completion(
            client, sample_uri, line=5, col=15, trigger_char="."
        )
        labels = [i.label for i in items]
        # Then: all three enumeration literals are suggested
        assert "Red" in labels, (
            f"Expected 'Red' in dot-completion labels: {labels}"
        )
        assert "Green" in labels, (
            f"Expected 'Green' in dot-completion labels: {labels}"
        )
        assert "Blue" in labels, (
            f"Expected 'Blue' in dot-completion labels: {labels}"
        )

    async def test_record_field_completion_after_brace(
        self, client, types_uri, sample_uri
    ):
        """After '{', completions include non-optional record components."""
        # Given: a schema file open, and an instance file mid-'MyRecord wip {'
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_RECORD_BRACE_TRIGGER)
        await wait_for_diagnostics(client, sample_uri)

        # When: completion is requested right after the opening brace
        # line=2 col=14 is right after '{'
        items = await completion(
            client, sample_uri, line=2, col=14, trigger_char="{"
        )
        # Then: the required fields x, y, c are suggested as a scaffold
        assert len(items) >= 1, (
            f"Expected completions after '{{', got: {items}"
        )

    async def test_completion_on_unparsed_file_returns_empty(
        self,
        client,
        types_uri,  # pylint: disable=unused-argument  # fixture opens types.rsl as a side effect; must keep pytest's exact fixture name
    ):
        """Completions on a file that hasn't been opened return empty list."""
        # Given: a uri that was never opened/parsed by the server
        fake_uri = urllib.parse.urlunparse(
            ("file", "", "/nonexistent/file.trlc", "", "", "")
        )
        # When: completion is requested against that uri
        items = await completion(client, fake_uri, line=0, col=0)
        # Then: an empty list is returned, not an error
        assert items == []

    async def test_completion_inside_string_literal_returns_empty(
        self, client, types_uri, sample_uri
    ):
        """Completion inside a string literal has no schema-aware suggestions."""
        # Given: a schema file and an instance file whose last field value
        # is a string literal — the cursor is placed *inside* that literal
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_IN_STRING)
        await wait_for_diagnostics(client, sample_uri)

        # When: completion is requested at a position inside the string value
        # (line=5 col=11 is inside the unclosed string literal " inside")
        items = await completion(client, sample_uri, line=5, col=11)
        labels = [i.label for i in items]
        # Then: no type names or field names from the schema are suggested
        assert "MyRecord" not in labels, (
            f"Expected no schema names in string-literal completions: {labels}"
        )
        assert "x" not in labels, (
            f"Expected no field names in string-literal completions: {labels}"
        )
