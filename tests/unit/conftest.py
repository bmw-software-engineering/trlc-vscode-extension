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
"""Shared pytest fixtures and test doubles for unit tests."""

from unittest.mock import patch

import pytest
import trlc.errors
import trlc.lexer

from server.bazel import BazelQueryError


def patch_build_target_manager_failure(
    message="Bazel command failed", stderr="boom"
):
    """Patch ``server.bazel.build_target_manager`` to raise a
    ``BazelQueryError`` (a broken Bazel setup). Returns the patch context
    manager, so callers use it as ``with ... as mock_build:``."""
    return patch(
        "server.bazel.build_target_manager",
        side_effect=BazelQueryError(message, returncode=1, stderr=stderr),
    )


class _FakeContext:  # pylint: disable=too-few-public-methods
    """Minimal stand-in for a ParseContext - just the `vsm` attribute,
    reachable via `snapshot()` like the real ParseContext.snapshot()."""

    def __init__(self, vsm):
        self.vsm = vsm

    def snapshot(self):
        """Mimic ParseContext.snapshot() - the stub is its own snapshot."""
        return self


class _FakeVSM:  # pylint: disable=too-few-public-methods
    """Minimal stand-in for a VSM - just the `all_files` attribute."""

    def __init__(self, all_files):
        self.all_files = all_files


def _tokenize(content: str, file_name: str = "test.trlc") -> list:
    """Tokenize *content* with the real TRLC lexer; return token list."""
    mh = trlc.errors.Message_Handler()
    lexer = trlc.lexer.TRLC_Lexer(mh, file_name, content)
    tokens = []
    while True:
        try:
            tok = lexer.token()
        except trlc.errors.TRLC_Error as e:
            pytest.fail(f"Unexpected lex error in test content: {e}")
        if tok is None:
            break
        tokens.append(tok)
    return tokens


@pytest.fixture
def make_tokens():
    """Return the _tokenize helper so individual tests can call it."""
    return _tokenize
