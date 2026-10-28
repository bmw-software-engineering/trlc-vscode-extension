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
from typing import Optional, Union

from lsprotocol.types import Position, Range, TextEdit
from trlc.ast import Implicit_Null, Symbol_Table
from trlc.lexer import TRLC_Lexer

from server.preview_scan import FieldSpan, FileStructure, RecordSpan
from server.preview_values import (
    PreviewEditError,
    field_kind,
    format_array,
    format_markdown,
    format_value,
    placeholder,
    visible_record_types,
)
from server.token_utils import get_location

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_STEP = "    "


def _eol(content: str) -> str:
    """Line ending used by the file.

    Args:
        content: Full text of the file.

    Returns:
        CRLF if the file contains any CRLF, else LF.
    """
    return "\r\n" if "\r\n" in content else "\n"


def _line_start(content: str, offset: int) -> int:
    """Character offset of the start of the line containing *offset*.

    Args:
        content: Full text of the file.
        offset: Character offset (code points) into *content*.

    Returns:
        Offset just after the last newline before *offset*, or 0 on the
        first line.
    """
    return content.rfind("\n", 0, offset) + 1


def _indent(content: str, offset: int) -> str:
    """Leading whitespace of the line containing *offset*.

    Args:
        content: Full text of the file.
        offset: Character offset (code points) into *content*.

    Returns:
        The run of spaces and tabs at the start of that line (possibly
        empty).
    """
    start = _line_start(content, offset)
    end = start
    while end < len(content) and content[end] in " \t":
        end += 1
    return content[start:end]


def _only_space_before(content: str, tok) -> bool:
    """Tell whether a token is the first thing on its line.

    Args:
        content: Full text of the file.
        tok: TRLC token; its ``location.start_pos`` is a character offset.

    Returns:
        True if only whitespace precedes *tok* on its line.
    """
    start = tok.location.start_pos
    return not content[_line_start(content, start) : start].strip()


def _doc_end(content: str) -> Position:
    """Position just after the last character of the file.

    Args:
        content: Full text of the file.

    Returns:
        LSP position with the zero-based last line and the column counted
        in code points (not yet converted to UTF-16).
    """
    return Position(
        line=content.count("\n"),
        character=len(content) - _line_start(content, len(content)),
    )


def _utf16_column(line_text: str, column: int) -> int:
    """UTF-16 code units of the first *column* code points of *line_text*.

    Args:
        line_text: Text of a single line, without its line ending.
        column: Number of code points from the start of the line.

    Returns:
        The same distance in UTF-16 code units; each character beyond
        the BMP counts as two.
    """
    prefix = line_text[:column]
    return column + sum(1 for ch in prefix if ord(ch) > 0xFFFF)


def _to_utf16(content: str, edits: "list[TextEdit]") -> "list[TextEdit]":
    """Convert edit positions from code points to UTF-16 code units.

    TRLC columns count code points; LSP (and VS Code) count UTF-16 code
    units, so a non-BMP character earlier on a line shifts the column.

    Args:
        content: Full text of the file the edits apply to.
        edits: Edits whose positions are zero-based line and code point
            column.

    Returns:
        New edits with the same text and lines but UTF-16 columns.
    """
    lines = content.split("\n")

    def conv(pos: Position) -> Position:
        """Convert one position to UTF-16 columns.

        Args:
            pos: Position with a code point column.

        Returns:
            Position on the same line with a UTF-16 column; a line past
            the end of the file is treated as empty.
        """
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
    """Start of a token as an LSP position.

    Args:
        tok: TRLC token.

    Returns:
        Zero-based line and code point column of the token start.
    """
    return get_location(tok).range.start


def _end(tok) -> Position:
    """End of a token as an LSP position.

    Args:
        tok: TRLC token.

    Returns:
        Zero-based line and code point column just past the token.
    """
    return get_location(tok).range.end


def _insert_before_ket(
    content: str, ket, indent: str, closing_indent: str, text: str
) -> TextEdit:
    """Insert *text* (already EOL-joined) before the closing ``}``.

    If the ``}`` stands alone on its line the text becomes new lines
    above it; otherwise the ``}`` is moved onto a line of its own.

    Args:
        content: Full text of the file.
        ket: Token of the closing ``}``.
        indent: Indentation for the inserted text.
        closing_indent: Indentation for a ``}`` that has to move to a new
            line.
        text: Text to insert, lines already joined with the file's EOL.

    Returns:
        A zero-width insertion edit (code point columns).
    """
    eol = _eol(content)
    if _only_space_before(content, ket):
        pos = Position(line=_start(ket).line, character=0)
        new_text = indent + text + eol
    else:
        pos = _start(ket)
        new_text = eol + indent + text + eol + closing_indent
    return TextEdit(range=Range(start=pos, end=pos), new_text=new_text)


def _find_component(rec: RecordSpan, field: str):
    """Look up a field's component declaration on a record's type.

    Args:
        rec: Record to search.
        field: Field name.

    Returns:
        The matching ``Composite_Component``.

    Raises:
        PreviewEditError: If the record type has no such field.
    """
    for comp in rec.obj.n_typ.all_components():
        if comp.name == field:
            return comp
    raise PreviewEditError(f"Record '{rec.obj.name}' has no field '{field}'.")


def _replace_value(tokens: list, span: FieldSpan, text: str) -> TextEdit:
    """Replace the value of an existing assignment.

    Args:
        tokens: Token list of the file.
        span: Token range of the assignment; its indices are token
            indices, ``value_last`` is inclusive.
        text: Replacement source text for the value.

    Returns:
        An edit covering the value tokens (code point columns).
    """
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
    """True if only whitespace/comments follow token *last* on its line.

    Args:
        content: Full text of the file.
        tokens: Token list of the file.
        last: Token index of the last token of the assignment.

    Returns:
        True if every following token on that line is a comment that
        ends on it; False if real code or a multi-line comment follows.
    """
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
    """Remove an assignment, taking its whole line when that is safe.

    Args:
        content: Full text of the file.
        tokens: Token list of the file.
        span: Token range of the assignment to delete.

    Returns:
        An edit with empty ``new_text`` (code point columns). It covers
        the full line(s) if the assignment is alone on its line (a
        trailing comment is removed too), else just the assignment.
    """
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
    """Indentation to use for a field of *rec*.

    Args:
        content: Full text of the file.
        structure: Scanned structure of the file.
        rec: ``RecordSpan`` of the record.

    Returns:
        The indentation of the record's first explicit field, or the
        record's own indentation plus one step if it has none.
    """
    tokens = structure.tokens
    if rec.fields:
        first = min(span.name_tok for span in rec.fields.values())
        return _indent(content, tokens[first].location.start_pos)
    return _indent(content, tokens[rec.name_tok].location.start_pos) + _STEP


def _value_text(
    content: str, structure, rec, comp, value, markdown: bool, cur_package
) -> str:
    """TRLC source text of *value* for component *comp* of *rec*.

    Args:
        content: Full text of the file (for line endings and indent).
        structure: Scanned structure of the file.
        rec: ``RecordSpan`` of the record being edited.
        comp: ``Composite_Component`` of the field.
        value: A list of element inputs for array fields, else a single
            input text.
        markdown: Write a string field as an indented triple-quoted
            Markdown block.
        cur_package: Package of the file, used to qualify enum literals.

    Returns:
        Source text with the file's line endings.

    Raises:
        PreviewEditError: If a non-array field is given a list, or the
            input cannot be formatted for the field type.
    """
    kind = field_kind(comp.n_typ)
    if kind == "array":
        text = format_array(value, comp.n_typ, cur_package)
    elif not isinstance(value, str):
        raise PreviewEditError(f"Field '{comp.name}' takes a single value.")
    elif kind == "string" and markdown:
        return format_markdown(
            value, _field_indent(content, structure, rec), _eol(content)
        )
    else:
        text = format_value(kind, value, comp.n_typ, cur_package)
    # Multi-line string literals must use the file's line endings.
    return text.replace("\n", _eol(content))


def set_field_edits(
    structure: FileStructure,
    content: str,
    cur_package,
    record: str,
    field: str,
    value: Optional[Union[str, "list[str]"]],
    markdown_components: "tuple[str, ...]" = (),
) -> "list[TextEdit]":
    """Edits that set (or, with ``value=None``, unset) a record field.

    String fields named in *markdown_components* are written as
    indented triple-quoted Markdown blocks. Array fields take a list
    of element inputs, every other field a single text.

    Args:
        structure: Scanned structure of the file.
        content: Exact text that was parsed.
        cur_package: Package of the file, used to qualify enum literals.
        record: Name of the record to edit.
        field: Name of the field to set.
        value: New value (list for arrays, text otherwise), or None to
            remove the assignment.
        markdown_components: Names of string fields to write as Markdown
            blocks.

    Returns:
        Edits with UTF-16 positions; empty if *value* is None and the
        field is not assigned.

    Raises:
        PreviewEditError: If the record or field is unknown, the field is
            frozen, a required field is unset, the assignment cannot be
            located, or the value is invalid for the field.
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

    text = _value_text(
        content,
        structure,
        rec,
        comp,
        value,
        comp.name in markdown_components,
        cur_package,
    )
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
    """Record declaration; lines after the first are joined by *eol*.

    Args:
        cu: Compilation unit (package) providing the record types.
        type_name: Name of a visible record type.
        name: Name of the new record.
        eol: Separator between lines, which may include indentation.

    Returns:
        Text with one placeholder assignment per required, non-frozen
        field.
    """
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
    """Edits that add a new record at file level or into a section.

    Args:
        structure: Scanned structure of the file.
        content: Exact text that was parsed.
        cu: Compilation unit (package) of the file.
        section_id: Section to add the record to, or None for the end of
            the file.
        type_name: Name of the record type.
        name: Name of the new record.

    Returns:
        A single insertion edit with UTF-16 positions.

    Raises:
        PreviewEditError: If the name is invalid or already used, the
            type is unknown, or the section is missing or unclosed.
    """
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
