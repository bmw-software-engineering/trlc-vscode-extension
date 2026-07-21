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
"""Unit tests for trlc_lsp.parse_engine._diagnostics_changed."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

from lsprotocol.types import Diagnostic, DiagnosticSeverity, Position, Range

from server.parse_engine import _diagnostics_changed


def _diag(message="msg", line=0, col=0, severity=DiagnosticSeverity.Error):
    """Build a minimal Diagnostic for testing."""
    pos = Position(line=line, character=col)
    rng = Range(start=pos, end=Position(line=line, character=col + 1))
    return Diagnostic(range=rng, message=message, severity=severity)


class TestDiagnosticsChanged:
    def test_identical_lists_return_false(self):
        # Given/When: comparing a diagnostic list against itself
        d = _diag()
        # Then: no change is detected
        assert _diagnostics_changed([d], [d]) is False

    def test_empty_to_empty_returns_false(self):
        # Given/When: comparing two empty diagnostic lists
        # Then: no change is detected
        assert _diagnostics_changed([], []) is False

    def test_empty_to_one_returns_true(self):
        # Given/When: comparing an empty list to a list with one diagnostic
        # Then: a change is detected
        assert _diagnostics_changed([], [_diag()]) is True

    def test_one_to_empty_returns_true(self):
        # Given/When: comparing a list with one diagnostic to an empty list
        # Then: a change is detected
        assert _diagnostics_changed([_diag()], []) is True

    def test_different_message_returns_true(self):
        # Given/When: comparing diagnostics that differ only in message
        # Then: a change is detected
        assert _diagnostics_changed([_diag("a")], [_diag("b")]) is True

    def test_different_severity_returns_true(self):
        # Given: two diagnostics differing only in severity
        a = _diag(severity=DiagnosticSeverity.Error)
        b = _diag(severity=DiagnosticSeverity.Warning)
        # When/Then: comparing them detects a change
        assert _diagnostics_changed([a], [b]) is True

    def test_different_position_returns_true(self):
        # Given: two diagnostics differing only in line position
        a = _diag(line=0)
        b = _diag(line=5)
        # When/Then: comparing them detects a change
        assert _diagnostics_changed([a], [b]) is True

    def test_same_set_different_order_returns_false(self):
        """Regression: order-independent comparison must not flag reordering."""
        # Given: the same two diagnostics in different list order
        a = _diag("first", line=0)
        b = _diag("second", line=1)
        # When/Then: no change is detected (comparison is order-independent)
        assert _diagnostics_changed([a, b], [b, a]) is False

    def test_duplicate_diagnostics_treated_as_set(self):
        """Two identical diagnostics in one list collapse to one set entry."""
        # Given: a list with a duplicated diagnostic vs. a list with one copy
        d = _diag()
        # When/Then: no change is detected (set-based comparison)
        # [d, d] vs [d] — same set content
        assert _diagnostics_changed([d, d], [d]) is False

    def test_multiple_diags_all_same_returns_false(self):
        # Given: the same set of diagnostics in reversed order
        diags = [_diag(f"msg{i}", line=i) for i in range(5)]
        # When/Then: no change is detected
        assert _diagnostics_changed(diags, list(reversed(diags))) is False

    def test_one_changed_in_many_returns_true(self):
        # Given: a list of diagnostics with exactly one entry modified
        diags = [_diag(f"msg{i}", line=i) for i in range(5)]
        modified = list(diags)
        modified[2] = _diag("different", line=2)
        # When/Then: a change is detected
        assert _diagnostics_changed(diags, modified) is True
