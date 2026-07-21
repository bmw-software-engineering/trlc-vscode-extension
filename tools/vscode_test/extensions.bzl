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

"""Pins the VSCode desktop build used by //tests/e2e's smoke test.

`@vscode/test-electron` (the e2e harness, a normal npm devDependency - see
package.json) normally downloads this itself at test-run time via
`downloadAndUnzipVSCode()`. That's unusable inside a hermetic, sandboxed
Bazel test (no network at test-execution time). Instead this fetches the
same tarball once, during Bazel's module-resolution phase (network allowed
there, cached by the repository cache the same way `pip.parse` and
`node.toolchain` already are in MODULE.bazel) - the identical mechanism
category as this repo's existing Node toolchain fetch, not an ad hoc
one-off download.

Exposes `@vscode_test_linux_x64//:code` (an alias to the `code` launcher
binary inside the extracted `VSCode-linux-x64/` directory) and
`@vscode_test_linux_x64//:tree` (the full extracted tree, needed as `data`
since the launcher needs its sibling resources/locales/etc alongside it).
Same shape for `@vscode_test_darwin_arm64` (macOS CI runners are Apple
Silicon) and `@vscode_test_win32_x64` - see `tests/e2e/BUILD.bazel`'s
`select()` on `@platforms//os` for how the right one gets picked per host
platform.

To bump the pinned version (repeat per platform):
  1. `curl -sI https://update.code.visualstudio.com/<version>/<platform>/stable`
     (`<platform>` e.g. `linux-x64`, `darwin-arm64`) and follow the
     `location` redirect to get the concrete tarball/zip URL.
  2. Use that same response's `x-sha256` header for the checksum below
     (or hash the downloaded archive to double check).
"""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

# VSCode 1.95.3, linux-x64.
_LINUX_URL = "https://vscode.download.prss.microsoft.com/dbazure/download/stable/f1a4fb101478ce6ec82fe9627c43efbf9e98c813/code-stable-x64-1731511985.tar.gz"
_LINUX_SHA256 = "881c6dce9f9b18bdeaa0020197501be3808c6e23c26baa0ba905b0bc84175b46"

# VSCode 1.95.3, darwin-arm64.
_DARWIN_ARM64_URL = "https://vscode.download.prss.microsoft.com/dbazure/download/stable/f1a4fb101478ce6ec82fe9627c43efbf9e98c813/VSCode-darwin-arm64.zip"
_DARWIN_ARM64_SHA256 = "fe01f564777afb75703e748ada00f1204f0495be54947d6ffb513e5a5eb16a08"

# VSCode 1.95.3, win32-x64. The "-archive" variant (not the installer) -
# unzips flat, with Code.exe directly at the archive root.
_WIN32_X64_URL = "https://vscode.download.prss.microsoft.com/dbazure/download/stable/f1a4fb101478ce6ec82fe9627c43efbf9e98c813/VSCode-win32-x64-1.95.3.zip"
_WIN32_X64_SHA256 = "6d6fcd71fee97a3e110770032d7c8494145f15a92598813f031ceb09449c3f1d"

def _vscode_test_impl(module_ctx):
    http_archive(
        name = "vscode_test_linux_x64",
        url = _LINUX_URL,
        sha256 = _LINUX_SHA256,
        build_file = Label("//tools/vscode_test:vscode_test.BUILD.bazel"),
    )
    http_archive(
        name = "vscode_test_darwin_arm64",
        url = _DARWIN_ARM64_URL,
        sha256 = _DARWIN_ARM64_SHA256,
        build_file = Label("//tools/vscode_test:vscode_test_darwin.BUILD.bazel"),
    )
    http_archive(
        name = "vscode_test_win32_x64",
        url = _WIN32_X64_URL,
        sha256 = _WIN32_X64_SHA256,
        build_file = Label("//tools/vscode_test:vscode_test_win32.BUILD.bazel"),
    )
    return module_ctx.extension_metadata(reproducible = True)

vscode_test = module_extension(implementation = _vscode_test_impl)
