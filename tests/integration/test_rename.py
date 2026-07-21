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
"""Integration tests for textDocument/rename."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

from .conftest import FIXTURES_DIR, RSL_TEXT, TRLC_TEXT
from .lsp_helpers import (
    clear_diagnostics,
    execute_command,
    open_document,
    rename,
    wait_for_diagnostics,
)


class TestRename:
    async def test_rename_in_workspace_mode_returns_empty_edit(
        self, client, types_uri, sample_uri
    ):
        """In the default WORKSPACE mode, rename is rejected."""
        # Given: a schema file and an instance file, parsed in WORKSPACE mode (default)
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: rename is requested on the 'MyRecord' type declaration
        result = await rename(
            client, types_uri, line=2, col=5, new_name="NewRecord"
        )
        # Then: an empty edit is returned (rename requires directory/repo/bazel mode)
        assert result is not None
        changes = result.document_changes or result.changes or []
        assert len(changes) == 0

    async def test_rename_on_builtin_returns_empty(
        self, client, types_uri, sample_uri
    ):
        """Builtins (Integer, String…) cannot be renamed."""
        # Given: a schema file and an instance file, both open and parsed
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        # When: rename is requested on the builtin 'Integer' type
        result = await rename(
            client, types_uri, line=3, col=6, new_name="NewInt"
        )
        # Then: an empty edit is returned
        changes = (
            (result.document_changes if result else None)
            or (result.changes if result else None)
            or []
        )
        assert len(changes) == 0

    async def test_rename_with_existing_errors_returns_empty(
        self, client, types_uri, invalid_uri
    ):
        """Rename is blocked when there are parse errors."""
        # Given: a schema file and an instance file with an unresolved parse error
        open_document(client, types_uri, RSL_TEXT)
        open_document(
            client,
            invalid_uri,
            (FIXTURES_DIR / "invalid_scope" / "invalid.trlc").read_text(
                encoding="utf-8"
            ),
        )
        await wait_for_diagnostics(client, invalid_uri)

        try:
            await execute_command(client, "extension.parseAll")
        except Exception:  # pylint: disable=broad-exception-caught  # fire-and-forget command, any failure is tolerable
            pass
        clear_diagnostics(client, invalid_uri)
        try:
            await wait_for_diagnostics(client, invalid_uri, timeout=15)
        except TimeoutError:
            pass

        # When: rename is requested on the 'MyRecord' type declaration
        result = await rename(
            client, types_uri, line=2, col=5, new_name="NewRecord"
        )
        # Then: an empty edit is returned because errors remain unresolved
        changes = (
            (result.document_changes if result else None)
            or (result.changes if result else None)
            or []
        )
        assert len(changes) == 0
