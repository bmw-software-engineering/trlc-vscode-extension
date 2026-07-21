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

"""TRLC error → LSP Diagnostic converter.

:class:`Vscode_Message_Handler` subclasses the TRLC compiler's own
:class:`trlc.errors.Message_Handler` (part of the pluggable error-reporting
hook in the TRLC parser) and accumulates parse errors and warnings as LSP
:class:`~lsprotocol.types.Diagnostic` objects, grouped by file URI, so they
can be published via ``textDocument/publishDiagnostics``.

Note: This module is NOT responsible for JSON-RPC dispatch or protocol
handling — that is entirely managed by pygls. This module only converts
TRLC's internal error representation to LSP's Diagnostic format.
"""

from lsprotocol.types import (
    Diagnostic,
    DiagnosticSeverity,
    Position,
    Range,
)
from trlc.errors import Kind, Message_Handler, TRLC_Error

from .token_utils import uri_from_file

#: Maps TRLC error kinds to LSP diagnostic severities.
kind_to_severity_mapping = {
    Kind.SYS_ERROR: DiagnosticSeverity.Error,
    Kind.SYS_CHECK: DiagnosticSeverity.Information,
    Kind.SYS_WARNING: DiagnosticSeverity.Warning,
    Kind.USER_ERROR: DiagnosticSeverity.Error,
    Kind.USER_WARNING: DiagnosticSeverity.Warning,
}


class Vscode_Message_Handler(Message_Handler):
    """Reimplementation of TRLC's Message_Handler to emit LSP diagnostics."""

    def __init__(self):
        super().__init__()
        self.diagnostics = {}

    @staticmethod
    def _location_to_range(location) -> Range:
        """Convert a TRLC Location to an LSP Range."""
        end_location = location.get_end_location()
        end_line = (
            0 if end_location.line_no is None else end_location.line_no - 1
        )
        end_col = 1 if end_location.col_no is None else end_location.col_no
        start_line = 0 if location.line_no is None else location.line_no - 1
        start_col = 0 if location.col_no is None else location.col_no - 1
        return Range(
            start=Position(line=start_line, character=start_col),
            end=Position(line=end_line, character=end_col),
        )

    @staticmethod
    def _location_to_uri(location) -> str:
        """Convert a TRLC Location's file_name to a file:// URI."""
        return uri_from_file(location.file_name)

    def emit(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # interface imposed by TRLC Message_Handler base class
        self,
        location,
        kind,
        message,
        fatal=True,
        extrainfo=None,
        category=None,
    ):
        msg = message + (f"\n{extrainfo}" if extrainfo is not None else "")
        diag = Diagnostic(
            range=self._location_to_range(location),
            message=msg,
            severity=kind_to_severity_mapping.get(
                kind, DiagnosticSeverity.Error
            ),
            code=category,
        )
        uri = self._location_to_uri(location)
        self.diagnostics.setdefault(uri, []).append(diag)
        if fatal:
            raise TRLC_Error(location, kind, message)
