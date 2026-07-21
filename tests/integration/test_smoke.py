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
"""Smoke tests: server lifecycle and basic protocol compliance."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.


# pylint: disable-next=too-few-public-methods  # single smoke test; separate class for clarity
class TestServerLifecycle:
    async def test_server_starts_and_initializes(self, client):
        """initialize/initialized handshake completes without error."""
        # Given: a server subprocess started via the `client` fixture
        # When: the initialize/initialized handshake has completed
        # Then: the client and its capabilities are populated
        assert client is not None
        assert client.initialize_result is not None
        assert client.initialize_result.capabilities is not None
