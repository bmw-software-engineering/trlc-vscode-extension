#!/usr/bin/env python3
#
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

import os
import urllib.parse
import uuid

from lsprotocol.types import (Diagnostic, DiagnosticSeverity, Position, Range,
                              WorkDoneProgressBegin, WorkDoneProgressEnd,
                              WorkDoneProgressReport)
from trlc.errors import Kind, Message_Handler, TRLC_Error
from trlc.trlc import Source_Manager

kind_to_severity_mapping = {
    Kind.SYS_ERROR: DiagnosticSeverity.Error,
    Kind.SYS_CHECK: DiagnosticSeverity.Information,
    Kind.SYS_WARNING: DiagnosticSeverity.Warning,
    Kind.USER_ERROR: DiagnosticSeverity.Error,
    Kind.USER_WARNING: DiagnosticSeverity.Warning
}


class Vscode_Message_Handler(Message_Handler):
    """Reimplementation of TRLC's Message_Handler to emit the diagnostics."""

    def __init__(self):
        super().__init__()
        self.diagnostics = {}

    def emit(self,
             location,
             kind,
             message,
             fatal=True,
             extrainfo=None,
             category=None):
        end_location = location.get_end_location()
        end_line = (0 if end_location.line_no is None
                    else end_location.line_no - 1)
        end_col = 1 if end_location.col_no is None else end_location.col_no
        start_line = 0 if location.line_no is None else location.line_no - 1
        start_col = 0 if location.col_no is None else location.col_no - 1
        end_range = Position(line=end_line, character=end_col)
        start_range = Position(line=start_line, character=start_col)
        msg = message + (f"\n{extrainfo}" if extrainfo is not None else "")
        url = urllib.parse.quote(location.file_name.replace('\\', '/'))
        uri = urllib.parse.urlunparse(('file', '', url, '', '', ''))
        diag = Diagnostic(range=Range(start=start_range, end=end_range),
                          message=msg,
                          severity=kind_to_severity_mapping.get(kind),
                          code=category)

        if uri in self.diagnostics:
            self.diagnostics[uri].append(diag)
        else:
            self.diagnostics[uri] = [diag]

        if fatal:
            raise TRLC_Error(location, kind, message)


class File_Handler():
    def __init__(self):
        self.files = {}

    def update_files(self, uri, content):
        self.files[uri] = content

    def delete_files(self, uri):
        del self.files[uri]


class Vscode_Source_Manager(Source_Manager):
    """Reimplementation of TRLC's Source_Manager to read from vscode's
    workspace."""

    def __init__(self, mh, fh, ls):
        super().__init__(mh          = mh,
                         verify_mode = True)
        self.fh       = fh
        self.progress = ls.progress
        self.ptoken   = None

    def callback_parse_begin(self):
        self.ptoken = str(uuid.uuid4())
        self.progress.create(self.ptoken)
        self.progress.begin(self.ptoken,
                            WorkDoneProgressBegin(title="Parsing",
                                                  percentage=0,
                                                  cancellable=False))

    def callback_parse_progress(self, progress):
        assert isinstance(progress, int)
        self.progress.report(
            self.ptoken,
            WorkDoneProgressReport(message="Parsing (%i%%)" % progress,
                                   percentage=progress))

    def callback_parse_end(self):
        self.progress.end(self.ptoken,
                          WorkDoneProgressEnd(message="Finished"))

    def register_workspace(self, dir_name):
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
                if os.path.splitext(file_name)[1] in (".rsl",
                                                      ".check",
                                                      ".trlc"):
                    file_path = os.path.join(path, file_name)
                    url = urllib.parse.quote(file_path.replace('\\', '/'))
                    uri = urllib.parse.urlunparse(('file', '', url,
                                                   '', '', ''))
                    if uri in self.fh.files:
                        file_content = self.fh.files[uri]
                    else:
                        file_content = None
                    ok &= self.register_file(file_path, file_content)
        return ok
