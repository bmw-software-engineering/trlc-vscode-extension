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
"""Fake `bazel` executable standing in for the real binary in
tests/integration/test_bazel_mode.py.

Built as a real py_binary (see the `:fake_bazel` target in tests/BUILD.bazel)
specifically so Windows gets a native launcher exe for it. Two things that
look simpler don't work there: a raw `.py` file has no shebang support on
Windows (`%1 is not a valid Win32 application`), and a `.bat` wrapper gets
silently re-parsed by cmd.exe (Windows' CreateProcess special-cases
`.bat`/`.cmd` by re-invoking them through cmd.exe with the *original*
command line) - whose tokenizer treats the unescaped `(`, `)`, and `|` in a
real `kind(...)` / `deps(...)` query string as grouping/pipe operators,
corrupting the call before this script would ever see it (observed as
bazel.py's subprocess call failing with rc=255, cmd's "was unexpected at
this time" exit code). A genuine PE binary sidesteps this entirely -
CreateProcess hands it argv directly, no shell involved.

Reads its "queried workspace" from the process's own cwd rather than argv
or an env var - BazelClient (src/server/bazel.py) always runs with
cwd=workspace_root, so this needs no extra wiring, and keeps the whole
surface free of anything a shell could reinterpret.
"""

import sys
from pathlib import Path

# Canned `bazel query 'kind(...)' --output=xml` result (used by
# find_trlc_targets() for the workspace-wide target listing / file->target
# reverse mapping): one requirement target whose source is example.trlc,
# depending on a specification target whose source is types.rsl.
_QUERY_XML = """<?xml version="1.0"?>
<query_result>
  <rule class="feature_requirements" name="//pkg:feature_requirements">
    <list name="srcs">
      <label value="//pkg:example.trlc"/>
    </list>
    <list name="deps">
      <label value="//pkg:base_spec"/>
    </list>
  </rule>
  <rule class="_trlc_specification" name="//pkg:base_spec">
    <list name="srcs">
      <label value="//pkg:types.rsl"/>
    </list>
  </rule>
</query_result>
"""

# Canned `bazel query 'deps(...)' --output=xml` result (used by
# files_for_target()'s per-target closure resolution): the same two files
# as source-file elements with resolved locations - proves BazelScopeStrategy
# pulls in the transitive dep's file via the real deps()-query path, not
# just a target's own srcs.
_DEPS_XML = """<?xml version="1.0"?>
<query_result>
  <rule class="feature_requirements" name="//pkg:feature_requirements"/>
  <source-file name="//pkg:example.trlc" location="{example_trlc}:1:1"/>
  <rule class="_trlc_specification" name="//pkg:base_spec"/>
  <source-file name="//pkg:types.rsl" location="{types_rsl}:1:1"/>
</query_result>
"""

#: Marker file (see test_bazel_mode.py's failing_bazel_workspace fixture)
#: whose presence in the queried workspace root makes every query fail,
#: simulating a broken Bazel setup.
FAIL_MARKER_NAME = ".fake_bazel_fail"


def main() -> int:
    """Serve one canned query response, or fail if FAIL_MARKER_NAME is present."""
    cwd = Path.cwd()
    if (cwd / FAIL_MARKER_NAME).exists():
        sys.stderr.write(
            "ERROR: no such package 'pkg': BUILD file not found\n"
        )
        return 1

    query = next((a for a in sys.argv if "kind(" in a or "deps(" in a), "")
    if "deps(" in query:
        pkg = cwd / "pkg"
        sys.stdout.write(
            _DEPS_XML.format(
                example_trlc=str(pkg / "example.trlc"),
                types_rsl=str(pkg / "types.rsl"),
            )
        )
    else:
        sys.stdout.write(_QUERY_XML)
    return 0


if __name__ == "__main__":
    sys.exit(main())
