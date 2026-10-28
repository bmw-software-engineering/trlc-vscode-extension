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
"""Integration tests for the trlc/preview* custom requests."""

# pylint: disable=missing-class-docstring,missing-function-docstring

from .conftest import RSL_TEXT, TRLC_TEXT
from .content import INVALID_TRLC_TEXT
from .lsp_helpers import open_document, wait_for_diagnostics


class TestPreview:
    async def test_model_and_set_field(self, client, types_uri, sample_uri):
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        model = await client.protocol.send_request_async(
            "trlc/previewModel", {"uri": sample_uri}
        )
        assert model.editable is True
        assert model.version == 1
        node = model.nodes[0]
        assert node.name == "example"
        field = next(f for f in node.fields if f.name == "x")
        assert field.value == "1"

        result = await client.protocol.send_request_async(
            "trlc/previewSetField",
            {
                "uri": sample_uri,
                "record": "example",
                "field": "x",
                "value": "5",
            },
        )
        # A versioned document edit: only valid against the parsed buffer.
        (change,) = result.edit.documentChanges
        assert change.textDocument.uri == sample_uri
        assert change.textDocument.version == 1
        assert change.edits[0].newText == "5"

        added = await client.protocol.send_request_async(
            "trlc/previewAddRecord",
            {
                "uri": sample_uri,
                "sectionId": None,
                "type": "MyRecord",
                "name": "fresh",
                "modelVersion": model.version,
            },
        )
        assert hasattr(added, "edit"), added
        edit = added.edit.documentChanges[0].edits[0]
        assert edit.newText.startswith("\nMyRecord fresh")

    async def test_add_record_into_section_uses_camel_case_section_id(
        self, client, types_uri, sample_uri
    ):
        text = 'package Types\n\nsection "S" {\n}\n'
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, text)
        await wait_for_diagnostics(client, sample_uri)
        model = await client.protocol.send_request_async(
            "trlc/previewModel", {"uri": sample_uri}
        )
        section_id = model.nodes[0].sectionId

        added = await client.protocol.send_request_async(
            "trlc/previewAddRecord",
            {
                "uri": sample_uri,
                "sectionId": section_id,
                "type": "MyRecord",
                "name": "inner",
                "modelVersion": model.version,
            },
        )

        edit = added.edit.documentChanges[0].edits[0]
        # Inside the section: on the closing-brace line (3), not at file end (5).
        assert edit.range.start.line == 3

    async def test_syntax_error_locks_the_file(
        self, client, types_uri, sample_uri
    ):
        text = TRLC_TEXT.replace("x = 1", "x = = 1")
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, text)
        await wait_for_diagnostics(client, sample_uri)

        model = await client.protocol.send_request_async(
            "trlc/previewModel", {"uri": sample_uri}
        )
        # Error recovery keeps the file parsed: shown, but read-only.
        assert model.editable is False
        assert "errors" in model.reason
        assert model.nodes[0].name == "example"

        result = await client.protocol.send_request_async(
            "trlc/previewSetField",
            {
                "uri": sample_uri,
                "record": "example",
                "field": "y",
                "value": "5",
            },
        )
        assert "errors" in result.error

    async def test_array_field_takes_a_list(
        self, client, types_uri, sample_uri
    ):
        rsl = "package Types\ntype Tagged {\n    tags String [0 .. *]\n}\n"
        open_document(client, types_uri, rsl)
        open_document(
            client, sample_uri, 'package Types\nTagged t { tags = ["a"] }\n'
        )
        await wait_for_diagnostics(client, sample_uri)
        model = await client.protocol.send_request_async(
            "trlc/previewModel", {"uri": sample_uri}
        )
        (field,) = model.nodes[0].fields
        assert field.kind == "array"
        assert field.elementKind == "string"
        assert field.items == ["a"]

        result = await client.protocol.send_request_async(
            "trlc/previewSetField",
            {
                "uri": sample_uri,
                "record": "t",
                "field": "tags",
                "value": ["a", "b c"],
            },
        )
        assert result.edit.documentChanges[0].edits[0].newText == (
            '["a", "b c"]'
        )

    async def test_missing_required_value_stays_editable(
        self, client, types_uri, sample_uri
    ):
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, INVALID_TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)

        model = await client.protocol.send_request_async(
            "trlc/previewModel", {"uri": sample_uri}
        )
        assert model.editable is True


class TestPreviewStructure:
    async def test_insert_section(self, client, types_uri, sample_uri):
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)
        result = await client.protocol.send_request_async(
            "trlc/previewInsertSection",
            {
                "uri": sample_uri,
                "sectionId": None,
                "title": "New",
                "modelVersion": 1,
            },
        )
        edits = result.edit.documentChanges[0].edits
        assert any('section "New"' in e.newText for e in edits)

    async def test_move_with_malformed_item_is_rejected(
        self, client, types_uri, sample_uri
    ):
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)
        result = await client.protocol.send_request_async(
            "trlc/previewMove",
            {
                "uri": sample_uri,
                "item": {"kind": "bogus"},
                "targetSectionId": None,
                "before": None,
                "modelVersion": 1,
            },
        )
        assert result.error == "Invalid request."

    async def test_outdated_model_version_is_refused(
        self, client, types_uri, sample_uri
    ):
        open_document(client, types_uri, RSL_TEXT)
        open_document(client, sample_uri, TRLC_TEXT)
        await wait_for_diagnostics(client, sample_uri)
        result = await client.protocol.send_request_async(
            "trlc/previewInsertSection",
            {
                "uri": sample_uri,
                "sectionId": None,
                "title": "New",
                "modelVersion": 0,
            },
        )
        assert result.code == "outdated"
