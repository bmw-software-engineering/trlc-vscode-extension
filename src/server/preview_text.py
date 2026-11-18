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
"""Text helpers shared by the preview edit modules.

Offsets are character (code point) offsets into the file text.
"""

#: Whitespace other than the newline that may pad a line.
BLANK = " \t\r"
#: Indentation added per nesting level when a file gives no other clue.
STEP = "    "


def line_ending(content: str) -> str:
    """Line ending used by the file.

    Args:
        content: Full text of the file.

    Returns:
        CRLF if the file contains any CRLF, else LF.
    """
    return "\r\n" if "\r\n" in content else "\n"


def line_start(content: str, offset: int) -> int:
    """Character offset of the start of the line containing *offset*.

    Args:
        content: Full text of the file.
        offset: Character offset (code points) into *content*.

    Returns:
        Offset just after the last newline before *offset*, or 0 on the
        first line.
    """
    return content.rfind("\n", 0, offset) + 1


def line_indent(content: str, offset: int) -> str:
    """Leading whitespace of the line containing *offset*.

    Args:
        content: Full text of the file.
        offset: Character offset (code points) into *content*.

    Returns:
        The run of spaces and tabs at the start of that line (possibly
        empty).
    """
    start = line_start(content, offset)
    end = start
    while end < len(content) and content[end] in " \t":
        end += 1
    return content[start:end]


def first_on_line(content: str, offset: int) -> bool:
    """Tell whether only whitespace precedes *offset* on its line.

    Whitespace is what the TRLC lexer skips (``str.isspace``), so this
    agrees with it on which token starts a line.

    Args:
        content: Full text of the file.
        offset: Character offset (code points) into *content*.

    Returns:
        True if the text from the line start up to *offset* is blank.
    """
    return not content[line_start(content, offset) : offset].strip()
