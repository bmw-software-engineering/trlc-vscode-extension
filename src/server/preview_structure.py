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
"""Structural preview edits: insert, rename, dissolve sections; move items.

Every edit is applied in memory and checked by
``verify_structure_edit`` before it is returned, so an edit that would
change a record's values or break the brace structure is refused.
"""

import io
from collections import Counter
from dataclasses import dataclass
from typing import NamedTuple, Optional

from lsprotocol.types import Position, Range, TextEdit
from trlc.errors import Message_Handler, TRLC_Error
from trlc.lexer import TRLC_Lexer

from server.preview_edits import (
    _doc_end,
    _end,
    _eol,
    _indent,
    _insert_before_ket,
    _line_start,
    _start,
    _to_utf16,
)
from server.preview_layout import (
    Extent,
    indent_step,
    record_extent,
    section_extent,
    shift_block,
)
from server.preview_scan import FileStructure, SectionSpan
from server.preview_values import PreviewEditError, format_value

_BLANK = " \t\r"


# --- applying and verifying -------------------------------------------------


def _offset(lines: "list[int]", pos: Position) -> int:
    return lines[pos.line] + pos.character


def apply_text_edits(content: str, edits: "list[TextEdit]") -> str:
    """Apply non-overlapping *edits* (code-point positions) to *content*."""
    lines = [0]
    pos = content.find("\n")
    while pos >= 0:
        lines.append(pos + 1)
        pos = content.find("\n", pos + 1)
    spans = sorted(
        (
            (
                _offset(lines, e.range.start),
                _offset(lines, e.range.end),
                e.new_text,
            )
            for e in edits
        ),
        key=lambda span: span[:2],
        reverse=True,
    )
    for start, end, text in spans:
        content = content[:start] + text + content[end:]
    return content


def _lex(text: str) -> list:
    """Non-comment tokens of *text*; raises PreviewEditError.

    *text* must end with a newline (avoids a TRLC lexer quirk at the
    very end of input).
    """
    lexer = TRLC_Lexer(Message_Handler(out=io.StringIO()), "preview", text)
    tokens = []
    try:
        while True:
            tok = lexer.token()
            if tok is None or tok.kind == "EOF":
                break
            if tok.kind != "COMMENT":
                tokens.append(tok)
    except TRLC_Error as exc:
        raise PreviewEditError(
            "The edit would produce text that cannot be read."
        ) from exc
    if lexer.mh.errors:
        raise PreviewEditError(
            "The edit would produce text that cannot be read."
        )
    return tokens


def _text(content: str, tok) -> str:
    if tok.kind == "STRING":
        return tok.value
    return content[tok.location.start_pos : tok.location.end_pos + 1]


def _is_record_start(tokens: list, idx: int) -> Optional[int]:
    """Index of the record's ``{`` if a declaration starts at *idx*."""
    kinds = [tok.kind for tok in tokens[idx : idx + 5]]
    if kinds[:3] == ["IDENTIFIER", "IDENTIFIER", "C_BRA"]:
        return idx + 2
    if kinds == ["IDENTIFIER", "DOT", "IDENTIFIER", "IDENTIFIER", "C_BRA"]:
        return idx + 4
    return None


def _record_entries(
    content: str, tokens: list, first: int, bra: int, entries: list
) -> int:
    """Add the record declared at ``[first, bra]``; return its ``}``."""
    name = tokens[bra - 1].value
    typ = " ".join(_text(content, tok) for tok in tokens[first : bra - 1])
    entries.append((name, "", typ))
    field, value = None, []
    idx = bra + 1
    while idx < len(tokens) and tokens[idx].kind != "C_KET":
        tok = tokens[idx]
        if (
            tok.kind == "IDENTIFIER"
            and idx + 1 < len(tokens)
            and tokens[idx + 1].kind == "ASSIGN"
        ):
            if field is not None:
                entries.append((name, field, " ".join(value)))
            field, value = tok.value, []
            idx += 2
            continue
        value.append(_text(content, tok))
        idx += 1
    if field is not None:
        entries.append((name, field, " ".join(value)))
    return idx


def _entries(content: str, tokens: list) -> Counter:
    """Multiset of (record, field, value text) in a lexed file.

    Each record also contributes ``(name, "", type)``, so a record
    without explicit assignments is accounted for as well.
    """
    entries: list = []
    idx = 0
    while idx < len(tokens):
        bra = _is_record_start(tokens, idx)
        if bra is None:
            idx += 1
        else:
            idx = _record_entries(content, tokens, idx, bra, entries) + 1
    return Counter(entries)


def _balanced(tokens: list) -> bool:
    depth = 0
    for tok in tokens:
        if tok.kind == "C_BRA":
            depth += 1
        elif tok.kind == "C_KET":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def verify_structure_edit(old: str, new: str) -> None:
    """Refuse an edit that changes records, values or brace balance."""
    # The newline avoids a TRLC lexer quirk at the very end of input.
    old, new = old + "\n", new + "\n"
    old_tokens = _lex(old)
    new_tokens = _lex(new)
    if not _balanced(new_tokens):
        raise PreviewEditError("The edit would leave unbalanced braces.")
    if _entries(old, old_tokens) != _entries(new, new_tokens):
        raise PreviewEditError(
            "The edit would change records or their values; refused."
        )


# --- edits ------------------------------------------------------------------


def _title(title: str) -> str:
    if "\n" in title or "\r" in title or not title.strip():
        raise PreviewEditError("A section title must be a non-empty line.")
    return format_value("string", title, None, None)


def _section(structure: FileStructure, section_id: int) -> SectionSpan:
    sec = structure.section(section_id)
    if sec is None:
        raise PreviewEditError("The section no longer exists.")
    return sec


def _closed(structure: FileStructure, section_id: int) -> SectionSpan:
    sec = _section(structure, section_id)
    if sec.close_tok is None:
        raise PreviewEditError(
            "The section is not closed; fix it in the editor."
        )
    return sec


def _checked(content: str, edits: "list[TextEdit]") -> "list[TextEdit]":
    """Verify code-point *edits* against *content*; return them in UTF-16."""
    verify_structure_edit(content, apply_text_edits(content, edits))
    return _to_utf16(content, edits)


def _first_on_line(content: str, offset: int) -> bool:
    return not content[_line_start(content, offset) : offset].strip()


def _position(content: str, offset: int) -> Position:
    return Position(
        line=content.count("\n", 0, offset),
        character=offset - _line_start(content, offset),
    )


def insert_section_edits(
    structure: FileStructure,
    content: str,
    parent_id: Optional[int],
    title: str,
) -> "list[TextEdit]":
    """Edits that append an empty section to a section or the file."""
    header = f"section {_title(title)} {{"
    eol = _eol(content)
    if parent_id is None:
        pos = _doc_end(content)
        lead = eol if content.endswith("\n") else eol + eol
        edit = TextEdit(
            range=Range(start=pos, end=pos),
            new_text=lead + header + eol + "}" + eol,
        )
        return _checked(content, [edit])

    parent = _closed(structure, parent_id)
    tokens = structure.tokens
    closing_indent = _indent(
        content, tokens[parent.keyword_tok].location.start_pos
    )
    indent = closing_indent + indent_step(structure, content)
    edit = _insert_before_ket(
        content,
        tokens[parent.close_tok],
        indent,
        closing_indent,
        header + eol + indent + "}",
    )
    return _checked(content, [edit])


def rename_section_edits(
    structure: FileStructure, content: str, section_id: int, title: str
) -> "list[TextEdit]":
    """Edits that replace the title string of a section."""
    text = _title(title)
    sec = _closed(structure, section_id)
    tokens = structure.tokens
    idx = sec.keyword_tok + 1
    while idx < len(tokens) and tokens[idx].kind == "COMMENT":
        idx += 1
    if idx >= len(tokens) or tokens[idx].kind != "STRING":
        raise PreviewEditError("The section title cannot be located.")
    edit = TextEdit(
        range=Range(start=_start(tokens[idx]), end=_end(tokens[idx])),
        new_text=text,
    )
    return _checked(content, [edit])


def _comment(content: str, tok) -> str:
    """Source text of a comment token, without a CRLF's CR."""
    loc = tok.location
    return content[loc.start_pos : loc.end_pos + 1].rstrip("\r")


def _same_line(content: str, prev_end: int, tok) -> bool:
    """True if *tok* starts on the line where offset *prev_end* is."""
    return "\n" not in content[prev_end : tok.location.start_pos]


def _header(content: str, tokens: list, sec: SectionSpan):
    """(comments to keep, body start offset) of a section's header."""
    bra = sec.keyword_tok
    while tokens[bra].kind != "C_BRA":
        bra += 1
    kept = [
        tok
        for tok in tokens[sec.keyword_tok + 1 : bra]
        if tok.kind == "COMMENT"
    ]
    end = tokens[bra].location.end_pos + 1
    trailing = []
    idx = bra + 1
    while (
        idx < len(tokens)
        and tokens[idx].kind == "COMMENT"
        and _same_line(content, end, tokens[idx])
    ):
        trailing.append(tokens[idx])
        end = tokens[idx].location.end_pos + 1
        idx += 1
    newline = content.find("\n", end)
    line_end = len(content) if newline < 0 else newline
    if content[end:line_end].strip(_BLANK):
        # Content follows on the header line: it is part of the body.
        return kept, tokens[bra].location.end_pos + 1
    return kept + trailing, min(line_end + 1, len(content))


def _comment_lines(content: str, comments: list, indent: str, eol: str):
    """*comments* as standalone lines at *indent*."""
    return "".join(indent + _comment(content, tok) + eol for tok in comments)


def _body(
    structure: FileStructure,
    content: str,
    sec: SectionSpan,
    body_from: int,
    eol: str,
) -> str:
    """The section's content, dedented one step, ending with *eol*."""
    tokens = structure.tokens
    close_pos = tokens[sec.close_tok].location.start_pos
    close_first = _first_on_line(content, close_pos)
    body_to = _line_start(content, close_pos) if close_first else close_pos
    body_to = max(body_to, body_from)
    body = shift_block(
        content,
        tokens,
        body_from,
        body_to,
        -1,
        indent_step(structure, content),
    )
    if not body.strip(_BLANK + "\n"):
        return ""
    if body_from > 0 and content[body_from - 1] != "\n":
        # Starts on the header line: put it on its own line.
        indent = _indent(content, tokens[sec.keyword_tok].location.start_pos)
        body = indent + body.lstrip(" \t")
    if not close_first:
        body = body.rstrip(" \t") + eol
    return body


def _keep_final_eol(content: str, ext) -> bool:
    """True if the replacement of *ext* must end with an EOL."""
    if ext.whole_lines:
        return content[ext.start : ext.end].endswith("\n")
    newline = content.find("\n", ext.end)
    rest = content[ext.end : len(content) if newline < 0 else newline]
    return bool(rest.strip(_BLANK))


def dissolve_section_edits(
    structure: FileStructure, content: str, section_id: int
) -> "list[TextEdit]":
    """Edit that removes a section's braces, keeping its content.

    The content moves up one nesting level and is dedented one step;
    comments of the header and of the closing brace become standalone
    lines at the section's former indentation. Comment lines directly
    above the section stay where they are.
    """
    sec = _closed(structure, section_id)
    tokens = structure.tokens
    ext = section_extent(structure, content, sec)
    eol = _eol(content)
    kw_pos = tokens[sec.keyword_tok].location.start_pos
    indent = _indent(content, kw_pos)
    comments, body_from = _header(content, tokens, sec)
    closing = [
        tok
        for tok in tokens[sec.close_tok + 1 :]
        if tok.kind == "COMMENT" and tok.location.start_pos < ext.end
    ]

    # Comment lines above the section (and, for a section that does not
    # start its line, the text before it) stay as they are.
    above_end = kw_pos
    if _first_on_line(content, kw_pos):
        above_end = _line_start(content, kw_pos)
    text = "".join(
        [
            content[ext.start : above_end],
            _comment_lines(content, comments, indent, eol),
            _body(structure, content, sec, body_from, eol),
            _comment_lines(content, closing, indent, eol),
        ]
    )
    if not _keep_final_eol(content, ext) and text.endswith(eol):
        text = text[: -len(eol)]
    return _checked(
        content,
        [
            TextEdit(
                range=Range(
                    start=_position(content, ext.start),
                    end=_position(content, ext.end),
                ),
                new_text=text,
            )
        ],
    )


# --- move -------------------------------------------------------------------


@dataclass(frozen=True)
class ItemRef:
    """A record (by name) or a section (by id) in a move request."""

    kind: str  # "record" | "section"
    name: Optional[str] = None  # record
    section_id: Optional[int] = None  # section


class _Item(NamedTuple):
    """A resolved :class:`ItemRef`."""

    key: tuple  # ("record", name) | ("section", section_id)
    container: Optional[int]  # enclosing section id, None at file level
    level: int  # number of enclosing sections
    extent: Extent


def _resolve(structure: FileStructure, content: str, ref: ItemRef) -> _Item:
    if ref.kind == "record":
        rec = structure.record(ref.name) if ref.name else None
        if rec is None:
            raise PreviewEditError("The record no longer exists.")
        level = 0
        if rec.section_id is not None:
            level = _section(structure, rec.section_id).depth
        return _Item(
            ("record", rec.obj.name),
            rec.section_id,
            level,
            record_extent(structure, content, rec),
        )
    if ref.kind == "section" and ref.section_id is not None:
        sec = _closed(structure, ref.section_id)
        return _Item(
            ("section", sec.section_id),
            sec.parent_id,
            sec.depth - 1,
            section_extent(structure, content, sec),
        )
    raise PreviewEditError("Unknown item to move.")


def _children(structure: FileStructure, target: Optional[int]) -> list:
    """Keys of the direct children of *target* in source order."""
    items = [
        (rec.name_tok, ("record", rec.obj.name))
        for rec in structure.records
        if rec.section_id == target
    ]
    items += [
        (sec.keyword_tok, ("section", sec.section_id))
        for sec in structure.sections
        if sec.parent_id == target
    ]
    return [key for _, key in sorted(items)]


def _same_position(
    structure: FileStructure,
    moved: _Item,
    target: Optional[int],
    anchor: Optional[_Item],
) -> bool:
    if moved.container != target:
        return False
    kids = _children(structure, target)
    idx = kids.index(moved.key)
    if anchor is None:
        return idx == len(kids) - 1
    following = kids[idx + 1] if idx + 1 < len(kids) else None
    return anchor.key in (moved.key, following)


def _reject_descendant(
    structure: FileStructure, section_id: int, target: Optional[int]
) -> None:
    while target is not None:
        if target == section_id:
            raise PreviewEditError(
                "A section cannot be moved into itself or its subsections."
            )
        target = _section(structure, target).parent_id


def _edit(content: str, start: int, end: int, text: str) -> TextEdit:
    return TextEdit(
        range=Range(
            start=_position(content, start), end=_position(content, end)
        ),
        new_text=text,
    )


def _cut(content: str, ext: Extent) -> TextEdit:
    """Edit that deletes *ext*.

    A token-range extent also loses one adjacent space, and its line
    goes entirely if nothing but whitespace is left on it.
    """
    start, end = ext.start, ext.end
    if not ext.whole_lines:
        if end < len(content) and content[end] in " \t":
            end += 1
        elif start > 0 and content[start - 1] in " \t":
            start -= 1
        line_start = _line_start(content, start)
        newline = content.find("\n", end)
        line_end = len(content) if newline < 0 else newline
        rest = content[line_start:start] + content[end:line_end]
        if not rest.strip(_BLANK):
            start, end = line_start, min(line_end + 1, len(content))
    return _edit(content, start, end, "")


def _target_indent(
    structure: FileStructure, content: str, target: Optional[SectionSpan]
) -> str:
    """Indentation of the direct children of *target*."""
    if target is None:
        return ""
    keyword = structure.tokens[target.keyword_tok]
    return _indent(content, keyword.location.start_pos) + indent_step(
        structure, content
    )


def _paste(
    structure: FileStructure,
    content: str,
    target: Optional[SectionSpan],
    anchor: Optional[_Item],
    block: str,
) -> TextEdit:
    """Edit that inserts *block* (complete lines) at the drop position."""
    eol = _eol(content)
    if anchor is not None:
        start = anchor.extent.start
        if _first_on_line(content, start):
            pos = _line_start(content, start)
            return _edit(content, pos, pos, block)
        # The anchor shares its line: it moves onto a fresh line, too.
        gap = start
        while gap > 0 and content[gap - 1] in " \t":
            gap -= 1
        indent = _target_indent(structure, content, target)
        return _edit(content, gap, start, eol + block + indent)
    if target is None:
        lead = eol if content.endswith("\n") else eol + eol
        return _edit(content, len(content), len(content), lead + block)
    tokens = structure.tokens
    return _insert_before_ket(
        content,
        tokens[target.close_tok],
        "",
        _indent(content, tokens[target.keyword_tok].location.start_pos),
        block[: -len(eol)] if block.endswith(eol) else block[:-1],
    )


def _block(
    structure: FileStructure,
    content: str,
    moved: _Item,
    levels: int,
    indent: str,
) -> str:
    """The moved item's text as complete lines, shifted by *levels*.

    A token-range item starts a fresh line at *indent*.
    """
    ext = moved.extent
    block = shift_block(
        content,
        structure.tokens,
        ext.start,
        ext.end,
        levels,
        indent_step(structure, content),
    )
    if not ext.whole_lines:
        # The first line may or may not have been shifted (it is only
        # when the item starts its line); either way it gets *indent*.
        block = indent + block.lstrip(" \t")
    if not block.endswith("\n"):
        block += _eol(content)
    return block


def move_edits(
    structure: FileStructure,
    content: str,
    item: ItemRef,
    target_section_id: Optional[int],
    before: Optional[ItemRef],
) -> "list[TextEdit]":
    """Edits that move *item* into a section (None: the file level).

    The item lands before *before* (a direct child of the target) or,
    if that is None, at the end of the target. Its comments travel with
    it, and its lines are re-indented to the target nesting level. The
    result is one cut and one paste, or ``[]`` if nothing would move.
    """
    moved = _resolve(structure, content, item)
    target = None
    level = 0
    if target_section_id is not None:
        target = _closed(structure, target_section_id)
        level = target.depth
    if moved.key[0] == "section":
        _reject_descendant(structure, moved.key[1], target_section_id)
    anchor = None
    if before is not None:
        anchor = _resolve(structure, content, before)
        if anchor.container != target_section_id:
            raise PreviewEditError(
                "The drop position is not inside the target section."
            )
    if _same_position(structure, moved, target_section_id, anchor):
        return []

    indent = _target_indent(structure, content, target)
    if anchor is not None and _first_on_line(content, anchor.extent.start):
        indent = _indent(content, anchor.extent.start)
    block = _block(structure, content, moved, level - moved.level, indent)
    return _checked(
        content,
        [
            _cut(content, moved.extent),
            _paste(structure, content, target, anchor, block),
        ],
    )
