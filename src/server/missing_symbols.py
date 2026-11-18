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
"""Find unparsed .trlc files that declare a symbol TRLC reports unknown.

In parse mode WORKSPACE only open .trlc files are parsed, so a record
declared in a file that is not open is an "unknown symbol" error. This
text-only scan (no parse) names the files that would resolve it, for
the preview's hint and the editor's quick fix. Parse modes DIRECTORY and
BAZEL leave files outside the directory or Bazel target unparsed; the
same scan names those for the preview's hint.
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
    r"'''.*?'''|\"\"\".*?\"\"\"|\"(?:\\.|[^\"\\\n])*\"|//[^\n]*|/\*.*?\*/",
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
#: (folder, exclude patterns) -> its .trlc files, kept until
#: :func:`forget_files` so a request does not walk the folder again.
_FILES: Dict[Tuple[str, Tuple[str, ...]], List[str]] = {}
#: Bumped by :func:`forget_files`; a walk that began before is not kept.
_FILES_EPOCH = [0]


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


def forget_files() -> None:
    """Drop the cached .trlc file lists.

    Called when files changed on disk (see
    ``lifecycle.on_watched_files_change``), so the next lookup walks the
    folder again.
    """
    _FILES_EPOCH[0] += 1
    _FILES.clear()


def _known_trlc_files(
    folder: str, exclude_patterns: Iterable[str]
) -> List[str]:
    """:func:`_trlc_files`, walking the folder only once until
    :func:`forget_files`.

    Args:
        folder: Root folder to walk.
        exclude_patterns: Regular expressions matched against directory
            names.

    Returns:
        The file paths in sorted directory-walk order.
    """
    key = (folder, tuple(exclude_patterns))
    files = _FILES.get(key)
    if files is None:
        epoch = _FILES_EPOCH[0]
        files = _trlc_files(folder, exclude_patterns)
        if epoch == _FILES_EPOCH[0]:
            _FILES[key] = files
    return files


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


def files_declaring(
    ls, uri: str, wanted: "List[Tuple[str, str]]"
) -> "Dict[str, List[Tuple[str, str]]]":
    """Unopened .trlc files declaring the *wanted* symbols.

    Empty unless *uri* is parsed in parse mode WORKSPACE (the only mode
    that leaves .trlc files of the scope unparsed).

    Args:
        ls: Language server (provides config, store and open files).
        uri: URI of the file that uses the symbols.
        wanted: ``(name, package)`` pairs as returned by
            :func:`unknown_symbol`.

    Returns:
        A dict from file path to the wanted pairs it declares, in the
        order of *wanted*; empty if there is nothing to find or the mode
        is not WORKSPACE.
    """
    config = ls.get_config_for_uri(uri)
    scope_id = ls.store.scope_id_for_uri(uri)
    if (
        not wanted
        or config.parse_mode != ParseMode.WORKSPACE
        or scope_id is None
    ):
        return {}
    opened = {normalize_fs_path(path_from_uri(u)) for u in ls.fh.snapshot()}
    folder = path_from_uri(decode_scope_id(scope_id)[0])
    return _scan(folder, config.exclude_patterns, opened, wanted)


def _scan(
    folder: str,
    exclude_patterns: Iterable[str],
    skip: "set[str]",
    wanted: "List[Tuple[str, str]]",
) -> "Dict[str, List[Tuple[str, str]]]":
    """The .trlc files under *folder* declaring the *wanted* symbols.

    Args:
        folder: Root folder to search.
        exclude_patterns: Regular expressions matched against directory
            names.
        skip: Normalized paths of files to leave out.
        wanted: ``(name, package)`` pairs to look for.

    Returns:
        A dict from file path to the wanted pairs it declares, in the
        order of *wanted*.
    """
    result: Dict[str, List[Tuple[str, str]]] = {}
    for path in _known_trlc_files(folder, exclude_patterns):
        if normalize_fs_path(path) in skip:
            continue
        try:
            package, names = _declarations(path)
        except OSError:
            continue
        for symbol in wanted:
            if symbol[1] == package and symbol[0] in names:
                declared = result.setdefault(path, [])
                if symbol not in declared:
                    declared.append(symbol)
    return result


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
    return _names(
        files_declaring(ls, uri, _unknown_symbols(text, diagnostics))
    )


def files_outside_scope(
    ls, uri: str, text: str, diagnostics, parsed_paths: Iterable[str]
) -> "Dict[str, List[str]]":
    """Files outside the parse scope declaring the unknown symbols.

    Parse modes DIRECTORY and BAZEL parse a part of the workspace folder
    only (a directory, a Bazel target with its dependencies), so a record
    declared elsewhere in the folder is an "unknown symbol". Opening such
    a file does not bring it into the scope, unlike in mode WORKSPACE
    (see :func:`declaring_files`).

    Args:
        ls: Language server (provides config and store).
        uri: URI of the file with the diagnostics.
        text: Source text of that file.
        diagnostics: Diagnostics of the file; only unknown-symbol ones
            are considered.
        parsed_paths: Paths of the files parsed in the scope of *uri*.

    Returns:
        A dict from file path to the wanted names it declares; empty
        if there is nothing to find or the mode is neither DIRECTORY
        nor BAZEL.
    """
    config = ls.get_config_for_uri(uri)
    scope_id = ls.store.scope_id_for_uri(uri)
    wanted = _unknown_symbols(text, diagnostics)
    if (
        not wanted
        or config.parse_mode not in (ParseMode.DIRECTORY, ParseMode.BAZEL)
        or scope_id is None
    ):
        return {}
    parsed = {normalize_fs_path(path) for path in parsed_paths}
    folder = path_from_uri(decode_scope_id(scope_id)[0])
    return _names(_scan(folder, config.exclude_patterns, parsed, wanted))


def _unknown_symbols(text: str, diagnostics) -> "List[Tuple[str, str]]":
    """The ``(name, package)`` pairs of the unknown-symbol *diagnostics*."""
    return [
        found
        for found in (unknown_symbol(text, d) for d in diagnostics)
        if found is not None
    ]


def _names(
    found: "Dict[str, List[Tuple[str, str]]]",
) -> "Dict[str, List[str]]":
    """Reduce the declared ``(name, package)`` pairs per file to names."""
    # Same name wanted from two packages: listed once.
    return {
        path: list(dict.fromkeys(name for name, _ in symbols))
        for path, symbols in found.items()
    }
