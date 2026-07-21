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
"""Unit tests for trlc_lsp.handlers.rename."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

# pylint: disable=protected-access  # tests legitimately access private members to verify internal state

# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

from dataclasses import replace

import pytest
from lsprotocol.types import (
    TEXT_DOCUMENT_RENAME,
    Diagnostic,
    DiagnosticSeverity,
    Position,
    Range,
    RenameParams,
    TextDocumentIdentifier,
)

from server.handlers import rename as rename_mod
from server.parse_context import ParseResult
from server.server_config import ParseMode
from server.token_utils import uri_from_file


def _rename_params(uri, line, col, new_name="NewName"):
    return RenameParams(
        text_document=TextDocumentIdentifier(uri=uri),
        position=Position(line=line, character=col),
        new_name=new_name,
    )


@pytest.fixture
def rename_fn(fake_ls):
    """Rename fn."""
    rename_mod.register(fake_ls)
    return fake_ls.get_handler(TEXT_DOCUMENT_RENAME)


class TestRename:
    def test_rename_in_workspace_mode_returns_empty_edit_with_warning(
        self, fake_ls, rename_fn, rsl_path
    ):
        """Rename is blocked in WORKSPACE mode (partial parse)."""
        # Given: the default config, which is WORKSPACE mode
        # default config is WORKSPACE
        assert fake_ls.config.parse_mode is ParseMode.WORKSPACE
        uri = uri_from_file(rsl_path)
        params = _rename_params(uri, line=2, col=5)
        # When: rename is requested
        result = rename_fn(fake_ls, params)
        # Then: an empty edit with a warning message is returned
        # Returns a WorkspaceEdit with no changes and a warning message
        assert result is not None
        assert not result.document_changes
        assert any(
            "full" in m.message.lower() or "rename" in m.message.lower()
            for m in fake_ls._msgs
        )

    def test_rename_with_existing_errors_blocked(
        self, fake_ls, rename_fn, rsl_path
    ):
        """Rename must be blocked when the workspace has errors."""
        # Given: REPO mode (passes the parse-mode check) and a scope whose
        # last parse cycle reported an error
        # Switch to REPO mode so we pass the parse-mode check
        fake_ls.config = replace(fake_ls.config, parse_mode=ParseMode.REPO)

        uri = uri_from_file(rsl_path)
        # Simulate the ParseEngine having just published a parse cycle whose
        # scope contained an error — done via the same wholesale ParseResult
        # swap the engine itself uses (never mutate .diagnostics in place).
        context = fake_ls.store.get_context("test-scope")
        context.result = ParseResult(
            vsm=context.snapshot().vsm,
            diagnostics={
                uri: [
                    Diagnostic(
                        range=Range(
                            start=Position(line=0, character=0),
                            end=Position(line=0, character=1),
                        ),
                        message="existing error",
                        severity=DiagnosticSeverity.Error,
                    )
                ]
            },
        )

        # When: rename is requested
        params = _rename_params(uri, line=2, col=5)
        result = rename_fn(fake_ls, params)
        # Then: an empty edit is returned, with a message about the error
        assert result is not None
        assert not result.document_changes
        assert any(
            "error" in m.message.lower() or "resolve" in m.message.lower()
            for m in fake_ls._msgs
        )

    def test_rename_on_builtin_blocked_with_message(
        self, fake_ls, rename_fn, rsl_path
    ):
        """Rename of a builtin (Integer) must be rejected."""
        # Given: REPO mode, cursor on the builtin 'Integer' type
        fake_ls.config = replace(fake_ls.config, parse_mode=ParseMode.REPO)

        uri = uri_from_file(rsl_path)
        params = _rename_params(
            uri, line=3, col=6
        )  # 'Integer' (Builtin_Integer)
        # When: rename is requested
        result = rename_fn(fake_ls, params)
        # Then: an empty edit is returned
        assert result is not None
        assert not result.document_changes

    def test_rename_on_unparsed_file_returns_none(self, fake_ls, rename_fn):
        # Given: REPO mode, and a uri that was never parsed
        fake_ls.config = replace(fake_ls.config, parse_mode=ParseMode.REPO)

        params = _rename_params("file:///unknown.rsl", line=0, col=0)
        # When: rename is requested against that uri
        result = rename_fn(fake_ls, params)
        # Then: no result is returned
        assert result is None

    def test_rename_record_type_succeeds_across_files(
        self, fake_ls, rename_fn, rsl_path, trlc_path
    ):
        """Happy path: renaming 'MyRecord' (defined in types.rsl, referenced
        in sample.trlc) finds both occurrences and builds a WorkspaceEdit
        that renames each of them, in each file, to new_name."""
        # Given: REPO mode (passes the parse-mode check), no existing errors,
        # cursor on 'MyRecord' at its declaration in types.rsl
        fake_ls.config = replace(fake_ls.config, parse_mode=ParseMode.REPO)
        rsl_uri = uri_from_file(rsl_path)
        trlc_uri = uri_from_file(trlc_path)
        params = _rename_params(
            rsl_uri, line=2, col=5, new_name="RenamedRecord"
        )

        # When: rename is requested
        result = rename_fn(fake_ls, params)

        # Then: a non-empty WorkspaceEdit covering both files is returned —
        # the definition in types.rsl and the usage in sample.trlc.
        assert result is not None
        assert result.document_changes
        edited_uris = {
            edit.text_document.uri for edit in result.document_changes
        }
        assert edited_uris == {rsl_uri, trlc_uri}

        for edit in result.document_changes:
            assert edit.edits
            for text_edit in edit.edits:
                assert text_edit.new_text == "RenamedRecord"

        # No warning shown — this is the success path, not a guard rejection.
        assert not fake_ls._msgs
