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
"""Unit tests for code action handler."""

# pylint: disable=missing-class-docstring,missing-function-docstring

from lsprotocol.types import (
    CodeActionContext,
    CodeActionParams,
    Diagnostic,
    Position,
    Range,
    TextDocumentIdentifier,
)

from server.handlers import code_actions


def test_did_you_mean_suggestion(fake_ls):
    """Test that 'did you mean X?' diagnostics produce quick-fix code actions."""
    # Given: the code-action handler registered, and a "did you mean" diagnostic
    code_actions.register(fake_ls)
    handler = fake_ls.get_handler("textDocument/codeAction")

    uri = "file:///test.trlc"
    diagnostic = Diagnostic(
        range=Range(
            start=Position(line=0, character=0),
            end=Position(line=0, character=5),
        ),
        message="unknown symbol foo, did you mean bar?",
    )

    params = CodeActionParams(
        text_document=TextDocumentIdentifier(uri=uri),
        range=diagnostic.range,
        context=CodeActionContext(diagnostics=[diagnostic]),
    )

    # When: code actions are requested for that diagnostic
    actions = handler(fake_ls, params)
    # Then: a single quick-fix action suggesting the corrected name is returned
    assert actions is not None
    assert len(actions) == 1
    assert actions[0].title == "Change to 'bar'"
    assert actions[0].edit.document_changes[0].edits[0].new_text == "bar"


def test_unrelated_diagnostic_no_action(fake_ls):
    """Test that unrelated diagnostics don't produce code actions."""
    # Given: the code-action handler registered, and an unrelated diagnostic
    code_actions.register(fake_ls)
    handler = fake_ls.get_handler("textDocument/codeAction")

    uri = "file:///test.trlc"
    diagnostic = Diagnostic(
        range=Range(
            start=Position(line=0, character=0),
            end=Position(line=0, character=5),
        ),
        message="Some other error",
    )

    params = CodeActionParams(
        text_document=TextDocumentIdentifier(uri=uri),
        range=diagnostic.range,
        context=CodeActionContext(diagnostics=[diagnostic]),
    )

    # When: code actions are requested for that diagnostic
    actions = handler(fake_ls, params)
    # Then: no code actions are produced. Exactly None (not an empty list) —
    # the handler's ternary (`actions if actions else None`) only takes this
    # path once at least one diagnostic was inspected and none matched.
    assert actions is None


def test_no_diagnostics_returns_empty(fake_ls):
    """Test that empty diagnostics list returns no actions."""
    # Given: the code-action handler registered, and no diagnostics in range
    code_actions.register(fake_ls)
    handler = fake_ls.get_handler("textDocument/codeAction")

    uri = "file:///test.trlc"
    params = CodeActionParams(
        text_document=TextDocumentIdentifier(uri=uri),
        range=Range(
            start=Position(line=0, character=0),
            end=Position(line=0, character=5),
        ),
        context=CodeActionContext(diagnostics=[]),
    )

    # When: code actions are requested
    actions = handler(fake_ls, params)
    # Then: exactly an empty list (not None) — an empty diagnostics list
    # takes the early `return actions` before the loop that would otherwise
    # collapse it to None.
    assert actions == []


def test_unknown_symbol_in_unopened_file_offers_open_command(
    fake_ls, tmp_path
):
    """An unknown symbol declared in a closed .trlc file offers to open it."""
    # Given: a scope rooted at tmp_path with an open file referencing a
    # record that only a closed file declares
    code_actions.register(fake_ls)
    handler = fake_ls.get_handler("textDocument/codeAction")
    (tmp_path / "sys").mkdir()
    (tmp_path / "sys" / "far.trlc").write_text(
        "package P\nT far_away { }\n", encoding="utf-8"
    )
    source = "package P\nT here { other = far_away }\n"
    uri = (tmp_path / "here.trlc").as_uri()
    fake_ls.open_scope(tmp_path.as_uri(), uri, source, vsm=None)
    fake_ls.fh.update_files(uri, source)
    diagnostic = Diagnostic(
        range=Range(
            start=Position(line=1, character=17),
            end=Position(line=1, character=25),
        ),
        message="unknown symbol far_away",
    )
    params = CodeActionParams(
        text_document=TextDocumentIdentifier(uri=uri),
        range=diagnostic.range,
        context=CodeActionContext(diagnostics=[diagnostic]),
    )

    # When: code actions are requested for that diagnostic
    actions = handler(fake_ls, params)

    # Then: one quick fix runs the client's open-in-background command
    (action,) = actions
    assert action.title == "Open sys/far.trlc (declares far_away)"
    assert action.kind == "quickfix"
    assert action.diagnostics == [diagnostic]
    assert action.edit is None
    assert action.command.command == "trlc.openFileInBackground"
    assert action.command.arguments[0].endswith("/sys/far.trlc")
