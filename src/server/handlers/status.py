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

"""Custom ``trlc/scopeStatus``/``trlc/scopeFiles`` requests: feed the
client's status bar item.

Not part of the LSP spec — the client's own status bar (parse mode +
progress + file count, and on click, the actual file list) has no standard
message to piggyback on, so this adds two custom request/response pairs,
following the same ``ls``/``params`` handler shape as every standard
``server.feature``.
"""

from dataclasses import dataclass

from lsprotocol.types import DiagnosticSeverity

from ..server_protocol import ServerProtocol

SCOPE_STATUS_METHOD = "trlc/scopeStatus"
SCOPE_FILES_METHOD = "trlc/scopeFiles"


@dataclass
class ScopeStatusParams:
    """Params for :data:`SCOPE_STATUS_METHOD`/:data:`SCOPE_FILES_METHOD`: the
    client's active document."""

    uri: str


def _count_by_severity(diagnostics: dict, severity: DiagnosticSeverity) -> int:
    return sum(
        1
        for file_diagnostics in diagnostics.values()
        for d in file_diagnostics
        if d.severity == severity
    )


def register(server: "ServerProtocol") -> None:
    """Register the ``trlc/scopeStatus``/``trlc/scopeFiles`` requests on
    *server*."""

    @server.feature(SCOPE_STATUS_METHOD)
    def scope_status(ls, params: ScopeStatusParams):
        """Return the parse status of *params.uri*'s scope, or ``None`` if
        the uri isn't part of any active scope (e.g. not a TRLC file, or
        not opened yet) — the client hides its status bar item in that case.
        """
        context = ls.get_context_for_uri(params.uri)
        if context is None:
            return None

        config = ls.get_config_for_uri(params.uri)
        # Single snapshot: vsm/diagnostics/fallback_reason must come from the
        # same published ParseResult (see ParseContext.snapshot).
        result = context.snapshot()

        return {
            "scopeId": context.scope_id,
            "parseMode": config.parse_mode.value,
            "verifyMode": config.verify_mode,
            "fileCount": len(result.vsm.all_files),
            "openFileCount": ls.store.open_file_count(context),
            "errorCount": _count_by_severity(
                result.diagnostics, DiagnosticSeverity.Error
            ),
            "warningCount": _count_by_severity(
                result.diagnostics, DiagnosticSeverity.Warning
            ),
            "fallbackReason": result.fallback_reason,
        }

    @server.feature(SCOPE_FILES_METHOD)
    def scope_files(ls, params: ScopeStatusParams):
        """Return every file path in *params.uri*'s scope, or ``None`` if
        the uri isn't part of any active scope. On-demand and separate from
        :func:`scope_status` (which fires on every diagnostics change) so
        the lightweight status ping never carries a potentially large file
        list.
        """
        context = ls.get_context_for_uri(params.uri)
        if context is None:
            return None
        return {"files": sorted(context.snapshot().vsm.all_files)}
