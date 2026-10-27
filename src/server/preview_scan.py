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
"""Token-stream scanner locating sections, records and field assignments.

Structure is recognised through the ``ast_link`` the TRLC parser sets on
tokens: the ``section`` keyword and its closing ``}`` link to the
``Section``; a record's name, ``{`` and ``}`` link to the
``Record_Object``; a component name and its ``=`` link to the
``Composite_Component``.
"""

from dataclasses import dataclass
from typing import Optional

from trlc.ast import Composite_Component, Record_Object, Section


@dataclass(frozen=True)
class FieldSpan:
    """Token range of one explicit field assignment."""

    name: str
    name_tok: int
    value_first: int
    value_last: int


@dataclass(frozen=True)
class RecordSpan:
    """Token range of one record object declaration."""

    obj: Record_Object
    name_tok: int
    close_tok: int
    fields: "dict[str, FieldSpan]"
    section_id: Optional[int]


@dataclass(frozen=True)
class SectionSpan:
    """Token range of one section.

    ``close_tok`` is None for a section still open at the end of the
    file (TRLC's error recovery keeps such files around).
    """

    section_id: int
    title: str
    depth: int
    parent_id: Optional[int]
    keyword_tok: int
    close_tok: Optional[int]


@dataclass(frozen=True)
class FileStructure:
    """Sections and records found in a token list."""

    tokens: list
    sections: "list[SectionSpan]"
    records: "list[RecordSpan]"

    def record(self, name: str) -> Optional[RecordSpan]:
        """Return the record called *name*, or None."""
        for rec in self.records:
            if rec.obj.name == name:
                return rec
        return None

    def section(self, section_id: int) -> Optional[SectionSpan]:
        """Return the section with *section_id*, or None."""
        for sec in self.sections:
            if sec.section_id == section_id:
                return sec
        return None


def _field_end(tokens: list, first: int, stop: int) -> int:
    """Index of the last non-COMMENT token in ``[first, stop)``."""
    last = first
    for idx in range(first, stop):
        if tokens[idx].kind != "COMMENT":
            last = idx
    return last


def _next_non_comment(tokens: list, idx: int, stop: int) -> Optional[int]:
    """Index of the first non-COMMENT token in ``[idx, stop)``, or None."""
    for pos in range(idx, stop):
        if tokens[pos].kind != "COMMENT":
            return pos
    return None


def _match_brace(tokens: list, open_idx: int) -> Optional[int]:
    """Index of the ``}`` matching the ``{`` at *open_idx*, or None."""
    depth = 0
    for idx in range(open_idx, len(tokens)):
        kind = tokens[idx].kind
        if kind == "C_BRA":
            depth += 1
        elif kind == "C_KET":
            depth -= 1
            if depth == 0:
                return idx
    return None


def _scan_record(tokens: list, start: int, section_id: Optional[int]):
    """Scan the record whose name token is at *start*.

    Returns the RecordSpan and the index of its closing ``}``, or None
    if the record is not terminated.
    """
    obj = tokens[start].ast_link
    close = None
    for idx in range(start + 2, len(tokens)):
        tok = tokens[idx]
        if tok.kind == "C_KET" and tok.ast_link is obj:
            close = idx
            break
    if close is None:
        # TRLC skips linking the closing brace when it reports an error
        # such as a missing required component; match braces instead.
        close = _match_brace(tokens, start + 1)
    if close is None:
        return None

    # (name token, assign token) per explicit assignment; comments may
    # sit between the component name and its '='.
    starts = []
    for idx in range(start + 2, close - 1):
        tok = tokens[idx]
        if tok.kind != "IDENTIFIER" or not isinstance(
            tok.ast_link, Composite_Component
        ):
            continue
        assign = _next_non_comment(tokens, idx + 1, close)
        if (
            assign is not None
            and tokens[assign].kind == "ASSIGN"
            and tokens[assign].ast_link is tok.ast_link
        ):
            starts.append((idx, assign))

    fields = {}
    for pos, (name_idx, assign) in enumerate(starts):
        stop = starts[pos + 1][0] if pos + 1 < len(starts) else close
        first = assign + 1
        name = tokens[name_idx].value
        fields[name] = FieldSpan(
            name, name_idx, first, _field_end(tokens, first, stop)
        )
    return RecordSpan(obj, start, close, fields, section_id), close


def scan_file(tokens: list) -> FileStructure:
    """Locate sections, records and explicit assignments in *tokens*."""
    sections: "list[SectionSpan]" = []
    records: "list[RecordSpan]" = []
    # open sections: (section_id, keyword_tok, title, depth, parent_id)
    stack: list = []
    next_id = 0
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok.kind == "KEYWORD" and tok.value == "section":
            link = tok.ast_link
            title = link.name if isinstance(link, Section) else ""
            parent = stack[-1][0] if stack else None
            stack.append((next_id, idx, title, len(stack) + 1, parent))
            next_id += 1
        elif (
            tok.kind == "C_KET" and isinstance(tok.ast_link, Section) and stack
        ):
            sec_id, kw, title, depth, parent = stack.pop()
            sections.append(SectionSpan(sec_id, title, depth, parent, kw, idx))
        elif (
            tok.kind == "IDENTIFIER"
            and isinstance(tok.ast_link, Record_Object)
            and idx + 1 < len(tokens)
            and tokens[idx + 1].kind == "C_BRA"
            and tokens[idx + 1].ast_link is tok.ast_link
        ):
            scanned = _scan_record(
                tokens, idx, stack[-1][0] if stack else None
            )
            if scanned:
                records.append(scanned[0])
                idx = scanned[1]
        idx += 1
    # Sections still open at EOF (a syntax error): keep them, unclosed, so
    # their records stay attached to a known section.
    for sec_id, kw, title, depth, parent in stack:
        sections.append(SectionSpan(sec_id, title, depth, parent, kw, None))
    sections.sort(key=lambda s: s.section_id)
    return FileStructure(tokens, sections, records)
