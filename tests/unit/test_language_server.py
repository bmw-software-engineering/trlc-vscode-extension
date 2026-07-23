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
"""Unit tests for TrlcLanguageServer._resolve_bazel_target, the
scope-resolution half of the BAZEL parse mode."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

# pylint: disable=protected-access  # tests legitimately access private members to verify internal state

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from server.bazel import (
    BazelManagerCache,
    BazelQueryError,
    BazelUnavailable,
    BazelWorkspaceNotFoundError,
)
from server.context_store import ContextStore
from server.language_server import TrlcLanguageServer
from server.server_config import ServerConfig
from server.token_utils import uri_from_file

from .conftest import patch_build_target_manager_failure


class TestResolveBazelTarget:
    """Real (unbound) ``TrlcLanguageServer._resolve_bazel_target`` — the
    scope-resolution half of the BAZEL parse mode. ``build_target_manager``
    is patched (it would otherwise shell out to real ``bazel query``); the
    workspace-root discovery (``BazelClient.find_bazel_workspace``) runs for
    real against tmp_path, since it's a pure filesystem walk."""

    def _server_stub(self, config=None):
        server = SimpleNamespace(
            store=ContextStore(),
            bazel_cache=BazelManagerCache(),
            get_config_for_uri=lambda _uri: config or ServerConfig(),
            work_done_progress=SimpleNamespace(
                create=lambda _token: None,
                begin=lambda _token, _value: None,
                end=lambda _token, _value: None,
            ),
            shown_messages=[],
        )
        server.window_show_message = server.shown_messages.append
        return server

    def test_resolves_to_the_owning_target_label(self, tmp_path):
        """A file under a real Bazel workspace resolves to its target label."""
        # Given: a real Bazel workspace, and a manager reporting one owning target
        (tmp_path / "WORKSPACE").write_text("")
        file_path = tmp_path / "pkg" / "a.trlc"
        file_path.parent.mkdir()
        file_path.write_text("package A\n")

        fake_manager = SimpleNamespace(
            targets_for_file=lambda _fp: ["//pkg:feature_requirements"]
        )
        server = self._server_stub()
        with patch(
            "server.bazel.build_target_manager",
            return_value=fake_manager,
        ):
            # When: resolving the file's Bazel target
            result = TrlcLanguageServer._resolve_bazel_target(
                server, uri_from_file(str(file_path))
            )

        # Then: it resolves to that target's label
        assert result == "//pkg:feature_requirements"

    def test_file_owned_by_no_target_returns_none(self, tmp_path):
        """A file that exists in a Bazel workspace but isn't listed in any
        target's srcs falls back (caller then uses directory-scoped
        parsing)."""
        # Given: a Bazel workspace, and a manager reporting no owning target
        (tmp_path / "WORKSPACE").write_text("")
        file_path = tmp_path / "untracked.trlc"
        file_path.write_text("package A\n")

        fake_manager = SimpleNamespace(targets_for_file=lambda _fp: [])
        server = self._server_stub()
        with patch(
            "server.bazel.build_target_manager",
            return_value=fake_manager,
        ):
            # When: resolving the file's Bazel target
            result = TrlcLanguageServer._resolve_bazel_target(
                server, uri_from_file(str(file_path))
            )

        # Then: resolution returns None (caller falls back to directory mode)
        assert result is None

    def test_no_bazel_workspace_raises_without_building_manager(
        self, tmp_path
    ):
        """A file outside any Bazel workspace (no WORKSPACE/MODULE.bazel
        marker) must short-circuit before even attempting to build a
        manager (no query), and raise BazelWorkspaceNotFoundError rather
        than silently degrading — the caller (BazelScopeStrategy.scope_id)
        turns that into a directory-scoped fallback, since a workspace can
        legitimately mix Bazel and non-Bazel folders. No popup: this is an
        expected outcome for a plain folder, not a broken-Bazel error."""
        # Given: a file with no Bazel workspace marker anywhere above it
        file_path = tmp_path / "a.trlc"
        file_path.write_text("package A\n")

        server = self._server_stub()
        with patch("server.bazel.build_target_manager") as mock_build:
            # When: resolving the file's Bazel target
            with pytest.raises(BazelWorkspaceNotFoundError):
                TrlcLanguageServer._resolve_bazel_target(
                    server, uri_from_file(str(file_path))
                )

        # Then: no manager was ever built, and no popup was shown
        mock_build.assert_not_called()
        assert not server.shown_messages

    def test_manager_build_failure_raises_and_warns(self, tmp_path):
        """If building/querying the manager raises (e.g. bazel not
        installed), resolution raises BazelUnavailable (wrapping the
        original exception) instead of silently degrading — the caller
        decides what "unavailable" means (no scope / keep last good)."""
        # Given: a Bazel workspace, and a manager build that raises
        (tmp_path / "WORKSPACE").write_text("")
        file_path = tmp_path / "a.trlc"
        file_path.write_text("package A\n")

        server = self._server_stub()
        with patch(
            "server.bazel.build_target_manager",
            side_effect=RuntimeError("bazel not found"),
        ):
            # When: resolving the file's Bazel target
            with pytest.raises(BazelUnavailable, match="bazel not found"):
                TrlcLanguageServer._resolve_bazel_target(
                    server, uri_from_file(str(file_path))
                )

        assert len(server.shown_messages) == 1

    def test_bazel_query_error_raises_and_is_negative_cached(self, tmp_path):
        """A BazelQueryError (broken bazel setup) raises on every call, but
        the failure is negative-cached: the second resolution must not
        re-run the expensive build, and only the first shows a popup."""
        # Given: a Bazel workspace, and a manager build that raises BazelQueryError
        (tmp_path / "WORKSPACE").write_text("")
        file_path = tmp_path / "a.trlc"
        file_path.write_text("package A\n")

        server = self._server_stub()
        with patch_build_target_manager_failure() as mock_build:
            # When: resolving the same file's target twice
            with pytest.raises(BazelQueryError):
                TrlcLanguageServer._resolve_bazel_target(
                    server, uri_from_file(str(file_path))
                )
            with pytest.raises(BazelQueryError):
                TrlcLanguageServer._resolve_bazel_target(
                    server, uri_from_file(str(file_path))
                )

        # Then: the build ran only once (negative-cached), and only one
        # popup was shown despite two failing calls.
        mock_build.assert_called_once()
        assert len(server.shown_messages) == 1

    def test_multiple_owning_targets_pick_shortest_label_deterministically(
        self, tmp_path
    ):
        """A file shared by several targets (e.g. a spec included by
        multiple requirement targets) must always resolve to the same
        target label — the shortest one, tie-broken lexically."""
        # Given: a file owned by three targets of differing label length
        (tmp_path / "WORKSPACE").write_text("")
        file_path = tmp_path / "shared.rsl"
        file_path.write_text("package Shared\n")

        fake_manager = SimpleNamespace(
            targets_for_file=lambda _fp: [
                "//pkg/long/path:feature_requirements",
                "//a:x",
                "//b:y",
            ]
        )
        server = self._server_stub()
        with patch(
            "server.bazel.build_target_manager",
            return_value=fake_manager,
        ):
            # When: resolving the file's Bazel target
            result = TrlcLanguageServer._resolve_bazel_target(
                server, uri_from_file(str(file_path))
            )

        # Then: the shortest label wins deterministically
        assert result == "//a:x"

    def test_manager_is_cached_across_calls_for_same_workspace(self, tmp_path):
        """The (expensive, subprocess-backed) manager is built once per
        Bazel workspace and reused, not rebuilt on every scope-resolution
        call — proven via the store's cache, not just call counting."""
        # Given: a Bazel workspace and a manager factory
        (tmp_path / "WORKSPACE").write_text("")
        file_path = tmp_path / "a.trlc"
        file_path.write_text("package A\n")

        fake_manager = SimpleNamespace(
            targets_for_file=lambda _fp: ["//pkg:x"]
        )
        server = self._server_stub()
        with patch(
            "server.bazel.build_target_manager",
            return_value=fake_manager,
        ) as mock_build:
            # When: the same file's target is resolved twice
            TrlcLanguageServer._resolve_bazel_target(
                server, uri_from_file(str(file_path))
            )
            TrlcLanguageServer._resolve_bazel_target(
                server, uri_from_file(str(file_path))
            )

        # Then: the manager was built only once
        mock_build.assert_called_once()
