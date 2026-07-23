# *******************************************************************************
# TRLC VSCode Extension
# Copyright (C) 2025 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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

"""Bazel workspace integration for the TRLC language server.

Provides :class:`BazelClient` (runs ``bazel query`` to discover TRLC targets)
and :class:`BazelTargetManager` (maps Bazel targets to file sets used by
:class:`~server.scope_strategies.BazelScopeStrategy`).
"""

import hashlib
import logging
import os
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set

from lsprotocol.types import MessageType, ShowMessageParams

from .parse_guard import normalize_fs_path
from .server_config import DEFAULT_TRLC_RULE_CLASSES, ServerConfig

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .server_protocol import ServerProtocol


class BazelUnavailable(Exception):
    """Base: Bazel cannot be used at all for a workspace/folder — no
    workspace root found, ``bazel query`` failed, or building the target
    manager raised unexpectedly.

    Distinct from a query that *succeeded* but simply found nothing
    relevant to one particular file (no targets, no srcs, file not owned
    by any target) — that is not a failure and keeps its own quiet
    directory-mode fallback (see ``scope_strategies.py``). Only this
    branch — genuine Bazel-is-broken conditions — surfaces as a popup with
    no fallback; see :func:`resolve_bazel_manager`.
    """


class BazelWorkspaceNotFoundError(BazelUnavailable):
    """No ``WORKSPACE``/``MODULE.bazel`` found walking up from a folder."""

    def __init__(self, folder_path: str):
        self.folder_path = folder_path
        super().__init__(f"No Bazel workspace found under {folder_path}")


class BazelQueryError(BazelUnavailable):
    """A ``bazel query`` invocation failed (as opposed to succeeding with an
    empty result).

    Carries the failing command, the process return code (``None`` when the
    process could not be run at all) and a trimmed stderr excerpt, so callers
    can log an actionable message instead of guessing why the query produced
    nothing.
    """

    def __init__(
        self,
        reason: str,
        cmd: Optional[List[str]] = None,
        returncode: Optional[int] = None,
        stderr: str = "",
    ):
        self.reason = reason
        self.cmd = list(cmd) if cmd else []
        self.returncode = returncode
        self.stderr = stderr.strip()[:500] if stderr else ""
        detail = reason
        if returncode is not None:
            detail += f" (rc={returncode})"
        if self.cmd:
            detail += f": {' '.join(self.cmd)}"
        if self.stderr:
            detail += f"\nstderr: {self.stderr}"
        super().__init__(detail)


@dataclass
class BazelTarget:
    """Represents a Bazel target that owns TRLC/RSL files."""

    label: str  # e.g. "//score/path/req:feature_requirements"
    package: str  # e.g. "score/path/req"
    name: str  # e.g. "feature_requirements"
    rule_class: str  # e.g. "_trlc_requirement"
    srcs: List[str] = field(default_factory=list)  # file labels in srcs
    deps: List[str] = field(default_factory=list)  # dep target labels
    specs: List[str] = field(default_factory=list)  # spec target labels


@dataclass
class BazelTargetFiles:
    """A Bazel target plus its resolved parse-relevant file set."""

    target: BazelTarget
    # Absolute paths to all files needed to parse this target
    absolute_srcs: List[str]


#: POSIX only (see _run_bazel) - Windows native launchers need PYTHONPATH.
_PYTHON_ENV_VARS_TO_STRIP = (
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSAFEPATH",
    "PYTHONNOUSERSITE",
    "__PYVENV_LAUNCHER__",
)


class BazelClient:
    """Thin Bazel client using a single XML query to discover TRLC targets."""

    #: Fallback subprocess timeout (seconds) when no *query_timeout_seconds*
    #: is passed in — kept for callers/tests that construct a BazelClient
    #: directly without a ServerConfig. See ServerConfig.bazel_query_timeout_seconds
    #: for why the real default is well above a typical warm-server query.
    DEFAULT_QUERY_TIMEOUT_SECONDS = 180

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        workspace_root: str,
        bazel_executable: str = "bazel",
        rule_classes: Optional[List[str]] = None,
        use_shared_server: bool = False,
        query_timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS,
    ):
        self.workspace_root = os.path.abspath(workspace_root)
        self.bazel_executable = bazel_executable
        self.rule_classes = rule_classes or list(DEFAULT_TRLC_RULE_CLASSES)
        self.use_shared_server = use_shared_server
        self.query_timeout_seconds = query_timeout_seconds
        self._targets_cache: Dict[str, BazelTarget] = {}
        self._query_lock = threading.Lock()

    @property
    def _query_output_base(self) -> Optional[str]:
        """Dedicated Bazel output base for LSP queries."""
        if self.use_shared_server:
            return None
        ws_hash = hashlib.md5(self.workspace_root.encode()).hexdigest()
        base = os.path.join(tempfile.gettempdir(), f"trlc_lsp_{ws_hash}")
        return base

    @staticmethod
    def find_bazel_workspace(file_path: str) -> Optional[str]:
        """Walk up from file_path and return the nearest workspace root."""
        current = os.path.abspath(file_path)
        while True:
            if BazelClient._is_dir_bazel_workspace(current):
                return current
            parent = os.path.dirname(current)
            if parent == current:  # filesystem root
                break
            current = parent
        return None

    def is_bazel_workspace(self) -> bool:
        """Return True if workspace_root contains Bazel workspace markers."""
        return self._is_dir_bazel_workspace(self.workspace_root)

    @staticmethod
    def _is_dir_bazel_workspace(dir_path: str) -> bool:
        if not os.path.isdir(dir_path):
            return False
        for marker in (
            "WORKSPACE",
            "WORKSPACE.bazel",
            "MODULE.bazel",
            "REPO.bazel",
        ):
            if os.path.exists(os.path.join(dir_path, marker)):
                return True
        return False

    def label_to_abs_path(
        self, label: str, workspace_root: str = None
    ) -> Optional[str]:
        """Convert Bazel file label to absolute filesystem path.

        Returns ``None`` for labels that don't start with ``//`` or that would
        resolve outside *workspace_root* (path-traversal guard: labels come
        from ``bazel query`` output over the user's BUILD files, but a crafted
        or buggy label must never escape the workspace root).
        """
        root = workspace_root or self.workspace_root
        if not label.startswith("//"):
            return None
        rest = label[2:]
        if ":" in rest:
            package, name = rest.split(":", 1)
        else:
            parts = rest.rsplit("/", 1)
            package = parts[0] if len(parts) > 1 else ""
            name = parts[-1]
        # Reject traversal segments before joining.
        if ".." in package.split("/") or ".." in name.split("/"):
            LOGGER.debug("Rejecting label with traversal segment: %s", label)
            return None
        candidate = os.path.realpath(os.path.join(root, package, name))
        root_real = os.path.realpath(root)
        if os.path.commonpath([candidate, root_real]) != root_real:
            LOGGER.debug("Rejecting label escaping workspace: %s", label)
            return None
        return candidate

    def find_trlc_targets(self) -> List[BazelTarget]:
        """Return all TRLC Bazel targets; cached after first call."""
        with self._query_lock:
            if self._targets_cache:
                return list(self._targets_cache.values())

            rule_pattern = "|".join(sorted(self.rule_classes))
            # Raises BazelQueryError on failure; an empty (but successful)
            # query result is a valid "no targets" answer, not an error.
            xml_text = self._run_bazel_query_xml(
                f'kind("{rule_pattern}", //...)'
            )
            if not xml_text:
                return []

            targets = _parse_xml_targets(xml_text, self.rule_classes)
            for t in targets:
                self._targets_cache[t.label] = t

            LOGGER.info("Found %d TRLC targets in workspace", len(targets))
            return targets

    def clear_cache(self):
        """Discard cached target list so next call re-queries."""
        with self._query_lock:
            self._targets_cache = {}

    def run_deps_query_xml(self, label: str) -> str:
        """Run ``bazel query 'deps("<label>")'`` and return the raw XML.

        Unlike :meth:`find_trlc_targets`'s ``kind(pattern, //...)`` query
        (scoped to the main repo's own package tree under Bzlmod), ``deps()``
        walks actual dependency edges (any ``attr.label``/``attr.label_list``,
        including a private ``spec`` attribute) and is not scoped to ``//...``
        at all — so it correctly crosses into an external module (e.g. a
        ``spec`` target under ``@score_tooling//...``) that the blanket query
        can never see. Not cached here; the one caller
        (:meth:`~BazelTargetManager.files_for_target`) already memoizes
        per label.
        """
        return self._run_bazel_query_xml(f'deps("{label}")')

    def _run_bazel_query_xml(self, query: str) -> str:
        """Run bazel query --output=xml and return raw XML string."""
        cmd = [
            self.bazel_executable,
            "query",
            query,
            "--output=xml",
            "--noshow_progress",
            "--keep_going",
        ]
        if self._query_output_base:
            cmd.insert(1, f"--output_base={self._query_output_base}")
        return self._run_bazel(cmd)

    def _run_bazel(self, cmd: List[str]) -> str:
        """Execute cmd and return stdout.

        Raises :class:`BazelQueryError` on any failure (timeout, missing
        executable, OS error, non-tolerated exit code) so callers can tell
        a broken Bazel apart from a query that legitimately found nothing.
        """
        env = os.environ.copy()
        if sys.platform != "win32":
            for var in _PYTHON_ENV_VARS_TO_STRIP:
                env.pop(var, None)
        try:
            result = subprocess.run(
                cmd,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=self.query_timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise BazelQueryError(
                f"Bazel command timed out ({self.query_timeout_seconds} s)",
                cmd=cmd,
            ) from e
        except FileNotFoundError as e:
            raise BazelQueryError(
                f"Bazel executable not found: {self.bazel_executable}",
                cmd=cmd,
            ) from e
        except OSError as e:
            raise BazelQueryError(
                f"Bazel command OS error: {e}", cmd=cmd
            ) from e
        # rc=3 is "partial success" under --keep_going; its stdout is usable.
        if result.returncode not in (0, 3):
            raise BazelQueryError(
                "Bazel command failed",
                cmd=cmd,
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return result.stdout


#: Reject implausibly large `bazel query --output=xml` output before parsing.
#: A real workspace's target graph, even a huge one, is a few MB at most; this
#: is defense-in-depth against a corrupted/adversarial output (e.g. entity
#: expansion / "billion laughs" style blowup) causing unbounded memory use in
#: xml.etree, without adding a defusedxml dependency (which would need a
#: Bazel pip-lock regen this change can't perform).
_MAX_XML_BYTES = 64 * 1024 * 1024


def _parse_xml_targets(
    xml_text: str, rule_classes: List[str]
) -> List[BazelTarget]:
    """Parse bazel query --output=xml into BazelTarget list."""
    if len(xml_text) > _MAX_XML_BYTES:
        LOGGER.error(
            "Bazel XML output too large (%d bytes > %d); refusing to parse",
            len(xml_text),
            _MAX_XML_BYTES,
        )
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        LOGGER.error("Failed to parse Bazel XML output: %s", e)
        return []

    allowed = frozenset(rule_classes)
    targets = []
    for rule_elem in root.findall("rule"):
        rule_class = rule_elem.get("class", "")
        label = rule_elem.get("name", "")
        if not label or rule_class not in allowed:
            continue

        rest = label.lstrip("/")
        if ":" in rest:
            package, name = rest.split(":", 1)
        else:
            package, name = rest, rest.split("/")[-1]

        srcs = _list_attr_from_elem(rule_elem, "srcs")
        deps = _list_attr_from_elem(rule_elem, "deps")
        specs = _list_attr_from_elem(rule_elem, "spec")

        targets.append(
            BazelTarget(
                label=label,
                package=package,
                name=name,
                rule_class=rule_class,
                srcs=srcs,
                deps=deps,
                specs=specs,
            )
        )

    return targets


def _parse_xml_source_files(xml_text: str) -> List[str]:
    """Extract absolute ``.trlc``/``.rsl`` file paths from a ``bazel query
    --output=xml`` result's ``<source-file>`` elements.

    Each element's ``location`` attribute is Bazel's own resolved
    ``"<abs path>:<line>:<col>"`` — correct across repository boundaries
    (e.g. landing inside ``external/<repo>+/...``) without this extension
    having to reimplement Bazel's external-repo checkout layout itself.
    """
    if len(xml_text) > _MAX_XML_BYTES:
        LOGGER.error(
            "Bazel XML output too large (%d bytes > %d); refusing to parse",
            len(xml_text),
            _MAX_XML_BYTES,
        )
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        LOGGER.error("Failed to parse Bazel XML output: %s", e)
        return []

    paths: List[str] = []
    for elem in root.findall("source-file"):
        location = elem.get("location", "")
        # location is "<path>:<line>:<col>"; rsplit from the right so a
        # Windows drive-letter colon (C:\...) is never mistaken for the
        # line/col separator.
        path = location.rsplit(":", 2)[0] if location else ""
        if path.endswith((".trlc", ".rsl")):
            paths.append(path)
    return paths


def _list_attr_from_elem(rule_elem: ET.Element, attr_name: str) -> List[str]:
    """Extract list of label values for attr_name from a rule element.

    Filters by attribute value in Python rather than interpolating
    *attr_name* into an XPath predicate, so this is safe even if a future
    caller passes an untrusted name.
    """
    values: List[str] = []
    for lst in rule_elem.findall("list"):
        if lst.get("name") != attr_name:
            continue
        for lbl in lst.findall("label"):
            v = lbl.get("value", "")
            if v:
                values.append(v)
    for single in rule_elem.findall("label"):
        if single.get("name") != attr_name:
            continue
        v = single.get("value", "")
        if v and v not in values:
            values.append(v)
        break
    return values


class BazelTargetManager:
    """Maps Bazel target labels to their resolved parse-relevant file sets.

    ``_lock`` (RLock) guards ``_targets``/``_file_to_targets``/
    ``_resolved_closures`` dict reads and writes, so any already-built
    manager's data is safe to read from either thread. First-build
    serialization for a *fresh* manager is not this class's job — see
    :meth:`build_targets`.
    """

    def __init__(
        self,
        bazel_client: BazelClient,
        workspace_root: str,
    ):
        self.bazel_client = bazel_client
        self.workspace_root = workspace_root
        self._lock = threading.RLock()
        self._targets: Dict[str, BazelTargetFiles] = {}
        self._file_to_targets: Dict[str, List[str]] = {}
        #: label -> its full transitive parse-relevant file set, resolved
        #: lazily (one `bazel query deps()` call per label) and memoized by
        #: files_for_target. Separate from _targets/absolute_srcs,
        #: which build_targets() computes eagerly for every target purely
        #: to build the (local-only) file->target reverse mapping below.
        self._resolved_closures: Dict[str, List[str]] = {}

    @property
    def targets(self) -> Dict[str, BazelTargetFiles]:
        """Every built target, keyed by label."""
        with self._lock:
            return dict(self._targets)

    @property
    def file_to_targets(self) -> Dict[str, List[str]]:
        """Every known file path, mapped to the target labels that own it."""
        with self._lock:
            return dict(self._file_to_targets)

    def is_bazel_workspace(self) -> bool:
        """Return True if the workspace root contains a Bazel workspace file."""
        return self.bazel_client.is_bazel_workspace()

    def build_targets(self) -> List[BazelTarget]:
        """Discover all TRLC Bazel targets and build the file→target map.

        Returns the list of built targets for logging/display.

        Cached after the first successful build (mirrors
        BazelClient.find_trlc_targets's own "cached after first call"
        convention) — callers like BazelScopeStrategy.discover() call this
        on every parse cycle just to get the current target list, and
        without this the (bazel-subprocess-free, but non-trivial) local
        file-collection walk and its debug logging would still redundantly
        re-run every time even though nothing changed. :meth:`invalidate`
        clears it, same as it already clears find_trlc_targets's cache.

        Not single-flighted here: the *first* build of a fresh manager only
        ever runs inside :func:`build_target_manager`, called exclusively
        as the factory passed to :class:`BazelManagerCache.get_or_build`
        (see docs/ARCHITECTURE.md, Concurrency) — that Event-based
        single-flight is what serializes concurrent callers across threads;
        a manager instance isn't reachable by a second caller until it's
        already built. Every *other* call site (e.g.
        ``BazelScopeStrategy.discover()``, once per parse cycle) hits the
        already-populated early-return above. ``_lock`` here only guards the
        dict read/write against ``targets``/``file_to_targets`` property
        readers, not against concurrent builders.
        """
        with self._lock:
            if self._targets:
                return [tf.target for tf in self._targets.values()]

        if not self.bazel_client.is_bazel_workspace():
            LOGGER.debug("Not a Bazel workspace; target build skipped")
            return []

        all_targets: Dict[str, BazelTarget] = {
            t.label: t for t in self.bazel_client.find_trlc_targets()
        }
        if not all_targets:
            LOGGER.info("No TRLC Bazel targets found in workspace")
            return []

        targets: Dict[str, BazelTargetFiles] = {}
        file_to_targets: Dict[str, List[str]] = {}

        for label, _target in all_targets.items():
            raw_paths = self._collect_files_for_target(
                label, all_targets, visited=set()
            )
            seen: Set[str] = set()
            unique_srcs: List[str] = []
            for p in raw_paths:
                norm = normalize_fs_path(p)
                if norm not in seen:
                    seen.add(norm)
                    abs_p = os.path.abspath(p)
                    if os.path.exists(abs_p):
                        unique_srcs.append(abs_p)
                    else:
                        LOGGER.debug("Target file missing: %s", abs_p)

            target_files = BazelTargetFiles(
                target=all_targets[label], absolute_srcs=unique_srcs
            )
            targets[label] = target_files

            for src_path in unique_srcs:
                key = normalize_fs_path(src_path)
                file_to_targets.setdefault(key, []).append(label)
                LOGGER.debug("Mapped %s -> target %s", src_path, label)

        with self._lock:
            self._targets = targets
            self._file_to_targets = file_to_targets

        LOGGER.info("Built %d TRLC targets", len(targets))
        return list(all_targets.values())

    def _collect_files_for_target(
        self,
        label: str,
        all_targets: Dict[str, BazelTarget],
        visited: Set[str],
    ) -> List[str]:
        """Recursively collect all file paths needed to parse label."""
        if label in visited:
            return []
        visited.add(label)

        target = all_targets.get(label)
        if target is None:
            # Not visible to the workspace-scoped `kind(pattern, //...)`
            # query that populated all_targets — almost always because
            # label points into an external repo (e.g. a Bzlmod
            # `@score_tooling//...` dep/spec), which that query can't see
            # (see BazelClient.run_deps_query_xml's own docstring). Resolve
            # it the same way files_for_target() already does for the
            # target-level lazy closure: a `deps()` query, which *does*
            # cross repository boundaries. Memoized per label, so this only
            # costs one extra subprocess per distinct external target
            # actually referenced, not one per target in the workspace.
            # Swallow BazelUnavailable the same as a genuinely-unknown
            # label — this DFS's job is "best-effort file set", not
            # propagating a query failure out of the middle of a walk.
            try:
                return self.files_for_target(label)
            except BazelUnavailable:
                return []

        files: List[str] = []

        for src_label in target.srcs:
            path = self.bazel_client.label_to_abs_path(
                src_label, self.workspace_root
            )
            if path:
                files.append(path)

        for spec_label in target.specs:
            spec_target = all_targets.get(spec_label)
            if spec_target:
                for src_label in spec_target.srcs:
                    path = self.bazel_client.label_to_abs_path(
                        src_label, self.workspace_root
                    )
                    if path:
                        files.append(path)
            files.extend(
                self._collect_files_for_target(
                    spec_label, all_targets, visited
                )
            )

        for dep_label in target.deps:
            files.extend(
                self._collect_files_for_target(dep_label, all_targets, visited)
            )

        return files

    def targets_for_file(self, file_path: str) -> List[str]:
        """Return target labels that include file_path."""
        key = normalize_fs_path(file_path)
        with self._lock:
            return list(self._file_to_targets.get(key, []))

    def files_for_target(self, target_label: str) -> List[str]:
        """Return every file in *target_label*'s full transitive parse
        closure (srcs + specs + deps, including cross-repository labels).

        Resolved lazily via :meth:`_resolve_target_closure` — only the one
        target actually being discovered pays for a `bazel query deps()`
        call, not every target in the workspace — and memoized per label
        until :meth:`invalidate`.
        """
        with self._lock:
            cached = self._resolved_closures.get(target_label)
            if cached is not None:
                return list(cached)
        files = self._resolve_target_closure(target_label)
        with self._lock:
            self._resolved_closures[target_label] = files
        return list(files)

    def _resolve_target_closure(self, label: str) -> List[str]:
        """Full parse-relevant file set for *label* via Bazel's own
        dependency-graph traversal (`bazel query deps()`), rather than the
        manual `specs`/`deps` recursion `_collect_files_for_target` does for
        the (deliberately local-only) file->target reverse mapping.

        `deps()` walks actual dependency edges (any `attr.label`/
        `attr.label_list`, including a private `spec` attribute) and, unlike
        the blanket `kind(pattern, //...)` query, isn't scoped to the main
        repo under Bzlmod — so it also resolves a `spec`/`deps` label
        pointing into an external module. Lets `BazelQueryError` propagate
        uncaught: it's a `BazelUnavailable` subclass, and the caller
        (`BazelScopeStrategy.discover`, via `ParseEngine._build_vsm`)
        skips this scope's cycle rather than falling back to a different
        parse mode — see docs/ARCHITECTURE.md's Bazel section. Not routed
        through `resolve_bazel_manager`'s popup/dedup: this failure is
        specific to one already-resolved target's deps query, not the
        workspace-level "Bazel unusable" conditions that function covers.
        """
        xml_text = self.bazel_client.run_deps_query_xml(label)
        if not xml_text:
            return []
        return _parse_xml_source_files(xml_text)

    def invalidate(self):
        """Clear all cached target information."""
        self.bazel_client.clear_cache()
        with self._lock:
            self._targets = {}
            self._file_to_targets = {}
            self._resolved_closures = {}
        LOGGER.info("TRLC target cache invalidated")


def build_target_manager(
    bazel_ws: str, config: "ServerConfig"
) -> BazelTargetManager:
    """Build a :class:`BazelTargetManager` for *bazel_ws* from *config*.

    Constructs the :class:`BazelClient`, runs the initial ``build_targets()``
    (populating the file→target map used by scope resolution), and returns the
    ready manager. Shared by both the language server's ``_resolve_bazel_target``
    and :class:`~server.scope_strategies.BazelScopeStrategy` so they use one
    cached instance (and therefore one ``bazel query``) per workspace.
    """
    client = BazelClient(
        workspace_root=bazel_ws,
        bazel_executable=config.bazel_executable,
        rule_classes=config.bazel_rule_classes,
        use_shared_server=config.bazel_use_shared_server,
        query_timeout_seconds=config.bazel_query_timeout_seconds,
    )
    manager = BazelTargetManager(client, bazel_ws)
    manager.build_targets()
    return manager


def resolve_bazel_manager(
    ls: "ServerProtocol", folder_path: str, config: ServerConfig
) -> BazelTargetManager:
    """Single entry point for turning *folder_path* into a built
    :class:`BazelTargetManager`, or raising :class:`BazelUnavailable`.

    Both scope-id resolution (``TrlcLanguageServer._resolve_bazel_target``)
    and file discovery (``BazelScopeStrategy.discover``) go through this
    instead of each independently walking the filesystem for a workspace
    root and calling ``bazel query`` — so a workspace's Bazel state
    (usable or broken) is resolved and cached exactly once, and both
    callers agree on when it's usable.

    Shows exactly one ``MessageType.Error`` popup per distinct failure per
    *folder_path* (via ``BazelManagerCache.consume_error_for_warning``) —
    not one per parse cycle — then re-raises so the caller decides what
    "unavailable" means for its own scope handling (no scope vs. keep the
    last published parse). The one exception is
    :class:`BazelWorkspaceNotFoundError`: a workspace can legitimately mix
    Bazel and non-Bazel folders (see docs/ARCHITECTURE.md's Bazel section),
    so "no WORKSPACE/MODULE.bazel here" is an expected, quiet outcome for a
    plain folder, not an error — it's re-raised with no popup, and callers
    (``BazelScopeStrategy.discover``/``scope_id``) degrade to directory-mode
    parsing instead of leaving the file with no scope at all.
    """
    # find_bazel_workspace is a filesystem walk (no subprocess), but it's
    # still wrapped: a raise here (permissions, etc.) must go through the
    # same single popup + no-fallback path as every other failure kind,
    # not bypass it as a raw, unlabelled exception.
    try:
        bazel_ws = BazelClient.find_bazel_workspace(folder_path)
        lookup_error: Optional[BaseException] = None
    except Exception as e:  # pylint: disable=broad-exception-caught  # unified unavailable boundary — see factory() below
        LOGGER.exception("Bazel workspace lookup failed for %s", folder_path)
        bazel_ws = None
        lookup_error = e
    cache_key = bazel_ws or folder_path

    def factory() -> BazelTargetManager:
        if lookup_error is not None:
            raise BazelUnavailable(
                f"Bazel workspace lookup failed: {lookup_error}"
            ) from lookup_error
        if bazel_ws is None:
            raise BazelWorkspaceNotFoundError(folder_path)
        try:
            return build_target_manager(bazel_ws, config)
        except BazelUnavailable:
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught  # Bazel/TRLC may raise anything; unified unavailable boundary
            LOGGER.exception("Bazel target build failed for %s", bazel_ws)
            raise BazelUnavailable(f"Bazel target build failed: {e}") from e

    try:
        return ls.bazel_cache.get_or_build(
            cache_key,
            factory,
            wait_seconds=config.bazel_query_timeout_seconds + 10,
        )
    except BazelWorkspaceNotFoundError:
        # Quiet: see this function's docstring. No popup, no warning log —
        # this is the expected shape for a non-Bazel folder in a mixed
        # workspace, not a broken-Bazel condition.
        raise
    except BazelUnavailable as e:
        fresh_error = ls.bazel_cache.consume_error_for_warning(cache_key)
        if fresh_error is not None:
            LOGGER.warning(
                "Bazel unavailable for %s: %s", folder_path, fresh_error
            )
            ls.window_show_message(
                ShowMessageParams(
                    type=MessageType.Error,
                    message=f"TRLC: {fresh_error}; Bazel parsing "
                    "unavailable for this workspace until fixed and "
                    "reparsed.",
                )
            )
        raise e


def _is_bazel_error_retryable(error: BaseException) -> bool:
    """True if *error* is a transient Bazel failure worth retrying on the
    very next parse cycle, rather than staying negative-cached until an
    explicit :meth:`BazelManagerCache.clear` (config/workspace-folder
    change).

    ``BazelWorkspaceNotFoundError`` is never retryable here: "no Bazel
    workspace at this folder" is a stable fact about the folder (e.g. a
    non-Bazel folder living alongside Bazel ones in a mixed workspace),
    not a condition that clears up on its own — see
    :func:`resolve_bazel_manager`. A ``BazelQueryError`` is retryable when
    the subprocess never produced a real exit code at all (timeout,
    missing executable, other OS error — ``returncode is None``, see
    ``BazelClient._run_bazel``) or when Bazel's own exit code 32 says
    another command holds the server lock
    (https://bazel.build/run/scripts#exit-codes) — both are momentary.
    Any other returncode (bad rule/BUILD-file/config error) reflects a
    structural problem that won't fix itself between parse cycles.
    """
    if not isinstance(error, BazelQueryError):
        return False
    return error.returncode is None or error.returncode == 32


# pylint: disable-next=too-few-public-methods  # data-only slot, not a service object
class _CacheEntry:
    """One per-workspace slot in :class:`BazelManagerCache`."""

    __slots__ = ("ready", "manager", "error", "stale")

    def __init__(self):
        self.ready = threading.Event()
        self.manager: Optional[BazelTargetManager] = None
        self.error: Optional[BaseException] = None
        #: Set by clear() if this entry was still building when clear() ran
        #: (see BazelManagerCache.clear's docstring for why it isn't just
        #: dropped immediately like a finished entry would be).
        self.stale = False


class BazelManagerCache:
    """Per-Bazel-workspace :class:`BazelTargetManager` cache with
    build-once semantics.

    The internal lock is only ever held for dict operations — never while
    the factory (which runs a ``bazel query`` subprocess, up to
    ``bazel_query_timeout_seconds``) is executing. Exactly one caller
    becomes the builder; concurrent callers for the same workspace block on
    an Event until the build finishes.

    A factory failure is *negative-cached*: the exception is stored and
    re-raised to every subsequent caller until :meth:`clear`, so a broken
    Bazel setup fails fast instead of re-running a long query on every
    ``did_open``. Cleared on reparse/config change (see ParseEngine).
    """

    #: Fallback waiter timeout when the caller doesn't pass *wait_seconds*
    #: (kept above BazelClient.DEFAULT_QUERY_TIMEOUT_SECONDS for the same
    #: reason — see get_or_build).
    _DEFAULT_BUILD_WAIT_SECONDS = (
        BazelClient.DEFAULT_QUERY_TIMEOUT_SECONDS + 10
    )

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: Dict[str, _CacheEntry] = {}
        #: Cache keys a caller has already been shown a popup for. Deliber-
        #: ately a separate, longer-lived set rather than a per-entry flag:
        #: evict_retryable_errors() drops+rebuilds an entry on every retry
        #: of a transient failure, and a *persistently* broken workspace
        #: must still only warn once across all those rebuilds — not once
        #: per retry. Only clear() resets it; that's also the only path
        #: back to a fresh build once a workspace succeeds (a successful
        #: manager stays cached until then), so a later, distinct failure
        #: after a real recovery always follows a clear() anyway and warns
        #: again correctly.
        self._warned_keys: Set[str] = set()

    def get_or_build(
        self,
        workspace_root: str,
        factory: Callable[[], BazelTargetManager],
        wait_seconds: Optional[float] = None,
    ) -> BazelTargetManager:
        """Return the cached manager for *workspace_root*, building it via
        *factory* (outside the lock) if this is the first caller.

        *wait_seconds* bounds how long a non-builder caller waits for the
        in-flight build; callers should pass the configured
        ``bazel_query_timeout_seconds`` (plus a small margin) so the waiter
        never gives up before the builder itself would have timed out.
        """
        with self._lock:
            entry = self._entries.get(workspace_root)
            is_builder = entry is None
            if is_builder:
                entry = _CacheEntry()
                self._entries[workspace_root] = entry
        if is_builder:
            try:
                entry.manager = factory()
            except BaseException as e:
                entry.error = e
                raise
            finally:
                entry.ready.set()
                if entry.stale:
                    # clear() ran while this build was in flight and left
                    # it in place rather than spawning a second concurrent
                    # builder for the same workspace (see clear()'s
                    # docstring) — now that it's done, evict it so the
                    # *next* caller gets a properly fresh rebuild instead
                    # of silently reusing data from before whatever
                    # triggered the clear.
                    with self._lock:
                        # Only remove if still the current entry for this
                        # key — a later clear()+rebuild cycle may already
                        # have installed a newer one by the time this
                        # builder re-acquires the lock.
                        if self._entries.get(workspace_root) is entry:
                            del self._entries[workspace_root]
        elif not entry.ready.wait(
            wait_seconds
            if wait_seconds is not None
            else self._DEFAULT_BUILD_WAIT_SECONDS
        ):
            raise BazelQueryError(
                "Timed out waiting for the Bazel manager build of "
                f"{workspace_root}"
            )
        if entry.error is not None:
            raise entry.error
        return entry.manager

    def consume_error_for_warning(
        self, cache_key: str
    ) -> Optional[BaseException]:
        """Return *cache_key*'s cached error the first time it's observed,
        ``None`` on every later call (or if there is no cached error yet).

        Lets any number of concurrent callers all hit the same failed
        entry — as ``discover()`` does every parse cycle — while exactly
        one of them shows a popup for it. Uses the same lock as every
        other dict access on this class; never held while a factory runs.
        """
        with self._lock:
            entry = self._entries.get(cache_key)
            if (
                entry is None
                or entry.error is None
                or cache_key in self._warned_keys
            ):
                return None
            self._warned_keys.add(cache_key)
            return entry.error

    def evict_retryable_errors(self) -> None:
        """Drop negative-cached entries whose failure was transient (see
        :func:`_is_bazel_error_retryable`), so the next :meth:`get_or_build`
        call for that workspace retries ``bazel query`` instead of
        continuing to serve a stale failure.

        Intended to be called once per parse cycle (see
        ``ParseEngine.validate``) — deliberately narrower than
        :meth:`clear`: a structural failure (no Bazel workspace here at
        all, or a real query/config error) must stay cached until an
        explicit config/workspace-folder change, or every parse cycle
        would re-attempt a filesystem walk / doomed query for no reason.
        Entries still building are left alone.
        """
        with self._lock:
            self._entries = {
                key: entry
                for key, entry in self._entries.items()
                if not (
                    entry.ready.is_set()
                    and entry.error is not None
                    and _is_bazel_error_retryable(entry.error)
                )
            }

    def clear(self) -> None:
        """Drop cached managers (including negative-cached failures) so the
        next parse re-queries Bazel.

        An entry whose build already finished is dropped immediately. An
        entry still *in flight* is left in place instead — removing it here
        would let the very next caller become a second builder for the same
        workspace, running a second concurrent ``bazel query`` alongside the
        one already running. It's marked stale instead: the in-flight
        builder itself evicts it (see :meth:`get_or_build`) the moment it
        finishes, so the *next* caller after that gets a properly fresh
        rebuild — reflecting whatever triggered this clear — rather than
        either a duplicate query or a silently-outdated cached result.
        """
        with self._lock:
            still_building = {}
            for workspace_root, entry in self._entries.items():
                if entry.ready.is_set():
                    continue
                entry.stale = True
                still_building[workspace_root] = entry
            self._entries = still_building
            self._warned_keys.clear()
