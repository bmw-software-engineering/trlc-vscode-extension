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
"""End-to-end coverage for BAZEL parse mode.

Every other integration test runs the server in WORKSPACE mode; BAZEL mode
previously had only unit-level coverage with ``bazel query`` mocked out
(``tests/unit/test_bazel.py``, ``tests/unit/test_scope_strategies
.py``). These tests drive the real server subprocess against a fake ``bazel``
executable (see ``fake_bazel_main.py``, a real ``py_binary`` rather than a
loose script - see its docstring for why) to prove the whole path —
workspace discovery, ``bazel query`` invocation, target-scoped file
registration, and the query-failure fallback — actually works together,
not just each piece in isolation.
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Optional

import pytest
from lsprotocol import types
from pytest_lsp import ClientServerConfig, LanguageClient

# pylint's static import resolution doesn't follow rules_python's runfiles
# py_library's `imports = ["../.."]` (a cross-repo path escape, needed so
# "python.runfiles" resolves from the external repo root) - the import
# works fine at actual test runtime, this is a pylint/PYTHONPATH-modeling
# gap only.
from python.runfiles import Runfiles  # pylint: disable=import-error

from .conftest import initialize_params, server_env
from .content import BAZEL_INVALID_TRLC, BAZEL_TYPES_RSL, BAZEL_VALID_TRLC
from .fake_bazel_main import FAIL_MARKER_NAME
from .lsp_helpers import open_document, wait_for_diagnostics, wait_for_message

# Windows-only skip: these tests drive a real fake_bazel.exe py_binary
# subprocess as the "bazel" executable. On Windows, py_binary's native PE
# launcher needs PYTHONPATH to bootstrap its own interpreter; our
# BazelClient strips PYTHONPATH before spawning it (see bazel.py's
# _PYTHON_ENV_VARS_TO_STRIP, POSIX-only for exactly this reason), so on
# some Windows setups the launcher falls back to a PATH search and can hit
# the Microsoft Store's python.exe placeholder stub, which hangs
# indefinitely under piped stdio instead of failing fast (confirmed
# manually: `.\fake_bazel.exe` alone hit the Store nag; `bazel run
# //tests:fake_bazel -- ...`, which sets up its own correct env, worked).
# This is a fake_bazel test-harness bootstrap issue, not a bug in the
# extension's real bazel-query code path (that always shells out to the
# user's real `bazel`, not a py_binary). Skip rather than keep chasing
# Windows-only interpreter-launcher environment differences.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="fake_bazel.exe py_binary launcher hangs on some Windows CI/dev "
    "setups (Store Python stub fallback) - test-harness-only, not a "
    "product bug; see comment above",
)

# ---------------------------------------------------------------------------
# Fixture workspace + fake `bazel` executable
# ---------------------------------------------------------------------------


def _make_uri(path: Path) -> str:
    url = urllib.parse.quote(str(path).replace("\\", "/"))
    return urllib.parse.urlunparse(("file", "", url, "", "", ""))


def _fake_bazel_path() -> str:
    """Runfiles path of the prebuilt fake-bazel py_binary (built once, not
    per-fixture) - see fake_bazel_main.py's docstring."""
    runfiles = Runfiles.Create()
    # "_main" (Bazel's fixed canonical name for the root module, independent
    # of MODULE.bazel's own module(name=...)): Rlocation() only consults the
    # repo-mapping file to translate an *apparent* repo name to canonical -
    # "_main" is already canonical, so this skips that translation (and thus
    # doesn't depend on the mapping file, or on source_repo, at all). Also
    # sidesteps Rlocation()'s default caller-repo auto-detection
    # (CurrentRepository(), which os.path.relpath()s this file's location
    # against the runfiles root) crashing when Bazel's execroot and the
    # materialized runfiles tree land on different drive letters, as seen on
    # a real Windows CI runner (D: vs C:) - passing source_repo explicitly
    # avoids that call.
    #
    # ".exe" on Windows: a py_binary's actual output *file* (what the
    # runfiles manifest key is derived from, not the BUILD target name) is
    # "fake_bazel.exe" there - the whole reason py_binary was picked for this
    # in the first place is that Windows gives it a real PE launcher (see
    # fake_bazel_main.py's docstring).
    suffix = ".exe" if sys.platform == "win32" else ""
    rlocation_path = f"_main/tests/fake_bazel{suffix}"
    path = runfiles.Rlocation(rlocation_path, source_repo="")
    assert path is not None, (
        f"fake_bazel py_binary not found in runfiles: "
        f"Rlocation({rlocation_path!r}) returned None"
    )
    return path


@pytest.fixture
def bazel_workspace(tmp_path_factory) -> Path:
    """A fresh Bazel-marked workspace with one requirement + one spec file,
    and a working fake `bazel` executable (query succeeds)."""
    ws = tmp_path_factory.mktemp("bazel_ws")
    (ws / "MODULE.bazel").write_text("", encoding="utf-8")
    pkg = ws / "pkg"
    pkg.mkdir()
    (pkg / "types.rsl").write_text(BAZEL_TYPES_RSL, encoding="utf-8")
    (pkg / "example.trlc").write_text(BAZEL_VALID_TRLC, encoding="utf-8")
    return ws


@pytest.fixture
def failing_bazel_workspace(tmp_path_factory) -> Path:
    """Same layout as :func:`bazel_workspace`, but marked so the fake
    `bazel` executable's query always fails (simulates a broken Bazel
    setup) - see fake_bazel_main.py's FAIL_MARKER_NAME."""
    ws = tmp_path_factory.mktemp("bazel_ws_failing")
    (ws / "MODULE.bazel").write_text("", encoding="utf-8")
    pkg = ws / "pkg"
    pkg.mkdir()
    (pkg / "types.rsl").write_text(BAZEL_TYPES_RSL, encoding="utf-8")
    (pkg / "example.trlc").write_text(BAZEL_VALID_TRLC, encoding="utf-8")
    (ws / FAIL_MARKER_NAME).write_text("", encoding="utf-8")
    return ws


def _bazel_config() -> dict:
    return {
        "parseMode": "bazel",
        # Comfortably under wait_for_diagnostics/wait_for_message's own
        # 15s/10s timeouts: if the fake_bazel subprocess call hangs instead
        # of erroring (observed on Windows CI - every wait_for_* call here
        # timing out with no "Bazel unavailable" warning ever logged, i.e.
        # BazelClient's subprocess.run hadn't even returned yet), this turns
        # a silent, undiagnosable 15s stall into a real, logged
        # BazelQueryError well before the test's own timeout fires.
        "bazel": {"executable": _fake_bazel_path(), "queryTimeoutSeconds": 5},
    }


#: Cap on how much of the discovered log file _dump_server_log() prints.
#: Dumping a whole file once actually exceeded Bazel's own
#: --experimental_ui_max_stdouterr_bytes (1 MiB default) in CI, which made
#: Bazel silently discard the *entire* test log instead of truncating it -
#: strictly worse than not dumping anything. Tailing keeps this useful
#: without risking that again.
_LOG_DUMP_MAX_BYTES = 50_000


def _find_server_log() -> Optional[Path]:
    """Locate this test's own server-subprocess log file.

    logging_config.LOG_FILE is PID-suffixed ("pygls-<pid>.log") precisely so
    concurrent server instances never collide on the same file - but that
    also means importing LOG_FILE here would give *this test process's own*
    pid, never the separate server subprocess's. There can be several
    bazel_mode server subprocesses' logs sitting in the temp dir at once
    (other tests in this file, run sequentially), so this picks the most
    recently modified one - the one this test's own server just wrote to.
    """
    candidates = sorted(
        Path(tempfile.gettempdir()).glob("pygls-*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _dump_server_log() -> None:
    """Print the tail of the server's own log file to stderr - a genuine
    BazelQueryError (with its stderr capture) would be logged there even if
    it never made it into an LSP notification the client-side wait_for_*
    helpers saw, so this is the difference between a bare timeout and an
    actual diagnosis."""
    log_path = _find_server_log()
    if log_path is None:
        print(
            f"[_dump_server_log] no pygls-*.log found in "
            f"{tempfile.gettempdir()}",
            file=sys.stderr,
        )
        return
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(
            f"[_dump_server_log] could not read {log_path}: {e}",
            file=sys.stderr,
        )
        return
    if len(content) > _LOG_DUMP_MAX_BYTES:
        content = content[-_LOG_DUMP_MAX_BYTES:]
        print(
            f"[_dump_server_log] --- {log_path} (tail, "
            f"{_LOG_DUMP_MAX_BYTES} bytes) ---",
            file=sys.stderr,
        )
    else:
        print(f"[_dump_server_log] --- {log_path} ---", file=sys.stderr)
    print(content, file=sys.stderr)
    print("[_dump_server_log] --- end ---", file=sys.stderr)


async def _start_client(workspace: Path) -> LanguageClient:
    """Start a fresh server subprocess rooted at *workspace*."""
    config = ClientServerConfig(
        server_command=[sys.executable, "-m", "server", "--stdio"],
        server_env=server_env(),
    )
    client = await config.start()
    client.initialize_result = await client.initialize_session(
        initialize_params(workspace)
    )
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBazelModeSuccess:
    async def test_diagnostics_published_for_bazel_scoped_target(
        self, bazel_workspace
    ):
        """Given a Bazel workspace with a working `bazel query`, opening a
        target's source file resolves it to a BAZEL-mode scope, pulls in
        its transitive dep (the spec file), and publishes diagnostics —
        proving the query→target→file pipeline works end-to-end, not just
        each stage in isolation."""
        # Given: a Bazel workspace with a working bazel query, configured for BAZEL mode
        client = await _start_client(bazel_workspace)
        try:
            uri = _make_uri(bazel_workspace / "pkg" / "example.trlc")
            client.set_configuration(
                _bazel_config(),
                section="trlcServer",
                scope_uri=bazel_workspace.as_uri(),
            )
            # When: the target's valid instance file is opened
            open_document(client, uri, BAZEL_VALID_TRLC)
            try:
                diags = await wait_for_diagnostics(client, uri)
            except TimeoutError:
                _dump_server_log()
                raise
            # Then: the valid instance parses clean under its Bazel-derived
            # scope (which required the query to run and the spec's srcs to
            # be pulled in transitively — an unresolved/missing schema would
            # produce parse errors here instead).
            assert diags == []
        finally:
            await client.shutdown_session()

    async def test_error_in_target_source_is_reported(self, bazel_workspace):
        """An invalid instance file under a Bazel target still gets a real
        error diagnostic — the Bazel scope isn't just "no errors by
        accident of nothing being parsed"."""
        # Given: a Bazel workspace with a working bazel query, configured for BAZEL mode
        client = await _start_client(bazel_workspace)
        try:
            uri = _make_uri(bazel_workspace / "pkg" / "example.trlc")
            client.set_configuration(
                _bazel_config(),
                section="trlcServer",
                scope_uri=bazel_workspace.as_uri(),
            )
            # When: an invalid instance file is opened under that target
            open_document(client, uri, BAZEL_INVALID_TRLC)
            try:
                diags = await wait_for_diagnostics(client, uri)
            except TimeoutError:
                _dump_server_log()
                raise
            # Then: a real error diagnostic is reported
            assert len(diags) >= 1
            assert any(
                d.severity == types.DiagnosticSeverity.Error for d in diags
            )
        finally:
            await client.shutdown_session()


# pylint: disable-next=too-few-public-methods  # single scenario; kept as its own class for grouping alongside TestBazelModeSuccess
class TestBazelModeQueryFailure:
    async def test_shows_one_error_popup_and_leaves_file_unparsed(
        self, failing_bazel_workspace
    ):
        """Given a `bazel query` that always fails with a real error (as
        opposed to simply finding no WORKSPACE/MODULE.bazel at all — that
        case gets a quiet directory-mode fallback instead, covered at the
        unit level in test_scope_strategies.py/test_bazel.py), the
        workspace is genuinely broken: BazelScopeStrategy.scope_id()
        resolves to no scope at all (not a directory-scoped fallback), so
        did_open never creates a ParseContext for the file and it's never
        parsed — see docs/ARCHITECTURE.md's "Resolution outcomes" table,
        row "Unavailable — no scope". The one user-visible signal is a
        single Error popup naming the failure."""
        # Given: a Bazel workspace whose bazel query always fails
        client = await _start_client(failing_bazel_workspace)
        try:
            uri = _make_uri(failing_bazel_workspace / "pkg" / "example.trlc")
            client.set_configuration(
                _bazel_config(),
                section="trlcServer",
                scope_uri=failing_bazel_workspace.as_uri(),
            )
            # When: a valid instance file under that (unreachable) target is opened
            open_document(client, uri, BAZEL_VALID_TRLC)
            try:
                msg = await wait_for_message(client)
            except TimeoutError:
                _dump_server_log()
                raise
            # Then: one Error popup names the underlying bazel query
            # failure, and the file is left with no diagnostics at all —
            # it was never parsed, not "parsed clean by accident".
            assert msg.type == types.MessageType.Error
            assert "Bazel command failed" in msg.message
            assert uri not in client.diagnostics
        finally:
            await client.shutdown_session()
