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
"""Shared fixtures for handler unit tests.

The key fixture is :func:`fake_ls` which provides a :class:`FakeLanguageServer`
that satisfies :class:`~server.server_protocol.ServerProtocol` and is
pre-populated with a real TRLC parse of small fixture content.
"""
# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
import trlc.errors
import trlc.trlc

from server.bazel import BazelManagerCache
from server.context_store import ContextStore
from server.file_handler import File_Handler
from server.parse_context import ParseContext, ParseResult
from server.parse_guard import normalize_fs_path
from server.reference_resolver import ReferenceResolver
from server.server_config import ServerConfig
from server.token_utils import uri_from_file

# ---------------------------------------------------------------------------
# Minimal TRLC fixture content
# ---------------------------------------------------------------------------

RSL_CONTENT = """\
package Types

type MyRecord {
    x Integer
    y Integer
}

enum Color {
    Red
    Green
    Blue
}
"""

TRLC_CONTENT = """\
package Types

MyRecord example {
    x = 1
    y = 2
}
"""

#: Second, independent package used by scope-boundary tests to prove that a
#: second scope's symbols/diagnostics never leak into the first scope's
#: lookups (and vice versa).
OTHER_RSL_CONTENT = """\
package OtherTypes

type OtherRecord {
    z Integer
}
"""

OTHER_TRLC_CONTENT = """\
package OtherTypes

OtherRecord other_example {
    z = 3
}
"""


# ---------------------------------------------------------------------------
# FakeLanguageServer
# ---------------------------------------------------------------------------


def _make_workspace_stub():
    """Return a minimal workspace stub for FakeLanguageServer.

    ``get_text_document`` is wired to a no-op side_effect here; the real
    per-uri lookup is installed by :meth:`FakeLanguageServer.__post_init__`
    (needs ``self._uri_contents``, populated by ``open_scope``, which
    doesn't exist yet at this point during dataclass field construction).
    """
    return MagicMock()


class HandlerRegistryMixin:
    """pygls-compatible ``feature``/``command`` registration + lookup.

    Shared by every fake language-server stub in the test suite (here and
    in ``test_lifecycle.py``'s ``LifecycleTestServer``) — each just needs
    its own ``_handlers: dict`` field for this to work against.
    """

    _handlers: dict

    def feature(self, feature_id, *_args, **_kwargs):
        """Capture the decorated handler function under feature_id."""

        def decorator(fn):
            self._handlers[feature_id] = fn
            return fn

        return decorator

    def command(self, command_name: str, **_kwargs):
        """Capture a command handler under command_name."""

        def decorator(fn):
            self._handlers[command_name] = fn
            return fn

        return decorator

    def get_handler(self, feature_id):
        """Return the handler registered for *feature_id*, or None."""
        return self._handlers.get(feature_id)


@dataclass
# pylint: disable-next=too-many-instance-attributes  # stub mirrors full server API
class FakeLanguageServer(HandlerRegistryMixin):
    """Minimal stub that satisfies ServerProtocol for handler unit tests.

    Populate it via :func:`fake_ls` which runs a real TRLC parse so
    handler logic executes against a realistic AST.

    Also implements ``feature()`` so that ``register(server)`` can be called
    directly — registered handler functions are captured in ``_handlers`` and
    can be retrieved via :meth:`get_handler`.
    """

    config: ServerConfig = field(default_factory=ServerConfig)
    store: ContextStore = field(default_factory=ContextStore)
    fh: File_Handler = field(default_factory=File_Handler)
    refs: ReferenceResolver = field(init=False)
    bazel_cache: BazelManagerCache = field(default_factory=BazelManagerCache)
    workspace: Any = field(default_factory=_make_workspace_stub)
    _msgs: list = field(default_factory=list)
    _logs: list = field(default_factory=list)
    _events: list = field(default_factory=list)
    _handlers: dict = field(default_factory=dict)
    #: uri -> "live buffer" content, populated by open_scope(). Backs
    #: workspace.get_text_document() so resolve_document_tokens's
    #: staleness check (parsed.lexer.content vs. the live buffer) sees a
    #: match for any uri a test opened via open_scope with the same
    #: content it was actually parsed from — the normal "not being
    #: actively edited" case these tests model. A uri with no entry (never
    #: opened, or a test's own manual context construction) falls back to
    #: "" — same as the old fixed stub, needed by semantic_tokens's
    #: not-yet-parsed fallback.
    _uri_contents: dict = field(default_factory=dict)

    def __post_init__(self):
        self.refs = ReferenceResolver()
        self.workspace.get_text_document.side_effect = self._get_text_document

    def _get_text_document(self, uri):
        return MagicMock(source=self._uri_contents.get(uri, ""))

    # -- ServerProtocol methods ------------------------------------------

    def queue_event(self, kind, uri=None, content=None):
        """Queue event."""
        self._events.append((kind, uri, content))

    def stop_engine(self):
        """Stop engine (no-op stub; no real ParseEngine in handler tests)."""

    def window_show_message(self, params):
        """Window show message."""
        self._msgs.append(params)

    def window_log_message(self, params):
        """Window log message."""
        self._logs.append(params)

    def get_config_for_uri(self, _uri):
        """Return the config applicable to a document URI."""
        return self.config

    def get_context_for_uri(self, uri):
        """Return the parse context for a document URI.

        Delegates to the real ContextStore, which resolves via the
        scope_id recorded when *uri* was opened — this genuinely scopes by
        uri (unlike the old stub, which ignored uri and always returned
        whichever context was populated first), so tests can exercise
        multiple concurrent scopes and assert isolation between them.
        """
        return self.store.get_context_for_uri(uri)

    # -- Test helper --------------------------------------------------------

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments  # test helper mirrors ParseContext's own field count
    def open_scope(self, scope_id, uri, content, vsm, parse_mode=None):
        """Register *uri* as open in *scope_id*, creating the scope's
        ParseContext (with *vsm*) if this is the first file opened in it."""
        self._uri_contents[uri] = content

        def factory():
            return ParseContext(
                scope_id=scope_id,
                result=ParseResult(vsm=vsm),
                parse_mode=parse_mode or self.config.parse_mode,
            )

        self.store.add_file(scope_id, uri, content, factory)


# ---------------------------------------------------------------------------
# Parse helper
# ---------------------------------------------------------------------------


def _parse_trlc(
    rsl_content: str, rsl_path: str, trlc_content: str, trlc_path: str, tmp_dir
):
    """Run a minimal TRLC parse and return (symbols, all_files, rsl_abs, trlc_abs).

    *tmp_dir* must be a directory that outlives the caller; the returned
    file paths point into it.  Using a pytest-managed temp directory
    (e.g. from ``tmp_path_factory``) ensures the files exist for the
    full fixture lifetime.
    """
    # normalize_fs_path so all_files is keyed the same way production keys it
    # (Vscode_Source_Manager.register_file normalizes every key). This bare
    # trlc.trlc.Source_Manager doesn't, so without this the keys are raw paths
    # — which on Windows keep the drive letter's original case (``C:\…``) while
    # the handlers under test look files up via uri_from_file→path_from_uri→
    # normalize_fs_path (``c:\…``), so every lookup misses and the handler
    # returns None. No-op on POSIX.
    rsl_file = normalize_fs_path(os.path.join(str(tmp_dir), rsl_path))
    trlc_file = normalize_fs_path(os.path.join(str(tmp_dir), trlc_path))
    with open(rsl_file, "w", encoding="utf-8") as f:
        f.write(rsl_content)
    with open(trlc_file, "w", encoding="utf-8") as f:
        f.write(trlc_content)

    mh = trlc.errors.Message_Handler()
    sm = trlc.trlc.Source_Manager(mh=mh, verify_mode=False)
    sm.register_file(rsl_file)
    sm.register_file(trlc_file)
    try:
        sm.process()
    except trlc.errors.TRLC_Error:
        pass

    return sm.stab, sm.all_files, rsl_file, trlc_file


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    """Return (symbols, all_files, rsl_path, trlc_path) from a real TRLC parse.

    The temp directory is module-scoped so the returned file paths remain
    valid for the entire test module (they are used as dict keys into
    ``all_files`` and as cursor-position anchors).
    """
    tmp_dir = tmp_path_factory.mktemp("trlc_parse")
    return _parse_trlc(
        RSL_CONTENT, "types.rsl", TRLC_CONTENT, "sample.trlc", tmp_dir
    )


@pytest.fixture(scope="module")
def other_parsed(tmp_path_factory):
    """A second, independent real TRLC parse (different package/dir) used to
    populate a second scope for cross-scope isolation assertions."""
    tmp_dir = tmp_path_factory.mktemp("trlc_parse_other")
    return _parse_trlc(
        OTHER_RSL_CONTENT,
        "other_types.rsl",
        OTHER_TRLC_CONTENT,
        "other_sample.trlc",
        tmp_dir,
    )


# pylint: disable-next=too-few-public-methods  # data-only stand-in; __init__ is the only interface needed
class FakeVSM:
    """Minimal stand-in for Vscode_Source_Manager: exposes just the two
    attributes handlers read (``stab``, ``all_files``)."""

    def __init__(self, stab, all_files):
        self.stab = stab
        self.all_files = all_files


@pytest.fixture
def fake_ls(parsed):
    """A FakeLanguageServer pre-populated with a real ParseContext built
    from the parsed fixture state."""
    symbols, all_files, rsl_path, trlc_path = parsed
    ls = FakeLanguageServer()
    vsm = FakeVSM(stab=symbols, all_files=all_files)
    ls.open_scope("test-scope", uri_from_file(rsl_path), RSL_CONTENT, vsm)
    ls.open_scope("test-scope", uri_from_file(trlc_path), TRLC_CONTENT, vsm)
    return ls


@pytest.fixture
def fake_ls_two_scopes(fake_ls, other_parsed):
    """``fake_ls`` plus a second, independent scope ("other-scope") — for
    tests that assert lookups never cross a scope boundary."""
    other_symbols, other_all_files, other_rsl_path, other_trlc_path = (
        other_parsed
    )
    other_vsm = FakeVSM(stab=other_symbols, all_files=other_all_files)
    fake_ls.open_scope(
        "other-scope",
        uri_from_file(other_rsl_path),
        OTHER_RSL_CONTENT,
        other_vsm,
    )
    fake_ls.open_scope(
        "other-scope",
        uri_from_file(other_trlc_path),
        OTHER_TRLC_CONTENT,
        other_vsm,
    )
    return fake_ls


@pytest.fixture
def rsl_path(parsed):
    """Absolute path to the parsed types.rsl fixture file."""
    return parsed[2]


@pytest.fixture
def trlc_path(parsed):
    """Absolute path to the parsed sample.trlc fixture file."""
    return parsed[3]


@pytest.fixture
def other_rsl_path(other_parsed):
    """Absolute path to the second scope's other_types.rsl fixture file."""
    return other_parsed[2]


@pytest.fixture
def other_trlc_path(other_parsed):
    """Absolute path to the second scope's other_sample.trlc fixture file."""
    return other_parsed[3]
