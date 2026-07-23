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

"""In-memory file store for open editor documents.

:class:`File_Handler` maintains the current content of all files that the
editor has opened.  The :class:`~server.parse_engine.ParseEngine` reads
from this store when it needs to parse a file that is open in the editor,
so that unsaved edits are reflected in diagnostics and completions.
"""

import threading


class File_Handler:
    """In-memory map of open file URIs to their current text content."""

    def __init__(self):
        self.files = {}
        self._lock = threading.Lock()

    def update_files(self, uri: str, content: str) -> None:
        """Store or replace the content for *uri*."""
        with self._lock:
            self.files[uri] = content

    def delete_files(self, uri: str) -> None:
        """Remove *uri* from the store.  No-op if *uri* is not present."""
        with self._lock:
            self.files.pop(uri, None)

    def snapshot(self) -> dict:
        """Return a snapshot copy of all files, safe to iterate without the lock."""
        with self._lock:
            return dict(self.files)
