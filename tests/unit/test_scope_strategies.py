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
"""Unit tests for context discovery strategies.

Exercises the discovery logic against real temp-directory file trees and a
real File_Handler wherever possible. Only the Bazel CLI boundary
(BazelClient / BazelTargetManager, which shell out to the ``bazel`` binary)
stays mocked. BAZEL mode's sole remaining directory-mode fallback (no
Bazel workspace found at all) runs the real DirectoryScopeStrategy against
real files, so those tests prove the fallback actually discovers files, not
just that a mock was called; every other "Bazel found nothing usable"
condition now raises BazelUnavailable instead (see
TestBazelUnavailablePopupNoFallback).
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

import os
from unittest.mock import MagicMock, patch

import pytest
from lsprotocol.types import MessageType

from server.bazel import (
    BazelManagerCache,
    BazelUnavailable,
    BazelWorkspaceNotFoundError,
)
from server.file_handler import File_Handler
from server.scope_strategies import (
    BazelScopeStrategy,
    DirectoryScopeStrategy,
    RepoScopeStrategy,
    WorkspaceScopeStrategy,
    get_scope_strategy,
)
from server.server_config import ParseMode, ServerConfig
from server.token_utils import encode_scope_id, path_from_uri, uri_from_file


class RecordingVSM:
    """Records what a strategy registered, without any real TRLC parsing —
    the vsm/parser is an external dependency of the strategies under test,
    not what these tests are exercising.

    ``register_file`` requires a real filesystem path (it asserts
    ``os.path.isfile``), never a ``file://`` URI — enforced here so these
    tests would fail loudly if a strategy regressed to passing a URI."""

    def __init__(self):
        self.registered_includes = []
        self.registered_files = {}
        self.registered_workspaces = []

    def register_include(self, path):
        """Register include."""
        self.registered_includes.append(path)

    def register_file(self, file_path, content):
        """Register file."""
        assert not file_path.startswith("file://"), (
            f"register_file() must receive a real path, got a URI: {file_path!r}"
        )
        self.registered_files[file_path] = content

    def register_workspace(self, path):
        """Register workspace."""
        self.registered_workspaces.append(path)


def _fake_ls(open_files=None):
    """MagicMock server double with a real File_Handler (keyed by uri),
    for strategies that read ``ls.fh`` — passing a bare MagicMock() there
    breaks the moment a strategy actually calls ``ls.fh.snapshot()``."""
    fake_ls = MagicMock()
    fh = File_Handler()
    for uri, content in (open_files or {}).items():
        fh.update_files(uri, content)
    fake_ls.fh = fh
    return fake_ls


class TestDirectoryScopeStrategy:
    """Real tmp-directory file discovery — no mocking of the filesystem."""

    def test_registers_only_rsl_and_trlc_files_non_recursively(self, tmp_path):
        # Given: a directory with .rsl/.trlc/.txt files, and a nested subdirectory
        (tmp_path / "a.rsl").write_text("package A\n")
        (tmp_path / "b.trlc").write_text("package A\n")
        (tmp_path / "c.txt").write_text("not trlc")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "d.rsl").write_text("package D\n")  # must NOT be picked up

        # When: DirectoryScopeStrategy discovers files in that directory
        vsm = RecordingVSM()
        DirectoryScopeStrategy().discover(
            vsm, str(tmp_path), ServerConfig(), _fake_ls()
        )

        # Then: only the top-level .rsl/.trlc files are registered
        assert vsm.registered_includes == [str(tmp_path)]
        registered_names = {
            os.path.basename(path) for path in vsm.registered_files
        }
        assert registered_names == {"a.rsl", "b.trlc"}
        assert vsm.registered_files[str(tmp_path / "a.rsl")] == "package A\n"

    def test_missing_directory_is_a_noop(self, tmp_path):
        # Given: a directory path that does not exist
        missing = tmp_path / "does-not-exist"
        vsm = RecordingVSM()
        # When: DirectoryScopeStrategy discovers files there
        DirectoryScopeStrategy().discover(
            vsm, str(missing), ServerConfig(), _fake_ls()
        )
        # Then: no files are registered (but no error is raised)
        assert vsm.registered_includes == [str(missing)]
        assert not vsm.registered_files

    def test_open_file_uses_buffer_content_not_disk(self, tmp_path):
        """An open file's unsaved buffer content wins over what's on disk —
        otherwise DIRECTORY mode would ignore unsaved edits until save."""
        # Given: a.rsl on disk, but open in the editor with different,
        # unsaved content; b.rsl is on disk only, never opened
        (tmp_path / "a.rsl").write_text("package OnDisk\n")
        (tmp_path / "b.rsl").write_text("package B\n")
        uri_a = uri_from_file(str(tmp_path / "a.rsl"))

        vsm = RecordingVSM()
        # When: DirectoryScopeStrategy discovers files in that directory
        DirectoryScopeStrategy().discover(
            vsm,
            str(tmp_path),
            ServerConfig(),
            _fake_ls({uri_a: "package Unsaved\n"}),
        )

        # Then: a.rsl gets the buffer content, b.rsl falls back to disk
        assert (
            vsm.registered_files[str(tmp_path / "a.rsl")]
            == "package Unsaved\n"
        )
        assert vsm.registered_files[str(tmp_path / "b.rsl")] == "package B\n"


class TestWorkspaceScopeStrategy:
    """Real File_Handler — proves open-editor content (not disk content) is
    what gets registered."""

    def test_registers_open_files_from_file_handler(self, tmp_path):
        # Given: two files open in the editor (File_Handler), with edited content
        fh = File_Handler()
        uri_a = uri_from_file(str(tmp_path / "a.rsl"))
        uri_b = uri_from_file(str(tmp_path / "b.trlc"))
        fh.update_files(uri_a, "package A\n")
        fh.update_files(uri_b, "package A\n\nA x {}\n")

        fake_ls = MagicMock()
        fake_ls.fh = fh
        vsm = RecordingVSM()

        # When: WorkspaceScopeStrategy discovers files
        WorkspaceScopeStrategy().discover(
            vsm, str(tmp_path), ServerConfig(), fake_ls
        )

        # Then: the open editor content is registered, not disk content
        assert vsm.registered_includes == [str(tmp_path)]
        assert vsm.registered_files == {
            path_from_uri(uri_a): "package A\n",
            path_from_uri(uri_b): "package A\n\nA x {}\n",
        }

    def test_no_open_files_registers_only_the_include(self, tmp_path):
        # Given: no files open in the editor
        fake_ls = MagicMock()
        fake_ls.fh = File_Handler()
        vsm = RecordingVSM()
        # When: WorkspaceScopeStrategy discovers files
        WorkspaceScopeStrategy().discover(
            vsm, str(tmp_path), ServerConfig(), fake_ls
        )
        # Then: only the transitive include is registered, no open files
        assert vsm.registered_includes == [str(tmp_path)]
        assert not vsm.registered_files

    def test_multi_root_workspace_does_not_leak_other_folders_files(
        self, tmp_path
    ):
        """File_Handler is one global open-files map shared by every
        workspace folder's scope. A file open in folder B must not be
        registered into folder A's scope, or a multi-root workspace would
        parse folder B's content into folder A's symbol table."""
        # Given: two sibling workspace folders, each with one open file
        folder_a = tmp_path / "folder_a"
        folder_b = tmp_path / "folder_b"
        folder_a.mkdir()
        folder_b.mkdir()

        fh = File_Handler()
        uri_a = uri_from_file(str(folder_a / "a.rsl"))
        uri_b = uri_from_file(str(folder_b / "b.rsl"))
        fh.update_files(uri_a, "package A\n")
        fh.update_files(uri_b, "package B\n")

        fake_ls = MagicMock()
        fake_ls.fh = fh
        vsm = RecordingVSM()

        # When: WorkspaceScopeStrategy discovers files for folder_a's scope
        WorkspaceScopeStrategy().discover(
            vsm, str(folder_a), ServerConfig(), fake_ls
        )

        # Then: only folder_a's open file is registered, not folder_b's
        assert vsm.registered_files == {
            path_from_uri(uri_a): "package A\n",
        }


# pylint: disable-next=too-few-public-methods  # single test for this strategy; keeps strategy tests grouped
class TestRepoScopeStrategy:
    def test_delegates_to_register_workspace(self, tmp_path):
        # Given: a folder path
        vsm = RecordingVSM()
        # When: RepoScopeStrategy discovers files
        RepoScopeStrategy().discover(
            vsm, str(tmp_path), ServerConfig(), MagicMock()
        )
        # Then: it delegates to a full recursive register_workspace() walk
        assert vsm.registered_workspaces == [str(tmp_path)]


class TestBazelUnavailablePopupNoFallback:
    """Genuine Bazel-infra failures (the workspace lookup itself raising,
    a query/build failure, no targets found, a file owned by no target, or
    a resolved target with no parse-relevant files) all show one popup and
    skip discovery — no directory-mode fallback. "No Bazel workspace
    found" is deliberately the *only* exception: a workspace can
    legitimately mix Bazel and non-Bazel folders, so that case alone gets
    a quiet directory-mode fallback instead (see
    test_no_workspace_found_falls_back_to_directory_mode_quietly below,
    and TestGetScopeIdWorkspaceFallback for the scope_id() side of the
    same fallback). Uses a REAL BazelManagerCache so the single-flight +
    popup-once dedup is actually exercised, not just mocked away."""

    def _config(self):
        return ServerConfig(
            parse_mode=ParseMode.BAZEL,
            bazel_executable="bazel",
            bazel_rule_classes=["_trlc_specification"],
            bazel_use_shared_server=False,
        )

    def test_no_workspace_found_falls_back_to_directory_mode_quietly(
        self, tmp_path
    ):
        """When find_bazel_workspace() returns None, this folder simply
        isn't a Bazel workspace — expected in a mixed repo that has both
        Bazel and non-Bazel folders — so discover() degrades to real
        directory-mode discovery instead of raising, and shows no popup
        (unlike a genuinely broken Bazel setup)."""
        # Given: a real file on disk, and no Bazel workspace found
        (tmp_path / "a.rsl").write_text("package A\n")
        config = self._config()
        vsm = RecordingVSM()
        fake_ls = _fake_ls()
        fake_ls.bazel_cache = BazelManagerCache()
        fake_ls.window_show_message = MagicMock()

        # When: BazelScopeStrategy discovers files, twice (as happens once
        # per parse cycle)
        with patch("server.bazel.BazelClient") as mock_bazel:
            mock_bazel.find_bazel_workspace.return_value = None

            strategy = BazelScopeStrategy()
            reason_first = strategy.discover(
                vsm, str(tmp_path), config, fake_ls
            )
            reason_second = strategy.discover(
                vsm, str(tmp_path), config, fake_ls
            )

        # Then: real directory-mode discovery ran (both times), finding
        # the file on disk
        assert (
            vsm.registered_files.get(str(tmp_path / "a.rsl")) == "package A\n"
        )
        assert reason_first == (
            "Not a Bazel workspace; falling back to directory-mode parsing"
        )
        assert reason_second == reason_first

        # And: no popup was ever shown — this isn't a broken-Bazel error
        fake_ls.window_show_message.assert_not_called()

    def test_workspace_lookup_exception_raises_and_shows_one_error_popup(
        self, tmp_path
    ):
        """When find_bazel_workspace() itself raises (not just returns
        None), that's still a BazelUnavailable — popup, no fallback."""
        # Given: a real file on disk, and a workspace lookup that raises
        (tmp_path / "a.rsl").write_text("package A\n")
        config = self._config()
        vsm = RecordingVSM()
        fake_ls = _fake_ls()
        fake_ls.bazel_cache = BazelManagerCache()
        fake_ls.window_show_message = MagicMock()

        with patch("server.bazel.BazelClient") as mock_bazel:
            mock_bazel.find_bazel_workspace.side_effect = RuntimeError(
                "Bazel not installed"
            )

            strategy = BazelScopeStrategy()
            with pytest.raises(BazelUnavailable):
                strategy.discover(vsm, str(tmp_path), config, fake_ls)

        assert not vsm.registered_files
        fake_ls.window_show_message.assert_called_once()
        msg_params = fake_ls.window_show_message.call_args[0][0]
        assert msg_params.type == MessageType.Error
        assert "Bazel workspace lookup failed" in msg_params.message
        assert "directory-mode" not in msg_params.message

    def test_bazel_fallback_no_targets_found(self, tmp_path):
        """When no targets found in Bazel query, show one popup and raise
        BazelUnavailable instead of falling back to directory mode — a
        workspace-wide query finding zero TRLC targets means the Bazel/BUILD
        setup itself needs fixing, not "quietly parse everything found on
        disk instead"."""
        # Given: a real file on disk, and a Bazel workspace with no TRLC targets
        (tmp_path / "b.trlc").write_text("package A\n")
        config = self._config()
        vsm = RecordingVSM()
        fake_ls = _fake_ls()
        fake_ls.window_show_message = MagicMock()

        # When: BazelScopeStrategy discovers files (cached manager reports
        # no targets)
        with patch("server.bazel.BazelClient") as mock_bazel:
            mock_bazel.find_bazel_workspace.return_value = str(tmp_path)
            mock_bazel.return_value = MagicMock()

            mock_mgr = MagicMock()
            mock_mgr.build_targets.return_value = []
            fake_ls.bazel_cache.get_or_build.return_value = mock_mgr

            strategy = BazelScopeStrategy()
            with pytest.raises(BazelUnavailable) as exc_info:
                strategy.discover(vsm, str(tmp_path), config, fake_ls)

        # Then: nothing was registered — no directory scan ran
        assert not vsm.registered_files

        # And: one warning popup named the reason, matching the raised error
        fake_ls.window_show_message.assert_called_once()
        msg_params = fake_ls.window_show_message.call_args[0][0]
        assert msg_params.type == MessageType.Warning
        assert "No TRLC targets found in Bazel query" in msg_params.message
        assert str(exc_info.value) == "No TRLC targets found in Bazel query"

    def test_build_targets_second_call_exception_propagates_uncaught(
        self, tmp_path
    ):
        """An already-resolved manager's own build_targets() raising
        (its docstring says it should just be a cached early-return, so
        this is a manager-internal bug, not a "Bazel unavailable"
        condition) propagates out of discover() uncaught rather than
        triggering a directory-mode fallback — ParseEngine.validate()'s
        generic per-scope exception handler is the safety net, same as
        any other unexpected bug during a parse cycle."""
        config = self._config()
        vsm = RecordingVSM()
        fake_ls = _fake_ls()
        fake_ls.window_show_message = MagicMock()

        with patch("server.bazel.BazelClient") as mock_bazel:
            mock_bazel.find_bazel_workspace.return_value = str(tmp_path)

            mock_mgr = MagicMock()
            mock_mgr.build_targets.side_effect = RuntimeError("Query failed")
            fake_ls.bazel_cache.get_or_build.return_value = mock_mgr

            strategy = BazelScopeStrategy()
            with pytest.raises(RuntimeError, match="Query failed"):
                strategy.discover(vsm, str(tmp_path), config, fake_ls)

        # No fallback ran, and this isn't a BazelUnavailable so no popup
        # was shown by discover() itself either.
        assert not vsm.registered_files
        fake_ls.window_show_message.assert_not_called()

    def test_bazel_target_source_uses_buffer_content_not_disk(self, tmp_path):
        """A Bazel target's src file, if open in the editor, is registered
        with its unsaved buffer content — not the last-saved-to-disk
        content."""
        # Given: a target source file on disk, open in the editor with
        # different, unsaved content
        src_path = tmp_path / "a.rsl"
        src_path.write_text("package OnDisk\n")
        uri = uri_from_file(str(src_path))

        config = self._config()
        vsm = RecordingVSM()
        fake_ls = _fake_ls({uri: "package Unsaved\n"})

        mock_mgr = MagicMock()
        mock_mgr.build_targets.return_value = [MagicMock()]
        mock_mgr.files_for_target.return_value = [str(src_path)]
        fake_ls.bazel_cache.get_or_build.return_value = mock_mgr

        scope_id = encode_scope_id(uri_from_file(str(tmp_path)), "//pkg:a")

        # When: BazelScopeStrategy discovers files for a scope resolved to
        # Bazel target //pkg:a
        with patch("server.bazel.BazelClient") as mock_bazel:
            mock_bazel.find_bazel_workspace.return_value = str(tmp_path)

            reason = BazelScopeStrategy().discover(
                vsm, str(tmp_path), config, fake_ls, scope_id
            )

        # Then: the target's own transitive file set (from
        # files_for_target) is registered with the buffer content
        mock_mgr.files_for_target.assert_called_once_with("//pkg:a")
        assert vsm.registered_files[str(src_path)] == "package Unsaved\n"
        # And: no fallback happened - the configured Bazel strategy ran
        assert reason is None

    def test_shows_work_done_progress_around_the_bazel_query(self, tmp_path):
        """A cold Bazel query can take a long time (see
        ServerConfig.bazel_query_timeout_seconds); report_bazel_query_progress
        should show a work-done-progress indicator around it, same mechanism
        already used for parse progress, so the LSP doesn't look hung."""
        # Given: a target that resolves successfully
        src_path = tmp_path / "a.rsl"
        src_path.write_text("package A\n")
        config = self._config()
        vsm = RecordingVSM()
        fake_ls = _fake_ls()

        mock_mgr = MagicMock()
        mock_mgr.build_targets.return_value = [MagicMock()]
        mock_mgr.files_for_target.return_value = [str(src_path)]
        fake_ls.bazel_cache.get_or_build.return_value = mock_mgr

        scope_id = encode_scope_id(uri_from_file(str(tmp_path)), "//pkg:a")

        # When: BazelScopeStrategy discovers files
        with patch("server.bazel.BazelClient") as mock_bazel:
            mock_bazel.find_bazel_workspace.return_value = str(tmp_path)

            BazelScopeStrategy().discover(
                vsm, str(tmp_path), config, fake_ls, scope_id
            )

        # Then: the progress indicator was created, begun, and ended exactly
        # once around the query
        fake_ls.work_done_progress.create.assert_called_once()
        fake_ls.work_done_progress.begin.assert_called_once()
        fake_ls.work_done_progress.end.assert_called_once()
        token = fake_ls.work_done_progress.create.call_args[0][0]
        assert fake_ls.work_done_progress.begin.call_args[0][0] == token
        assert fake_ls.work_done_progress.end.call_args[0][0] == token

    def test_bazel_target_closure_excludes_other_targets_files(self, tmp_path):
        """Only the resolved target's own transitive closure is registered —
        not every TRLC target's own srcs found anywhere else in the
        workspace (the bug this fix addresses: a sibling target's files
        used to leak in, while a `deps`-only-reachable file could be
        missing)."""
        # Given: two files - one belongs to //pkg:a's closure, one doesn't
        own_path = tmp_path / "owned.rsl"
        other_path = tmp_path / "unrelated.rsl"
        own_path.write_text("package Owned\n")
        other_path.write_text("package Unrelated\n")

        config = self._config()
        vsm = RecordingVSM()
        fake_ls = _fake_ls()

        mock_mgr = MagicMock()
        mock_mgr.build_targets.return_value = [MagicMock(), MagicMock()]
        mock_mgr.files_for_target.return_value = [str(own_path)]
        fake_ls.bazel_cache.get_or_build.return_value = mock_mgr

        scope_id = encode_scope_id(uri_from_file(str(tmp_path)), "//pkg:a")

        # When: BazelScopeStrategy discovers files for //pkg:a's scope
        with patch("server.bazel.BazelClient") as mock_bazel:
            mock_bazel.find_bazel_workspace.return_value = str(tmp_path)

            reason = BazelScopeStrategy().discover(
                vsm, str(tmp_path), config, fake_ls, scope_id
            )

        # Then: only //pkg:a's own file is registered
        assert set(vsm.registered_files) == {str(own_path)}
        assert reason is None

    def test_raises_and_warns_when_resolved_target_has_no_files(
        self, tmp_path
    ):
        """A target resolved as the file's owner (a real `//...` label in
        scope_id) whose own files_for_target() closure comes back empty
        (e.g. its deps() query only reached non-TRLC files) shows a popup
        naming the target and raises BazelUnavailable — not a silent
        directory-mode fallback."""
        # Given: a target that resolves successfully but has no
        # parse-relevant files in its transitive closure
        config = self._config()
        vsm = RecordingVSM()
        fake_ls = _fake_ls()
        fake_ls.window_show_message = MagicMock()

        mock_mgr = MagicMock()
        mock_mgr.build_targets.return_value = [MagicMock()]
        mock_mgr.files_for_target.return_value = []
        fake_ls.bazel_cache.get_or_build.return_value = mock_mgr

        scope_id = encode_scope_id(uri_from_file(str(tmp_path)), "//pkg:a")

        # When: BazelScopeStrategy discovers files for //pkg:a's scope
        with patch("server.bazel.BazelClient") as mock_bazel:
            mock_bazel.find_bazel_workspace.return_value = str(tmp_path)

            with pytest.raises(BazelUnavailable) as exc_info:
                BazelScopeStrategy().discover(
                    vsm, str(tmp_path), config, fake_ls, scope_id
                )

        # Then: nothing registered, no directory-mode fallback ran
        assert not vsm.registered_files

        # And: one popup names the target label, matching the raised error
        fake_ls.window_show_message.assert_called_once()
        msg_params = fake_ls.window_show_message.call_args[0][0]
        assert msg_params.type == MessageType.Warning
        assert "No files found for Bazel target //pkg:a" in msg_params.message
        assert str(exc_info.value) == "No files found for Bazel target //pkg:a"

    def test_raises_and_warns_when_file_owns_no_target(self, tmp_path):
        """A file that doesn't belong to any Bazel target (scope_id()'s own
        directory-path fallback) now shows a meaningful popup and raises
        BazelUnavailable, instead of silently degrading to directory-mode
        discovery — the user needs to know the file simply isn't covered
        by any target's srcs."""
        # Given: a real file on disk, a working query that found targets
        # elsewhere, but this scope isn't owned by any of them
        (tmp_path / "orphan.rsl").write_text("package Orphan\n")
        config = self._config()
        vsm = RecordingVSM()
        fake_ls = _fake_ls()
        fake_ls.window_show_message = MagicMock()

        mock_mgr = MagicMock()
        mock_mgr.build_targets.return_value = [MagicMock()]
        fake_ls.bazel_cache.get_or_build.return_value = mock_mgr

        # scope_id's segment is a plain directory path, not a target label -
        # matches BazelScopeStrategy.scope_id()'s own fallback shape.
        scope_id = encode_scope_id(uri_from_file(str(tmp_path)), str(tmp_path))

        # When: BazelScopeStrategy discovers files for that scope
        with patch("server.bazel.BazelClient") as mock_bazel:
            mock_bazel.find_bazel_workspace.return_value = str(tmp_path)

            with pytest.raises(BazelUnavailable) as exc_info:
                BazelScopeStrategy().discover(
                    vsm, str(tmp_path), config, fake_ls, scope_id
                )

        # Then: no directory-mode discovery ran, nothing registered
        mock_mgr.files_for_target.assert_not_called()
        assert not vsm.registered_files

        # And: a popup names the reason (unlike the old, deliberately quiet
        # behavior) and the raised error carries the same reason
        fake_ls.window_show_message.assert_called_once()
        msg_params = fake_ls.window_show_message.call_args[0][0]
        assert msg_params.type == MessageType.Warning
        assert "not part of any Bazel TRLC target" in msg_params.message
        assert "not part of any Bazel TRLC target" in str(exc_info.value)


class TestGetScopeIdWorkspaceFallback:
    """`BazelScopeStrategy.scope_id()` — the scope-resolution half of the
    same mixed-workspace fallback exercised on the discover() side above
    (test_no_workspace_found_falls_back_to_directory_mode_quietly)."""

    def test_workspace_not_found_degrades_to_directory_scoped_id(self):
        """A folder with no Bazel workspace marker still gets a real
        (directory-scoped) scope, not ``None`` — ``None`` is reserved for
        a genuinely broken Bazel setup (see the ``BazelUnavailable``
        branch below), which the caller (did_open) treats as "no active
        scope" until fixed and reparsed."""

        # Given: resolve_bazel_target raising "no workspace here"
        def resolve_bazel_target():
            raise BazelWorkspaceNotFoundError("/ws")

        # When: scope_id is resolved for a uri under that folder
        scope_id = BazelScopeStrategy().scope_id(
            "file:///ws/pkg/a.rsl",
            "file:///ws",
            ServerConfig(),
            resolve_bazel_target,
        )

        # Then: it degrades to the same directory-scoped id shape used
        # when a file simply isn't owned by any target
        assert scope_id == encode_scope_id(
            "file:///ws",
            os.path.dirname(path_from_uri("file:///ws/pkg/a.rsl")),
        )

    def test_genuinely_broken_bazel_gives_no_scope_at_all(self):
        """Unlike workspace-not-found, a real BazelUnavailable (broken
        query/build) still resolves to ``None`` — no scope until fixed."""

        def resolve_bazel_target():
            raise BazelUnavailable("boom")

        scope_id = BazelScopeStrategy().scope_id(
            "file:///ws/pkg/a.rsl",
            "file:///ws",
            ServerConfig(),
            resolve_bazel_target,
        )

        assert scope_id is None


class TestGetScopeStrategy:
    """Factory function returns correct strategy for each mode."""

    def test_workspace_mode(self):
        # Given/When: the factory is called with WORKSPACE mode
        strategy = get_scope_strategy(ParseMode.WORKSPACE)
        # Then: a WorkspaceScopeStrategy is returned
        assert isinstance(strategy, WorkspaceScopeStrategy)

    def test_directory_mode(self):
        # Given/When: the factory is called with DIRECTORY mode
        strategy = get_scope_strategy(ParseMode.DIRECTORY)
        # Then: a DirectoryScopeStrategy is returned
        assert isinstance(strategy, DirectoryScopeStrategy)

    def test_repo_mode(self):
        # Given/When: the factory is called with REPO mode
        strategy = get_scope_strategy(ParseMode.REPO)
        # Then: a RepoScopeStrategy is returned
        assert isinstance(strategy, RepoScopeStrategy)

    def test_bazel_mode(self):
        # Given/When: the factory is called with BAZEL mode
        strategy = get_scope_strategy(ParseMode.BAZEL)
        # Then: a BazelScopeStrategy is returned
        assert isinstance(strategy, BazelScopeStrategy)
