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

"""Per-scope parse context for symbol table isolation."""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .server_config import ParseMode
from .source_manager import Vscode_Source_Manager


@dataclass(frozen=True)
class ParseResult:
    """One parse cycle's output for a scope: symbol table + diagnostics.

    Published as a single atomic reference swap onto
    :attr:`ParseContext.result` by the ParseEngine worker thread — never
    mutated in place. A reader that captures ``context.snapshot()`` exactly
    once into a local variable is guaranteed to see one consistent
    (vsm, diagnostics) pair, even if the worker publishes a new result mid
    read — see :meth:`ParseContext.snapshot`.
    """

    vsm: Vscode_Source_Manager
    """Symbol table for files in this scope."""

    diagnostics: Dict[str, Any] = field(default_factory=dict)
    """Diagnostics from this parse of the scope. Maps uri -> list[Diagnostic]."""

    fallback_reason: Optional[str] = None
    """Set when this cycle's discovery fell back to a different strategy
    than configured (BAZEL mode only — see
    :meth:`~server.scope_strategies.ScopeStrategy.discover`'s return
    value). ``None`` when the configured strategy ran normally."""


@dataclass
class ParseContext:
    """Isolated symbol table + state for one scope.

    A scope is determined by the parseMode setting:
    - WORKSPACE/REPO: scope is one workspace folder
    - DIRECTORY: scope is one directory within a folder
    - BAZEL: scope is one Bazel target

    The scope_id uniquely identifies the scope within the workspace.
    """

    scope_id: str
    """Unique identifier for this scope. Format varies by parseMode:
    - Folder-scoped (WORKSPACE/REPO): folder_uri
    - Directory-scoped (DIRECTORY): "folder_uri::directory_path"
    - Target-scoped (BAZEL): "folder_uri::target_label"

    Built and parsed via :func:`~server.token_utils.encode_scope_id` /
    :func:`~server.token_utils.decode_scope_id` — never hand-rolled.
    """

    result: ParseResult
    """Last parse cycle's output (symbol table + diagnostics), replaced
    wholesale by the ParseEngine worker thread; see :class:`ParseResult`."""

    open_files: Dict[str, str] = field(default_factory=dict)
    """Editor-open files in this scope (uri -> current document content),
    mutated by did_open/did_close via ContextStore.

    Distinct from ``vsm.all_files`` (file path -> parsed File object), which
    holds every file the last parse discovered in the scope, not just the
    ones currently open in the editor.
    """

    parse_mode: ParseMode = ParseMode.WORKSPACE
    """Parse mode that governs this context's scope boundary."""

    def snapshot(self) -> ParseResult:
        """Single consistent read of ``vsm``/``diagnostics``/``fallback_reason``.

        Binding ``self.result`` once here guarantees all three come from the
        same published :class:`ParseResult`, even if the worker swaps in a
        new one mid-read. Use this whenever a caller needs any of those
        fields — there is no shortcut property, precisely so a caller can't
        accidentally read ``self.result`` twice and observe two different
        results (see :class:`ParseResult`). A single-field read still goes
        through here too: ``ctx.snapshot().vsm``.
        """
        return self.result
