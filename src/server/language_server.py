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

"""The TRLC Language Server class.

:class:`TrlcLanguageServer` is a thin :class:`pygls.lsp.server.LanguageServer`
subclass.  Its only responsibilities are:

* holding the shared objects — :class:`~server.context_store.ContextStore`
  (parse scopes + per-folder config) and :class:`~server.parse_engine.ParseEngine`
  — plus the :class:`~server.trlc_utils.File_Handler`;
* registering all LSP feature handlers (via the ``handlers/`` package);
* applying configuration updates received from the client;
* resolving a document URI to its scope_id (parse-mode dependent scope
  boundary math — the one piece of scope logic that isn't pure data storage
  and so doesn't belong on :class:`~server.context_store.ContextStore`).

All TRLC-specific logic lives in the modules imported below.
"""

import logging
import os
from typing import Optional

from pygls.lsp.server import LanguageServer

from .bazel import BazelManagerCache, resolve_bazel_manager
from .context_store import ContextStore
from .file_handler import File_Handler
from .handlers import (
    code_actions,
    completion,
    hover,
    lifecycle,
    navigation,
    rename,
    semantic_tokens,
    status,
)
from .logging_config import attach_output_channel_handler, level_from_name
from .parse_context import ParseContext
from .parse_engine import ParseEngine
from .reference_resolver import ReferenceResolver
from .scope_strategies import get_scope_strategy, report_bazel_query_progress
from .server_config import ServerConfig
from .server_protocol import HandlerModule
from .token_utils import folder_for_uri, path_from_uri

LOGGER = logging.getLogger(__name__)


class TrlcLanguageServer(LanguageServer):
    """TRLC Language Server.

    Create an instance, then call :meth:`start_io` (or another transport
    method) from :mod:`trlc_lsp.__main__` to run it.
    """

    CONFIGURATION_SECTION = "trlcServer"

    def __init__(self, *args):
        super().__init__(*args)
        self._init_shared_state()
        self._register_handlers()
        self._init_output_channel_logging()
        self.engine.start()

    def _init_shared_state(self) -> None:
        """Construct the objects handlers and the parse engine share."""
        self.store = ContextStore()
        self.fh = File_Handler()
        self.refs = ReferenceResolver()
        self.bazel_cache = BazelManagerCache()
        self.engine = ParseEngine(self)

    def _register_handlers(self) -> None:
        """Register all LSP feature and command handlers. The tuple is typed
        as HandlerModule so a type checker verifies every entry exposes
        register(server) with the right signature, not just at runtime."""
        handler_modules: tuple[HandlerModule, ...] = (
            lifecycle,
            code_actions,
            completion,
            navigation,
            hover,
            rename,
            semantic_tokens,
            status,
        )
        for mod in handler_modules:
            mod.register(self)

    def _init_output_channel_logging(self) -> None:
        """Forward LOGGER records (at/above the configured level) into the
        LSP output channel, in addition to the rotating pygls-<pid>.log file
        configure_logging() already set up in __main__. Never touches
        window_show_message - the hand-placed degradation-pattern popups
        stay exactly as they are."""
        attach_output_channel_handler(
            self, level_from_name(self.config.log_level)
        )

    @property
    def config(self) -> ServerConfig:
        """Default (workspace-wide) configuration."""
        return self.store.default_config

    def apply_config(self, config, folder_uri: Optional[str] = None) -> None:
        """Apply a configuration update received from the client.

        Args:
            config: Raw dict or pre-built ServerConfig instance.
            folder_uri: If provided, store config for this folder;
                       else replace the default global config.
        """
        if isinstance(config, dict):
            config = ServerConfig.from_dict(config)
        if folder_uri:
            self.store.set_folder_config(folder_uri, config)
        else:
            self.store.set_default_config(config)

    def get_config_for_uri(self, uri: str) -> ServerConfig:
        """Get the config applicable to a document URI.

        Returns the folder-specific config if one exists, else the default.
        """
        folder_uri = folder_for_uri(uri, self.workspace.folders)
        return self.store.get_config_for_uri(folder_uri)

    def uri_to_scope_id(self, uri: str) -> Optional[str]:
        """Resolve a document URI to its scope identifier.

        Scope boundary depends on parseMode: WORKSPACE/REPO give one scope
        per workspace folder, DIRECTORY one per directory, BAZEL one per
        Bazel target — see each strategy's :meth:`~scope_strategies.
        ScopeStrategy.scope_id` for the mode-specific math; this method
        only resolves the owning workspace folder, which is mode-independent.
        """
        folder_uri = folder_for_uri(uri, self.workspace.folders)
        if not folder_uri:
            return None

        config = self.get_config_for_uri(uri)

        # Lazy callable, not self._resolve_bazel_target passed directly:
        # uri_to_scope_id runs unbound against lightweight test doubles
        # that may not implement it, and it must only ever be looked up
        # for an actual BAZEL-mode call (only BazelScopeStrategy.scope_id
        # invokes it).
        strategy = get_scope_strategy(config.parse_mode)
        return strategy.scope_id(
            uri, folder_uri, config, lambda: self._resolve_bazel_target(uri)
        )

    def _resolve_bazel_target(self, uri: str) -> Optional[str]:
        """Resolve a document URI to its owning Bazel target label.

        Looks the file up in the (cached) BazelTargetManager's file→target
        map. Returns the most specific target label, or ``None`` when
        resolution succeeded but the file simply isn't part of any TRLC
        target. Lets :class:`~server.bazel.BazelUnavailable`
        propagate when Bazel itself can't be used — the sole caller,
        :meth:`~server.scope_strategies.BazelScopeStrategy.scope_id`,
        turns that into "no scope" rather than a directory-scoped fallback.
        """
        file_path = path_from_uri(uri)
        config = self.get_config_for_uri(uri)
        # Folder, not the file itself: BazelScopeStrategy.discover()
        # derives its own folder_path the same way (dirname of the
        # triggering file — see BazelScopeStrategy.scope_root), so both
        # resolution paths land on the same resolve_bazel_manager cache
        # entry/popup for a given location instead of silently diverging.
        folder_path = os.path.dirname(file_path)
        with report_bazel_query_progress(self):
            manager = resolve_bazel_manager(self, folder_path, config)
            labels = manager.targets_for_file(file_path)
        if not labels:
            return None
        # Deterministic tie-break: shortest label (shortest package path),
        # then lexical, so a file shared by multiple targets always maps to
        # the same scope.
        return min(labels, key=lambda label: (len(label), label))

    def get_context_for_uri(self, uri: str) -> Optional[ParseContext]:
        """Get the parse context for a document URI.

        Returns the context whose scope contains this URI, or None if
        the URI is not in any active scope.
        """
        return self.store.get_context_for_uri(uri)

    # ------------------------------------------------------------------
    # Thin delegation wrappers (kept for backward compatibility with any
    # external code or tests that call these directly).
    #
    # Deliberately NO ``validate()`` wrapper here: ParseEngine.validate()
    # (and the _scope_signatures/_dirty_uris state it touches) is
    # single-writer, worker-thread-only — see the Concurrency section of
    # docs/ARCHITECTURE.md. A main-thread delegate would let external
    # callers invoke it concurrently with the worker. Trigger a parse from
    # the main thread via queue_event() instead.
    # ------------------------------------------------------------------

    def queue_event(self, kind: str, uri=None, content=None) -> None:
        """Enqueue a parse event; delegates to the engine."""
        self.engine.queue_event(kind, uri, content)

    def stop_engine(self) -> None:
        """Stop the parse worker thread and wait for it to exit.

        Called from the LSP ``shutdown`` handler so the worker never gets
        killed mid-cycle when the process tears down.
        """
        self.engine.stop()
