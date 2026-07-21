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
"""Unit tests for :mod:`server.bazel`.

Exercises the success path that :mod:`tests.unit.test_scope_strategies`'s
``TestBazelFallback*`` tests deliberately don't cover (they only prove the
error/fallback path): XML parsing, label-to-path resolution (incl. the
path-traversal guard), the recursive file-collection DFS with a circular
dependency, and the target-list cache. The ``bazel`` binary itself is never
invoked — ``subprocess.run`` is stubbed at the boundary in ``_run_bazel``.
"""

# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access

import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from server.bazel import (
    BazelClient,
    BazelManagerCache,
    BazelQueryError,
    BazelTargetManager,
    _parse_xml_targets,
    build_target_manager,
)
from server.server_config import ServerConfig

_SAMPLE_XML = """<?xml version="1.0"?>
<query_result>
  <rule class="_trlc_requirement" name="//pkg/a:feature_requirements">
    <list name="srcs">
      <label value="//pkg/a:a.trlc"/>
    </list>
    <list name="deps">
      <label value="//pkg/b:base_requirements"/>
    </list>
  </rule>
  <rule class="_trlc_specification" name="//pkg/b:base_requirements">
    <list name="srcs">
      <label value="//pkg/b:b.rsl"/>
      <label value="//pkg/b:b2.rsl"/>
    </list>
  </rule>
  <rule class="some_unrelated_rule" name="//pkg/c:not_trlc">
    <list name="srcs">
      <label value="//pkg/c:c.txt"/>
    </list>
  </rule>
</query_result>
"""


class TestParseXmlTargets:
    """`_parse_xml_targets` — the XML -> BazelTarget[] success path."""

    def test_parses_matching_rule_classes_with_srcs_and_deps(self):
        """Given real bazel query XML, matching rules become BazelTargets
        with their srcs/deps label lists populated."""
        # Given: real bazel query XML with two matching rules and one non-match
        # When: parsing for the two matching rule classes
        targets = _parse_xml_targets(
            _SAMPLE_XML, ["_trlc_requirement", "_trlc_specification"]
        )

        # Then: both matching targets are returned with srcs/deps populated
        by_label = {t.label: t for t in targets}
        assert set(by_label) == {
            "//pkg/a:feature_requirements",
            "//pkg/b:base_requirements",
        }

        a = by_label["//pkg/a:feature_requirements"]
        assert a.package == "pkg/a"
        assert a.name == "feature_requirements"
        assert a.rule_class == "_trlc_requirement"
        assert a.srcs == ["//pkg/a:a.trlc"]
        assert a.deps == ["//pkg/b:base_requirements"]

        b = by_label["//pkg/b:base_requirements"]
        assert b.srcs == ["//pkg/b:b.rsl", "//pkg/b:b2.rsl"]
        assert b.deps == []

    def test_skips_rules_whose_class_is_not_in_rule_classes(self):
        """A rule matching none of the requested rule_classes is dropped,
        even though it appears in the XML alongside matching rules."""
        # Given: XML with a matching rule and an unrelated one
        # When: parsing for just the one matching rule class
        targets = _parse_xml_targets(_SAMPLE_XML, ["_trlc_requirement"])
        # Then: only the matching rule is returned
        assert [t.label for t in targets] == ["//pkg/a:feature_requirements"]

    def test_malformed_xml_returns_empty_list(self):
        """Unparseable XML is a recoverable condition (logged), not a crash."""
        # Given: XML that isn't well-formed
        # When: parsing it
        # Then: an empty list is returned, not an exception
        assert not _parse_xml_targets("<not valid xml", ["_trlc_requirement"])

    def test_oversized_xml_is_rejected_before_parsing(self):
        """Defense-in-depth: implausibly large query output is refused
        outright rather than handed to xml.etree (entity-expansion /
        memory-blowup guard)."""
        # Given: XML output far larger than any real workspace would produce
        huge = "<query_result></query_result>" + " " * (64 * 1024 * 1024 + 1)
        # When: parsing it
        # Then: it is rejected outright, never handed to the XML parser
        assert not _parse_xml_targets(huge, ["_trlc_requirement"])


class TestLabelToAbsPath:
    """`BazelClient.label_to_abs_path` — including the path-traversal guard."""

    def _client(self, tmp_path):
        return BazelClient(workspace_root=str(tmp_path))

    def test_resolves_package_colon_name_label(self, tmp_path):
        """`//pkg/a:a.trlc` resolves to `<root>/pkg/a/a.trlc`."""
        # Given/When/Then: a package:name label resolves under workspace_root
        client = self._client(tmp_path)
        result = client.label_to_abs_path("//pkg/a:a.trlc", str(tmp_path))
        assert result == str((tmp_path / "pkg" / "a" / "a.trlc").resolve())

    def test_resolves_label_without_colon(self, tmp_path):
        """A colon-less label resolves the same as its `:name` equivalent."""
        # Given/When/Then: a colon-less label resolves identically to :name
        client = self._client(tmp_path)
        result = client.label_to_abs_path("//pkg/a/a.trlc", str(tmp_path))
        assert result == str((tmp_path / "pkg" / "a" / "a.trlc").resolve())

    def test_non_absolute_label_returns_none(self, tmp_path):
        """A label missing the leading `//` is rejected, not guessed at."""
        # Given/When/Then: a label without the leading // resolves to None
        client = self._client(tmp_path)
        assert client.label_to_abs_path("pkg/a:a.trlc", str(tmp_path)) is None

    def test_rejects_traversal_in_package_segment(self, tmp_path):
        """A crafted label with a `..` package segment must not resolve to a
        path outside workspace_root."""
        # Given/When/Then: a ".." package segment is rejected, not resolved
        client = self._client(tmp_path)
        assert (
            client.label_to_abs_path("//../../etc:passwd", str(tmp_path))
            is None
        )

    def test_rejects_traversal_in_name_segment(self, tmp_path):
        """A crafted `..` in the name segment must not escape workspace_root."""
        # Given/When/Then: a ".." name segment is rejected, not resolved
        client = self._client(tmp_path)
        assert (
            client.label_to_abs_path("//pkg:../../etc/passwd", str(tmp_path))
            is None
        )

    def test_rejects_symlink_escaping_workspace_root(self, tmp_path):
        """Even without literal `..` segments, a path that resolves (via a
        symlink) outside workspace_root must be rejected."""
        # Given: a symlink inside the workspace pointing outside it
        outside = tmp_path.parent / f"{tmp_path.name}_outside_target"
        outside.mkdir(exist_ok=True)
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "evil_link").symlink_to(
            outside, target_is_directory=True
        )

        # When: resolving a label through the symlink
        # Then: it is rejected, since it resolves outside workspace_root
        client = self._client(tmp_path)
        result = client.label_to_abs_path("//pkg/evil_link:x", str(tmp_path))
        assert result is None


class TestRunBazel:
    """`BazelClient._run_bazel` — subprocess boundary error handling."""

    def _client(self, tmp_path):
        return BazelClient(workspace_root=str(tmp_path))

    def test_timeout_raises_bazel_query_error(self, tmp_path):
        """A hung `bazel` subprocess surfaces as BazelQueryError, so callers
        can distinguish it from an empty (successful) query."""
        # Given: subprocess.run configured to time out like a hung bazel
        client = self._client(tmp_path)
        with patch(
            "server.bazel.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="bazel", timeout=60),
        ):
            # When: the query is run
            # Then: running the query raises BazelQueryError
            with pytest.raises(BazelQueryError, match="timed out"):
                client._run_bazel(["bazel", "query", "//..."])

    def test_default_query_timeout_is_used(self, tmp_path):
        """With no explicit query_timeout_seconds, subprocess.run is given
        BazelClient.DEFAULT_QUERY_TIMEOUT_SECONDS, not a hardcoded literal."""
        # Given: a client constructed without an explicit timeout
        client = self._client(tmp_path)
        result = MagicMock(returncode=0, stdout="", stderr="")
        with patch(
            "server.bazel.subprocess.run", return_value=result
        ) as mock_run:
            # When: a query is run
            client._run_bazel(["bazel", "query", "//..."])
        # Then: subprocess.run's timeout matches the client's default
        assert (
            mock_run.call_args.kwargs["timeout"]
            == BazelClient.DEFAULT_QUERY_TIMEOUT_SECONDS
        )

    def test_custom_query_timeout_is_used(self, tmp_path):
        """A configured query_timeout_seconds is passed straight through to
        subprocess.run, and reported in the timeout error message."""
        # Given: a client constructed with a custom timeout
        client = BazelClient(
            workspace_root=str(tmp_path), query_timeout_seconds=5
        )
        with patch(
            "server.bazel.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="bazel", timeout=5),
        ) as mock_run:
            # When/Then: the query times out, reporting the configured value
            with pytest.raises(BazelQueryError, match=r"timed out \(5 s\)"):
                client._run_bazel(["bazel", "query", "//..."])
        assert mock_run.call_args.kwargs["timeout"] == 5

    def test_missing_executable_raises_bazel_query_error(self, tmp_path):
        """No `bazel` on PATH surfaces as BazelQueryError naming the binary."""
        # Given: subprocess.run configured to fail as if bazel isn't installed
        client = self._client(tmp_path)
        with patch(
            "server.bazel.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            # When: the query is run
            # Then: running the query raises BazelQueryError
            with pytest.raises(BazelQueryError, match="not found"):
                client._run_bazel(["bazel", "query", "//..."])

    def test_nonzero_non_partial_returncode_raises_with_stderr(self, tmp_path):
        """A hard failure (not the rc=3 partial-success case) raises, and the
        error carries the return code and stderr for actionable logging."""
        # Given: subprocess.run reporting a hard failure with stderr output
        client = self._client(tmp_path)
        result = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch("server.bazel.subprocess.run", return_value=result):
            # When: running the query
            with pytest.raises(BazelQueryError) as exc_info:
                client._run_bazel(["bazel", "query", "//..."])
        # Then: the raised error carries the return code and stderr
        assert exc_info.value.returncode == 1
        assert exc_info.value.stderr == "boom"

    def test_returncode_3_partial_success_returns_stdout(self, tmp_path):
        """rc=3 (some targets failed, some succeeded) is still usable output."""
        # Given: subprocess.run reporting the rc=3 partial-success case
        client = self._client(tmp_path)
        result = MagicMock(returncode=3, stdout="<query_result/>", stderr="")
        with patch("server.bazel.subprocess.run", return_value=result):
            # When: the query is run
            # Then: the query's stdout is still returned as usable output
            assert client._run_bazel(["bazel", "query", "//..."]) == (
                "<query_result/>"
            )


# pylint: disable-next=too-few-public-methods  # single test for build_target_manager's config wiring
class TestBuildContextManager:
    """`build_target_manager` — wires a ServerConfig's bazel.* fields into
    a fresh BazelClient."""

    def test_query_timeout_seconds_is_threaded_through(self, tmp_path):
        # Given: a config with a custom query timeout
        config = ServerConfig(bazel_query_timeout_seconds=42)
        # When: a context manager is built for it
        with patch.object(BazelTargetManager, "build_targets"):
            manager = build_target_manager(str(tmp_path), config)
        # Then: the underlying BazelClient uses that timeout
        assert manager.bazel_client.query_timeout_seconds == 42


class TestFindTrlcTargetsCache:
    """`BazelClient.find_trlc_targets` / `clear_cache` — caching behaviour."""

    def test_caches_targets_after_first_query(self, tmp_path):
        """A second call is served from cache, not a repeat `bazel query`."""
        # Given: a client with no cached targets yet
        client = BazelClient(
            workspace_root=str(tmp_path), rule_classes=["_trlc_requirement"]
        )
        with patch(
            "server.bazel.BazelClient._run_bazel_query_xml",
            return_value=_SAMPLE_XML,
        ) as mock_query:
            # When: find_trlc_targets is called twice
            first = client.find_trlc_targets()
            second = client.find_trlc_targets()

        # Then: both calls return the same targets, and only one real query ran
        assert len(first) == 1
        assert first == second
        mock_query.assert_called_once()

    def test_clear_cache_forces_requery(self, tmp_path):
        """`clear_cache` makes the next call issue a fresh `bazel query`."""
        # Given: a client whose targets have already been queried once
        client = BazelClient(
            workspace_root=str(tmp_path), rule_classes=["_trlc_requirement"]
        )
        with patch(
            "server.bazel.BazelClient._run_bazel_query_xml",
            return_value=_SAMPLE_XML,
        ) as mock_query:
            client.find_trlc_targets()
            # When: the cache is cleared and targets are queried again
            client.clear_cache()
            client.find_trlc_targets()

        # Then: a second real query ran
        assert mock_query.call_count == 2


class TestBazelManagerCache:
    """`BazelManagerCache` — build-once semantics, negative caching, and the
    guarantee that the factory never runs under the cache lock."""

    def test_builds_once_and_returns_cached_manager(self):
        """The factory runs exactly once and both calls get the same
        object."""
        # Given: an empty cache and a factory that records its calls
        cache = BazelManagerCache()
        manager = object()
        calls = []

        def factory():
            calls.append(1)
            return manager

        # When: the same workspace is requested twice
        first = cache.get_or_build("/ws", factory)
        second = cache.get_or_build("/ws", factory)

        # Then: both calls return the cached object, factory ran once
        assert first is manager and second is manager
        assert len(calls) == 1

    def test_factory_error_is_negative_cached_until_clear(self):
        """The stored error is re-raised without re-running the factory —
        until clear() resets the cache."""
        # Given: a factory that always raises
        cache = BazelManagerCache()
        calls = []

        def failing_factory():
            calls.append(1)
            raise BazelQueryError("Bazel command failed", returncode=1)

        # When: get_or_build is retried against the same workspace
        with pytest.raises(BazelQueryError):
            cache.get_or_build("/ws", failing_factory)
        with pytest.raises(BazelQueryError):
            cache.get_or_build("/ws", failing_factory)
        # Then: the error is re-raised from cache, factory not re-run
        assert len(calls) == 1  # negative-cached, not re-run

        # When: the cache is cleared and the workspace requested again
        cache.clear()
        with pytest.raises(BazelQueryError):
            cache.get_or_build("/ws", failing_factory)
        # Then: clear() allowed a fresh factory attempt
        assert len(calls) == 2

    def test_concurrent_callers_share_one_build(self):
        """Only one factory call runs and every thread receives the built
        manager."""
        # Given: a slow factory and several threads requesting the same
        # workspace concurrently
        cache = BazelManagerCache()
        manager = object()
        release = threading.Event()
        calls = []
        results = []

        def slow_factory():
            calls.append(1)
            assert release.wait(5)
            return manager

        def worker():
            results.append(cache.get_or_build("/ws", slow_factory))

        # When: all four threads race to build the same workspace
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        release.set()
        for t in threads:
            t.join(timeout=5)

        # Then: the factory ran exactly once, every thread got the same manager
        assert len(calls) == 1
        assert results == [manager] * 4

    def test_distinct_workspaces_get_distinct_managers(self):
        """Each workspace root gets its own factory-built manager."""
        # Given: an empty cache
        cache = BazelManagerCache()
        # When: two distinct workspace roots are each requested
        a = cache.get_or_build("/ws_a", object)
        b = cache.get_or_build("/ws_b", object)
        # Then: each got its own manager
        assert a is not b

    def test_clear_during_in_flight_build_does_not_spawn_a_second_builder(
        self,
    ):
        """clear() running while a build is still in flight must not let a
        new caller become a second concurrent builder for the same
        workspace (that would mean two `bazel query` subprocesses running
        at once for one workspace) — the in-flight build should be left
        alone to finish."""
        # Given: a build in flight for "/ws"
        cache = BazelManagerCache()
        manager = object()
        started = threading.Event()
        release = threading.Event()
        calls = []

        def slow_factory():
            calls.append(1)
            started.set()
            assert release.wait(5)
            return manager

        builder_thread = threading.Thread(
            target=cache.get_or_build, args=("/ws", slow_factory)
        )
        builder_thread.start()
        assert started.wait(2), "builder never started"

        # When: clear() runs while that build is still in flight, and then
        # another caller requests the same workspace before it finishes
        cache.clear()
        second_caller_result = []
        second_caller_thread = threading.Thread(
            target=lambda: second_caller_result.append(
                cache.get_or_build("/ws", slow_factory)
            )
        )
        second_caller_thread.start()

        # Then: no second factory call started - the second caller is
        # waiting on the same in-flight build, not building its own
        time.sleep(0.05)
        assert len(calls) == 1

        release.set()
        builder_thread.join(timeout=5)
        second_caller_thread.join(timeout=5)

        # And: both callers got the one manager that was actually built
        assert second_caller_result == [manager]

    def test_clear_evicts_entry_once_in_flight_build_completes(self):
        """The entry clear() couldn't drop immediately (build was still in
        flight) must still get evicted once that build finishes, so the
        *next* caller after that gets a properly fresh rebuild rather than
        silently reusing data from before whatever triggered the clear."""
        # Given: a build in flight for "/ws", cleared mid-flight
        cache = BazelManagerCache()
        first_manager = object()
        second_manager = object()
        started = threading.Event()
        release = threading.Event()
        managers = iter([first_manager, second_manager])

        def factory():
            started.set()
            release.wait(5)
            return next(managers)

        builder_thread = threading.Thread(
            target=cache.get_or_build, args=("/ws", factory)
        )
        builder_thread.start()
        assert started.wait(2), "builder never started"
        cache.clear()
        release.set()
        builder_thread.join(timeout=5)

        # When: a caller requests "/ws" again after the in-flight build
        # (the one clear() couldn't drop right away) has completed
        result = cache.get_or_build("/ws", factory)

        # Then: it gets a freshly-built manager, not the stale one
        assert result is second_manager

    def test_waiter_gives_up_after_wait_seconds(self):
        """A non-builder caller passing an explicit (short) wait_seconds
        gives up with BazelQueryError instead of blocking indefinitely when
        the builder is still running — used so the waiter timeout tracks a
        configured bazel_query_timeout_seconds rather than a fixed constant."""
        # Given: a builder that never finishes within the test
        cache = BazelManagerCache()
        started = threading.Event()
        release = threading.Event()

        def slow_factory():
            started.set()
            release.wait(5)
            return object()

        builder_thread = threading.Thread(
            target=cache.get_or_build, args=("/ws", slow_factory)
        )
        builder_thread.start()
        assert started.wait(2), "builder never started"

        # When: a second caller waits with a short explicit timeout
        # Then: it gives up instead of blocking for the builder's full run
        with pytest.raises(BazelQueryError, match="Timed out waiting"):
            cache.get_or_build("/ws", slow_factory, wait_seconds=0.2)

        release.set()
        builder_thread.join(timeout=5)

    def test_consume_error_for_warning_fires_once_per_entry(self):
        """The cached error is returned the first time it's observed for a
        given cache_key, ``None`` on every later call — the mechanism
        ``resolve_bazel_manager`` uses to show exactly one popup per
        distinct failure, no matter how many callers hit the same broken
        workspace."""
        # Given: a cache entry that failed to build
        cache = BazelManagerCache()

        def failing_factory():
            raise BazelQueryError("Bazel command failed", returncode=1)

        with pytest.raises(BazelQueryError):
            cache.get_or_build("/ws", failing_factory)

        # When/Then: the first observer gets the error, every later
        # observer (including for an unrelated, unbuilt key) gets None
        assert cache.consume_error_for_warning("/ws") is not None
        assert cache.consume_error_for_warning("/ws") is None
        assert cache.consume_error_for_warning("/ws") is None
        assert cache.consume_error_for_warning("/unknown-ws") is None

        # And: clear() resets it, so a fixed setup's next failure warns again
        cache.clear()
        with pytest.raises(BazelQueryError):
            cache.get_or_build("/ws", failing_factory)
        assert cache.consume_error_for_warning("/ws") is not None
