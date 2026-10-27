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
"""Unit tests for server.preview_model."""

# pylint: disable=missing-function-docstring,redefined-outer-name

import pytest
from trlc.errors import Message_Handler
from trlc.trlc import Source_Manager

from server.preview_model import build_preview_model, unavailable_model

RSL = """\
package Types

enum Color {
    Red
    Green
    Blue
}

type MyRecord {
    x Integer
    y Integer
    text optional String
    c optional Color
}
"""

OTHER_RSL = """\
package Other

type T {
    v Integer
}
"""

TRLC = """\
package Types
import Other
section "A" {
    MyRecord r1 {
        x = 1  // keep me
        y = 2
        c = Color.Green
    }
    section "A" {
        MyRecord r2 { x = 3 y = 4 }
    }
}
MyRecord r3 { x = 5 y = 6 }
"""


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("preview_model")
    files = {
        "other.rsl": OTHER_RSL,
        "types.rsl": RSL,
        "a.trlc": TRLC,
    }
    sm = Source_Manager(Message_Handler())
    for name, text in files.items():
        (tmp / name).write_text(text, encoding="utf-8")
        sm.register_file(str(tmp / name), text)
    assert sm.process() is not None
    return build_preview_model(
        "file:///a.trlc", sm.all_files[str(tmp / "a.trlc")]
    )


def _rec(node):
    return {k: node[k] for k in ("kind", "name")}


def test_tree_nests_sections_and_records(model):
    assert model["package"] == "Types"
    assert model["editable"] is True
    assert model["reason"] is None
    nodes = model["nodes"]
    assert [n["kind"] for n in nodes] == ["section", "record"]
    sec = nodes[0]
    assert (sec["title"], sec["depth"], sec["sectionId"]) == ("A", 1, 0)
    assert [c["kind"] for c in sec["children"]] == ["record", "section"]
    assert sec["children"][0]["name"] == "r1"
    inner = sec["children"][1]
    assert inner["depth"] == 2
    assert [c["name"] for c in inner["children"]] == ["r2"]
    assert nodes[1]["name"] == "r3"
    assert nodes[1]["type"] == "MyRecord"
    assert nodes[1]["line"] == 12


def test_fields_follow_declaration_order_and_include_unset_optional(model):
    r1 = model["nodes"][0]["children"][0]
    assert [f["name"] for f in r1["fields"]] == ["x", "y", "text", "c"]
    by_name = {f["name"]: f for f in r1["fields"]}
    assert by_name["x"]["value"] == "1"
    assert by_name["x"]["kind"] == "integer"
    assert by_name["x"]["editable"] is True
    assert by_name["text"]["value"] is None
    assert by_name["text"]["optional"] is True


def test_enum_field_lists_literals(model):
    r1 = model["nodes"][0]["children"][0]
    field = {f["name"]: f for f in r1["fields"]}["c"]
    assert field["kind"] == "enum"
    assert field["value"] == "Green"
    assert field["enumValues"] == ["Red", "Green", "Blue"]
    assert r1["fields"][0]["enumValues"] is None


def test_record_types_lists_local_and_imported(model):
    assert model["recordTypes"] == ["MyRecord", "Other.T"]


def test_unavailable_model():
    m = unavailable_model("file:///x.trlc", "broken")
    assert m["nodes"] is None
    assert m["editable"] is False
    assert m["reason"] == "broken"
    assert m["uri"] == "file:///x.trlc"


def _build(tmp_path, rsl, src):
    sm = Source_Manager(Message_Handler())
    for name, text in (("p.rsl", rsl), ("p.trlc", src)):
        (tmp_path / name).write_text(text, encoding="utf-8")
        sm.register_file(str(tmp_path / name), text)
    sm.process()
    return build_preview_model(
        "file:///p.trlc", sm.all_files[str(tmp_path / "p.trlc")]
    )


def test_record_missing_required_component_appears(tmp_path):
    rsl = "package P\ntype T {\n    a Integer\n    b Integer\n}\n"
    src = "package P\nT r1 { a = 1 }\n"
    nodes = _build(tmp_path, rsl, src)["nodes"]
    assert [n["name"] for n in nodes] == ["r1"]
    fields = {f["name"]: f for f in nodes[0]["fields"]}
    assert fields["a"]["value"] == "1"
    assert fields["b"]["value"] is None
    assert fields["b"]["optional"] is False


def test_frozen_component_shows_value(tmp_path):
    rsl = (
        "package P\ntype B {\n    x Integer\n}\n"
        "type D extends B {\n    freeze x = 7\n}\n"
    )
    src = "package P\nD r1 { }\n"
    nodes = _build(tmp_path, rsl, src)["nodes"]
    field = nodes[0]["fields"][0]
    assert field["value"] == "7"
    assert field["editable"] is False


def test_array_value_is_source_text(tmp_path):
    rsl = "package P\ntype T {\n    tags optional String [0 .. *]\n}\n"
    src = 'package P\nT r1 { tags = ["a", "b"] }\n'
    nodes = _build(tmp_path, rsl, src)["nodes"]
    assert nodes[0]["fields"][0]["value"] == '["a", "b"]'


def test_sections_open_at_eof_keep_their_records(tmp_path):
    # TRLC's error recovery keeps a file whose sections are never closed.
    rsl = "package P\ntype T {\n    a Integer\n}\n"
    src = (
        'package P\nsection "A" {\nT r1 { a = 1 }\n'
        'section "B" {\nT r2 { a = 2 }\n'
    )
    nodes = _build(tmp_path, rsl, src)["nodes"]
    assert len(nodes) == 1
    outer = nodes[0]
    assert outer["title"] == "A"
    assert outer["closed"] is False
    assert outer["children"][0]["name"] == "r1"
    inner = outer["children"][1]
    assert inner["title"] == "B"
    assert inner["closed"] is False
    assert [c["name"] for c in inner["children"]] == ["r2"]


def test_closed_sections_are_marked_closed(model):
    assert model["nodes"][0]["closed"] is True
