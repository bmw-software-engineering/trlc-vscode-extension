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
"""Unit tests for trlc_lsp.handlers.completion.

Trigger positions in types.rsl and sample.trlc (0-based LSP lines/cols):

types.rsl
  line=1 col=8   → after 'package ' (trigger ' ' on 'package' token)

sample.trlc
  line=2 col=8   → after 'MyRecord ' keyword-like (no trigger, manual)
  line=3 col=18  → after '{' inside record object body
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=too-few-public-methods
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment —
# some groupings only need a single test.

# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

import os
from unittest.mock import Mock

import pytest
import trlc.ast
import trlc.errors
import trlc.trlc
from lsprotocol.types import (
    TEXT_DOCUMENT_COMPLETION,
    CompletionContext,
    CompletionParams,
    CompletionTriggerKind,
    Position,
    TextDocumentIdentifier,
)

from server.handlers import completion as comp_mod
from server.parse_context import ParseContext, ParseResult
from server.token_utils import lex_fallback, lookup_by_name, uri_from_file
from tests.unit.handlers.conftest import FakeLanguageServer, FakeVSM


def _params(uri, line, col, trigger_char=None):
    kind = (
        CompletionTriggerKind.TriggerCharacter
        if trigger_char
        else CompletionTriggerKind.Invoked
    )
    return CompletionParams(
        text_document=TextDocumentIdentifier(uri=uri),
        position=Position(line=line, character=col),
        context=CompletionContext(
            trigger_kind=kind,
            trigger_character=trigger_char,
        ),
    )


@pytest.fixture
def comp_fn(fake_ls):
    """Comp fn."""
    comp_mod.register(fake_ls)
    return fake_ls.get_handler(TEXT_DOCUMENT_COMPLETION)


class TestCompletion:
    def test_completion_returns_list_object(self, fake_ls, comp_fn, rsl_path):
        """Any valid call returns a CompletionList (possibly empty)."""
        # Given: a parsed schema file and a completion request against it
        uri = uri_from_file(rsl_path)
        params = _params(uri, line=1, col=9, trigger_char=" ")
        # When: completion is requested
        result = comp_fn(fake_ls, params)
        # Then: a CompletionList object is returned
        assert result is not None

    def test_completion_on_unparsed_file_returns_empty_list(
        self, fake_ls, comp_fn
    ):
        # Given: a uri that was never parsed
        params = _params("file:///unknown.trlc", line=0, col=0)
        # When: completion is requested against that uri
        result = comp_fn(fake_ls, params)
        # Then: an empty CompletionList is returned, not an error
        assert result is not None
        assert result.items == []

    def test_package_completion_after_space_on_package_keyword(
        self, fake_ls, comp_fn, rsl_path
    ):
        """After 'package ', completions include known package names."""
        # Given: a parsed schema file, cursor right after 'package '
        uri = uri_from_file(rsl_path)
        # token at line=0 col=0 is 'package' keyword, cursor after it
        params = _params(uri, line=0, col=8, trigger_char=" ")
        # When: completion is requested
        result = comp_fn(fake_ls, params)
        # Then: the known package name is suggested
        assert result is not None
        labels = [item.label for item in result.items]
        assert any("Types" in label for label in labels)

    def test_import_completion_after_space_on_import_keyword(
        self, fake_ls, comp_fn, trlc_path
    ):
        """After 'import ', completions include package names."""
        # Given: a parsed instance file, cursor right after 'import '
        uri = uri_from_file(trlc_path)
        params = _params(uri, line=0, col=8, trigger_char=" ")
        # When: completion is requested
        result = comp_fn(fake_ls, params)
        # Then: a CompletionList is returned
        assert result is not None

    def test_package_completion_never_crosses_scope_boundary(
        self, fake_ls_two_scopes, rsl_path
    ):
        """Package-name completion in one scope must not offer a package
        that only exists in a different, independently-parsed scope."""
        # Given: two independently-parsed scopes with different packages
        comp_mod.register(fake_ls_two_scopes)
        comp_fn = fake_ls_two_scopes.get_handler(TEXT_DOCUMENT_COMPLETION)

        uri = uri_from_file(rsl_path)  # in "test-scope" (package Types)
        params = _params(uri, line=0, col=8, trigger_char=" ")
        # When: completion is requested for a file in "test-scope"
        result = comp_fn(fake_ls_two_scopes, params)

        # Then: only test-scope's own package is suggested, never the other scope's
        assert result is not None
        labels = [item.label for item in result.items]
        assert any("Types" in label for label in labels)
        assert not any("OtherTypes" in label for label in labels)


class TestDotTriggerLabelsPackageCompletion:
    """`Package.`-triggered completion (e.g. a cross-package derived_from
    reference like ``OtherPkg.SomeInstance@1``) must offer record
    *instances*, not just types — previously excluded entirely."""

    def test_package_dot_trigger_includes_record_object_instances(self):
        # Given: a Package token whose symbol table holds both a type and
        # a record instance
        record_type = Mock()
        record_type.name = "MyRecord"
        record_instance = Mock()
        record_instance.name = "SomeInstance"

        # __class__ assignment (rather than Mock(spec=...), which would
        # also restrict which attributes the mock accepts) is enough to
        # satisfy the isinstance(tok.ast_link, trlc.ast.Package) check.
        pkg = Mock()
        pkg.__class__ = trlc.ast.Package
        pkg.symbols.table.values.return_value = [record_type, record_instance]

        tok = Mock(ast_link=pkg, kind="IDENTIFIER")

        # When: dot-trigger labels are computed for the package token
        # (tok_index=None disables field-type filtering — covered
        # separately in TestDotTriggerLabelsTypeFiltering)
        labels = comp_mod._dot_trigger_labels(  # pylint: disable=protected-access
            tok, None, None, None, None, []
        )

        # Then: both the type and the instance are offered
        assert labels is not None
        assert "MyRecord" in labels
        assert "SomeInstance" in labels


# ---------------------------------------------------------------------------
# Round 4 fixture: mirrors the real-world shape from BMW's
# score_requirements_model.rsl (the actual file this round's bug was found
# against) — a cross-package array-of-*tuple* field (``derived_from
# FeatPkg.FeatReqId [0..*]``, where FeatReqId is a ``tuple { item FeatReq
# separator @ version Integer }``), with FeatPkg also holding an unrelated
# OtherThing type and a FeatReq *subtype* via extends. Exercised end-to-end
# through the real TRLC parser instead of hand-built mocks.
# ---------------------------------------------------------------------------

FEATPKG_RSL = """\
package FeatPkg

type FeatReq {
    description optional String
}

type SafetyReq extends FeatReq {
    asil optional String
}

type OtherThing {
    x Integer
}

tuple FeatReqId {
    item                     FeatReq
    separator                @
    version                  Integer
}
"""

FEATPKG_TRLC = """\
package FeatPkg

FeatReq Req1 {
    description = "r1"
}

SafetyReq Req2 {
    description = "r2"
    asil = "B"
}

OtherThing Thing1 {
    x = 1
}
"""

COMPPKG_RSL = """\
package CompPkg
import FeatPkg

type CompReq {
    derived_from FeatPkg.FeatReqId [0 .. *]
}
"""

#: Valid content, used to build the real (successful) base parse.
COMPPKG_TRLC_VALID = """\
package CompPkg
import FeatPkg

CompReq MyComp {
    derived_from = [FeatPkg.Req1@1]
}
"""

#: Same file mid-edit: a second array entry started but not finished (no
#: instance name, no closing ']'/'}') — the exact shape that drops a file
#: from TRLC's own vsm.all_files entirely (see resolve_document_tokens).
COMPPKG_TRLC_BROKEN = """\
package CompPkg
import FeatPkg

CompReq MyComp {
    derived_from = [FeatPkg.Req1@1, FeatPkg.
"""

#: Same broken shape, but the package name itself is misspelled.
COMPPKG_TRLC_BROKEN_TYPO = """\
package CompPkg
import FeatPkg

CompReq MyComp {
    derived_from = [FeatPkg.Req1@1, FeatPkk.
"""


def _parse_files(files: dict, tmp_dir):
    """Parse *files* (relative-name -> content) together and return
    ``(stab, all_files)`` from a real TRLC parse, mirroring conftest's
    ``_parse_trlc`` but for an arbitrary multi-package file set."""
    abs_paths = {}
    for name, content in files.items():
        path = os.path.join(str(tmp_dir), name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        abs_paths[name] = path

    mh = trlc.errors.Message_Handler()
    sm = trlc.trlc.Source_Manager(mh=mh, verify_mode=False)
    for path in abs_paths.values():
        sm.register_file(path)
    try:
        sm.process()
    except trlc.errors.TRLC_Error:
        pass

    return sm.stab, sm.all_files, abs_paths


@pytest.fixture(scope="module")
def cross_pkg_parsed(tmp_path_factory):
    """A real parse of FEATPKG_RSL/TRLC + COMPPKG_RSL/TRLC (valid content),
    used as the "last known good" state for the resilience tests below."""
    tmp_dir = tmp_path_factory.mktemp("cross_pkg_parse")
    return _parse_files(
        {
            "featpkg.rsl": FEATPKG_RSL,
            "featpkg.trlc": FEATPKG_TRLC,
            "comppkg.rsl": COMPPKG_RSL,
            "comppkg.trlc": COMPPKG_TRLC_VALID,
        },
        tmp_dir,
    )


def _make_cross_pkg_ls(cross_pkg_parsed, comppkg_buffer_content):
    """A FakeLanguageServer whose scope has featpkg.rsl/trlc and
    comppkg.rsl fully parsed (present in vsm.all_files), but
    comppkg.trlc *dropped* — simulating the real TRLC behaviour where a
    file with a syntax error is excluded from vsm.all_files entirely for
    that parse cycle. ``ls.workspace.get_text_document`` is wired to
    return *comppkg_buffer_content* for comppkg.trlc's uri, so the
    lex-only fallback in completion.py has something to lex."""
    stab, all_files, abs_paths = cross_pkg_parsed
    comppkg_trlc_path = abs_paths["comppkg.trlc"]
    all_files_minus_broken = {
        path: obj
        for path, obj in all_files.items()
        if path != comppkg_trlc_path
    }

    ls = FakeLanguageServer()
    vsm = FakeVSM(stab=stab, all_files=all_files_minus_broken)
    scope_id = "cross-pkg-scope"

    def factory():
        return ParseContext(scope_id=scope_id, result=ParseResult(vsm=vsm))

    comppkg_trlc_uri = uri_from_file(comppkg_trlc_path)
    ls.store.add_file(
        scope_id, comppkg_trlc_uri, comppkg_buffer_content, factory
    )
    ls.store.add_file(
        scope_id,
        uri_from_file(abs_paths["comppkg.rsl"]),
        COMPPKG_RSL,
        factory,
    )
    ls.store.add_file(
        scope_id, uri_from_file(abs_paths["featpkg.rsl"]), FEATPKG_RSL, factory
    )
    ls.store.add_file(
        scope_id,
        uri_from_file(abs_paths["featpkg.trlc"]),
        FEATPKG_TRLC,
        factory,
    )

    def _get_text_document(uri):
        content = comppkg_buffer_content if uri == comppkg_trlc_uri else ""
        return Mock(source=content)

    ls.workspace.get_text_document.side_effect = _get_text_document
    return ls, comppkg_trlc_uri


class TestResilientCompletionAfterSyntaxError:
    """`Package.` completion must keep working while the current file has
    an (even unrelated) syntax error elsewhere — the natural transient
    state while typing a new derived_from entry — because TRLC drops a
    broken file from vsm.all_files entirely, independent of where the
    error is or how far the cursor is from it."""

    def test_dot_trigger_still_completes_via_lex_fallback(
        self, cross_pkg_parsed
    ):
        # Given: comppkg.trlc is mid-edit (unclosed second array entry) and
        # therefore absent from this cycle's vsm.all_files
        ls, uri = _make_cross_pkg_ls(cross_pkg_parsed, COMPPKG_TRLC_BROKEN)
        comp_mod.register(ls)
        comp_fn = ls.get_handler(TEXT_DOCUMENT_COMPLETION)
        # cursor right after "FeatPkg." on the broken line (line 4, 0-based)
        line = COMPPKG_TRLC_BROKEN.splitlines()[4]
        params = _params(uri, line=4, col=len(line), trigger_char=".")

        # When: completion is requested
        result = comp_fn(ls, params)

        # Then: FeatPkg's own instance is still offered, sourced from the
        # scope's persistent symbol table rather than this file's own
        # (currently unparseable) AST
        assert result is not None
        labels = [item.label for item in result.items]
        assert "Req1" in labels

    def test_misspelled_package_returns_empty_not_a_crash(
        self, cross_pkg_parsed
    ):
        # Given: same broken shape, but "FeatPkk" doesn't name a real package
        ls, uri = _make_cross_pkg_ls(
            cross_pkg_parsed, COMPPKG_TRLC_BROKEN_TYPO
        )
        comp_mod.register(ls)
        comp_fn = ls.get_handler(TEXT_DOCUMENT_COMPLETION)
        line = COMPPKG_TRLC_BROKEN_TYPO.splitlines()[4]
        params = _params(uri, line=4, col=len(line), trigger_char=".")

        # When: completion is requested
        result = comp_fn(ls, params)

        # Then: a dead-end lookup degrades to no suggestions, not an error
        assert result is not None
        assert result.items == []


class TestDotTriggerFieldTypeFiltering:
    """`Package.` candidates must be filtered to the enclosing field's
    declared type (respecting `extends` subtypes) when it can be
    resolved — derived_from FeatPkg.FeatReq [0..*] should offer FeatReq/
    SafetyReq instances but not an unrelated OtherThing instance."""

    def test_fallback_path_filters_by_declared_field_type(
        self, cross_pkg_parsed
    ):
        # Given: the same mid-edit broken file as the resilience test above
        ls, uri = _make_cross_pkg_ls(cross_pkg_parsed, COMPPKG_TRLC_BROKEN)
        comp_mod.register(ls)
        comp_fn = ls.get_handler(TEXT_DOCUMENT_COMPLETION)
        line = COMPPKG_TRLC_BROKEN.splitlines()[4]
        params = _params(uri, line=4, col=len(line), trigger_char=".")

        # When: completion is requested
        result = comp_fn(ls, params)

        # Then: FeatReq and its SafetyReq subtype are offered, but the
        # unrelated OtherThing instance is filtered out
        labels = [item.label for item in result.items]
        assert "Req1" in labels  # FeatReq instance
        assert "Req2" in labels  # SafetyReq instance (extends FeatReq)
        assert "Thing1" not in labels  # OtherThing instance — wrong type

    def test_ast_available_path_filters_by_declared_field_type(
        self, cross_pkg_parsed
    ):
        # Given: comppkg.trlc parses cleanly this time (ast_available path)
        stab, all_files, abs_paths = cross_pkg_parsed
        ls = FakeLanguageServer()
        vsm = FakeVSM(stab=stab, all_files=all_files)
        ls.open_scope(
            "clean-scope",
            uri_from_file(abs_paths["comppkg.trlc"]),
            COMPPKG_TRLC_VALID,
            vsm,
        )
        ls.open_scope(
            "clean-scope",
            uri_from_file(abs_paths["featpkg.trlc"]),
            FEATPKG_TRLC,
            vsm,
        )
        comp_mod.register(ls)
        comp_fn = ls.get_handler(TEXT_DOCUMENT_COMPLETION)
        uri = uri_from_file(abs_paths["comppkg.trlc"])
        # cursor right after "FeatPkg." in "derived_from = [FeatPkg.Req1@1]"
        line = COMPPKG_TRLC_VALID.splitlines()[4]
        dot_col = line.index("FeatPkg.") + len("FeatPkg.")
        params = _params(uri, line=4, col=dot_col, trigger_char=".")

        # When: completion is requested
        result = comp_fn(ls, params)

        # Then: same filtering applies when the file parses cleanly
        labels = [item.label for item in result.items]
        assert "Req1" in labels
        assert "Thing1" not in labels


# ---------------------------------------------------------------------------
# Union_Type fixture: a field declared to accept *either* of several record
# types directly (``refs [Alpha, Beta] [0..*]``, no tuple wrapper) — mirrors
# the real score_requirements_model.rsl's ``item [FeatReq, AssumedSystemReq]``
# component inside its "versioned reference" tuples. Filtering must offer
# instances of *any* member type, not just the first.
# ---------------------------------------------------------------------------

PKGX_RSL = """\
package PkgX

type Alpha {
    d optional String
}

type Beta {
    d optional String
}

type Gamma {
    d optional String
}
"""

PKGX_TRLC = """\
package PkgX

Alpha A1 {
    d = "a1"
}

Beta B1 {
    d = "b1"
}

Gamma G1 {
    d = "g1"
}
"""

PKGY_RSL = """\
package PkgY
import PkgX

type Holder {
    refs [PkgX.Alpha, PkgX.Beta] [0 .. *]
}
"""

PKGY_TRLC = """\
package PkgY
import PkgX

Holder H1 {
    refs = [PkgX.A1]
}
"""


@pytest.fixture(scope="module")
def union_type_parsed(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("union_type_parse")
    return _parse_files(
        {
            "pkgx.rsl": PKGX_RSL,
            "pkgx.trlc": PKGX_TRLC,
            "pkgy.rsl": PKGY_RSL,
            "pkgy.trlc": PKGY_TRLC,
        },
        tmp_dir,
    )


class TestDotTriggerUnionTypeFiltering:
    """A field typed as a Union_Type (``[Alpha, Beta]``) must offer
    instances of *any* member type, and still exclude an unrelated type
    (``Gamma``) — the bug this guards against: the filter previously only
    recognized a single ``Record_Type``, so a Union_Type field silently
    fell back to "no expected type resolved" and showed every instance in
    the package unfiltered, Gamma included."""

    def test_offers_instances_of_every_union_member_but_not_others(
        self, union_type_parsed
    ):
        stab, all_files, abs_paths = union_type_parsed
        ls = FakeLanguageServer()
        vsm = FakeVSM(stab=stab, all_files=all_files)
        ls.open_scope(
            "union-scope",
            uri_from_file(abs_paths["pkgy.trlc"]),
            PKGY_TRLC,
            vsm,
        )
        comp_mod.register(ls)
        comp_fn = ls.get_handler(TEXT_DOCUMENT_COMPLETION)
        uri = uri_from_file(abs_paths["pkgy.trlc"])
        line = PKGY_TRLC.splitlines()[4]
        dot_col = line.index("PkgX.") + len("PkgX.")
        params = _params(uri, line=4, col=dot_col, trigger_char=".")

        result = comp_fn(ls, params)

        labels = [item.label for item in result.items]
        assert "A1" in labels  # Alpha instance — first union member
        assert "B1" in labels  # Beta instance — second union member
        assert "G1" not in labels  # Gamma instance — not in the union


class TestResolveExpectedType:
    """Direct unit tests for the enclosing-field resolution helper that
    powers the filtering above."""

    def test_cursor_in_array_literal_resolves_declared_element_type(
        self, cross_pkg_parsed
    ):
        # Given: the raw (lex-only) tokens of the broken buffer, and the
        # real scope's symbol table + CompPkg as cur_pkg
        stab, _all_files, _abs_paths = cross_pkg_parsed
        ls = FakeLanguageServer()
        ls.workspace.get_text_document.side_effect = lambda _uri: Mock(
            source=COMPPKG_TRLC_BROKEN
        )
        raw_tokens = lex_fallback("file:///comppkg.trlc", ls)
        cur_pkg = lookup_by_name(stab, "CompPkg", trlc.ast.Package)
        # index of the trailing '.' — matches what completion() passes as
        # tok_index for a dot-trigger (get_token's exact-match phase always
        # lands on the dot itself, never the identifier before it)
        target_index = len(raw_tokens) - 1
        assert raw_tokens[target_index].kind == "DOT"
        assert raw_tokens[target_index - 1].value == "FeatPkg"

        # When: resolving the expected type at that position
        expected = comp_mod._resolve_expected_type(  # pylint: disable=protected-access
            raw_tokens, target_index, stab, cur_pkg
        )

        # Then: it's FeatReq (the array's element type), not the Array_Type
        # wrapper itself
        assert isinstance(expected, trlc.ast.Record_Type)
        assert expected.name == "FeatReq"

    def test_cursor_outside_any_record_object_returns_none(
        self, cross_pkg_parsed
    ):
        # Given: tokens from a file with no open record object at all
        stab, _all_files, _abs_paths = cross_pkg_parsed
        ls = FakeLanguageServer()
        ls.workspace.get_text_document.side_effect = lambda _uri: Mock(
            source="package CompPkg\nimport FeatPkg\n"
        )
        raw_tokens = lex_fallback("file:///comppkg.rsl", ls)
        cur_pkg = lookup_by_name(stab, "CompPkg", trlc.ast.Package)

        # When: resolving the expected type at the last token
        expected = comp_mod._resolve_expected_type(  # pylint: disable=protected-access
            raw_tokens, len(raw_tokens) - 1, stab, cur_pkg
        )

        # Then: no enclosing '{' exists, so nothing is resolved
        assert expected is None
