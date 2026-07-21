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
# trlc_lsp/handlers/__init__.py
"""LSP feature handler modules.

Each sub-module exposes a single ``register(server)`` function that
decorates the relevant feature handlers directly onto the supplied
:class:`~server.language_server.TrlcLanguageServer` instance, eliminating
the need for a module-level singleton.
"""

#: User-facing message shown when a handler is invoked before the first
#: parse cycle has completed.  Re-exported from the leaf guard module so all
#: callers (handlers + reference resolver) share one source of truth.
from ..parse_guard import WAIT_PARSING  # noqa: F401  (re-export)
