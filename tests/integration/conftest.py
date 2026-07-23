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
"""Shared fixtures for LSP integration tests.

Each test function gets a fresh ``LanguageClient`` (pytest-lsp) connected to
a newly-started ``server`` (the ``trlc-lsp`` package) subprocess. The
workspace is shared across the session (fixtures are read-only).

Content strings (RSL_TEXT, TRLC_TEXT, …) live in :mod:`.content` and are
re-exported here so existing ``from .conftest import RSL_TEXT`` imports in
test files continue to work unchanged.
"""
# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

import os
import shutil
import sys
from pathlib import Path

import pytest
import pytest_lsp
from lsprotocol import types
from pytest_lsp import ClientServerConfig, LanguageClient, client_capabilities

from server.token_utils import uri_from_file

# ---------------------------------------------------------------------------
# Paths — imported here so test files can still do
# ``from .conftest import FIXTURES_DIR`` if needed.
# ---------------------------------------------------------------------------
from .content import FIXTURES_DIR, RSL_TEXT, TRLC_TEXT  # noqa: E402, F401


def _make_uri(path: Path) -> str:
    """Build a ``file://`` URI matching the server's own canonicalization
    (:func:`server.token_utils.uri_from_file`) — a hand-rolled
    ``urllib.parse`` version doesn't lowercase the Windows drive letter the
    way pygls's ``from_fs_path`` does, so fixtures built with one and
    diagnostics published under the other never compare equal on Windows.
    """
    return uri_from_file(str(path))


def server_env() -> dict:
    """Environment for a freshly spawned ``python -m server --stdio``
    subprocess.

    Under `bazel test`, this process's own import path (this is how
    `from server... import ...` above resolves) comes from sys.path
    entries injected by Bazel's Python bootstrap at startup, not from
    a PYTHONPATH env var - so a freshly spawned subprocess (which
    doesn't go through that same bootstrap) starts with a bare
    sys.path and can't find the "server" module on its own. Passing
    the parent's resolved sys.path through explicitly fixes that
    while being a no-op outside Bazel (a normal editable install
    already has "server" importable, so this just adds redundant
    entries).
    """
    return dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))


def initialize_params(workspace: Path) -> types.InitializeParams:
    """Standard ``initialize`` request params rooted at *workspace*."""
    return types.InitializeParams(
        capabilities=client_capabilities("visual-studio-code"),
        root_uri=workspace.as_uri(),
        workspace_folders=[
            types.WorkspaceFolder(uri=workspace.as_uri(), name=workspace.name)
        ],
    )


# ---------------------------------------------------------------------------
# Session-scoped workspace (read-only copy of fixtures)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def workspace(tmp_path_factory) -> Path:
    """Temp directory with all fixture files, shared for the whole session."""
    ws = tmp_path_factory.mktemp("lsp_workspace")
    for src in FIXTURES_DIR.iterdir():
        dest = ws / src.name
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
    return ws


# ---------------------------------------------------------------------------
# Function-scoped client (fresh server per test)
# ---------------------------------------------------------------------------


@pytest_lsp.fixture(
    config=ClientServerConfig(
        server_command=[sys.executable, "-m", "server", "--stdio"],
        server_env=server_env(),
    ),
)
async def client(client: LanguageClient, workspace):
    """A live ``server`` subprocess. One per test function for isolation."""
    client.initialize_result = await client.initialize_session(
        initialize_params(workspace)
    )
    yield
    await client.shutdown_session()


# ---------------------------------------------------------------------------
# Convenience URI helpers (function-scoped so they use the workspace fixture)
# ---------------------------------------------------------------------------


@pytest.fixture
def types_uri(workspace) -> str:
    """Types uri."""
    return _make_uri(workspace / "types.rsl")


@pytest.fixture
def sample_uri(workspace) -> str:
    """Sample uri."""
    return _make_uri(workspace / "sample.trlc")


@pytest.fixture
def invalid_uri(workspace) -> str:
    """Invalid uri."""
    # Kept in its own subdirectory (not alongside sample.trlc/types.rsl):
    # WORKSPACE mode's register_include() recursively auto-includes every
    # .rsl/.trlc file it finds under the workspace folder, so an
    # invalid.trlc sitting next to sample.trlc would collide with whatever
    # content another test opens at the same package/object name.
    return _make_uri(workspace / "invalid_scope" / "invalid.trlc")


@pytest.fixture
def broken_ref_rsl_uri(workspace) -> str:
    """Broken-reference RSL uri."""
    # Kept in its own subdirectory (not alongside types.rsl/sample.trlc):
    # same WORKSPACE mode isolation reason as invalid_uri — BrokenRef package
    # must not collide with the Types package used by other tests.
    return _make_uri(workspace / "broken_reference" / "broken_ref.rsl")


@pytest.fixture
def broken_ref_trlc_uri(workspace) -> str:
    """Broken-reference TRLC uri."""
    return _make_uri(workspace / "broken_reference" / "broken_ref.trlc")
