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
"""Unit tests for server.preview_values."""

# pylint: disable=missing-function-docstring,redefined-outer-name

import pytest
from trlc.ast import (
    Array_Type,
    Builtin_Boolean,
    Builtin_Decimal,
    Builtin_Integer,
    Builtin_Markup_String,
    Builtin_String,
)
from trlc.errors import Location, Message_Handler
from trlc.lexer import TRLC_Lexer, triple_quoted_string_value
from trlc.trlc import Source_Manager

from server.preview_values import (
    PreviewEditError,
    element_type,
    field_kind,
    format_array,
    format_markdown,
    format_value,
    placeholder,
    type_ref,
    visible_record_types,
)

OTHER_RSL = """\
package Other

type T { x Integer }
"""

TYPES_RSL = """\
package Types

import Other

enum Color { Red Green }
tuple Pair { a Integer separator @ b Integer }
abstract type Base { x Integer }
type MyRecord {
    s String
    c Color
    r optional MyRecord
    arr optional Integer [0 .. 3]
    p optional Pair
    pairs optional Pair [0 .. *]
    colors optional Color [1 .. *]
    either optional [MyRecord, Base]
    rs optional MyRecord [0 .. *]
}
"""

TRLC = """\
package Types
import Other
MyRecord r1 { s = "a" c = Color.Red }
"""


@pytest.fixture(scope="module")
def cu(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("preview_values")
    sm = Source_Manager(Message_Handler())
    files = {"other.rsl": OTHER_RSL, "types.rsl": TYPES_RSL, "a.trlc": TRLC}
    for name, text in files.items():
        path = tmp / name
        path.write_text(text, encoding="utf-8")
        sm.register_file(str(path), text)
    assert sm.process() is not None
    return sm.all_files[str(tmp / "a.trlc")].cu


def _lookup(cu, name):
    return next(
        item for item in cu.package.symbols.values() if item.name == name
    )


def _other_package(cu):
    (pkg,) = cu.imports
    return pkg


def test_field_kind_maps_builtins(cu):
    rec = _lookup(cu, "MyRecord")
    assert field_kind(Builtin_String()) == "string"
    assert field_kind(Builtin_Markup_String()) == "string"
    assert field_kind(Builtin_Integer()) == "integer"
    assert field_kind(Builtin_Decimal()) == "decimal"
    assert field_kind(Builtin_Boolean()) == "boolean"
    assert field_kind(_lookup(cu, "Color")) == "enum"
    assert field_kind(rec) == "reference"
    assert field_kind(rec.components.table["arr"].n_typ) == "array"
    assert field_kind(rec.components.table["colors"].n_typ) == "array"
    assert field_kind(_lookup(cu, "Pair")) == "other"
    assert field_kind(rec.components.table["either"].n_typ) == "reference"
    assert field_kind(rec.components.table["rs"].n_typ) == "array"
    # Lists of tuples have no element editor: edited as raw text.
    assert field_kind(rec.components.table["pairs"].n_typ) == "other"


def test_element_type(cu):
    comps = _lookup(cu, "MyRecord").components.table
    assert element_type(comps["colors"].n_typ) is _lookup(cu, "Color")
    assert element_type(comps["s"].n_typ) is None


def test_format_array(cu):
    comps, pkg = _lookup(cu, "MyRecord").components.table, cu.package
    arr, colors = comps["arr"].n_typ, comps["colors"].n_typ
    assert format_array([], arr, pkg) == "[]"
    assert format_array(["1", " 0x2 "], arr, pkg) == "[1, 0x2]"
    assert format_array(["Red", "Green"], colors, pkg) == (
        "[Color.Red, Color.Green]"
    )
    other = _other_package(cu)
    assert format_array(["Red"], colors, other) == "[Types.Color.Red]"


def test_format_array_of_strings_quotes_each_element():
    loc = Location("x")
    typ = Array_Type(loc, Builtin_String(), loc, 0, loc, None)
    assert format_array(['a "b"', "", "c,d"], typ, None) == (
        '["a \\"b\\"", "", "c,d"]'
    )


def test_format_array_rejects_bad_elements_and_bounds(cu):
    comps, pkg = _lookup(cu, "MyRecord").components.table, cu.package
    arr, colors = comps["arr"].n_typ, comps["colors"].n_typ
    with pytest.raises(PreviewEditError, match="not a valid integer"):
        format_array(["1", "x"], arr, pkg)
    with pytest.raises(PreviewEditError, match="at most 3"):
        format_array(["1", "2", "3", "4"], arr, pkg)
    with pytest.raises(PreviewEditError, match="at least 1"):
        format_array([], colors, pkg)
    with pytest.raises(PreviewEditError, match="list"):
        format_array("1", arr, pkg)
    with pytest.raises(PreviewEditError, match="list"):
        format_array([1], arr, pkg)


def _lex(text):
    lexer = TRLC_Lexer(Message_Handler(), "t", text)
    tokens = []
    while True:
        tok = lexer.token()
        if tok is None or tok.kind == "EOF":
            break
        tokens.append(tok)
    return tokens


@pytest.mark.parametrize(
    "value",
    ["plain", 'say "hi"', "ends with \\", "line1\nline2", 'a\\"b'],
)
def test_format_string_round_trips(value):
    text = format_value("string", value, None, None)
    # Newline: the lexer misreports a string at the very end of input.
    tokens = _lex(text + "\n")
    assert len(tokens) == 1
    assert tokens[0].kind == "STRING"
    assert tokens[0].value == value


def test_format_string_rejects_unrepresentable():
    with pytest.raises(PreviewEditError):
        format_value("string", "'''\n\"\"\"", None, None)


def test_format_integer():
    assert format_value("integer", " 42 ", None, None) == "42"
    assert format_value("integer", "-1_000", None, None) == "-1_000"
    with pytest.raises(PreviewEditError):
        format_value("integer", "4.2", None, None)


def test_format_decimal():
    assert format_value("decimal", "1.5", None, None) == "1.5"
    with pytest.raises(PreviewEditError):
        format_value("decimal", "x", None, None)


def test_format_boolean():
    assert format_value("boolean", "true", None, None) == "true"
    assert format_value("boolean", "false", None, None) == "false"
    with pytest.raises(PreviewEditError):
        format_value("boolean", "yes", None, None)


def test_format_enum_qualifies(cu):
    color, pkg = _lookup(cu, "Color"), cu.package
    assert format_value("enum", "Red", color, pkg) == "Color.Red"
    other = _other_package(cu)
    assert format_value("enum", "Red", color, other) == "Types.Color.Red"
    with pytest.raises(PreviewEditError):
        format_value("enum", "Blue", color, pkg)


@pytest.mark.parametrize("kind", ["reference", "other"])
def test_format_raw_kinds(kind):
    assert format_value(kind, "  r1 ", None, None) == "r1"
    with pytest.raises(PreviewEditError):
        format_value(kind, "  ", None, None)


def test_placeholders(cu):
    color, pkg = _lookup(cu, "Color"), cu.package
    assert placeholder("integer", None, pkg) == "0"
    assert placeholder("decimal", None, pkg) == "0.0"
    assert placeholder("boolean", None, pkg) == "false"
    assert placeholder("string", None, pkg) == '""'
    assert placeholder("enum", color, pkg) == "Color.Red"
    assert placeholder("enum", color, _other_package(cu)) == "Types.Color.Red"
    assert placeholder("reference", None, pkg) is None
    assert placeholder("other", None, pkg) is None
    comps = _lookup(cu, "MyRecord").components.table
    assert placeholder("array", comps["arr"].n_typ, pkg) == "[]"
    # A list that needs elements has no neutral default.
    assert placeholder("array", comps["colors"].n_typ, pkg) is None


def test_type_ref(cu):
    rec = _lookup(cu, "MyRecord")
    assert type_ref(rec, cu.package) == "MyRecord"
    assert type_ref(rec, _other_package(cu)) == "Types.MyRecord"


def test_visible_record_types_skips_abstract_and_qualifies_imports(cu):
    visible = visible_record_types(cu)
    assert "MyRecord" in visible
    assert "Other.T" in visible
    assert "Base" not in visible


@pytest.mark.parametrize(
    "value",
    [
        "a\nb\n",
        "a\r\nb",
        "a\n'",
        "a\nb''",
        'a\n"',
        "a\n''' and \"",
    ],
)
def test_format_string_multiline_edge_cases(value):
    try:
        text = format_value("string", value, None, None)
    except PreviewEditError:
        return
    tokens = _lex(text + "\n")
    assert len(tokens) == 1
    assert tokens[0].kind == "STRING"
    assert tokens[0].value == triple_quoted_string_value(text)
    assert tokens[0].value == value.replace("\r\n", "\n").strip()


def test_format_string_quote_endings_use_other_delimiter():
    assert format_value("string", "a\n'", None, None).startswith('"""')
    assert format_value("string", 'a\n"', None, None).startswith("'''")


@pytest.mark.parametrize(
    "value",
    [
        "1__0",
        "1_",
        "_1",
        "1_.5",
        "1.5e3",
        "1.5",
        "0x",
        "0x_1",
        "0x1_",
        "0xG",
        "0X1F",
        "0b",
        "0b102",
        "0b1__0",
    ],
)
def test_format_integer_rejects_malformed(value):
    with pytest.raises(PreviewEditError):
        format_value("integer", value, None, None)


@pytest.mark.parametrize(
    "value", ["1__0.5", "1_.5", "1.5_", "1._5", "1.5e3", "1"]
)
def test_format_decimal_rejects_malformed(value):
    with pytest.raises(PreviewEditError):
        format_value("decimal", value, None, None)


def test_format_reference_validation():
    assert format_value("reference", " Pkg.req_1 ", None, None) == "Pkg.req_1"
    for bad in ["r1 }", "1abc", "a..b", "a b"]:
        with pytest.raises(PreviewEditError):
            format_value("reference", bad, None, None)


def test_format_string_fallback_writes_nothing_to_stdout(capsys):
    assert format_value("string", "a\n'", None, None).startswith('"""')
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0x1F", 31),
        ("0x1_f", 31),
        ("0b101", 5),
        ("0b1_0", 2),
        ("-0x10", 16),  # sign is a separate operator token
    ],
)
def test_format_integer_accepts_hex_and_binary(value, expected):
    assert format_value("integer", value, None, None) == value
    # TRLC lexes it to the integer literal we expect.
    literal = [t for t in _lex(value) if t.kind == "INTEGER"]
    assert [t.value for t in literal] == [expected]


@pytest.mark.parametrize(
    "raw",
    [
        "A paragraph with **bold** text.",
        "| a | b |\n|---|---|\n| 1 | 2 |",
        "```py\nx = 1\n```",
        "intro\n\n- a\n  - b\n- c",
    ],
)
def test_markdown_round_trip(raw):
    literal = format_markdown(raw, "    ", "\n")
    assert triple_quoted_string_value(literal) == raw


def test_markdown_layout():
    assert (
        format_markdown("a\n\nb", "    ", "\n")
        == "'''\n        a\n\n        b\n    '''"
    )


def test_markdown_normalises_input():
    literal = format_markdown("\r\n\r\na  \r\n\r\nb\t\r\n\r\n", "", "\r\n")
    assert literal == "'''\r\n    a\r\n\r\n    b\r\n'''"


def test_markdown_unrepresentable_nesting_rejected():
    with pytest.raises(PreviewEditError, match="indentation"):
        format_markdown("- a\n  - b", "    ", "\n")


def test_markdown_uses_double_quotes_when_needed():
    raw = "it's '''q'''"
    literal = format_markdown(raw, "", "\n")
    assert literal.startswith('"""') and literal.endswith('"""')
    assert triple_quoted_string_value(literal) == raw


def test_markdown_with_both_triple_quotes_is_refused():
    with pytest.raises(PreviewEditError, match="both ''' and"):
        format_markdown("a ''' b \"\"\" c", "", "\n")
