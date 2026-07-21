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
"""Integration tests for textDocument/codeAction.

Regression coverage for a production bug where the registered handler's
signature didn't match what pygls actually passes at dispatch time
(`code_action() missing 1 required positional argument: 'params'`) -
tests/unit/handlers/test_code_actions.py calls the handler function
directly and so never exercises pygls's real request-dispatch/binding
path; these tests drive it through an actual `textDocument/codeAction`
request over a live server subprocess instead.
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

from lsprotocol import types

# CHECK_TYPO_RSL_TEXT is self-contained rather than reusing the shared
# types.rsl/RSL_TEXT fixture: a record-type-name typo does *not* trigger
# trlc's "did you mean" diagnostic — only a misspelled builtin in a checks
# block does. Kept in content.py alongside the other shared content strings.
from .content import (
    CHECK_TYPO_RSL_TEXT,
    RSL_TEXT,
)
from .content import (
    INVALID_TRLC_TEXT as UNRELATED_ERROR_TEXT,
)
from .content import (
    TRLC_TEXT as VALID_TEXT,
)
from .lsp_helpers import code_action, open_document, wait_for_diagnostics


class TestCodeActions:
    async def test_did_you_mean_diagnostic_produces_quick_fix(
        self, client, types_uri
    ):
        """A "did you mean" diagnostic produces a matching quick-fix action."""
        # Given: a schema file with a checks block calling a misspelled
        # builtin function ("startsXwith" instead of "startswith")
        open_document(client, types_uri, CHECK_TYPO_RSL_TEXT)
        diags = await wait_for_diagnostics(client, types_uri)
        typo_diag = next(d for d in diags if "did you mean" in d.message)

        # When: code actions are requested for that diagnostic
        actions = await code_action(
            client, types_uri, typo_diag.range, [typo_diag]
        )

        # Then: exactly one quick-fix action suggesting the real name
        assert len(actions) == 1, (
            f"Expected exactly one code action, got: {actions}"
        )
        assert actions[0].title == "Change to 'startswith'"
        assert (
            actions[0].edit.document_changes[0].edits[0].new_text
            == "startswith"
        )

    async def test_unrelated_diagnostic_produces_no_action(
        self, client, types_uri, sample_uri
    ):
        """A diagnostic that isn't a "did you mean" produces no action."""
        # Given: a schema file open, and an instance file missing a
        # required field (a real diagnostic, but not a "did you mean" one)
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, UNRELATED_ERROR_TEXT)
        diags = await wait_for_diagnostics(client, sample_uri)
        assert diags, "Expected at least one diagnostic to request actions for"

        # When: code actions are requested for that diagnostic
        actions = await code_action(client, sample_uri, diags[0].range, diags)

        # Then: no code actions are produced
        assert actions == []

    async def test_no_diagnostics_in_context_produces_no_action(
        self, client, types_uri, sample_uri
    ):
        """An empty diagnostics context produces no action, not an error."""
        # Given: a fully valid instance file (no diagnostics at all)
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, VALID_TEXT)
        diags = await wait_for_diagnostics(client, sample_uri)
        assert diags == []

        # When: code actions are requested with an empty diagnostics context
        actions = await code_action(
            client,
            sample_uri,
            types.Range(
                start=types.Position(line=0, character=0),
                end=types.Position(line=0, character=0),
            ),
            [],
        )

        # Then: no code actions are produced
        assert actions == []
