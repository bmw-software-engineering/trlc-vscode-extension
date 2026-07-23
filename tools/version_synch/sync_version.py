#!/usr/bin/env python3
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
"""Sync version from VERSION file to package.json, pyproject.toml, and
src/server/BUILD.bazel, or check that they already agree.

--check additionally verifies that py_wheel's hand-maintained `requires`
list in src/server/BUILD.bazel matches pyproject.toml's dependencies.
"""

import os
import re
import sys
from pathlib import Path

# `bazel run` invokes this binary from within its runfiles tree, not the
# repo root - BUILD_WORKING_DIRECTORY is Bazel's own env var for recovering
# the directory the user actually ran `bazel` from. Falls back to cwd for
# direct `python3 sync_version.py` invocations (not run through Bazel).
_ROOT = Path(os.environ.get("BUILD_WORKING_DIRECTORY", "."))

VERSION_FILE = _ROOT / "VERSION"
PACKAGE_JSON = _ROOT / "package.json"
PYPROJECT = _ROOT / "pyproject.toml"
SERVER_BUILD = _ROOT / "src/server/BUILD.bazel"

PACKAGE_JSON_RE = re.compile(r'"version":\s*"([^"]*)"')
PYPROJECT_RE = re.compile(r'version\s*=\s*"([^"]*)"')
SERVER_BUILD_RE = re.compile(r'version\s*=\s*"([^"]*)",')

# py_wheel's `requires` in src/server/BUILD.bazel duplicates pyproject.toml's
# `[project].dependencies` by hand (py_wheel can't read pyproject) - check()
# verifies the two agree so a dep bump in one can't silently ship stale wheel
# metadata. Line-anchored on pyproject to avoid matching optional-dependencies.
PYPROJECT_DEPS_RE = re.compile(
    r"^dependencies\s*=\s*\[(.*?)\]", re.DOTALL | re.MULTILINE
)
SERVER_BUILD_REQUIRES_RE = re.compile(r"requires\s*=\s*\[(.*?)\]", re.DOTALL)
_QUOTED_RE = re.compile(r'"([^"]*)"')


def _read(path):
    return path.read_text(encoding="utf-8")


def _current(path, pattern):
    match = pattern.search(_read(path))
    if not match:
        raise SystemExit(f"Could not find a version in {path}")
    return match.group(1)


def _dep_list(path, block_pattern):
    match = block_pattern.search(_read(path))
    if not match:
        raise SystemExit(f"Could not find a dependency list in {path}")
    return _QUOTED_RE.findall(match.group(1))


def _write_version(path, pattern, version):
    text = _read(path)
    new_text, count = pattern.subn(
        lambda m: m.group(0).replace(m.group(1), version), text, count=1
    )
    if count != 1:
        raise SystemExit(f"Could not find a version in {path}")
    path.write_text(new_text, encoding="utf-8")
    print(f"  Updated {path}")


def sync(version):
    """Write *version* into package.json, pyproject.toml, and BUILD.bazel."""
    print(f"Syncing version: {version}")
    _write_version(PACKAGE_JSON, PACKAGE_JSON_RE, version)
    _write_version(PYPROJECT, PYPROJECT_RE, version)
    _write_version(SERVER_BUILD, SERVER_BUILD_RE, version)
    print("Version synced successfully")


def check(version):
    """Exit non-zero (writing nothing) if any file disagrees with *version*."""
    mismatches = []
    for path, pattern in (
        (PACKAGE_JSON, PACKAGE_JSON_RE),
        (PYPROJECT, PYPROJECT_RE),
        (SERVER_BUILD, SERVER_BUILD_RE),
    ):
        found = _current(path, pattern)
        if found != version:
            mismatches.append(f"  {path}: {found} != {version} (VERSION)")
    pyproject_deps = _dep_list(PYPROJECT, PYPROJECT_DEPS_RE)
    wheel_deps = _dep_list(SERVER_BUILD, SERVER_BUILD_REQUIRES_RE)
    if sorted(pyproject_deps) != sorted(wheel_deps):
        mismatches.append(
            "  py_wheel requires != pyproject dependencies:\n"
            f"    pyproject.toml: {pyproject_deps}\n"
            f"    BUILD.bazel:    {wheel_deps}"
        )
    if mismatches:
        print("Version mismatch:")
        print("\n".join(mismatches))
        raise SystemExit(1)
    print(f"All files agree with VERSION ({version})")


def check_tag(tag, version):
    """Exit non-zero if *tag* (a `vX.Y.Z` git ref) doesn't match *version*."""
    tag_version = tag[1:] if tag.startswith("v") else tag
    if tag_version != version:
        raise SystemExit(
            f"Tag {tag!r} (version {tag_version}) does not match "
            f"VERSION ({version})"
        )
    print(f"Tag {tag!r} matches VERSION ({version})")


def main(argv):
    """Dispatch to sync/check/check_tag based on argv."""
    version = VERSION_FILE.read_text(encoding="utf-8").strip()

    if not argv:
        sync(version)
    elif argv[0] == "--check":
        check(version)
    elif argv[0] == "--check-tag":
        if len(argv) != 2:
            raise SystemExit("--check-tag requires a <ref> argument")
        check_tag(argv[1], version)
    else:
        raise SystemExit(f"Unknown argument: {argv[0]}")


if __name__ == "__main__":
    main(sys.argv[1:])
