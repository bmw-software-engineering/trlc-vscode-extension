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
"""Unit tests for lifecycle handlers: did_open / did_close / cmd_parse_all.

Exercises did_open/did_close's real interaction with ContextStore — context
creation/destruction, scope_id tracking across parseMode changes, and the
config-fetch short-circuit — using the REAL (unbound)
``TrlcLanguageServer.uri_to_scope_id`` scope-resolution logic against a
lightweight stub of everything else (no live pygls transport needed).
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

# pylint: disable=protected-access  # tests legitimately access private members to verify internal state

# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest
from lsprotocol.types import (
    SHUTDOWN,
    TEXT_DOCUMENT_DID_CLOSE,
    TEXT_DOCUMENT_DID_OPEN,
    WORKSPACE_DID_CHANGE_CONFIGURATION,
    WORKSPACE_DID_CHANGE_WATCHED_FILES,
    DidChangeConfigurationParams,
    DidChangeWatchedFilesParams,
    DidCloseTextDocumentParams,
    DidOpenTextDocumentParams,
    FileChangeType,
    FileEvent,
    TextDocumentIdentifier,
    TextDocumentItem,
)

from server.context_store import ContextStore
from server.file_handler import File_Handler
from server.handlers import lifecycle as lifecycle_mod
from server.language_server import TrlcLanguageServer
from server.server_config import ParseMode, ServerConfig
from server.token_utils import folder_for_uri

from .conftest import HandlerRegistryMixin

FOLDER_URI = "file:///workspace"


def _make_workspace(sources: Optional[Dict[str, str]] = None):
    ws = MagicMock()
    ws.folders = {FOLDER_URI: "workspace"}
    sources = dict(sources or {})

    def get_text_document(uri):
        doc = MagicMock()
        doc.source = sources.get(uri, "")
        return doc

    ws.get_text_document.side_effect = get_text_document
    return ws


@dataclass
# pylint: disable-next=too-many-instance-attributes  # stub replicates full server state
class LifecycleTestServer(HandlerRegistryMixin):
    """Minimal server double: a real ContextStore plus the REAL (unbound)
    scope-resolution method from TrlcLanguageServer, so these tests exercise
    the same logic production uses, not a re-implementation of it."""

    workspace: Any
    store: ContextStore = field(default_factory=ContextStore)
    fh: File_Handler = field(default_factory=File_Handler)
    work_done_progress: Any = field(default_factory=MagicMock)
    CONFIGURATION_SECTION: str = "trlcServer"  # pylint: disable=invalid-name  # ALL_CAPS class constant
    _events: list = field(default_factory=list)
    _msgs: list = field(default_factory=list)
    _logs: list = field(default_factory=list)
    _handlers: dict = field(default_factory=dict)
    _configuration_response: list = field(default_factory=list)
    config_fetch_count: int = 0

    @property
    def config(self):
        """Config."""
        return self.store.default_config

    def queue_event(self, kind, uri=None, content=None):
        """Queue event."""
        self._events.append((kind, uri, content))

    def stop_engine(self):
        """Stop engine (records the call; no real ParseEngine in these tests)."""
        self._events.append(("stop_engine", None, None))

    def window_show_message(self, params):
        """Window show message."""
        self._msgs.append(params)

    def window_log_message(self, params):
        """Window log message."""
        self._logs.append(params)

    def apply_config(self, config, folder_uri=None):
        """Apply config."""
        if isinstance(config, dict):
            config = ServerConfig.from_dict(config)
        if folder_uri:
            self.store.set_folder_config(folder_uri, config)
        else:
            self.store.set_default_config(config)

    def get_config_for_uri(self, uri):
        """Get config for uri."""
        folder_uri = folder_for_uri(uri, self.workspace.folders)
        return self.store.get_config_for_uri(folder_uri)

    def uri_to_scope_id(self, uri):
        # Reuse the REAL scope-resolution logic (bound to this stub) so
        # these tests can't drift from what did_open actually calls.
        return TrlcLanguageServer.uri_to_scope_id(self, uri)

    def _resolve_bazel_target(self, _uri):
        return None

    async def workspace_configuration_async(self, _params):
        """Workspace configuration async."""
        self.config_fetch_count += 1
        return self._configuration_response


def _open_params(uri, content=""):
    return DidOpenTextDocumentParams(
        text_document=TextDocumentItem(
            uri=uri, language_id="trlc", version=1, text=content
        )
    )


def _close_params(uri):
    return DidCloseTextDocumentParams(
        text_document=TextDocumentIdentifier(uri=uri)
    )


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """did_open's config-fetch retry (see _fetch_folder_config) awaits
    asyncio.sleep between attempts; patched to instant so the many tests
    that incidentally hit the empty-config retry path don't pay real
    wall-clock delay."""

    async def _instant_sleep(_seconds):
        return None

    monkeypatch.setattr(lifecycle_mod.asyncio, "sleep", _instant_sleep)


@pytest.fixture
def server():
    """Server."""
    return LifecycleTestServer(workspace=_make_workspace())


@pytest.fixture
def handlers(server):
    """Handlers."""
    lifecycle_mod.register(server)
    return {
        "did_open": server.get_handler(TEXT_DOCUMENT_DID_OPEN),
        "did_close": server.get_handler(TEXT_DOCUMENT_DID_CLOSE),
        "shutdown": server.get_handler(SHUTDOWN),
        "watched_files": server.get_handler(
            WORKSPACE_DID_CHANGE_WATCHED_FILES
        ),
        "config_change": server.get_handler(
            WORKSPACE_DID_CHANGE_CONFIGURATION
        ),
    }


def _run(coro):
    """Run *coro* to completion, then drain any detached background tasks
    it scheduled (e.g. did_open's _schedule_config_refetch) within the same
    loop before closing it — asyncio.run() alone would cancel those before
    they ever got a chance to execute, since nothing else awaits them."""
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(coro)
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if pending:
            loop.run_until_complete(asyncio.gather(*pending))
        return result
    finally:
        loop.close()


class TestDidOpen:
    def test_creates_context_for_new_scope(self, server, handlers):
        # Given: a fresh server with no active scopes, and a new file to open
        uri = f"{FOLDER_URI}/a.rsl"
        server.workspace = _make_workspace({uri: "package A"})
        # When: the file is opened
        _run(handlers["did_open"](server, _open_params(uri, "package A")))

        # Then: a context is created for its scope, holding the open file
        context = server.store.get_context_for_uri(uri)
        assert context is not None
        assert uri in context.open_files
        assert context.open_files[uri] == "package A"

    def test_no_config_override_logs_defaults_without_a_warning_popup(
        self, server, handlers
    ):
        """Regression test: window_log_message must actually be callable
        on the server double, or the "no config override" did_open branch
        raises AttributeError, gets swallowed by the broad except Exception
        below it, and masquerades as a config-fetch failure warning popup
        instead of the intended quiet log-channel line."""
        # Given: the client has no override for this section, on every
        # attempt (default [])
        uri = f"{FOLDER_URI}/a.rsl"
        server.workspace = _make_workspace({uri: ""})

        # When: a file in that folder is opened (this also drains the one
        # detached background refetch did_open schedules - see _run)
        _run(handlers["did_open"](server, _open_params(uri)))

        # Then: exactly one "using hardcoded defaults" line reaches the log
        # channel, and no spurious warning popup is shown
        assert len(server._logs) == 1
        assert "using hardcoded defaults" in server._logs[0].message
        assert not server._msgs
        # The immediate fetch plus the one scheduled background retry
        assert server.config_fetch_count == 2

    def test_config_fetch_empty_response_self_heals_via_background_retry(
        self, server, handlers
    ):
        """A transient empty response (e.g. a startup race between the
        config request and VS Code resolving effective settings) is given
        one detached, delayed retry rather than being treated as a
        permanent "no override" — and that retry runs off did_open's
        critical path, not by blocking the file-open itself."""
        # Given: the client's first response is empty, but a later one has
        # a real override
        responses = iter([[], [{"parseMode": "repo"}]])

        async def _fetch_side_effect(_params):
            server.config_fetch_count += 1
            return next(responses)

        server.workspace_configuration_async = _fetch_side_effect
        uri = f"{FOLDER_URI}/a.rsl"
        server.workspace = _make_workspace({uri: ""})

        # When: the file is opened (did_open's own immediate fetch is
        # empty; _run then drains the scheduled background retry)
        _run(handlers["did_open"](server, _open_params(uri)))

        # Then: the background retry picked up the real override instead of
        # giving up after the first empty response
        assert server.config_fetch_count == 2
        assert (
            server.store.get_config_for_uri(FOLDER_URI).parse_mode
            == ParseMode.REPO
        )
        # A transient warning during the empty window is expected (did_open
        # can't know in advance that the retry will succeed) - the point is
        # that the *config* self-heals, not that the warning never appears
        assert any("hardcoded defaults" in log.message for log in server._logs)

    def test_two_files_same_workspace_scope_share_one_context(
        self, server, handlers
    ):
        # Given: two files under the same workspace folder (WORKSPACE mode, default)
        uri_a = f"{FOLDER_URI}/a.rsl"
        uri_b = f"{FOLDER_URI}/b.rsl"
        server.workspace = _make_workspace({uri_a: "a", uri_b: "b"})

        # When: both files are opened
        _run(handlers["did_open"](server, _open_params(uri_a, "a")))
        _run(handlers["did_open"](server, _open_params(uri_b, "b")))

        # Then: they share the same context
        ctx_a = server.store.get_context_for_uri(uri_a)
        ctx_b = server.store.get_context_for_uri(uri_b)
        assert ctx_a is ctx_b
        assert set(ctx_a.open_files) == {uri_a, uri_b}

    def test_two_directories_in_directory_mode_get_separate_scopes(
        self, server, handlers
    ):
        # Given: DIRECTORY parse mode, and two files in different directories
        server.apply_config(
            ServerConfig(parse_mode=ParseMode.DIRECTORY), FOLDER_URI
        )
        uri1 = f"{FOLDER_URI}/dir1/a.rsl"
        uri2 = f"{FOLDER_URI}/dir2/a.rsl"
        server.workspace = _make_workspace(
            {uri1: "package A", uri2: "package A"}
        )

        # When: both files are opened
        _run(handlers["did_open"](server, _open_params(uri1)))
        _run(handlers["did_open"](server, _open_params(uri2)))

        # Then: each directory gets its own isolated context/symbol table
        ctx1 = server.store.get_context_for_uri(uri1)
        ctx2 = server.store.get_context_for_uri(uri2)
        assert ctx1 is not None and ctx2 is not None
        assert ctx1.scope_id != ctx2.scope_id
        assert ctx1.snapshot().vsm is not ctx2.snapshot().vsm

    def test_queues_a_change_event_with_document_content(
        self, server, handlers
    ):
        # Given: a file to open
        uri = f"{FOLDER_URI}/a.rsl"
        server.workspace = _make_workspace({uri: "package A"})
        # When: the file is opened
        _run(handlers["did_open"](server, _open_params(uri)))
        # Then: a "change" event carrying its content is queued
        assert ("change", uri, "package A") in server._events

    def test_skips_config_fetch_when_folder_config_already_present(
        self, server, handlers
    ):
        # Given: a folder that already has a config applied
        server.apply_config(ServerConfig(), FOLDER_URI)
        uri = f"{FOLDER_URI}/a.rsl"
        server.workspace = _make_workspace({uri: ""})

        # When: a file in that folder is opened
        _run(handlers["did_open"](server, _open_params(uri)))

        # Then: no new configuration fetch is made
        assert server.config_fetch_count == 0

    def test_fetches_config_when_folder_config_missing(self, server, handlers):
        # Given: a folder with no config yet, and a pending client response
        server._configuration_response = [{"parseMode": "repo"}]
        uri = f"{FOLDER_URI}/a.rsl"
        server.workspace = _make_workspace({uri: ""})

        # When: a file in that folder is opened
        _run(handlers["did_open"](server, _open_params(uri)))

        # Then: configuration is fetched once and applied to the folder
        assert server.config_fetch_count == 1
        assert server.store.has_folder_config(FOLDER_URI)
        assert (
            server.store.get_config_for_uri(FOLDER_URI).parse_mode
            is ParseMode.REPO
        )

    def test_invalid_exclude_pattern_warns_once(self, server, handlers):
        """A malformed excludePatterns entry from the client is applied
        (with the bad entry dropped) and surfaces exactly one warning
        message naming it — the user has no other way to notice their
        exclusion silently isn't taking effect."""
        # Given: a client config with one valid and one malformed pattern
        server._configuration_response = [
            {"excludePatterns": ["valid/.*", "[unterminated"]}
        ]
        uri = f"{FOLDER_URI}/a.rsl"
        server.workspace = _make_workspace({uri: ""})

        # When: a file in that folder is opened
        _run(handlers["did_open"](server, _open_params(uri)))

        # Then: the valid pattern is applied, and exactly one warning is shown
        applied = server.store.get_config_for_uri(FOLDER_URI)
        assert applied.exclude_patterns == ("valid/.*",)
        warnings = [m for m in server._msgs if "[unterminated" in m.message]
        assert len(warnings) == 1


# pylint: disable-next=too-few-public-methods  # minimal data-holder stand-in; __init__ + window_log_message is the only interface needed
class _FakeLsForLogging:
    def __init__(self):
        self._logs = []

    def window_log_message(self, params):
        self._logs.append(params)


# pylint: disable-next=too-few-public-methods  # single test for _log_config_applied
class TestLogConfigApplied:
    def test_includes_bazel_shared_server_and_timeout(self):
        """Regression test: bazel.useSharedServer wasn't visible anywhere in
        the Output channel, so there was no way to confirm from the logs
        whether a configured value actually took effect."""
        # Given: a config with a non-default bazel.useSharedServer/timeout
        ls = _FakeLsForLogging()
        config = ServerConfig(
            bazel_use_shared_server=True, bazel_query_timeout_seconds=300
        )

        # When: the config-applied confirmation is logged
        lifecycle_mod._log_config_applied(ls, config, FOLDER_URI)

        # Then: both values are visible in the logged line
        message = ls._logs[0].message
        assert "bazel.useSharedServer=True" in message
        assert "bazel.queryTimeoutSeconds=300" in message


class TestOnConfigChangeRebindsOpenFiles:
    """Regression tests for the stale-scope-binding bug: a config change
    that alters scope_id's shape for an already-open file (e.g. WORKSPACE's
    folder-only id -> DIRECTORY's folder::directory id) must move the file
    into the new context, not leave it pinned to the shape it opened under
    until a full reload discards all ContextStore state."""

    def test_scope_id_change_rebinds_open_file_to_new_context(
        self, server, handlers
    ):
        # Given: a file open under WORKSPACE mode's folder-only scope_id
        uri = f"{FOLDER_URI}/dir/a.rsl"
        server.workspace = _make_workspace({uri: "package A"})
        _run(handlers["did_open"](server, _open_params(uri, "package A")))
        old_scope_id = server.store.scope_id_for_uri(uri)
        assert old_scope_id == FOLDER_URI  # WORKSPACE mode: folder-only id

        # The real ParseEngine populates ls.fh while processing the
        # "change" event did_open queues; this lightweight test double
        # never runs that loop, so seed it directly to simulate a file the
        # engine has already parsed at least once (the state
        # _rebind_open_files_to_current_scopes iterates over).
        server.fh.update_files(uri, "package A")

        # When: a config change switches the folder to DIRECTORY mode,
        # which reshapes scope_id from folder-only to folder::directory
        server._configuration_response = [{"parseMode": "directory"}, None]
        _run(
            handlers["config_change"](
                server, DidChangeConfigurationParams(settings=None)
            )
        )

        # Then: the file is rebound to the new, directory-shaped scope_id
        new_scope_id = server.store.scope_id_for_uri(uri)
        assert new_scope_id != old_scope_id
        assert new_scope_id is not None and "::" in new_scope_id
        new_context = server.store.get_context_for_uri(uri)
        assert new_context is not None
        assert uri in new_context.open_files

        # And: the old (WORKSPACE-shaped) context is gone, not leaked
        assert server.store.get_context(old_scope_id) is None

    def test_unchanged_scope_id_leaves_context_untouched(
        self, server, handlers
    ):
        """When a config change doesn't affect a file's scope_id shape at
        all, rebinding is a no-op — the file keeps its original context
        object rather than being torn down and rebuilt for nothing."""
        # Given: a file open under WORKSPACE mode
        uri = f"{FOLDER_URI}/a.rsl"
        server.workspace = _make_workspace({uri: "package A"})
        _run(handlers["did_open"](server, _open_params(uri, "package A")))
        server.fh.update_files(uri, "package A")
        original_context = server.store.get_context_for_uri(uri)

        # When: a config change happens but doesn't alter parse_mode
        server._configuration_response = [{"verify": False}, None]
        _run(
            handlers["config_change"](
                server, DidChangeConfigurationParams(settings=None)
            )
        )

        # Then: the file's context is the exact same object as before
        assert server.store.get_context_for_uri(uri) is original_context


class TestDidClose:
    def test_removes_file_and_destroys_empty_context(self, server, handlers):
        # Given: a single open file, with its context created
        uri = f"{FOLDER_URI}/a.rsl"
        server.workspace = _make_workspace({uri: "x"})
        _run(handlers["did_open"](server, _open_params(uri, "x")))
        assert server.store.get_context_for_uri(uri) is not None

        # When: that file is closed (the last file in its scope)
        handlers["did_close"](server, _close_params(uri))

        # Then: the now-empty context is destroyed
        assert server.store.get_context_for_uri(uri) is None

    def test_keeps_context_when_other_files_remain_open(
        self, server, handlers
    ):
        # Given: two open files sharing one context
        uri_a = f"{FOLDER_URI}/a.rsl"
        uri_b = f"{FOLDER_URI}/b.rsl"
        server.workspace = _make_workspace({uri_a: "a", uri_b: "b"})
        _run(handlers["did_open"](server, _open_params(uri_a, "a")))
        _run(handlers["did_open"](server, _open_params(uri_b, "b")))

        # When: only one of the two files is closed
        handlers["did_close"](server, _close_params(uri_a))

        # Then: the context survives, now holding only the remaining file
        remaining = server.store.get_context_for_uri(uri_b)
        assert remaining is not None
        assert uri_a not in remaining.open_files
        assert uri_b in remaining.open_files

    def test_queues_a_delete_event(self, server, handlers):
        # Given: a single open file
        uri = f"{FOLDER_URI}/a.rsl"
        server.workspace = _make_workspace({uri: "x"})
        _run(handlers["did_open"](server, _open_params(uri, "x")))
        # When: that file is closed
        handlers["did_close"](server, _close_params(uri))
        # Then: a "delete" event is queued
        assert ("delete", uri, None) in server._events

    def test_unknown_uri_is_a_no_op(self, server, handlers):
        # Given: a uri that was never opened
        # When: a close notification arrives for it anyway
        handlers["did_close"](
            server, _close_params("file:///never-opened.rsl")
        )
        # Then: it must not raise, and must not fabricate a context.
        assert (
            server.store.get_context_for_uri("file:///never-opened.rsl")
            is None
        )

    def test_reuses_open_time_scope_id_after_parse_mode_changes(
        self, server, handlers
    ):
        """did_close must destroy the context the file was actually opened
        into, even if the folder's parseMode changed to something that
        would now resolve to a different scope_id — otherwise the original
        context leaks forever (never destroyed, never revisited)."""
        # Given: a file opened in DIRECTORY mode (directory-scoped context)
        server.apply_config(
            ServerConfig(parse_mode=ParseMode.DIRECTORY), FOLDER_URI
        )
        uri = f"{FOLDER_URI}/dir1/a.rsl"
        server.workspace = _make_workspace({uri: "x"})
        _run(handlers["did_open"](server, _open_params(uri, "x")))

        directory_scope_id = server.store.get_context_for_uri(uri).scope_id
        assert "::" in directory_scope_id  # sanity: really is DIRECTORY-shaped

        # When: the folder's parseMode changes to WORKSPACE, then the file closes
        server.apply_config(
            ServerConfig(parse_mode=ParseMode.WORKSPACE), FOLDER_URI
        )
        # A fresh scope resolution now would give the bare folder_uri, not
        # the recorded directory_scope_id.
        assert server.uri_to_scope_id(uri) != directory_scope_id

        handlers["did_close"](server, _close_params(uri))

        # Then: the ORIGINAL (directory-scoped) context is gone — not leaked.
        assert server.store.get_context(directory_scope_id) is None


# pylint: disable-next=too-few-public-methods  # single test for shutdown wiring
class TestShutdown:
    def test_shutdown_request_stops_the_engine(self, server, handlers):
        """Given a registered SHUTDOWN handler, When the client sends a
        shutdown request, Then the server's parse engine is stopped —
        proving the worker thread doesn't get killed mid-cycle on exit."""
        # Given: a registered SHUTDOWN handler
        # When: the client sends a shutdown request
        handlers["shutdown"](server)
        # Then: the server's parse engine is stopped
        assert ("stop_engine", None, None) in server._events


def _watched_change(uri, change_type=FileChangeType.Changed):
    return DidChangeWatchedFilesParams(
        changes=[FileEvent(uri=uri, type=change_type)]
    )


class TestWatchedFilesChange:
    def test_plain_content_change_queues_reparse_files_not_a_full_reparse(
        self, server, handlers
    ):
        """Given a registered WORKSPACE_DID_CHANGE_WATCHED_FILES handler,
        When the client reports an on-disk .trlc/.rsl content change (e.g.
        `git checkout`, an edit made outside the editor), Then a lightweight
        ``reparse_files`` wake-up is queued — NOT a full ``reparse`` — so a
        plain content change doesn't force an unnecessary Bazel cache clear/
        full `bazel query` re-run (discover()'s own re-walk + the per-scope
        input-signature check already detect the on-disk change)."""
        # Given: a registered watched-files handler
        # When: the client reports a .trlc content change
        handlers["watched_files"](
            server, _watched_change(f"{FOLDER_URI}/a.trlc")
        )
        # Then: a lightweight reparse_files wake-up is queued, not "reparse"
        assert ("reparse_files", None, None) in server._events
        assert ("reparse", None, None) not in server._events

    def test_build_file_change_queues_a_full_reparse(self, server, handlers):
        """A BUILD file change can alter the Bazel target graph itself, so
        it gets the full ``reparse`` (which clears bazel_cache, forcing the
        next parse to re-run `bazel query`)."""
        # When: the client reports a BUILD file change
        handlers["watched_files"](
            server, _watched_change(f"{FOLDER_URI}/pkg/BUILD.bazel")
        )
        # Then: a full reparse is queued
        assert ("reparse", None, None) in server._events

    def test_bzl_file_change_queues_a_full_reparse(self, server, handlers):
        """A .bzl macro file change can also alter the target graph."""
        # When: the client reports a .bzl file change
        handlers["watched_files"](
            server, _watched_change(f"{FOLDER_URI}/pkg/trlc.bzl")
        )
        # Then: a full reparse is queued
        assert ("reparse", None, None) in server._events

    def test_mixed_batch_with_one_build_file_queues_a_full_reparse(
        self, server, handlers
    ):
        """A single batched notification with both a content change and a
        BUILD change still gets the full reparse — any graph-relevant file
        in the batch is enough."""
        params = DidChangeWatchedFilesParams(
            changes=[
                FileEvent(
                    uri=f"{FOLDER_URI}/a.trlc", type=FileChangeType.Changed
                ),
                FileEvent(
                    uri=f"{FOLDER_URI}/pkg/BUILD", type=FileChangeType.Changed
                ),
            ]
        )
        # When: the batch is reported
        handlers["watched_files"](server, params)
        # Then: a full reparse is queued
        assert ("reparse", None, None) in server._events
        assert ("reparse_files", None, None) not in server._events


# pylint: disable-next=too-few-public-methods  # single test for this command; keeps cmd tests grouped
class TestCmdParseAll:
    def test_switches_default_and_all_folder_configs_to_repo_mode(
        self,
        server,
        handlers,  # pylint: disable=unused-argument  # fixture triggers handler registration as side effect
    ):
        # Given: a default config and a folder config, neither in REPO mode
        server.apply_config(ServerConfig(parse_mode=ParseMode.WORKSPACE))
        server.apply_config(
            ServerConfig(parse_mode=ParseMode.DIRECTORY), FOLDER_URI
        )

        # When: the extension.parseAll command is executed
        cmd = server.get_handler("extension.parseAll")
        cmd(server)

        # Then: both configs switch to REPO mode and a reparse is queued
        assert server.store.default_config.parse_mode is ParseMode.REPO
        assert (
            server.store.get_config_for_uri(FOLDER_URI).parse_mode
            is ParseMode.REPO
        )
        assert ("reparse", None, None) in server._events

    def test_switches_every_folder_config_independently(
        self,
        server,
        handlers,  # pylint: disable=unused-argument  # fixture triggers handler registration as side effect
    ):
        """With multiple folders configured, every one of them switches to
        REPO mode — not just whichever folder happens to be iterated first —
        and each folder's other settings survive the switch untouched."""
        # Given: two folders with distinct, non-REPO configs (and a distinct
        # non-default setting each, to prove the switch preserves the rest
        # of the config rather than replacing it wholesale)
        other_folder_uri = "file:///other-workspace"
        server.apply_config(
            ServerConfig(
                parse_mode=ParseMode.DIRECTORY, bazel_executable="first-bazel"
            ),
            FOLDER_URI,
        )
        server.apply_config(
            ServerConfig(
                parse_mode=ParseMode.WORKSPACE, bazel_executable="second-bazel"
            ),
            other_folder_uri,
        )

        # When: the extension.parseAll command is executed
        cmd = server.get_handler("extension.parseAll")
        cmd(server)

        # Then: both folders are independently switched to REPO mode
        first_cfg = server.store.get_config_for_uri(FOLDER_URI)
        second_cfg = server.store.get_config_for_uri(other_folder_uri)
        assert first_cfg.parse_mode is ParseMode.REPO
        assert second_cfg.parse_mode is ParseMode.REPO
        # Each folder's other settings are preserved, not just replaced by a
        # fresh default ServerConfig() — proves dataclasses.replace() is
        # used per-folder rather than a single shared REPO config object.
        assert first_cfg.bazel_executable == "first-bazel"
        assert second_cfg.bazel_executable == "second-bazel"
        assert ("reparse", None, None) in server._events

    def test_bazel_mode_is_left_untouched(
        self,
        server,
        handlers,  # pylint: disable=unused-argument  # fixture triggers handler registration as side effect
    ):
        """BAZEL mode has its own (bazel-query-driven) notion of "parse
        everything" — switching it to REPO would silently and permanently
        drop the user out of Bazel-target scoping onto a raw recursive
        filesystem walk. Only the reparse (which also clears the Bazel
        cache) should be forced, not the parse mode itself."""
        # Given: default and folder configs already in BAZEL mode
        server.apply_config(ServerConfig(parse_mode=ParseMode.BAZEL))
        server.apply_config(
            ServerConfig(parse_mode=ParseMode.BAZEL), FOLDER_URI
        )

        # When: the extension.parseAll command is executed
        cmd = server.get_handler("extension.parseAll")
        cmd(server)

        # Then: both configs stay in BAZEL mode, but a reparse still happens
        assert server.store.default_config.parse_mode is ParseMode.BAZEL
        assert (
            server.store.get_config_for_uri(FOLDER_URI).parse_mode
            is ParseMode.BAZEL
        )
        assert ("reparse", None, None) in server._events

    def test_repo_mode_is_left_untouched(
        self,
        server,
        handlers,  # pylint: disable=unused-argument  # fixture triggers handler registration as side effect
    ):
        """REPO mode already parses everything reachable; parseAll should
        still trigger a reparse without needing to switch modes."""
        # Given: a default config already in REPO mode
        server.apply_config(ServerConfig(parse_mode=ParseMode.REPO))

        # When: the extension.parseAll command is executed
        cmd = server.get_handler("extension.parseAll")
        cmd(server)

        # Then: it stays in REPO mode, and a reparse still happens
        assert server.store.default_config.parse_mode is ParseMode.REPO
        assert ("reparse", None, None) in server._events
