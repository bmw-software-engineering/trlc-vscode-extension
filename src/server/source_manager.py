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

"""VS Code-aware TRLC source manager.

:class:`Vscode_Source_Manager` subclasses TRLC's
:class:`trlc.trlc.Source_Manager` to:

* read file content from the editor's in-memory store
  (:class:`~server.file_handler.File_Handler`) instead of disk when the
  file is currently open, so unsaved edits are reflected immediately;
* report parse progress via VS Code's work-done progress protocol;
* walk the workspace directory while honouring ``excludePatterns``
  (defaults to ``bazel-*``);
* restrict automatic transitive inclusion (``register_include``) to
  ``.rsl`` schema files — ``.trlc`` data files only participate if the
  editor has them explicitly open.
"""

import logging
import os
import re
import uuid

from lsprotocol.types import (
    WorkDoneProgressBegin,
    WorkDoneProgressEnd,
    WorkDoneProgressReport,
)
from trlc.trlc import Source_Manager

from .parse_guard import normalize_fs_path
from .token_utils import uri_from_file

LOGGER = logging.getLogger(__name__)


class Vscode_Source_Manager(Source_Manager):
    """Reimplementation of TRLC's Source_Manager to read from VS Code's
    workspace."""

    def __init__(
        self,
        mh,
        fh,
        ls,
        verify_mode=True,
        exclude_patterns=None,
    ):
        super().__init__(mh=mh, verify_mode=verify_mode)
        self.fh = fh
        self.progress = ls.work_done_progress
        self.ptoken = None
        # Records every file scheduled for parsing, so the parse engine can
        # cheaply detect an unchanged input set and skip a redundant reparse.
        # abspath -> in-editor content (str) or None (parse from disk).
        self._registered_inputs: dict = {}
        # Mirror TRLC CLI default: exclude bazel-* directories.
        # Patterns come from trlcServer.excludePatterns, i.e. the user's own
        # workspace settings (not external/untrusted input), so a
        # pathological regex here is a self-inflicted footgun, not an attack
        # surface — compile errors are caught and logged (below), not
        # guarded against ReDoS.
        self.exclude_patterns = [re.compile(r"^bazel-.*$")]
        if exclude_patterns:
            for pattern in exclude_patterns:
                try:
                    self.exclude_patterns.append(re.compile(pattern))
                except re.error as e:
                    LOGGER.warning(
                        "Invalid exclude pattern '%s': %s", pattern, e
                    )

    def callback_parse_begin(self):
        self.ptoken = str(uuid.uuid4())
        self.progress.create(self.ptoken)
        self.progress.begin(
            self.ptoken,
            WorkDoneProgressBegin(
                title="Parsing", percentage=0, cancellable=False
            ),
        )

    def callback_parse_progress(self, progress):
        if not isinstance(progress, int):
            raise TypeError(
                f"progress must be int, got {type(progress).__name__}"
            )
        self.progress.report(
            self.ptoken,
            WorkDoneProgressReport(
                message=f"Parsing ({progress}%)", percentage=progress
            ),
        )

    def callback_parse_end(self):
        self.progress.end(self.ptoken, WorkDoneProgressEnd(message="Finished"))

    def register_include(self, dir_name):
        """Only .rsl files are eligible for automatic transitive inclusion —
        matches WorkspaceScopeStrategy's documented intent (schema files
        only). Unlike upstream Source_Manager.register_include, .trlc
        (data/instance) files are never auto-included from disk; only files
        the editor has explicitly opened participate. Prevents an unrelated,
        unopened .trlc file elsewhere in the workspace from colliding with
        an open file's symbol names purely by coincidence.
        """
        for path, dirs, files in os.walk(dir_name):
            for n, dirname in reversed(list(enumerate(dirs))):
                keep = True
                for exclude_pattern in self.exclude_patterns:
                    if exclude_pattern.match(dirname):
                        keep = False
                        break
                if not keep:
                    del dirs[n]

            self.includes.update(
                {
                    # Key by normalize_fs_path, NOT os.path.abspath (which is
                    # what upstream Source_Manager.register_include uses):
                    # register_file() below registers every file under its
                    # normalize_fs_path form, and TRLC's own include dedup in
                    # register_rsl_file/register_trlc_file removes an
                    # already-registered file from self.includes via
                    # ``os.path.abspath(file_name)`` — i.e. it looks the key up
                    # using the *registered* name. On Windows a raw os.walk path
                    # keeps the drive letter's original case (``C:\…``) while the
                    # registered name has been lowercased by normalize_fs_path
                    # (``c:\…``), so an abspath key never matches, the dedup
                    # misses, and build_graph re-registers the file a second time
                    # → ``assert file_name not in self.rsl_files`` fires. Keying
                    # by the same canonical form both sides use keeps them in
                    # sync (no-op on POSIX).
                    normalize_fs_path(full_name): full_name
                    for full_name in (
                        os.path.join(path, file_name)
                        for file_name in files
                        if os.path.splitext(file_name)[1] == ".rsl"
                    )
                }
            )

    def register_workspace(self, dir_name):
        """Register workspace."""
        ok = True
        for path, dirs, files in os.walk(dir_name):
            dirs.sort()

            for n, dirname in reversed(list(enumerate(dirs))):
                keep = True
                for exclude_pattern in self.exclude_patterns:
                    if exclude_pattern.match(dirname):
                        keep = False
                        break
                if not keep:
                    del dirs[n]

            for file_name in sorted(files):
                if os.path.splitext(file_name)[1] in (".rsl", ".trlc"):
                    file_path = os.path.join(path, file_name)
                    uri = uri_from_file(file_path)
                    file_content = self.fh.snapshot().get(uri)
                    ok &= self.register_file(file_path, file_content)
        return ok

    def register_file(self, file_name, file_content=None, primary=True):
        """Record the input for change-detection, then defer to TRLC.

        Every file that gets scheduled for parsing — whether via our
        ``register_workspace`` or TRLC's own ``register_directory`` — flows
        through here, so :meth:`compute_input_signature` sees the complete
        set of parse inputs.

        Registers with TRLC under :func:`normalize_fs_path`'s canonical
        form rather than *file_name* as-is: TRLC's own ``Source_Manager``
        keys ``all_files`` (and every ``includes``/``rsl_files`` lookup) by
        that exact string with no normalization of its own, and callers
        elsewhere in this codebase (e.g. ``token_utils.resolve_document_
        tokens``, ``parse_guard.lookup_parsed_file``) look files back up via
        a path derived from a URI — which, on Windows, is not guaranteed to
        be byte-for-byte identical to the raw filesystem path a directory
        walk hands back (drive-letter casing). Registering and looking up
        under the same canonical form keeps both sides in sync.
        """
        normalized_name = normalize_fs_path(file_name)
        self._registered_inputs[normalized_name] = file_content
        return super().register_file(
            normalized_name, file_content=file_content, primary=primary
        )

    def compute_input_signature(self) -> dict:
        """Return a hashable signature of all parse inputs for this manager.

        For in-editor files the signature captures the exact content; for
        on-disk files it captures ``(mtime, size)`` (cheap, no read). Includes
        the ``.rsl`` include set, whose on-disk state also affects the parse.
        Two managers with equal signatures parse identical inputs, so the
        engine can safely reuse the previous parse result instead of
        re-running the expensive ``process()``.
        """
        signature: dict = {}
        # Directly registered files (primary + open files).
        for abspath, content in self._registered_inputs.items():
            signature[abspath] = self._file_fingerprint(abspath, content)
        # Transitive .rsl includes (parsed from disk during process()).
        for abspath in self.includes:
            if abspath not in signature:
                signature[abspath] = self._file_fingerprint(abspath, None)
        return signature

    @staticmethod
    def _file_fingerprint(abspath: str, content):
        """Fingerprint one input: content hash if open, else (mtime, size)."""
        if content is not None:
            return ("mem", hash(content))
        try:
            st = os.stat(abspath)
            return ("disk", st.st_mtime_ns, st.st_size)
        except OSError:
            return ("missing",)
