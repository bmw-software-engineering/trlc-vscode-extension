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
"""Shared LSP content strings for integration tests.

These strings are the *text content* sent to the language server via
``textDocument/didOpen`` and ``textDocument/didChange`` — they are
decoupled from the on-disk fixture files (whose paths are used only for URI
identity).  In WORKSPACE mode the server uses the LSP-provided buffer, not
the disk file, so content strings can be minimal (no license headers needed).

The one exception is :data:`RSL_TEXT`, which is read from
``tests/fixtures/types.rsl`` so that its token positions (which include the
19-line license header) stay consistent with the comments in
``test_navigation.py`` that annotate specific line numbers.

Token positions for BROKEN_REF_TRLC_TEXT (0-based, no header):
  line=2  col=8  → 'CompB'        (Record_Object declaration)
  line=3  col=20 → 'DoesNotExist' (unresolvable reference — triggers bug)
  line=3  col=34 → 'FeatA'        (forward reference inside derived_from)
  line=6  col=4  → 'FeatA'        (Record_Object declaration)

Token positions for DESCRIBED_TRLC_TEXT (0-based, no header):
  line=2  col=5  → 'doc_item'     (Record_Object with description field)
"""

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

# ---------------------------------------------------------------------------
# Core Types package (types.rsl / sample.trlc equivalents)
# ---------------------------------------------------------------------------

#: Full content of tests/fixtures/types.rsl, including its 19-line license
#: header.  Used wherever tests need token positions that match test_navigation.py
#: line-number annotations (e.g. line=27 for 'MyRecord', line=21 for 'Color').
RSL_TEXT = (FIXTURES_DIR / "types.rsl").read_text(encoding="utf-8")

#: Minimal valid TRLC instance file for the Types package.
#:
#: Token positions (0-based, no header):
#:   line=2  col=9   → 'example'   (Record_Object declaration)
TRLC_TEXT = """\
package Types

MyRecord example {
    x = 1
    y = 2
    c = Color.Red
}
"""

#: TRLC content with a missing required field — triggers a diagnostics error.
#: (``MyRecord`` requires ``x``, ``y``, and ``c``; only ``x`` is provided.)
INVALID_TRLC_TEXT = """\
package Types

MyRecord bad_instance {
    x = 1
}
"""

#: RSL schema referencing an undeclared type — the error is reported against
#: the .rsl URI itself (see test_diagnostics.py).
INVALID_RSL_TEXT = "package Types\n\ntype Bad { x UnknownType }\n"

# ---------------------------------------------------------------------------
# Completion trigger content
# ---------------------------------------------------------------------------

#: TRLC file where the cursor is positioned immediately after the dot in
#: ``Color.`` — used to trigger enum-literal dot-completion.
#:
#: Token positions (0-based, no header):
#:   line=5  col=15  → after the '.' on 'c = Color.'
TRLC_ENUM_TRIGGER = """\
package Types

MyRecord wip {
    x = 1
    y = 2
    c = Color.
}
"""

# ---------------------------------------------------------------------------
# Code-action content
# ---------------------------------------------------------------------------

#: RSL file with a misspelled builtin function name in a ``checks`` block.
#: ``startsXwith`` reliably triggers TRLC's "unknown symbol X, did you mean Y?"
#: diagnostic, which is the shape ``code_actions.py``'s DID_YOU_MEAN_RE matches.
CHECK_TYPO_RSL_TEXT = """\
package Types

type MyType {
    name String
}

checks MyType {
    startsXwith(name, "Q"), error "name must start with Q", name
}
"""

# ---------------------------------------------------------------------------
# Broken-reference / forward-reference regression fixtures
# ---------------------------------------------------------------------------

#: RSL content for the BrokenRef package.
#:
#: Token positions (0-based, no header):
#:   line=2  col=5  → 'Req'     (Record_Type declaration)
#:   line=6  col=5  → 'CompReq' (Record_Type declaration)
BROKEN_REF_RSL_TEXT = """\
package BrokenRef

type Req {
    description String
}

type CompReq extends Req {
    derived_from Req [0 .. *]
}
"""

#: TRLC content for the BrokenRef package.  ``CompB`` is declared *before*
#: ``FeatA`` to reproduce the forward-reference + unresolvable-name scenario
#: that caused the original hover / goto-definition / find-references bugs.
#: ``CompB`` intentionally omits its required ``description`` field; this
#: parse error causes TRLC to skip the deferred ``resolve_record_references``
#: pass for the *whole file*, leaving ``FeatA``\'s ``.target`` as ``None``
#: even though ``FeatA`` is a valid object in scope.  The ``DoesNotExist``
#: entry in the ``derived_from`` list is the per-element unresolvable name
#: that would abort the deferred pass mid-list if it ran.
#:
#: Token positions (0-based, no header):
#:   line=2  col=8  → \'CompB\'        (Record_Object declaration)
#:   line=3  col=20 → \'DoesNotExist\'  (unresolvable name in derived_from)
#:   line=3  col=34 → \'FeatA\'         (forward reference inside derived_from)
#:   line=6  col=4  → \'FeatA\'         (Record_Object declaration)
BROKEN_REF_TRLC_TEXT = """\
package BrokenRef

CompReq CompB {
    derived_from = [DoesNotExist, FeatA]
}

Req FeatA {
    description = "A real feature"
}
"""

# ---------------------------------------------------------------------------
# Hover-on-instance fixtures (Record_Object with description field)
# ---------------------------------------------------------------------------

#: Minimal RSL defining a type whose schema includes a ``description`` field,
#: so that instances of it can be hovered to retrieve their description.
#:
#: Token positions (0-based, no header):
#:   line=2  col=5  → 'Item' (Record_Type declaration)
DESCRIBED_RSL_TEXT = """\
package Types

type Item {
    description String
}
"""

#: Minimal TRLC instance of the ``Item`` type with ``description`` filled in.
#:
#: Token positions (0-based, no header):
#:   line=2  col=5  → 'doc_item' (Record_Object declaration with description)
DESCRIBED_TRLC_TEXT = """\
package Types

Item doc_item {
    description = "Explains what this item does"
}
"""

# ---------------------------------------------------------------------------
# Completion-trigger content (partial buffers captured mid-edit)
# ---------------------------------------------------------------------------

#: Instance buffer stopped right after ``import `` — triggers import-target
#: package-name completion (cursor at line=1 col=7).
TRLC_IMPORT_TRIGGER = "package Types\nimport "

#: Instance buffer stopped right after a record's opening ``{`` — triggers
#: record-field completion (cursor at line=2 col=14).
TRLC_RECORD_BRACE_TRIGGER = "package Types\n\nMyRecord wip {"

#: Instance buffer whose last field value is an unclosed string literal —
#: used to assert completion inside a string yields no schema-aware
#: suggestions (cursor at line=5 col=11, inside the ``"inside`` literal).
TRLC_IN_STRING = (
    'package Types\n\nMyRecord wip {\n    x = 1\n    y = 2\n    c = "inside\n'
)

# ---------------------------------------------------------------------------
# BAZEL-mode fixtures
#
# BAZEL mode reads files from disk (not LSP buffers); test_bazel_mode.py
# writes these into a throwaway fake-Bazel workspace.  The package is
# deliberately a *minimal* ``Types`` (single ``x`` field) — unrelated to the
# richer ``Types`` schema in types.rsl above — hence the ``BAZEL_`` prefix.
# ---------------------------------------------------------------------------

#: Minimal spec written to the fake workspace's ``pkg/types.rsl``.
BAZEL_TYPES_RSL = """\
package Types

type MyRecord {
    x Integer
}
"""

#: Valid instance written to the fake workspace's ``pkg/example.trlc``.
BAZEL_VALID_TRLC = """\
package Types

MyRecord example {
    x = 1
}
"""

#: Invalid instance (missing required ``x``) — proves a Bazel-scoped target
#: still surfaces real error diagnostics, not "clean by accident".
BAZEL_INVALID_TRLC = "package Types\n\nMyRecord bad {\n}\n"
