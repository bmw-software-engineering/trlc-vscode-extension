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
"""Unit tests for server.preview_layout."""

# pylint: disable=missing-function-docstring
# The fixture deliberately mirrors the one in test_preview_scan.py.
# pylint: disable=duplicate-code

import io

import pytest
from trlc.errors import Message_Handler
from trlc.lexer import TRLC_Lexer
from trlc.trlc import Source_Manager

from server.preview_layout import (
    Extent,
    indent_step,
    record_extent,
    section_extent,
    shift_block,
)
from server.preview_scan import FileStructure, scan_file

RSL = """\
package Types

type MyRecord {
    x Integer
    y Integer
    text optional String
}
"""


def _parse(tmp_path, content: str) -> FileStructure:
    """Parse *content* as a.trlc next to the RSL and scan it."""
    (tmp_path / "types.rsl").write_text(RSL, encoding="utf-8")
    trlc = tmp_path / "a.trlc"
    trlc.write_bytes(content.encode("utf-8"))
    mh = Message_Handler(out=io.StringIO())
    sm = Source_Manager(mh)
    sm.register_file(str(tmp_path / "types.rsl"), RSL)
    sm.register_file(str(trlc), content)
    assert sm.process() is not None
    return scan_file(sm.all_files[str(trlc)].lexer.tokens)


def _text(content: str, ext: Extent) -> str:
    return content[ext.start : ext.end]


def _strings(content: str) -> "list[str]":
    """Values of all STRING tokens when *content* is lexed."""
    lexer = TRLC_Lexer(Message_Handler(out=io.StringIO()), "x", content)
    values = []
    while True:
        tok = lexer.token()
        if tok is None:
            return values
        if tok.kind == "STRING":
            values.append(tok.value)


def test_record_extent_whole_lines_with_leading_comment(tmp_path):
    content = (
        "package Types\n"
        'section "A" {\n'
        "    // detached\n"
        "\n"
        "    // about r1\n"
        "    /* more */\n"
        "    MyRecord r1 {\n"
        "        x = 1\n"
        "        y = 2\n"
        "    } // end r1\n"
        "    MyRecord r2 { x = 3 y = 4 }\n"
        "}\n"
    )
    structure = _parse(tmp_path, content)
    ext = record_extent(structure, content, structure.record("r1"))
    assert ext.whole_lines
    assert _text(content, ext) == (
        "    // about r1\n"
        "    /* more */\n"
        "    MyRecord r1 {\n"
        "        x = 1\n"
        "        y = 2\n"
        "    } // end r1\n"
    )
    ext2 = record_extent(structure, content, structure.record("r2"))
    assert ext2.whole_lines
    assert _text(content, ext2) == "    MyRecord r2 { x = 3 y = 4 }\n"


def test_record_extent_whole_lines_crlf_and_eof(tmp_path):
    content = "package Types\r\n// c\r\nMyRecord r1 { x = 1 y = 2 }"
    structure = _parse(tmp_path, content)
    ext = record_extent(structure, content, structure.record("r1"))
    assert ext.whole_lines
    assert _text(content, ext) == "// c\r\nMyRecord r1 { x = 1 y = 2 }"


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_record_extent_inline_record_is_token_range(tmp_path, eol):
    content = (
        "package Types\n"
        "// not carried: the record shares its line\n"
        "MyRecord r1 { x = 1 y = 2 } MyRecord r2 { x = 3 y = 4 } // t\n"
        "MyRecord r3 { x = 5 y = 6 }\n"
    ).replace("\n", eol)
    structure = _parse(tmp_path, content)
    ext1 = record_extent(structure, content, structure.record("r1"))
    assert not ext1.whole_lines
    assert _text(content, ext1) == "MyRecord r1 { x = 1 y = 2 }"
    ext2 = record_extent(structure, content, structure.record("r2"))
    assert not ext2.whole_lines
    assert _text(content, ext2) == "MyRecord r2 { x = 3 y = 4 } // t"


def test_record_extent_qualified_type(tmp_path):
    content = (
        "package Types\n// q\nTypes.MyRecord r1 {\n    x = 1\n    y = 2\n}\n"
    )
    structure = _parse(tmp_path, content)
    ext = record_extent(structure, content, structure.record("r1"))
    assert ext.whole_lines
    assert _text(content, ext) == content[len("package Types\n") :]


def test_record_extent_does_not_take_package_name(tmp_path):
    content = "package Types MyRecord r1 { x = 1 y = 2 }\n"
    structure = _parse(tmp_path, content)
    ext = record_extent(structure, content, structure.record("r1"))
    assert not ext.whole_lines
    assert _text(content, ext) == "MyRecord r1 { x = 1 y = 2 }"


def test_section_extent_includes_trailing_comment(tmp_path):
    content = (
        "package Types\n"
        "MyRecord r0 { x = 0 y = 0 } // r0 only\n"
        "// about A\n"
        'section "A" {\n'
        '    section "B" {\n'
        "        MyRecord r1 { x = 1 y = 2 }\n"
        "    } // end B\n"
        "} // end A\n"
    )
    structure = _parse(tmp_path, content)
    outer, inner = structure.sections
    ext = section_extent(structure, content, outer)
    assert ext.whole_lines
    assert _text(content, ext) == content[content.index("// about A") :]
    ext_b = section_extent(structure, content, inner)
    assert ext_b.whole_lines
    assert _text(content, ext_b) == (
        '    section "B" {\n'
        "        MyRecord r1 { x = 1 y = 2 }\n"
        "    } // end B\n"
    )


def test_section_extent_followed_by_item_is_token_range(tmp_path):
    content = (
        "package Types\n"
        'section "A" {\n'
        "  MyRecord r1 { x = 1 y = 2 }\n"
        "} MyRecord r2 { x = 3 y = 4 }\n"
    )
    structure = _parse(tmp_path, content)
    ext = section_extent(structure, content, structure.sections[0])
    assert not ext.whole_lines
    assert _text(content, ext) == (
        'section "A" {\n  MyRecord r1 { x = 1 y = 2 }\n}'
    )


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_section_extent_token_range_with_trailing_comment(tmp_path, eol):
    content = (
        "package Types\n"
        'MyRecord r0 { x = 0 y = 0 } section "A" {\n'
        "  MyRecord r1 { x = 1 y = 2 }\n"
        "} // end A\n"
    ).replace("\n", eol)
    structure = _parse(tmp_path, content)
    ext = section_extent(structure, content, structure.sections[0])
    assert not ext.whole_lines
    assert _text(content, ext) == (
        'section "A" {\n  MyRecord r1 { x = 1 y = 2 }\n} // end A'
    ).replace("\n", eol)


def test_section_extent_requires_close(tmp_path):
    content = 'package Types\nsection "A" {\n'
    structure = _parse_lenient(tmp_path, content)
    with pytest.raises(ValueError):
        section_extent(structure, content, structure.sections[0])


def _parse_lenient(tmp_path, content: str) -> FileStructure:
    (tmp_path / "types.rsl").write_text(RSL, encoding="utf-8")
    trlc = tmp_path / "a.trlc"
    trlc.write_bytes(content.encode("utf-8"))
    sm = Source_Manager(Message_Handler(out=io.StringIO()))
    sm.register_file(str(tmp_path / "types.rsl"), RSL)
    sm.register_file(str(trlc), content)
    sm.process()
    return scan_file(sm.all_files[str(trlc)].lexer.tokens)


def test_indent_step_from_file(tmp_path):
    content = (
        "package Types\n"
        'section "A" {\n'
        "  MyRecord r1 {\n"
        "      x = 1\n"
        "      y = 2\n"
        "  }\n"
        "}\n"
    )
    structure = _parse(tmp_path, content)
    assert indent_step(structure, content) == "  "


def test_indent_step_with_comments_around_section_brace(tmp_path):
    content = (
        "package Types\n"
        'section "A" /* title */ {\n'
        "   // first child follows\n"
        "   MyRecord r1 { x = 1 y = 2 }\n"
        "}\n"
    )
    structure = _parse(tmp_path, content)
    assert indent_step(structure, content) == "   "


def test_indent_step_from_record_fields(tmp_path):
    content = "package Types\nMyRecord r1 {\n\tx = 1\n\ty = 2\n}\n"
    structure = _parse(tmp_path, content)
    assert indent_step(structure, content) == "\t"


def test_indent_step_default(tmp_path):
    content = (
        "package Types\n"
        'section "A" { MyRecord r1 { x = 1 y = 2 } }\n'
        "MyRecord r2 { x = 3 y = 4 }\n"
    )
    structure = _parse(tmp_path, content)
    assert indent_step(structure, content) == "    "


def test_shift_block_indent_and_dedent():
    content = "head\n  a {\n\n    b\n  }\n x\ntail\n"
    start = content.index("  a")
    end = content.index("tail")
    assert shift_block(content, [], start, end, 1, "  ") == (
        "    a {\n\n      b\n    }\n   x\n"
    )
    assert shift_block(content, [], start, end, -1, "  ") == (
        "a {\n\n  b\n}\nx\n"
    )
    assert shift_block(content, [], start, end, -2, "  ") == (
        "a {\n\nb\n}\nx\n"
    )
    assert (
        shift_block(content, [], start, end, 0, "  ") == (content[start:end])
    )


def test_shift_block_dedent_counts_a_tab_as_one_step():
    content = "\t\tx\n\t  y\n  \tz\n      w\n"
    assert shift_block(content, [], 0, len(content), -1, "    ") == (
        "\tx\n  y\n\tz\n  w\n"
    )
    assert shift_block(content, [], 0, len(content), -2, "    ") == (
        "x\ny\n\tz\nw\n"
    )


def test_shift_block_mid_line_start_keeps_first_line():
    content = "} MyRecord r {\n    x = 1\n}\n"
    start = content.index("MyRecord")
    assert shift_block(content, [], start, len(content), 1, "  ") == (
        "MyRecord r {\n      x = 1\n  }\n"
    )


MD_RECORD = (
    "package Types\n"
    'section "A" {\n'
    "    MyRecord r1 {\n"
    "        x = 1\n"
    "        y = 2\n"
    "        text = '''\n"
    "            # Title\n"
    "\n"
    "              indented\n"
    "            body\n"
    "        '''\n"
    "    }\n"
    "}\n"
)


def _block(structure, content):
    ext = record_extent(structure, content, structure.record("r1"))
    assert ext.whole_lines
    return ext


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_shift_block_moves_multiline_string_uniformly(tmp_path, eol):
    content = MD_RECORD.replace("\n", eol)
    structure = _parse(tmp_path, content)
    ext = _block(structure, content)
    before = _strings(content)
    for levels, first in ((-1, "MyRecord r1 {"), (1, "        MyRecord r1 {")):
        shifted = shift_block(
            content, structure.tokens, ext.start, ext.end, levels, "    "
        )
        lines = shifted.split(eol)
        assert lines[0] == first
        new = content[: ext.start] + shifted + content[ext.end :]
        assert _strings(new) == before
        body = [line for line in lines if "Title" in line][0]
        expected = 12 + 4 * levels
        assert body == " " * expected + "# Title"
        assert [line for line in lines if "indented" in line] == [
            " " * (expected + 2) + "indented"
        ]


def test_shift_block_leaves_underindented_string_untouched(tmp_path):
    content = (
        "package Types\n"
        'section "A" {\n'
        "    MyRecord r1 {\n"
        "        x = 1\n"
        "        y = 2\n"
        "        text = '''first\n"
        "  short\n"
        "      long\n"
        "        '''\n"
        "    }\n"
        "}\n"
    )
    structure = _parse(tmp_path, content)
    ext = _block(structure, content)
    before = _strings(content)
    shifted = shift_block(
        content, structure.tokens, ext.start, ext.end, -1, "    "
    )
    assert shifted == (
        "MyRecord r1 {\n"
        "    x = 1\n"
        "    y = 2\n"
        "    text = '''first\n"
        "  short\n"
        "      long\n"
        "        '''\n"
        "}\n"
    )
    new = content[: ext.start] + shifted + content[ext.end :]
    assert _strings(new) == before


def _tokens(content: str) -> list:
    lexer = TRLC_Lexer(Message_Handler(out=io.StringIO()), "x", content)
    tokens = []
    while (tok := lexer.token()) is not None:
        tokens.append(tok)
    return tokens


def test_shift_block_keeps_string_with_mixed_indent_whitespace():
    # Each line has enough whitespace, but removing it non-uniformly
    # (tab vs. spaces) would change the value: leave it untouched.
    content = "    x = '''a\n\t    b\n    \tc\n    '''\n"
    before = _strings(content)
    shifted = shift_block(
        content, _tokens(content), 0, len(content), -1, "    "
    )
    assert shifted == "x = '''a\n\t    b\n    \tc\n    '''\n"
    assert _strings(shifted) == before
