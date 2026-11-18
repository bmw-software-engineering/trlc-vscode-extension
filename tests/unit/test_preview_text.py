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
"""Unit tests for the text helpers shared by the preview edit modules."""

# pylint: disable=missing-function-docstring

from server.preview_text import (
    first_on_line,
    line_ending,
    line_indent,
    line_start,
)

TEXT = "a = 1\n\t  b = 2\r\nc"


def test_line_start():
    assert line_start(TEXT, 3) == 0
    assert line_start(TEXT, TEXT.index("b")) == 6
    assert line_start(TEXT, len(TEXT)) == len(TEXT) - 1


def test_line_indent_is_the_leading_spaces_and_tabs():
    assert line_indent(TEXT, 3) == ""
    assert line_indent(TEXT, TEXT.index("2")) == "\t  "


def test_line_ending_follows_the_file():
    assert line_ending("a\nb\n") == "\n"
    assert line_ending("a\r\nb\n") == "\r\n"


def test_first_on_line():
    assert first_on_line(TEXT, 0)
    assert first_on_line(TEXT, TEXT.index("b"))
    assert not first_on_line(TEXT, TEXT.index("2"))


def test_first_on_line_agrees_with_the_lexer_on_whitespace():
    # The TRLC lexer skips everything str.isspace() accepts, so a form
    # feed or a no-break space before a token is still only whitespace.
    text = "x\n\f  y"
    assert first_on_line(text, text.index("y"))
