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
"""Unit tests for trlc_lsp.handlers.hover.

Token positions (0-based LSP lines/cols) in types.rsl:
  line=2 col=5  → 'MyRecord'  (Record_Type)
  line=3 col=4  → 'x'         (Composite_Component)
  line=8 col=4  → 'Color'     (Enumeration_Type)
  line=9 col=4  → 'Red'       (Enumeration_Literal_Spec)
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

# pylint: disable=redefined-outer-name  # pytest fixture names must match the fixture function name for injection

import os

import pytest
import trlc.errors
import trlc.trlc
from lsprotocol.types import (
    TEXT_DOCUMENT_HOVER,
    Position,
    TextDocumentIdentifier,
    TextDocumentPositionParams,
)

from server.handlers import hover as hover_mod
from server.server_config import ServerConfig
from server.token_utils import uri_from_file
from tests.unit.handlers.conftest import FakeLanguageServer, FakeVSM


def _hover_params(uri, line, col):
    return TextDocumentPositionParams(
        text_document=TextDocumentIdentifier(uri=uri),
        position=Position(line=line, character=col),
    )


@pytest.fixture
def hover_fn(fake_ls):
    """Hover fn."""
    hover_mod.register(fake_ls)
    return fake_ls.get_handler(TEXT_DOCUMENT_HOVER)


class TestHover:
    def test_hover_on_record_type_returns_hover_object(
        self, fake_ls, hover_fn, rsl_path
    ):
        # Given: a parsed schema file, cursor on the 'MyRecord' type
        uri = uri_from_file(rsl_path)
        params = _hover_params(uri, line=2, col=5)  # 'MyRecord'
        # When: hover is requested
        result = hover_fn(fake_ls, params)
        # Then: a hover object with a range is returned
        assert result is not None
        assert result.range is not None

    def test_hover_on_enum_type_returns_hover_object(
        self, fake_ls, hover_fn, rsl_path
    ):
        # Given: a parsed schema file, cursor on the 'Color' enum type
        uri = uri_from_file(rsl_path)
        params = _hover_params(uri, line=8, col=4)  # 'Color'
        # When: hover is requested
        result = hover_fn(fake_ls, params)
        # Then: a hover object with a range is returned
        assert result is not None
        assert result.range is not None

    def test_hover_on_builtin_returns_none(self, fake_ls, hover_fn, rsl_path):
        """Builtin types like Integer have no user-defined description."""
        # Given: a parsed schema file, cursor on the builtin 'Integer' type
        uri = uri_from_file(rsl_path)
        params = _hover_params(
            uri, line=3, col=6
        )  # 'Integer' (Builtin_Integer)
        # When: hover is requested
        result = hover_fn(fake_ls, params)
        # Then: no hover object is returned
        assert result is None

    def test_hover_on_unparsed_file_returns_none(self, fake_ls, hover_fn):
        # Given: a uri that was never parsed
        params = _hover_params("file:///nonexistent.rsl", line=0, col=0)
        # When: hover is requested against that uri
        result = hover_fn(fake_ls, params)
        # Then: no hover object is returned
        assert result is None

    def test_hover_on_keyword_returns_none(self, fake_ls, hover_fn, rsl_path):
        """KEYWORD tokens have no ast_link and must return None."""
        # Given: a parsed schema file, cursor on the 'type' keyword
        uri = uri_from_file(rsl_path)
        params = _hover_params(uri, line=2, col=0)  # 'type' keyword
        # When: hover is requested
        result = hover_fn(fake_ls, params)
        # Then: no hover object is returned
        assert result is None


# ---------------------------------------------------------------------------
# Record_Object hover: a reference/declaration's own "description" field
# (regression coverage — Record_Object has no .description Python attribute
# at all, unlike Record_Type/Tuple_Type/Composite_Component/Enumeration_Type,
# so hovering over an instance name previously always returned None).
# ---------------------------------------------------------------------------

REQ_RSL = """\
package ReqPkg

type Req {
    description String
    summary optional String
}

type CompReq extends Req {
    derived_from Req
}
"""

REQ_TRLC = """\
package ReqPkg

Req FeatA {
    description = "Feature A shall work."
    summary = "Short version."
}

CompReq CompB {
    description = "Comp B shall do Y."
    derived_from = FeatA
}
"""


@pytest.fixture(scope="module")
def req_parsed(tmp_path_factory):
    """A real parse of REQ_RSL/REQ_TRLC: a Req type with a description
    field, one FeatA instance, and a CompReq instance whose derived_from
    references FeatA by name."""
    tmp_dir = tmp_path_factory.mktemp("hover_req_parse")
    rsl_file = os.path.join(str(tmp_dir), "req.rsl")
    trlc_file = os.path.join(str(tmp_dir), "req.trlc")
    with open(rsl_file, "w", encoding="utf-8") as f:
        f.write(REQ_RSL)
    with open(trlc_file, "w", encoding="utf-8") as f:
        f.write(REQ_TRLC)

    mh = trlc.errors.Message_Handler()
    sm = trlc.trlc.Source_Manager(mh=mh, verify_mode=False)
    sm.register_file(rsl_file)
    sm.register_file(trlc_file)
    sm.process()

    return sm.stab, sm.all_files, trlc_file


@pytest.fixture
def req_ls(req_parsed):
    stab, all_files, trlc_file = req_parsed
    vsm = FakeVSM(stab=stab, all_files=all_files)
    ls = FakeLanguageServer()
    ls.open_scope("req-scope", uri_from_file(trlc_file), REQ_TRLC, vsm)
    return ls, trlc_file


class TestHoverOnRecordObject:
    def test_hover_on_reference_shows_target_objects_description(self, req_ls):
        """Hovering the name used in a cross-reference (e.g. derived_from
        = FeatA) must show FeatA's own description field — not nothing,
        which is what Record_Object.description (a nonexistent Python
        attribute) used to silently produce."""
        # Given: a real parse, cursor on "FeatA" inside CompB's derived_from
        ls, trlc_file = req_ls
        hover_mod.register(ls)
        hover_fn = ls.get_handler(TEXT_DOCUMENT_HOVER)
        uri = uri_from_file(trlc_file)
        line = REQ_TRLC.splitlines()[9]  # '    derived_from = FeatA'
        col = line.index("FeatA")

        # When: hover is requested
        result = hover_fn(ls, _hover_params(uri, line=9, col=col))

        # Then: FeatA's own description is shown
        assert result is not None
        assert result.contents == "Feature A shall work."

    def test_hover_on_declaration_shows_its_own_description(self, req_ls):
        # Given: a real parse, cursor on "FeatA" at its own declaration
        ls, trlc_file = req_ls
        hover_mod.register(ls)
        hover_fn = ls.get_handler(TEXT_DOCUMENT_HOVER)
        uri = uri_from_file(trlc_file)
        line = REQ_TRLC.splitlines()[2]  # 'Req FeatA {'
        col = line.index("FeatA")

        # When: hover is requested
        result = hover_fn(ls, _hover_params(uri, line=2, col=col))

        # Then: its own description is shown
        assert result is not None
        assert result.contents == "Feature A shall work."

    def test_custom_description_component_name(self, req_ls):
        """trlcServer.hover.descriptionComponent lets a schema that doesn't
        use "description" as its summary field name still get hover
        previews — genericizes what would otherwise be a hardcoded
        assumption about one project's naming convention."""
        # Given: a config naming "summary" instead of the default "description"
        ls, trlc_file = req_ls
        ls.config = ServerConfig(hover_description_component="summary")
        hover_mod.register(ls)
        hover_fn = ls.get_handler(TEXT_DOCUMENT_HOVER)
        uri = uri_from_file(trlc_file)
        line = REQ_TRLC.splitlines()[9]  # '    derived_from = FeatA'
        col = line.index("FeatA")

        # When: hover is requested
        result = hover_fn(ls, _hover_params(uri, line=9, col=col))

        # Then: the configured component's value is shown, not "description"'s
        assert result is not None
        assert result.contents == "Short version."

    def test_empty_description_component_disables_the_feature(self, req_ls):
        """An empty trlcServer.hover.descriptionComponent falls all the way
        through to the pre-existing AttributeError-caught path — same
        "nothing to show" result as before this feature existed, not an
        error."""
        # Given: the feature explicitly disabled
        ls, trlc_file = req_ls
        ls.config = ServerConfig(hover_description_component="")
        hover_mod.register(ls)
        hover_fn = ls.get_handler(TEXT_DOCUMENT_HOVER)
        uri = uri_from_file(trlc_file)
        line = REQ_TRLC.splitlines()[9]  # '    derived_from = FeatA'
        col = line.index("FeatA")

        # When: hover is requested
        result = hover_fn(ls, _hover_params(uri, line=9, col=col))

        # Then: no hover is shown
        assert result is None
