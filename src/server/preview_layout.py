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
"""Layout helpers for structural preview edits.

Extents say which text belongs to a record or section (including the
comments that travel with it); ``shift_block`` re-indents such text
without changing the value of multi-line string literals.
"""

# The public signatures are fixed by the structural edit modules.
# pylint: disable=too-many-arguments,too-many-positional-arguments

from dataclasses import dataclass
from typing import Optional

from trlc.lexer import triple_quoted_string_value

from server.preview_edits import _STEP, _indent, _line_start
from server.preview_scan import FileStructure, RecordSpan, SectionSpan

_BLANK = " \t\r"


@dataclass(frozen=True)
class Extent:
    """Text range of an item in the file content.

    ``start`` is inclusive, ``end`` exclusive. With ``whole_lines`` the
    range consists of complete lines, including the final EOL (unless
    the item ends the file without one).
    """

    start: int
    end: int
    whole_lines: bool


def _line_end(content: str, offset: int) -> int:
    """Offset just after the EOL of the line containing *offset*."""
    newline = content.find("\n", offset)
    return len(content) if newline < 0 else newline + 1


def _blank(text: str) -> bool:
    return not text.strip(_BLANK + "\n")


def _first_on_line(content: str, offset: int) -> bool:
    return _blank(content[_line_start(content, offset) : offset])


def _rest_blank(content: str, offset: int) -> bool:
    """True if only whitespace follows *offset* up to the EOL."""
    newline = content.find("\n", offset)
    end = len(content) if newline < 0 else newline
    return _blank(content[offset:end])


def _record_first(tokens: list, rec: RecordSpan) -> int:
    """Index of the record's first token (its possibly qualified type)."""
    first = rec.name_tok - 1
    if (
        first >= 2
        and tokens[first - 1].kind == "DOT"
        and tokens[first - 2].kind == "IDENTIFIER"
    ):
        first -= 2
    return first


def _extent(content: str, tokens: list, first: int, last: int) -> Extent:
    """Extent of the item spanning tokens ``[first, last]``."""
    # Trailing comments starting on the line of the closing brace.
    while (
        last + 1 < len(tokens)
        and tokens[last + 1].kind == "COMMENT"
        and "\n"
        not in content[
            tokens[last].location.end_pos + 1 : tokens[
                last + 1
            ].location.start_pos
        ]
    ):
        last += 1
    start = tokens[first].location.start_pos
    end = tokens[last].location.end_pos + 1
    if not (_first_on_line(content, start) and _rest_blank(content, end)):
        return Extent(start, end, False)

    # Comment lines directly above, without a blank line in between.
    top = _line_start(content, start)
    idx = first - 1
    while idx >= 0 and tokens[idx].kind == "COMMENT":
        com = tokens[idx].location
        gap = content[com.end_pos + 1 : top]
        if gap.count("\n") != 1 or not _blank(gap):
            break
        if not _first_on_line(content, com.start_pos):
            break
        top = _line_start(content, com.start_pos)
        idx -= 1
    return Extent(top, _line_end(content, end), True)


def record_extent(
    structure: FileStructure, content: str, rec: RecordSpan
) -> Extent:
    """Text of *rec* with the comments that belong to it."""
    tokens = structure.tokens
    return _extent(content, tokens, _record_first(tokens, rec), rec.close_tok)


def section_extent(
    structure: FileStructure, content: str, sec: SectionSpan
) -> Extent:
    """Text of *sec* (with all content) and the comments of it.

    Raises ValueError for a section that is not closed.
    """
    if sec.close_tok is None:
        raise ValueError(f"Section '{sec.title}' is not closed.")
    return _extent(content, structure.tokens, sec.keyword_tok, sec.close_tok)


def _step_between(content: str, outer, inner) -> Optional[str]:
    """Indent of token *inner* minus that of *outer*, if determinable."""
    outer_pos = outer.location.start_pos
    inner_pos = inner.location.start_pos
    if not (
        _first_on_line(content, outer_pos)
        and _first_on_line(content, inner_pos)
    ):
        return None
    if _line_start(content, outer_pos) == _line_start(content, inner_pos):
        return None
    outer_ws = _indent(content, outer_pos)
    inner_ws = _indent(content, inner_pos)
    if len(inner_ws) <= len(outer_ws) or not inner_ws.startswith(outer_ws):
        return None
    return inner_ws[len(outer_ws) :]


def _first_section_step(structure: FileStructure, content: str):
    if not structure.sections:
        return None
    tokens = structure.tokens
    sec = structure.sections[0]
    stop = len(tokens) if sec.close_tok is None else sec.close_tok
    # Skip the keyword, the title and the '{'.
    for idx in range(sec.keyword_tok + 3, stop):
        if tokens[idx].kind != "COMMENT":
            return _step_between(content, tokens[sec.keyword_tok], tokens[idx])
    return None


def _first_record_step(structure: FileStructure, content: str):
    tokens = structure.tokens
    for rec in structure.records:
        if rec.fields:
            field = min(span.name_tok for span in rec.fields.values())
            return _step_between(
                content, tokens[_record_first(tokens, rec)], tokens[field]
            )
    return None


def indent_step(structure: FileStructure, content: str) -> str:
    """The file's indentation step (default four spaces)."""
    return (
        _first_section_step(structure, content)
        or _first_record_step(structure, content)
        or _STEP
    )


def _leading_ws(content: str, start: int, limit: int) -> int:
    """Count of space/tab characters at *start*, at most *limit*."""
    count = 0
    while (
        count < limit
        and start + count < len(content)
        and content[start + count] in " \t"
    ):
        count += 1
    return count


def _line_starts(content: str, start: int, end: int) -> "list[int]":
    """Line starts inside ``[start, end)``; *start* only at a line start."""
    starts = []
    if start == 0 or content[start - 1] == "\n":
        starts.append(start)
    pos = content.find("\n", start, end)
    while 0 <= pos < end - 1:
        starts.append(pos + 1)
        pos = content.find("\n", pos + 1, end)
    return starts


def _string_lines(content: str, tokens: list, start: int, end: int) -> dict:
    """Map continuation line start -> STRING token, inside the block."""
    owner = {}
    for tok in tokens:
        if tok.kind != "STRING":
            continue
        loc = tok.location
        if loc.end_pos < start or loc.start_pos >= end:
            continue
        pos = content.find("\n", loc.start_pos, loc.end_pos)
        while pos >= 0:
            owner[pos + 1] = tok
            pos = content.find("\n", pos + 1, loc.end_pos)
    return owner


def _shift_line(content: str, pos: int, levels: int, step: str):
    """(offset to cut to, prefix to add) for the line starting at *pos*."""
    if levels > 0:
        if _rest_blank(content, pos):
            return pos, ""
        return pos, step * levels
    return pos + _leading_ws(content, pos, -levels * len(step)), ""


def _string_frozen(content: str, tok, levels: int, step: str) -> bool:
    """True if the lines of STRING *tok* must not be shifted."""
    loc = tok.location
    raw = content[loc.start_pos : loc.end_pos + 1]
    if not raw.startswith(("'''", '"""')):
        return False
    edits = []
    pos = content.find("\n", loc.start_pos, loc.end_pos)
    while pos >= 0:
        line = pos + 1
        cut, prefix = _shift_line(content, line, levels, step)
        newline = content.find("\n", line, loc.end_pos + 1)
        text_end = loc.end_pos - 2 if newline < 0 else newline
        if (
            levels < 0
            and cut - line < -levels * len(step)
            and not _blank(content[line:text_end])
        ):
            return True
        edits.append((line, cut, prefix))
        pos = content.find("\n", line, loc.end_pos)
    # Safety net: the shifted literal must keep its value.
    out, prev = [], loc.start_pos
    for line, cut, prefix in edits:
        out.append(content[prev:line] + prefix)
        prev = cut
    out.append(content[prev : loc.end_pos + 1])
    return triple_quoted_string_value("".join(out)) != (
        triple_quoted_string_value(raw)
    )


def shift_block(
    content: str, tokens: list, start: int, end: int, levels: int, step: str
) -> str:
    """``content[start:end]`` with every line moved by *levels* steps.

    A negative *levels* dedents, removing at most the available leading
    whitespace. Blank lines get no indentation. When *start* is not at
    a line start, the partial first line is returned unshifted.
    Continuation lines of a multi-line STRING are shifted like the
    others; if that cannot be done uniformly (dedent with too little
    whitespace), all lines of that string are left as they are.
    """
    if levels == 0:
        return content[start:end]
    owner = _string_lines(content, tokens, start, end)
    frozen = {
        id(tok)
        for tok in set(owner.values())
        if _string_frozen(content, tok, levels, step)
    }
    out = []
    prev = start
    for line in _line_starts(content, start, end):
        tok = owner.get(line)
        if tok is not None and id(tok) in frozen:
            continue
        cut, prefix = _shift_line(content, line, levels, step)
        out.append(content[prev:line] + prefix)
        prev = min(cut, end)
    out.append(content[prev:end])
    return "".join(out)
