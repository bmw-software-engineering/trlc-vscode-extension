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
"""Unit tests for trlc_lsp.message_handler.Vscode_Message_Handler."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

from unittest.mock import MagicMock, patch

import pytest
from lsprotocol.types import DiagnosticSeverity
from trlc.errors import Kind

from server.message_handler import (
    Vscode_Message_Handler,
    kind_to_severity_mapping,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_location(file_name="/tmp/test.rsl", line=1, col=1):
    """Build a minimal TRLC Location mock.

    line and col are 1-based (matching TRLC's convention).
    """
    loc = MagicMock()
    loc.file_name = file_name
    loc.line_no = line
    loc.col_no = col
    end = MagicMock()
    end.line_no = line
    end.col_no = col + 1
    loc.get_end_location.return_value = end
    return loc


# ---------------------------------------------------------------------------
# kind_to_severity_mapping
# ---------------------------------------------------------------------------


class TestKindToSeverityMapping:
    def test_user_error_maps_to_error(self):
        # Given/When/Then: USER_ERROR maps to Error severity
        assert (
            kind_to_severity_mapping[Kind.USER_ERROR]
            == DiagnosticSeverity.Error
        )

    def test_sys_error_maps_to_error(self):
        # Given/When/Then: SYS_ERROR maps to Error severity
        assert (
            kind_to_severity_mapping[Kind.SYS_ERROR]
            == DiagnosticSeverity.Error
        )

    def test_user_warning_maps_to_warning(self):
        # Given/When/Then: USER_WARNING maps to Warning severity
        assert (
            kind_to_severity_mapping[Kind.USER_WARNING]
            == DiagnosticSeverity.Warning
        )

    def test_sys_warning_maps_to_warning(self):
        # Given/When/Then: SYS_WARNING maps to Warning severity
        assert (
            kind_to_severity_mapping[Kind.SYS_WARNING]
            == DiagnosticSeverity.Warning
        )

    def test_sys_check_maps_to_information(self):
        # Given/When/Then: SYS_CHECK maps to Information severity
        assert (
            kind_to_severity_mapping[Kind.SYS_CHECK]
            == DiagnosticSeverity.Information
        )


# ---------------------------------------------------------------------------
# Vscode_Message_Handler
# ---------------------------------------------------------------------------


class TestVscodeMessageHandler:
    def test_fatal_error_raises(self):
        # Given: a fresh handler and a fatal USER_ERROR to emit
        # TRLC_Error requires a real Location object (asserts isinstance).
        # Patch the constructor so the mock location is accepted.
        mh = Vscode_Message_Handler()
        loc = _make_location()
        # When/Then: emitting it with fatal=True raises
        with patch(
            "server.message_handler.TRLC_Error",
            side_effect=RuntimeError("fatal"),
        ):
            with pytest.raises(RuntimeError, match="fatal"):
                mh.emit(loc, Kind.USER_ERROR, "bad thing", fatal=True)

    def test_non_fatal_does_not_raise(self):
        # Given: a fresh handler and a non-fatal warning to emit
        mh = Vscode_Message_Handler()
        loc = _make_location()
        # When/Then: emitting it with fatal=False does not raise
        mh.emit(loc, Kind.USER_WARNING, "minor issue", fatal=False)  # no raise

    def test_diagnostic_accumulated_by_uri(self):
        # Given: a fresh handler
        mh = Vscode_Message_Handler()
        loc = _make_location("/tmp/a.rsl", line=3)
        # When: a diagnostic is emitted for that location
        mh.emit(loc, Kind.USER_WARNING, "warn", fatal=False)
        # Then: it is accumulated under a file:// uri key
        assert len(mh.diagnostics) == 1
        uri = next(iter(mh.diagnostics))
        assert uri.startswith("file://")
        assert len(mh.diagnostics[uri]) == 1

    def test_multiple_errors_same_file_grouped(self):
        # Given: a fresh handler and a single file location
        mh = Vscode_Message_Handler()
        loc = _make_location("/tmp/a.rsl")
        # When: two diagnostics are emitted for the same file
        mh.emit(loc, Kind.USER_WARNING, "w1", fatal=False)
        mh.emit(loc, Kind.USER_WARNING, "w2", fatal=False)
        # Then: both are grouped under that file's single uri entry
        assert sum(len(v) for v in mh.diagnostics.values()) == 2
        uri = next(iter(mh.diagnostics))
        assert len(mh.diagnostics[uri]) == 2

    def test_errors_from_different_files_are_separate(self):
        # Given: a fresh handler and two different file locations
        mh = Vscode_Message_Handler()
        loc1 = _make_location("/tmp/a.rsl")
        loc2 = _make_location("/tmp/b.rsl")
        # When: a diagnostic is emitted for each file
        mh.emit(loc1, Kind.USER_WARNING, "w1", fatal=False)
        mh.emit(loc2, Kind.USER_WARNING, "w2", fatal=False)
        # Then: each file gets its own separate diagnostics entry
        assert len(mh.diagnostics) == 2

    def test_extrainfo_appended_to_message(self):
        # Given: a fresh handler and extra detail text
        mh = Vscode_Message_Handler()
        loc = _make_location()
        # When: a diagnostic is emitted with extrainfo
        mh.emit(
            loc,
            Kind.USER_WARNING,
            "main msg",
            fatal=False,
            extrainfo="extra detail",
        )
        # Then: both the main message and extra detail appear in the diagnostic
        diag = next(iter(mh.diagnostics.values()))[0]
        assert "main msg" in diag.message
        assert "extra detail" in diag.message

    def test_category_stored_as_code(self):
        # Given: a fresh handler and a category code
        mh = Vscode_Message_Handler()
        loc = _make_location()
        # When: a diagnostic is emitted with that category
        mh.emit(loc, Kind.USER_ERROR, "err", fatal=False, category="E001")
        # Then: the category is stored as the diagnostic's code
        diag = next(iter(mh.diagnostics.values()))[0]
        assert diag.code == "E001"

    def test_severity_matches_kind(self):
        # Given: a fresh handler
        mh = Vscode_Message_Handler()
        loc = _make_location()
        # When: a USER_ERROR diagnostic is emitted
        mh.emit(loc, Kind.USER_ERROR, "err", fatal=False)
        # Then: its severity is Error
        diag = next(iter(mh.diagnostics.values()))[0]
        assert diag.severity == DiagnosticSeverity.Error
