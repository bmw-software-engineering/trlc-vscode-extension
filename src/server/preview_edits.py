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
"""Text edits for preview changes: set a field value, add a record.

All positions come from the TRLC tokens of the parsed file, so the
edits are only valid against the exact ``content`` that was parsed.
"""

# The public signatures are fixed by the preview protocol handlers.
# pylint: disable=too-many-arguments,too-many-positional-arguments

import re
from typing import Optional

from lsprotocol.types import Position, Range, TextEdit
from trlc.ast import Implicit_Null, Symbol_Table
from trlc.lexer import TRLC_Lexer

from server.preview_scan import FieldSpan, FileStructure, RecordSpan
from server.preview_values import (
    PreviewEditError,
    field_kind,
    format_markdown,
    format_value,
    placeholder,
    visible_record_types,
)
from server.token_utils import get_location

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_STEP = "    "


def _eol(content: str) -> str:
    return "\r\n" if "\r\n" in content else "\n"


def _line_start(content: str, offset: int) -> int:
    return content.rfind("\n", 0, offset) + 1


def _indent(content: str, offset: int) -> str:
    """Leading whitespace of the line containing *offset*."""
    start = _line_start(content, offset)
    end = start
    while end < len(content) and content[end] in " \t":
        end += 1
    return content[start:end]


def _only_space_before(content: str, tok) -> bool:
    start = tok.location.start_pos
    return not content[_line_start(content, start) : start].strip()


def _doc_end(content: str) -> Position:
    return Position(
        line=content.count("\n"),
        character=len(content) - _line_start(content, len(content)),
    )


def _utf16_column(line_text: str, column: int) -> int:
    """UTF-16 code units of the first *column* code points of *line_text*."""
    prefix = line_text[:column]
    return column + sum(1 for ch in prefix if ord(ch) > 0xFFFF)


def _to_utf16(content: str, edits: "list[TextEdit]") -> "list[TextEdit]":
    """Convert edit positions from code points to UTF-16 code units.

    TRLC columns count code points; LSP (and VS Code) count UTF-16 code
    units, so a non-BMP character earlier on a line shifts the column.
    """
    lines = content.split("\n")

    def conv(pos: Position) -> Position:
        text = lines[pos.line] if pos.line < len(lines) else ""
        return Position(
            line=pos.line, character=_utf16_column(text, pos.character)
        )

    return [
        TextEdit(
            range=Range(start=conv(e.range.start), end=conv(e.range.end)),
            new_text=e.new_text,
        )
        for e in edits
    ]


def _start(tok) -> Position:
    return get_location(tok).range.start


def _end(tok) -> Position:
    return get_location(tok).range.end


def _insert_before_ket(
    content: str, ket, indent: str, closing_indent: str, text: str
) -> TextEdit:
    """Insert *text* (already EOL-joined) before the closing ``}``."""
    eol = _eol(content)
    if _only_space_before(content, ket):
        pos = Position(line=_start(ket).line, character=0)
        new_text = indent + text + eol
    else:
        pos = _start(ket)
        new_text = eol + indent + text + eol + closing_indent
    return TextEdit(range=Range(start=pos, end=pos), new_text=new_text)


def _find_component(rec: RecordSpan, field: str):
    for comp in rec.obj.n_typ.all_components():
        if comp.name == field:
            return comp
    raise PreviewEditError(f"Record '{rec.obj.name}' has no field '{field}'.")


def _replace_value(tokens: list, span: FieldSpan, text: str) -> TextEdit:
    # Keep any comment between '=' and the value.
    first = span.value_first
    while first < span.value_last and tokens[first].kind == "COMMENT":
        first += 1
    return TextEdit(
        range=Range(
            start=_start(tokens[first]), end=_end(tokens[span.value_last])
        ),
        new_text=text,
    )


def _rest_of_line_is_comment(content: str, tokens: list, last: int) -> bool:
    """True if only whitespace/comments follow token *last* on its line."""
    newline = content.find("\n", tokens[last].location.end_pos + 1)
    line_end = len(content) if newline < 0 else newline
    for tok in tokens[last + 1 :]:
        if tok.kind == "EOF" or tok.location.start_pos >= line_end:
            break
        if tok.kind != "COMMENT" or tok.location.end_pos > line_end:
            return False
    return True


def _delete_assignment(
    content: str, tokens: list, span: FieldSpan
) -> TextEdit:
    name_tok = tokens[span.name_tok]
    last_tok = tokens[span.value_last]
    if _only_space_before(content, name_tok) and _rest_of_line_is_comment(
        content, tokens, span.value_last
    ):
        start = Position(line=_start(name_tok).line, character=0)
        if content.find("\n", last_tok.location.end_pos + 1) < 0:
            end = _doc_end(content)
        else:
            end = Position(line=_end(last_tok).line + 1, character=0)
    else:
        start, end = _start(name_tok), _end(last_tok)
    return TextEdit(range=Range(start=start, end=end), new_text="")


def _field_indent(content: str, structure: FileStructure, rec) -> str:
    tokens = structure.tokens
    if rec.fields:
        first = min(span.name_tok for span in rec.fields.values())
        return _indent(content, tokens[first].location.start_pos)
    return _indent(content, tokens[rec.name_tok].location.start_pos) + _STEP


def set_field_edits(
    structure: FileStructure,
    content: str,
    cur_package,
    record: str,
    field: str,
    value: Optional[str],
    markdown_components: "tuple[str, ...]" = (),
) -> "list[TextEdit]":
    """Edits that set (or, with ``value=None``, unset) a record field.

    String fields named in *markdown_components* are written as
    indented triple-quoted Markdown blocks.
    """
    rec = structure.record(record)
    if rec is None:
        raise PreviewEditError(f"Record '{record}' not found.")
    comp = _find_component(rec, field)
    if rec.obj.n_typ.is_frozen(comp):
        raise PreviewEditError(f"Field '{field}' is frozen.")
    if value is None and not comp.optional:
        raise PreviewEditError(f"Field '{field}' is required.")

    tokens = structure.tokens
    span = rec.fields.get(field)
    current = rec.obj.field.get(field)
    if (
        span is None
        and current is not None
        and not isinstance(current, Implicit_Null)
    ):
        # Set somewhere we cannot locate: inserting would duplicate it.
        raise PreviewEditError(
            f"Cannot locate the assignment of '{field}'; "
            "edit it in the text editor."
        )
    if value is None:
        if span is None:
            return []
        return _to_utf16(content, [_delete_assignment(content, tokens, span)])

    kind = field_kind(comp.n_typ)
    if kind == "string" and field in markdown_components:
        text = format_markdown(
            value, _field_indent(content, structure, rec), _eol(content)
        )
    else:
        text = format_value(kind, value, comp.n_typ, cur_package)
        # Multi-line string literals must use the file's line endings.
        text = text.replace("\n", _eol(content))
    if span is not None:
        return _to_utf16(content, [_replace_value(tokens, span, text)])
    return _to_utf16(
        content,
        [
            _insert_before_ket(
                content,
                tokens[rec.close_tok],
                _field_indent(content, structure, rec),
                _indent(content, tokens[rec.name_tok].location.start_pos),
                f"{field} = {text}",
            )
        ],
    )


def _record_text(cu, type_name: str, name: str, eol: str) -> str:
    """Record declaration; lines after the first are joined by *eol*."""
    typ = visible_record_types(cu)[type_name]
    lines = [f"{type_name} {name} {{"]
    for comp in typ.all_components():
        if comp.optional or typ.is_frozen(comp):
            continue
        text = placeholder(field_kind(comp.n_typ), comp.n_typ, cu.package)
        if text is not None:
            lines.append(f"{_STEP}{comp.name} = {text}")
    lines.append("}")
    return eol.join(lines)


def add_record_edits(
    structure: FileStructure,
    content: str,
    cu,
    section_id: Optional[int],
    type_name: str,
    name: str,
) -> "list[TextEdit]":
    """Edits that add a new record at file level or into a section."""
    if not _NAME.match(name) or name in TRLC_Lexer.KEYWORDS:
        raise PreviewEditError(f"'{name}' is not a valid record name.")
    # TRLC's own rule: names must be sufficiently distinct (case and
    # underscores are ignored), so 'Q' clashes with an existing 'q'.
    if cu.package.symbols.contains_raw(Symbol_Table.simplified_name(name)):
        raise PreviewEditError(
            f"The name '{name}' is already in use (or too similar to "
            "an existing name)."
        )
    if type_name not in visible_record_types(cu):
        raise PreviewEditError(f"Unknown record type '{type_name}'.")
    section = None
    if section_id is not None:
        section = structure.section(section_id)
        if section is None:
            raise PreviewEditError("The section no longer exists.")
        if section.close_tok is None:
            raise PreviewEditError(
                "The section is not closed; fix it in the editor."
            )

    eol = _eol(content)
    if section is None:
        pos = _doc_end(content)
        text = _record_text(cu, type_name, name, eol)
        return _to_utf16(
            content,
            [
                TextEdit(
                    range=Range(start=pos, end=pos),
                    new_text=eol + text + eol,
                )
            ],
        )

    tokens = structure.tokens
    closing_indent = _indent(
        content, tokens[section.keyword_tok].location.start_pos
    )
    indent = closing_indent + _STEP
    return _to_utf16(
        content,
        [
            _insert_before_ket(
                content,
                tokens[section.close_tok],
                indent,
                closing_indent,
                _record_text(cu, type_name, name, eol + indent),
            )
        ],
    )
