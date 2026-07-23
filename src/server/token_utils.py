# *******************************************************************************
# TRLC VSCode Extension
# Copyright (C) 2023 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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

"""Pure utility functions shared by LSP feature handlers.

All functions in this module are stateless and do not reference the
language server instance directly.  This makes them easy to unit-test
in isolation.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

import trlc.ast
import trlc.errors
import trlc.lexer
from lsprotocol.types import Location, Position, Range
from pygls.uris import from_fs_path, to_fs_path

from .parse_guard import ensure_parsed, normalize_fs_path

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LSP Semantic Token Types
# The order here defines the index used in the encoded token data.
# ---------------------------------------------------------------------------
SEMANTIC_TOKEN_TYPES = [
    "keyword",  # 0
    "comment",  # 1
    "string",  # 2
    "number",  # 3
    "operator",  # 4
    "namespace",  # 5
    "type",  # 6
    "variable",  # 7
    "enumMember",  # 8
    "property",  # 9
]

_SEMANTIC_KEYWORD = 0
_SEMANTIC_COMMENT = 1
_SEMANTIC_STRING = 2
_SEMANTIC_NUMBER = 3
_SEMANTIC_OPERATOR = 4
_SEMANTIC_NAMESPACE = 5
_SEMANTIC_TYPE = 6
_SEMANTIC_VARIABLE = 7
_SEMANTIC_ENUM_MEMBER = 8
_SEMANTIC_PROPERTY = 9

# Static mapping from TRLC lexer token kind to semantic type index.
# IDENTIFIER is handled dynamically via the AST link.
_KIND_TO_SEMANTIC = {
    "KEYWORD": _SEMANTIC_KEYWORD,
    "COMMENT": _SEMANTIC_COMMENT,
    "STRING": _SEMANTIC_STRING,
    "INTEGER": _SEMANTIC_NUMBER,
    "DECIMAL": _SEMANTIC_NUMBER,
    "OPERATOR": _SEMANTIC_OPERATOR,
}


# ---------------------------------------------------------------------------
# URI ↔ path conversion
# ---------------------------------------------------------------------------


def uri_from_file(file_name: str) -> str:
    """Convert an absolute file-system path to a ``file://`` URI.

    Thin wrapper around :func:`pygls.uris.from_fs_path` (ported from
    vscode-uri) — handles UNC paths and Windows drive-letter casing that a
    hand-rolled ``urllib.parse`` version would not.
    """
    return from_fs_path(os.path.abspath(file_name))


def path_from_uri(uri: str) -> str:
    """Extract the decoded file-system path from a ``file://`` URI.

    Thin wrapper around :func:`pygls.uris.to_fs_path`; see
    :func:`uri_from_file`.
    """
    return to_fs_path(uri)


# ---------------------------------------------------------------------------
# AST / token helpers
# ---------------------------------------------------------------------------


def get_ast_entity(token: trlc.lexer.Token) -> Optional[trlc.ast.Entity]:
    """Return the ``trlc.ast.Entity`` linked with *token*, or ``None``.

    Follows one level of indirection for ``Name_Reference``,
    ``Record_Reference``, and ``Enumeration_Literal`` links so callers
    always receive a concrete ``Entity`` (or ``None``).
    """
    if not isinstance(token, trlc.lexer.Token):
        raise TypeError(
            f"token must be trlc.lexer.Token, got {type(token).__name__}"
        )
    lk = token.ast_link
    if isinstance(lk, trlc.ast.Entity):
        return lk
    if isinstance(lk, trlc.ast.Name_Reference):
        return lk.entity
    if isinstance(lk, trlc.ast.Record_Reference):
        return lk.target
    if isinstance(lk, trlc.ast.Enumeration_Literal):
        return lk.value
    return None


def resolve_unresolved_record_reference(
    ast_link,
) -> Optional["trlc.ast.Record_Object"]:
    """Best-effort by-name fallback for an unresolved ``Record_Reference``.

    ``get_ast_entity`` returns ``None`` for a ``Record_Reference`` whose
    ``.target`` was never filled in — most commonly a forward reference in a
    file where TRLC's deferred ``resolve_record_references`` pass aborted
    early: it iterates a ``Record_Object``'s fields/array-elements with no
    per-element try/except, so one broken reference earlier in the same
    field (e.g. an earlier entry in a ``derived_from`` list) prevents a
    perfectly valid, *later* reference in that same field from ever being
    resolved — even though the referenced object exists and parsed cleanly.

    The reference's own ``.package`` is always filled in by the parser
    regardless of resolution success, so a by-name lookup against it works
    independent of that pass ever having run for this particular entry.
    Returns ``None`` if *ast_link* isn't a ``Record_Reference`` or the
    by-name lookup still finds nothing (e.g. a genuinely unknown name).
    """
    if not isinstance(ast_link, trlc.ast.Record_Reference):
        return None
    return lookup_by_name(
        ast_link.package.symbols if ast_link.package else None,
        ast_link.name,
        trlc.ast.Record_Object,
    )


def get_location(obj) -> Location:
    """Return an LSP :class:`Location` for a token or AST node."""
    if not isinstance(obj, (trlc.lexer.Token, trlc.ast.Node)):
        raise TypeError(f"obj must be Token or Node, got {type(obj).__name__}")
    end_loc = obj.location.get_end_location()
    end_line = 0 if end_loc.line_no is None else end_loc.line_no - 1
    end_col = 1 if end_loc.col_no is None else end_loc.col_no
    start_line = (
        0 if obj.location.line_no is None else obj.location.line_no - 1
    )
    start_col = 0 if obj.location.col_no is None else obj.location.col_no - 1
    return Location(
        uri=uri_from_file(obj.location.file_name),
        range=Range(
            start=Position(line=start_line, character=start_col),
            end=Position(line=end_line, character=end_col),
        ),
    )


def get_token(
    tokens: list,
    cursor_line: int,
    cursor_col: int,
    greedy: bool = False,
    tok_pre: int = 0,
):
    """Return the token that covers *(cursor_line, cursor_col)*, or ``None``.

    Parameters
    ----------
    tokens:
        Flat list of lexer tokens for the file.
    cursor_line:
        0-based line index.
    cursor_col:
        0-based column index.
    greedy:
        When ``True`` and no token covers the cursor, find the nearest token
        to the left of the cursor in a single O(n) pass instead of the
        original O(n × cursor_col) column-by-column retry.  Useful for
        resolving the token the cursor is immediately *after*.
    tok_pre:
        Number of tokens to step backwards from the matched token.
        ``0`` returns the matched token itself; ``1`` returns the one before
        it, etc.
    """
    # Phase 1: exact match — token whose range covers the cursor position.
    for i, token in enumerate(tokens):
        tok_rng = get_location(token).range
        if (
            tok_rng.start.character <= cursor_col < tok_rng.end.character
            and tok_rng.start.line <= cursor_line <= tok_rng.end.line
        ):
            target = i - tok_pre
            return tokens[target] if 0 <= target < len(tokens) else None

    if not greedy or cursor_col <= 0:
        return None

    # Phase 2: greedy — cursor is in a gap between tokens.  In that case
    # no token covers cursor_col exactly, so every token is either fully
    # to the left (end ≤ cursor_col) or fully to the right.  Find the
    # rightmost token on cursor_line that ends at or before cursor_col.
    best_i: Optional[int] = None
    for i, token in enumerate(tokens):
        tok_rng = get_location(token).range
        if (
            tok_rng.end.line == cursor_line
            and tok_rng.end.character <= cursor_col
        ):
            best_i = i  # tokens are in source order; last match is rightmost

    if best_i is None:
        return None
    target = best_i - tok_pre
    return tokens[target] if 0 <= target < len(tokens) else None


def lex_fallback(uri: str, ls) -> list:
    """Lex *uri* from the workspace snapshot without AST links.

    Used when the file has not been parsed yet, or its last parse attempt
    failed (e.g. a syntax error dropped it from ``vsm.all_files`` entirely —
    TRLC's ``Source_Manager.process()`` does not keep a partial AST for a
    file with a syntax error). Tokens returned this way never have
    ``ast_link`` set; callers that need semantic info must resolve it some
    other way (e.g. by name, against the scope's symbol table).
    """
    doc = ls.workspace.get_text_document(uri)
    try:
        source = doc.source
    except OSError as e:
        # pygls' Workspace reads from disk for a uri it doesn't have an
        # open/in-memory copy of; a uri that also doesn't exist on disk
        # (never opened, no active scope) has nothing to fall back to.
        LOGGER.debug("Lex fallback: no content for %s: %s", uri, e)
        return []
    if not source:
        return []
    mh = trlc.errors.Message_Handler()
    lexer = trlc.lexer.TRLC_Lexer(mh, uri, source)
    tokens = []
    while True:
        try:
            tok = lexer.token()
        except trlc.errors.TRLC_Error as e:
            LOGGER.debug("Lex fallback stopped early for %s: %s", uri, e)
            break
        if tok is None:
            break
        tokens.append(tok)
    return tokens


@dataclass
class ResolvedTokens:
    """Result of :func:`resolve_document_tokens`."""

    tokens: list
    """Token objects for the document, in source order."""

    ast_available: bool
    """``True`` if these tokens come from the last full parse and
    ``tok.ast_link`` can be trusted; ``False`` if they're a lex-only
    fallback pass over the live buffer, where ``ast_link`` is always
    ``None``."""


def _ast_tokens_are_stale(parsed, ls, uri: str) -> bool:
    """True if *parsed*'s lexer content no longer matches the live editor
    buffer for *uri* — i.e. an edit landed after the last completed parse.

    A stale-but-"available" AST is worse than no AST for cursor-position
    lookups: :func:`get_token` matches purely by ``(line, col)``, so if the
    edited line's length changed since this parse — the common case while
    actively typing, especially under BAZEL mode where a debounced parse
    cycle (``bazel query`` + CVC5 verify) can take many seconds to catch up
    — the *same* live cursor position can silently resolve to the wrong
    token (observed: cursor right after a freshly-typed ``.`` resolving to
    the identifier *before* it instead of the ``.`` itself, because the
    stale token stream doesn't have that character's column shift yet).
    """
    try:
        live_source = ls.workspace.get_text_document(uri).source
    except OSError:
        return False  # no live buffer to compare against; trust the AST
    return parsed.lexer.content != live_source


def resolve_document_tokens(
    ls, uri: str, file_path: str
) -> Optional[ResolvedTokens]:
    """Return tokens for *uri*, preferring the last full parse.

    Falls back to a lex-only pass over the live buffer (see
    :func:`lex_fallback`) when the file isn't in the current scope's
    ``vsm.all_files`` — most commonly because a syntax error (possibly one
    unrelated to the cursor position, e.g. an in-progress edit elsewhere in
    the file) caused TRLC to drop it from this parse cycle entirely, or
    because there's no active scope/parse for this uri at all yet — or
    because the last parsed content is stale relative to the live buffer
    (see :func:`_ast_tokens_are_stale`). Returns ``None`` only when even the
    lex-only fallback finds nothing (e.g. the editor has no buffer content
    for this uri either).
    """
    context = ls.get_context_for_uri(uri)
    if context is not None:
        parsed = context.snapshot().vsm.all_files.get(
            normalize_fs_path(file_path)
        )
        if (
            parsed is not None
            and parsed.lexer.tokens
            and not _ast_tokens_are_stale(parsed, ls, uri)
        ):
            return ResolvedTokens(
                tokens=parsed.lexer.tokens, ast_available=True
            )
    tokens = lex_fallback(uri, ls)
    if not tokens:
        return None
    return ResolvedTokens(tokens=tokens, ast_available=False)


def lookup_by_name(table, name: Optional[str], required_type):
    """Side-effect-free symbol lookup by *name* in *table* (a Symbol_Table).

    Thin wrapper over ``trlc.ast.Symbol_Table.lookup_assuming`` (never
    raises/errors when called without ``required_subclass``) that also
    tolerates a ``None`` *table* or *name* — both are common for callers
    resolving an unresolved package, or a file with no ``package``
    statement yet. Shared by completion (resolving a dot-triggered name
    when the file's own AST link isn't available) and navigation (falling
    back to a name-based lookup when a ``Record_Reference.target`` never
    got filled in — see :mod:`server.handlers.navigation`).
    """
    if table is None or name is None:
        return None
    mh = trlc.errors.Message_Handler()
    result = table.lookup_assuming(mh, name)
    return result if isinstance(result, required_type) else None


def package_name_from_tokens(tokens: list) -> Optional[str]:
    """Return the declared package name from a raw (lex-only) token list.

    The ``package <name>`` statement is always the first statement in a
    ``.trlc``/``.rsl`` file, so this is reliable even when the rest of the
    file fails to parse.
    """
    for i, tok in enumerate(tokens):
        if tok.kind == "KEYWORD" and tok.value == "package":
            if i + 1 < len(tokens) and tokens[i + 1].kind == "IDENTIFIER":
                return tokens[i + 1].value
            return None
    return None


def resolve_scope_context(ls, uri: str, file_path: str):
    """Return ``(ResolvedTokens, symbols, cur_pkg)`` for *uri*, or ``None``.

    Shared by completion and hover: wraps :func:`resolve_document_tokens`
    with the symbol-table/current-package lookup every fallback-aware
    resolver needs. Prefers the last full parse's ``cu.package``; on the
    lex-only fallback path, derives the package from the raw token stream
    via :func:`package_name_from_tokens` instead.
    """
    resolved = resolve_document_tokens(ls, uri, file_path)
    if resolved is None:
        return None
    context = ls.get_context_for_uri(uri)
    if context is None:
        return None
    vsm = context.snapshot().vsm
    symbols = vsm.stab
    if resolved.ast_available:
        cur_pkg = vsm.all_files[normalize_fs_path(file_path)].cu.package
    else:
        cur_pkg = lookup_by_name(
            symbols,
            package_name_from_tokens(resolved.tokens),
            trlc.ast.Package,
        )
    return resolved, symbols, cur_pkg


def resolve_open_file(ls, params):
    """Return ``(uri, file_path, file_obj)`` for *params*'s document.

    ``None`` if the file isn't parsed yet (the "please wait" message has
    already been shown via :func:`~server.parse_guard.ensure_parsed` in that
    case). Used by ``rename``, which needs the parsed file itself and —
    unlike hover/navigation, which fall back to a lex-only pass via
    :func:`resolve_scope_context` when the file dropped out of the last
    parse — intentionally has no such fallback: rename already refuses to
    run on a scope with errors, so requiring a full parse here is correct,
    not a gap.
    """
    uri = params.text_document.uri
    file_path = path_from_uri(uri)
    file_obj = ensure_parsed(ls, uri, file_path)
    if file_obj is None:
        return None
    return uri, file_path, file_obj


def resolve_identifier_at_position(ls, params, *, greedy: bool, kinds):
    """Return ``(cur_tok, ast_obj)`` for the token at *params*'s cursor, or
    ``None`` if there's no resolvable token there.

    Shared by hover and navigation: wraps :func:`resolve_scope_context` with
    the token lookup and AST/name-based resolution both need. *greedy*
    and *kinds* (the set of acceptable ``tok.kind`` values) are the two
    axes the two callers differ on — hover wants an exact-position,
    IDENTIFIER-only match; navigation wants a greedy nearest-token match
    that also accepts DOT (Ctrl+click can land on the dot in
    ``Package.Name``).

    *ast_obj* may be ``None`` even when a token is returned (e.g. an
    unresolved ``Record_Reference`` whose deferred resolution pass was
    skipped for the whole file — see :func:`get_ast_entity`). Callers that
    need a fallback for that case (see navigation's ``Record_Reference``
    handling) must implement it themselves; hover already tolerates
    ``None`` via ``AttributeError`` on ``ast_obj.description``.
    """
    uri = params.text_document.uri
    file_path = path_from_uri(uri)
    ctx = resolve_scope_context(ls, uri, file_path)
    if ctx is None:
        return None
    resolved, symbols, cur_pkg = ctx

    cur_tok = get_token(
        resolved.tokens,
        params.position.line,
        params.position.character,
        greedy=greedy,
    )
    if (
        cur_tok is None
        or cur_tok.kind not in kinds
        or is_builtin_token(cur_tok)
    ):
        return None

    if cur_tok.ast_link is not None:
        ast_obj = get_ast_entity(cur_tok)
    else:
        ast_obj = resolve_unlinked_identifier(
            cur_tok, resolved.tokens, symbols, cur_pkg
        )
    return cur_tok, ast_obj


def resolve_unlinked_identifier(tok, tokens, symbols, cur_pkg):
    """Best-effort name-based lookup for an IDENTIFIER token with no
    ``ast_link`` (a lex-only fallback token — see
    :func:`resolve_document_tokens`).

    Mirrors completion's dot-trigger package resolution (see
    ``_resolve_package_object`` in ``handlers/completion.py``), generalized
    to any :class:`trlc.ast.Entity` subclass rather than just ``Package``,
    since callers here don't know ahead of time whether the identifier names
    a ``Record_Type``, ``Enumeration_Type``, ``Record_Object``, etc. A
    qualified ``Package.Name`` reference resolves via the package; an
    unqualified name resolves against the current package's own symbols.

    Locates *tok* within *tokens* by identity rather than by cursor
    position, so it works the same whether the caller matched *tok* via an
    exact-position lookup (hover) or a greedy nearest-token-to-the-left one
    (goto-definition) — either way *tok* is one of the objects in *tokens*.
    """
    idx = next((i for i, t in enumerate(tokens) if t is tok), None)
    if (
        idx is not None
        and idx >= 2
        and tokens[idx - 1].kind == "DOT"
        and tokens[idx - 2].kind == "IDENTIFIER"
    ):
        pkg = lookup_by_name(symbols, tokens[idx - 2].value, trlc.ast.Package)
        return lookup_by_name(
            pkg.symbols if pkg is not None else None,
            tok.value,
            trlc.ast.Entity,
        )
    return lookup_by_name(
        cur_pkg.symbols if cur_pkg is not None else None,
        tok.value,
        trlc.ast.Entity,
    )


def _get_identifier_semantic_type(ast_obj) -> int:
    """Return the semantic token type for an IDENTIFIER token's AST entity."""
    if isinstance(ast_obj, trlc.ast.Package):
        return _SEMANTIC_NAMESPACE
    if isinstance(
        ast_obj,
        (
            trlc.ast.Record_Type,
            trlc.ast.Tuple_Type,
            trlc.ast.Enumeration_Type,
            trlc.ast.Builtin_Type,
        ),
    ):
        return _SEMANTIC_TYPE
    if isinstance(ast_obj, trlc.ast.Enumeration_Literal_Spec):
        return _SEMANTIC_ENUM_MEMBER
    if isinstance(ast_obj, trlc.ast.Composite_Component):
        return _SEMANTIC_PROPERTY
    return _SEMANTIC_VARIABLE


def get_token_semantic_type(tok) -> Optional[int]:
    """Return the LSP semantic token type index for *tok*, or ``None`` to skip.

    Multi-line tokens (block comments, triple-quoted strings) are always
    skipped because the LSP delta encoding does not support them.
    """
    if tok.location.lexer is None:
        return None
    token_text = tok.location.lexer.content[
        tok.location.start_pos : tok.location.end_pos + 1
    ]
    if "\n" in token_text:
        return None

    st = _KIND_TO_SEMANTIC.get(tok.kind)
    if st is not None:
        return st

    if tok.kind == "IDENTIFIER" and tok.ast_link is not None:
        ast_obj = get_ast_entity(tok)
        if ast_obj is None:
            return _SEMANTIC_VARIABLE
        return _get_identifier_semantic_type(ast_obj)

    return None


#: Separator between a folder_uri and its scope segment (directory path or
#: Bazel target label) in a scope_id. Must not appear inside a folder_uri.
_SCOPE_SEPARATOR = "::"


def encode_scope_id(folder_uri: str, segment: Optional[str] = None) -> str:
    """Build a scope_id from a folder_uri and an optional segment.

    With no *segment*, the scope is folder-wide (WORKSPACE/REPO mode) and
    the scope_id is the bare folder_uri. With a *segment* (a directory path
    for DIRECTORY mode, or a Bazel target label for BAZEL mode), the scope_id
    is ``"{folder_uri}::{segment}"``.

    Raises ``ValueError`` if *folder_uri* itself contains the separator,
    since that would make :func:`decode_scope_id` ambiguous.
    """
    if _SCOPE_SEPARATOR in folder_uri:
        raise ValueError(
            f"folder_uri must not contain {_SCOPE_SEPARATOR!r}: {folder_uri!r}"
        )
    if segment is None:
        return folder_uri
    return f"{folder_uri}{_SCOPE_SEPARATOR}{segment}"


def decode_scope_id(scope_id: str) -> "tuple[str, Optional[str]]":
    """Inverse of :func:`encode_scope_id`.

    Returns ``(folder_uri, segment)`` where *segment* is ``None`` for a
    folder-wide scope. Splits on the first occurrence of the separator so a
    segment (directory path, target label) may itself safely contain it.
    """
    if _SCOPE_SEPARATOR in scope_id:
        folder_uri, segment = scope_id.split(_SCOPE_SEPARATOR, 1)
        return folder_uri, segment
    return scope_id, None


def folder_for_uri(uri: str, folders: dict) -> Optional[str]:
    """Resolve which workspace folder owns a document URI.

    Returns the folder URI (longest-prefix path match) that contains the
    document, or None if the document is outside all folders (e.g., untitled).

    Compares real filesystem paths (via :func:`path_from_uri`, which
    percent-decodes and normalizes Windows drive-letter casing) rather than
    raw URI text, and only matches a folder if the document path *equals*
    it or is nested *under* it (a path separator after the shared prefix) —
    a bare string-prefix match would wrongly treat folder ``/home/foo`` as
    containing a sibling directory ``/home/foo-bar``.

    Args:
        uri: Document URI (file://...)
        folders: Dict[folder_uri, folder_name] from ls.workspace.folders

    Returns:
        Folder URI that contains this document, or None if no match.
    """
    doc_fs_path = path_from_uri(uri)
    if doc_fs_path is None:
        # Non-file-scheme document (e.g. "untitled:Untitled-1") has no
        # filesystem path, so no workspace folder can contain it.
        return None
    doc_path = normalize_fs_path(doc_fs_path)

    matches = []
    for folder_uri in folders.keys():
        folder_fs_path = path_from_uri(folder_uri)
        if folder_fs_path is None:
            continue
        folder_path = normalize_fs_path(folder_fs_path)
        if doc_path == folder_path or doc_path.startswith(
            folder_path + os.sep
        ):
            matches.append((folder_path, folder_uri))

    if not matches:
        return None

    matches.sort(key=lambda x: len(x[0]), reverse=True)
    return matches[0][1]


def is_builtin_token(tok) -> bool:
    """Return True if *tok*'s AST link is a built-in type or function.

    Used by navigation and hover handlers to skip tokens that have no
    user-defined definition to navigate to.
    """
    return tok.ast_link is not None and isinstance(
        tok.ast_link, (trlc.ast.Builtin_Type, trlc.ast.Builtin_Function)
    )


def is_navigable_reference_token(tok) -> bool:
    """Return True if *tok* is a goto-definition/find-references candidate.

    Shared validity check for navigation's ``goto_type_definition`` and
    :meth:`~server.reference_resolver.ReferenceResolver.resolve`: the token
    must be a resolvable ``IDENTIFIER``/``DOT`` with a non-builtin AST link.
    """
    return (
        tok is not None
        and tok.ast_link is not None
        and tok.kind in ("IDENTIFIER", "DOT")
        and not is_builtin_token(tok)
    )
