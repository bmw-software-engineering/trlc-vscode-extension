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
"""Unit tests for trlc_lsp.parse_engine.ParseEngine.

Tests cover the public API (queue_event), the internal event-dispatch
logic (_drain_and_validate), the debounce loop (_run), and the per-scope
diagnostic-publish cycle (validate).

A minimal ls stub is used instead of a full TrlcLanguageServer so the
engine can be exercised without a running pygls server. ``ls_stub.store``
is a REAL ContextStore (not a MagicMock) — the engine reads contexts
through it, and a MagicMock's default (empty, always-truthy) iteration
behavior would silently turn every contexts_to_validate lookup into a
no-op, making these tests vacuous.
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

# pylint: disable=protected-access  # tests legitimately access private members to verify internal state

# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

import threading
from unittest.mock import MagicMock, patch

import pytest
from lsprotocol.types import (
    Diagnostic,
    DiagnosticSeverity,
    Position,
    Range,
)

from server.bazel import BazelUnavailable
from server.context_store import ContextStore
from server.parse_context import ParseContext, ParseResult
from server.parse_engine import ParseEngine
from server.server_config import ParseMode, ServerConfig
from server.token_utils import uri_from_file

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ls_stub():
    """Minimal language-server stub with the attributes ParseEngine uses."""
    ls = MagicMock()
    ls.fh.files = {}
    ls.workspace.folders = {}
    ls.store = ContextStore()
    ls.get_config_for_uri = MagicMock(return_value=ServerConfig())
    return ls


@pytest.fixture
def engine(ls_stub):
    """A ParseEngine instance wired to ls_stub.

    The worker thread is NOT started (engine.start() is not called) so
    tests can call _drain_and_validate() / validate() synchronously.
    """
    return ParseEngine(ls_stub)


# ---------------------------------------------------------------------------
# queue_event
# ---------------------------------------------------------------------------


class TestQueueEvent:
    def test_event_is_enqueued(self, engine):
        # Given: a fresh engine
        # When: an event is queued
        engine.queue_event("change", "file://foo", "content")
        # Then: it is present in the internal queue
        with engine._queue_lock:
            assert len(engine._queue) == 1

    def test_trigger_is_set_after_enqueue(self, engine):
        # Given: a fresh engine, trigger not yet set
        assert not engine._trigger.is_set()
        # When: an event is queued
        engine.queue_event("reparse")
        # Then: the trigger event is set, waking the worker
        assert engine._trigger.is_set()

    def test_multiple_events_accumulate(self, engine):
        # Given: a fresh engine
        # When: two events are queued
        engine.queue_event("change", "file://a", "x")
        engine.queue_event("change", "file://b", "y")
        # Then: both are present in the queue
        with engine._queue_lock:
            assert len(engine._queue) == 2

    def test_events_are_prepended_lifo(self, engine):
        """Newer events sit at the front (appendleft) and are popped first."""
        # Given: a fresh engine
        # When: two events are queued in order
        engine.queue_event("change", "file://first", "a")
        engine.queue_event("change", "file://second", "b")
        # Then: the newest event sits at the front of the deque
        with engine._queue_lock:
            # deque: leftmost item is the newest (most recently appended)
            uri_newest = engine._queue[0][1]
        assert uri_newest == "file://second"


# ---------------------------------------------------------------------------
# _drain_and_validate — event dispatch
# ---------------------------------------------------------------------------


class TestDrainAndValidate:
    def test_change_calls_update_files(self, engine, ls_stub):
        # Given: a queued "change" event
        engine.queue_event("change", "file://x", "hello")
        # When: the queue is drained
        with patch.object(engine, "validate"):
            engine._drain_and_validate()
        # Then: the file handler's content is updated
        ls_stub.fh.update_files.assert_called_once_with("file://x", "hello")

    def test_delete_calls_delete_files(self, engine, ls_stub):
        # Given: a queued "delete" event
        engine.queue_event("delete", "file://x", None)
        # When: the queue is drained
        with patch.object(engine, "validate"):
            engine._drain_and_validate()
        # Then: the file handler removes the file
        ls_stub.fh.delete_files.assert_called_once_with("file://x")

    def test_reparse_calls_neither_update_nor_delete(self, engine, ls_stub):
        # Given: a queued "reparse" event
        engine.queue_event("reparse")
        # When: the queue is drained
        with patch.object(engine, "validate"):
            engine._drain_and_validate()
        # Then: neither update nor delete is called on the file handler
        ls_stub.fh.update_files.assert_not_called()
        ls_stub.fh.delete_files.assert_not_called()

    def test_reparse_clears_the_bazel_cache(self, engine, ls_stub):
        """A full "reparse" (workspace-folder/config changes, or a
        Bazel-graph file change) invalidates the cached Bazel query result
        so the next parse re-runs it against the new state."""
        # Given: a queued "reparse" event
        engine.queue_event("reparse")
        # When: the queue is drained
        with patch.object(engine, "validate"):
            engine._drain_and_validate()
        # Then: the Bazel cache is cleared
        ls_stub.bazel_cache.clear.assert_called_once()

    def test_reparse_files_calls_neither_update_nor_delete(
        self, engine, ls_stub
    ):
        # Given: a queued "reparse_files" event (plain on-disk .trlc/.rsl
        # content change reported by the client's file watcher)
        engine.queue_event("reparse_files")
        # When: the queue is drained
        with patch.object(engine, "validate"):
            engine._drain_and_validate()
        # Then: neither update nor delete is called on the file handler
        ls_stub.fh.update_files.assert_not_called()
        ls_stub.fh.delete_files.assert_not_called()

    def test_reparse_files_does_not_clear_the_bazel_cache(
        self, engine, ls_stub
    ):
        """Unlike "reparse", "reparse_files" must NOT clear bazel_cache — a
        plain content change never alters the Bazel target graph, so
        forcing a full `bazel query` re-run for it would be pure waste."""
        # Given: a queued "reparse_files" event
        engine.queue_event("reparse_files")
        # When: the queue is drained
        with patch.object(engine, "validate"):
            engine._drain_and_validate()
        # Then: the Bazel cache is left untouched
        ls_stub.bazel_cache.clear.assert_not_called()

    def test_reparse_files_still_triggers_validate(self, engine):
        # Given: a queued "reparse_files" event
        engine.queue_event("reparse_files")
        # When: the queue is drained
        with patch.object(engine, "validate") as mock_validate:
            engine._drain_and_validate()
        # Then: validate() still runs (discover() + the input-signature
        # check are what actually pick up the on-disk change)
        mock_validate.assert_called_once()

    def test_empty_queue_returns_without_validate(self, engine):
        # Given: an engine with an empty event queue
        # When: draining is attempted
        with patch.object(engine, "validate") as mock_validate:
            engine._drain_and_validate()
        # Then: validate() is never called
        mock_validate.assert_not_called()

    def test_validate_called_after_draining(self, engine):
        # Given: a queued "reparse" event
        engine.queue_event("reparse")
        # When: the queue is drained
        with patch.object(engine, "validate") as mock_validate:
            engine._drain_and_validate()
        # Then: validate() is called exactly once
        mock_validate.assert_called_once()


# ---------------------------------------------------------------------------
# _run — debounce loop
# ---------------------------------------------------------------------------


class _StepSleep:
    """Deterministic stand-in for ``time.sleep`` in the debounce loop.

    Each call blocks until the test explicitly calls :meth:`step` — so a
    test can drive the debounce loop tick-by-tick (queue events while a
    tick is "in flight", then release it) without racing real wall-clock
    timing. Replaces the old approach of picking a tiny real
    ``DEBOUNCE_SECONDS`` and hoping ``time.sleep`` calls in the test thread
    land on the right side of it.
    """

    def __init__(self):
        self._entered = threading.Event()
        self._release = threading.Event()

    def __call__(self, _seconds):
        self._entered.set()
        self._release.wait(5)
        self._release.clear()
        self._entered.clear()

    def wait_until_sleeping(self, timeout=2):
        """Block until the worker is inside this fake sleep (i.e. has
        started a debounce tick)."""
        assert self._entered.wait(timeout), (
            "worker never entered the debounce sleep"
        )
        self._entered.clear()

    def step(self):
        """Let the current debounce tick complete."""
        self._release.set()

    def release_all(self):
        """Unblock a worker that may still be mid-tick — test teardown
        safety net so a failed assertion never leaves engine.stop()'s
        join() hanging on a worker stuck in this fake sleep."""
        self._release.set()


class TestDebounce:
    """Exercises the real worker thread (_run), not just _drain_and_validate
    directly, so the debounce-coalescing behavior itself is covered."""

    def test_rapid_events_coalesce_into_one_validate_call(self, ls_stub):
        """Several queue_event calls fired while a debounce tick is still
        in flight (the real-world "faster than the debounce window" case)
        must trigger exactly one validate() cycle, with all of them applied."""
        # Given: a worker thread whose debounce sleep is under test control
        fake_sleep = _StepSleep()
        engine = ParseEngine(ls_stub, sleep_fn=fake_sleep)
        validated = threading.Event()
        with patch.object(
            engine, "validate", side_effect=validated.set
        ) as mock_validate:
            engine.start()
            try:
                # When: the first event starts a debounce tick, and 4 more
                # arrive while that tick is still in flight
                engine.queue_event("change", "file://0", "x")
                fake_sleep.wait_until_sleeping()
                for i in range(1, 5):
                    engine.queue_event("change", f"file://{i}", "x")

                # Completing that tick sees the trigger re-armed (by the 4
                # late arrivals) and starts one more tick to let them settle...
                fake_sleep.step()
                fake_sleep.wait_until_sleeping()
                # ...which sees no further arrivals and proceeds to validate.
                fake_sleep.step()
                assert validated.wait(timeout=2), (
                    "validate() was never called within the timeout"
                )
            finally:
                fake_sleep.release_all()
                engine.stop()
        # Then: they coalesce into exactly one validate() call, all applied
        assert mock_validate.call_count == 1
        assert ls_stub.fh.update_files.call_count == 5

    def test_events_after_debounce_window_trigger_separate_cycles(
        self, ls_stub
    ):
        """Two events with a completed debounce tick between them (the
        real-world "beyond the debounce window" case) each get their own
        validate() cycle."""
        # Given: a running worker with a controllable debounce sleep
        fake_sleep = _StepSleep()
        engine = ParseEngine(ls_stub, sleep_fn=fake_sleep)
        calls = []
        call_event = threading.Event()

        def _record():
            calls.append(1)
            call_event.set()

        with patch.object(engine, "validate", side_effect=_record):
            engine.start()
            try:
                # When: the first event's tick completes with no further
                # arrivals, so it proceeds straight to its own validate()
                engine.queue_event("change", "file://a", "1")
                fake_sleep.wait_until_sleeping()
                fake_sleep.step()
                assert call_event.wait(timeout=2)
                call_event.clear()

                # A second, later event starts an entirely separate cycle
                engine.queue_event("change", "file://b", "2")
                fake_sleep.wait_until_sleeping()
                fake_sleep.step()
                assert call_event.wait(timeout=2)
            finally:
                fake_sleep.release_all()
                engine.stop()
        # Then: each triggers its own separate validate() cycle
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# stop — worker shutdown
# ---------------------------------------------------------------------------


class TestStop:
    def test_stop_joins_the_worker_thread(self, engine):
        """Given a running worker, When stop() is called, Then it returns
        only after the thread has actually exited (not merely signalled)."""
        # Given: a running worker
        engine.start()
        # When: stop() is called
        engine.stop()
        # Then: it returns only after the thread has actually exited
        assert not engine._worker.is_alive()

    def test_stop_is_idempotent(self, engine):
        """Given an already-stopped engine, When stop() is called again,
        Then it returns immediately without raising."""
        # Given: an already-stopped engine
        engine.start()
        engine.stop()
        # When: stop() is called again
        engine.stop()  # must not hang or raise
        # Then: it returns immediately without raising
        assert not engine._worker.is_alive()

    def test_stop_without_join_returns_immediately(self, ls_stub):
        """Given join=False, When stop() is called, Then it signals the
        worker but does not block waiting for it to exit."""
        # Given: a worker stuck inside a debounce sleep that only releases
        # when explicitly signalled — deterministic instead of racing a
        # fixed wall-clock delay against stop()'s own execution time.
        entered_sleep = threading.Event()
        blocked = threading.Event()

        def _stuck_sleep(_seconds):
            entered_sleep.set()
            blocked.wait(5)

        engine = ParseEngine(ls_stub, sleep_fn=_stuck_sleep)
        engine.start()
        engine.queue_event("reparse")
        try:
            assert entered_sleep.wait(2), (
                "worker never entered the debounce sleep"
            )
            # When: stop(join=False) is called
            engine.stop(join=False)
            # Then: it returns without waiting for the worker, which is
            # still blocked inside the debounce sleep
            assert engine._worker.is_alive()
        finally:
            blocked.set()
            engine._worker.join(timeout=2)

    def test_stop_logs_warning_when_worker_does_not_exit_in_time(
        self, ls_stub, caplog
    ):
        """Given a worker stuck past the join timeout, When stop() is
        called, Then it logs a warning and returns instead of hanging
        forever."""
        # Given: a worker stuck inside its debounce sleep
        blocked = threading.Event()
        entered_sleep = threading.Event()

        def _stuck_sleep(_seconds):
            entered_sleep.set()
            blocked.wait(5)

        engine = ParseEngine(ls_stub, sleep_fn=_stuck_sleep)
        engine.start()
        engine.queue_event("reparse")
        try:
            # Wait until the worker is actually stuck inside the debounce
            # sleep before calling stop() — otherwise stop() can race ahead
            # of the worker even reaching _run()'s loop and see it already
            # exited (_stop set before the worker's first check), which
            # would make this test vacuous rather than exercise the
            # "stuck worker" path.
            assert entered_sleep.wait(2)
            # When: stop() is called with a short timeout
            with caplog.at_level("WARNING"):
                engine.stop(timeout=0.05)
            # Then: it logs a warning instead of hanging forever
            assert "did not exit" in caplog.text
        finally:
            blocked.set()
            engine._worker.join(timeout=2)


# ---------------------------------------------------------------------------
# validate — empty-workspace no-op
# ---------------------------------------------------------------------------


# pylint: disable-next=too-few-public-methods  # single scenario; kept as its own class for grouping alongside the other validate() test classes
class TestValidateEmptyWorkspace:
    def test_no_active_contexts_is_a_silent_no_op(self, engine, ls_stub):
        """Given no scopes have ever been opened (ls_stub.store starts
        empty), When validate() runs, Then it returns immediately without
        touching diagnostics, logging, or scope bookkeeping — a workspace
        with nothing open must not do any parse work."""
        # Given: no scopes have ever been opened
        assert not ls_stub.store.snapshot_contexts()

        # When: validate() runs
        engine.validate()

        # Then: it returns immediately, touching nothing
        ls_stub.text_document_publish_diagnostics.assert_not_called()
        ls_stub.window_log_message.assert_not_called()
        assert engine._scope_signatures == {}

    def test_still_evicts_retryable_bazel_errors(self, engine, ls_stub):
        """A transient Bazel failure (server lock, timeout) should be
        retried on the very next parse cycle even when that cycle has no
        active contexts to validate — otherwise a workspace with nothing
        open yet would never get the chance to clear a stale transient
        failure before something is finally opened in it."""
        # Given: no scopes have ever been opened
        assert not ls_stub.store.snapshot_contexts()
        # When: validate() runs
        engine.validate()
        # Then: the retryable-error eviction still ran
        ls_stub.bazel_cache.evict_retryable_errors.assert_called_once()


# ---------------------------------------------------------------------------
# validate — per-scope diagnostic publish cycle
# ---------------------------------------------------------------------------


class TestValidateDiagnosticPublish:
    """Tests the publish logic using a mocked Vscode_Message_Handler /
    Vscode_Source_Manager, seeded into a real ContextStore scope."""

    SCOPE_ID = "file:///tmp/test"

    def _run_validate(self, engine, ls_stub, new_diagnostics: dict):
        """Patch the TRLC source manager so validate() uses *new_diagnostics*
        as its output without performing a real filesystem parse."""
        vmh_mock = MagicMock()
        vmh_mock.diagnostics = new_diagnostics
        vsm_mock = MagicMock()
        vsm_mock.stab = MagicMock()
        # Set all_files to include the URIs in new_diagnostics
        # (so scope_parsed_uris is populated and diagnostics get published)
        vsm_mock.all_files = {
            uri.replace("file://", ""): MagicMock()
            for uri in new_diagnostics.keys()
        }

        if not ls_stub.store.snapshot_contexts():
            uri_key = next(iter(new_diagnostics.keys()), "file://a")

            def factory():
                return ParseContext(
                    scope_id=self.SCOPE_ID,
                    result=ParseResult(vsm=vsm_mock),
                )

            ls_stub.store.add_file(
                self.SCOPE_ID, uri_key, "test content", factory
            )

        with (
            patch.object(engine, "_message_handler_factory", lambda: vmh_mock),
            patch.object(engine, "_vsm_factory", lambda *a, **kw: vsm_mock),
        ):
            engine.validate()

    def test_new_uri_diagnostics_are_published(self, engine, ls_stub):
        # Given: a scope whose parse cycle produces one error diagnostic
        pos = Position(line=0, character=0)
        diag = Diagnostic(
            range=Range(start=pos, end=pos),
            message="error",
            severity=DiagnosticSeverity.Error,
        )
        # When: validate() runs a parse cycle
        self._run_validate(engine, ls_stub, {"file://a": [diag]})
        # Then: the diagnostic is published
        ls_stub.text_document_publish_diagnostics.assert_called()

    def test_disappeared_uri_gets_empty_publish(self, engine, ls_stub):
        """A URI that had diagnostics last cycle but is no longer parsed
        this cycle (removed from the scope) gets an empty-list publish."""
        # Given: a first parse cycle where "file://gone" has an error
        pos = Position(line=0, character=0)
        diag = Diagnostic(
            range=Range(start=pos, end=pos),
            message="error",
            severity=DiagnosticSeverity.Error,
        )
        self._run_validate(engine, ls_stub, {"file://gone": [diag]})
        ls_stub.text_document_publish_diagnostics.reset_mock()

        # When: the scope's second parse cycle no longer includes that file
        # Second cycle: the scope now parses a different file only —
        # "file://gone" has disappeared entirely from this cycle's output.
        self._run_validate(engine, ls_stub, {"file://other": []})

        # Then: "file://gone" receives an empty-list publish, clearing its diagnostics
        calls = ls_stub.text_document_publish_diagnostics.call_args_list
        assert any(
            c.args[0].uri == "file://gone" and c.args[0].diagnostics == []
            for c in calls
        )

    def test_diagnostic_history_updated_after_validate(self, engine, ls_stub):
        # Given: a scope whose parse cycle produces one warning diagnostic
        pos = Position(line=0, character=0)
        diag = Diagnostic(
            range=Range(start=pos, end=pos),
            message="new",
            severity=DiagnosticSeverity.Warning,
        )
        # When: validate() runs a parse cycle
        self._run_validate(engine, ls_stub, {"file://b": [diag]})
        # Then: the context's diagnostics history reflects the new diagnostic
        context = ls_stub.store.get_context(self.SCOPE_ID)
        assert "file://b" in context.snapshot().diagnostics

    def test_unchanged_diagnostics_not_republished(self, engine, ls_stub):
        """If diagnostics did not change between two cycles, the second
        cycle's publish call is suppressed."""
        # Given: a first parse cycle that primes the context's diagnostics with an error
        pos = Position(line=0, character=0)
        diag = Diagnostic(
            range=Range(start=pos, end=pos),
            message="stable",
            severity=DiagnosticSeverity.Error,
        )
        # First cycle primes the context's diagnostics with this exact diagnostic.
        self._run_validate(engine, ls_stub, {"file://c": [diag]})
        ls_stub.text_document_publish_diagnostics.reset_mock()

        # When: a second cycle produces the identical diagnostic for the same uri
        # Second cycle: identical diagnostic for the same uri.
        self._run_validate(engine, ls_stub, {"file://c": [diag]})
        # Then: no publish is made, since nothing actually changed
        ls_stub.text_document_publish_diagnostics.assert_not_called()

    def test_no_active_contexts_is_a_no_op(self, engine, ls_stub):
        """validate() with zero active scopes must not touch fh or publish."""
        # Given: an engine with no active scopes
        # When: validate() runs
        engine.validate()
        # Then: nothing is published
        ls_stub.text_document_publish_diagnostics.assert_not_called()


# ---------------------------------------------------------------------------
# validate — BAZEL mode: BazelUnavailable skips the cycle, keeps last-good
# ---------------------------------------------------------------------------


class TestValidateBazelUnavailableSkipsCycle:
    """When BAZEL mode's discovery hits BazelUnavailable (no workspace, or
    the query failed — resolve_bazel_manager already showed the one
    popup), _build_vsm returns None and _validate_scope skips the scope
    entirely: no vsm.process(), no ParseResult swap, no publish — the
    previously-published ParseResult (if any) stays live untouched,
    instead of being replaced by a wrongly-empty one."""

    SCOPE_ID = "file:///tmp/bazeltest"

    def _seed_scope(self, ls_stub, prior_result):
        def factory():
            return ParseContext(scope_id=self.SCOPE_ID, result=prior_result)

        ls_stub.store.add_file(self.SCOPE_ID, "file://a", "content", factory)
        ls_stub.get_config_for_uri = MagicMock(
            return_value=ServerConfig(parse_mode=ParseMode.BAZEL)
        )

    def test_previous_parse_result_stays_published(self, engine, ls_stub):
        # Given: a scope with an already-published, non-empty ParseResult
        pos = Position(line=0, character=0)
        diag = Diagnostic(
            range=Range(start=pos, end=pos),
            message="last good",
            severity=DiagnosticSeverity.Error,
        )
        vsm_mock = MagicMock()
        vsm_mock.stab = MagicMock()
        prior_result = ParseResult(
            vsm=vsm_mock, diagnostics={"file://a": [diag]}
        )
        self._seed_scope(ls_stub, prior_result)

        # When: this cycle's BAZEL discovery hits BazelUnavailable
        with patch("server.parse_engine.get_scope_strategy") as mock_get:
            mock_strategy = MagicMock()
            mock_strategy.discover.side_effect = BazelUnavailable(
                "no workspace"
            )
            mock_get.return_value = mock_strategy
            engine.validate()

        # Then: no publish happened, and the context's ParseResult is the
        # exact same object — not a fresh, wrongly-empty replacement.
        ls_stub.text_document_publish_diagnostics.assert_not_called()
        context = ls_stub.store.get_context(self.SCOPE_ID)
        assert context.result is prior_result

    def test_signature_not_recorded_so_next_cycle_retries(
        self, engine, ls_stub
    ):
        """A skipped cycle must not mark the scope as up-to-date, or a
        later-fixed Bazel setup would never get re-discovered."""
        empty_vsm = MagicMock()
        empty_vsm.stab = MagicMock()
        self._seed_scope(ls_stub, ParseResult(vsm=empty_vsm))

        with patch("server.parse_engine.get_scope_strategy") as mock_get:
            mock_strategy = MagicMock()
            mock_strategy.discover.side_effect = BazelUnavailable("boom")
            mock_get.return_value = mock_strategy
            engine.validate()

        assert self.SCOPE_ID not in engine._scope_signatures


# ---------------------------------------------------------------------------
# validate — per-scope crash isolation
# ---------------------------------------------------------------------------


class TestValidateScopeIsolation:
    """A parse failure in one scope must not abort the whole cycle or starve
    other scopes of diagnostics (each scope is validated independently,
    inside its own try/except)."""

    def _seed_scope(self, ls_stub, scope_id, uri):
        def factory():
            return ParseContext(
                scope_id=scope_id, result=ParseResult(vsm=MagicMock())
            )

        ls_stub.store.add_file(scope_id, uri, "content", factory)

    def test_one_scope_raising_does_not_stop_other_scopes(
        self, engine, ls_stub
    ):
        """One scope's `_validate_scope` raising doesn't skip the others."""
        # Given: two active scopes
        self._seed_scope(ls_stub, "scope-a", "file://a/x.rsl")
        self._seed_scope(ls_stub, "scope-b", "file://b/x.rsl")

        calls = []

        def fake_validate_scope(scope_id, _context, _dirty_uris):
            calls.append(scope_id)
            if scope_id == "scope-a":
                raise RuntimeError("boom")

        # When: validate() runs and scope-a's _validate_scope raises
        with patch.object(
            engine, "_validate_scope", side_effect=fake_validate_scope
        ):
            engine.validate()  # must not raise

        # Then: both scopes were attempted, despite scope-a's failure
        assert set(calls) == {"scope-a", "scope-b"}

    def test_raising_scope_does_not_propagate_out_of_validate(
        self, engine, ls_stub
    ):
        """A single scope's failure never propagates out of validate()."""
        # Given/When/Then: A single scope's failure never propagates out of validate()
        self._seed_scope(ls_stub, "scope-a", "file://a/x.rsl")

        with patch.object(
            engine, "_validate_scope", side_effect=RuntimeError("boom")
        ):
            # Then: validate() itself must not raise
            engine.validate()


# ---------------------------------------------------------------------------
# _run — worker thread survives an unhandled exception in a parse cycle
# ---------------------------------------------------------------------------


class TestWorkerSurvivesUnhandledException:
    def test_worker_keeps_running_after_drain_and_validate_raises(
        self, ls_stub
    ):
        """If _drain_and_validate raises (a bug, not a scope-level failure),
        the worker thread must log and keep looping — not die silently and
        take all future parsing down with it."""
        # Given: a running worker whose _drain_and_validate is rigged to
        # raise on its first call and succeed on its second.
        # A zero-wait sleep_fn: this test only cares that a second cycle
        # runs after the first raises, not about debounce timing itself.
        engine = ParseEngine(ls_stub, sleep_fn=lambda _seconds: None)

        calls = []
        first_call_done = threading.Event()
        second_call_done = threading.Event()

        def fake_drain():
            calls.append(1)
            if len(calls) == 1:
                first_call_done.set()
                raise RuntimeError("simulated bug in parse cycle")
            second_call_done.set()

        with patch.object(
            engine, "_drain_and_validate", side_effect=fake_drain
        ):
            engine.start()
            try:
                # When: a first cycle raises
                engine.queue_event("reparse")
                assert first_call_done.wait(timeout=2)

                # Then: the worker thread is still alive afterwards...
                assert engine._worker.is_alive()

                # ...and a second cycle still runs normally.
                engine.queue_event("reparse")
                assert second_call_done.wait(timeout=2)
            finally:
                engine.stop()

        assert len(calls) == 2


# ---------------------------------------------------------------------------
# _validate_scope — unchanged-input reuse (skips the expensive process())
# ---------------------------------------------------------------------------


class _ControllableFakeVSM:
    """A fake VSM with a REAL (settable) `compute_input_signature()` dict —
    unlike the MagicMock-based fixtures above, whose auto-mocked
    compute_input_signature() returns a fresh unique MagicMock every call
    and so can never accidentally look "unchanged"."""

    process_calls = 0  # class-level: shared across every instance built

    def __init__(self, signature, all_files=None):
        self._signature = signature
        self.all_files = all_files or {}
        self.stab = MagicMock()

    def compute_input_signature(self):
        """Return the caller-supplied, settable signature."""
        return self._signature

    def process(self):
        """Record that a (re)parse happened for this VSM instance."""
        type(self).process_calls += 1

    # discover() (real, unmocked) calls these on whatever vsm _build_vsm
    # constructs — a MagicMock auto-stubs them, but this plain class needs
    # explicit no-ops.
    def register_include(self, _dir_name):
        """No-op: discover() calls this, this fake doesn't track includes."""

    def register_file(self, _file_name, _file_content=None, primary=True):
        """No-op: discover() calls this, this fake doesn't track files."""


class TestInputSignatureReuse:
    SCOPE_ID = "file:///tmp/reuse-test"

    def setup_method(self):
        """Reset the shared process_calls counter before each test."""
        _ControllableFakeVSM.process_calls = 0

    def _run_cycle(self, engine, ls_stub, signature):
        vmh_mock = MagicMock()
        vmh_mock.diagnostics = {}
        # all_files must be non-empty (and match the opened uri below) so
        # the dirty-uri intersection check in _validate_scope has something
        # to intersect against — an empty all_files would make every scope
        # look untouched regardless of dirty_uris.
        vsm = _ControllableFakeVSM(
            signature, all_files={"/a.rsl": MagicMock()}
        )

        if not ls_stub.store.snapshot_contexts():

            def factory():
                return ParseContext(
                    scope_id=self.SCOPE_ID, result=ParseResult(vsm=vsm)
                )

            ls_stub.store.add_file(
                self.SCOPE_ID, "file:///a.rsl", "content", factory
            )

        with (
            patch.object(engine, "_message_handler_factory", lambda: vmh_mock),
            patch.object(engine, "_vsm_factory", lambda *a, **kw: vsm),
        ):
            engine.validate()
        return vsm

    def test_unchanged_signature_skips_process_and_keeps_old_vsm_published(
        self, engine, ls_stub
    ):
        """An unchanged input signature skips process(), keeps old vsm live."""
        # Given: a first cycle that parses successfully
        same_signature = {"/a.rsl": ("disk", 123, 10)}
        first_vsm = self._run_cycle(engine, ls_stub, same_signature)
        assert _ControllableFakeVSM.process_calls == 1
        published_vsm_after_first = (
            ls_stub.store.get_context(self.SCOPE_ID).snapshot().vsm
        )
        assert published_vsm_after_first is first_vsm

        # When: a second cycle's discovery produces the identical signature
        second_vsm = self._run_cycle(engine, ls_stub, same_signature)

        # Then: process() was NOT called again for the unchanged input...
        assert _ControllableFakeVSM.process_calls == 1
        # ...and the previously-published (already-processed) vsm is still
        # the one active — the freshly-discovered-but-unprocessed second_vsm
        # is discarded, never published.
        published_vsm_after_second = (
            ls_stub.store.get_context(self.SCOPE_ID).snapshot().vsm
        )
        assert published_vsm_after_second is first_vsm
        assert published_vsm_after_second is not second_vsm

    def test_changed_signature_reparses_and_publishes_new_vsm(
        self, engine, ls_stub
    ):
        """A changed input signature triggers reparse and publishes the new vsm."""
        # Given: a first cycle
        first_vsm = self._run_cycle(
            engine, ls_stub, {"/a.rsl": ("disk", 123, 10)}
        )
        assert _ControllableFakeVSM.process_calls == 1

        # When: a second cycle's discovery produces a DIFFERENT signature
        # (e.g. the file's mtime/size changed)
        second_vsm = self._run_cycle(
            engine, ls_stub, {"/a.rsl": ("disk", 456, 11)}
        )

        # Then: process() runs again, and the new vsm is published
        assert _ControllableFakeVSM.process_calls == 2
        published_vsm = ls_stub.store.get_context(self.SCOPE_ID).snapshot().vsm
        assert published_vsm is second_vsm
        assert published_vsm is not first_vsm

    def test_dirty_uri_forces_reparse_even_with_unchanged_signature(
        self, engine, ls_stub
    ):
        """An explicitly-edited file (queued via a "change" event) must
        still trigger a real reparse even if, by coincidence, the computed
        signature looks unchanged (e.g. same content hash after an undo)."""
        # Given: a scope already parsed once with a known signature
        same_signature = {"/a.rsl": ("mem", 42)}
        self._run_cycle(engine, ls_stub, same_signature)
        assert _ControllableFakeVSM.process_calls == 1

        # When: the scope's file is marked dirty via the real event-queue
        # path (drained so engine._dirty_uris is populated) and validated
        # again with the SAME (unchanged) signature.
        # Must be uri_from_file of the same path all_files is keyed by
        # ("/a.rsl" below): _validate_scope's dirty check intersects
        # dirty_uris against {uri_from_file(p) for p in vsm.all_files}, and on
        # Windows uri_from_file("/a.rsl") is "file:///c:/a.rsl" (abspath adds
        # the drive) — a hardcoded "file:///a.rsl" would never intersect, so
        # the scope would look untouched and the forced reparse wouldn't fire.
        engine.queue_event("change", uri_from_file("/a.rsl"), "content")
        with engine._queue_lock:
            while engine._queue:
                action, uri, content = engine._queue.pop()
                if action == "change":
                    ls_stub.fh.update_files(uri, content)
                    engine._dirty_uris.add(uri)

        self._run_cycle(engine, ls_stub, same_signature)

        # Then: process() runs again despite the unchanged signature
        assert _ControllableFakeVSM.process_calls == 2
