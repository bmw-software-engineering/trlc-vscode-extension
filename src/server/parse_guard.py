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

"""Shared parse-wait guard for handlers and the reference resolver.

This is a *leaf* module: it imports only :mod:`lsprotocol` and stdlib
typing, never ``handlers`` or ``server_protocol``.  Keeping the guard here
gives every caller a single source of truth for the "please wait" message
and the not-yet-parsed check, and breaks the ``server_protocol`` <->
``handlers`` import cycle that previously forced inline imports.
"""

import os
from typing import Any, Optional, Tuple

from lsprotocol.types import MessageType, ShowMessageParams

#: User-facing message shown when a handler (or find-references/rename) is
#: invoked before the first parse cycle has produced this file.  Defined
#: here so every caller shares a single source of truth.
WAIT_PARSING = "TRLC: Please wait for parsing to finish"


def normalize_fs_path(path: str) -> str:
    """Canonicalize *path* for use as a dict key or equality comparison.

    ``normpath`` on an absolute path, plus (on Windows only) lowercasing
    just the drive letter — matters because ``token_utils.path_from_uri``
    (via pygls's ``to_fs_path``) always lowercases the drive letter, while
    paths obtained other ways (``os.walk``, ``tempfile``, ...) keep
    whatever case the filesystem happened to hand back. Two paths pointing
    at the same file can therefore differ in drive-letter case as raw
    strings on Windows even though POSIX never has this problem
    (case-sensitive, single separator) — comparing or keying a dict (e.g.
    TRLC's own ``Source_Manager.all_files``) by the raw string silently
    breaks only on Windows. A no-op on POSIX.

    Deliberately does *not* use ``os.path.normcase``: that lowercases the
    *entire* string on Windows, not just the drive letter. Since this
    function's result also flows back out into ``token_utils.uri_from_file``
    (e.g. via TRLC ``Location.file_name`` when publishing diagnostics), a
    full-string lowercase would silently rewrite folder-name casing that
    pygls's own URI conversion never touches — producing a
    ``file://`` URI that no longer matches the one the client actually
    used, so ``publishDiagnostics`` and the client's request never agree
    on a key. Lowercasing only the drive letter mirrors pygls's own
    convention exactly, so URIs re-derived from a normalized path stay
    byte-identical to the client's.

    Lives in this leaf module (stdlib-only, no ``token_utils``/``bazel``
    imports) rather than next to the URI helpers it pairs with, so every
    module that needs it — including ``token_utils`` itself, which already
    imports :func:`ensure_parsed` from here — can import it without risking
    a cycle.
    """
    resolved = os.path.normpath(os.path.abspath(path))
    if os.name == "nt" and len(resolved) >= 2 and resolved[1] == ":":
        resolved = resolved[0].lower() + resolved[1:]
    return resolved


def lookup_parsed_file(
    ls: Any, uri: str, file_path: str
) -> Optional[Tuple[Any, Any]]:
    """Return ``(vsm, file_obj)`` for a parsed file, or ``None`` if not ready.

    Looks up the :class:`~server.parse_context.ParseContext` that owns *uri*
    and, when the scope has already parsed *file_path*, returns both the
    symbol table (``vsm``) and the parsed file object.  If the uri has no
    active scope yet, or the scope hasn't parsed this file yet, an info
    message is shown to the user via *ls* and ``None`` is returned.

    Returning the ``vsm`` alongside the file object lets callers that need
    the whole file table (e.g. cross-file reference search) avoid a second
    context lookup and a second, potentially racing, read via
    :meth:`~server.parse_context.ParseContext.snapshot`.
    """
    context = ls.get_context_for_uri(uri)
    if context is None:
        ls.window_show_message(
            ShowMessageParams(type=MessageType.Info, message=WAIT_PARSING)
        )
        return None
    vsm = context.snapshot().vsm
    normalized_path = normalize_fs_path(file_path)
    if normalized_path not in vsm.all_files:
        ls.window_show_message(
            ShowMessageParams(type=MessageType.Info, message=WAIT_PARSING)
        )
        return None
    return vsm, vsm.all_files[normalized_path]


def ensure_parsed(ls: Any, uri: str, file_path: str):
    """Guard: return the parsed file object, or ``None`` if not ready.

    Thin wrapper over :func:`lookup_parsed_file` preserving the historical
    handler-facing signature (handlers only need the file object).  Shows the
    "please wait" message and returns ``None`` when the file is not parsed.

    Typical usage::

        file_obj = ensure_parsed(ls, uri, file_path)
        if file_obj is None:
            return None  # message already shown
        tokens = file_obj.lexer.tokens
        # ... rest of handler logic
    """
    result = lookup_parsed_file(ls, uri, file_path)
    if result is None:
        return None
    _vsm, file_obj = result
    return file_obj
