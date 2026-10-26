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
"""Unit tests for the supported-TRLC-range helpers in server.trlc_compat."""

# pylint: disable=missing-class-docstring,missing-function-docstring

import pytest

from server import trlc_compat


class TestIsSupported:
    @pytest.mark.parametrize(
        "version",
        [(3, 1, 1), (3, 1, 2), (3, 2, 0), (3, 99, 99)],
    )
    def test_versions_inside_range_are_supported(self, version):
        """Given a version from the minimum up to (excluding) the next
        major, Then it is supported."""
        # Given/When/Then
        assert trlc_compat.is_supported(version)

    @pytest.mark.parametrize(
        "version",
        [(3, 0, 0), (3, 0, 1), (3, 1, 0), (2, 9, 9), (4, 0, 0), (4, 1, 0)],
    )
    def test_versions_outside_range_are_unsupported(self, version):
        """Given a version below the minimum or at/above the next major,
        Then it is unsupported."""
        # Given/When/Then
        assert not trlc_compat.is_supported(version)


class TestUnsupportedMessage:
    def test_supported_version_has_no_message(self):
        # Given: a supported version
        # When/Then: no warning text
        assert trlc_compat.unsupported_message((3, 1, 1)) is None

    def test_unsupported_version_message_names_version_and_range(self):
        # Given: an unsupported version
        # When: the warning is built
        message = trlc_compat.unsupported_message((3, 0, 0))
        # Then: it names the found version and the supported range
        assert message is not None
        assert "trlc 3.0.0" in message
        assert trlc_compat.TRLC_REQUIREMENT in message

    def test_message_shows_given_label_and_reset_setup_advice(self):
        # Given: an unsupported version with a pre-release label
        # When: the warning is built with that label
        message = trlc_compat.unsupported_message((4, 0, 0), "4.0.0-dev")
        # Then: the label is shown, and the VS Code remedy comes before pip
        assert message is not None
        assert "trlc 4.0.0-dev" in message
        assert "TRLC: Reset Setup" in message
        assert message.index("Reset Setup") < message.index("pip install")


class TestInstalled:
    def test_installed_trlc_is_in_supported_range(self):
        """Given the TRLC version the test environment resolves (via the
        lockfile), Then it lies in the supported range — guards the
        lockfile against drifting out of it."""
        # Given/When/Then
        assert trlc_compat.is_supported(trlc_compat.installed_version())

    def test_label_starts_with_installed_version(self):
        # Given: the installed TRLC
        version = trlc_compat.format_version(trlc_compat.installed_version())
        # When/Then: its printed label is that version plus any suffix
        assert trlc_compat.installed_label().startswith(version)


def test_format_version_joins_with_dots():
    # Given/When/Then
    assert trlc_compat.format_version((3, 1, 1)) == "3.1.1"
