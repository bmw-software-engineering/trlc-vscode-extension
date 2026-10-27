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
"""Unit tests for server.preview_structure."""

# pylint: disable=missing-function-docstring
# The fixture deliberately mirrors the one in test_preview_edits.py.
# pylint: disable=duplicate-code

import io

import pytest
from trlc.errors import Message_Handler
from trlc.trlc import Source_Manager

from server.preview_scan import scan_file
from server.preview_structure import (
    dissolve_section_edits,
    insert_section_edits,
    rename_section_edits,
    verify_structure_edit,
)
from server.preview_values import PreviewEditError

RSL = """\
package Types

type MyRecord {
    x Integer
    y Integer
    text optional String
}
"""


def _parse(tmp_path, content):
    """Parse *content* as a.trlc next to the RSL; return (mh, parsed)."""
    (tmp_path / "types.rsl").write_text(RSL, encoding="utf-8")
    trlc = tmp_path / "a.trlc"
    trlc.write_bytes(content.encode("utf-8"))
    mh = Message_Handler(out=io.StringIO())
    sm = Source_Manager(mh)
    sm.register_file(str(tmp_path / "types.rsl"), RSL)
    sm.register_file(str(trlc), content)
    sm.process()
    return mh, sm.all_files[str(trlc)]


def _offset(content, pos):
    """String offset of an LSP position (UTF-16 code units)."""
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


def _apply(content, edits):
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


def _run(tmp_path, content, func, *args):
    """Apply func's edits to content; return the new content."""
    mh, parsed = _parse(tmp_path, content)
    assert mh.errors == 0
    structure = scan_file(parsed.lexer.tokens)
    return _apply(content, func(structure, parsed.lexer.content, *args))


def _check(tmp_path, content):
    """Re-parse *content*; return {record: (section path, x, y, text)}."""
    mh, parsed = _parse(tmp_path, content)
    assert mh.errors == 0, content
    result = {}
    for obj in parsed.cu.package.symbols.values():
        if obj.__class__.__name__ != "Record_Object":
            continue
        text = obj.field["text"]
        result[obj.name] = (
            tuple(sec.name for sec in obj.section or []),
            obj.field["x"].value,
            obj.field["y"].value,
            getattr(text, "value", None),
        )
    return result


def _sections(tmp_path, content):
    """(title, depth) of every section in *content*."""
    _, parsed = _parse(tmp_path, content)
    return [
        (sec.title, sec.depth)
        for sec in scan_file(parsed.lexer.tokens).sections
    ]


BASE = """\
package Types
section "A" {
    MyRecord r1 {
        x = 1
        y = 2
    }
}
MyRecord r2 { x = 3 y = 4 }
"""


def test_insert_empty_section_at_file_level(tmp_path):
    new = _run(tmp_path, BASE, insert_section_edits, None, "New")
    assert new == BASE + '\nsection "New" {\n}\n'
    assert _sections(tmp_path, new) == [("A", 1), ("New", 1)]
    assert _check(tmp_path, new) == _check(tmp_path, BASE)


def test_insert_section_into_section(tmp_path):
    new = _run(tmp_path, BASE, insert_section_edits, 0, "Inner")
    assert new == BASE.replace(
        "    }\n}\n", '    }\n    section "Inner" {\n    }\n}\n'
    )
    assert _sections(tmp_path, new) == [("A", 1), ("Inner", 2)]
    assert _check(tmp_path, new) == _check(tmp_path, BASE)


def test_insert_section_rejects_bad_titles(tmp_path):
    for title in ("", "   ", "a\nb"):
        with pytest.raises(PreviewEditError):
            _run(tmp_path, BASE, insert_section_edits, None, title)


def test_insert_into_unclosed_section_rejected(tmp_path):
    content = 'package Types\nsection "A" {\n    MyRecord r1 { x = 1 y = 2 }\n'
    _, parsed = _parse(tmp_path, content)
    structure = scan_file(parsed.lexer.tokens)
    assert structure.sections[0].close_tok is None
    with pytest.raises(PreviewEditError):
        insert_section_edits(structure, content, 0, "B")


def test_rename_section_with_quote_in_title(tmp_path):
    new = _run(tmp_path, BASE, rename_section_edits, 0, 'Say "hi"')
    _, parsed = _parse(tmp_path, new)
    assert scan_file(parsed.lexer.tokens).sections[0].title == 'Say "hi"'
    assert _check(tmp_path, new)["r1"][0] == ('Say "hi"',)
    assert _check(tmp_path, new)["r2"] == _check(tmp_path, BASE)["r2"]


NESTED = """\
package Types
section "A" {
    MyRecord r0 { x = 0 y = 0 }
    section "B" {
        MyRecord r1 {
            x = 1
            y = 2
        }
        section "C" {
            MyRecord r2 { x = 3 y = 4 }
        }
    }
}
"""


def test_dissolve_moves_children_up_one_level(tmp_path):
    new = _run(tmp_path, NESTED, dissolve_section_edits, 1)
    assert new == (
        "package Types\n"
        'section "A" {\n'
        "    MyRecord r0 { x = 0 y = 0 }\n"
        "    MyRecord r1 {\n"
        "        x = 1\n"
        "        y = 2\n"
        "    }\n"
        '    section "C" {\n'
        "        MyRecord r2 { x = 3 y = 4 }\n"
        "    }\n"
        "}\n"
    )
    assert _sections(tmp_path, NESTED) == [("A", 1), ("B", 2), ("C", 3)]
    assert _sections(tmp_path, new) == [("A", 1), ("C", 2)]
    old = _check(tmp_path, NESTED)
    res = _check(tmp_path, new)
    assert res["r0"] == old["r0"]
    assert res["r1"] == (("A",),) + old["r1"][1:]
    assert res["r2"] == (("A", "C"),) + old["r2"][1:]


def test_dissolve_top_level_section(tmp_path):
    new = _run(tmp_path, BASE, dissolve_section_edits, 0)
    assert new == (
        "package Types\n"
        "MyRecord r1 {\n"
        "    x = 1\n"
        "    y = 2\n"
        "}\n"
        "MyRecord r2 { x = 3 y = 4 }\n"
    )
    assert _check(tmp_path, new)["r1"] == ((), 1, 2, None)


def test_dissolve_keeps_comments(tmp_path):
    content = (
        "package Types\n"
        "// above A\n"
        'section "A" { // head\n'
        "    // inside\n"
        "    MyRecord r1 { x = 1 y = 2 } // r1\n"
        "} /* tail */ // end\n"
        "MyRecord r2 { x = 3 y = 4 }\n"
    )
    new = _run(tmp_path, content, dissolve_section_edits, 0)
    assert new == (
        "package Types\n"
        "// above A\n"
        "// head\n"
        "// inside\n"
        "MyRecord r1 { x = 1 y = 2 } // r1\n"
        "/* tail */\n"
        "// end\n"
        "MyRecord r2 { x = 3 y = 4 }\n"
    )
    assert _check(tmp_path, new)["r1"] == ((), 1, 2, None)


def test_dissolve_keeps_underindented_multiline_string(tmp_path):
    content = (
        "package Types\n"
        'section "A" {\n'
        "    MyRecord r1 {\n"
        "        x = 1\n"
        "        y = 2\n"
        "        text = '''\n"
        "  short\n"
        "      deeper\n"
        "  '''\n"
        "    }\n"
        "}\n"
    )
    new = _run(tmp_path, content, dissolve_section_edits, 0)
    assert new == (
        "package Types\n"
        "MyRecord r1 {\n"
        "    x = 1\n"
        "    y = 2\n"
        "    text = '''\n"
        "  short\n"
        "      deeper\n"
        "  '''\n"
        "}\n"
    )
    old = _check(tmp_path, content)["r1"]
    assert _check(tmp_path, new)["r1"] == ((),) + old[1:]


def test_dissolve_shifts_multiline_string_uniformly(tmp_path):
    content = (
        "package Types\n"
        'section "A" {\n'
        "    MyRecord r1 {\n"
        "        x = 1\n"
        "        y = 2\n"
        "        text = '''\n"
        "            a\n"
        "              b\n"
        "        '''\n"
        "    }\n"
        "}\n"
    )
    new = _run(tmp_path, content, dissolve_section_edits, 0)
    assert "        a\n          b\n    '''\n" in new
    old = _check(tmp_path, content)["r1"]
    assert _check(tmp_path, new)["r1"] == ((),) + old[1:]


def test_dissolve_crlf(tmp_path):
    content = (
        "package Types\n"
        'section "A" { // head\n'
        "    MyRecord r1 {\n"
        "        x = 1\n"
        "        y = 2\n"
        "    }\n"
        "} // end\n"
        "MyRecord r2 { x = 3 y = 4 }\n"
    ).replace("\n", "\r\n")
    new = _run(tmp_path, content, dissolve_section_edits, 0)
    assert new == (
        "package Types\n"
        "// head\n"
        "MyRecord r1 {\n"
        "    x = 1\n"
        "    y = 2\n"
        "}\n"
        "// end\n"
        "MyRecord r2 { x = 3 y = 4 }\n"
    ).replace("\n", "\r\n")
    assert _check(tmp_path, new)["r1"] == ((), 1, 2, None)


def test_dissolve_tab_indented(tmp_path):
    content = (
        "package Types\n"
        'section "A" {\n'
        '\tsection "B" {\n'
        "\t\tMyRecord r1 { x = 1 y = 2 }\n"
        "\t}\n"
        "}\n"
    )
    new = _run(tmp_path, content, dissolve_section_edits, 1)
    assert new == (
        'package Types\nsection "A" {\n\tMyRecord r1 { x = 1 y = 2 }\n}\n'
    )


def test_dissolve_inline_section(tmp_path):
    content = (
        "package Types\n"
        'MyRecord r0 { x = 0 y = 0 } section "A" { MyRecord r1 '
        "{ x = 1 y = 2 } }\n"
        "MyRecord r2 { x = 3 y = 4 }\n"
    )
    new = _run(tmp_path, content, dissolve_section_edits, 0)
    assert _check(tmp_path, new)["r1"] == ((), 1, 2, None)
    assert "section" not in new


def test_dissolve_at_eof_without_eol(tmp_path):
    content = (
        'package Types\nsection "A" {\n    MyRecord r1 { x = 1 y = 2 }\n}'
    )
    new = _run(tmp_path, content, dissolve_section_edits, 0)
    assert new == "package Types\nMyRecord r1 { x = 1 y = 2 }"


def test_dissolve_empty_section(tmp_path):
    content = 'package Types\nsection "A" {\n}\nMyRecord r1 { x = 1 y = 2 }\n'
    new = _run(tmp_path, content, dissolve_section_edits, 0)
    assert new == "package Types\nMyRecord r1 { x = 1 y = 2 }\n"


def test_unknown_section_rejected(tmp_path):
    for func, args in (
        (insert_section_edits, (7, "X")),
        (rename_section_edits, (7, "X")),
        (dissolve_section_edits, (7,)),
    ):
        with pytest.raises(PreviewEditError):
            _run(tmp_path, BASE, func, *args)


def test_verify_accepts_section_changes():
    verify_structure_edit(BASE, BASE.replace('"A"', '"Renamed"'))
    verify_structure_edit(BASE, BASE + 'section "N" {\n}\n')


def test_verify_rejects_dropped_record():
    with pytest.raises(PreviewEditError):
        verify_structure_edit(
            BASE, BASE.replace("MyRecord r2 { x = 3 y = 4 }\n", "")
        )


def test_verify_rejects_changed_value():
    with pytest.raises(PreviewEditError):
        verify_structure_edit(BASE, BASE.replace("x = 3", "x = 5"))


def test_verify_rejects_unbalanced_braces():
    with pytest.raises(PreviewEditError):
        verify_structure_edit(BASE, BASE.replace("    }\n}\n", "    }\n"))


def test_verify_rejects_lex_error():
    with pytest.raises(PreviewEditError):
        verify_structure_edit(BASE, BASE + '"unterminated\n')
