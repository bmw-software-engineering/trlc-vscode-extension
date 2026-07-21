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
"""Navigation handlers: goto definition, goto type definition, find references."""

import logging

import trlc.ast
from lsprotocol.types import (
    TEXT_DOCUMENT_DEFINITION,
    TEXT_DOCUMENT_REFERENCES,
    TEXT_DOCUMENT_TYPE_DEFINITION,
    DefinitionParams,
    ReferenceParams,
    TypeDefinitionParams,
)

from ..server_protocol import ServerProtocol
from ..token_utils import (
    get_location,
    path_from_uri,
    resolve_identifier_at_position,
    resolve_unresolved_record_reference,
)

LOGGER = logging.getLogger(__name__)


def _resolve_reference_entity(ls, params):
    """Resolve the identifier token at *params*'s cursor to the one AST
    entity it refers to, or ``None``.

    Falls back to lex-only tokens plus name-based symbol lookup (see
    :func:`~server.token_utils.resolve_identifier_at_position`) when the
    file — or another file in the same scope — currently has a syntax error
    that dropped it out of the last full parse; same fallback hover and
    completion use.
    """
    resolved = resolve_identifier_at_position(
        ls, params, greedy=True, kinds=("IDENTIFIER", "DOT")
    )
    if resolved is None:
        return None
    cur_tok, ast_obj = resolved

    if ast_obj is not None:
        return ast_obj

    if cur_tok.ast_link is None:
        return None

    # get_ast_entity's own docstring: "or None" — most commonly an
    # unresolved Record_Reference (see resolve_unresolved_record_reference's
    # own docstring for why: one broken reference earlier in the same field
    # can leave a perfectly valid, later reference in that field never
    # resolved by TRLC's deferred pass).
    link = cur_tok.ast_link
    fallback = resolve_unresolved_record_reference(link)
    if fallback is not None:
        return fallback
    if isinstance(link, trlc.ast.Record_Reference):
        LOGGER.debug(
            "goto-definition: unresolved Record_Reference %r "
            "(package=%s, target=%r) — by-name fallback found nothing",
            link.name,
            link.package.name if link.package else None,
            link.target,
        )
    else:
        LOGGER.debug(
            "goto-definition: ast_link %s not handled by get_ast_entity",
            type(link).__name__,
        )
    return None


def _type_of_entity(ast_obj):
    """Return the AST entity "Go to Type Definition" should resolve to for
    *ast_obj*.

    A ``Record_Object`` (a declared instance — e.g. what a ``derived_from``
    reference points to) is itself a *value*; its type is the RSL
    ``Record_Type`` that declares its schema (``ast_obj.n_typ``), which is
    what "type definition" means for it — not the instance's own location,
    which is what plain "definition" already correctly returns. Every other
    kind of entity this resolves to (``Record_Type``, ``Enumeration_Type``,
    ``Composite_Component``, ``Package``, ...) already *is* the type-level
    thing being referenced, so it's returned unchanged.
    """
    if isinstance(ast_obj, trlc.ast.Record_Object):
        return ast_obj.n_typ
    return ast_obj


def register(server: "ServerProtocol") -> None:
    """Register navigation handlers on *server*."""

    @server.feature(TEXT_DOCUMENT_DEFINITION)
    def goto_definition(ls, params: DefinitionParams):
        """
        Finds the definition location for the identifier token at a given
        cursor position — what VS Code's Ctrl+click / F12 actually send
        (distinct from "Go to Type Definition", :func:`goto_type_definition`,
        which resolves one step further, to the entity's *type*).
        """
        ast_obj = _resolve_reference_entity(ls, params)
        if ast_obj is None:
            return None
        return get_location(ast_obj)

    @server.feature(TEXT_DOCUMENT_TYPE_DEFINITION)
    def goto_type_definition(ls, params: TypeDefinitionParams):
        """
        Finds the location of the type definition for the identifier token at a
        given cursor position linked to an AST object of type Entity — e.g.
        for a reference to a Record_Object (an instance, such as a name used
        in a derived_from list), this is the RSL Record_Type that declares
        its schema, not the instance's own location (see :func:`_type_of_entity`).
        """
        ast_obj = _resolve_reference_entity(ls, params)
        if ast_obj is None:
            return None
        type_obj = _type_of_entity(ast_obj)
        if type_obj is None:
            return None
        return get_location(type_obj)

    @server.feature(TEXT_DOCUMENT_REFERENCES)
    def references(ls, params: ReferenceParams):
        """
        Finds all references for the identifier token at a given cursor
        position linked to identical AST objects.
        """
        return ls.refs.resolve(
            ls,
            path_from_uri(params.text_document.uri),
            params.position.line,
            params.position.character,
        )
