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
"""Integration tests for textDocument/publishDiagnostics."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

from lsprotocol import types

from .conftest import TRLC_TEXT as VALID_TEXT
from .content import (
    INVALID_RSL_TEXT,
    RSL_TEXT,
)
from .content import (
    INVALID_TRLC_TEXT as INVALID_TEXT,
)
from .lsp_helpers import (
    change_document,
    clear_diagnostics,
    close_document,
    open_document,
    wait_for_diagnostics,
)


class TestDiagnostics:
    async def test_valid_file_produces_no_diagnostics(
        self, client, types_uri, sample_uri
    ):
        # Given: a schema file and a fully valid instance file
        # When: both are opened and the server publishes diagnostics
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, VALID_TEXT)
        diags = await wait_for_diagnostics(client, sample_uri)
        # Then: no diagnostics are reported for the valid instance file
        assert diags == []

    async def test_invalid_file_produces_error_diagnostic(
        self, client, types_uri, invalid_uri
    ):
        # This scenario is mirrored in tests/e2e/e2e_smoke.json
        # (diagnosticsOnInvalidFile) for the VSCode e2e smoke suite — keep
        # both in sync.
        # Given: a schema file and an instance file missing a required field
        # When: both are opened and the server publishes diagnostics
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, invalid_uri, INVALID_TEXT)
        diags = await wait_for_diagnostics(client, invalid_uri)
        # Then: at least one error diagnostic is reported
        assert len(diags) >= 1
        assert any(d.severity == types.DiagnosticSeverity.Error for d in diags)

    async def test_fixing_error_clears_diagnostics(
        self, client, types_uri, sample_uri
    ):
        """Changing an invalid document to valid clears its diagnostics."""
        # Given: an instance file open with invalid content, already diagnosed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, INVALID_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # wait_for_diagnostics only waits until the uri key is *present* in
        # client.diagnostics, not until a *fresh* notification arrives — it
        # already has an entry from the cycle above, so without clearing it
        # here the next wait would return immediately with the stale error.
        clear_diagnostics(client, sample_uri)
        # When: the document is edited to fully valid content
        change_document(client, sample_uri, VALID_TEXT, version=2)
        diags = await wait_for_diagnostics(client, sample_uri)
        # Then: the previously-reported diagnostics are cleared
        assert diags == []

    async def test_introducing_error_produces_diagnostic(
        self, client, types_uri, sample_uri
    ):
        """Changing a valid document to invalid produces diagnostics."""
        # Given: an instance file open with valid content, already diagnosed clean
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, VALID_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # See test_fixing_error_clears_diagnostics: clear the stale cache
        # entry so this second wait blocks for the actual post-change publish.
        clear_diagnostics(client, sample_uri)
        # When: the document is edited to invalid content
        change_document(client, sample_uri, INVALID_TEXT, version=2)
        diags = await wait_for_diagnostics(client, sample_uri)
        # Then: an error diagnostic is now reported
        assert any(d.severity == types.DiagnosticSeverity.Error for d in diags)

    async def test_close_then_reopen_re_parses(
        self, client, types_uri, sample_uri
    ):
        # Given: a valid instance file, opened and diagnosed clean, then closed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, VALID_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        close_document(client, sample_uri)
        clear_diagnostics(client, sample_uri)
        # When: the same file is reopened
        open_document(client, sample_uri, VALID_TEXT)
        diags = await wait_for_diagnostics(client, sample_uri)
        # Then: it is re-parsed clean, with no stale diagnostics
        assert diags == []

    async def test_rsl_error_reported_in_rsl_file(self, client, types_uri):
        """Errors in the .rsl schema file are reported against the .rsl URI."""
        # Given: a schema file referencing an undeclared type
        # When: the schema file is opened and diagnosed
        open_document(client, types_uri, INVALID_RSL_TEXT)
        diags = await wait_for_diagnostics(client, types_uri)
        # Then: the error is reported against the .rsl file itself
        assert len(diags) >= 1
