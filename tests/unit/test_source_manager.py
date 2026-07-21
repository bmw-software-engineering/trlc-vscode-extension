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
"""Unit tests for :mod:`server.source_manager`.

Previously only exercised indirectly via ``test_parse_engine_integration.py``
(through ``ParseEngine._build_vsm``). These tests isolate
``Vscode_Source_Manager``'s own responsibilities: directory-walk file
discovery (``register_include``/``register_workspace``) with exclude-pattern
filtering, the open-file-vs-disk content fallback, the invalid-pattern
warning path, and ``compute_input_signature`` (added for the parse engine's
unchanged-input reuse optimization).
"""

# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access,too-few-public-methods

import logging
from unittest.mock import MagicMock

import trlc.errors

from server.file_handler import File_Handler
from server.parse_guard import normalize_fs_path
from server.source_manager import Vscode_Source_Manager
from server.token_utils import uri_from_file


def _make_sm(fh=None, exclude_patterns=None):
    """Build a Vscode_Source_Manager with a real Message_Handler and
    File_Handler, and a minimal ls stub (only work_done_progress is read)."""
    ls = MagicMock()
    return Vscode_Source_Manager(
        mh=trlc.errors.Message_Handler(),
        fh=fh or File_Handler(),
        ls=ls,
        verify_mode=False,
        exclude_patterns=exclude_patterns,
    )


class TestRegisterInclude:
    def test_only_rsl_files_are_auto_included(self, tmp_path):
        """.rsl files are eligible for transitive inclusion; .trlc files
        never are (only explicitly-opened .trlc files participate)."""
        # Given/When/Then: .rsl files are eligible for transitive inclusion; .trlc files never are
        # (only explicitly-opened .trlc files participate)
        (tmp_path / "schema.rsl").write_text("package A\n")
        (tmp_path / "data.trlc").write_text("package A\n")
        (tmp_path / "other.txt").write_text("not trlc")

        sm = _make_sm()
        sm.register_include(str(tmp_path))

        included = {str(p) for p in sm.includes}
        assert normalize_fs_path(str(tmp_path / "schema.rsl")) in included
        assert not any(p.endswith("data.trlc") for p in included)
        assert not any(p.endswith("other.txt") for p in included)

    def test_excluded_directory_names_are_skipped(self, tmp_path):
        """A directory matching an exclude pattern (default: bazel-*) is
        never walked into."""
        # Given/When/Then: A directory matching an exclude pattern (default: bazel-*) is never
        # walked into
        excluded_dir = tmp_path / "bazel-out"
        excluded_dir.mkdir()
        (excluded_dir / "hidden.rsl").write_text("package Hidden\n")
        (tmp_path / "visible.rsl").write_text("package Visible\n")

        sm = _make_sm()
        sm.register_include(str(tmp_path))

        included = {str(p) for p in sm.includes}
        assert any(p.endswith("visible.rsl") for p in included)
        assert not any(p.endswith("hidden.rsl") for p in included)

    def test_custom_exclude_pattern_is_applied(self, tmp_path):
        """A user-configured exclude pattern (trlcServer.excludePatterns)
        is applied in addition to the built-in bazel-* default."""
        # Given/When/Then: A user-configured exclude pattern (trlcServer.excludePatterns) is
        # applied in addition to the built-in bazel-* default
        excluded_dir = tmp_path / "generated"
        excluded_dir.mkdir()
        (excluded_dir / "gen.rsl").write_text("package Gen\n")
        (tmp_path / "real.rsl").write_text("package Real\n")

        sm = _make_sm(exclude_patterns=["^generated$"])
        sm.register_include(str(tmp_path))

        included = {str(p) for p in sm.includes}
        assert any(p.endswith("real.rsl") for p in included)
        assert not any(p.endswith("gen.rsl") for p in included)


class TestInvalidExcludePattern:
    def test_bad_regex_is_logged_and_does_not_raise(
        self,
        tmp_path,  # pylint: disable=unused-argument  # isolated cwd side effect; must keep pytest's exact fixture name
        caplog,
    ):
        """An invalid regex in trlcServer.excludePatterns is a user config
        mistake, not a crash — it's skipped and logged."""
        # Given/When/Then: An invalid regex in trlcServer.excludePatterns is a user config mistake,
        # not a crash — it's skipped and logged
        with caplog.at_level(logging.WARNING, logger="server.source_manager"):
            sm = _make_sm(exclude_patterns=["("])  # unbalanced group

        assert any(
            "Invalid exclude pattern" in r.message for r in caplog.records
        )
        # The valid default pattern is still present (not clobbered).
        assert any(p.pattern == r"^bazel-.*$" for p in sm.exclude_patterns)


class TestRegisterWorkspace:
    def test_registers_rsl_and_trlc_files_from_disk(self, tmp_path):
        """.rsl/.trlc files register; unrelated files are skipped."""
        # Given/When/Then: .rsl/.trlc files register; unrelated files are skipped
        (tmp_path / "a.rsl").write_text("package A\n")
        (tmp_path / "b.trlc").write_text("package A\n")
        (tmp_path / "c.md").write_text("not trlc")

        sm = _make_sm()
        ok = sm.register_workspace(str(tmp_path))

        assert ok
        registered = {str(p) for p in sm.all_files}
        assert any(p.endswith("a.rsl") for p in registered)
        assert any(p.endswith("b.trlc") for p in registered)
        assert not any(p.endswith("c.md") for p in registered)

    def test_open_file_content_is_read_from_editor_not_disk(self, tmp_path):
        """A file with unsaved editor changes must be parsed with the
        in-memory content, not the (stale) on-disk content."""
        # Given: a file on disk, open in the editor with different, unsaved content
        disk_content = "package A\n\ntype OnDisk {\n    x Integer\n}\n"
        editor_content = "package A\n\ntype InEditor {\n    x Integer\n}\n"
        rsl_path = tmp_path / "a.rsl"
        rsl_path.write_text(disk_content)

        fh = File_Handler()
        fh.update_files(uri_from_file(str(rsl_path)), editor_content)

        sm = _make_sm(fh=fh)

        # When: the workspace is registered
        sm.register_workspace(str(tmp_path))

        # Then: the registered content is the editor's, not disk's — proven
        # via the input signature, which fingerprints open-file content by
        # hash rather than falling back to (mtime, size).
        parser = sm.all_files[normalize_fs_path(str(rsl_path))]
        sig = sm.compute_input_signature()
        fingerprint = sig[normalize_fs_path(str(rsl_path))]
        assert fingerprint == ("mem", hash(editor_content))
        assert fingerprint != ("mem", hash(disk_content))
        assert parser is not None

    def test_excluded_directory_is_skipped_in_workspace_walk(self, tmp_path):
        """Files under an excluded directory never get registered."""
        # Given/When/Then: Files under an excluded directory never get registered
        excluded_dir = tmp_path / "bazel-bin"
        excluded_dir.mkdir()
        (excluded_dir / "gen.trlc").write_text("package Gen\n")
        (tmp_path / "real.trlc").write_text("package Real\n")

        sm = _make_sm()
        sm.register_workspace(str(tmp_path))

        registered = {str(p) for p in sm.all_files}
        assert any(p.endswith("real.trlc") for p in registered)
        assert not any(p.endswith("gen.trlc") for p in registered)


class TestComputeInputSignature:
    def test_open_file_signature_depends_on_content_not_disk_state(
        self, tmp_path
    ):
        """Two managers registering the same open-file content produce the
        same signature, even if nothing was ever written to disk — content
        hashing is what makes the ParseEngine's unchanged-input skip safe
        for unsaved edits."""
        # Given: a file open in the editor, its saved-to-disk content different
        rsl_path = tmp_path / "a.rsl"
        rsl_path.write_text("package A\n")
        content = "package A\n\ntype T {\n    x Integer\n}\n"

        fh = File_Handler()
        fh.update_files(uri_from_file(str(rsl_path)), content)

        # When: two independent managers register the same workspace
        sm1 = _make_sm(fh=fh)
        sm1.register_workspace(str(tmp_path))
        sm2 = _make_sm(fh=fh)
        sm2.register_workspace(str(tmp_path))

        # Then: both compute the identical signature from the shared open content
        assert sm1.compute_input_signature() == sm2.compute_input_signature()

    def test_changed_open_file_content_changes_signature(self, tmp_path):
        """Different in-memory content produces a different signature."""
        # Given/When/Then: Different in-memory content produces a different signature
        rsl_path = tmp_path / "a.rsl"
        rsl_path.write_text("package A\n")

        fh1 = File_Handler()
        fh1.update_files(uri_from_file(str(rsl_path)), "package A\n")
        sm1 = _make_sm(fh=fh1)
        sm1.register_workspace(str(tmp_path))

        fh2 = File_Handler()
        fh2.update_files(uri_from_file(str(rsl_path)), "package A\n\n")
        sm2 = _make_sm(fh=fh2)
        sm2.register_workspace(str(tmp_path))

        assert sm1.compute_input_signature() != sm2.compute_input_signature()

    def test_disk_only_file_fingerprint_uses_mtime_and_size(self, tmp_path):
        """A file never opened in the editor fingerprints via (mtime, size)."""
        # Given/When/Then: A file never opened in the editor fingerprints via (mtime, size)
        rsl_path = tmp_path / "a.rsl"
        rsl_path.write_text("package A\n")

        sm = _make_sm()
        sm.register_workspace(str(tmp_path))

        sig = sm.compute_input_signature()
        fingerprint = sig[normalize_fs_path(str(rsl_path))]
        assert fingerprint[0] == "disk"
        assert fingerprint[2] == rsl_path.stat().st_size

    def test_missing_file_fingerprints_as_missing(self):
        """A path that doesn't exist on disk fingerprints as "missing"."""
        # Given/When/Then: A path that doesn't exist on disk fingerprints as "missing"
        fingerprint = Vscode_Source_Manager._file_fingerprint(
            "/does/not/exist.rsl", None
        )
        assert fingerprint == ("missing",)

    def test_include_files_are_covered_by_signature(self, tmp_path):
        """Transitive .rsl includes (parsed from disk during process(), not
        directly registered) still show up in the signature — otherwise an
        edit to an included-but-not-open schema file would go undetected."""
        # Given/When/Then: Transitive .rsl includes (parsed from disk during process(), not directly
        # registered) still show up in the signature — otherwise an edit to an included-
        # but-not-open schema file would go undetected
        included = tmp_path / "included.rsl"
        included.write_text("package Included\n")

        sm = _make_sm()
        sm.register_include(str(tmp_path))

        sig = sm.compute_input_signature()
        assert normalize_fs_path(str(included)) in sig
