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

# This server is derived from the pygls example server, licensed under
# the Apache License, Version 2.0.

"""Server entry point.

Imports :class:`~server.language_server.TrlcLanguageServer` and creates
the singleton instance ``trlc_server`` that :mod:`trlc_lsp.__main__` uses to
start the chosen transport (stdio / TCP / WebSocket).

All language-server logic has been factored out into dedicated modules:

* :mod:`trlc_lsp.language_server` — server class + handler registration
* :mod:`trlc_lsp.parse_engine`    — background parse thread
* :mod:`trlc_lsp.context_store`   — thread-safe scope + config store
* :mod:`trlc_lsp.server_config`   — configuration dataclass + ParseMode
* :mod:`trlc_lsp.token_utils`     — pure token / URI helpers
* :mod:`trlc_lsp.handlers`        — one module per LSP feature
* :mod:`trlc_lsp.trlc_utils`      — TRLC library integration
"""

import os

from .language_server import TrlcLanguageServer


def _read_version(version_file: str) -> str:
    """Return the extension version from *version_file*, stripped, or
    ``"unknown"`` if it can't be read (e.g. missing in a packaging layout
    that doesn't ship it)."""
    try:
        with open(version_file, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "unknown"


_VERSION_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "VERSION",
)
_VERSION = _read_version(_VERSION_FILE)

trlc_server = TrlcLanguageServer("pygls-trlc", _VERSION)
