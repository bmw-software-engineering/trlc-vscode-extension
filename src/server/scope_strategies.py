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

"""Scope-discovery strategies for the TRLC language server.

Each strategy implements the :class:`ScopeStrategy` Protocol's
``discover()`` method, which registers files with a
:class:`~server.source_manager.Vscode_Source_Manager` according to a
configured :class:`~server.server_config.ParseMode`.
"""

import contextlib
import logging
import os
import uuid
from typing import TYPE_CHECKING, Callable, Dict, Optional, Protocol

from lsprotocol.types import (
    MessageType,
    ShowMessageParams,
    WorkDoneProgressBegin,
    WorkDoneProgressEnd,
)

from .bazel import (
    BazelUnavailable,
    BazelWorkspaceNotFoundError,
    resolve_bazel_manager,
)
from .server_config import ParseMode, ServerConfig
from .source_manager import Vscode_Source_Manager
from .token_utils import decode_scope_id, encode_scope_id, path_from_uri

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .language_server import TrlcLanguageServer


@contextlib.contextmanager
def report_bazel_query_progress(ls: "TrlcLanguageServer"):
    """Show a work-done-progress indicator around a `bazel query` call.

    Same mechanism already used for parse progress (see
    :meth:`~server.source_manager.Vscode_Source_Manager.callback_parse_begin`)
    — without it, the first query against a cold Bazel server/output_base
    (up to ``bazel_query_timeout_seconds``, see :class:`ServerConfig`) looks
    like the LSP has simply stopped responding.
    """
    token = str(uuid.uuid4())
    ls.work_done_progress.create(token)
    ls.work_done_progress.begin(
        token,
        WorkDoneProgressBegin(
            title="TRLC: Querying Bazel workspace...",
            percentage=0,
            cancellable=False,
        ),
    )
    try:
        yield
    finally:
        ls.work_done_progress.end(
            token, WorkDoneProgressEnd(message="Finished")
        )


def _scope_root_from_segment(scope_id: str) -> str:
    """Shared ``scope_root()`` body for every folder-uri-or-directory-segment
    scope (WORKSPACE/DIRECTORY/REPO) — the scope_id itself already encodes
    the path discovery should run from. BAZEL is the sole exception (see
    :meth:`BazelScopeStrategy.scope_root`) since its segment is a target
    label, not a filesystem path."""
    folder_uri, segment = decode_scope_id(scope_id)
    if segment is None:
        return path_from_uri(folder_uri)
    return segment


def _norm(path: str) -> str:
    """Canonicalize a path for cross-platform comparison/keying.

    ``os.path.normcase`` on top of ``normpath`` is what makes this
    Windows-correct: one side of every comparison here is a path derived
    from a URI via :func:`~server.token_utils.path_from_uri` (pygls
    lowercases the drive letter — ``c:\\…``), the other is a raw filesystem
    path from an ``os.walk``/``os.listdir``/``WorkspaceFolder`` (drive letter
    left as-is — ``C:\\…``). ``normpath`` alone leaves that ``c:`` vs ``C:``
    mismatch intact, so without ``normcase`` every such comparison silently
    fails on Windows (open files never register, open buffers never override
    disk). No-op on POSIX, where ``normcase`` is the identity.
    """
    return os.path.normcase(os.path.normpath(path))


def _is_under(path: str, root: str) -> bool:
    """True if normalized *path* is *root* itself, or nested under it."""
    path = _norm(path)
    root = _norm(root)
    return path == root or path.startswith(root + os.sep)


def _open_buffers_by_path(ls: "TrlcLanguageServer") -> Dict[str, str]:
    """Snapshot of ``ls.fh`` keyed by real filesystem path instead of uri.

    DIRECTORY/BAZEL discovery walks the filesystem, so it needs to check
    "is this path open in the editor" by path, not by uri.
    """
    return {
        _norm(path_from_uri(uri)): content
        for uri, content in ls.fh.snapshot().items()
    }


def _read_file(file_path: str, open_by_path: Dict[str, str]) -> str:
    """Return *file_path*'s content: the open editor buffer if present,
    else disk.

    Without this, an open file's unsaved edits are invisible to
    DIRECTORY/BAZEL mode — they'd keep parsing the last-saved-to-disk
    content until the user hits Ctrl+S. WORKSPACE mode already reads from
    ``ls.fh`` directly, and REPO mode's ``register_workspace`` already
    checks it too (see ``Vscode_Source_Manager.register_workspace``); this
    closes the same gap for the two strategies that still read straight
    from disk.
    """
    content = open_by_path.get(_norm(file_path))
    if content is not None:
        return content
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


class ScopeStrategy(Protocol):
    """Strategy for discovering files to parse within a folder, and for
    computing the scope_id a given document URI belongs to under this mode.

    Keeping both operations on the same strategy means a new ParseMode is a
    single new class registered once in :data:`_STRATEGIES` — not a set of
    parallel branches scattered across :mod:`language_server` and this
    module that have to be kept in sync by hand.
    """

    # One param per discovery input; scope_id is only used by
    # BazelScopeStrategy, but the Protocol must stay uniform.
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def discover(
        self,
        vsm: Vscode_Source_Manager,
        folder_path: str,
        config: ServerConfig,
        ls: "TrlcLanguageServer",
        scope_id: str = "",
    ) -> Optional[str]:
        """Register files with vsm based on the discovery strategy.

        Args:
            vsm: The source manager to register files into.
            folder_path: The workspace folder path.
            config: Current server configuration.
            ls: The language server instance.
            scope_id: The scope being discovered for (see
                :func:`~server.token_utils.encode_scope_id`). Only
                :class:`BazelScopeStrategy` uses this (to recover its
                target label); other strategies ignore it.

        Returns:
            A human-readable reason if this discovery run fell back to a
            different strategy than configured, else ``None``. Only ever
            non-``None`` for :class:`BazelScopeStrategy`, and even there,
            only for its sole remaining quiet fallback — no
            ``WORKSPACE``/``MODULE.bazel`` found at all (a plain,
            non-Bazel folder in a mixed workspace). Every other BAZEL
            "found nothing usable" condition (no targets, file owned by no
            target, target with no parse-relevant files) instead raises
            :class:`~server.bazel.BazelUnavailable` after one popup — see
            :meth:`BazelScopeStrategy._warn_and_skip` — so the caller skips
            this scope's cycle entirely rather than silently switching
            parse mode. Published onto
            :class:`~server.parse_context.ParseResult` so the client's status
            bar can show when the configured mode isn't actually what ran.
        """
        ...  # pylint: disable=unnecessary-ellipsis  # Protocol stub body

    def scope_id(
        self,
        uri: str,
        folder_uri: str,
        config: ServerConfig,
        resolve_bazel_target: Callable[[], Optional[str]],
    ) -> Optional[str]:
        """Return the scope_id that *uri* belongs to under this mode.

        Args:
            uri: The document URI being resolved.
            folder_uri: The workspace folder URI that owns *uri* (already
                resolved — this method only computes the mode-specific
                segment).
            config: Current server configuration.
            resolve_bazel_target: Zero-arg callable that resolves *uri* to
                its owning Bazel target label (or ``None``); only ever
                invoked by :class:`BazelScopeStrategy`, so other
                strategies' test doubles need not implement it.
        """
        ...  # pylint: disable=unnecessary-ellipsis  # Protocol stub body

    def scope_root(self, scope_id: str, sample_uri: str) -> str:
        """Return the directory/folder/target path discovery should run
        from for a scope, given its *scope_id* and one *sample_uri* open in
        it.

        Every mode but BAZEL derives this purely from *scope_id*'s own
        segment (see :func:`~server.token_utils.decode_scope_id`); BAZEL's
        segment is a target label rather than a filesystem path, so it
        instead needs *sample_uri*'s directory (see
        :meth:`BazelScopeStrategy.scope_root`). Both params are threaded
        through the Protocol uniformly since the caller
        (:meth:`~server.parse_engine.ParseEngine._build_vsm`) doesn't know
        ahead of time which one a given mode needs.
        """
        ...  # pylint: disable=unnecessary-ellipsis  # Protocol stub body


class WorkspaceScopeStrategy:
    """Strategy: open files + transitive RSL includes (interactive editing)."""

    def discover(
        self,
        vsm: Vscode_Source_Manager,
        folder_path: str,
        _config: ServerConfig,
        ls: "TrlcLanguageServer",
        _scope_id: str = "",
    ) -> Optional[str]:
        """Register the workspace folder's include path and the currently
        open files that belong to it.

        ``ls.fh`` is a single global open-files map shared by every
        workspace folder's scope, not pre-filtered per folder — so this
        must skip open files that live under a *different* folder, or a
        multi-root workspace would leak folder B's open files into folder
        A's symbol table on every one of folder A's parse cycles.
        """
        vsm.register_include(folder_path)
        for uri, content in ls.fh.snapshot().items():
            # register_file() asserts os.path.isfile(file_name) — it wants
            # a real filesystem path, not a file:// URI.
            file_path = path_from_uri(uri)
            if not _is_under(file_path, folder_path):
                continue
            vsm.register_file(file_path, content)

    def scope_id(
        self,
        _uri: str,
        folder_uri: str,
        _config: ServerConfig,
        _resolve_bazel_target: Callable[[], Optional[str]],
    ) -> Optional[str]:
        """One scope per workspace folder."""
        return encode_scope_id(folder_uri)

    def scope_root(self, scope_id: str, _sample_uri: str) -> str:
        """Folder path encoded in *scope_id* (see :meth:`scope_id`)."""
        return _scope_root_from_segment(scope_id)


class DirectoryScopeStrategy:
    """Strategy: only the directory containing the open file, non-recursive."""

    def discover(
        self,
        vsm: Vscode_Source_Manager,
        folder_path: str,
        _config: ServerConfig,
        ls: "TrlcLanguageServer",
        _scope_id: str = "",
    ) -> Optional[str]:
        """Register only the TRLC files found directly in folder_path (non-recursive).

        Open files use their editor-buffer content (unsaved edits included);
        everything else is read from disk — see :func:`_read_file`.
        """
        # Register include and scan only files in this directory.
        vsm.register_include(folder_path)

        if not os.path.isdir(folder_path):
            return

        open_by_path = _open_buffers_by_path(ls)

        try:
            for filename in os.listdir(folder_path):
                if not filename.endswith((".rsl", ".trlc")):
                    continue
                file_path = os.path.join(folder_path, filename)
                if not os.path.isfile(file_path):
                    continue
                try:
                    content = _read_file(file_path, open_by_path)
                    # register_file() wants the real filesystem path, not a file:// URI.
                    vsm.register_file(file_path, content)
                except OSError as e:
                    LOGGER.debug("Error registering %s: %s", file_path, e)
        except OSError as e:
            LOGGER.debug("Error listing directory %s: %s", folder_path, e)

    def scope_id(
        self,
        uri: str,
        folder_uri: str,
        _config: ServerConfig,
        _resolve_bazel_target: Callable[[], Optional[str]],
    ) -> Optional[str]:
        """One scope per directory containing an open file."""
        directory = os.path.dirname(path_from_uri(uri))
        return encode_scope_id(folder_uri, directory)

    def scope_root(self, scope_id: str, _sample_uri: str) -> str:
        """Directory path encoded in *scope_id* (see :meth:`scope_id`)."""
        return _scope_root_from_segment(scope_id)


class RepoScopeStrategy:
    """Strategy: all .rsl/.trlc files found by os.walk."""

    def discover(
        self,
        vsm: Vscode_Source_Manager,
        folder_path: str,
        _config: ServerConfig,
        _ls: "TrlcLanguageServer",
        _scope_id: str = "",
    ) -> Optional[str]:
        """Register all .rsl/.trlc files under folder_path recursively."""
        vsm.register_workspace(folder_path)

    def scope_id(
        self,
        _uri: str,
        folder_uri: str,
        _config: ServerConfig,
        _resolve_bazel_target: Callable[[], Optional[str]],
    ) -> Optional[str]:
        """One scope per workspace folder, same as WORKSPACE mode."""
        return encode_scope_id(folder_uri)

    def scope_root(self, scope_id: str, _sample_uri: str) -> str:
        """Folder path encoded in *scope_id* (see :meth:`scope_id`)."""
        return _scope_root_from_segment(scope_id)


class BazelScopeStrategy:
    """Strategy: files declared in Bazel TRLC targets (bazel query output)."""

    @staticmethod
    def _register_source(
        src_path: str,
        vsm: Vscode_Source_Manager,
        open_by_path: Dict[str, str],
    ) -> None:
        """Register *src_path* with *vsm*, preferring its open editor buffer
        over disk (see :func:`_read_file`); silently skip on I/O errors (file
        may have been deleted since the Bazel query ran)."""
        if not (src_path and os.path.isfile(src_path)):
            return
        try:
            content = _read_file(src_path, open_by_path)
            vsm.register_file(src_path, content)
        except OSError:
            pass

    @staticmethod
    def _target_label_from_scope_id(scope_id: str) -> Optional[str]:
        """Recover the Bazel target label this scope was resolved to, if any.

        A BAZEL scope_id's segment is either a target label (set by
        :meth:`scope_id` when the file resolves to an owning target) or a
        plain directory path (its own fallback for a file not owned by any
        target). Target labels are the only segment shape that starts with
        ``//`` — that's the sole distinguishing signal available here.
        """
        _folder_uri, segment = decode_scope_id(scope_id)
        if segment and segment.startswith("//"):
            return segment
        return None

    @staticmethod
    def _warn_and_skip(ls: "TrlcLanguageServer", reason: str) -> None:
        """Show exactly one warning naming *reason*, then raise
        :class:`~server.bazel.BazelUnavailable` so the caller
        (:meth:`~server.parse_engine.ParseEngine._build_vsm`) skips this
        scope's parse cycle entirely — same "no scope until fixed and
        reparsed" treatment as a genuine Bazel query/build failure,
        rather than silently re-parsing the file in a different mode the
        user didn't choose. Bazel *is* set up in this workspace for all
        three callers of this method (no targets at all, a file no target
        owns, a resolved target with no parse-relevant files) — unlike
        :class:`~server.bazel.BazelWorkspaceNotFoundError`, none of these
        are a stable, expected fact about the folder, so none of them get
        the quiet directory-mode fallback that case still gets."""
        ls.window_show_message(
            ShowMessageParams(
                type=MessageType.Warning,
                message=f"TRLC: {reason}; this file will not be parsed "
                "until the Bazel target configuration is fixed and the "
                "workspace is reparsed.",
            )
        )
        raise BazelUnavailable(reason)

    # Matches ScopeStrategy's Protocol signature.
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def discover(
        self,
        vsm: Vscode_Source_Manager,
        folder_path: str,
        config: ServerConfig,
        ls: "TrlcLanguageServer",
        scope_id: str = "",
    ) -> Optional[str]:
        """Register files from *scope_id*'s Bazel target.

        Lets a genuine :class:`~server.bazel.BazelUnavailable` (the query
        itself failed) propagate uncaught — so the caller
        (:meth:`~server.parse_engine.ParseEngine._build_vsm`) can skip this
        scope's parse cycle entirely rather than register an empty scope;
        :func:`~server.bazel.resolve_bazel_manager` already showed the one
        popup for that failure. Degrades to directory-mode discovery
        (quietly, no popup) only when this folder isn't a Bazel workspace
        at all (:class:`~server.bazel.BazelWorkspaceNotFoundError`) — a
        workspace can legitimately mix Bazel and non-Bazel folders, so
        that's a stable, expected fact about the folder rather than a
        failure. Every other "Bazel worked but found nothing usable"
        condition (no targets at all, this file owned by no target, a
        resolved target with no parse-relevant files) instead raises via
        :meth:`_warn_and_skip` — one popup, then the same "skip this
        cycle" treatment as a genuine query failure, since each of those
        means something in the Bazel/BUILD setup needs fixing rather than
        a legitimately Bazel-free file. Returns the fallback reason for
        the sole quiet case, else ``None``.
        """
        # Progress spans this whole block since either the manager build
        # (kind() query) or files_for_target (deps() query) below can
        # be the slow one, depending on what's already cached.
        with report_bazel_query_progress(ls):
            try:
                manager = resolve_bazel_manager(ls, folder_path, config)
            except BazelWorkspaceNotFoundError:
                DirectoryScopeStrategy().discover(
                    vsm, folder_path, config, ls, scope_id
                )
                return (
                    "Not a Bazel workspace; falling back to "
                    "directory-mode parsing"
                )

            targets = manager.build_targets()
            if not targets:
                self._warn_and_skip(ls, "No TRLC targets found in Bazel query")

            target_label = self._target_label_from_scope_id(scope_id)
            if not target_label:
                # This document isn't owned by any Bazel target (scope_id()'s
                # own directory-path fallback).
                self._warn_and_skip(
                    ls,
                    "File is not part of any Bazel TRLC target; add it to "
                    "a target's srcs (directly or transitively), or switch "
                    "parseMode for this folder",
                )

            # The target's own transitive closure (srcs + specs + deps,
            # including cross-repo labels — see
            # BazelTargetManager.files_for_target) — not every TRLC
            # target's own direct srcs across the whole workspace, which is
            # what silently pulled in unrelated targets and left genuine
            # deps unregistered. A deps() query failure here is also a
            # BazelUnavailable — propagates uncaught, same as above.
            files = manager.files_for_target(target_label)
            if not files:
                self._warn_and_skip(
                    ls, f"No files found for Bazel target {target_label}"
                )

        open_by_path = _open_buffers_by_path(ls)
        for src_path in files:
            self._register_source(src_path, vsm, open_by_path)
        return None

    def scope_id(
        self,
        uri: str,
        folder_uri: str,
        _config: ServerConfig,
        resolve_bazel_target: Callable[[], Optional[str]],
    ) -> Optional[str]:
        """One scope per owning Bazel target; degrades to a directory-scoped
        id both when *uri* just isn't owned by any target, and when this
        folder isn't a Bazel workspace at all
        (:class:`~server.bazel.BazelWorkspaceNotFoundError` — a mixed
        workspace can have plain, non-Bazel folders alongside Bazel ones).
        Falls back to no scope at all (``None``) only when Bazel itself is
        genuinely unavailable (workspace found, but broken) — the caller
        (did_open) already treats a ``None`` scope_id as "not part of any
        active scope", same as a uri outside every workspace folder, so the
        file gets no context until Bazel is fixed and reparsed."""
        try:
            target_label = resolve_bazel_target()
        except BazelWorkspaceNotFoundError:
            target_label = None
        except BazelUnavailable:
            return None
        if target_label:
            return encode_scope_id(folder_uri, target_label)
        directory = os.path.dirname(path_from_uri(uri))
        return encode_scope_id(folder_uri, directory)

    def scope_root(self, _scope_id: str, sample_uri: str) -> str:
        """*sample_uri*'s directory, not *scope_id*'s own segment — a BAZEL
        scope_id's segment is a Bazel target label (see :meth:`scope_id`),
        not a filesystem path. ``discover()`` needs a real directory:
        ``BazelClient.find_bazel_workspace`` walks up from it to locate the
        workspace root (re-querying every target itself rather than
        trusting the scope's own label), and the directory-mode fallback
        path lists it directly if the query fails. The directory of the
        file that triggered this scope's discovery satisfies both."""
        return os.path.dirname(path_from_uri(sample_uri))


_STRATEGIES: Dict[ParseMode, ScopeStrategy] = {
    ParseMode.WORKSPACE: WorkspaceScopeStrategy(),
    ParseMode.DIRECTORY: DirectoryScopeStrategy(),
    ParseMode.REPO: RepoScopeStrategy(),
    ParseMode.BAZEL: BazelScopeStrategy(),
}


def get_scope_strategy(mode: ParseMode) -> ScopeStrategy:
    """Factory: return the appropriate scope strategy for a parse mode."""
    return _STRATEGIES.get(mode, RepoScopeStrategy())
