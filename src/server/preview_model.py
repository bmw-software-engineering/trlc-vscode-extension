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
"""Builds the JSON-ready preview model of a parsed TRLC file."""

from typing import Optional

from trlc.ast import (
    Enumeration_Literal,
    Implicit_Null,
    Literal,
    String_Literal,
)

from server.preview_scan import FieldSpan, FileStructure, RecordSpan, scan_file
from server.preview_values import (
    element_type,
    field_kind,
    type_ref,
    visible_record_types,
)


def unavailable_model(uri: str, reason: str) -> dict:
    """Model for a file that cannot be previewed."""
    return {
        "uri": uri,
        "package": None,
        "editable": False,
        "reason": reason,
        "recordTypes": [],
        "nodes": None,
        "version": None,
    }


def _source_text(parsed, structure: FileStructure, span) -> str:
    tokens = structure.tokens
    start = tokens[span.value_first].location.start_pos
    end = tokens[span.value_last].location.end_pos
    return parsed.lexer.content[start : end + 1]


def _field_value(parsed, structure, rec: RecordSpan, comp, expr):
    if expr is None or isinstance(expr, Implicit_Null):
        return None
    if isinstance(expr, String_Literal):
        return expr.value
    if isinstance(expr, Enumeration_Literal):
        return expr.value.name
    span = rec.fields.get(comp.name)
    if span is None:
        # e.g. a frozen component: the value comes from the type.
        return expr.to_string() if isinstance(expr, Literal) else None
    return _source_text(parsed, structure, span)


def _element_text(content: str, toks: list, elem_kind: str) -> str:
    if elem_kind == "string" and len(toks) == 1:
        return toks[0].value
    if elem_kind == "enum":
        return toks[-1].value  # [Pkg.]Type.Literal
    start = toks[0].location.start_pos
    return content[start : toks[-1].location.end_pos + 1]


def _array_items(
    parsed, structure, span: FieldSpan, elem_kind: str
) -> Optional[list]:
    """Display texts of the elements of an array literal (None if odd)."""
    tokens = structure.tokens
    first, last = span.value_first, span.value_last
    while first < last and tokens[first].kind == "COMMENT":
        first += 1
    while last > first and tokens[last].kind == "COMMENT":
        last -= 1
    if tokens[first].kind != "S_BRA" or tokens[last].kind != "S_KET":
        return None
    elements = [[]]
    for tok in tokens[first + 1 : last]:
        if tok.kind == "COMMA":
            elements.append([])
        elif tok.kind != "COMMENT":
            elements[-1].append(tok)
    content = parsed.lexer.content
    return [
        _element_text(content, toks, elem_kind) for toks in elements if toks
    ]


def _kind_extras(kind: str, n_typ) -> dict:
    """``enumValues`` and ``elementKind`` of a field of type *n_typ*."""
    elem_kind = None
    if kind == "array":
        n_typ = element_type(n_typ)
        elem_kind = field_kind(n_typ)
    enum_values = None
    if (elem_kind or kind) == "enum":
        enum_values = [lit.name for lit in n_typ.literals.table.values()]
    return {"enumValues": enum_values, "elementKind": elem_kind}


def _fields(parsed, structure, rec: RecordSpan, markdown) -> list:
    obj = rec.obj
    fields = []
    for comp in obj.n_typ.all_components():
        kind = field_kind(comp.n_typ)
        extras = _kind_extras(kind, comp.n_typ)
        value = _field_value(
            parsed, structure, rec, comp, obj.field.get(comp.name)
        )
        span = rec.fields.get(comp.name)
        items = None
        if kind == "array" and value is not None and span is not None:
            items = _array_items(
                parsed, structure, span, extras["elementKind"]
            )
        fields.append(
            {
                "name": comp.name,
                "kind": kind,
                "value": value,
                "items": items,
                "optional": bool(comp.optional),
                "editable": not obj.n_typ.is_frozen(comp),
                "markdown": kind == "string" and comp.name in markdown,
                **extras,
            }
        )
    return fields


def _record_node(parsed, structure, rec: RecordSpan, markdown) -> dict:
    cur_package = parsed.cu.package
    name_tok = structure.tokens[rec.name_tok]
    return {
        "kind": "record",
        "name": rec.obj.name,
        "type": type_ref(rec.obj.n_typ, cur_package),
        "line": name_tok.location.line_no - 1,
        "fields": _fields(parsed, structure, rec, markdown),
    }


def build_preview_model(
    uri: str, parsed, markdown_components: "tuple[str, ...]" = ()
) -> dict:
    """Build the preview model for a parsed file.

    String fields named in *markdown_components* are flagged
    ``"markdown": True`` so the client renders and edits them as Markdown.
    """
    structure = scan_file(parsed.lexer.tokens)
    cu = parsed.cu

    section_nodes = {
        sec.section_id: {
            "kind": "section",
            "sectionId": sec.section_id,
            "title": sec.title,
            "depth": sec.depth,
            # False for a section still open at EOF: no add-record target.
            "closed": sec.close_tok is not None,
            "children": [],
        }
        for sec in structure.sections
    }
    roots: list = []

    def container(section_id: Optional[int]) -> list:
        if section_id is None:
            return roots
        return section_nodes[section_id]["children"]

    # Merge sections and records in source order (by token index).
    items = [(s.keyword_tok, s) for s in structure.sections]
    items += [(r.name_tok, r) for r in structure.records]
    for _, item in sorted(items, key=lambda pair: pair[0]):
        if isinstance(item, RecordSpan):
            container(item.section_id).append(
                _record_node(parsed, structure, item, markdown_components)
            )
        else:
            container(item.parent_id).append(section_nodes[item.section_id])

    return {
        "uri": uri,
        "package": cu.package.name if cu.package else None,
        "editable": True,
        "reason": None,
        "recordTypes": sorted(visible_record_types(cu)),
        "nodes": roots,
        # Set by the handler: the buffer version the parse matches.
        "version": None,
    }
