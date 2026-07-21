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
"""Unit tests for trlc_lsp.file_handler.File_Handler."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

from server.file_handler import File_Handler


class TestFileHandler:
    def test_update_stores_content(self):
        # Given: a fresh File_Handler
        fh = File_Handler()
        # When: a file's content is set
        fh.update_files("file:///a.trlc", "content a")
        # Then: the content is stored under its uri
        assert fh.files["file:///a.trlc"] == "content a"

    def test_update_overwrites_existing(self):
        # Given: a file already tracked with initial content
        fh = File_Handler()
        fh.update_files("file:///a.trlc", "v1")
        # When: the same uri is updated again
        fh.update_files("file:///a.trlc", "v2")
        # Then: the stored content reflects the latest update
        assert fh.files["file:///a.trlc"] == "v2"

    def test_delete_removes_entry(self):
        # Given: a tracked file
        fh = File_Handler()
        fh.update_files("file:///a.trlc", "content")
        # When: that file is deleted
        fh.delete_files("file:///a.trlc")
        # Then: it is no longer tracked
        assert "file:///a.trlc" not in fh.files

    def test_delete_noop_for_unknown_uri(self):
        # Given: a fresh File_Handler
        fh = File_Handler()
        # When/Then: deleting a uri that was never tracked must not raise
        fh.delete_files("file:///does_not_exist.trlc")  # must not raise

    def test_multiple_files_are_independent(self):
        # Given: two tracked files
        fh = File_Handler()
        fh.update_files("file:///a.trlc", "aaa")
        fh.update_files("file:///b.trlc", "bbb")
        # When: one of them is deleted
        fh.delete_files("file:///a.trlc")
        # Then: only the deleted file is affected
        assert "file:///a.trlc" not in fh.files
        assert fh.files["file:///b.trlc"] == "bbb"

    def test_empty_string_content_is_valid(self):
        # Given: a fresh File_Handler
        fh = File_Handler()
        # When: a file is tracked with empty-string content
        fh.update_files("file:///empty.trlc", "")
        # Then: the empty content is stored as-is, not treated as absent
        assert fh.files["file:///empty.trlc"] == ""

    def test_snapshot_reflects_current_files(self):
        # Given: two tracked files
        fh = File_Handler()
        fh.update_files("file:///a.trlc", "aaa")
        fh.update_files("file:///b.trlc", "bbb")
        # When: a snapshot is taken
        snap = fh.snapshot()
        # Then: it contains exactly the currently tracked files
        assert snap == {"file:///a.trlc": "aaa", "file:///b.trlc": "bbb"}

    def test_snapshot_of_empty_handler_is_an_empty_dict(self):
        # Given: a fresh File_Handler
        fh = File_Handler()
        # When/Then: its snapshot is an empty dict
        assert not fh.snapshot()

    def test_snapshot_is_a_copy_not_a_live_view(self):
        """Callers iterate the snapshot without holding the lock (see
        snapshot()'s docstring) — mutating fh afterwards must not be
        visible through a previously-taken snapshot, or a caller iterating
        it could observe a torn/changing dict from another thread."""
        # Given: a tracked file and a snapshot taken of it
        fh = File_Handler()
        fh.update_files("file:///a.trlc", "v1")
        snap = fh.snapshot()
        # When: the underlying handler is mutated afterwards
        fh.update_files("file:///a.trlc", "v2")
        fh.update_files("file:///b.trlc", "new")
        # Then: the earlier snapshot is unaffected
        assert snap == {"file:///a.trlc": "v1"}

    def test_mutating_the_returned_snapshot_does_not_affect_the_handler(self):
        # Given: a tracked file and a snapshot taken of it
        fh = File_Handler()
        fh.update_files("file:///a.trlc", "v1")
        snap = fh.snapshot()
        # When: the returned snapshot dict is mutated directly
        snap["file:///a.trlc"] = "tampered"
        snap["file:///new.trlc"] = "injected"
        # Then: the handler's own internal state is untouched
        assert fh.files == {"file:///a.trlc": "v1"}
