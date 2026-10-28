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
"""Unit tests for missing_symbols (unopened files declaring unknown symbols)."""

# pylint: disable=missing-function-docstring

import os
from types import SimpleNamespace

from lsprotocol.types import Diagnostic, Position, Range

from server.context_store import ContextStore
from server.file_handler import File_Handler
from server.missing_symbols import (
    declaring_files,
    package_of,
    unknown_symbol,
)
from server.parse_guard import normalize_fs_path
from server.server_config import ParseMode, ServerConfig
from server.token_utils import encode_scope_id, uri_from_file

SOURCE = """\
package SweReqs
import SysArchItems
SweReq r {
    derived = SysArchItems.tsc_diag
    other = local_one
}
"""


def _diag(line, character, message):
    pos = Position(line=line, character=character)
    return Diagnostic(range=Range(start=pos, end=pos), message=message)


QUALIFIED = _diag(3, 27, "unknown symbol tsc_diag")
UNQUALIFIED = _diag(4, 12, "unknown symbol local_one, did you mean local_two?")


def test_package_of_ignores_comments_and_strings():
    assert (
        package_of('// package X\n/* package Y */ "package Z"\npackage P\n')
        == "P"
    )
    assert package_of("") is None


def test_unknown_symbol_uses_qualifier_or_own_package():
    assert unknown_symbol(SOURCE, QUALIFIED) == ("tsc_diag", "SysArchItems")
    assert unknown_symbol(SOURCE, UNQUALIFIED) == ("local_one", "SweReqs")
    assert unknown_symbol(SOURCE, _diag(0, 0, "expected '}'")) is None


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _setup(tmp_path, mode=ParseMode.WORKSPACE):
    root = str(tmp_path)
    swe = os.path.join(root, "swe", "swe.trlc")
    _write(swe, SOURCE)
    _write(
        os.path.join(root, "sys", "tsc.trlc"),
        'package SysArchItems\nsection "S" {\n'
        "    DiagReq tsc_diag {\n        x = 1\n    }\n}\n",
    )
    # Same name in another package, and only in a comment: no match.
    _write(
        os.path.join(root, "sys", "other.trlc"),
        "package Other\nDiagReq tsc_diag { }\n"
        "package_x // SysArchItems: DiagReq tsc_diag { }\n",
    )
    _write(
        os.path.join(root, "swe", "local.trlc"),
        "package SweReqs\nT local_one {}\n",
    )
    _write(
        os.path.join(root, "bazel-out", "copy.trlc"),
        "package SysArchItems\nDiagReq tsc_diag { }\n",
    )
    uri = uri_from_file(swe)
    config = ServerConfig(parse_mode=mode)
    ls = SimpleNamespace(
        get_config_for_uri=lambda _uri: config,
        store=ContextStore(),
        fh=File_Handler(),
    )
    ls.fh.update_files(uri, SOURCE)
    scope_id = encode_scope_id(uri_from_file(root))
    ls.store.add_file(
        scope_id, uri, SOURCE, lambda: SimpleNamespace(open_files={})
    )
    return ls, root, uri


def test_declaring_files_finds_unopened_files(tmp_path):
    ls, root, uri = _setup(tmp_path)
    found = declaring_files(ls, uri, SOURCE, [QUALIFIED, UNQUALIFIED])
    assert {normalize_fs_path(k): v for k, v in found.items()} == {
        normalize_fs_path(os.path.join(root, "swe", "local.trlc")): [
            "local_one"
        ],
        normalize_fs_path(os.path.join(root, "sys", "tsc.trlc")): ["tsc_diag"],
    }


def test_declaring_files_only_in_workspace_mode(tmp_path):
    ls, _, uri = _setup(tmp_path, ParseMode.REPO)
    assert not declaring_files(ls, uri, SOURCE, [QUALIFIED])
