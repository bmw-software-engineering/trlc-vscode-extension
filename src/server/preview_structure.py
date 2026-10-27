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
"""Structural preview edits on sections: insert, rename, dissolve.

Every edit is applied in memory and checked by
``verify_structure_edit`` before it is returned, so an edit that would
change a record's values or break the brace structure is refused.
"""

import io
from collections import Counter
from typing import Optional

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
from server.preview_layout import indent_step, section_extent, shift_block
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
