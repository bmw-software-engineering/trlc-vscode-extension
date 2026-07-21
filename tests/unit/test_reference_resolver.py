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
"""Unit tests for :mod:`server.reference_resolver`.

``handlers/test_navigation.py`` only exercises the resolver indirectly
through the find-references/rename handlers, and only within a single
package (no import relationships). These tests cover the resolver's own
scope logic in isolation: ``_collect_pars``'s (deliberately non-transitive)
import-based file selection, and ``ReferenceResolver.resolve()`` end-to-end
across a real cross-package import — plus the identifier-index cache added
for the reference lookup's performance path.
"""

# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access
# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

import gc
import os
from types import SimpleNamespace

import pytest
import trlc.errors
import trlc.trlc

from server.parse_guard import WAIT_PARSING, normalize_fs_path
from server.reference_resolver import ReferenceResolver, _collect_pars
from server.token_utils import uri_from_file

from .conftest import _FakeContext, _FakeVSM

# ---------------------------------------------------------------------------
# _collect_pars: pure logic, fabricated "par" objects
# ---------------------------------------------------------------------------


def _fake_par(package, imports=(), has_tokens=True):
    """A minimal stand-in for a parsed file object: only the attributes
    `_collect_pars` reads (`lexer.tokens`, `cu.package`, `cu.imports`)."""
    return SimpleNamespace(
        lexer=SimpleNamespace(tokens=["tok"] if has_tokens else []),
        cu=SimpleNamespace(package=package, imports=list(imports)),
    )


class TestCollectPars:
    def test_includes_file_in_same_package(self):
        """A file in the same package as the query is always included."""
        # Given/When/Then: A file in the same package as the query is always included
        same = _fake_par(package="A")
        result = _collect_pars({"same": same}, cur_pkg="A", imp_pkg=[])
        assert result == [same]

    def test_includes_file_directly_imported_by_query_file(self):
        """cur_pkg's imports (imp_pkg) directly name a package: files in
        that package are included."""
        # Given/When/Then: cur_pkg's imports (imp_pkg) directly name a package: files in that
        # package are included
        imported = _fake_par(package="B")
        result = _collect_pars(
            {"imported": imported}, cur_pkg="A", imp_pkg=["B"]
        )
        assert result == [imported]

    def test_includes_file_that_imports_the_query_package(self):
        """The reverse direction also counts: a file whose own imports name
        cur_pkg is included (its imports list contains cur_pkg)."""
        # Given/When/Then: The reverse direction also counts: a file whose own imports name cur_pkg
        # is included (its imports list contains cur_pkg)
        importer = _fake_par(package="C", imports=["A"])
        result = _collect_pars({"importer": importer}, cur_pkg="A", imp_pkg=[])
        assert result == [importer]

    def test_excludes_indirectly_imported_package_two_hops_away(self):
        """A imports B, B imports C: from A's perspective, C is NOT
        included unless A directly imports it too — `_collect_pars` is a
        single-hop check, not a transitive closure. This test pins that
        contract so a future change doesn't silently make lookups leak
        across indirect import chains (or silently stop finding direct
        ones)."""
        # Given: cur_pkg="A" imports only "B" (not "C"); "C" doesn't import
        # "A" either — so from _collect_pars's perspective there is no direct
        # relationship between "A" and "C".
        two_hops_away = _fake_par(package="C")
        # When: collecting pars reachable from "A"
        result = _collect_pars(
            {"c": two_hops_away}, cur_pkg="A", imp_pkg=["B"]
        )
        # Then: the indirectly-related (two-hop) file is excluded
        assert result == []

    def test_excludes_unrelated_package(self):
        """A file in a package with no import relationship at all is
        excluded."""
        # Given/When/Then: A file in a package with no import relationship at all is excluded
        unrelated = _fake_par(package="Z")
        result = _collect_pars({"z": unrelated}, cur_pkg="A", imp_pkg=["B"])
        assert result == []

    def test_cyclic_import_does_not_break_collection(self):
        """A imports B and B imports A (a cycle): both directions of the
        relationship independently satisfy inclusion, so this must not
        raise or loop — `_collect_pars` has no recursion to begin with,
        but this documents that a cycle is a perfectly normal input."""
        # Given/When/Then: A imports B and B imports A (a cycle): both directions of the
        # relationship independently satisfy inclusion, so this must not raise or loop —
        # `_collect_pars` has no recursion to begin with, but this documents that a cycle is a
        # perfectly normal input
        a_file = _fake_par(package="A", imports=["B"])
        b_file = _fake_par(package="B", imports=["A"])
        all_files = {"a": a_file, "b": b_file}

        from_a = _collect_pars(all_files, cur_pkg="A", imp_pkg=["B"])
        from_b = _collect_pars(all_files, cur_pkg="B", imp_pkg=["A"])

        assert {id(p) for p in from_a} == {id(a_file), id(b_file)}
        assert {id(p) for p in from_b} == {id(a_file), id(b_file)}

    def test_excludes_files_with_no_tokens(self):
        """A file that failed to lex (empty tokens) is excluded even if its
        package matches — nothing there to search."""
        # Given: a file whose package matches but which has no lexer tokens
        empty = _fake_par(package="A", has_tokens=False)
        # When: collecting pars reachable from the same package
        result = _collect_pars({"empty": empty}, cur_pkg="A", imp_pkg=[])
        # Then: it is excluded despite the package match
        assert result == []


# ---------------------------------------------------------------------------
# ReferenceResolver.resolve(): end-to-end over a real cross-package import
# ---------------------------------------------------------------------------

BASE_RSL = """\
package Base

type Widget {
    label String
}
"""

MID_RSL = """\
package Mid
import Base

Base.Widget instance {
    label = "hello"
}
"""

OTHER_RSL = """\
package Other

type Unrelated {
    n Integer
}
"""


def _parse(tmp_path):
    """Parse three files (Base, Mid importing Base, Other unrelated) with a
    real trlc.trlc.Source_Manager and return (all_files, base_path, mid_path,
    other_path). Mid is a .trlc data file (it instantiates Base.Widget, and
    object instantiation belongs in .trlc, not .rsl schema files)."""
    # normalize_fs_path so all_files is keyed the way production keys it
    # (Vscode_Source_Manager.register_file normalizes every key). This bare
    # trlc.trlc.Source_Manager doesn't, so on Windows the raw paths keep the
    # drive letter's original case (``C:\…``) while the resolver looks files
    # up via uri_from_file→normalize_fs_path (``c:\…``) — the mismatch makes
    # every cross-file lookup miss. No-op on POSIX.
    base_path = normalize_fs_path(os.path.join(str(tmp_path), "base.rsl"))
    mid_path = normalize_fs_path(os.path.join(str(tmp_path), "mid.trlc"))
    other_path = normalize_fs_path(os.path.join(str(tmp_path), "other.rsl"))
    with open(base_path, "w", encoding="utf-8") as f:
        f.write(BASE_RSL)
    with open(mid_path, "w", encoding="utf-8") as f:
        f.write(MID_RSL)
    with open(other_path, "w", encoding="utf-8") as f:
        f.write(OTHER_RSL)

    mh = trlc.errors.Message_Handler()
    sm = trlc.trlc.Source_Manager(mh=mh, verify_mode=False)
    sm.register_file(base_path)
    sm.register_file(mid_path)
    sm.register_file(other_path)
    try:
        sm.process()
    except trlc.errors.TRLC_Error:
        pass

    return sm.all_files, base_path, mid_path, other_path


class _FakeLs:
    """Just enough of ServerProtocol for ReferenceResolver.resolve()."""

    def __init__(self, all_files, uri_to_context):
        self._all_files = all_files
        self._uri_to_context = uri_to_context
        self.messages = []
        self.logs = []

    def get_context_for_uri(self, uri):
        """Return the fixed context registered for this uri, or None."""
        return self._uri_to_context.get(uri)

    def window_show_message(self, params):
        """Record the message instead of actually showing it."""
        self.messages.append(params)

    def window_log_message(self, params):
        """Record the log message instead of actually showing it."""
        self.logs.append(params)


@pytest.fixture
def parsed(tmp_path):
    """Parse the shared cross-package fixture layout once per test."""
    return _parse(tmp_path)


@pytest.fixture
def fake_ls(parsed):
    """Build a `_FakeLs` whose contexts all resolve to the parsed fixture."""
    all_files, base_path, mid_path, other_path = parsed
    vsm = _FakeVSM(all_files)
    context = _FakeContext(vsm)
    uri_to_context = {
        uri_from_file(base_path): context,
        uri_from_file(mid_path): context,
        uri_from_file(other_path): context,
    }
    return _FakeLs(all_files, uri_to_context)


class TestResolveAcrossImport:
    def test_finds_usage_in_file_that_imports_the_definition(
        self, fake_ls, parsed
    ):
        """Renaming/finding references on 'Widget' (defined in Base) must
        find its usage in Mid.rsl, which imports Base — proving the
        resolver's cross-file search follows a real import, not just
        same-package matches."""
        # Given: 'Widget' defined in Base, used in Mid (which imports Base)
        _, base_path, mid_path, _ = parsed
        resolver = ReferenceResolver()

        # When: resolving references at Widget's declaration site
        # cursor on 'Widget' at its declaration: "type Widget {" (line 2, 0-based)
        locations = resolver.resolve(fake_ls, base_path, line=2, col=6)

        # Then: both the declaration and the cross-file usage are found
        assert locations is not None
        found_uris = {loc.uri for loc in locations}
        assert uri_from_file(base_path) in found_uris
        assert uri_from_file(mid_path) in found_uris

    def test_excludes_unrelated_file_with_no_import_relationship(
        self, fake_ls, parsed
    ):
        """Other.rsl has no import relationship with Base at all, so it must
        never appear in the results."""
        # Given/When/Then: Other.rsl has no import relationship with Base at all, so it must never
        # appear in the results
        _, base_path, _, other_path = parsed
        resolver = ReferenceResolver()

        locations = resolver.resolve(fake_ls, base_path, line=2, col=6)

        assert locations is not None
        found_uris = {loc.uri for loc in locations}
        assert uri_from_file(other_path) not in found_uris

    def test_unparsed_file_shows_wait_message_and_returns_none(self, fake_ls):
        """A file with no active context (never opened) shows the shared
        WAIT_PARSING message and returns None."""
        # Given/When/Then: A file with no active context (never opened) shows the shared
        # WAIT_PARSING message and returns None
        resolver = ReferenceResolver()

        result = resolver.resolve(
            fake_ls, "/does/not/exist.rsl", line=0, col=0
        )

        assert result is None
        assert any(m.message == WAIT_PARSING for m in fake_ls.messages)


class TestIdentifierIndexCache:
    def test_index_is_built_once_and_reused_across_resolve_calls(
        self, fake_ls, parsed
    ):
        """The identifier index (perf optimization: avoids re-scanning every
        token on every call) is cached on the resolver, keyed by vsm, and
        reused (not rebuilt) across multiple resolve() calls against the
        same parse generation."""
        # Given: a resolver and a real parsed vsm
        _, base_path, _, _ = parsed
        resolver = ReferenceResolver()
        vsm = fake_ls._uri_to_context[uri_from_file(base_path)].vsm

        # When: resolve() is called twice against the same vsm
        resolver.resolve(fake_ls, base_path, line=2, col=6)
        index_after_first = resolver._index_cache.get(vsm)
        assert index_after_first is not None

        resolver.resolve(fake_ls, base_path, line=2, col=6)
        index_after_second = resolver._index_cache.get(vsm)

        # Then: the same cached index object is reused, not rebuilt
        assert index_after_first is index_after_second

    def test_fresh_vsm_does_not_reuse_a_previous_vsm_index(
        self, fake_ls, parsed
    ):
        """A new parse cycle publishes a brand-new vsm object (never mutates
        the previous one) — its identifier index must start uncached, never
        inherit a stale index from the vsm it replaced."""
        # Given: a resolver that has already built an index for the old vsm
        all_files, base_path, _, _ = parsed
        resolver = ReferenceResolver()

        resolver.resolve(fake_ls, base_path, line=2, col=6)

        # When: the next parse cycle publishes a fresh vsm/context
        # (same file table).
        new_vsm = _FakeVSM(all_files)
        fake_ls._uri_to_context[uri_from_file(base_path)] = _FakeContext(
            new_vsm
        )

        # Then: the new vsm starts uncached until resolve() builds its own
        assert new_vsm not in resolver._index_cache
        resolver.resolve(fake_ls, base_path, line=2, col=6)
        assert new_vsm in resolver._index_cache

    def test_old_vsm_index_is_garbage_collected_once_unreferenced(
        self, fake_ls, parsed
    ):
        """The cache is keyed by weak reference: once nothing but the cache
        itself would hold a vsm, its entry disappears on its own — the
        resolver never accumulates indexes for every parse generation ever
        seen over a long-running session."""
        # Given: a resolver with a cached index for the current vsm
        _, base_path, _, _ = parsed
        resolver = ReferenceResolver()
        context = fake_ls._uri_to_context[uri_from_file(base_path)]
        old_vsm = context.vsm

        resolver.resolve(fake_ls, base_path, line=2, col=6)
        assert old_vsm in resolver._index_cache

        # When: the only strong reference to old_vsm is dropped (context.vsm
        # is reassigned, just like a real reparse publishing a new ParseResult)
        context.vsm = _FakeVSM(context.vsm.all_files)
        del old_vsm
        gc.collect()

        # Then: its cache entry is gone — nothing keeps it alive but the cache
        assert len(resolver._index_cache) == 0
