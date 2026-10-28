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
"""Find unopened .trlc files that declare a symbol TRLC reports unknown.

In parse mode WORKSPACE only open .trlc files are parsed, so a record
declared in a file that is not open is an "unknown symbol" error. This
text-only scan (no parse) names the files that would resolve it, for
the preview's hint and the editor's quick fix.
"""

import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

from server.parse_guard import normalize_fs_path
from server.server_config import ParseMode
from server.token_utils import decode_scope_id, path_from_uri

#: TRLC's "unknown symbol X" (optionally ", did you mean Y?").
UNKNOWN_SYMBOL_RE = re.compile(r"^unknown symbol ([A-Za-z]\w*)")
# Strings and comments are blanked out before looking for declarations.
_NOISE_RE = re.compile(
    r"'''.*?'''|\"\"\".*?\"\"\"|\"(?:\.|[^\"\\n])*\"|//[^\n]*|/\*.*?\*/",
    re.S,
)
_PACKAGE_RE = re.compile(r"\bpackage\s+([A-Za-z][\w.]*)")
# A record object declaration: "[Pkg.]Type name {".
_RECORD_RE = re.compile(
    r"\b[A-Za-z]\w*(?:\.[A-Za-z]\w*)?\s+([A-Za-z]\w*)\s*\{"
)
# "Pkg." right before the unknown name.
_QUALIFIER_RE = re.compile(r"([A-Za-z]\w*)\s*\.\s*$")

#: path -> (mtime_ns, size, package, declared record names)
_CACHE: Dict[str, Tuple[int, int, Optional[str], frozenset]] = {}


def package_of(text: str) -> Optional[str]:
    """Package declared by a TRLC source text, if any.

    Args:
        text: TRLC source text.

    Returns:
        The package name, or None if no declaration is found.
    """
    match = _PACKAGE_RE.search(_NOISE_RE.sub(" ", text))
    return match.group(1) if match else None


def _declarations(path: str) -> Tuple[Optional[str], frozenset]:
    """(package, record names) declared in the .trlc file at *path*.

    Results are cached per path until its modification time or size
    changes.

    Args:
        path: File system path of the .trlc file.

    Returns:
        A tuple of the package name (None if absent) and the frozen
        set of declared record object names.
    """
    stat = os.stat(path)
    cached = _CACHE.get(path)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2], cached[3]
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = _NOISE_RE.sub(" ", handle.read())
    match = _PACKAGE_RE.search(text)
    package = match.group(1) if match else None
    names = frozenset(m.group(1) for m in _RECORD_RE.finditer(text))
    _CACHE[path] = (stat.st_mtime_ns, stat.st_size, package, names)
    return package, names


def _trlc_files(folder: str, exclude_patterns: Iterable[str]) -> List[str]:
    """All .trlc files under *folder*, skipping excluded directories.

    Args:
        folder: Root folder to walk.
        exclude_patterns: Regular expressions matched against directory
            names; invalid ones are ignored. ``bazel-*`` directories
            are always skipped.

    Returns:
        The file paths in sorted directory-walk order.
    """
    patterns = [re.compile(r"^bazel-.*$")]
    for pattern in exclude_patterns:
        try:
            patterns.append(re.compile(pattern))
        except re.error:
            continue  # reported where the patterns are applied to parsing
    found = []
    for path, dirs, files in os.walk(folder):
        dirs[:] = sorted(
            d for d in dirs if not any(p.match(d) for p in patterns)
        )
        found.extend(
            os.path.join(path, f) for f in sorted(files) if f.endswith(".trlc")
        )
    return found


def unknown_symbol(text: str, diagnostic) -> Optional[Tuple[str, str]]:
    """(name, package) of an "unknown symbol" *diagnostic* in *text*.

    The package is the qualifier written before the name (``Pkg.name``)
    or, unqualified, the package of *text* itself. None if the
    diagnostic is not an unknown symbol or the package is unknown.

    Args:
        text: Source text of the file the diagnostic belongs to.
        diagnostic: LSP diagnostic with ``message`` and ``range``
            (0-based line and character).

    Returns:
        A tuple ``(name, package)``, or None.
    """
    match = UNKNOWN_SYMBOL_RE.match(diagnostic.message)
    if match is None:
        return None
    lines = text.split("\n")
    start = diagnostic.range.start
    before = (
        lines[start.line][: start.character] if start.line < len(lines) else ""
    )
    qualifier = _QUALIFIER_RE.search(before)
    package = qualifier.group(1) if qualifier else package_of(text)
    return (match.group(1), package) if package else None


def scope_label(ls, uri: str, path: str) -> str:
    """*path* relative to the scope folder of *uri*, with ``/``.

    Args:
        ls: Language server (provides the context store).
        uri: URI of the file whose scope is used.
        path: File system path to label.

    Returns:
        The path relative to the scope folder using ``/``, or just the
        file name if *uri* has no scope.
    """
    scope_id = ls.store.scope_id_for_uri(uri)
    if scope_id is None:
        return os.path.basename(path)
    folder = path_from_uri(decode_scope_id(scope_id)[0])
    return os.path.relpath(path, folder).replace(os.sep, "/")


def declaring_files(
    ls, uri: str, text: str, diagnostics
) -> "Dict[str, List[str]]":
    """Unopened .trlc files declaring the unknown symbols of *diagnostics*.

    Maps each file path to the names it declares, in file order. Empty
    unless *uri* is parsed in parse mode WORKSPACE (the only mode that
    leaves .trlc files of the scope unparsed).

    Args:
        ls: Language server (provides config, store and open files).
        uri: URI of the file with the diagnostics.
        text: Source text of that file.
        diagnostics: Diagnostics of the file; only unknown-symbol ones
            are considered.

    Returns:
        A dict from file path to the wanted names it declares; empty
        if there is nothing to find or the mode is not WORKSPACE.
    """
    config = ls.get_config_for_uri(uri)
    scope_id = ls.store.scope_id_for_uri(uri)
    if config.parse_mode != ParseMode.WORKSPACE or scope_id is None:
        return {}
    wanted = [
        found
        for found in (unknown_symbol(text, d) for d in diagnostics)
        if found is not None
    ]
    if not wanted:
        return {}
    opened = {normalize_fs_path(path_from_uri(u)) for u in ls.fh.snapshot()}
    folder = path_from_uri(decode_scope_id(scope_id)[0])
    result: Dict[str, List[str]] = {}
    for path in _trlc_files(folder, config.exclude_patterns):
        if normalize_fs_path(path) in opened:
            continue
        try:
            package, names = _declarations(path)
        except OSError:
            continue
        for name, wanted_package in wanted:
            if package == wanted_package and name in names:
                result.setdefault(path, [])
                if name not in result[path]:
                    result[path].append(name)
    return result
