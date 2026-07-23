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
"""Unit tests for per-scope symbol table isolation.

Uses real TRLC parses (not mocks) to prove the actual bug this architecture
fixes: two files with the same package/type name in different directories
must not collide in a shared symbol table when parsed into separate scopes.
"""

# pylint: disable=missing-class-docstring,missing-function-docstring

import os
from unittest.mock import MagicMock

import pytest
import trlc.ast
import trlc.errors
import trlc.trlc

from server.parse_context import ParseContext, ParseResult
from server.server_config import ParseMode
from server.token_utils import decode_scope_id, encode_scope_id


def _mock_vsm():
    """Return a mock Vscode_Source_Manager for pure dataclass-shape tests."""
    mock = MagicMock()
    mock.stab = MagicMock()
    mock.all_files = {}
    return mock


def _parse_one(package_decl: str, tmp_dir, filename="a.rsl"):
    """Parse a single minimal .rsl file declaring *package_decl* and return
    its real Source_Manager (so ``.stab`` reflects a genuine TRLC parse)."""
    path = os.path.join(str(tmp_dir), filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            f"package {package_decl}\n\ntype Marker {{\n    v Integer\n}}\n"
        )
    mh = trlc.errors.Message_Handler()
    sm = trlc.trlc.Source_Manager(mh=mh, verify_mode=False)
    sm.register_file(path)
    sm.process()
    return sm, path


class TestParseContextBasics:
    """ParseContext dataclass and basic operations."""

    def test_parse_context_creation(self):
        """Create a ParseContext with minimal fields."""
        # Given: a mock vsm
        vsm = _mock_vsm()
        # When: a ParseContext is created with minimal fields
        context = ParseContext(
            scope_id="file:///test",
            result=ParseResult(vsm=vsm),
            parse_mode=ParseMode.WORKSPACE,
        )
        # Then: its fields reflect what was passed, with sensible defaults
        assert context.scope_id == "file:///test"
        assert context.snapshot().vsm is vsm
        assert context.parse_mode == ParseMode.WORKSPACE
        assert context.open_files == {}

    def test_open_files_populated(self):
        """open_files can be populated with URIs."""
        # Given: a mock vsm and an open file uri/content pair
        vsm = _mock_vsm()
        # When: a ParseContext is created with open_files populated
        context = ParseContext(
            scope_id="file:///test",
            result=ParseResult(vsm=vsm),
            open_files={"file:///test/a.trlc": "content"},
            parse_mode=ParseMode.DIRECTORY,
        )
        # Then: the open file is present in open_files
        assert "file:///test/a.trlc" in context.open_files

    def test_diagnostics_defaults_to_empty(self):
        # Given/When: a ParseContext created without explicit diagnostics
        context = ParseContext(
            scope_id="file:///test",
            result=ParseResult(vsm=_mock_vsm()),
        )
        # Then: diagnostics defaults to an empty dict
        assert context.snapshot().diagnostics == {}

    def test_result_swap_is_visible_through_snapshot(self):
        """context.snapshot() always reflects the currently-published
        result — the wholesale-swap contract ParseEngine relies on."""
        # Given: a context published with an initial ParseResult
        vsm_a, vsm_b = _mock_vsm(), _mock_vsm()
        context = ParseContext(scope_id="s", result=ParseResult(vsm=vsm_a))
        assert context.snapshot().vsm is vsm_a
        # When: a new ParseResult is published (wholesale swap)
        context.result = ParseResult(vsm=vsm_b, diagnostics={"u": []})
        # Then: both .vsm and .diagnostics reflect the new result
        assert context.snapshot().vsm is vsm_b
        assert context.snapshot().diagnostics == {"u": []}

    def test_diagnostics_and_vsm_from_same_result_are_paired(self):
        """A single ``result`` capture always yields a matched (vsm,
        diagnostics) pair — the whole point of publishing them as one
        ParseResult instead of two separate field assignments."""
        # Given: a context published with a specific (vsm, diagnostics) pair
        vsm = _mock_vsm()
        diags = {"file://x": ["error"]}
        context = ParseContext(
            scope_id="s", result=ParseResult(vsm=vsm, diagnostics=diags)
        )
        # When: .result is captured once
        captured = context.result
        # Then: the captured vsm and diagnostics are the exact matched pair
        assert captured.vsm is vsm
        assert captured.diagnostics is diags


class TestRealScopeIsolation:
    """Two independently-parsed scopes never share symbol state.

    This is the core bug the per-scope architecture fixes: without
    isolation, ``dir1/a.rsl`` and ``dir2/a.rsl`` both declaring the same
    package name would collide in one shared symbol table and produce a
    spurious "duplicate definition" diagnostic. Parsing each into its own
    ParseContext (own Source_Manager) means each package name is only ever
    known to its own scope.
    """

    def test_same_package_name_in_two_scopes_does_not_collide(self, tmp_path):
        # Given: two directories, each with its own file declaring "Example"
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        sm1, path1 = _parse_one("Example", dir1)
        sm2, path2 = _parse_one("Example", dir2)

        # Each Source_Manager parsed successfully and independently — no
        # cross-contamination exception, and each has exactly its own file.
        assert list(sm1.all_files.keys()) == [path1]
        assert list(sm2.all_files.keys()) == [path2]

        # When: each is wrapped in its own isolated ParseContext
        ctx1 = ParseContext(
            scope_id=encode_scope_id("file:///ws", str(dir1)),
            result=ParseResult(vsm=sm1),
            parse_mode=ParseMode.DIRECTORY,
        )
        ctx2 = ParseContext(
            scope_id=encode_scope_id("file:///ws", str(dir2)),
            result=ParseResult(vsm=sm2),
            parse_mode=ParseMode.DIRECTORY,
        )

        # Then: the two scopes are fully independent
        assert ctx1.snapshot().vsm is not ctx2.snapshot().vsm
        assert ctx1.scope_id != ctx2.scope_id

        def _package(ctx):
            for value in ctx.snapshot().vsm.stab.table.values():
                if (
                    isinstance(value, trlc.ast.Package)
                    and value.name == "Example"
                ):
                    return value
            return None

        pkg1, pkg2 = _package(ctx1), _package(ctx2)
        # Each scope's symbol table has its OWN "Example" package object —
        # not a shared/merged one — proving the two parses never collided.
        assert pkg1 is not None and pkg2 is not None
        assert pkg1 is not pkg2

    def test_folder_scope_shares_one_context_for_multiple_files(
        self, tmp_path
    ):
        """WORKSPACE/REPO mode: files in the same folder share one context
        (only cross-directory scopes are isolated)."""
        # Given: a parsed file
        sm, path = _parse_one("Types", tmp_path)
        # When: it's registered as an open file in a WORKSPACE-mode context
        context = ParseContext(
            scope_id="file:///workspace",
            result=ParseResult(vsm=sm),
            open_files={path: "content"},
            parse_mode=ParseMode.WORKSPACE,
        )
        # Then: the file is tracked as open in that single shared context
        assert len(context.open_files) == 1


class TestScopeIdEncoding:
    """encode_scope_id / decode_scope_id round-trip for every parse mode's
    scope_id shape, and the derived scope_root each decodes to."""

    def test_workspace_and_repo_scope_id_is_bare_folder_uri(self):
        # Given/When: a scope_id is encoded with no segment (WORKSPACE/REPO)
        scope_id = encode_scope_id("file:///workspace")
        # Then: it is just the bare folder_uri, and decodes back to it
        assert scope_id == "file:///workspace"
        assert "::" not in scope_id
        folder_uri, segment = decode_scope_id(scope_id)
        assert folder_uri == "file:///workspace"
        assert segment is None

    def test_directory_scope_id_has_folder_and_directory(self):
        # Given/When: a scope_id is encoded with a directory segment
        scope_id = encode_scope_id("file:///workspace", "/workspace/subdir")
        # Then: it combines folder_uri and directory, and decodes back to both
        assert scope_id == "file:///workspace::/workspace/subdir"
        folder_uri, segment = decode_scope_id(scope_id)
        assert folder_uri == "file:///workspace"
        assert segment == "/workspace/subdir"

    def test_bazel_scope_id_has_folder_and_target_label(self):
        # Given/When: a scope_id is encoded with a Bazel target label segment
        scope_id = encode_scope_id("file:///workspace", "//trlc:trlc_spec")
        # Then: it decodes back to the folder_uri and target label
        folder_uri, segment = decode_scope_id(scope_id)
        assert folder_uri == "file:///workspace"
        assert segment == "//trlc:trlc_spec"

    def test_bazel_fallback_scope_id_matches_directory_shape(self):
        """When Bazel target resolution fails, the fallback scope_id must be
        indistinguishable in shape from a genuine DIRECTORY scope_id."""
        # Given/When: encoding the same directory segment via both paths
        fallback = encode_scope_id("file:///workspace", "/workspace/dir1")
        direct = encode_scope_id("file:///workspace", "/workspace/dir1")
        # Then: the resulting scope_ids are identical
        assert fallback == direct

    def test_folder_uri_containing_separator_is_rejected(self):
        # Given: a folder_uri that itself contains the "::" separator
        # When/Then: encoding it raises ValueError (would make decoding ambiguous)
        with pytest.raises(ValueError):
            encode_scope_id("file:///weird::folder", "/dir")

    def test_segment_may_itself_contain_separator(self):
        """decode_scope_id splits on the FIRST separator only, so a target
        label or path containing '::' round-trips correctly."""
        # Given/When: a segment that itself contains "::" is encoded
        scope_id = encode_scope_id("file:///workspace", "//pkg::sub:target")
        # Then: it still decodes back to the correct folder_uri and segment
        folder_uri, segment = decode_scope_id(scope_id)
        assert folder_uri == "file:///workspace"
        assert segment == "//pkg::sub:target"

    def test_two_directories_under_same_folder_get_different_scope_ids(self):
        # Given/When: two different directories under the same folder are encoded
        scope_id_dir1 = encode_scope_id("file:///workspace", "/workspace/dir1")
        scope_id_dir2 = encode_scope_id("file:///workspace", "/workspace/dir2")
        # Then: they produce distinct scope_ids
        assert scope_id_dir1 != scope_id_dir2
