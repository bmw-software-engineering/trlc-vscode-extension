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
"""Unit tests for server.preview_edits."""

# pylint: disable=missing-function-docstring
# The fixture deliberately mirrors the one in test_preview_scan.py.
# pylint: disable=duplicate-code
# pylint: disable=too-many-arguments,too-many-positional-arguments

import dataclasses
import io

import pytest
from trlc.ast import Implicit_Null, Record_Object
from trlc.errors import Message_Handler
from trlc.trlc import Source_Manager

from server.preview_edits import add_record_edits, set_field_edits
from server.preview_scan import FileStructure, scan_file
from server.preview_values import PreviewEditError

RSL = """\
package Types

type MyRecord {
    x Integer
    y Integer
    text optional String
}

type Frozen extends MyRecord {
    freeze y = 9
}

type Opt {
    o optional Integer
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


def _parse(tmp_path, content):
    """Parse content as a.trlc next to the RSL; return (mh, parsed)."""
    (tmp_path / "types.rsl").write_text(RSL, encoding="utf-8")
    trlc = tmp_path / "a.trlc"
    trlc.write_bytes(content.encode("utf-8"))
    mh = Message_Handler(out=io.StringIO())  # keep diagnostics quiet
    sm = Source_Manager(mh)
    sm.register_file(str(tmp_path / "types.rsl"), RSL)
    sm.register_file(str(trlc), content)
    sm.process()
    return mh, sm.all_files[str(trlc)]


def _offset(content, pos):
    """String offset of an LSP position (character in UTF-16 code units,
    as VS Code interprets it)."""
    line_start = 0
    for _ in range(pos.line):
        line_start = content.index("\n", line_start) + 1
    units = 0
    offset = line_start
    while units < pos.character:
        units += 2 if ord(content[offset]) > 0xFFFF else 1
        offset += 1
    assert units == pos.character, "position splits a surrogate pair"
    return offset


def apply_text_edits(content, edits):
    """Apply LSP TextEdits (non-overlapping) to content."""
    spans = sorted(
        (
            (_offset(content, e.range.start), _offset(content, e.range.end))
            + (e.new_text,)
            for e in edits
        ),
        reverse=True,
    )
    for start, end, text in spans:
        assert start <= end
        content = content[:start] + text + content[end:]
    return content


def _set(tmp_path, content, record, field, value, markdown=()):
    _, parsed = _parse(tmp_path, content)
    structure = scan_file(parsed.lexer.tokens)
    edits = set_field_edits(
        structure,
        parsed.lexer.content,
        parsed.cu.package,
        record,
        field,
        value,
        markdown,
    )
    return apply_text_edits(content, edits)


def _add(tmp_path, content, section_id, type_name, name):
    _, parsed = _parse(tmp_path, content)
    structure = scan_file(parsed.lexer.tokens)
    edits = add_record_edits(
        structure,
        parsed.lexer.content,
        parsed.cu,
        section_id,
        type_name,
        name,
    )
    return apply_text_edits(content, edits)


def _reparse_ok(tmp_path, content):
    """Re-parse content; assert no errors; return {name: Record_Object}."""
    mh, parsed = _parse(tmp_path, content)
    assert mh.errors == 0, content
    return {
        obj.name: obj
        for obj in parsed.cu.package.symbols.values(Record_Object)
    }


def _int(obj, field):
    return obj.field[field].value


def test_replace_integer_value_keeps_comment(tmp_path):
    out = _set(tmp_path, TRLC, "r1", "x", "7")
    assert "x = 7  // keep me" in out
    assert _int(_reparse_ok(tmp_path, out)["r1"], "x") == 7


def test_replace_keeps_comment_between_assign_and_value(tmp_path):
    src = TRLC.replace("x = 1  // keep me", "x = /* c */ 1")
    out = _set(tmp_path, src, "r1", "x", "42")
    assert "x = /* c */ 42" in out
    assert _int(_reparse_ok(tmp_path, out)["r1"], "x") == 42


def test_replace_string_value(tmp_path):
    out = _set(tmp_path, TRLC, "r1", "text", "a")
    out = _set(tmp_path, out, "r1", "text", "b")
    assert _reparse_ok(tmp_path, out)["r1"].field["text"].value == "b"


def test_set_unset_optional_inserts_line(tmp_path):
    value = 'he said "hi"'
    out = _set(tmp_path, TRLC, "r1", "text", value)
    assert '        y = 2\n        text = "he said \\"hi\\""\n    }' in out
    assert _reparse_ok(tmp_path, out)["r1"].field["text"].value == value


def test_insert_into_inline_record(tmp_path):
    out = _set(tmp_path, TRLC, "r2", "text", "t")
    assert _reparse_ok(tmp_path, out)["r2"].field["text"].value == "t"


def test_insert_into_multiline_record_at_file_level(tmp_path):
    src = TRLC.replace(
        "MyRecord r3 { x = 5 y = 6 }", "Frozen r3 {\n    x = 5\n}"
    )
    out = _set(tmp_path, src, "r3", "text", "t")
    assert '    x = 5\n    text = "t"\n}' in out
    assert _reparse_ok(tmp_path, out)["r3"].field["text"].value == "t"


def test_remove_optional_deletes_whole_line(tmp_path):
    out = _set(tmp_path, TRLC, "r1", "text", "gone soon")
    out = _set(tmp_path, out, "r1", "text", None)
    assert out == TRLC


def test_remove_optional_with_trailing_comment_deletes_line(tmp_path):
    src = TRLC.replace("y = 2\n", 'y = 2\n        text = "q"  // c\n')
    out = _set(tmp_path, src, "r1", "text", None)
    assert out == TRLC


def test_remove_optional_inline_deletes_assignment_only(tmp_path):
    src = TRLC.replace("{ x = 3 y = 4 }", '{ x = 3 text = "q" y = 4 }')
    out = _set(tmp_path, src, "r2", "text", None)
    assert "{ x = 3  y = 4 }" in out
    assert isinstance(
        _reparse_ok(tmp_path, out)["r2"].field["text"], Implicit_Null
    )


def test_remove_unset_optional_is_noop(tmp_path):
    assert _set(tmp_path, TRLC, "r1", "text", None) == TRLC


def test_remove_required_rejected(tmp_path):
    with pytest.raises(PreviewEditError):
        _set(tmp_path, TRLC, "r1", "x", None)


def test_unknown_record_or_field_rejected(tmp_path):
    with pytest.raises(PreviewEditError):
        _set(tmp_path, TRLC, "nope", "x", "1")
    with pytest.raises(PreviewEditError):
        _set(tmp_path, TRLC, "r1", "nope", "1")


def test_frozen_field_rejected(tmp_path):
    src = TRLC.replace("MyRecord r3 { x = 5 y = 6 }", "Frozen r3 { x = 5 }")
    with pytest.raises(PreviewEditError):
        _set(tmp_path, src, "r3", "y", "1")


def test_invalid_value_rejected(tmp_path):
    with pytest.raises(PreviewEditError):
        _set(tmp_path, TRLC, "r1", "x", "abc")


def test_add_record_at_file_level(tmp_path):
    out = _add(tmp_path, TRLC, None, "MyRecord", "r4")
    assert out.startswith(TRLC)
    r4 = _reparse_ok(tmp_path, out)["r4"]
    assert r4.section is None
    assert _int(r4, "x") == 0
    assert _int(r4, "y") == 0
    assert isinstance(r4.field["text"], Implicit_Null)


def test_add_record_skips_frozen_component(tmp_path):
    out = _add(tmp_path, TRLC, None, "Frozen", "r4")
    assert "y =" not in out[len(TRLC) :]
    r4 = _reparse_ok(tmp_path, out)["r4"]
    assert _int(r4, "x") == 0


def test_add_record_into_second_duplicate_section(tmp_path):
    out = _add(tmp_path, TRLC, 1, "MyRecord", "r4")
    assert (
        "        MyRecord r2 { x = 3 y = 4 }\n"
        "        MyRecord r4 {\n"
        "            x = 0\n"
        "            y = 0\n"
        "        }\n"
        "    }\n"
    ) in out
    objs = _reparse_ok(tmp_path, out)
    sec = objs["r4"].section
    assert len(sec) == 2
    assert sec[-1].name == "A"
    assert sec[-1] is objs["r2"].section[-1]


def test_add_record_into_section_with_inline_close(tmp_path):
    src = TRLC.replace(
        "MyRecord r2 { x = 3 y = 4 }\n    }", "MyRecord r2 { x = 3 y = 4 } }"
    )
    out = _add(tmp_path, src, 1, "MyRecord", "r4")
    objs = _reparse_ok(tmp_path, out)
    assert objs["r4"].section[-1] is objs["r2"].section[-1]


def test_add_record_rejects_bad_or_duplicate_name(tmp_path):
    for name in ("1abc", "r1", "", "a-b", "section"):
        with pytest.raises(PreviewEditError):
            _add(tmp_path, TRLC, None, "MyRecord", name)


def test_add_record_rejects_unknown_type_or_section(tmp_path):
    with pytest.raises(PreviewEditError):
        _add(tmp_path, TRLC, None, "Nope", "r4")
    with pytest.raises(PreviewEditError):
        _add(tmp_path, TRLC, 7, "MyRecord", "r4")


def test_crlf_file_keeps_crlf(tmp_path):
    src = TRLC.replace("\n", "\r\n")
    out = _set(tmp_path, src, "r1", "text", "t")
    out = _set(tmp_path, out, "r2", "text", "u")
    out = _add(tmp_path, out, 1, "MyRecord", "r4")
    out = _add(tmp_path, out, None, "MyRecord", "r5")
    assert "\n" not in out.replace("\r\n", "")
    objs = _reparse_ok(tmp_path, out)
    assert objs["r1"].field["text"].value == "t"
    assert objs["r2"].field["text"].value == "u"
    assert {"r4", "r5"} <= set(objs)
    back = _set(tmp_path, out, "r1", "text", None)
    assert (
        "r1 {\r\n        x = 1  // keep me\r\n        y = 2\r\n    }" in back
    )


def test_remove_multiline_string_deletes_all_its_lines(tmp_path):
    out = _set(tmp_path, TRLC, "r1", "text", "line1\nline2")
    assert _reparse_ok(tmp_path, out)["r1"].field["text"].value == (
        "line1\nline2"
    )
    assert _set(tmp_path, out, "r1", "text", None) == TRLC


@pytest.mark.parametrize(
    "body,expected",
    [
        ("{ }", "Opt e { \n    o = 5\n}\n"),
        ("{}", "Opt e {\n    o = 5\n}\n"),
        ("{\n}", "Opt e {\n    o = 5\n}\n"),
    ],
)
def test_insert_into_empty_record(tmp_path, body, expected):
    src = TRLC + f"Opt e {body}\n"
    out = _set(tmp_path, src, "e", "o", "5")
    assert out == TRLC + expected
    assert _int(_reparse_ok(tmp_path, out)["e"], "o") == 5


def test_comment_between_name_and_assign_is_replaced_in_place(tmp_path):
    src = TRLC.replace("x = 1  // keep me", "x /*c*/ = 5")
    out = _set(tmp_path, src, "r1", "x", "8")
    assert out == src.replace("x /*c*/ = 5", "x /*c*/ = 8")
    assert _int(_reparse_ok(tmp_path, out)["r1"], "x") == 8
    src = TRLC.replace("y = 2\n", 'y = 2\n        text /*c*/ = "q"\n')
    assert _set(tmp_path, src, "r1", "text", None) == TRLC


def _without_span(structure: FileStructure, record, field):
    rec = structure.record(record)
    fields = {k: v for k, v in rec.fields.items() if k != field}
    new_rec = dataclasses.replace(rec, fields=fields)
    return dataclasses.replace(
        structure,
        records=[new_rec if r is rec else r for r in structure.records],
    )


@pytest.mark.parametrize(
    "field,value", [("x", "8"), ("text", "t"), ("text", None)]
)
def test_unlocatable_assignment_rejected(tmp_path, field, value):
    src = TRLC.replace("y = 2\n", 'y = 2\n        text = "q"\n')
    _, parsed = _parse(tmp_path, src)
    structure = _without_span(scan_file(parsed.lexer.tokens), "r1", field)
    with pytest.raises(PreviewEditError, match="Cannot locate"):
        set_field_edits(
            structure,
            parsed.lexer.content,
            parsed.cu.package,
            "r1",
            field,
            value,
        )


def test_crlf_multiline_string(tmp_path):
    src = TRLC.replace("\n", "\r\n")
    out = _set(tmp_path, src, "r1", "text", "a\nb")
    assert "\n" not in out.replace("\r\n", "")
    assert _reparse_ok(tmp_path, out)["r1"].field["text"].value == "a\nb"
    out = _set(tmp_path, out, "r1", "text", "c\r\nd")
    assert "\n" not in out.replace("\r\n", "")
    assert _reparse_ok(tmp_path, out)["r1"].field["text"].value == "c\nd"
    assert _set(tmp_path, out, "r1", "text", None) == src


def test_positions_are_utf16_after_non_bmp_character(tmp_path):
    # The emoji is one code point but two UTF-16 code units: positions
    # after it on the same line must count it twice.
    src = 'package Types\nMyRecord r1 { text = "😀" x = 1 y = 2 }\n'
    out = _set(tmp_path, src, "r1", "y", "7")
    assert _int(_reparse_ok(tmp_path, out)["r1"], "y") == 7
    out = _set(tmp_path, out, "r1", "text", None)
    objs = _reparse_ok(tmp_path, out)
    assert isinstance(objs["r1"].field["text"], Implicit_Null)
    assert _int(objs["r1"], "y") == 7


def test_add_record_into_section_after_non_bmp_character(tmp_path):
    src = 'package Types\nsection "😀" { MyRecord q { x = 1 y = 2 } }\n'
    out = _add(tmp_path, src, 0, "MyRecord", "n1")
    objs = _reparse_ok(tmp_path, out)
    assert objs["n1"].section[-1] is objs["q"].section[-1]


def test_add_record_rejects_too_similar_name(tmp_path):
    # TRLC rejects names differing only in case/underscores.
    src = "package Types\nMyRecord q { x = 1 y = 2 }\n"
    for name in ("Q", "q_"):
        with pytest.raises(PreviewEditError, match="too similar"):
            _add(tmp_path, src, None, "MyRecord", name)


def test_add_record_into_unclosed_section_rejected(tmp_path):
    src = 'package Types\nsection "A" {\nMyRecord q { x = 1 y = 2 }\n'
    with pytest.raises(PreviewEditError, match="not closed"):
        _add(tmp_path, src, 0, "MyRecord", "n1")


def test_set_markdown_field_writes_block(tmp_path):
    value = "intro\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    out = _set(tmp_path, TRLC, "r1", "text", value, ("text",))
    assert _reparse_ok(tmp_path, out)["r1"].field["text"].value == value
    lines = out.split("\n")
    start = lines.index("        text = '''")
    assert lines[start + 1] == "            intro"
    assert lines[start + 6] == "        '''"


def test_set_markdown_field_crlf(tmp_path):
    src = TRLC.replace("\n", "\r\n")
    out = _set(tmp_path, src, "r1", "text", "a\n\nb", ("text",))
    assert "\n" not in out.replace("\r\n", "")
    assert _reparse_ok(tmp_path, out)["r1"].field["text"].value == "a\n\nb"


def test_set_markdown_field_unrepresentable_rejected(tmp_path):
    with pytest.raises(PreviewEditError):
        _set(tmp_path, TRLC, "r1", "text", "- a\n  - b", ("text",))
