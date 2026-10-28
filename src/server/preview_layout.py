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
    """Offset just after the EOL of the line containing *offset*.

    Args:
        content: Full file text.
        offset: Character offset anywhere inside the line.

    Returns:
        The character offset one past the newline ending the line, or
        ``len(content)`` for a last line without EOL.
    """
    newline = content.find("\n", offset)
    return len(content) if newline < 0 else newline + 1


def _blank(text: str) -> bool:
    """Tell whether *text* consists of whitespace only.

    Args:
        text: Text to test.

    Returns:
        True if *text* is empty or holds only blanks, tabs, carriage
        returns and newlines.
    """
    return not text.strip(_BLANK + "\n")


def _first_on_line(content: str, offset: int) -> bool:
    """Tell whether only whitespace precedes *offset* on its line.

    Args:
        content: Full file text.
        offset: Character offset to test.

    Returns:
        True if the text from the line start up to *offset* is blank.
    """
    return _blank(content[_line_start(content, offset) : offset])


def _rest_blank(content: str, offset: int) -> bool:
    """True if only whitespace follows *offset* up to the EOL.

    Args:
        content: Full file text.
        offset: Character offset to start at (inclusive).

    Returns:
        True if the rest of the line from *offset* is blank (or empty).
    """
    newline = content.find("\n", offset)
    end = len(content) if newline < 0 else newline
    return _blank(content[offset:end])


def _record_first(tokens: list, rec: RecordSpan) -> int:
    """Index of the record's first token (its possibly qualified type).

    Args:
        tokens: Token list of the file.
        rec: Span of the record; ``name_tok`` is the index of its name.

    Returns:
        The token index of the type name, or of the package name when
        the type is written qualified (``Pkg.Type name``).
    """
    first = rec.name_tok - 1
    if (
        first >= 2
        and tokens[first - 1].kind == "DOT"
        and tokens[first - 2].kind == "IDENTIFIER"
    ):
        first -= 2
    return first


def _extent(content: str, tokens: list, first: int, last: int) -> Extent:
    """Extent of the item spanning tokens ``[first, last]``.

    Trailing comments on the line of the last token and comment lines
    directly above the first token (no blank line between) are included.

    Args:
        content: Full file text.
        tokens: Token list of the file.
        first: Index of the item's first token (inclusive).
        last: Index of the item's last token (inclusive).

    Returns:
        The extent; whole-line (including the final EOL) only if the
        item starts its line and nothing but blanks follows it.
    """
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
    # A '//' comment token keeps the '\r' of a CRLF line end.
    while end > start and content[end - 1] == "\r":
        end -= 1
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
    """Text of *rec* with the comments that belong to it.

    Args:
        structure: Scanned structure of the file (provides the tokens).
        content: Full file text.
        rec: Span of the record.

    Returns:
        The extent of the record, from its type name to its closing
        brace, widened by its comments.
    """
    tokens = structure.tokens
    return _extent(content, tokens, _record_first(tokens, rec), rec.close_tok)


def section_extent(
    structure: FileStructure, content: str, sec: SectionSpan
) -> Extent:
    """Text of *sec* (with all content) and the comments of it.

    Args:
        structure: Scanned structure of the file (provides the tokens).
        content: Full file text.
        sec: Span of the section.

    Returns:
        The extent from the ``section`` keyword to the closing brace,
        widened by its comments.

    Raises:
        ValueError: If the section is not closed.
    """
    if sec.close_tok is None:
        raise ValueError(f"Section '{sec.title}' is not closed.")
    return _extent(content, structure.tokens, sec.keyword_tok, sec.close_tok)


def _step_between(content: str, outer, inner) -> Optional[str]:
    """Indent of token *inner* minus that of *outer*, if determinable.

    Args:
        content: Full file text.
        outer: Token that opens the outer construct.
        inner: Token inside it, on a later line.

    Returns:
        The extra leading whitespace of *inner*, or None if either
        token is not first on its line, both share a line, or the
        indent of *inner* does not extend that of *outer*.
    """
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


def _first_section_step(
    structure: FileStructure, content: str
) -> Optional[str]:
    """Indentation step used inside the file's first section.

    Args:
        structure: Scanned structure of the file.
        content: Full file text.

    Returns:
        The step between the first section's keyword and its first
        non-comment token after the opening brace, or None if there is
        no section or the step cannot be determined.
    """
    if not structure.sections:
        return None
    tokens = structure.tokens
    sec = structure.sections[0]
    stop = len(tokens) if sec.close_tok is None else sec.close_tok
    # The first non-comment token after the section's '{'.
    opened = False
    for idx in range(sec.keyword_tok + 1, stop):
        kind = tokens[idx].kind
        if not opened:
            opened = kind == "C_BRA"
        elif kind != "COMMENT":
            return _step_between(content, tokens[sec.keyword_tok], tokens[idx])
    return None


def _first_record_step(
    structure: FileStructure, content: str
) -> Optional[str]:
    """Indentation step used for the fields of the first record.

    Args:
        structure: Scanned structure of the file.
        content: Full file text.

    Returns:
        The step between the first record that has fields and its
        earliest field, or None if there is none or it is not
        determinable.
    """
    tokens = structure.tokens
    for rec in structure.records:
        if rec.fields:
            field = min(span.name_tok for span in rec.fields.values())
            return _step_between(
                content, tokens[_record_first(tokens, rec)], tokens[field]
            )
    return None


def indent_step(structure: FileStructure, content: str) -> str:
    """The file's indentation step (default four spaces).

    Args:
        structure: Scanned structure of the file.
        content: Full file text.

    Returns:
        The whitespace of one indentation level, taken from the first
        section or else the first record with fields; the default step
        if neither yields one.
    """
    return (
        _first_section_step(structure, content)
        or _first_record_step(structure, content)
        or _STEP
    )


def _dedent_cut(content: str, pos: int, levels: int, step: str):
    """(offset to cut to, whether all *levels* were removed).

    Per level, one leading tab or up to a step's width of spaces is
    removed, never more than the available leading whitespace.

    Args:
        content: Full file text.
        pos: Offset of the line start to dedent.
        levels: Number of levels to remove.
        step: Whitespace of one indentation level.

    Returns:
        A tuple ``(cut, complete)``: the offset up to which the line's
        leading whitespace is removed, and whether all *levels* could
        be removed.
    """
    width = len(step.expandtabs(4))
    cut = pos
    for _ in range(levels):
        if cut < len(content) and content[cut] == "\t":
            cut += 1
            continue
        removed = 0
        while removed < width and cut < len(content) and content[cut] == " ":
            cut += 1
            removed += 1
        if removed < width:
            return cut, False
    return cut, True


def _line_starts(content: str, start: int, end: int) -> "list[int]":
    """Line starts inside ``[start, end)``; *start* only at a line start.

    Args:
        content: Full file text.
        start: Offset where the block begins (inclusive).
        end: Offset where the block ends (exclusive).

    Returns:
        Ascending offsets of the lines that begin inside the block; a
        trailing EOL at ``end - 1`` does not start another line.
    """
    starts = []
    if start == 0 or content[start - 1] == "\n":
        starts.append(start)
    pos = content.find("\n", start, end)
    while 0 <= pos < end - 1:
        starts.append(pos + 1)
        pos = content.find("\n", pos + 1, end)
    return starts


def _string_lines(content: str, tokens: list, start: int, end: int) -> dict:
    """Map continuation line start -> STRING token, inside the block.

    Args:
        content: Full file text.
        tokens: Token list of the file.
        start: Offset where the block begins (inclusive).
        end: Offset where the block ends (exclusive).

    Returns:
        A dict from the offset of every line that continues a
        multi-line STRING token (all lines after its first) to that
        token, for tokens overlapping the block.
    """
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
    """(cut offset, prefix, complete) for the line starting at *pos*.

    *complete* is False when a dedent ran out of leading whitespace.

    Args:
        content: Full file text.
        pos: Offset of the line start.
        levels: Levels to shift; positive indents, negative dedents.
        step: Whitespace of one indentation level.

    Returns:
        A tuple ``(cut, prefix, complete)``: the offset up to which
        the old leading whitespace is dropped, the whitespace to put
        in front instead, and whether the shift was fully possible.
        Blank lines get no prefix when indenting.
    """
    if levels > 0:
        if _rest_blank(content, pos):
            return pos, "", True
        return pos, step * levels, True
    cut, complete = _dedent_cut(content, pos, -levels, step)
    return cut, "", complete


def _has_text(content: str, line: int, tok_end: int) -> bool:
    """True if the string line at *line* holds more than whitespace.

    *tok_end* is the token's last offset; its closing quotes on the
    last line do not count as text.

    Args:
        content: Full file text.
        line: Offset of the start of the string line.
        tok_end: Offset of the last character of the STRING token.

    Returns:
        True if the line contains non-whitespace text.
    """
    newline = content.find("\n", line, tok_end + 1)
    text_end = tok_end - 2 if newline < 0 else newline
    return not _blank(content[line:text_end])


def _string_frozen(content: str, tok, levels: int, step: str) -> bool:
    """True if the lines of STRING *tok* must not be shifted.

    Only triple-quoted strings are affected. They are frozen when a
    dedent would cut into text, or when the shifted literal would
    evaluate to a different value.

    Args:
        content: Full file text.
        tok: The STRING token.
        levels: Levels the block is shifted by (negative dedents).
        step: Whitespace of one indentation level.

    Returns:
        True if the string's continuation lines must stay unchanged.
    """
    loc = tok.location
    raw = content[loc.start_pos : loc.end_pos + 1]
    if not raw.startswith(("'''", '"""')):
        return False
    edits = []
    pos = content.find("\n", loc.start_pos, loc.end_pos)
    while pos >= 0:
        line = pos + 1
        cut, prefix, complete = _shift_line(content, line, levels, step)
        if not complete and _has_text(content, line, loc.end_pos):
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

    A negative *levels* dedents, removing per level one leading tab or
    up to a step's width of spaces, at most the available leading
    whitespace. Blank lines get no indentation. When *start* is not at
    a line start, the partial first line is returned unshifted.
    Continuation lines of a multi-line STRING are shifted like the
    others; if that cannot be done uniformly (dedent with too little
    whitespace), all lines of that string are left as they are.

    Args:
        content: Full file text.
        tokens: Token list of the file.
        start: Offset where the block begins (inclusive).
        end: Offset where the block ends (exclusive).
        levels: Levels to shift; positive indents, negative dedents,
            zero returns the text unchanged.
        step: Whitespace of one indentation level.

    Returns:
        The shifted text of ``content[start:end]``.
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
        cut, prefix, _ = _shift_line(content, line, levels, step)
        out.append(content[prev:line] + prefix)
        prev = min(cut, end)
    out.append(content[prev:end])
    return "".join(out)
