# *******************************************************************************
# TRLC VSCode Extension
# Copyright (C) 2023 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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

"""Server configuration data classes and parse-mode enumeration.

Defines :class:`ParseMode` (workspace / directory / repo / bazel) and
:class:`ServerConfig`, which holds all user-configurable server settings
populated from the VS Code extension configuration JSON.
"""

import enum
import re
from dataclasses import dataclass, field
from typing import List, Tuple


class ParseMode(enum.Enum):
    """Controls which files are included in each TRLC parse cycle.

    WORKSPACE — only the files currently open in the editor plus their
                transitive RSL includes.  Fast; used for interactive editing.

    DIRECTORY — only the directory containing the currently open file(s),
                non-recursively.  Useful in huge monorepos where full REPO
                parse is impractical but WORKSPACE includes are insufficient.

    REPO      — all ``.rsl`` and ``.trlc`` files found by ``os.walk`` under
                the workspace folder(s), excluding ``bazel-*`` directories.
                Required for rename and cross-file references.

    BAZEL     — files declared in Bazel TRLC targets, discovered via
                a single ``bazel query --output=xml`` call. Workspace root
                is auto-detected; requires a WORKSPACE/MODULE.bazel file.
    """

    WORKSPACE = "workspace"
    DIRECTORY = "directory"
    REPO = "repo"
    BAZEL = "bazel"


#: A Bazel rule-class name (Starlark identifier): letters, digits,
#: underscore, not starting with a digit. bazel.ruleClasses entries are
#: interpolated unescaped into a `kind("a|b|c", //...)` query string (see
#: BazelClient.find_trlc_targets) — this guards against a misconfigured
#: entry (regex metacharacters, embedded quotes) producing a malformed or
#: surprising query rather than the "no such rule class" the user expects.
_VALID_RULE_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Default Bazel rule classes that produce TRLC source files.
#: Mirrored in package.json's trlcServer.bazel.ruleClasses default - keep
#: both lists in sync by hand if this changes.
DEFAULT_TRLC_RULE_CLASSES: List[str] = [
    "_trlc_specification",
    "_trlc_requirement",
    "_trlc_rst",
    "_score_requirements_rule",
    "feature_requirements",
    "component_requirements",
    "assumed_system_requirements",
    "fmea",
]


@dataclass(frozen=True)
class ServerConfig:  # pylint: disable=too-many-instance-attributes  # one field per setting
    """Immutable snapshot of the LSP server configuration.

    Construct from a raw configuration dict received from the client using
    :meth:`from_dict`.  Replace individual fields with
    ``dataclasses.replace(config, field=new_value)`` to produce a new
    instance — the instance itself is frozen (hashable-shaped, safe to hand
    to another thread without a copy) so it can be read by the parse worker
    thread mid-cycle without racing a concurrent config update on the main
    thread (see :func:`~server.parse_engine.ParseEngine._build_vsm`).
    """

    parse_mode: ParseMode = ParseMode.WORKSPACE
    verify_mode: bool = True
    exclude_patterns: Tuple[str, ...] = ()
    #: Exclude patterns from :meth:`from_dict` that failed ``re.compile``.
    #: Kept alongside (rather than merely dropped) so callers can warn the
    #: user which patterns were ignored, instead of a silent partial
    #: exclusion the user has no way to notice.
    invalid_patterns: Tuple[str, ...] = ()
    bazel_executable: str = "bazel"
    bazel_rule_classes: Tuple[str, ...] = field(
        default_factory=lambda: tuple(DEFAULT_TRLC_RULE_CLASSES)
    )
    #: bazel.ruleClasses entries from :meth:`from_dict` that aren't valid
    #: Starlark identifiers (see ``_VALID_RULE_CLASS_RE``). Kept alongside,
    #: same rationale as ``invalid_patterns``.
    invalid_rule_classes: Tuple[str, ...] = ()
    bazel_use_shared_server: bool = False
    #: Subprocess timeout for a single `bazel query` call. Default is well
    #: above a typical warm-server query because the *first* query against a
    #: fresh output_base (the common case in BAZEL mode: see
    #: BazelClient._query_output_base) can require a full cold Bazel
    #: server start + workspace analysis, which can legitimately take
    #: minutes on a large repo or a slow filesystem (e.g. WSL).
    bazel_query_timeout_seconds: int = 180
    #: Component name whose string value hover shows for a reference to a
    #: record object (see server.handlers.hover). Not a TRLC language
    #: concept — just a configurable convention hook, since which field (if
    #: any) holds a human-readable summary is entirely schema-specific.
    #: Empty string disables the feature.
    hover_description_component: str = "description"
    log_level: str = "warning"

    @classmethod
    # pylint: disable-next=too-many-locals,too-many-branches  # straight-line dict->dataclass field mapping
    def from_dict(cls, d: dict) -> "ServerConfig":
        """Create a :class:`ServerConfig` from a raw workspace configuration
        dict as received from ``workspace/configuration``.

        Keys: parseMode (directory/workspace/repo/bazel), verify,
        excludePatterns, bazel.executable, bazel.ruleClasses,
        bazel.useSharedServer, bazel.queryTimeoutSeconds,
        hover.descriptionComponent, logLevel.

        Legacy key ``parsing`` (partial/full) is still recognized for
        backwards compatibility but maps to parseMode values.
        """
        raw_mode = d.get("parseMode")
        if not raw_mode:
            legacy_parsing = d.get("parsing", "workspace")
            _legacy_map = {
                "partial": "workspace",
                "full": "repo",
            }
            raw_mode = _legacy_map.get(
                str(legacy_parsing).lower(), "workspace"
            )

        _mode_map = {
            "directory": ParseMode.DIRECTORY,
            "workspace": ParseMode.WORKSPACE,
            "repo": ParseMode.REPO,
            "bazel": ParseMode.BAZEL,
        }
        mode = _mode_map.get(str(raw_mode).lower(), ParseMode.WORKSPACE)
        verify = d.get("verify")
        patterns = d.get("excludePatterns")
        bazel = d.get("bazel", {}) or {}
        bazel_executable = bazel.get("executable", "bazel")
        bazel_rule_classes = bazel.get("ruleClasses")
        bazel_use_shared = bazel.get("useSharedServer", False)
        raw_timeout = bazel.get("queryTimeoutSeconds")
        try:
            bazel_query_timeout = (
                int(raw_timeout)
                if raw_timeout is not None
                else cls.bazel_query_timeout_seconds
            )
            if bazel_query_timeout <= 0:
                bazel_query_timeout = cls.bazel_query_timeout_seconds
        except (TypeError, ValueError):
            bazel_query_timeout = cls.bazel_query_timeout_seconds
        hover = d.get("hover", {}) or {}
        raw_hover_component = hover.get("descriptionComponent")
        hover_description_component = (
            str(raw_hover_component)
            if raw_hover_component is not None
            else cls.hover_description_component
        )
        log_level = str(d.get("logLevel", "warning")).lower()
        if log_level not in ("debug", "info", "warning", "error"):
            log_level = "warning"

        invalid_rule_classes: List[str] = []
        if isinstance(bazel_rule_classes, list):
            valid_rule_classes = []
            for r in bazel_rule_classes:
                rule_class = str(r)
                if _VALID_RULE_CLASS_RE.match(rule_class):
                    valid_rule_classes.append(rule_class)
                else:
                    invalid_rule_classes.append(rule_class)
            # An empty result (every entry invalid) would make the `kind()`
            # query match nothing at all — worse than useless — so fall
            # back to the defaults rather than silently discovering zero
            # targets forever.
            bazel_rule_classes = tuple(
                valid_rule_classes or DEFAULT_TRLC_RULE_CLASSES
            )
        else:
            bazel_rule_classes = tuple(DEFAULT_TRLC_RULE_CLASSES)

        valid_patterns: List[str] = []
        invalid_patterns: List[str] = []
        if isinstance(patterns, list):
            for p in patterns:
                pattern = str(p)
                try:
                    re.compile(pattern)
                except re.error:
                    invalid_patterns.append(pattern)
                else:
                    valid_patterns.append(pattern)

        return cls(
            parse_mode=mode,
            verify_mode=bool(verify) if verify is not None else True,
            exclude_patterns=tuple(valid_patterns),
            invalid_patterns=tuple(invalid_patterns),
            bazel_executable=str(bazel_executable),
            bazel_rule_classes=bazel_rule_classes,
            invalid_rule_classes=tuple(invalid_rule_classes),
            bazel_use_shared_server=bool(bazel_use_shared),
            bazel_query_timeout_seconds=bazel_query_timeout,
            hover_description_component=hover_description_component,
            log_level=log_level,
        )
