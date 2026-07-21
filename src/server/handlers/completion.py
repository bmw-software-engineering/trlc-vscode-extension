# *******************************************************************************
# TRLC VSCode Extension
# Copyright (C) 2025 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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
# *******************************************************************************
"""Completion handler."""

import logging
from typing import List, Optional

import trlc.ast
import trlc.errors
from lsprotocol.types import (
    TEXT_DOCUMENT_COMPLETION,
    CompletionItem,
    CompletionList,
    CompletionOptions,
    CompletionParams,
)

from ..server_protocol import ServerProtocol
from ..token_utils import (
    get_token,
    lookup_by_name,
    path_from_uri,
    resolve_scope_context,
)

LOGGER = logging.getLogger(__name__)


def _token_index(tokens, target) -> Optional[int]:
    """Return the index of *target* within *tokens* (identity match)."""
    for i, tok in enumerate(tokens):
        if tok is target:
            return i
    return None


# ---------------------------------------------------------------------------
# Enclosing-field resolution: "which Composite_Component is being filled at
# the cursor" — needed to filter Package. completion candidates by the type
# the field actually accepts. Works purely off the raw token stream + the
# symbol table, so it applies identically whether the current file parsed
# cleanly or we're on the lex-only fallback path (see resolve_document_tokens
# in token_utils.py) — the enclosing record object's type and the target
# field are almost always resolvable even while the file has an unrelated or
# still in-progress syntax error elsewhere.
# ---------------------------------------------------------------------------


def _find_enclosing_brace_index(tokens, from_index) -> Optional[int]:
    """Return the index of the nearest unclosed ``{`` at/before *from_index*.

    Record object bodies are the only ``{...}`` construct in a ``.trlc``
    file and are never nested, so a simple depth counter over ``{``/``}``
    (skipping any fully-closed prior record object) is enough.
    """
    depth = 0
    for i in range(from_index, -1, -1):
        kind = tokens[i].kind
        if kind == "C_KET":
            depth += 1
        elif kind == "C_BRA":
            if depth == 0:
                return i
            depth -= 1
    return None


def _resolve_record_type_before_brace(tokens, brace_i, symbols, cur_pkg):
    """Resolve the ``[Package.]TypeName`` that immediately precedes a
    record object's opening ``{`` (the two tokens before it are always
    ``TypeName ObjectName``)."""
    if brace_i < 2:
        return None
    type_tok = tokens[brace_i - 2]
    if type_tok.kind != "IDENTIFIER":
        return None
    if (
        brace_i >= 4
        and tokens[brace_i - 3].kind == "DOT"
        and tokens[brace_i - 4].kind == "IDENTIFIER"
    ):
        pkg = lookup_by_name(
            symbols, tokens[brace_i - 4].value, trlc.ast.Package
        )
        return lookup_by_name(
            pkg.symbols if pkg else None,
            type_tok.value,
            trlc.ast.Record_Type,
        )
    return lookup_by_name(
        cur_pkg.symbols if cur_pkg else None,
        type_tok.value,
        trlc.ast.Record_Type,
    )


def _field_name_before(tokens, target_index, brace_i) -> Optional[str]:
    """Return the field name of the assignment currently being filled.

    Scans backward from *target_index* to the nearest ``=`` — this still
    finds the right field when the cursor is deep inside that field's array
    literal (``derived_from = [Foo@1, Bar.<cursor>]``), since no new ``=``
    appears until the *next* field.
    """
    for i in range(target_index, brace_i, -1):
        if tokens[i].kind == "ASSIGN" and tokens[i - 1].kind == "IDENTIFIER":
            return tokens[i - 1].value
    return None


def _component_named(record_type, name):
    for comp in record_type.all_components():
        if comp.name == name:
            return comp
    return None


def _resolve_expected_type(tokens, target_index, symbols, cur_pkg):
    """Return the declared type a completion at *target_index* should be
    filtered against (array fields unwrapped to their element type), or
    ``None`` when it can't be determined — callers must treat ``None`` as
    "show everything," not "show nothing."
    """
    if target_index is None:
        return None
    brace_i = _find_enclosing_brace_index(tokens, target_index)
    if brace_i is None:
        return None
    record_type = _resolve_record_type_before_brace(
        tokens, brace_i, symbols, cur_pkg
    )
    if record_type is None:
        return None
    field_name = _field_name_before(tokens, target_index, brace_i)
    if field_name is None:
        return None
    component = _component_named(record_type, field_name)
    if component is None:
        return None
    typ = component.n_typ
    if isinstance(typ, trlc.ast.Array_Type):
        typ = typ.element_type
    if isinstance(typ, trlc.ast.Tuple_Type):
        # The "versioned reference" idiom (e.g. score_requirements_model's
        # ``tuple FeatReqId { item FeatReq separator @ version Integer }``)
        # wraps the actual Record_Type (or Union_Type, for a component that
        # accepts several record types — ``item [FeatReq, AssumedSystemReq]``)
        # one level deeper — find that component rather than the tuple itself.
        typ = next(
            (
                comp.n_typ
                for comp in typ.components.table.values()
                if isinstance(
                    comp.n_typ, (trlc.ast.Record_Type, trlc.ast.Union_Type)
                )
            ),
            None,
        )
    return typ


def _matches_expected_type(candidate_type, expected_type) -> bool:
    """True if a Record_Object's *candidate_type* satisfies *expected_type*.

    *expected_type* may be a single ``Record_Type`` (candidate must be it or
    an ``extends`` subtype) or a ``Union_Type`` (candidate must satisfy any
    one of its member types — a field like ``item [FeatReq,
    AssumedSystemReq]`` legitimately accepts either).
    """
    if isinstance(expected_type, trlc.ast.Record_Type):
        return candidate_type.is_subclass_of(expected_type)
    if isinstance(expected_type, trlc.ast.Union_Type):
        return any(
            candidate_type.is_subclass_of(member)
            for member in expected_type.types
        )
    return True


def _package_symbol_labels(pkg, expected_type) -> List[str]:
    """Labels for every symbol in *pkg*, minus Record_Object instances that
    don't satisfy *expected_type* (when one was resolved)."""
    labels = []
    for v in pkg.symbols.table.values():
        if (
            expected_type is not None
            and isinstance(v, trlc.ast.Record_Object)
            and not _matches_expected_type(v.n_typ, expected_type)
        ):
            continue
        labels.append(v.name)
    return labels


def _resolve_package_object(tok, pre_tok, symbols):
    """Return the Package a dot-trigger's preceding token refers to.

    *tok* is the ``.`` token itself here (TRLC's own parser links a
    resolved Package onto both the name token and the following ``DOT``
    token — see ``parser.py``'s ``the_pkg.set_ast_link(t_dot)`` — so
    ``tok.ast_link`` is already the Package when the file parsed cleanly
    this cycle). When it isn't (lex-only fallback: a fresh lex has no
    ast_link at all), fall back to resolving *pre_tok* — the identifier
    immediately before the dot — by its own text against *symbols* (the
    scope-wide symbol table). Packages are always registered there
    independent of whether the *current* file's own content happens to
    parse this cycle, so this covers the fallback path too.
    """
    if isinstance(tok.ast_link, trlc.ast.Package):
        return tok.ast_link
    if pre_tok is not None and pre_tok.kind == "IDENTIFIER":
        pkg = lookup_by_name(symbols, pre_tok.value, trlc.ast.Package)
        if pkg is None:
            LOGGER.debug(
                "completion: dot-trigger fallback found no package named %r"
                " in this scope's symbol table",
                pre_tok.value,
            )
        return pkg
    LOGGER.debug(
        "completion: dot-trigger has no ast_link and no usable pre_tok "
        "(tok.kind=%s, pre_tok=%r)",
        tok.kind,
        pre_tok,
    )
    return None


def _is_dot_enum_qualified(tok, pre_tok) -> bool:
    """True when a dot-trigger on a Package token should complete enum literals.

    This is the case when the token before the dot is a Composite_Component
    whose type is an Enumeration_Type (e.g., typing ``MyRecord.color.``).
    """
    return (
        isinstance(tok.ast_link, trlc.ast.Package)
        and pre_tok is not None
        and pre_tok.ast_link is not None
        and isinstance(pre_tok.ast_link, trlc.ast.Composite_Component)
        and isinstance(pre_tok.ast_link.n_typ, trlc.ast.Enumeration_Type)
    )


# Threads through cursor/type-filtering context (tok_index, cur_pkg, tokens)
# needed for both the AST and lex-fallback resolution paths.
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def _dot_trigger_labels(
    tok, tok_index, pre_tok, symbols, cur_pkg, tokens
) -> Optional[List[str]]:
    """Return labels for a dot-trigger completion, or None."""
    if _is_dot_enum_qualified(tok, pre_tok):
        enu = pre_tok.ast_link.n_typ
        return [f"{enu.name}.{v.name}" for v in enu.literals.table.values()]

    pkg = _resolve_package_object(tok, pre_tok, symbols)
    if pkg is not None:
        # Includes Record_Object instances too: a cross-package derived_from
        # reference (Package.InstanceName@N) needs instance names, not just
        # types. Filtered to the enclosing field's declared type when one
        # could be resolved (see _resolve_expected_type).
        expected_type = _resolve_expected_type(
            tokens, tok_index, symbols, cur_pkg
        )
        return _package_symbol_labels(pkg, expected_type)

    if isinstance(tok.ast_link, trlc.ast.Enumeration_Type):
        return [v.name for v in tok.ast_link.literals.table.values()]
    if isinstance(tok.ast_link, trlc.ast.Name_Reference) and isinstance(
        tok.ast_link.typ, trlc.ast.Tuple_Type
    ):
        return [v.name for v in tok.ast_link.typ.components.table.values()]
    return None


def _space_trigger_labels(tok, cur_pkg, symbols) -> Optional[List[str]]:
    """Return labels for a space-trigger completion, or None."""
    if not (
        tok.kind == "ASSIGN"
        and isinstance(tok.ast_link, trlc.ast.Composite_Component)
    ):
        return None
    component_type = tok.ast_link.n_typ
    if isinstance(component_type, trlc.ast.Array_Type):
        component_type = component_type.element_type
    if isinstance(component_type, trlc.ast.Enumeration_Type):
        enu = component_type
        en_pkg = enu.n_package.name
        return [
            f"{en_pkg}.{enu.name}.{v.name}"
            if en_pkg != cur_pkg.name
            else f"{enu.name}.{v.name}"
            for v in enu.literals.table.values()
        ]
    if isinstance(component_type, trlc.ast.Record_Type):
        pkg_name = component_type.n_package.name
        try:
            return [
                v.name
                for v in symbols.table[pkg_name].symbols.table.values()
                if isinstance(v, trlc.ast.Record_Object)
                and v.n_typ.is_subclass_of(component_type)
            ]
        except KeyError:
            LOGGER.debug(
                "TRLC: completion: package %r not found in symbol table",
                pkg_name,
            )
    return None


# One branch per trigger-character kind; each returns independently rather
# than accumulating into a shared variable.
# pylint: disable-next=too-many-arguments,too-many-positional-arguments,too-many-return-statements
def _build_label_list(
    trigger_char: Optional[str],
    tok,
    tok_index: Optional[int],
    pre_tok,
    cur_pkg,
    symbols,
    tokens,
) -> Optional[List[str]]:
    """Return completion labels for the given trigger context, or None."""
    # Package/import keyword with space trigger → list all known packages
    if trigger_char == " " and tok and tok.value in ["package", "import"]:
        return [
            v.name
            for v in symbols.table.values()
            if isinstance(v, trlc.ast.Package)
        ]

    if tok is None:
        return None

    # Dot-trigger resolves the preceding Package by name when tok.ast_link
    # isn't available (lex-only fallback token) — must run before the
    # ast_link guard below, unlike every other trigger kind.
    if trigger_char == ".":
        return _dot_trigger_labels(
            tok, tok_index, pre_tok, symbols, cur_pkg, tokens
        )

    if tok.ast_link is None:
        return None

    # Brace trigger on a Record_Object → non-optional component list
    if trigger_char == "{" and isinstance(
        tok.ast_link, trlc.ast.Record_Object
    ):
        return [
            "".join(
                f"\n    {c_name} =" if value.optional is False else ""
                for c_name, value in tok.ast_link.n_typ.components.table.items()
            )
            + "\n"
        ]
    if trigger_char == " ":
        return _space_trigger_labels(tok, cur_pkg, symbols)
    return None


def _resolve_completion_inputs(ls, uri: str, file_path: str, trigger_char):
    """Resolve the token stream, symbol table, and current package needed to
    build completion labels for *uri*, or ``None`` (having already logged
    why) when completion can't proceed for this cursor position.

    Prefers the last full parse's AST-linked tokens; falls back to a
    lex-only pass over the live buffer when the file isn't in this cycle's
    vsm.all_files (most commonly a syntax error — possibly one unrelated to
    the cursor, e.g. still-being-typed content elsewhere in the file —
    dropped it from the parse entirely).
    """
    ctx = resolve_scope_context(ls, uri, file_path)
    if ctx is None:
        LOGGER.debug(
            "completion: no tokens/context for %s (either no editor buffer "
            "content to lex, or no active scope/context yet)",
            uri,
        )
        return None
    resolved, symbols, cur_pkg = ctx
    tokens = resolved.tokens
    LOGGER.debug(
        "completion: %s ast_available=%s token_count=%d trigger=%r",
        uri,
        resolved.ast_available,
        len(tokens),
        trigger_char,
    )
    return symbols, tokens, cur_pkg


def _locate_cursor_token(tokens, cursor_line: int, cursor_col: int):
    """Return ``(tok, pre_tok, tok_index)`` for the token at/before the
    cursor, and the token immediately before that one."""
    tok = get_token(tokens, cursor_line, cursor_col - 1, greedy=True)
    pre_tok = get_token(
        tokens, cursor_line, cursor_col - 1, greedy=True, tok_pre=1
    )
    tok_index = _token_index(tokens, tok) if tok is not None else None
    return tok, pre_tok, tok_index


def _log_dot_trigger_context(trigger_char, tok, pre_tok, cur_pkg) -> None:
    """Debug-log the resolved cursor context for a dot-trigger completion."""
    if trigger_char != ".":
        return
    LOGGER.debug(
        "completion: dot-trigger tok=%r(kind=%s, ast_link=%s) "
        "pre_tok=%r(kind=%s) cur_pkg=%s",
        getattr(tok, "value", None),
        getattr(tok, "kind", None),
        type(getattr(tok, "ast_link", None)).__name__,
        getattr(pre_tok, "value", None),
        getattr(pre_tok, "kind", None),
        cur_pkg.name if cur_pkg else None,
    )


def register(server: "ServerProtocol") -> None:
    """Register the completion handler on *server*."""

    @server.feature(
        TEXT_DOCUMENT_COMPLETION,
        CompletionOptions(trigger_characters=["{", " ", "."]),
    )
    def completion(ls, params: CompletionParams):
        """
        Gets completion items at a given cursor position for Package, Components
        of Record_Type, qualified Record_Type, Enumeration_Literal, Tuple_Type
        components in checks and Record_Reference when specific trigger
        characters appear.

        Parameters:
        - ls: The language server instance.
        - params: CompletionParams object containing the cursor position, the
          uri and the trigger character

        Returns:
        - CompletionList: A list of resolved completion items.
        """
        uri = params.text_document.uri
        file_path = path_from_uri(uri)
        trigger_char = params.context.trigger_character
        items: List[CompletionItem] = []

        resolved_inputs = _resolve_completion_inputs(
            ls, uri, file_path, trigger_char
        )
        if resolved_inputs is None:
            return CompletionList(is_incomplete=False, items=items)
        symbols, tokens, cur_pkg = resolved_inputs

        tok, pre_tok, tok_index = _locate_cursor_token(
            tokens, params.position.line, params.position.character
        )
        _log_dot_trigger_context(trigger_char, tok, pre_tok, cur_pkg)

        label_list = _build_label_list(
            trigger_char, tok, tok_index, pre_tok, cur_pkg, symbols, tokens
        )
        if label_list:
            items = [
                CompletionItem(label=label_string)
                for label_string in label_list
            ]

        return CompletionList(is_incomplete=False, items=items)
