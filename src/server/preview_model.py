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
    Record_Object,
    String_Literal,
    Union_Type,
)

from server.preview_scan import FieldSpan, FileStructure, RecordSpan, scan_file
from server.preview_values import (
    element_type,
    field_kind,
    type_ref,
    visible_record_types,
)
from server.token_utils import record_type_satisfies


def unavailable_model(uri: str, reason: str) -> dict:
    """Model for a file that cannot be previewed.

    Args:
        uri: URI of the file.
        reason: User-facing explanation why there is no preview.

    Returns:
        A JSON-ready model with ``editable`` False, the *reason*, and
        empty ``recordTypes``, ``references`` and ``openFiles``; its
        ``nodes``, ``package`` and ``version`` are None.
    """
    return {
        "uri": uri,
        "package": None,
        "editable": False,
        "reason": reason,
        "recordTypes": [],
        "nodes": None,
        "version": None,
        "references": {},
        "openFiles": [],
    }


class _References:  # pylint: disable=too-few-public-methods  # a cache
    """Record names a reference field accepts, keyed by expected type.

    Fields expecting the same type share one candidate list, so the
    model carries each list once.
    """

    def __init__(self, cu):
        """Create an empty candidate cache.

        Args:
            cu: Compilation unit of the previewed file; its package and
                imports are searched for candidates.
        """
        self._cu = cu
        #: Expected type key (e.g. ``A``, ``P.A``, ``B|C``) -> names.
        self.candidates: "dict[str, list[str]]" = {}

    def key_for(self, n_typ) -> str:
        """Key of *n_typ* (a record or union type); collects candidates.

        Args:
            n_typ: Expected record type or union of record types.

        Returns:
            The type key (``A``, ``P.A`` or ``B|C``, qualified outside the
            current package). The sorted names of all record objects
            satisfying the type are stored in ``candidates`` under it.
        """
        pkg = self._cu.package
        if isinstance(n_typ, Union_Type):
            key = "|".join(type_ref(t, pkg) for t in n_typ.types)
        else:
            key = type_ref(n_typ, pkg)
        if key not in self.candidates:
            self.candidates[key] = sorted(
                type_ref(obj, pkg)
                for package in [pkg, *self._cu.imports]
                for obj in package.symbols.values(Record_Object)
                if record_type_satisfies(obj.n_typ, n_typ)
            )
        return key


def _source_text(parsed, structure: FileStructure, span) -> str:
    """Source text of a field's value, exactly as written.

    Args:
        parsed: Parse result providing the lexer and its content.
        structure: Scanned structure of the file.
        span: Field span; ``value_first`` and ``value_last`` are the
            inclusive token indices of the value.

    Returns:
        The file text from the start of the first to the end of the
        last value token.
    """
    tokens = structure.tokens
    start = tokens[span.value_first].location.start_pos
    end = tokens[span.value_last].location.end_pos
    return parsed.lexer.content[start : end + 1]


def _field_value(parsed, structure, rec: RecordSpan, comp, expr):
    """Display value of a record field.

    Args:
        parsed: Parse result providing the lexer and its content.
        structure: Scanned structure of the file.
        rec: Span of the record holding the field.
        comp: Component (field declaration) of the record type.
        expr: The field's value expression, or None if unset.

    Returns:
        None for an unset or null value, the decoded value for string
        and enum literals, the type-provided value for a field without
        source span (e.g. frozen), and otherwise the source text.
    """
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
    """Display text of one array element.

    Args:
        content: Full file text.
        toks: Non-comment tokens of the element, at least one.
        elem_kind: Value kind of the array elements (``"string"``,
            ``"enum"``, ...).

    Returns:
        The decoded value for a single string token, the
        ``Type.Literal`` tail of an enum value, otherwise the source
        text spanned by the tokens.
    """
    if elem_kind == "string" and len(toks) == 1:
        return toks[0].value
    if elem_kind == "enum":
        return toks[-1].value  # [Pkg.]Type.Literal
    start = toks[0].location.start_pos
    return content[start : toks[-1].location.end_pos + 1]


def _array_items(
    parsed, structure, span: FieldSpan, elem_kind: str
) -> Optional[list]:
    """Display texts of the elements of an array literal (None if odd).

    Args:
        parsed: Parse result providing the lexer and its content.
        structure: Scanned structure of the file.
        span: Field span; ``value_first`` and ``value_last`` are the
            inclusive token indices of the value.
        elem_kind: Value kind of the array elements.

    Returns:
        One text per element, in order, without comments and empty
        elements; None if the value is not a ``[...]`` literal.
    """
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


def _kind_extras(kind: str, n_typ, refs: _References) -> dict:
    """``enumValues``, ``elementKind`` and ``referenceType`` of a field.

    Args:
        kind: Value kind of the field (see ``field_kind``).
        n_typ: TRLC type of the field.
        refs: Reference candidate cache; filled for reference fields.

    Returns:
        A dict with the three keys. ``enumValues`` lists the literal
        names for enum fields (or enum elements), ``elementKind`` is
        the element kind of arrays, ``referenceType`` the key into the
        model's ``references`` for reference fields. Each is None where
        not applicable.
    """
    elem_kind = None
    if kind == "array":
        n_typ = element_type(n_typ)
        elem_kind = field_kind(n_typ)
    enum_values = None
    reference_type = None
    if (elem_kind or kind) == "enum":
        enum_values = [lit.name for lit in n_typ.literals.table.values()]
    elif (elem_kind or kind) == "reference":
        reference_type = refs.key_for(n_typ)
    return {
        "enumValues": enum_values,
        "elementKind": elem_kind,
        "referenceType": reference_type,
    }


def _fields(parsed, structure, rec: RecordSpan, markdown, refs) -> list:
    """Field entries of a record node.

    Args:
        parsed: Parse result of the file.
        structure: Scanned structure of the file.
        rec: Span of the record.
        markdown: Names of string components rendered as Markdown.
        refs: Reference candidate cache.

    Returns:
        One dict per component of the record type, in declaration
        order, with ``name``, ``kind``, ``value``, ``items``,
        ``optional``, ``editable``, ``markdown`` and the keys of
        ``_kind_extras``. ``items`` lists array elements, else None.
    """
    obj = rec.obj
    fields = []
    for comp in obj.n_typ.all_components():
        kind = field_kind(comp.n_typ)
        extras = _kind_extras(kind, comp.n_typ, refs)
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


def _record_node(
    parsed, structure, rec: RecordSpan, markdown, refs: _References
) -> dict:
    """Preview node of one record object.

    Args:
        parsed: Parse result of the file.
        structure: Scanned structure of the file.
        rec: Span of the record.
        markdown: Names of string components rendered as Markdown.
        refs: Reference candidate cache.

    Returns:
        A dict with ``kind`` ``"record"``, ``name``, ``type`` (qualified
        outside the current package), ``line`` (0-based line of the
        record name) and ``fields``.
    """
    cur_package = parsed.cu.package
    name_tok = structure.tokens[rec.name_tok]
    return {
        "kind": "record",
        "name": rec.obj.name,
        "type": type_ref(rec.obj.n_typ, cur_package),
        "line": name_tok.location.line_no - 1,
        "fields": _fields(parsed, structure, rec, markdown, refs),
    }


def build_preview_model(
    uri: str, parsed, markdown_components: "tuple[str, ...]" = ()
) -> dict:
    """Build the preview model for a parsed file.

    String fields named in *markdown_components* are flagged
    ``"markdown": True`` so the client renders and edits them as Markdown.
    Reference fields name the records they accept via ``referenceType``
    and the model's ``references``.

    Args:
        uri: URI of the file.
        parsed: Parse result providing the lexer tokens, the lexer
            content and the compilation unit.
        markdown_components: Names of string components to flag as
            Markdown.

    Returns:
        The JSON-ready model: ``uri``, ``package`` (None if none),
        ``editable`` True, ``reason`` None, ``recordTypes`` (sorted
        names of the record types that can be added), ``nodes`` (the
        top-level sections and records in source order; sections
        carry ``children``), ``version`` None (set by the handler),
        ``references`` (candidates per ``referenceType`` key) and
        ``openFiles`` (empty, filled by the handler).
    """
    structure = scan_file(parsed.lexer.tokens)
    cu = parsed.cu
    refs = _References(cu)

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
        """Child list that receives a node of a section.

        Args:
            section_id: Id of the enclosing section, or None for the
                top level.

        Returns:
            The top-level node list for None, else the ``children`` list
            of that section's node.
        """
        if section_id is None:
            return roots
        return section_nodes[section_id]["children"]

    # Merge sections and records in source order (by token index).
    items = [(s.keyword_tok, s) for s in structure.sections]
    items += [(r.name_tok, r) for r in structure.records]
    for _, item in sorted(items, key=lambda pair: pair[0]):
        if isinstance(item, RecordSpan):
            container(item.section_id).append(
                _record_node(
                    parsed, structure, item, markdown_components, refs
                )
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
        # Candidates of the fields' "referenceType" keys.
        "references": refs.candidates,
        # Closed files declaring symbols this file misses (see handler).
        "openFiles": [],
    }
