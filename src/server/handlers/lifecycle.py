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
"""Lifecycle handlers: workspace events, config, document open/change/close."""

import asyncio
import dataclasses
import logging
import os
from typing import Callable, Optional, Set

from lsprotocol.types import (
    SHUTDOWN,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_CLOSE,
    TEXT_DOCUMENT_DID_OPEN,
    WORKSPACE_DID_CHANGE_CONFIGURATION,
    WORKSPACE_DID_CHANGE_WATCHED_FILES,
    WORKSPACE_DID_CHANGE_WORKSPACE_FOLDERS,
    ConfigurationItem,
    ConfigurationParams,
    DidChangeConfigurationParams,
    DidChangeTextDocumentParams,
    DidChangeWatchedFilesParams,
    DidChangeWorkspaceFoldersParams,
    DidCloseTextDocumentParams,
    DidOpenTextDocumentParams,
    LogMessageParams,
    MessageType,
    ShowMessageParams,
)

from ..logging_config import level_from_name, set_log_level
from ..message_handler import Vscode_Message_Handler
from ..parse_context import ParseContext, ParseResult
from ..server_config import ParseMode, ServerConfig
from ..server_protocol import ServerProtocol
from ..source_manager import Vscode_Source_Manager
from ..token_utils import folder_for_uri, path_from_uri

LOGGER = logging.getLogger(__name__)

#: Bazel BUILD-graph files: changing one of these can change which targets/
#: deps exist, so it's worth a full `bazel query` re-run. A plain .rsl/.trlc
#: content change never does — see on_watched_files_change.
_BAZEL_GRAPH_BASENAMES = frozenset(
    {
        "BUILD",
        "BUILD.bazel",
        "WORKSPACE",
        "WORKSPACE.bazel",
        "MODULE.bazel",
        "REPO.bazel",
    }
)


def _is_bazel_graph_file(uri: str) -> bool:
    name = os.path.basename(path_from_uri(uri))
    return name in _BAZEL_GRAPH_BASENAMES or name.endswith(".bzl")


# One param per constructor input, plus the two injectable factories —
# defaults preserve the exact prior behavior; tests (or a future caller)
# can substitute lightweight doubles instead of the real VSM/message
# handler without needing a live parse.
# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def _context_factory(
    ls,
    scope_id: str,
    uri: str,
    message_handler_factory: Callable[[], Vscode_Message_Handler] = (
        Vscode_Message_Handler
    ),
    vsm_factory: Callable[..., Vscode_Source_Manager] = Vscode_Source_Manager,
) -> ParseContext:
    """Build a fresh :class:`ParseContext` for *scope_id*, ready to pass to
    :meth:`ContextStore.add_file`. Shared by ``did_open`` and
    :func:`_rebind_open_files_to_current_scopes` so both build a context
    identically."""
    vmh = message_handler_factory()
    vsm = vsm_factory(vmh, ls.fh, ls)
    return ParseContext(
        scope_id=scope_id,
        result=ParseResult(vsm=vsm),
        parse_mode=ls.get_config_for_uri(uri).parse_mode,
    )


def _rebind_open_files_to_current_scopes(ls) -> None:
    """Re-derive every open file's scope_id under the current config and
    move it to the (possibly new) matching context.

    A uri's scope_id is normally fixed once, at ``did_open`` time (see
    :meth:`ContextStore.add_file`). If the config changes afterwards in a
    way that changes scope_id's shape for a folder (e.g. BAZEL mode's
    target-resolution query starts/stops matching, or the parse mode itself
    changes), already-open files stay bound to their stale scope_id and
    never pick up the new one without a full reload. Called from
    ``on_config_change``/``on_workspace_folders_change`` before the
    resulting reparse.
    """
    for uri, content in ls.fh.snapshot().items():
        old_scope_id = ls.store.scope_id_for_uri(uri)
        new_scope_id = ls.uri_to_scope_id(uri)
        if new_scope_id == old_scope_id:
            continue
        if old_scope_id is not None:
            ls.store.remove_file(uri)
        if new_scope_id:
            ls.store.add_file(
                new_scope_id,
                uri,
                content,
                lambda scope_id=new_scope_id, uri=uri: _context_factory(
                    ls, scope_id, uri
                ),
            )


def _warn_invalid_patterns(ls, config: ServerConfig) -> None:
    """Show one warning naming any ``excludePatterns`` entries that failed to
    compile as regexes.

    Without this, an invalid pattern is silently dropped (see
    :class:`~server.source_manager.Vscode_Source_Manager`'s own
    defense-in-depth re-validation) and the user has no way to notice their
    exclusion isn't taking effect.
    """
    if not config.invalid_patterns:
        return
    noun = "pattern" if len(config.invalid_patterns) == 1 else "patterns"
    ls.window_show_message(
        ShowMessageParams(
            type=MessageType.Warning,
            message=f"TRLC: Ignoring invalid excludePatterns {noun}: "
            + ", ".join(config.invalid_patterns),
        )
    )


def _warn_invalid_rule_classes(ls, config: ServerConfig) -> None:
    """Show one warning naming any ``bazel.ruleClasses`` entries that
    aren't valid Starlark identifiers.

    Mirrors :func:`_warn_invalid_patterns`: :meth:`ServerConfig.from_dict`
    already drops these (they'd otherwise be interpolated unescaped into
    a `kind()` query — see ``_VALID_RULE_CLASS_RE``), but silently doing so
    would leave the user with no way to notice a typo'd rule class is
    simply never matching anything.
    """
    if not config.invalid_rule_classes:
        return
    noun = (
        "rule class"
        if len(config.invalid_rule_classes) == 1
        else "rule classes"
    )
    ls.window_show_message(
        ShowMessageParams(
            type=MessageType.Warning,
            message=f"TRLC: Ignoring invalid bazel.ruleClasses {noun}: "
            + ", ".join(config.invalid_rule_classes),
        )
    )


def _log_config_applied(
    ls, config: ServerConfig, folder_uri: Optional[str]
) -> None:
    """Client-visible (Output channel) confirmation of the config that just
    took effect for *folder_uri* (or the global default).

    Otherwise silent: the only client-visible sign that which
    parseMode/verify/scope/bazel.* settings actually took effect for a given
    folder is this line, or turning on trlcServer.trace.server: verbose and
    reading the raw workspace/configuration exchange by hand. bazel.* fields
    are included unconditionally (not just in BAZEL mode) so switching into
    BAZEL mode later doesn't require re-triggering a config change just to
    see whether e.g. useSharedServer actually took effect.
    """
    ls.window_log_message(
        LogMessageParams(
            type=MessageType.Log,
            message=(
                f"TRLC: Config applied for "
                f"{folder_uri or '(global)'}: "
                f"parseMode={config.parse_mode.value}, "
                f"verify={config.verify_mode}, "
                f"bazel.useSharedServer={config.bazel_use_shared_server}, "
                f"bazel.queryTimeoutSeconds={config.bazel_query_timeout_seconds}"
            ),
        )
    )


#: Delay before a single detached retry of an empty config fetch (see
#: _schedule_config_refetch). Covers a startup race where the client answers
#: a workspace/configuration request before VS Code has resolved effective
#: settings for the given resource — indistinguishable, at fetch time, from
#: "the user genuinely has no override here". Scope resolution itself
#: already re-reads config fresh on every use (get_config_for_uri), so the
#: only thing an empty response can strand is *this one folder never
#: getting its override applied* — not a snapshot anything else is stuck
#: reading. The retry therefore runs detached (fire-and-forget), off
#: did_open's critical path, instead of blocking every file-open with a
#: guessed delay on the common (genuinely-empty) case.
_CONFIG_REFETCH_DELAY_SECONDS = 2.0

#: Strong references to in-flight _schedule_config_refetch tasks. Nothing
#: else holds one — a bare asyncio.create_task() result with no referent is
#: eligible for GC mid-flight, which would silently drop the retry.
_pending_config_refetches: Set[asyncio.Task] = set()


async def _fetch_folder_config(ls, scope_uri: str):
    """Fetch *scope_uri*'s ``workspace/configuration`` item once. Returns
    the config item, or ``None`` if the response was empty."""
    config = await ls.workspace_configuration_async(
        ConfigurationParams(
            items=[
                ConfigurationItem(
                    scope_uri=scope_uri,
                    section=ls.CONFIGURATION_SECTION,
                )
            ]
        )
    )
    return config[0] if config and config[0] else None


def _schedule_config_refetch(
    ls, folder_uri: Optional[str], scope_uri: str
) -> None:
    """Schedule one detached, delayed retry of *scope_uri*'s config fetch,
    for when did_open's own immediate attempt came back empty.

    Runs off did_open's critical path (never adds latency to a file open,
    including the common case where there's genuinely no override). If this
    retry is also empty, no further attempt is scheduled — the next
    did_open for this folder (has_folder_config stays False, so it fetches
    again) or a live config-change notification gets the next shot.
    """
    task = asyncio.ensure_future(
        _refetch_folder_config_once(ls, folder_uri, scope_uri)
    )
    _pending_config_refetches.add(task)
    task.add_done_callback(_pending_config_refetches.discard)


async def _refetch_folder_config_once(
    ls, folder_uri: Optional[str], scope_uri: str
) -> None:
    """The retry body scheduled by :func:`_schedule_config_refetch`."""
    await asyncio.sleep(_CONFIG_REFETCH_DELAY_SECONDS)
    if folder_uri and ls.store.has_folder_config(folder_uri):
        return  # a later did_open or config-change already applied one
    config_item = await _fetch_folder_config(ls, scope_uri)
    if config_item:
        _apply_config_and_warn(ls, config_item, folder_uri)


def _apply_config_and_warn(
    ls, raw_config, folder_uri: Optional[str] = None
) -> None:
    """Build a :class:`ServerConfig` from *raw_config* (a raw dict from
    ``workspace/configuration``), apply it, and surface the result to the
    user: a warning for any invalid ``excludePatterns``, then a log-level
    re-apply and an Output-channel confirmation of what took effect.
    """
    config = (
        ServerConfig.from_dict(raw_config)
        if isinstance(raw_config, dict)
        else raw_config
    )
    _warn_invalid_patterns(ls, config)
    _warn_invalid_rule_classes(ls, config)
    ls.apply_config(config, folder_uri)
    # trlcServer.logLevel is the one setting shared by both the Python
    # LOGGER stream and the TS client's own logger; this is the single
    # choke point every config update (global, per-folder, did_open's
    # fetch) already flows through, so it's the natural place to re-apply
    # it. Idempotent and cheap - safe to call on every update, last write
    # wins.
    set_log_level(level_from_name(config.log_level))
    _log_config_applied(ls, config, folder_uri)


def on_shutdown(ls, *_args):
    """Shutdown request: stop the parse worker thread cleanly before
    the process exits, instead of leaving it to be killed mid-cycle."""
    ls.stop_engine()


async def on_workspace_folders_change(ls, _: DidChangeWorkspaceFoldersParams):
    """Workspace folders did change notification."""
    # Off the event loop: re-deriving scope_id may run a `bazel query`
    # subprocess (up to 60s) in BAZEL mode — see did_open's own use of
    # asyncio.to_thread for the same reason.
    await asyncio.to_thread(_rebind_open_files_to_current_scopes, ls)
    ls.queue_event("reparse")


def on_watched_files_change(ls, params: DidChangeWatchedFilesParams):
    """A tracked file changed on disk outside the editor (git checkout,
    another tool, a save...). The client's file watcher (see
    extension.ts's `synchronize.fileEvents`) reports these regardless of
    whether the file is open.

    A BUILD/BUILD.bazel/*.bzl/WORKSPACE/MODULE.bazel change can alter the
    Bazel target graph itself, so it gets a full ``"reparse"`` (clears
    ``bazel_cache``, forcing the next parse to re-run `bazel query`).
    A plain .rsl/.trlc content change never changes the graph — it only
    needs to wake the parse worker (``"reparse_files"``); discover()'s
    fresh re-walk and _validate_scope's own input-signature comparison
    already pick up the new on-disk state on their own.
    """
    if any(_is_bazel_graph_file(change.uri) for change in params.changes):
        ls.queue_event("reparse")
    else:
        ls.queue_event("reparse_files")


async def on_config_change(ls, _: DidChangeConfigurationParams):
    """Configuration did change notification — refresh all folder configs."""
    try:
        items = [
            ConfigurationItem(
                scope_uri="",
                section=ls.CONFIGURATION_SECTION,
            )
        ]
        for folder_uri in ls.workspace.folders.keys():
            items.append(
                ConfigurationItem(
                    scope_uri=folder_uri,
                    section=ls.CONFIGURATION_SECTION,
                )
            )

        configs = await ls.workspace_configuration_async(
            ConfigurationParams(items=items)
        )
        # A per-item response of None/{} is valid LSP (client has no
        # override for this section) and means "keep current config",
        # not "apply an empty/null override".
        if configs and configs[0]:
            _apply_config_and_warn(ls, configs[0])

        for i, folder_uri in enumerate(ls.workspace.folders.keys(), 1):
            if i < len(configs) and configs[i]:
                _apply_config_and_warn(ls, configs[i], folder_uri)

        await asyncio.to_thread(_rebind_open_files_to_current_scopes, ls)
        ls.queue_event("reparse")
    except Exception:  # pylint: disable=broad-exception-caught  # pygls workspace_configuration may raise any exception
        LOGGER.error(
            "TRLC: Unable to get workspace configuration; "
            "skipping reparse to avoid using stale config",
            exc_info=True,
        )


def did_change(ls, params: DidChangeTextDocumentParams):
    """Text document did change notification."""
    uri = params.text_document.uri
    document = ls.workspace.get_text_document(uri)
    content = document.source
    ls.queue_event("change", uri, content)


def did_close(ls, params: DidCloseTextDocumentParams):
    """Text document did close notification."""
    uri = params.text_document.uri
    ls.store.remove_file(uri)
    ls.queue_event("delete", uri)


def _parse_all_config(config: ServerConfig) -> ServerConfig:
    """Return the config *cmd_parse_all* should apply for one scope.

    WORKSPACE/DIRECTORY mode don't already parse "everything" reachable, so
    "parse all" means switching them to REPO for this scope. REPO mode
    already does; BAZEL mode has its own (target-scoped, `bazel query`
    driven) notion of "everything" — forcing REPO there would silently and
    permanently drop the user out of Bazel-target scoping onto a raw
    recursive filesystem walk, so BAZEL mode is left untouched (its cache is
    cleared by ``ls.queue_event("reparse")`` instead, forcing a fresh
    `bazel query` on next parse).
    """
    if config.parse_mode in (ParseMode.WORKSPACE, ParseMode.DIRECTORY):
        return dataclasses.replace(config, parse_mode=ParseMode.REPO)
    return config


def cmd_parse_all(ls, *_args):
    """extension.parseAll command: force a full reparse of every folder.

    WORKSPACE/DIRECTORY mode switch to REPO for this; REPO and BAZEL mode
    already parse everything reachable in their own way and are left as-is
    (see :func:`_parse_all_config`) — only the reparse itself (which also
    clears the Bazel cache) is forced.
    """
    ls.apply_config(_parse_all_config(ls.config))
    for folder_uri, folder_cfg in ls.store.folder_config_items():
        ls.apply_config(_parse_all_config(folder_cfg), folder_uri)
    ls.queue_event("reparse")


# AST-available and lex-fallback paths each need their own set of resolved
# values (folder_uri/scope_id resolution, config fetch, context creation).
# pylint: disable-next=too-many-locals
async def did_open(ls, params: DidOpenTextDocumentParams):
    """Text document did open notification."""
    uri = params.text_document.uri
    folder_uri = folder_for_uri(uri, ls.workspace.folders)

    if not (folder_uri and ls.store.has_folder_config(folder_uri)):
        try:
            scope_uri = folder_uri if folder_uri else ""
            config_item = await _fetch_folder_config(ls, scope_uri)
            # A None/{} response is valid LSP (no override for this
            # section) and means "use defaults", not "apply null" — but
            # it's indistinguishable from a startup race between this
            # request and VS Code resolving effective settings for the
            # resource, so a detached retry is scheduled either way (see
            # _schedule_config_refetch's own docstring for why that runs
            # off this call's critical path instead of blocking it).
            if config_item:
                _apply_config_and_warn(ls, config_item, folder_uri)
            else:
                # The only client-visible sign that the server is running
                # on hardcoded defaults (not whatever the user configured)
                # instead of an applied override is this line — Warning
                # (not Log) so it's visible without needing
                # trlcServer.trace.server: verbose.
                ls.window_log_message(
                    LogMessageParams(
                        type=MessageType.Warning,
                        message=(
                            f"TRLC: No config override for "
                            f"{folder_uri or '(global)'}; using "
                            f"hardcoded defaults for now: parseMode="
                            f"{ls.config.parse_mode.value}, "
                            f"verify={ls.config.verify_mode}"
                        ),
                    )
                )
                _schedule_config_refetch(ls, folder_uri, scope_uri)
        except Exception:  # pylint: disable=broad-exception-caught  # pygls workspace_configuration may raise any exception
            LOGGER.error(
                "TRLC: Unable to get workspace configuration",
                exc_info=True,
            )
            ls.window_show_message(
                ShowMessageParams(
                    type=MessageType.Warning,
                    message="TRLC: Could not fetch workspace configuration; "
                    "using defaults.",
                )
            )

    # Create context for this file's scope if missing, and record the
    # scope_id this uri was opened under so did_close can look it up
    # later without recomputing (parseMode may change in between).
    # Resolved off the event loop: in BAZEL mode the first resolution
    # for a workspace runs a `bazel query` subprocess (up to 60 s), and
    # blocking here would freeze every other LSP request meanwhile.
    scope_id = await asyncio.to_thread(ls.uri_to_scope_id, uri)
    document = ls.workspace.get_text_document(uri)

    if scope_id:
        ls.store.add_file(
            scope_id,
            uri,
            document.source,
            lambda: _context_factory(ls, scope_id, uri),
        )

    content = document.source
    ls.queue_event("change", uri, content)


def register(server: "ServerProtocol") -> None:
    """Register all lifecycle handlers on *server*.

    One straight-line registration call per LSP event — handlers themselves
    are plain module-level functions (not nested closures) so each can be
    imported and unit-tested directly.
    """
    server.feature(SHUTDOWN)(on_shutdown)
    server.feature(WORKSPACE_DID_CHANGE_WORKSPACE_FOLDERS)(
        on_workspace_folders_change
    )
    server.feature(WORKSPACE_DID_CHANGE_WATCHED_FILES)(on_watched_files_change)
    server.feature(WORKSPACE_DID_CHANGE_CONFIGURATION)(on_config_change)
    server.feature(TEXT_DOCUMENT_DID_CHANGE)(did_change)
    server.feature(TEXT_DOCUMENT_DID_CLOSE)(did_close)
    server.command("extension.parseAll")(cmd_parse_all)
    server.feature(TEXT_DOCUMENT_DID_OPEN)(did_open)
