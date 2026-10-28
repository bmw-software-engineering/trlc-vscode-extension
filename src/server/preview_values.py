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
"""Value kinds and TRLC literal formatting for the preview editor."""

import io
import re
from typing import Optional

from trlc.ast import (
    Array_Type,
    Builtin_Boolean,
    Builtin_Decimal,
    Builtin_Integer,
    Builtin_String,
    Enumeration_Type,
    Record_Type,
    Union_Type,
)
from trlc.errors import Message_Handler, TRLC_Error
from trlc.lexer import TRLC_Lexer, triple_quoted_string_value

# Decimal, hex (0x) and binary (0b) literals with TRLC's '_' separators
# (each '_' must sit between two digits).
_INTEGER = re.compile(
    r"^[+-]?(\d+(_\d+)*"
    r"|0x[0-9a-fA-F]+(_[0-9a-fA-F]+)*"
    r"|0b[01]+(_[01]+)*)$"
)
_DECIMAL = re.compile(r"^[+-]?\d+(_\d+)*\.\d+(_\d+)*$")
_REFERENCE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)*$")


_MARKDOWN_STEP = "    "
_SINGLE3 = "'" * 3
_DOUBLE3 = '"' * 3


class PreviewEditError(Exception):
    """User-facing message in str(e)."""


_KINDS = (
    (Builtin_String, "string"),
    (Builtin_Integer, "integer"),
    (Builtin_Decimal, "decimal"),
    (Builtin_Boolean, "boolean"),
    (Enumeration_Type, "enum"),
    (Record_Type, "reference"),
    # A component typed [A, B] references a record of either type.
    (Union_Type, "reference"),
)


def _scalar_kind(n_typ) -> str:
    for cls, kind in _KINDS:
        if isinstance(n_typ, cls):
            return kind
    return "other"


def element_type(n_typ):
    """Element type of an array type, or None for other types."""
    return n_typ.element_type if isinstance(n_typ, Array_Type) else None


def field_kind(n_typ) -> str:
    """Classify a TRLC type into a preview editor value kind.

    Arrays of scalars are "array" (their elements have the kind of
    the element type); other arrays are "other" (raw text).
    """
    elem = element_type(n_typ)
    if elem is not None:
        return "other" if _scalar_kind(elem) == "other" else "array"
    return _scalar_kind(n_typ)


def type_ref(entity, cur_package) -> str:
    """Name of entity, package-qualified outside the current package."""
    if entity.n_package is cur_package:
        return entity.name
    return f"{entity.n_package.name}.{entity.name}"


def _lexes_to(literal: str, expected: str) -> bool:
    """True if literal lexes to exactly one STRING token == expected."""
    # The newline avoids a TRLC lexer quirk at the very end of input.
    lexer = TRLC_Lexer(
        Message_Handler(out=io.StringIO()), "preview", literal + "\n"
    )
    try:
        first = lexer.token()
        second = lexer.token()
    except TRLC_Error:
        return False
    return (
        first is not None
        and first.kind == "STRING"
        and first.value == expected
        and (second is None or second.kind == "EOF")
    )


def _format_string(raw: str) -> str:
    """Render raw as a TRLC string literal.

    Line endings are normalised to LF. Multi-line values are written as
    triple-quoted strings and are therefore subject to TRLC's
    triple-quote normalisation (outer whitespace stripped, common
    indentation removed, trailing line whitespace removed). Every
    candidate is re-lexed to guarantee the file stays valid.
    """
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in raw and not raw.endswith("\\"):
        literal = '"' + raw.replace('"', '\\"') + '"'
        if _lexes_to(literal, raw):
            return literal
    for quote in ("'''", '"""'):
        if quote in raw:
            continue
        literal = quote + raw + quote
        if _lexes_to(literal, triple_quoted_string_value(literal)):
            return literal
    raise PreviewEditError("This text cannot be written as a TRLC string.")


def format_markdown(raw: str, indent: str, eol: str) -> str:
    """Render Markdown as an indented triple-quoted TRLC string.

    Layout: opening quotes, the body indented one step deeper than
    *indent*, closing quotes at *indent*. The input is normalised first
    (LF line ends, no trailing whitespace, no outer blank lines). Raises
    PreviewEditError if TRLC's triple-quote dedent would alter the text.
    """
    norm = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in norm.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    expected = "\n".join(lines)
    if _SINGLE3 in expected and _DOUBLE3 in expected:
        raise PreviewEditError(
            "This Markdown contains both ''' and \"\"\" and cannot be "
            "stored as a TRLC string."
        )
    body_indent = indent + _MARKDOWN_STEP
    body = "\n".join(body_indent + line if line else "" for line in lines)
    quote = _SINGLE3 if _SINGLE3 not in expected else _DOUBLE3
    literal = f"{quote}\n{body}\n{indent}{quote}"
    if quote in expected or not _lexes_to(literal, expected):
        raise PreviewEditError(
            "This Markdown cannot be stored as a TRLC string "
            "(indentation of the first lines would be lost)."
        )
    return literal.replace("\n", eol)


def _enum_literal(name: str, n_typ, cur_package) -> str:
    if name not in [lit.name for lit in n_typ.literals.table.values()]:
        raise PreviewEditError(f"'{name}' is not a literal of {n_typ.name}.")
    return f"{type_ref(n_typ, cur_package)}.{name}"


def format_value(kind: str, raw: str, n_typ, cur_package) -> str:
    """Render raw user input as TRLC source text, or raise."""
    if kind == "string":
        return _format_string(raw)
    text = raw.strip()
    if kind == "integer":
        if not _INTEGER.match(text):
            raise PreviewEditError(f"'{text}' is not a valid integer.")
        return text
    if kind == "decimal":
        if not _DECIMAL.match(text):
            raise PreviewEditError(f"'{text}' is not a valid decimal.")
        return text
    if kind == "boolean":
        if text not in ("true", "false"):
            raise PreviewEditError("Boolean must be 'true' or 'false'.")
        return text
    if kind == "enum":
        return _enum_literal(text, n_typ, cur_package)
    if not text:
        raise PreviewEditError("Value must not be empty.")
    if kind == "reference" and not _REFERENCE.match(text):
        raise PreviewEditError(f"'{text}' is not a valid reference.")
    return text


def format_array(items, n_typ, cur_package) -> str:
    """Render a list of raw element inputs as a TRLC array literal.

    Each element is formatted like a single value of the element type;
    the number of elements must satisfy the array's bounds.
    """
    if not isinstance(items, list) or not all(
        isinstance(item, str) for item in items
    ):
        raise PreviewEditError("A list value must be a list of texts.")
    if len(items) < n_typ.lower_bound:
        raise PreviewEditError(
            f"The list needs at least {n_typ.lower_bound} element(s)."
        )
    if n_typ.upper_bound is not None and len(items) > n_typ.upper_bound:
        raise PreviewEditError(
            f"The list allows at most {n_typ.upper_bound} element(s)."
        )
    elem = n_typ.element_type
    kind = _scalar_kind(elem)
    return (
        "["
        + ", ".join(
            format_value(kind, item, elem, cur_package) for item in items
        )
        + "]"
    )


_PLACEHOLDERS = {
    "integer": "0",
    "decimal": "0.0",
    "boolean": "false",
    "string": '""',
}


def placeholder(kind: str, n_typ, cur_package) -> Optional[str]:
    """Default TRLC text for a newly added field, or None to omit it.

    For enums this is the first literal in declaration order; arrays
    get an empty list unless they need elements.
    """
    if kind in _PLACEHOLDERS:
        return _PLACEHOLDERS[kind]
    if kind == "array" and n_typ.lower_bound == 0:
        return "[]"
    if kind == "enum":
        first = next(iter(n_typ.literals.table.values()), None)
        if first is not None:
            return _enum_literal(first.name, n_typ, cur_package)
    return None


def visible_record_types(cu) -> "dict[str, Record_Type]":
    """Non-abstract record types of cu's package and its imports."""
    result = {}
    for pkg in [cu.package, *cu.imports]:
        for typ in pkg.symbols.values(Record_Type):
            if not typ.is_abstract:
                result[type_ref(typ, cu.package)] = typ
    return result
