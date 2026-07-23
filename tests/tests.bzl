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

"""Macro: one py_test per test file, instead of one per suite.

BUILD files can't contain `for` loops or function defs, so this generator
lives here and gets `load()`-ed from tests/BUILD.bazel.
"""

load("@rules_python//python:defs.bzl", "py_test")

def pytest_targets(test_files, size = None, extra_deps = [], extra_data = []):
    """Declare one py_test per file in *test_files*.

    Each target's only real `srcs` is the shared `pytest_main.py` runner —
    the test file itself (and every `conftest.py` alongside it) is already
    on the runfiles/import path via the `:tests` py_library dep, so it
    doesn't need to be duplicated into `srcs` here.

    Args:
      test_files: list of test file paths, relative to the tests/ package
        (i.e. exactly what glob() returns when called from tests/BUILD.bazel).
      size: py_test `size` attr, forwarded as-is (e.g. "medium" for the
        slower integration/pytest-lsp suite).
      extra_deps: additional `@pip//...` deps beyond `@pip//pytest`, e.g.
        `@pip//pytest_lsp` for the integration suite.
      extra_data: additional runtime `data` deps applied to every target in
        this call, e.g. `//tests/integration:fake_bazel` for the integration
        suite - shared across the whole group rather than per-file, since
        BUILD files can't single out one generated target for extra deps.

    Returns:
      The list of generated target names, for callers to fold into a
      `test_suite`.
    """
    names = []
    for f in test_files:
        name = f.replace("/", "_")[:-3]
        names.append(name)
        py_test(
            name = name,
            size = size,
            srcs = ["pytest_main.py"],
            main = "pytest_main.py",
            # Runfiles preserve the full repo-relative path ("tests/unit/...")
            # even though glob() (called from tests/BUILD.bazel) returns
            # paths relative to the tests/ package ("unit/...") - the test's
            # actual cwd is the runfiles root, one level up from tests/.
            args = ["tests/" + f],
            data = ["//:pyproject_toml"] + native.glob(["fixtures/**"]) + extra_data,
            deps = [":tests", "@pip//pytest"] + extra_deps,
        )
    return names
