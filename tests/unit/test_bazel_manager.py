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
"""Unit tests for :mod:`server.bazel`'s manager layer.

Split out from ``test_bazel.py`` (which keeps the lower-level XML parsing /
label-resolution / run-boundary tests): this module covers
``resolve_bazel_manager`` (the single entry point), the negative-result
caching + retry policy, cross-context file collection, and invalidation.
The ``bazel`` binary itself is never invoked — the boundary is stubbed.
"""

# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from server.bazel import (
    BazelClient,
    BazelManagerCache,
    BazelQueryError,
    BazelTarget,
    BazelTargetManager,
    BazelUnavailable,
    BazelWorkspaceNotFoundError,
    _is_bazel_error_retryable,
    _parse_xml_source_files,
    resolve_bazel_manager,
)
from server.server_config import ServerConfig

from .conftest import patch_build_target_manager_failure


def _ls_stub():
    server = SimpleNamespace(
        bazel_cache=BazelManagerCache(),
        shown_messages=[],
    )
    server.window_show_message = server.shown_messages.append
    return server


class TestResolveBazelManager:
    """`resolve_bazel_manager` — the single entry point both scope-id
    resolution and file discovery go through: one resolution, one popup
    per distinct failure, regardless of how many callers/cycles hit it."""

    def test_success_returns_manager_without_any_popup(self, tmp_path):
        (tmp_path / "WORKSPACE").write_text("")
        ls = _ls_stub()
        fake_manager = object()
        with patch(
            "server.bazel.build_target_manager",
            return_value=fake_manager,
        ):
            result = resolve_bazel_manager(ls, str(tmp_path), ServerConfig())
        assert result is fake_manager
        assert not ls.shown_messages

    def test_no_workspace_raises_without_ever_showing_a_popup(self, tmp_path):
        """A plain, non-Bazel folder (no WORKSPACE/MODULE.bazel marker) is
        an expected outcome in a mixed workspace, not a broken-Bazel
        condition — repeated calls (as discover() makes every parse cycle)
        keep raising BazelWorkspaceNotFoundError so the caller degrades to
        directory-mode parsing, but never show a popup for it."""
        ls = _ls_stub()
        for _ in range(3):
            with pytest.raises(BazelWorkspaceNotFoundError):
                resolve_bazel_manager(ls, str(tmp_path), ServerConfig())
        assert not ls.shown_messages

    def test_query_failure_raises_and_warns_once(self, tmp_path):
        (tmp_path / "WORKSPACE").write_text("")
        ls = _ls_stub()
        with patch_build_target_manager_failure() as mock_build:
            for _ in range(2):
                with pytest.raises(BazelQueryError):
                    resolve_bazel_manager(ls, str(tmp_path), ServerConfig())
        mock_build.assert_called_once()
        assert len(ls.shown_messages) == 1

    def test_unexpected_build_exception_is_wrapped_as_unavailable(
        self, tmp_path
    ):
        (tmp_path / "WORKSPACE").write_text("")
        ls = _ls_stub()
        with patch(
            "server.bazel.build_target_manager",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(BazelUnavailable, match="boom"):
                resolve_bazel_manager(ls, str(tmp_path), ServerConfig())
        assert len(ls.shown_messages) == 1

    def test_workspace_lookup_exception_is_wrapped_as_unavailable(
        self, tmp_path
    ):
        ls = _ls_stub()
        with patch(
            "server.bazel.BazelClient.find_bazel_workspace",
            side_effect=RuntimeError("permission denied"),
        ):
            with pytest.raises(BazelUnavailable, match="permission denied"):
                resolve_bazel_manager(ls, str(tmp_path), ServerConfig())
        assert len(ls.shown_messages) == 1


class TestIsBazelErrorRetryable:
    """`_is_bazel_error_retryable` — the classification driving
    `BazelManagerCache.evict_retryable_errors`."""

    def test_workspace_not_found_is_not_retryable(self):
        """ "No Bazel workspace here" is a stable fact about the folder,
        not a condition that clears up between parse cycles."""
        assert not _is_bazel_error_retryable(
            BazelWorkspaceNotFoundError("/ws")
        )

    def test_timeout_style_error_with_no_returncode_is_retryable(self):
        """A timeout/missing-executable/OS-error BazelQueryError never got
        a real exit code (returncode=None) — momentary, worth retrying."""
        assert _is_bazel_error_retryable(
            BazelQueryError("Bazel command timed out (180 s)")
        )

    def test_server_lock_returncode_is_retryable(self):
        """Bazel's exit code 32 (another command holds the server lock)
        is transient."""
        assert _is_bazel_error_retryable(
            BazelQueryError("Bazel command failed", returncode=32)
        )

    def test_other_returncode_is_not_retryable(self):
        """A real query/BUILD-file/config failure (e.g. rc=1) won't fix
        itself on the next cycle."""
        assert not _is_bazel_error_retryable(
            BazelQueryError("Bazel command failed", returncode=1)
        )

    def test_non_bazel_query_error_is_not_retryable(self):
        assert not _is_bazel_error_retryable(RuntimeError("boom"))


class TestBazelManagerCacheRetry:
    """`BazelManagerCache.evict_retryable_errors` and the warned-key
    lifecycle it interacts with."""

    def test_evict_retryable_errors_forces_a_fresh_build_next_call(self):
        """A transient failure (returncode=None) is dropped by
        evict_retryable_errors, so the next get_or_build re-runs the
        factory instead of re-raising the stale error."""
        cache = BazelManagerCache()
        manager = object()
        calls = []

        def factory():
            calls.append(1)
            if len(calls) == 1:
                raise BazelQueryError("Bazel command timed out (180 s)")
            return manager

        with pytest.raises(BazelQueryError):
            cache.get_or_build("/ws", factory)
        assert len(calls) == 1

        # When: the parse-cycle eviction runs before the next call
        cache.evict_retryable_errors()
        result = cache.get_or_build("/ws", factory)

        # Then: the factory re-ran and succeeded
        assert result is manager
        assert len(calls) == 2

    def test_evict_retryable_errors_leaves_structural_failures_cached(self):
        """A non-retryable failure (e.g. BazelWorkspaceNotFoundError, or a
        real query error) must survive evict_retryable_errors — only
        clear() should reset it."""
        cache = BazelManagerCache()
        calls = []

        def failing_factory():
            calls.append(1)
            raise BazelWorkspaceNotFoundError("/ws")

        with pytest.raises(BazelWorkspaceNotFoundError):
            cache.get_or_build("/ws", failing_factory)

        # When: the parse-cycle eviction runs
        cache.evict_retryable_errors()

        # Then: the structural failure is still cached, factory not re-run
        with pytest.raises(BazelWorkspaceNotFoundError):
            cache.get_or_build("/ws", failing_factory)
        assert len(calls) == 1

    def test_retry_of_a_persistent_failure_does_not_warn_again(self):
        """If a transient failure is evicted and retried but fails again
        with the *same* underlying problem, consume_error_for_warning must
        not fire a second time — only clear() (or an intervening success)
        should let it warn again."""
        cache = BazelManagerCache()

        def failing_factory():
            raise BazelQueryError("Bazel command timed out (180 s)")

        with pytest.raises(BazelQueryError):
            cache.get_or_build("/ws", failing_factory)
        assert cache.consume_error_for_warning("/ws") is not None

        # When: evicted and retried, failing the same way again
        cache.evict_retryable_errors()
        with pytest.raises(BazelQueryError):
            cache.get_or_build("/ws", failing_factory)

        # Then: no second warning for the still-ongoing problem
        assert cache.consume_error_for_warning("/ws") is None

    def test_clear_after_recovery_lets_a_later_distinct_failure_warn_again(
        self,
    ):
        """A successful build stays cached until clear() (the only path
        back to a fresh build once a workspace recovers) — and clear()
        itself resets the warned-keys set, so a later, distinct failure
        for the same workspace still gets its own popup."""
        cache = BazelManagerCache()
        manager = object()
        outcomes = iter(
            [BazelQueryError("Bazel command timed out (180 s)"), manager]
        )

        def factory():
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with pytest.raises(BazelQueryError):
            cache.get_or_build("/ws", factory)
        assert cache.consume_error_for_warning("/ws") is not None

        # When: evicted, retried, and this time it succeeds
        cache.evict_retryable_errors()
        assert cache.get_or_build("/ws", factory) is manager

        # And: a config/workspace change clears the cache, then the
        # workspace fails again later, for a new reason
        cache.clear()

        def failing_again():
            raise BazelQueryError("Bazel command failed", returncode=1)

        with pytest.raises(BazelQueryError):
            cache.get_or_build("/ws", failing_again)

        # Then: the new, distinct failure warns again
        assert cache.consume_error_for_warning("/ws") is not None


class TestCollectFilesForTarget:
    """`BazelTargetManager._collect_files_for_target` — recursive DFS,
    including the visited-set guard against circular Bazel deps."""

    def _manager(self, tmp_path):
        client = BazelClient(workspace_root=str(tmp_path))
        return BazelTargetManager(client, str(tmp_path))

    def test_collects_srcs_transitively_through_deps(self, tmp_path):
        """A -> deps -> B: A's file collection includes both A's and B's
        srcs."""
        # Given: target A depending on target B
        manager = self._manager(tmp_path)
        all_targets = {
            "//a:a": BazelTarget(
                label="//a:a",
                package="a",
                name="a",
                rule_class="_trlc_requirement",
                srcs=["//a:a.trlc"],
                deps=["//b:b"],
            ),
            "//b:b": BazelTarget(
                label="//b:b",
                package="b",
                name="b",
                rule_class="_trlc_specification",
                srcs=["//b:b.rsl"],
            ),
        }

        # When: collecting files for target A
        files = manager._collect_files_for_target(
            "//a:a", all_targets, visited=set()
        )

        # Then: both A's own src and B's (transitive dep) src are included
        assert str((tmp_path / "a" / "a.trlc").resolve()) in files
        assert str((tmp_path / "b" / "b.rsl").resolve()) in files

    def test_circular_dependency_terminates_and_collects_each_target_once(
        self, tmp_path
    ):
        """A depends on B, B depends back on A: recursion must terminate
        (visited-set guard) rather than looping forever, and each target's
        srcs are still collected exactly once."""
        # Given: target A and B depending on each other (circular)
        manager = self._manager(tmp_path)
        all_targets = {
            "//a:a": BazelTarget(
                label="//a:a",
                package="a",
                name="a",
                rule_class="_trlc_requirement",
                srcs=["//a:a.trlc"],
                deps=["//b:b"],
            ),
            "//b:b": BazelTarget(
                label="//b:b",
                package="b",
                name="b",
                rule_class="_trlc_requirement",
                srcs=["//b:b.trlc"],
                deps=["//a:a"],
            ),
        }

        # When: collecting files for target A
        # Must return (not hang) and include both files exactly once.
        files = manager._collect_files_for_target(
            "//a:a", all_targets, visited=set()
        )

        # Then: recursion terminated and each target's src appears once
        a_path = str((tmp_path / "a" / "a.trlc").resolve())
        b_path = str((tmp_path / "b" / "b.trlc").resolve())
        assert files.count(a_path) == 1
        assert files.count(b_path) == 1

    def test_unknown_target_label_returns_empty_list(self, tmp_path):
        """A label absent from the alias map falls back to a `deps()`
        query (see the cross-repo case below); when that query itself
        fails (e.g. the label genuinely doesn't exist), the DFS still
        collects no files rather than propagating the failure."""
        # Given: a label absent from the alias map, and a deps() query
        # that fails as it would for a target that doesn't exist
        manager = self._manager(tmp_path)
        with patch.object(
            BazelClient,
            "_run_bazel_query_xml",
            side_effect=BazelQueryError(
                "Bazel command failed", returncode=1, stderr="no such target"
            ),
        ):
            # When/Then: collection returns no files, not an exception
            assert not manager._collect_files_for_target(
                "//missing:x", {}, set()
            )

    def test_label_not_visible_to_kind_query_falls_back_to_deps_query(
        self, tmp_path
    ):
        """A dep/spec label absent from `all_targets` because it points
        into an external repo (invisible to the workspace-scoped
        `kind(pattern, //...)` query — see BazelClient.run_deps_query_xml)
        must still resolve its files, via the same `deps()`-based closure
        `files_for_target` uses — not silently collect nothing, which was
        the bug: a target's own cross-repo dep used to vanish from the
        reverse file→target map entirely."""
        # Given: a label with no entry in all_targets, and a deps() query
        # that resolves it to a real (external-repo) file
        manager = self._manager(tmp_path)
        deps_xml = """<?xml version="1.0"?>
<query_result>
  <source-file name="@score_tooling//cfg:model.rsl"
                location="/cache/external/score_tooling+/cfg/model.rsl:1:1"/>
</query_result>
"""
        with patch.object(
            BazelClient, "_run_bazel_query_xml", return_value=deps_xml
        ) as mock_query:
            # When: collecting files for that label
            files = manager._collect_files_for_target(
                "@score_tooling//cfg:base_spec", {}, set()
            )

        # Then: the external file is resolved via deps(), not dropped
        mock_query.assert_called_once_with(
            'deps("@score_tooling//cfg:base_spec")'
        )
        assert files == ["/cache/external/score_tooling+/cfg/model.rsl"]


class TestBuildContextsAndInvalidate:
    """`BazelTargetManager.build_targets` / `invalidate` — end-to-end over
    a real (small) file tree, and the file->context index it produces."""

    def test_build_targets_indexes_files_to_owning_targets(self, tmp_path):
        """Each target's srcs are indexed by file path after build_targets."""
        # Given: a real Bazel workspace with two targets and their files on disk
        (tmp_path / "WORKSPACE").write_text("")
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "a.trlc").write_text("package A\n")
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "b.rsl").write_text("package B\n")

        client = BazelClient(
            workspace_root=str(tmp_path), rule_classes=["_trlc_requirement"]
        )
        manager = BazelTargetManager(client, str(tmp_path))

        xml = """<?xml version="1.0"?>
<query_result>
  <rule class="_trlc_requirement" name="//a:feature_requirements">
    <list name="srcs"><label value="//a:a.trlc"/></list>
    <list name="deps"><label value="//b:base_requirements"/></list>
  </rule>
  <rule class="_trlc_requirement" name="//b:base_requirements">
    <list name="srcs"><label value="//b:b.rsl"/></list>
  </rule>
</query_result>
"""
        with patch(
            "server.bazel.BazelClient._run_bazel_query_xml",
            return_value=xml,
        ):
            # When: building contexts from the query
            targets = manager.build_targets()

        # Then: both targets are indexed, files map to their owning target(s)
        assert len(targets) == 2

        a_trlc = str((tmp_path / "a" / "a.trlc").resolve())
        b_rsl = str((tmp_path / "b" / "b.rsl").resolve())

        # a.trlc belongs only to //a:feature_requirements
        assert manager.targets_for_file(a_trlc) == ["//a:feature_requirements"]
        # b.rsl is a dep of //a, so it's reachable from both contexts
        assert set(manager.targets_for_file(b_rsl)) == {
            "//a:feature_requirements",
            "//b:base_requirements",
        }

    def test_build_targets_skips_files_that_do_not_exist_on_disk(
        self, tmp_path
    ):
        """A target whose declared src has no file on disk (stale query,
        generated file not yet built) is silently excluded, not a crash."""
        # Given: a target whose declared src file doesn't exist on disk
        (tmp_path / "WORKSPACE").write_text("")
        client = BazelClient(
            workspace_root=str(tmp_path), rule_classes=["_trlc_requirement"]
        )
        manager = BazelTargetManager(client, str(tmp_path))

        xml = """<?xml version="1.0"?>
<query_result>
  <rule class="_trlc_requirement" name="//a:feature_requirements">
    <list name="srcs"><label value="//a:missing.trlc"/></list>
  </rule>
</query_result>
"""
        with patch(
            "server.bazel.BazelClient._run_bazel_query_xml",
            return_value=xml,
        ):
            # When: building contexts
            # Then: the missing file is excluded, not a crash
            manager.build_targets()

    def test_build_targets_is_cached_after_first_call(self, tmp_path):
        """A second build_targets() call (e.g. BazelScopeStrategy.discover
        calling it on every parse cycle) must not redo the file-collection
        walk — it should return the cached target list, same as
        find_trlc_targets's own "cached after first call" behavior."""
        # Given: a manager with one target, and a query mock that would
        # reveal a re-query if called again
        (tmp_path / "WORKSPACE").write_text("")
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "a.trlc").write_text("package A\n")

        client = BazelClient(
            workspace_root=str(tmp_path), rule_classes=["_trlc_requirement"]
        )
        manager = BazelTargetManager(client, str(tmp_path))

        xml = """<?xml version="1.0"?>
<query_result>
  <rule class="_trlc_requirement" name="//a:feature_requirements">
    <list name="srcs"><label value="//a:a.trlc"/></list>
  </rule>
</query_result>
"""
        with patch(
            "server.bazel.BazelClient._run_bazel_query_xml",
            return_value=xml,
        ) as mock_query:
            first = manager.build_targets()
            second = manager.build_targets()

        # Then: the underlying query ran once, and both calls agree
        assert mock_query.call_count == 1
        assert len(first) == 1
        assert [t.label for t in second] == [t.label for t in first]

        # And: invalidate() busts the cache, so a subsequent call re-builds
        manager.invalidate()
        with patch(
            "server.bazel.BazelClient._run_bazel_query_xml",
            return_value=xml,
        ) as mock_query_after_invalidate:
            manager.build_targets()
        assert mock_query_after_invalidate.call_count == 1

    def test_invalidate_clears_target_and_alias_cache(self, tmp_path):
        """Invalidating drops both the target and file-alias caches."""
        # Given: a manager with built targets
        (tmp_path / "WORKSPACE").write_text("")
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "a.trlc").write_text("package A\n")

        client = BazelClient(
            workspace_root=str(tmp_path), rule_classes=["_trlc_requirement"]
        )
        manager = BazelTargetManager(client, str(tmp_path))

        xml = """<?xml version="1.0"?>
<query_result>
  <rule class="_trlc_requirement" name="//a:feature_requirements">
    <list name="srcs"><label value="//a:a.trlc"/></list>
  </rule>
</query_result>
"""
        with patch(
            "server.bazel.BazelClient._run_bazel_query_xml",
            return_value=xml,
        ):
            manager.build_targets()
        assert manager.targets

        # When: invalidate is called
        manager.invalidate()

        # Then: both the target and file-alias caches are cleared
        assert not manager.targets
        assert not manager.file_to_targets
        assert not client._targets_cache


class TestParseXmlSourceFiles:
    """`_parse_xml_source_files` — the `<source-file location="...">`
    extraction `files_for_target` relies on for its `deps()`-based
    closure, including a location string pointing outside the workspace
    root (an external Bzlmod repo's checkout) that `label_to_abs_path`'s
    traversal guard would have rejected."""

    def test_extracts_trlc_and_rsl_paths(self):
        xml = """<?xml version="1.0"?>
<query_result>
  <source-file name="//pkg:a.trlc" location="/ws/pkg/a.trlc:1:1"/>
  <source-file name="//pkg:a.rsl" location="/ws/pkg/a.rsl:1:1"/>
</query_result>
"""
        assert _parse_xml_source_files(xml) == [
            "/ws/pkg/a.trlc",
            "/ws/pkg/a.rsl",
        ]

    def test_filters_out_non_trlc_files(self):
        """A target's transitive deps() closure also includes build-tool
        files (.py, .yaml, bootstrap templates, ...) — confirmed live
        against a real repo; only .trlc/.rsl are parse-relevant."""
        xml = """<?xml version="1.0"?>
<query_result>
  <source-file name="//pkg:a.trlc" location="/ws/pkg/a.trlc:1:1"/>
  <source-file name="//tool:renderer.py" location="/ws/tool/renderer.py:1:1"/>
  <source-file name="//cfg:x.yaml" location="/ws/cfg/x.yaml:1:1"/>
</query_result>
"""
        assert _parse_xml_source_files(xml) == ["/ws/pkg/a.trlc"]

    def test_resolves_a_cross_repo_location_outside_the_workspace_root(self):
        """Bazel's own `location` for an external-repo file lands under its
        real checkout (e.g. `.../external/<repo>+/...`), nowhere near the
        main repo's workspace_root — unlike `label_to_abs_path`, this
        extraction has no traversal guard rejecting it, since Bazel already
        resolved the real path itself."""
        xml = """<?xml version="1.0"?>
<query_result>
  <source-file name="@score_tooling//cfg:model.rsl"
                location="/home/user/.cache/bazel/.../external/score_tooling+/cfg/model.rsl:1:1"/>
</query_result>
"""
        assert _parse_xml_source_files(xml) == [
            "/home/user/.cache/bazel/.../external/score_tooling+/cfg/model.rsl"
        ]

    def test_empty_query_result_returns_no_files(self):
        xml = '<?xml version="1.0"?>\n<query_result></query_result>\n'
        assert not _parse_xml_source_files(xml)

    def test_malformed_xml_returns_empty_list_not_an_exception(self):
        assert not _parse_xml_source_files("<not><valid")


class TestGetFilesForContextClosure:
    """`BazelTargetManager.files_for_target` — the new lazy,
    `deps()`-based closure resolution (round 2), replacing the old
    `_contexts`/`absolute_srcs` lookup that couldn't see cross-repo labels.
    """

    _DEPS_XML = """<?xml version="1.0"?>
<query_result>
  <source-file name="//pkg:a.trlc" location="/ws/pkg/a.trlc:1:1"/>
  <source-file name="@score_tooling//cfg:model.rsl"
                location="/cache/external/score_tooling+/cfg/model.rsl:1:1"/>
</query_result>
"""

    def _manager(self, tmp_path):
        client = BazelClient(workspace_root=str(tmp_path))
        return BazelTargetManager(client, str(tmp_path))

    def test_resolves_via_a_deps_query_not_the_kind_query(self, tmp_path):
        """Confirms files_for_target actually issues `deps("<label>")`
        — not the blanket `kind(pattern, //...)` build_targets() uses —
        which is what lets it cross repository boundaries."""
        manager = self._manager(tmp_path)
        with patch.object(
            BazelClient, "_run_bazel_query_xml", return_value=self._DEPS_XML
        ) as mock_query:
            files = manager.files_for_target("//pkg:feature_requirements")

        mock_query.assert_called_once_with(
            'deps("//pkg:feature_requirements")'
        )
        assert files == [
            "/ws/pkg/a.trlc",
            "/cache/external/score_tooling+/cfg/model.rsl",
        ]

    def test_memoizes_per_label_across_repeated_calls(self, tmp_path):
        """A second call for the same label must not re-spawn `bazel
        query` — closure resolution is one subprocess per label, not per
        parse cycle."""
        manager = self._manager(tmp_path)
        with patch.object(
            BazelClient, "_run_bazel_query_xml", return_value=self._DEPS_XML
        ) as mock_query:
            manager.files_for_target("//pkg:feature_requirements")
            manager.files_for_target("//pkg:feature_requirements")

        mock_query.assert_called_once()

    def test_different_labels_are_resolved_independently(self, tmp_path):
        manager = self._manager(tmp_path)
        with patch.object(
            BazelClient, "_run_bazel_query_xml", return_value=self._DEPS_XML
        ) as mock_query:
            manager.files_for_target("//pkg:a")
            manager.files_for_target("//pkg:b")

        assert mock_query.call_count == 2

    def test_invalidate_clears_the_memoized_closure_cache(self, tmp_path):
        manager = self._manager(tmp_path)
        with patch.object(
            BazelClient, "_run_bazel_query_xml", return_value=self._DEPS_XML
        ) as mock_query:
            manager.files_for_target("//pkg:feature_requirements")
            manager.invalidate()
            manager.files_for_target("//pkg:feature_requirements")

        assert mock_query.call_count == 2

    def test_empty_deps_result_returns_empty_list(self, tmp_path):
        manager = self._manager(tmp_path)
        with patch.object(
            BazelClient, "_run_bazel_query_xml", return_value=""
        ):
            assert not manager.files_for_target("//pkg:nothing")
