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
"""Hover handler."""

from typing import Optional

import trlc.ast
from lsprotocol.types import (
    TEXT_DOCUMENT_HOVER,
    Hover,
    TextDocumentPositionParams,
)

from ..server_protocol import ServerProtocol
from ..token_utils import (
    get_location,
    resolve_identifier_at_position,
    resolve_unresolved_record_reference,
)


def _record_object_description(ast_obj, component_name: str) -> Optional[str]:
    """Return the string value of *ast_obj*'s *component_name* field, or
    ``None`` if it isn't a :class:`Record_Object`, *component_name* is
    empty, has no such field, or that field isn't a plain string.

    ``Record_Type``/``Tuple_Type``/``Composite_Component``/``Enumeration_Type``
    have their own ``.description`` Python attribute (an RSL doc-comment on
    the schema element itself — see :attr:`hover`'s own docstring). A
    ``Record_Object`` (a declared instance, e.g. a requirement referenced by
    name in a ``derived_from`` list) has no such attribute at all — its
    fields live in ``.field`` instead — so hovering over a reference to one
    would otherwise silently show nothing. Which field (if any) holds a
    human-readable summary is entirely schema-specific — not a TRLC
    language concept — so *component_name* comes from
    ``trlcServer.hover.descriptionComponent`` (default ``"description"``,
    empty string disables this) rather than being hardcoded to any one
    project's convention.
    """
    if not component_name or not isinstance(ast_obj, trlc.ast.Record_Object):
        return None
    value = ast_obj.field.get(component_name)
    if value is None or isinstance(value, trlc.ast.Implicit_Null):
        return None
    text = value.to_python_object()
    return text if isinstance(text, str) else None


def register(server: "ServerProtocol") -> None:
    """Register the hover handler on *server*."""

    @server.feature(TEXT_DOCUMENT_HOVER)
    def hover(ls, params: TextDocumentPositionParams):
        """
        Provides user defined description from Record_Type, Tuple_Type,
        Composite_Component, Enumeration_Type and Enumeration_Literal_Spec at
        the identifier token at a given cursor position associated with the
        described AST object. For a reference to a Record_Object instance
        (e.g. a name used in a derived_from list), shows the value of its
        configured description component instead (see
        :func:`_record_object_description` and
        ``trlcServer.hover.descriptionComponent``).

        Falls back to a lex-only pass plus name-based symbol lookup (see
        :func:`~server.token_utils.resolve_identifier_at_position`) when the
        file (or another file in the same scope) currently has a syntax
        error that dropped it out of the last full parse — otherwise a
        wholly valid identifier would silently show nothing just because
        TRLC's ``Source_Manager.process()`` keeps no partial AST for a file
        with an error.

        Also falls back to a by-name lookup (see
        :func:`~server.token_utils.resolve_unresolved_record_reference`)
        when the token resolves to an unresolved ``Record_Reference`` — e.g.
        a valid, later entry in a ``derived_from`` list left unresolved
        because an earlier, unrelated entry in the same list broke TRLC's
        deferred resolution pass — same fallback navigation uses.
        """
        resolved = resolve_identifier_at_position(
            ls, params, greedy=False, kinds=("IDENTIFIER",)
        )
        if resolved is None:
            return None
        cur_tok, ast_obj = resolved
        if ast_obj is None:
            ast_obj = resolve_unresolved_record_reference(cur_tok.ast_link)

        tok_loc = get_location(cur_tok)
        tok_rng = tok_loc.range

        config = ls.get_config_for_uri(params.text_document.uri)
        desc = _record_object_description(
            ast_obj, config.hover_description_component
        )
        if desc is None:
            try:
                desc = ast_obj.description
            except AttributeError:
                return None

        return Hover(contents=desc, range=tok_rng)
