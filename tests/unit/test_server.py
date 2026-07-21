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
"""Unit tests for trlc_lsp.server._read_version — the VERSION-file loader
used to build the ``trlc_server`` singleton's reported version."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

from server.server import _read_version


class TestReadVersion:
    def test_returns_stripped_file_contents(self, tmp_path):
        # Given: a VERSION file with trailing whitespace/newline
        version_file = tmp_path / "VERSION"
        version_file.write_text("1.2.3\n")
        # When/Then: the returned version is the stripped content
        assert _read_version(str(version_file)) == "1.2.3"

    def test_returns_unknown_when_file_is_missing(self, tmp_path):
        # Given: a path with no VERSION file (e.g. a packaging layout that
        # doesn't ship one)
        missing = tmp_path / "does_not_exist"
        # When/Then: it falls back to "unknown" instead of raising
        assert _read_version(str(missing)) == "unknown"

    def test_returns_unknown_when_path_is_a_directory(self, tmp_path):
        # Given: a path that exists but is a directory, not a file — open()
        # raises IsADirectoryError, an OSError subclass
        # When/Then: it falls back to "unknown" instead of raising
        assert _read_version(str(tmp_path)) == "unknown"
