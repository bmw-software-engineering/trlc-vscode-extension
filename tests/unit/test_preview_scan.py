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
"""Unit tests for server.preview_scan."""

# pylint: disable=missing-function-docstring,redefined-outer-name

import pytest
from trlc.errors import Message_Handler
from trlc.trlc import Source_Manager

from server.preview_scan import scan_file

RSL = """\
package Types

type MyRecord {
    x Integer
    y Integer
    text optional String
}
"""

TRLC = """\
package Types
section "A" {
    MyRecord r1 {
        x = 1  // keep me
        y = 2
    }
    section "A" {
        MyRecord r2 { x = 3 y = 4 }
    }
}
MyRecord r3 { x = 5 y = 6 }
"""


@pytest.fixture(scope="module")
def structure(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("preview_scan")
    rsl, trlc = str(tmp / "types.rsl"), str(tmp / "a.trlc")
    (tmp / "types.rsl").write_text(RSL, encoding="utf-8")
    (tmp / "a.trlc").write_text(TRLC, encoding="utf-8")
    sm = Source_Manager(Message_Handler())
    sm.register_file(rsl, RSL)
    sm.register_file(trlc, TRLC)
    assert sm.process() is not None
    return scan_file(sm.all_files[trlc].lexer.tokens)


def test_sections_have_depth_and_parent(structure):
    secs = structure.sections
    assert [s.section_id for s in secs] == [0, 1]
    assert [s.depth for s in secs] == [1, 2]
    assert secs[0].parent_id is None
    assert secs[1].parent_id == 0
    assert structure.section(1) is secs[1]
    assert structure.section(7) is None


def test_duplicate_section_titles_get_distinct_ids(structure):
    assert structure.sections[0].title == structure.sections[1].title == "A"
    assert structure.record("r1").section_id == 0
    assert structure.record("r2").section_id == 1
    assert structure.record("r3").section_id is None


def test_value_span_excludes_trailing_comment(structure):
    span = structure.record("r1").fields["x"]
    tok = structure.tokens[span.value_last]
    assert tok.kind == "INTEGER"
    assert tok.value == 1


def test_inline_record_fields(structure):
    fields = structure.record("r2").fields
    assert list(fields) == ["x", "y"]
    for span in fields.values():
        assert span.value_first == span.value_last


def test_unknown_record_returns_none(structure):
    assert structure.record("nope") is None


def test_record_missing_required_component_is_kept(tmp_path):
    rsl = "package P\ntype T {\n    a Integer\n    b Integer\n}\n"
    src = "package P\nT r1 { a = 1 }\nT r2 { a = 2 b = 3 }\n"
    (tmp_path / "p.rsl").write_text(rsl, encoding="utf-8")
    (tmp_path / "p.trlc").write_text(src, encoding="utf-8")
    sm = Source_Manager(Message_Handler())
    sm.register_file(str(tmp_path / "p.rsl"), rsl)
    sm.register_file(str(tmp_path / "p.trlc"), src)
    sm.process()
    tokens = sm.all_files[str(tmp_path / "p.trlc")].lexer.tokens
    structure = scan_file(tokens)
    rec = structure.record("r1")
    assert rec is not None
    assert tokens[rec.close_tok].kind == "C_KET"
    assert tokens[rec.close_tok].location.line_no == 2
    assert list(rec.fields) == ["a"]
    assert structure.record("r2") is not None
