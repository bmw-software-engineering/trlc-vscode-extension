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
"""Unit tests for trlc_lsp.handlers.status."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

from dataclasses import replace

import pytest
from lsprotocol.types import Diagnostic, DiagnosticSeverity, Position, Range

from server.handlers import status as status_mod
from server.parse_context import ParseResult
from server.server_config import ParseMode
from server.token_utils import uri_from_file


def _diagnostic(severity):
    return Diagnostic(
        range=Range(Position(0, 0), Position(0, 1)),
        message="x",
        severity=severity,
    )


@pytest.fixture
def scope_status_fn(fake_ls):
    """The registered trlc/scopeStatus handler."""
    status_mod.register(fake_ls)
    return fake_ls.get_handler(status_mod.SCOPE_STATUS_METHOD)


@pytest.fixture
def scope_files_fn(fake_ls):
    """The registered trlc/scopeFiles handler."""
    status_mod.register(fake_ls)
    return fake_ls.get_handler(status_mod.SCOPE_FILES_METHOD)


class TestScopeStatus:
    def test_returns_none_for_uri_outside_any_scope(
        self, fake_ls, scope_status_fn
    ):
        """An unopened/unknown uri has no scope yet — client hides its item."""
        params = status_mod.ScopeStatusParams(uri="file:///not/opened.trlc")
        result = scope_status_fn(fake_ls, params)
        assert result is None

    def test_returns_scope_details_for_an_open_uri(
        self, fake_ls, scope_status_fn, rsl_path
    ):
        """A known uri reports its scope's mode, file count, and open count."""
        uri = uri_from_file(rsl_path)
        params = status_mod.ScopeStatusParams(uri=uri)

        result = scope_status_fn(fake_ls, params)

        assert result["scopeId"] == "test-scope"
        assert result["parseMode"] == ParseMode.WORKSPACE.value
        assert result["verifyMode"] is True
        # fake_ls's fixture context has two files (rsl + trlc) and both are
        # opened via ls.open_scope, so both counts land on 2.
        assert result["fileCount"] == 2
        assert result["openFileCount"] == 2
        assert result["errorCount"] == 0
        assert result["warningCount"] == 0

    def test_counts_diagnostics_by_severity(
        self, fake_ls, scope_status_fn, rsl_path, trlc_path
    ):
        """Error/warning counts are tallied across every file in the scope."""
        uri = uri_from_file(rsl_path)
        context = fake_ls.get_context_for_uri(uri)
        context.result = ParseResult(
            vsm=context.snapshot().vsm,
            diagnostics={
                uri_from_file(rsl_path): [
                    _diagnostic(DiagnosticSeverity.Error),
                    _diagnostic(DiagnosticSeverity.Warning),
                ],
                uri_from_file(trlc_path): [
                    _diagnostic(DiagnosticSeverity.Warning),
                ],
            },
        )

        result = scope_status_fn(
            fake_ls, status_mod.ScopeStatusParams(uri=uri)
        )

        assert result["errorCount"] == 1
        assert result["warningCount"] == 2

    def test_reports_the_config_applicable_to_the_uri(
        self, fake_ls, scope_status_fn, rsl_path
    ):
        """parseMode/verifyMode come from get_config_for_uri, not a hardcoded default."""
        fake_ls.config = replace(
            fake_ls.config, parse_mode=ParseMode.DIRECTORY, verify_mode=False
        )

        result = scope_status_fn(
            fake_ls, status_mod.ScopeStatusParams(uri=uri_from_file(rsl_path))
        )

        assert result["parseMode"] == ParseMode.DIRECTORY.value
        assert result["verifyMode"] is False

    def test_fallback_reason_is_none_when_no_fallback_happened(
        self, fake_ls, scope_status_fn, rsl_path
    ):
        result = scope_status_fn(
            fake_ls, status_mod.ScopeStatusParams(uri=uri_from_file(rsl_path))
        )
        assert result["fallbackReason"] is None

    def test_reports_the_fallback_reason_when_set(
        self, fake_ls, scope_status_fn, rsl_path
    ):
        """When BAZEL mode's discover() fell back to a different strategy
        this cycle, the reason is surfaced so the status bar can show it —
        not silently reported as if the configured mode ran normally."""
        uri = uri_from_file(rsl_path)
        context = fake_ls.get_context_for_uri(uri)
        context.result = ParseResult(
            vsm=context.snapshot().vsm,
            fallback_reason="No TRLC targets found in Bazel query",
        )

        result = scope_status_fn(
            fake_ls, status_mod.ScopeStatusParams(uri=uri)
        )

        assert (
            result["fallbackReason"] == "No TRLC targets found in Bazel query"
        )


class TestScopeFiles:
    def test_returns_none_for_uri_outside_any_scope(
        self, fake_ls, scope_files_fn
    ):
        """An unopened/unknown uri has no scope yet."""
        params = status_mod.ScopeStatusParams(uri="file:///not/opened.trlc")
        result = scope_files_fn(fake_ls, params)
        assert result is None

    def test_returns_every_file_path_in_the_scope(
        self, fake_ls, scope_files_fn, rsl_path, trlc_path
    ):
        """The full (sorted) set of paths in the uri's scope is returned —
        not just a count, so the client can list/open them."""
        uri = uri_from_file(rsl_path)
        params = status_mod.ScopeStatusParams(uri=uri)

        result = scope_files_fn(fake_ls, params)

        assert result["files"] == sorted([rsl_path, trlc_path])
