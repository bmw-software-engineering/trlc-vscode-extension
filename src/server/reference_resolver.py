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

"""Shared find-references logic.

:class:`ReferenceResolver` encapsulates the cross-file reference search that
is used by both the *find references* and *rename* handlers.  Keeping it here
breaks the cross-handler import dependency that previously existed between
``handlers/rename.py`` and ``handlers/navigation.py``.
"""

import threading
import weakref

from .parse_guard import lookup_parsed_file
from .token_utils import (
    get_ast_entity,
    get_location,
    get_token,
    is_navigable_reference_token,
    resolve_unresolved_record_reference,
    uri_from_file,
)


def _collect_pars(all_files: dict, cur_pkg, imp_pkg) -> list:
    """Collect parsed file objects in the same package scope."""
    return [
        par
        for par in all_files.values()
        if par.lexer.tokens
        and (
            cur_pkg == par.cu.package
            or par.cu.package in imp_pkg
            or cur_pkg in par.cu.imports
        )
    ]


def _build_identifier_index(vsm) -> dict:
    """Build an index from AST entity identity to the locations of every
    IDENTIFIER token that links to it, across all of ``vsm.all_files``.

    Since TRLC AST entities don't override ``__eq__`` (only
    :class:`trlc.ast.Value` does — verified against trlc/ast.py), ``==``
    between entities is identity comparison, so ``id()`` is a safe, correct
    dict key here.

    Falls back to a by-name lookup (see
    :func:`~server.token_utils.resolve_unresolved_record_reference`) for a
    token whose ``Record_Reference`` never got its ``.target`` resolved —
    otherwise that reference site would silently be missing from every
    other reference's result list too, not just its own.
    """
    index: dict = {}
    for par in vsm.all_files.values():
        if not par.lexer.tokens:
            continue
        par_id = id(par)
        for tok in par.lexer.tokens:
            if tok.kind != "IDENTIFIER":
                continue
            ast_obj = get_ast_entity(tok)
            if ast_obj is None:
                ast_obj = resolve_unresolved_record_reference(tok.ast_link)
            if ast_obj is None:
                continue
            index.setdefault(id(ast_obj), []).append(
                (par_id, get_location(tok))
            )
    return index


# pylint: disable-next=too-few-public-methods  # single-purpose class; exposing more methods would break the clean interface
class ReferenceResolver:
    """Finds all LSP Locations that refer to the same AST entity as the
    identifier at a given cursor position.

    An instance is created by :class:`~server.language_server.TrlcLanguageServer`
    and stored as ``ls.refs`` so both the navigation and rename handlers can
    use it without importing each other.

    Search is scoped to the caller's own
    :class:`~server.parse_context.ParseContext` (``ls.get_context_for_uri``)
    rather than a global file table, so matches never leak across a
    DIRECTORY- or BAZEL-mode scope boundary.
    """

    def __init__(self):
        # vsm -> identifier index (see _build_identifier_index). Keyed by
        # weak reference so a scope's old vsm (replaced wholesale by the
        # parse engine on every reparse — see ParseResult) is dropped from
        # the cache the moment nothing else references it, instead of
        # accumulating indexes for every vsm generation ever parsed. A
        # lock guards concurrent build-and-cache: this resolver is called
        # from synchronous pygls handlers, which pygls may run in a
        # thread-pool executor rather than always on one fixed thread.
        self._index_cache: "weakref.WeakKeyDictionary" = (
            weakref.WeakKeyDictionary()
        )
        self._lock = threading.Lock()

    def _get_or_build_identifier_index(self, vsm) -> dict:
        """Return (building + caching if needed) the identifier index for
        *vsm* — see :func:`_build_identifier_index`.

        Built once per ``vsm`` generation (lazily, on first query), so it
        amortizes across repeated find-references/rename calls between
        parse cycles, and (with the parse engine's input-signature reuse)
        across unchanged parse cycles too.
        """
        with self._lock:
            index = self._index_cache.get(vsm)
            if index is None:
                index = _build_identifier_index(vsm)
                self._index_cache[vsm] = index
            return index

    def _find_locations(self, vsm, pars: list, ast_obj) -> list:
        """Return Locations of tokens in *pars* referencing *ast_obj*.

        Uses the cached identifier index instead of re-scanning every token
        in *pars* on every call.
        """
        eligible_par_ids = {id(par) for par in pars}
        index = self._get_or_build_identifier_index(vsm)
        return [
            location
            for par_id, location in index.get(id(ast_obj), [])
            if par_id in eligible_par_ids
        ]

    def resolve(self, ls, file_path: str, line: int, col: int):
        """Return all reference :class:`~lsprotocol.types.Location` objects
        for the identifier at *file_path*:*line*:*col*.

        Returns ``None`` when:

        * the file has not been parsed yet — a "please wait" message is shown
          via *ls*;
        * the position is not on an ``IDENTIFIER`` or ``DOT`` token;
        * the token's AST link is a builtin type or function.
        """
        uri = uri_from_file(file_path)
        # Shared parse-wait guard: returns the captured vsm (needed here for
        # the whole all_files table) and the parsed file object, or None (with
        # a "please wait" message already shown) if the file isn't parsed yet.
        parsed = lookup_parsed_file(ls, uri, file_path)
        if parsed is None:
            return None
        vsm = parsed[0]
        cur_pkg = parsed[1].cu.package
        imp_pkg = parsed[1].cu.imports
        tokens = parsed[1].lexer.tokens
        cur_tok = get_token(tokens, line, col, greedy=True)

        if not is_navigable_reference_token(cur_tok):
            return None

        ast_obj = get_ast_entity(cur_tok)
        if ast_obj is None:
            ast_obj = resolve_unresolved_record_reference(cur_tok.ast_link)
        if ast_obj is None:
            return None
        pars = _collect_pars(vsm.all_files, cur_pkg, imp_pkg)
        locations = self._find_locations(vsm, pars, ast_obj)
        return locations if locations else None
