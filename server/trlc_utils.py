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
from trlc.errors import Message_Handler, TRLC_Error, Kind
from trlc.trlc import Source_Manager
from lsprotocol.types import Diagnostic, Position, Range, DiagnosticSeverity


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
        end_line = end_location.line_no - 1
        end_col = end_location.col_no
        start_line = location.line_no - 1
        start_col = location.col_no - 1
        end_range = Position(line=end_line, character=end_col)
        start_range = Position(line=start_line, character=start_col)
        msg = (message + (f"\n{extrainfo}" if extrainfo is not None else ""))

        url = urllib.parse.quote(location.file_name.replace('\\', '/'))
        uri = urllib.parse.urlunparse(('file', '', url, '', '', ''))

        d = Diagnostic(range=Range(start=start_range, end=end_range),
                       message=msg,
                       severity=kind_to_severity_mapping.get(kind),
                       code=category
                       )

        if uri in self.diagnostics:
            self.diagnostics[uri].append(d)
        else:
            self.diagnostics[uri] = [d]

        if fatal:
            raise TRLC_Error(location, kind, message)

class File_Handler():
    def __init__(self):
        self.files = {}

    def update_files(self, ls, params):
        uri = params.text_document.uri
        document = ls.workspace.get_document(uri)
        content = document.source
        self.files.update({uri:content})

    def delete_files(self, params):
        uri = params.text_document.uri
        del self.files[uri]

class Vscode_Source_Manager(Source_Manager):
    """Reimplementation of TRLC's Source_Manager to read from vscode's
    workspace."""

    def __init__(self, mh, fh):
        super().__init__(mh)
        self.fh = fh

    def register_workspace(self, ls):
        dir_name = ls.workspace.root_path

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
