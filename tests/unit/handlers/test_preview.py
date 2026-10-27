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
from server.token_utils import uri_from_file


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
        result = set_fn(fake_ls, _set(uri))
        assert result["edit"]["changes"][uri][0]["newText"] == "9"

    def test_set_field_rejected_when_stale(self, fake_ls, handlers, trlc_path):
        _, set_fn, _ = handlers
        uri = uri_from_file(trlc_path)
        fake_ls._uri_contents[uri] += "\n"  # pylint: disable=protected-access
        result = set_fn(fake_ls, _set(uri))
        assert "error" in result
        assert "edit" not in result

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
        dup = add_fn(
            fake_ls,
            preview_mod.PreviewAddRecordParams(
                uri=uri, sectionId=None, type="MyRecord", name="example"
            ),
        )
        assert "error" in dup
