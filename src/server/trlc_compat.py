# *******************************************************************************
# TRLC VSCode Extension
# Copyright (C) 2026 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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

"""Supported TRLC version range.

The server subclasses TRLC's ``Source_Manager`` and reads its lexer/AST
internals, so only a tested range of TRLC releases is supported.
"""

from typing import Optional, Tuple

import trlc.version

#: Lowest supported TRLC release. Read by tools/version_synch/sync_version.py.
TRLC_MIN_VERSION = (3, 1, 1)
#: Supported releases stay below this major version. Read by sync_version.py.
TRLC_MAX_MAJOR_EXCLUSIVE = 4

#: pip requirement for the supported range. sync_version.py checks that
#: pyproject.toml, BUILD.bazel, installerCore.ts and requirements_dev.in
#: all carry exactly this string.
TRLC_REQUIREMENT = (
    f"trlc>={'.'.join(map(str, TRLC_MIN_VERSION))},<{TRLC_MAX_MAJOR_EXCLUSIVE}"
)


def installed_version() -> Tuple[int, ...]:
    """Version of the TRLC package this process imported."""
    return tuple(trlc.version.VERSION_TUPLE)


def installed_label() -> str:
    """Installed TRLC version as TRLC prints it, incl. any ``-dev`` suffix."""
    return trlc.version.TRLC_VERSION


def format_version(version: Tuple[int, ...]) -> str:
    """``(3, 1, 1)`` -> ``"3.1.1"``."""
    return ".".join(map(str, version))


def is_supported(version: Tuple[int, ...]) -> bool:
    """True if *version* lies inside the supported range."""
    return TRLC_MIN_VERSION <= version < (TRLC_MAX_MAJOR_EXCLUSIVE,)


def unsupported_message(
    version: Tuple[int, ...], label: Optional[str] = None
) -> Optional[str]:
    """User-facing warning for an unsupported *version*, else ``None``.

    *label* is the version text to show; defaults to *version* joined by dots.
    """
    if is_supported(version):
        return None
    shown = label or format_version(version)
    return (
        f"TRLC: trlc {shown} is outside the supported "
        f"range {TRLC_REQUIREMENT}; diagnostics may be wrong or missing. "
        "In VS Code: run 'TRLC: Reset Setup', then reload the window. "
        f'Standalone: pip install "{TRLC_REQUIREMENT}".'
    )
