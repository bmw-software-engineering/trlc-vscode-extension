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
"""Unit tests for server.handlers.preview."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=redefined-outer-name  # pytest fixture names must match

import os

import pytest

from server.handlers import preview as preview_mod
from server.message_handler import Vscode_Message_Handler
from server.parse_context import ParseResult
from server.token_utils import uri_from_file
from tests.unit.handlers.conftest import FakeVSM, _parse_trlc

NOT_PARSED = "File has errors that prevent parsing; fix them in the editor."

LOCK_RSL = """\
package Lock

type Rec {
    x Integer
    y Integer
    o optional Integer
}

checks Rec {
    x > 0, error "x must be positive"
}
"""


def _open_real(fake_ls, tmp_path, content):
    """Really parse *content* (error recovery on, as in production) and
    open it in its own scope carrying that parse's real diagnostics."""
    vmh = Vscode_Message_Handler()
    stab, all_files, _, trlc_file = _parse_trlc(
        LOCK_RSL, "lock.rsl", content, "lock.trlc", tmp_path, mh=vmh
    )
    vsm = FakeVSM(stab=stab, all_files=all_files)
    uri = uri_from_file(trlc_file)
    fake_ls.open_scope("lock-scope", uri, content, vsm)
    fake_ls.get_context_for_uri(uri).result = ParseResult(
        vsm=vsm, diagnostics=vmh.diagnostics
    )
    return uri, vmh.diagnostics


def _set_o(uri, value="7"):
    return preview_mod.PreviewSetFieldParams(
        uri=uri, record="r", field="o", value=value
    )


@pytest.fixture
def handlers(fake_ls):
    preview_mod.register(fake_ls)
    return (
        fake_ls.get_handler(preview_mod.PREVIEW_MODEL_METHOD),
        fake_ls.get_handler(preview_mod.PREVIEW_SET_FIELD_METHOD),
        fake_ls.get_handler(preview_mod.PREVIEW_ADD_RECORD_METHOD),
    )


def _set(uri, value="9"):
    return preview_mod.PreviewSetFieldParams(
        uri=uri, record="example", field="x", value=value
    )


class TestPreviewModel:
    def test_model_for_open_file(self, fake_ls, handlers, trlc_path):
        model_fn, _, _ = handlers
        uri = uri_from_file(trlc_path)
        model = model_fn(fake_ls, preview_mod.PreviewModelParams(uri=uri))
        assert model["nodes"][0]["name"] == "example"

    def test_model_unavailable_for_unparsed_file(
        self, fake_ls, handlers, trlc_path
    ):
        model_fn, _, _ = handlers
        broken = os.path.join(os.path.dirname(trlc_path), "broken.trlc")
        uri = uri_from_file(broken)
        fake_ls.open_scope(
            "test-scope",
            uri,
            "package Types\nMyRecord {{{",
            fake_ls.get_context_for_uri(uri_from_file(trlc_path))
            .snapshot()
            .vsm,
        )
        model = model_fn(fake_ls, preview_mod.PreviewModelParams(uri=uri))
        assert model["editable"] is False
        assert "errors" in model["reason"]

    def test_model_unavailable_without_scope(self, fake_ls, handlers):
        model_fn, _, _ = handlers
        model = model_fn(
            fake_ls,
            preview_mod.PreviewModelParams(uri="file:///nowhere.trlc"),
        )
        assert model["editable"] is False
        assert "scope" in model["reason"]


class TestPreviewSetField:
    def test_set_field_returns_workspace_edit(
        self, fake_ls, handlers, trlc_path
    ):
        _, set_fn, _ = handlers
        uri = uri_from_file(trlc_path)
        fake_ls._uri_versions[uri] = 42  # pylint: disable=protected-access
        result = set_fn(fake_ls, _set(uri))
        (change,) = result["edit"]["documentChanges"]
        # Versioned against the buffer the parse matched.
        assert change["textDocument"] == {"uri": uri, "version": 42}
        assert change["edits"][0]["newText"] == "9"
        assert "changes" not in result["edit"]

    def test_set_field_rejected_when_stale(self, fake_ls, handlers, trlc_path):
        _, set_fn, _ = handlers
        uri = uri_from_file(trlc_path)
        fake_ls._uri_contents[uri] += "\n"  # pylint: disable=protected-access
        result = set_fn(fake_ls, _set(uri))
        assert "error" in result
        assert result["code"] == "stale"
        assert "edit" not in result

    def test_other_errors_carry_no_code(self, fake_ls, handlers, trlc_path):
        _, set_fn, _ = handlers
        uri = uri_from_file(trlc_path)
        result = set_fn(fake_ls, _set(uri, value="abc"))
        assert "error" in result
        assert "code" not in result

    def test_set_field_user_error_is_returned(
        self, fake_ls, handlers, trlc_path
    ):
        _, set_fn, _ = handlers
        uri = uri_from_file(trlc_path)
        result = set_fn(fake_ls, _set(uri, value="abc"))
        assert "error" in result


# pylint: disable-next=too-few-public-methods  # single scenario
class TestPreviewAddRecord:
    def test_add_record_returns_edit_or_error(
        self, fake_ls, handlers, trlc_path
    ):
        _, _, add_fn = handlers
        uri = uri_from_file(trlc_path)
        ok = add_fn(
            fake_ls,
            preview_mod.PreviewAddRecordParams(
                uri=uri, sectionId=None, type="MyRecord", name="fresh"
            ),
        )
        assert "edit" in ok
        change = ok["edit"]["documentChanges"][0]
        assert change["textDocument"] == {"uri": uri, "version": 1}
        dup = add_fn(
            fake_ls,
            preview_mod.PreviewAddRecordParams(
                uri=uri, sectionId=None, type="MyRecord", name="example"
            ),
        )
        assert "error" in dup


class TestPreviewLockRule:
    """Read-only rule, exercised with REAL error-recovering parses:
    syntax/semantic (SYS_ERROR) errors lock the file, except a missing
    required component; failing user checks never lock."""

    def test_syntax_error_locks_but_shows_records(
        self, fake_ls, handlers, tmp_path
    ):
        model_fn, set_fn, add_fn = handlers
        content = (
            "package Lock\n"
            "Rec r { x = 1 y = 2 }\n"
            "Rec bad {\n    x = = 1\n    y = 2\n}\n"
        )
        uri, _ = _open_real(fake_ls, tmp_path, content)
        model = model_fn(fake_ls, preview_mod.PreviewModelParams(uri=uri))
        assert model["editable"] is False
        assert model["reason"] == NOT_PARSED
        assert [n["name"] for n in model["nodes"]] == ["r", "bad"]
        assert set_fn(fake_ls, _set_o(uri)) == {"error": NOT_PARSED}
        added = add_fn(
            fake_ls,
            preview_mod.PreviewAddRecordParams(
                uri=uri, sectionId=None, type="Rec", name="fresh"
            ),
        )
        assert added == {"error": NOT_PARSED}

    def test_unclosed_section_builds_locked_model(
        self, fake_ls, handlers, tmp_path
    ):
        model_fn, set_fn, _ = handlers
        content = (
            'package Lock\nsection "A" {\nRec r {\n    x = 1\n    y = 2\n}\n'
        )
        uri, _ = _open_real(fake_ls, tmp_path, content)
        model = model_fn(fake_ls, preview_mod.PreviewModelParams(uri=uri))
        assert model["editable"] is False
        (section,) = model["nodes"]
        assert section["closed"] is False
        assert [c["name"] for c in section["children"]] == ["r"]
        assert set_fn(fake_ls, _set_o(uri)) == {"error": NOT_PARSED}

    def test_missing_required_component_stays_editable(
        self, fake_ls, handlers, tmp_path
    ):
        model_fn, set_fn, _ = handlers
        content = "package Lock\nRec r {\n    x = 1\n}\n"
        uri, diagnostics = _open_real(fake_ls, tmp_path, content)
        # Pins TRLC's wording: if it changes, the exemption would silently
        # stop working, so fail here instead.
        messages = [d.message for ds in diagnostics.values() for d in ds]
        assert any(
            m.startswith(preview_mod.REQUIRED_COMPONENT_PREFIX)
            for m in messages
        ), messages
        model = model_fn(fake_ls, preview_mod.PreviewModelParams(uri=uri))
        assert model["editable"] is True
        result = set_fn(
            fake_ls,
            preview_mod.PreviewSetFieldParams(
                uri=uri, record="r", field="y", value="2"
            ),
        )
        assert "edit" in result, result

    def test_failing_user_check_stays_editable(
        self, fake_ls, handlers, tmp_path
    ):
        model_fn, set_fn, _ = handlers
        content = "package Lock\nRec r {\n    x = 0\n    y = 2\n}\n"
        uri, diagnostics = _open_real(fake_ls, tmp_path, content)
        kinds = [d.data["kind"] for ds in diagnostics.values() for d in ds]
        assert "USER_ERROR" in kinds, kinds
        model = model_fn(fake_ls, preview_mod.PreviewModelParams(uri=uri))
        assert model["editable"] is True
        assert "edit" in set_fn(fake_ls, _set_o(uri))
