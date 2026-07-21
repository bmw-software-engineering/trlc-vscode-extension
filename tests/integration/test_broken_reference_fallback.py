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
"""Regression tests for the broken-reference / forward-reference fallback path.

Background
----------
TRLC's deferred ``resolve_record_references`` pass has no per-element
try/except.  When one entry in a ``derived_from`` list is unresolvable (e.g.
``DoesNotExist``), the entire pass aborts for that record, leaving every
*later* entry in the same list — even a perfectly valid forward reference like
``FeatA`` — with its ``.target`` still ``None``.

The regression being guarded here: before the fallback was added to
hover.py / navigation.py, those handlers silently returned ``None`` / an
empty list for ``FeatA`` even though the referenced object exists and is in
scope.  The fix uses a by-name lookup on ``link.package.symbols`` so that
navigation still works independent of whether TRLC's resolution pass ever ran.

Fixture layout (BROKEN_REF_TRLC_TEXT, 0-based line numbers, no header)
-----------------------------------------------------------------------
  line=2  col=8   → 'CompB'        (Record_Object declaration; missing description
                                    → TRLC reports an error and skips the deferred
                                    resolve_record_references pass for the whole file)
  line=3  col=20  → 'DoesNotExist' (unresolvable name — would abort mid-list pass if it ran)
  line=3  col=34  → 'FeatA'        (valid forward reference; .target stays None because
                                    the whole-file deferred pass was skipped)
  line=6  col=4   → 'FeatA'        (Record_Object declaration)
"""

# pylint: disable=missing-class-docstring,missing-function-docstring

from lsprotocol import types

from .content import BROKEN_REF_RSL_TEXT, BROKEN_REF_TRLC_TEXT
from .lsp_helpers import (
    goto_definition,
    goto_type_definition,
    hover,
    open_document,
    references,
    wait_for_diagnostics,
)


class TestBrokenReferenceFallback:
    """End-to-end regression suite for the broken-reference fallback path.

    Each test opens both fixture files, confirms the error diagnostic for
    ``DoesNotExist`` is present (proving we are in the broken-reference
    scenario), then exercises a different navigation feature on the *valid*
    forward reference ``FeatA`` that lives after the broken entry.
    """

    async def test_hover_resolves_forward_reference_after_earlier_broken_entry(
        self, client, broken_ref_rsl_uri, broken_ref_trlc_uri
    ):
        """Hover on a valid forward reference returns a result even when an
        earlier entry in the same derived_from list is unresolvable."""
        # Given: a schema and an instance file where CompB's derived_from list
        # starts with an unresolvable name ('DoesNotExist') followed by a
        # valid forward reference ('FeatA') declared later in the same file
        open_document(client, broken_ref_rsl_uri, BROKEN_REF_RSL_TEXT)
        open_document(client, broken_ref_trlc_uri, BROKEN_REF_TRLC_TEXT)
        diags = await wait_for_diagnostics(client, broken_ref_trlc_uri)

        # Confirm the broken-reference scenario is in effect: CompB's missing
        # 'description' field causes TRLC to report an error and skip the
        # deferred resolve_record_references pass for the whole file.
        assert any(
            d.severity == types.DiagnosticSeverity.Error for d in diags
        ), (
            f"Expected at least one error diagnostic to confirm broken state, got: {diags}"
        )

        # When: hover is requested on 'FeatA' inside CompB's derived_from list
        # (line=3 col=34 in BROKEN_REF_TRLC_TEXT — the forward reference)
        result = await hover(client, broken_ref_trlc_uri, line=3, col=34)

        # Then: a hover result is returned (fallback resolved FeatA by name)
        assert result is not None, (
            "Expected hover to return FeatA's description via by-name fallback, "
            "got None — the broken-entry-earlier-in-list regression may have returned."
        )

    async def test_goto_definition_resolves_to_instance(
        self, client, broken_ref_rsl_uri, broken_ref_trlc_uri
    ):
        """textDocument/definition (F12) on a valid forward reference navigates
        to the instance's own declaration in the .trlc file, not to the .rsl
        type — even when an earlier entry in the same derived_from list broke
        TRLC's deferred resolution pass."""
        # Given: the broken-reference fixture (same as above)
        open_document(client, broken_ref_rsl_uri, BROKEN_REF_RSL_TEXT)
        open_document(client, broken_ref_trlc_uri, BROKEN_REF_TRLC_TEXT)
        diags = await wait_for_diagnostics(client, broken_ref_trlc_uri)
        assert any(
            d.severity == types.DiagnosticSeverity.Error for d in diags
        ), (
            f"Expected at least one error diagnostic to confirm broken state, got: {diags}"
        )

        # When: go-to-definition (F12) is requested on 'FeatA' at line=3 col=34
        loc = await goto_definition(
            client, broken_ref_trlc_uri, line=3, col=34
        )

        # Then: navigation lands on FeatA's own declaration in the .trlc file
        assert loc is not None, (
            "Expected goto_definition to navigate to FeatA's .trlc declaration, "
            "got None — broken-reference fallback may not have resolved it."
        )
        assert "broken_ref.trlc" in loc.uri, (
            f"Expected location in broken_ref.trlc (the instance declaration), "
            f"got: {loc.uri}"
        )
        # FeatA is declared at line=6 in BROKEN_REF_TRLC_TEXT
        assert loc.range.start.line == 6, (
            f"Expected FeatA declaration at line=6, got line={loc.range.start.line}"
        )

    async def test_goto_type_definition_resolves_to_rsl_type(
        self, client, broken_ref_rsl_uri, broken_ref_trlc_uri
    ):
        """textDocument/typeDefinition on a valid forward reference navigates
        to the Record_Type in the .rsl file — not to the instance declaration
        — even when an earlier entry in the same derived_from list is broken."""
        # Given: the broken-reference fixture
        open_document(client, broken_ref_rsl_uri, BROKEN_REF_RSL_TEXT)
        open_document(client, broken_ref_trlc_uri, BROKEN_REF_TRLC_TEXT)
        diags = await wait_for_diagnostics(client, broken_ref_trlc_uri)
        assert any(
            d.severity == types.DiagnosticSeverity.Error for d in diags
        ), (
            f"Expected at least one error diagnostic to confirm broken state, got: {diags}"
        )

        # When: go-to-type-definition is requested on 'FeatA' at line=3 col=34
        loc = await goto_type_definition(
            client, broken_ref_trlc_uri, line=3, col=34
        )

        # Then: navigation lands on the 'Req' type in the .rsl file
        # (FeatA is a Req instance; typeDefinition resolves to Req's schema)
        assert loc is not None, (
            "Expected goto_type_definition to navigate to the Req type in .rsl, "
            "got None — broken-reference fallback may not have resolved it."
        )
        assert "broken_ref.rsl" in loc.uri, (
            f"Expected location in broken_ref.rsl (the Req type declaration), "
            f"got: {loc.uri}"
        )

    async def test_find_references_includes_forward_reference_after_earlier_broken_entry(
        self, client, broken_ref_rsl_uri, broken_ref_trlc_uri
    ):
        """find-references on FeatA's declaration includes the usage inside
        CompB's derived_from list, even though the earlier DoesNotExist entry
        prevented TRLC's resolution pass from filling in FeatA's .target."""
        # Given: the broken-reference fixture
        open_document(client, broken_ref_rsl_uri, BROKEN_REF_RSL_TEXT)
        open_document(client, broken_ref_trlc_uri, BROKEN_REF_TRLC_TEXT)
        diags = await wait_for_diagnostics(client, broken_ref_trlc_uri)
        assert any(
            d.severity == types.DiagnosticSeverity.Error for d in diags
        ), (
            f"Expected at least one error diagnostic to confirm broken state, got: {diags}"
        )

        # When: find-references is requested on FeatA's declaration
        # (line=6 col=4 in BROKEN_REF_TRLC_TEXT)
        locs = await references(client, broken_ref_trlc_uri, line=6, col=4)

        # Then: the result includes at least the usage inside CompB's derived_from
        assert locs is not None, (
            "Expected at least the declaration itself in references, got None"
        )
        assert len(locs) >= 1, (
            f"Expected at least 1 reference for FeatA, got: {locs}"
        )
        trlc_uris = [loc.uri for loc in locs if "broken_ref.trlc" in loc.uri]
        assert trlc_uris, (
            f"Expected at least one reference in broken_ref.trlc, "
            f"got uris: {[loc.uri for loc in locs]}"
        )
        # The usage inside CompB's derived_from list is at line=3
        ref_lines = [
            loc.range.start.line
            for loc in locs
            if "broken_ref.trlc" in loc.uri
        ]
        assert 3 in ref_lines, (
            f"Expected a reference at line=3 (FeatA inside CompB's derived_from), "
            f"got lines: {ref_lines}"
        )
