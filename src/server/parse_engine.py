# *******************************************************************************
# TRLC VSCode Extension
# Copyright (C) 2023 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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

"""Background parse engine for the TRLC language server.

:class:`ParseEngine` owns the debounce thread, the event queue, and the
full TRLC parse + diagnostic publish cycle. Per-scope state (contexts,
config) lives on :class:`~server.context_store.ContextStore`
(``ls.store``); this class only holds its own private queue/debounce
bookkeeping.
"""

import collections
import logging
import threading
import time
from typing import TYPE_CHECKING, Callable

from lsprotocol.types import (
    LogMessageParams,
    MessageType,
    PublishDiagnosticsParams,
)

from .bazel import BazelUnavailable
from .message_handler import Vscode_Message_Handler
from .parse_context import ParseContext, ParseResult
from .scope_strategies import get_scope_strategy
from .server_config import ParseMode
from .source_manager import Vscode_Source_Manager
from .token_utils import uri_from_file

if TYPE_CHECKING:
    from .server_protocol import ServerProtocol

LOGGER = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0.3
#: Upper bound for stop()'s join(); a hung worker logs a warning rather than
#: blocking process exit forever.
_STOP_JOIN_TIMEOUT_SECONDS = 5.0


def _diagnostics_changed(prev: list, current: list) -> bool:
    """Return ``True`` if *prev* and *current* differ in any user-visible way.

    Comparison is order-independent (set-based) so re-ordering between parse
    runs does not cause spurious re-publishes.
    """

    def _key(d):
        return (
            d.message,
            d.range.start.line,
            d.range.start.character,
            d.severity,
        )

    return {_key(d) for d in prev} != {_key(d) for d in current}


class ParseEngine:  # pylint: disable=too-many-instance-attributes  # one thread + its queue/sync primitives is genuinely this much state
    """Debounce loop + TRLC parse cycle.

    The engine starts a single long-lived daemon thread (``start()``) that
    waits on a :class:`threading.Event`, applies a debounce delay, drains
    the event queue, and then runs the full parse + diagnostic publish cycle.

    Call :meth:`stop` to signal the worker thread to exit cleanly.
    """

    # One param per constructor input, plus the two injectable factories —
    # defaults preserve the exact prior behavior (direct construction);
    # tests can substitute doubles instead of the real VSM/message handler.
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        ls: "ServerProtocol",
        debounce_seconds: float = DEBOUNCE_SECONDS,
        sleep_fn: Callable[[float], None] = time.sleep,
        message_handler_factory: Callable[[], Vscode_Message_Handler] = (
            Vscode_Message_Handler
        ),
        vsm_factory: Callable[..., Vscode_Source_Manager] = (
            Vscode_Source_Manager
        ),
    ):
        self._ls = ls
        self._debounce_seconds = debounce_seconds
        # Injectable so tests can drive the debounce loop deterministically
        # (an event-based fake) instead of racing real wall-clock sleeps.
        self._sleep_fn = sleep_fn
        # Injectable so _build_vsm depends on these as abstractions rather
        # than constructing the concrete classes directly; defaults are the
        # real implementations, same as _sleep_fn's own default.
        self._message_handler_factory = message_handler_factory
        self._vsm_factory = vsm_factory

        self._queue: collections.deque = collections.deque()
        self._queue_lock: threading.Lock = threading.Lock()
        self._trigger: threading.Event = threading.Event()
        self._stop: threading.Event = threading.Event()
        self._dirty_uris: set = set()
        # scope_id -> last successfully-parsed input signature. Lets a scope
        # whose inputs are byte-identical to last cycle skip the expensive
        # re-parse (see _validate_scope). Only written after a successful
        # process()+swap, so a failed parse always retries next cycle.
        self._scope_signatures: dict = {}
        # Set at the end of every drain+validate iteration; tests wait on
        # this instead of sleeping to know a cycle has actually completed.
        self.cycle_complete: threading.Event = threading.Event()
        self._worker: threading.Thread = threading.Thread(
            target=self._run, name="TRLC Parser Thread", daemon=True
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background worker thread."""
        self._worker.start()

    def stop(
        self, join: bool = True, timeout: float = _STOP_JOIN_TIMEOUT_SECONDS
    ) -> None:
        """Signal the worker thread to exit, and (by default) wait for it.

        Waiting for the thread avoids the process tearing down mid-publish
        (a killed daemon thread can leave diagnostics half-published or a
        parse cycle half-run). If the worker doesn't exit within *timeout*,
        a warning is logged and this returns anyway — shutdown must not
        hang forever on a stuck worker.
        """
        self._stop.set()
        self._trigger.set()  # wake it up so it notices _stop immediately
        if join and self._worker.is_alive():
            self._worker.join(timeout)
            if self._worker.is_alive():
                LOGGER.warning(
                    "TRLC parser thread did not exit within %.1fs of stop()",
                    timeout,
                )

    def queue_event(self, kind: str, uri=None, content=None) -> None:
        """Enqueue a parse event and wake the worker thread.

        *kind* is one of ``"change"``, ``"delete"``, ``"reparse"``, or
        ``"reparse_files"``.
        """
        with self._queue_lock:
            self._queue.appendleft((kind, uri, content))
            self._trigger.set()

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            self._trigger.wait()
            if self._stop.is_set():
                return
            self._trigger.clear()
            # Debounce: let rapid keystrokes settle before parsing.
            while True:
                self._sleep_fn(self._debounce_seconds)
                if self._stop.is_set():
                    return
                if not self._trigger.is_set():
                    break
                self._trigger.clear()
            try:
                self._drain_and_validate()
            except Exception:  # pylint: disable=broad-exception-caught
                # The worker thread must never die: a crash here would
                # silently stop ALL parsing for the rest of the session with
                # no diagnostics and no recovery. Log and keep looping.
                LOGGER.exception("Unhandled error in parse cycle; continuing")
            finally:
                # Signalled every iteration (success or failure) so tests
                # can wait for "a cycle ran" deterministically instead of
                # sleeping and hoping.
                self.cycle_complete.set()

    def _drain_and_validate(self) -> None:
        """Drain the event queue, update File_Handler, then run validate()."""
        while True:
            with self._queue_lock:
                if not self._queue:
                    return
                while self._queue:
                    action, uri, content = self._queue.pop()
                    if action == "change":
                        self._ls.fh.update_files(uri, content)
                        if uri:
                            self._dirty_uris.add(uri)
                    elif action == "reparse":
                        # Full reparse: mark all currently known files dirty
                        # so every open file gets a fresh publishDiagnostics.
                        self._dirty_uris.update(self._ls.fh.snapshot().keys())
                        # Config / workspace-folder changes invalidate any
                        # cached Bazel query result so the next parse re-runs
                        # `bazel query` against the new state.
                        self._ls.bazel_cache.clear()
                    elif action == "reparse_files":
                        # A disk-level file change reported by the client's
                        # file watcher (see lifecycle.on_watched_files_change)
                        # that doesn't affect the Bazel target graph itself —
                        # deliberately a no-op beyond waking the worker.
                        # discover() re-walks/re-registers files fresh every
                        # cycle regardless, and _validate_scope's own input
                        # signature comparison already detects the changed
                        # file's new mtime/content and reparses accordingly;
                        # forcing a bazel_cache clear here would only add an
                        # unnecessary full `bazel query` re-run.
                        pass
                    else:
                        self._ls.fh.delete_files(uri)
                        self._dirty_uris.discard(uri)
            self.validate()

    # ------------------------------------------------------------------
    # Parse + publish
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Run per-scope TRLC parse and publish diagnostics.

        Iterates all active contexts (one per scope) and validates each
        independently. Scopes are created on-demand by did_open and destroyed
        when all files in the scope close.
        """
        # Retry any transient Bazel failure (timeout, server lock, ...)
        # fresh on every cycle — cheap (a dict scan, no I/O) — while a
        # structural one (no Bazel workspace here, a real query/config
        # error) stays negative-cached until an explicit bazel_cache.clear()
        # (config/workspace-folder change, see the "reparse" branch above).
        self._ls.bazel_cache.evict_retryable_errors()

        # Locked snapshot: contexts may be added/removed concurrently by
        # did_open/did_close on the main thread while this iterates.
        contexts_to_validate = self._ls.store.snapshot_contexts()
        if not contexts_to_validate:
            # No active contexts — nothing to parse
            return

        # Drop signatures for scopes that no longer exist, so a scope_id that
        # is destroyed and later recreated never reuses a stale signature.
        self._scope_signatures = {
            scope_id: sig
            for scope_id, sig in self._scope_signatures.items()
            if scope_id in contexts_to_validate
        }

        # Snapshot dirty set before building diagnostics. No lock: _dirty_uris
        # (like _scope_signatures above) is worker-thread-private — only this
        # thread's _drain_and_validate/validate ever touch it, since
        # LanguageServer deliberately has no main-thread validate() wrapper
        # (see docs/ARCHITECTURE.md, Concurrency). _queue_lock guards only
        # _queue itself (see queue_event/_drain_and_validate).
        dirty_uris = self._dirty_uris.copy()
        self._dirty_uris.clear()

        for scope_id, context in contexts_to_validate.items():
            try:
                self._validate_scope(scope_id, context, dirty_uris)
            except Exception:  # pylint: disable=broad-exception-caught
                # Isolate scopes: a parse failure in one scope must not abort
                # the whole cycle or starve other scopes of diagnostics.
                LOGGER.exception(
                    "Failed to validate scope %s; skipping", scope_id
                )

        self._ls.window_log_message(
            LogMessageParams(
                type=MessageType.Log, message="TRLC: Diagnostics published"
            )
        )

    def _create_vsm(self, sample_uri: str, context: ParseContext):
        """Create a fresh (message handler, VSM, config) triple for
        *sample_uri*'s current configuration — not yet registered with any
        files. Uses the injected factories rather than constructing the
        concrete classes directly (see :meth:`__init__`)."""
        ls = self._ls
        config = ls.get_config_for_uri(sample_uri)
        vmh = self._message_handler_factory()
        vsm = self._vsm_factory(
            vmh,
            ls.fh,
            ls,
            verify_mode=context.parse_mode == ParseMode.WORKSPACE,
            exclude_patterns=getattr(config, "exclude_patterns", []),
        )
        return vmh, vsm, config

    def _build_vsm(
        self, scope_id: str, sample_uri: str, context: ParseContext
    ):
        """Create a fresh VSM for *scope_id* and run file discovery.

        Returns ``(vmh, vsm, fallback_reason)`` with all inputs registered
        but **not** yet parsed — the caller decides whether ``vsm.process()``
        is needed based on the input signature. ``fallback_reason`` is
        whatever :meth:`~server.scope_strategies.ScopeStrategy.discover`
        returned — non-``None`` only when BAZEL mode fell back to a
        different strategy this cycle (see that method's own docstring).

        Returns ``None`` instead when BAZEL mode's discovery hit
        :class:`~server.bazel.BazelUnavailable` (no workspace
        found, or the query failed) — the caller must skip this scope's
        cycle entirely rather than swap in a wrongly-empty parse; see
        :meth:`_validate_scope`.
        """
        ls = self._ls
        vmh, vsm, config = self._create_vsm(sample_uri, context)
        strategy = get_scope_strategy(config.parse_mode)
        scope_root = strategy.scope_root(scope_id, sample_uri)
        try:
            fallback_reason = strategy.discover(
                vsm, scope_root, config, ls, scope_id
            )
        except BazelUnavailable:
            return None
        return vmh, vsm, fallback_reason

    # One careful sequential atomic-swap + diagnostic-diff pass; splitting
    # it risks the ordering the comments below call out as load-bearing.
    # pylint: disable-next=too-many-locals
    def _validate_scope(
        self, scope_id: str, context: ParseContext, dirty_uris: set
    ) -> None:
        """Validate a single scope and publish its diagnostics."""
        ls = self._ls

        # context.open_files (uri -> content) is mutated by did_open/did_close
        # on the main thread; sample_uri() takes the store's lock internally.
        sample_uri = ls.store.sample_uri(context)

        built = self._build_vsm(scope_id, sample_uri, context)
        if built is None:
            # BAZEL mode's discovery hit BazelUnavailable this cycle (see
            # _build_vsm) — the popup already fired; skip this scope
            # entirely so its previously-published ParseResult (if any)
            # stays live instead of being swapped for an empty one.
            return
        vmh, vsm, fallback_reason = built

        # Skip the expensive parse when this scope's inputs are byte-identical
        # to the last successful parse AND no explicitly-edited file belongs to
        # it. Discovery already ran (cheap: dir walk + open-file reads); only
        # process() (parse + semantic analysis + lint) is avoided. The prior
        # ParseResult stays published, so handlers keep a valid symbol table.
        signature = vsm.compute_input_signature()
        prev_signature = self._scope_signatures.get(scope_id)
        scope_input_uris = {uri_from_file(path) for path in vsm.all_files}
        scope_touched = bool(dirty_uris & scope_input_uris)
        if (
            prev_signature is not None
            and prev_signature == signature
            and not scope_touched
        ):
            return

        vsm.process()

        new_all_files = {
            key.replace("\\", "/"): value
            for key, value in vsm.all_files.items()
        }

        # URIs parsed in this scope (all receive publishDiagnostics)
        scope_parsed_uris = {uri_from_file(path) for path in new_all_files}

        # Build new diagnostic state for this scope
        new_diagnostic_state: dict = {
            uri: vmh.diagnostics.get(uri, []) for uri in scope_parsed_uris
        }
        new_diagnostic_state.update(vmh.diagnostics)

        # Snapshot previous diagnostics before overwriting, to skip
        # publishing URIs whose diagnostics didn't actually change.
        prev_diagnostic_state = context.snapshot().diagnostics

        # Update context: symbol table + diagnostics as a single wholesale
        # swap (never mutated in place, and published as ONE ParseResult
        # reference rather than two separate field assignments) so handler
        # reads on other threads never observe a half-built vsm or a torn
        # vsm/diagnostics pair. See parse_context.ParseResult.
        context.result = ParseResult(
            vsm=vsm,
            diagnostics=new_diagnostic_state,
            fallback_reason=fallback_reason,
        )

        # Record the signature only after a successful process()+swap, so a
        # parse that raised (and was skipped by the per-scope guard) always
        # retries next cycle instead of being wrongly treated as up-to-date.
        self._scope_signatures[scope_id] = signature

        # URIs that had diagnostics last cycle but are no longer produced
        # this cycle (file removed/renamed within a scope that otherwise
        # persists) — clear them so the editor doesn't keep stale squiggles.
        for uri in set(prev_diagnostic_state) - set(new_diagnostic_state):
            ls.text_document_publish_diagnostics(
                PublishDiagnosticsParams(uri=uri, diagnostics=[])
            )

        # Publish every currently-known URI (not just scope_parsed_uris —
        # vmh.diagnostics can carry entries for files that error out before
        # they make it into vsm.all_files) when its diagnostics changed, when
        # it's new (prev is None), or when it was explicitly edited this
        # cycle (dirty_uris) — an unsaved edit that leaves diagnostics
        # unchanged (e.g. close-then-reopen of a clean file) still gets a
        # fresh publish so the client's view can't go stale.
        for uri, diagnostics in new_diagnostic_state.items():
            prev = prev_diagnostic_state.get(uri)
            if (
                prev is None
                or _diagnostics_changed(prev, diagnostics)
                or uri in dirty_uris
            ):
                ls.text_document_publish_diagnostics(
                    PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics)
                )
