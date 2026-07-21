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
"""Unit tests for server.context_store.ContextStore.

Covers the locked accessors/mutators themselves, and — since ContextStore is
the fix for the concurrency findings — dedicated regression tests that hammer
it from two real threads to prove the races it was built to close (dict
iteration during concurrent mutation, and torn vsm/diagnostics reads) are
actually closed.
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

import threading

from server.context_store import ContextStore
from server.parse_context import ParseContext, ParseResult
from server.server_config import ParseMode, ServerConfig


def _context(scope_id="scope-1", parse_mode=ParseMode.WORKSPACE):
    return ParseContext(
        scope_id=scope_id,
        result=ParseResult(vsm=object()),
        parse_mode=parse_mode,
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_default_config_is_a_server_config(self):
        # Given: a fresh store
        store = ContextStore()
        # When/Then: the default config is a ServerConfig instance
        assert isinstance(store.default_config, ServerConfig)

    def test_set_default_config_replaces_it(self):
        # Given: a fresh store and a new config
        store = ContextStore()
        new_cfg = ServerConfig(parse_mode=ParseMode.REPO)
        # When: the default config is set
        store.set_default_config(new_cfg)
        # Then: the store's default config is the new one
        assert store.default_config is new_cfg

    def test_get_config_for_uri_falls_back_to_default(self):
        # Given: a store with no folder-specific config
        store = ContextStore()
        # When/Then: looking up any uri returns the default config
        assert (
            store.get_config_for_uri("file:///no/such/folder")
            is store.default_config
        )

    def test_get_config_for_uri_prefers_folder_override(self):
        # Given: a store with a folder-specific config
        store = ContextStore()
        folder_cfg = ServerConfig(parse_mode=ParseMode.DIRECTORY)
        store.set_folder_config("file:///ws", folder_cfg)
        # When/Then: looking up that folder's uri returns its override
        assert store.get_config_for_uri("file:///ws") is folder_cfg

    def test_has_folder_config(self):
        # Given: a fresh store
        store = ContextStore()
        # Then: no folder config exists yet
        assert not store.has_folder_config("file:///ws")
        # When: a folder config is set
        store.set_folder_config("file:///ws", ServerConfig())
        # Then: it is now reported as present
        assert store.has_folder_config("file:///ws")

    def test_folder_config_items_returns_all_pairs(self):
        # Given: a store with two folder-specific configs
        store = ContextStore()
        store.set_folder_config("file:///a", ServerConfig())
        store.set_folder_config("file:///b", ServerConfig())
        # When: all folder config items are requested
        pairs = dict(store.folder_config_items())
        # Then: both folder uris are present
        assert set(pairs.keys()) == {"file:///a", "file:///b"}

    def test_folder_config_items_is_a_snapshot(self):
        """Mutating the returned list must not affect the store."""
        # Given: a store with one folder-specific config
        store = ContextStore()
        store.set_folder_config("file:///a", ServerConfig())
        # When: the returned snapshot is mutated
        items = store.folder_config_items()
        items.append(("file:///bogus", ServerConfig()))
        # Then: the store itself is unaffected
        assert not store.has_folder_config("file:///bogus")


# ---------------------------------------------------------------------------
# Contexts
# ---------------------------------------------------------------------------


class TestContexts:
    def test_get_context_missing_returns_none(self):
        # Given: a fresh, empty store
        store = ContextStore()
        # When/Then: looking up a scope_id that was never created returns None
        assert store.get_context("nope") is None

    def test_get_context_none_scope_id_returns_none(self):
        # Given: a fresh store
        store = ContextStore()
        # When/Then: None or empty-string scope_ids return None, not an error
        assert store.get_context(None) is None
        assert store.get_context("") is None

    def test_add_file_creates_context_via_factory(self):
        # Given: a fresh store and a factory that records its own calls
        store = ContextStore()
        calls = []

        def factory():
            calls.append(1)
            return _context()

        # When: a file is added to a new scope
        store.add_file("scope-1", "file:///a.rsl", "content", factory)
        # Then: the factory ran once and created the context
        assert store.get_context("scope-1") is not None
        assert len(calls) == 1

    def test_add_file_second_call_same_scope_does_not_recreate_context(self):
        # Given: a factory that records every context it creates
        store = ContextStore()
        created = []

        def factory():
            ctx = _context()
            created.append(ctx)
            return ctx

        # When: two files are added to the same scope
        store.add_file("scope-1", "file:///a.rsl", "a", factory)
        store.add_file("scope-1", "file:///b.rsl", "b", factory)
        # Then: the factory only ran once, and both files share the context
        assert len(created) == 1
        context = store.get_context("scope-1")
        assert set(context.open_files) == {"file:///a.rsl", "file:///b.rsl"}

    def test_get_context_for_uri_resolves_via_open_time_scope(self):
        # Given: a file added to a known scope
        store = ContextStore()
        store.add_file("scope-1", "file:///a.rsl", "a", _context)
        # When: the context is looked up by uri
        context = store.get_context_for_uri("file:///a.rsl")
        # Then: it resolves to the scope the file was added under
        assert context is not None
        assert context.scope_id == "scope-1"

    def test_get_context_for_uri_unknown_uri_returns_none(self):
        # Given: a fresh, empty store
        store = ContextStore()
        # When/Then: looking up a uri that was never added returns None
        assert store.get_context_for_uri("file:///never-opened.rsl") is None

    def test_scope_id_for_uri_returns_open_time_binding(self):
        # Given: a file added under a known scope_id
        store = ContextStore()
        store.add_file("scope-1", "file:///a.rsl", "a", _context)
        # When/Then: scope_id_for_uri returns that binding
        assert store.scope_id_for_uri("file:///a.rsl") == "scope-1"

    def test_scope_id_for_uri_unknown_uri_returns_none(self):
        # Given: a fresh, empty store
        store = ContextStore()
        # When/Then: a uri that was never added has no binding
        assert store.scope_id_for_uri("file:///never-opened.rsl") is None

    def test_scope_id_for_uri_after_remove_file_returns_none(self):
        # Given: a file added then removed
        store = ContextStore()
        store.add_file("scope-1", "file:///a.rsl", "a", _context)
        store.remove_file("file:///a.rsl")
        # When/Then: the binding no longer exists
        assert store.scope_id_for_uri("file:///a.rsl") is None

    def test_snapshot_contexts_is_a_copy(self):
        # Given: a store with one active context
        store = ContextStore()
        store.add_file("scope-1", "file:///a.rsl", "a", _context)
        # When: the snapshot is mutated
        snap = store.snapshot_contexts()
        snap["bogus"] = _context("bogus")
        # Then: the store itself is unaffected
        assert store.get_context("bogus") is None

    def test_remove_file_removes_uri_from_open_files(self):
        # Given: two files open in the same scope
        store = ContextStore()
        store.add_file("scope-1", "file:///a.rsl", "a", _context)
        store.add_file("scope-1", "file:///b.rsl", "b", _context)
        # When: one of the two files is removed
        store.remove_file("file:///a.rsl")
        # Then: the context survives, now holding only the other file
        context = store.get_context("scope-1")
        assert context is not None
        assert "file:///a.rsl" not in context.open_files
        assert "file:///b.rsl" in context.open_files

    def test_remove_last_file_destroys_context(self):
        # Given: a single open file in a scope
        store = ContextStore()
        store.add_file("scope-1", "file:///a.rsl", "a", _context)
        # When: that file is removed
        store.remove_file("file:///a.rsl")
        # Then: the now-empty context is destroyed
        assert store.get_context("scope-1") is None

    def test_remove_file_unknown_uri_is_a_no_op(self):
        # Given: a fresh, empty store
        store = ContextStore()
        # When/Then: removing a uri that was never added must not raise
        store.remove_file("file:///never-opened.rsl")  # must not raise

    def test_remove_file_uses_open_time_scope_even_if_config_changed(self):
        """Mirrors did_close's contract: removal must use the scope_id
        recorded at add_file time, not one re-derived from current state —
        a scope-store consumer has no way to re-derive it anyway since
        ContextStore holds no scope-resolution logic itself."""
        # Given: a file added under a specific scope_id
        store = ContextStore()
        store.add_file("scope-A", "file:///a.rsl", "a", _context)
        # When: the file is removed (no re-derivation API exists — remove_file
        # must still find it via the uri_scope_map recorded when it was added)
        store.remove_file("file:///a.rsl")
        # Then: the original scope's context is destroyed
        assert store.get_context("scope-A") is None

    def test_sample_uri_returns_an_open_uri(self):
        # Given: a context with one open file
        store = ContextStore()
        store.add_file("scope-1", "file:///a.rsl", "a", _context)
        context = store.get_context("scope-1")
        # When/Then: sample_uri returns that open file's uri
        assert store.sample_uri(context) == "file:///a.rsl"

    def test_sample_uri_empty_context_returns_empty_string(self):
        # Given: a context with no open files
        store = ContextStore()
        # When/Then: sample_uri returns an empty string, not an error
        assert store.sample_uri(_context()) == ""


# ---------------------------------------------------------------------------
# Concurrency regressions
# ---------------------------------------------------------------------------


#: Iterations each writer thread performs in the stress tests below. A
#: fixed op count (rather than a fixed wall-clock window) keeps the test's
#: race-detection power constant across machines: a slow CI runner still
#: completes all WRITE_ITERATIONS mutations (just takes longer), instead of
#: a fixed time budget silently shrinking how many mutations a slow run
#: manages to interleave with the reader.
_WRITE_ITERATIONS = 2000


class TestConcurrency:
    """Two real threads hammering the store — proves the races the
    ContextStore/ParseResult design was introduced to close are actually
    closed, not just theoretically avoided."""

    def test_snapshot_never_raises_during_concurrent_mutation(self):
        """Regression for the pre-fix bug: dict(self._ls.contexts) with no
        lock could raise 'dictionary changed size during iteration' if
        did_open/did_close mutated contexts mid-snapshot."""
        # Given: a store, plus writer threads that add/remove files a fixed
        # number of times, and a reader thread that snapshots contexts for
        # as long as any writer is still running
        store = ContextStore()
        errors = []

        def writer():
            for i in range(_WRITE_ITERATIONS):
                uri = f"file:///w{i % 20}.rsl"
                store.add_file(f"scope-{i % 20}", uri, "x", _context)
                store.remove_file(uri)

        def reader(stop: threading.Event):
            try:
                while not stop.is_set():
                    snap = store.snapshot_contexts()
                    # Must always be usable as a plain dict.
                    list(snap.items())
            except RuntimeError as e:  # pragma: no cover - failure path
                errors.append(e)

        # When: writers run to completion while the reader keeps sampling
        # until every writer has finished
        stop = threading.Event()
        reader_thread = threading.Thread(target=reader, args=(stop,))
        writers = [threading.Thread(target=writer) for _ in range(2)]
        reader_thread.start()
        for t in writers:
            t.start()
        for t in writers:
            t.join(timeout=10)
        stop.set()
        reader_thread.join(timeout=2)

        # Then: snapshot_contexts never raised during concurrent mutation
        assert not errors, f"snapshot_contexts raised: {errors}"

    def test_get_context_for_uri_never_raises_during_concurrent_mutation(self):
        # Given: a store, plus a writer thread that adds/removes files a
        # fixed number of times, and a reader thread that resolves uris for
        # as long as the writer is still running
        store = ContextStore()
        errors = []

        def writer():
            for i in range(_WRITE_ITERATIONS):
                uri = f"file:///w{i % 20}.rsl"
                store.add_file(f"scope-{i % 20}", uri, "x", _context)
                store.remove_file(uri)

        def reader(stop: threading.Event):
            try:
                while not stop.is_set():
                    for i in range(20):
                        store.get_context_for_uri(f"file:///w{i}.rsl")
            except RuntimeError as e:  # pragma: no cover - failure path
                errors.append(e)

        # When: the writer runs to completion while the reader keeps
        # resolving uris until it finishes
        stop = threading.Event()
        writer_thread = threading.Thread(target=writer)
        reader_thread = threading.Thread(target=reader, args=(stop,))
        reader_thread.start()
        writer_thread.start()
        writer_thread.join(timeout=10)
        stop.set()
        reader_thread.join(timeout=2)

        # Then: get_context_for_uri never raised during concurrent mutation
        assert not errors

    def test_concurrent_result_swap_is_never_torn(self):
        """A reader that captures context.result exactly once must always
        see a matched (vsm, diagnostics) pair, even while a writer thread
        publishes a fixed-size stream of new ParseResults — the property
        the ParseEngine relies on to avoid locking every handler read."""
        # Given: a context, plus a writer thread that publishes a fixed
        # number of new (vsm, diagnostics) pairs designed so the two must
        # always match
        store = ContextStore()
        store.add_file("scope-1", "file:///a.rsl", "a", _context)
        context = store.get_context("scope-1")
        # Seed a result that already satisfies the reader's invariant, so a
        # reader that wins the race to read before the writer's first
        # iteration lands sees a matched pair too — not a false-positive
        # "torn read" that's really just "read before any write happened".
        context.result = ParseResult(vsm=-1, diagnostics={"n": -1})

        mismatches = []

        def writer():
            for n in range(_WRITE_ITERATIONS):
                context.result = ParseResult(vsm=n, diagnostics={"n": n})

        def reader(stop: threading.Event):
            while not stop.is_set():
                result = context.result  # single capture
                if result.diagnostics.get("n") != result.vsm:
                    mismatches.append(result)

        # When: the writer publishes to completion while the reader samples
        # a single result reference per iteration until it finishes
        stop = threading.Event()
        writer_thread = threading.Thread(target=writer)
        reader_thread = threading.Thread(target=reader, args=(stop,))
        reader_thread.start()
        writer_thread.start()
        writer_thread.join(timeout=10)
        stop.set()
        reader_thread.join(timeout=2)

        # Then: no reader ever observed a torn (mismatched) vsm/diagnostics pair
        assert not mismatches, (
            f"observed a torn ParseResult (vsm/diagnostics out of sync): "
            f"{mismatches[:5]}"
        )
