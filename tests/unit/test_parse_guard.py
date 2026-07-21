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
"""Unit tests for :mod:`server.parse_guard` — the shared parse-wait guard.

Also pins the de-duplication itself: every module that used to define its
own copy of `WAIT_PARSING`/the guard logic (``token_utils.py``,
``handlers/__init__.py``, ``reference_resolver.py``) must now import or
call the single implementation in ``parse_guard.py``, not a second copy.
"""

# pylint: disable=missing-class-docstring,missing-function-docstring

import ntpath
from unittest.mock import MagicMock

import pygls.uris
from lsprotocol.types import MessageType

from server import handlers as handlers_pkg
from server import parse_guard, token_utils
from server.parse_guard import (
    WAIT_PARSING,
    ensure_parsed,
    lookup_parsed_file,
    normalize_fs_path,
)
from server.token_utils import uri_from_file

from .conftest import _FakeContext, _FakeVSM


class TestNoDuplication:
    def test_token_utils_uses_the_same_ensure_parsed(self):
        """token_utils.resolve_open_file calls the guard's own
        ensure_parsed, not a copy."""
        # Given/When/Then: token_utils.ensure_parsed is the guard's own, not a copy
        assert token_utils.ensure_parsed is ensure_parsed

    def test_handlers_package_reexports_the_same_wait_parsing_string(self):
        """handlers.WAIT_PARSING is the guard's own, not a copy."""
        # Given/When/Then: handlers.WAIT_PARSING is the guard's own, not a copy
        assert handlers_pkg.WAIT_PARSING is WAIT_PARSING

    def test_parse_guard_has_no_import_of_handlers_or_server_protocol(self):
        """The leaf-module property that broke the original circular
        import: parse_guard must not import handlers or server_protocol."""
        # Given/When/Then: The leaf-module property that broke the original circular import:
        # parse_guard must not import handlers or server_protocol
        assert "handlers" not in vars(parse_guard)
        assert "server_protocol" not in vars(parse_guard)


class TestLookupParsedFile:
    def test_returns_none_and_shows_message_when_no_context(self):
        """No context for the uri -> None result + a WAIT_PARSING message."""
        # Given/When/Then: No context for the uri -> None result + a WAIT_PARSING message
        ls = MagicMock()
        ls.get_context_for_uri.return_value = None

        result = lookup_parsed_file(ls, "file:///a.rsl", "/a.rsl")

        assert result is None
        ls.window_show_message.assert_called_once()
        params = ls.window_show_message.call_args[0][0]
        assert params.message == WAIT_PARSING
        assert params.type == MessageType.Info

    def test_returns_none_when_file_not_yet_in_vsm(self):
        """Context exists but the file isn't in its vsm yet -> None."""
        # Given/When/Then: Context exists but the file isn't in its vsm yet -> None
        ls = MagicMock()
        ls.get_context_for_uri.return_value = _FakeContext(
            _FakeVSM(all_files={})
        )

        result = lookup_parsed_file(ls, "file:///a.rsl", "/a.rsl")

        assert result is None
        ls.window_show_message.assert_called_once()

    def test_returns_vsm_and_file_obj_when_parsed(self):
        """File already parsed -> (vsm, file_obj) tuple, no message shown."""
        # Given/When/Then: File already parsed -> (vsm, file_obj) tuple, no message shown
        file_obj = object()
        # Key by normalize_fs_path("/a.rsl"), not the raw "/a.rsl":
        # lookup_parsed_file normalizes its file_path argument before the
        # all_files lookup, and on Windows normalize_fs_path("/a.rsl") is
        # "c:\\a.rsl" (abspath adds the current drive, normcase lowercases it),
        # so a raw "/a.rsl" key never matches. No-op on POSIX.
        vsm = _FakeVSM(all_files={normalize_fs_path("/a.rsl"): file_obj})
        ls = MagicMock()
        ls.get_context_for_uri.return_value = _FakeContext(vsm)

        result = lookup_parsed_file(ls, "file:///a.rsl", "/a.rsl")

        assert result == (vsm, file_obj)
        ls.window_show_message.assert_not_called()


class TestEnsureParsed:
    def test_returns_none_when_not_parsed(self):
        """No context for the uri -> None, same as lookup_parsed_file."""
        # Given/When/Then: No context for the uri -> None, same as lookup_parsed_file
        ls = MagicMock()
        ls.get_context_for_uri.return_value = None

        assert ensure_parsed(ls, "file:///a.rsl", "/a.rsl") is None

    def test_returns_just_the_file_object_when_parsed(self):
        """Parsed file -> just the file object, not the (vsm, file_obj) pair."""
        # Given/When/Then: Parsed file -> just the file object, not the (vsm, file_obj) pair
        file_obj = object()
        # Key by normalize_fs_path("/a.rsl"), not the raw "/a.rsl":
        # lookup_parsed_file normalizes its file_path argument before the
        # all_files lookup, and on Windows normalize_fs_path("/a.rsl") is
        # "c:\\a.rsl" (abspath adds the current drive, normcase lowercases it),
        # so a raw "/a.rsl" key never matches. No-op on POSIX.
        vsm = _FakeVSM(all_files={normalize_fs_path("/a.rsl"): file_obj})
        ls = MagicMock()
        ls.get_context_for_uri.return_value = _FakeContext(vsm)

        assert ensure_parsed(ls, "file:///a.rsl", "/a.rsl") is file_obj


class TestNormalizeFsPathWindowsCasing:
    """Pins the exact regression from CI run 29901546322: on real Windows
    runners, every ``wait_for_diagnostics()`` integration test timed out
    because the server published diagnostics under a fully-lowercased URI
    while the client's ``didOpen`` used the tmp dir's real mixed case
    (``AppData``, ``Local``, ``Temp``). Root cause was ``normalize_fs_path``
    using ``os.path.normcase``, which lowercases the *entire* Windows path,
    not just the drive letter like pygls's own ``from_fs_path`` does -- so
    a URI re-derived from the normalized path no longer matched the one the
    client actually used. These tests simulate Windows on any host by
    swapping in ``ntpath`` and pygls's ``IS_WIN`` flag, since neither
    ``os.path`` nor pygls's URI conversion branch on path *content*, only on
    the real host platform.
    """

    def _make_windows(self, monkeypatch):
        monkeypatch.setattr(parse_guard.os, "name", "nt")
        monkeypatch.setattr(parse_guard.os, "path", ntpath)
        monkeypatch.setattr(pygls.uris, "IS_WIN", True)

    def test_only_drive_letter_lowercased(self, monkeypatch):
        # Given: Windows simulated, and a mixed-case absolute path whose
        # folder names must NOT be touched -- only the drive letter
        self._make_windows(monkeypatch)
        path = "C:\\Users\\runneradmin\\AppData\\Local\\Temp\\sample.trlc"

        # When
        result = normalize_fs_path(path)

        # Then: drive letter lowercased, everything else preserved verbatim
        assert result == (
            "c:\\Users\\runneradmin\\AppData\\Local\\Temp\\sample.trlc"
        )

    def test_uri_survives_normalization_round_trip(self, monkeypatch):
        # Given: Windows simulated, and the client's real (mixed-case) path,
        # exactly as tempfile/os.walk would hand it back on a real
        # Windows CI runner -- the same path publishDiagnostics must key by
        self._make_windows(monkeypatch)
        path = "C:\\Users\\runneradmin\\AppData\\Local\\Temp\\sample.trlc"

        # When: the server registers the file under its normalized form
        # (source_manager.register_file) and later re-derives a uri from it
        # for publishing (message_handler._location_to_uri) -- both must
        # agree with the uri the client itself would have sent in didOpen
        published_uri = uri_from_file(normalize_fs_path(path))
        client_uri = uri_from_file(path)

        # Then: the two URIs match, so client.diagnostics[client_uri] is the
        # same key the server just published under -- no more spinning until
        # wait_for_diagnostics()'s timeout
        assert published_uri == client_uri
