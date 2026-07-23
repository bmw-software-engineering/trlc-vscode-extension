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

"""Thread-safe central store for parse scopes and per-folder configuration.

:class:`ContextStore` owns the three structures shared between the pygls
main thread (LSP handlers, lifecycle events) and the single ParseEngine
worker thread: active parse scopes (``scope_id -> ParseContext``), per-folder
configuration overrides, and the ``uri -> scope_id`` map that records which
scope each open document belongs to.

All structural mutation (dict insert/remove) goes through this class under
one lock. Parsing itself never happens under this lock: the engine calls
:meth:`snapshot_contexts` to get a locked copy, releases the lock, parses
off to the side, and publishes a fresh
:class:`~server.parse_context.ParseResult` onto the (already-locked-and-
released) ``ParseContext.result`` reference — see that class for why the
publish step itself needs no lock.
"""

import threading
from typing import Callable, Dict, List, Optional, Tuple

from .parse_context import ParseContext
from .server_config import ServerConfig


class ContextStore:
    """Owns ``contexts``, ``folder_configs``, and ``uri -> scope_id`` behind
    a single :class:`threading.RLock`.

    A single lock (rather than one per structure) is used because callers
    routinely need to read config and then look up/create a context as one
    logical operation (e.g. did_open); an RLock lets such call chains nest
    without deadlocking. Lock hold times are always plain dict operations —
    parsing happens outside the lock via the snapshot-then-swap pattern
    described in the module docstring.
    """

    def __init__(self, default_config: Optional[ServerConfig] = None):
        self._lock: threading.RLock = threading.RLock()
        self._contexts: Dict[str, ParseContext] = {}
        self._folder_configs: Dict[str, ServerConfig] = {}
        self._uri_scope_map: Dict[str, str] = {}
        self._default_config: ServerConfig = default_config or ServerConfig()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @property
    def default_config(self) -> ServerConfig:
        """Default config."""
        with self._lock:
            return self._default_config

    def set_default_config(self, config: ServerConfig) -> None:
        """Set default config."""
        with self._lock:
            self._default_config = config

    def set_folder_config(self, folder_uri: str, config: ServerConfig) -> None:
        """Set folder config."""
        with self._lock:
            self._folder_configs[folder_uri] = config

    def has_folder_config(self, folder_uri: str) -> bool:
        """Has folder config."""
        with self._lock:
            return folder_uri in self._folder_configs

    def get_config_for_uri(self, folder_uri: Optional[str]) -> ServerConfig:
        """Return the folder-specific config if one exists, else the default."""
        with self._lock:
            if folder_uri and folder_uri in self._folder_configs:
                return self._folder_configs[folder_uri]
            return self._default_config

    def folder_config_items(self) -> List[Tuple[str, ServerConfig]]:
        """Locked snapshot of ``(folder_uri, config)`` pairs."""
        with self._lock:
            return list(self._folder_configs.items())

    # ------------------------------------------------------------------
    # Contexts
    # ------------------------------------------------------------------

    def get_context(self, scope_id: Optional[str]) -> Optional[ParseContext]:
        """Return the context for *scope_id*, or ``None`` if no such scope
        is active."""
        if not scope_id:
            return None
        with self._lock:
            return self._contexts.get(scope_id)

    def get_context_for_uri(self, uri: str) -> Optional[ParseContext]:
        """Return the context owning the currently-open document *uri*.

        Uses the scope_id recorded at the time the document was opened
        (see :meth:`add_file`), not a freshly re-derived one — every LSP
        handler that calls this is acting on an already-open document, so
        this is an O(1) lookup rather than re-running scope-mode math on
        every request.
        """
        with self._lock:
            scope_id = self._uri_scope_map.get(uri)
            return self._contexts.get(scope_id) if scope_id else None

    def scope_id_for_uri(self, uri: str) -> Optional[str]:
        """Return the scope_id *uri* is currently bound to (recorded at
        :meth:`add_file` time), or ``None`` if the uri isn't open."""
        with self._lock:
            return self._uri_scope_map.get(uri)

    def snapshot_contexts(self) -> Dict[str, ParseContext]:
        """Locked copy of the ``scope_id -> ParseContext`` mapping, safe to
        iterate without the lock (a parse cycle may run for a while)."""
        with self._lock:
            return dict(self._contexts)

    def add_file(
        self,
        scope_id: str,
        uri: str,
        content: str,
        context_factory: Callable[[], ParseContext],
    ) -> None:
        """Create the scope's context if missing, then record *uri* as open
        in it.

        *context_factory* is invoked (at most once) under the lock, so
        callers can build a fresh :class:`ParseContext` without a
        check-then-create race against a concurrent ``add_file`` for the
        same scope.
        """
        with self._lock:
            if scope_id not in self._contexts:
                self._contexts[scope_id] = context_factory()
            self._contexts[scope_id].open_files[uri] = content
            self._uri_scope_map[uri] = scope_id

    def remove_file(self, uri: str) -> None:
        """Remove *uri* from its scope; destroy the scope if now empty.

        Looks up the scope_id recorded at :meth:`add_file` time rather than
        re-deriving one from the current config — the folder's parseMode may
        have changed since the file was opened, which would otherwise
        resolve to the wrong (or no) scope_id and leak the context.
        """
        with self._lock:
            scope_id = self._uri_scope_map.pop(uri, None)
            if scope_id and scope_id in self._contexts:
                self._contexts[scope_id].open_files.pop(uri, None)
                if not self._contexts[scope_id].open_files:
                    del self._contexts[scope_id]

    def sample_uri(self, context: ParseContext) -> str:
        """Locked read of one open uri from *context*, or ``""`` if none."""
        with self._lock:
            return next(iter(context.open_files), "")

    def open_file_count(self, context: ParseContext) -> int:
        """Locked read of how many files are open in *context*'s scope."""
        with self._lock:
            return len(context.open_files)
