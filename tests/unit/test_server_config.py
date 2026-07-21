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
"""Unit tests for trlc_lsp.server_config."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then docstring or inline comment.

import dataclasses

import pytest

from server.context_store import ContextStore
from server.server_config import ParseMode, ServerConfig


class TestParseMode:
    def test_workspace_value(self):
        # Given/When/Then: WORKSPACE's string value is "workspace"
        assert ParseMode.WORKSPACE.value == "workspace"

    def test_directory_value(self):
        # Given/When/Then: DIRECTORY's string value is "directory"
        assert ParseMode.DIRECTORY.value == "directory"

    def test_repo_value(self):
        # Given/When/Then: REPO's string value is "repo"
        assert ParseMode.REPO.value == "repo"

    def test_bazel_value(self):
        # Given/When/Then: BAZEL's string value is "bazel"
        assert ParseMode.BAZEL.value == "bazel"

    def test_four_members(self):
        # Given/When/Then: exactly four parse modes are defined
        assert len(ParseMode) == 4


class TestServerConfigDefaults:
    def test_parse_mode_default(self):
        # Given/When: a default-constructed ServerConfig
        cfg = ServerConfig()
        # Then: its parse_mode is WORKSPACE
        assert cfg.parse_mode is ParseMode.WORKSPACE

    def test_verify_mode_default(self):
        # Given/When: a default-constructed ServerConfig
        cfg = ServerConfig()
        # Then: verify_mode defaults to True
        assert cfg.verify_mode is True

    def test_exclude_patterns_default_empty(self):
        # Given/When: a default-constructed ServerConfig
        cfg = ServerConfig()
        # Then: exclude_patterns defaults to an empty tuple
        assert cfg.exclude_patterns == ()

    def test_log_level_default(self):
        # Given/When: a default-constructed ServerConfig
        cfg = ServerConfig()
        # Then: log_level defaults to "warning"
        assert cfg.log_level == "warning"

    def test_instance_is_immutable(self):
        """ServerConfig is a frozen dataclass: field assignment raises,
        instead of allowing a caller to mutate a config another thread (the
        parse worker, mid-cycle) may be reading concurrently."""
        # Given/When/Then: ServerConfig is a frozen dataclass: field assignment raises, instead of
        # allowing a caller to mutate a config another thread (the parse worker, mid-
        # cycle) may be reading concurrently
        cfg = ServerConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.log_level = "debug"


class TestServerConfigFromDictParseMode:
    """Tests for from_dict() parseMode key and legacy parsing key."""

    # ------------------------------------------------------------------ #
    # Empty / missing keys                                                 #
    # ------------------------------------------------------------------ #
    def test_empty_dict_gives_defaults(self):
        # Given/When: from_dict is called with an empty dict
        cfg = ServerConfig.from_dict({})
        # Then: all fields fall back to their defaults
        assert cfg.parse_mode is ParseMode.WORKSPACE
        assert cfg.verify_mode is True
        assert cfg.exclude_patterns == ()

    # ------------------------------------------------------------------ #
    # parseMode key (new)                                                  #
    # ------------------------------------------------------------------ #
    def test_parsemode_workspace(self):
        # Given/When/Then: "workspace" maps to ParseMode.WORKSPACE
        assert (
            ServerConfig.from_dict({"parseMode": "workspace"}).parse_mode
            is ParseMode.WORKSPACE
        )

    def test_parsemode_directory(self):
        # Given/When/Then: "directory" maps to ParseMode.DIRECTORY
        assert (
            ServerConfig.from_dict({"parseMode": "directory"}).parse_mode
            is ParseMode.DIRECTORY
        )

    def test_parsemode_repo(self):
        # Given/When/Then: "repo" maps to ParseMode.REPO
        assert (
            ServerConfig.from_dict({"parseMode": "repo"}).parse_mode
            is ParseMode.REPO
        )

    def test_parsemode_bazel(self):
        # Given/When/Then: "bazel" maps to ParseMode.BAZEL
        assert (
            ServerConfig.from_dict({"parseMode": "bazel"}).parse_mode
            is ParseMode.BAZEL
        )

    def test_parsemode_case_insensitive(self):
        # Given/When/Then: "REPO" (any case) still maps to ParseMode.REPO
        assert (
            ServerConfig.from_dict({"parseMode": "REPO"}).parse_mode
            is ParseMode.REPO
        )

    def test_parsemode_unknown_falls_back_to_workspace(self):
        # Given/When/Then: an unrecognized value falls back to WORKSPACE
        assert (
            ServerConfig.from_dict({"parseMode": "nonsense"}).parse_mode
            is ParseMode.WORKSPACE
        )

    # ------------------------------------------------------------------ #
    # Legacy parsing key (no longer supported, defaults used)              #
    # ------------------------------------------------------------------ #
    def test_legacy_parsing_key_ignored(self):
        # Given/When/Then: the old "parsing" key is ignored, defaults apply
        assert (
            ServerConfig.from_dict({"parsing": "partial"}).parse_mode
            is ParseMode.WORKSPACE
        )
        assert (
            ServerConfig.from_dict({"parsing": "full"}).parse_mode
            is ParseMode.REPO
        )

    # ------------------------------------------------------------------ #
    # verify key                                                           #
    # ------------------------------------------------------------------ #


class TestServerConfigFromDictFields:
    """Tests for from_dict() verify, excludePatterns, scope, and bazel keys."""

    # ------------------------------------------------------------------ #
    # verify key                                                           #
    # ------------------------------------------------------------------ #
    def test_verify_false(self):
        # Given/When/Then: {"verify": False} sets verify_mode to False
        assert ServerConfig.from_dict({"verify": False}).verify_mode is False

    def test_verify_true(self):
        # Given/When/Then: {"verify": True} sets verify_mode to True
        assert ServerConfig.from_dict({"verify": True}).verify_mode is True

    def test_verify_missing_defaults_to_true(self):
        # Given/When/Then: a missing "verify" key defaults to True
        assert ServerConfig.from_dict({}).verify_mode is True

    def test_verify_none_treated_as_missing(self):
        # Given/When/Then: {"verify": None} is treated like a missing key (defaults to True)
        # None means "not set by client" → use default True
        assert ServerConfig.from_dict({"verify": None}).verify_mode is True

    # ------------------------------------------------------------------ #
    # excludePatterns key                                                  #
    # ------------------------------------------------------------------ #
    def test_exclude_patterns_list(self):
        # Given/When: from_dict is called with a list of patterns
        cfg = ServerConfig.from_dict({"excludePatterns": ["a/*", "b/*"]})
        # Then: they are stored as-is
        assert cfg.exclude_patterns == ("a/*", "b/*")

    def test_exclude_patterns_non_string_coerced(self):
        # Given/When: from_dict is called with non-string pattern values
        cfg = ServerConfig.from_dict({"excludePatterns": [42, True]})
        # Then: they are coerced to strings
        assert cfg.exclude_patterns == ("42", "True")

    def test_exclude_patterns_none_gives_empty(self):
        # Given/When: from_dict is called with excludePatterns explicitly None
        cfg = ServerConfig.from_dict({"excludePatterns": None})
        # Then: exclude_patterns is an empty tuple
        assert cfg.exclude_patterns == ()

    def test_exclude_patterns_missing_gives_empty(self):
        # Given/When: from_dict is called with excludePatterns absent
        cfg = ServerConfig.from_dict({})
        # Then: exclude_patterns is an empty tuple
        assert cfg.exclude_patterns == ()

    def test_invalid_exclude_pattern_is_dropped_and_reported(self):
        """A malformed regex is excluded from exclude_patterns (so
        Vscode_Source_Manager never sees it) and surfaced via
        invalid_patterns (so callers can warn the user), rather than being
        silently dropped with only a debug log."""
        # Given/When/Then: A malformed regex is excluded from exclude_patterns (so
        # Vscode_Source_Manager never sees it) and surfaced via invalid_patterns (so callers can
        # warn the user), rather than being silently dropped with only a debug log
        cfg = ServerConfig.from_dict(
            {"excludePatterns": ["valid/.*", "[unterminated"]}
        )
        assert cfg.exclude_patterns == ("valid/.*",)
        assert cfg.invalid_patterns == ("[unterminated",)

    def test_all_valid_exclude_patterns_gives_empty_invalid_patterns(self):
        """No malformed patterns means an empty invalid_patterns tuple."""
        # Given/When/Then: No malformed patterns means an empty invalid_patterns tuple
        cfg = ServerConfig.from_dict({"excludePatterns": ["a/*", "b/*"]})
        assert cfg.invalid_patterns == ()

    # ------------------------------------------------------------------ #
    # bazel.* keys                                                         #
    # ------------------------------------------------------------------ #
    def test_bazel_executable_default(self):
        # Given/When: from_dict is called with no bazel config
        cfg = ServerConfig.from_dict({})
        # Then: bazel_executable defaults to "bazel"
        assert cfg.bazel_executable == "bazel"

    def test_bazel_executable_custom(self):
        # Given/When: from_dict is called with a custom bazel executable path
        cfg = ServerConfig.from_dict(
            {"bazel": {"executable": "/usr/bin/bazel"}}
        )
        # Then: it is used instead of the default
        assert cfg.bazel_executable == "/usr/bin/bazel"

    def test_bazel_rule_classes_default(self):
        # Given/When: from_dict is called with no bazel config
        cfg = ServerConfig.from_dict({})
        # Then: the default rule classes are present
        assert "_trlc_specification" in cfg.bazel_rule_classes
        assert "feature_requirements" in cfg.bazel_rule_classes
        assert "_score_requirements_rule" in cfg.bazel_rule_classes

    def test_bazel_rule_classes_custom(self):
        # Given/When: from_dict is called with custom rule classes
        cfg = ServerConfig.from_dict(
            {"bazel": {"ruleClasses": ["custom_rule"]}}
        )
        # Then: they replace the defaults
        assert cfg.bazel_rule_classes == ("custom_rule",)

    def test_bazel_use_shared_server_default(self):
        # Given/When: from_dict is called with no bazel config
        cfg = ServerConfig.from_dict({})
        # Then: bazel_use_shared_server defaults to False
        assert cfg.bazel_use_shared_server is False

    def test_bazel_use_shared_server_true(self):
        # Given/When: from_dict is called with useSharedServer explicitly True
        cfg = ServerConfig.from_dict({"bazel": {"useSharedServer": True}})
        # Then: bazel_use_shared_server is True
        assert cfg.bazel_use_shared_server is True

    def test_bazel_query_timeout_default(self):
        # Given/When: from_dict is called with no bazel config
        cfg = ServerConfig.from_dict({})
        # Then: it defaults to 180 seconds
        assert cfg.bazel_query_timeout_seconds == 180

    def test_bazel_query_timeout_custom(self):
        # Given/When: from_dict is called with a custom timeout
        cfg = ServerConfig.from_dict({"bazel": {"queryTimeoutSeconds": 300}})
        # Then: it is used instead of the default
        assert cfg.bazel_query_timeout_seconds == 300

    def test_bazel_query_timeout_zero_or_negative_falls_back_to_default(self):
        # Given/When: from_dict is called with a non-positive timeout
        cfg = ServerConfig.from_dict({"bazel": {"queryTimeoutSeconds": 0}})
        # Then: it falls back to the default rather than a useless 0s timeout
        assert cfg.bazel_query_timeout_seconds == 180

    def test_bazel_query_timeout_non_numeric_falls_back_to_default(self):
        # Given/When: from_dict is called with a non-numeric timeout
        cfg = ServerConfig.from_dict(
            {"bazel": {"queryTimeoutSeconds": "not-a-number"}}
        )
        # Then: it falls back to the default instead of raising
        assert cfg.bazel_query_timeout_seconds == 180


class TestServerConfigFromDictHoverFields:
    """Tests for from_dict()'s hover.* keys."""

    def test_hover_description_component_default(self):
        # Given/When: from_dict is called with no hover config
        cfg = ServerConfig.from_dict({})
        # Then: it defaults to "description"
        assert cfg.hover_description_component == "description"

    def test_hover_description_component_custom(self):
        # Given/When: from_dict is called with a custom component name
        cfg = ServerConfig.from_dict(
            {"hover": {"descriptionComponent": "summary"}}
        )
        # Then: it is used instead of the default
        assert cfg.hover_description_component == "summary"

    def test_hover_description_component_empty_string_disables_it(self):
        # Given/When: from_dict is called with an explicit empty string
        cfg = ServerConfig.from_dict({"hover": {"descriptionComponent": ""}})
        # Then: the empty string is kept as-is (the feature's own "disabled"
        # value), not silently replaced by the default
        assert cfg.hover_description_component == ""

    # ------------------------------------------------------------------ #
    # logLevel key                                                         #
    # ------------------------------------------------------------------ #
    def test_log_level_missing_gives_warning(self):
        # Given/When: from_dict is called with logLevel absent
        cfg = ServerConfig.from_dict({})
        # Then: log_level defaults to "warning"
        assert cfg.log_level == "warning"

    def test_log_level_debug(self):
        # Given/When/Then: {"logLevel": "debug"} is stored as-is
        assert (
            ServerConfig.from_dict({"logLevel": "debug"}).log_level == "debug"
        )

    def test_log_level_case_insensitive(self):
        # Given/When/Then: "DEBUG" (any case) is normalized to "debug"
        assert (
            ServerConfig.from_dict({"logLevel": "DEBUG"}).log_level == "debug"
        )

    def test_log_level_unknown_falls_back_to_warning(self):
        # Given/When/Then: an unrecognized value falls back to "warning"
        assert (
            ServerConfig.from_dict({"logLevel": "nonsense"}).log_level
            == "warning"
        )


class TestServerConfigReplace:
    def test_replace_parse_mode_leaves_original_unchanged(self):
        # Given: an original ServerConfig
        original = ServerConfig()
        # When: dataclasses.replace creates a modified copy
        modified = dataclasses.replace(original, parse_mode=ParseMode.REPO)
        # Then: the original is untouched, only the copy reflects the change
        assert original.parse_mode is ParseMode.WORKSPACE
        assert modified.parse_mode is ParseMode.REPO

    def test_replace_verify_mode(self):
        # Given/When: dataclasses.replace overrides verify_mode
        cfg = dataclasses.replace(ServerConfig(), verify_mode=False)
        # Then: the new value is applied
        assert cfg.verify_mode is False


class TestPerFolderConfigResolution:
    """get_config_for_uri / folder_configs live on ContextStore now (the
    per-folder override half of the thread-safe central store) — this
    covers the resolution *policy* (folder override beats default, unknown
    folder falls back to default); ContextStore's own locking is covered in
    test_context_store.py."""

    def test_unknown_folder_falls_back_to_default(self):
        # Given: a store with no folder-specific config
        store = ContextStore()
        # When/Then: looking up an unknown folder returns the default config
        assert (
            store.get_config_for_uri("file:///unknown") is store.default_config
        )

    def test_known_folder_returns_its_override(self):
        # Given: a store with a folder-specific override
        store = ContextStore()
        override = ServerConfig(parse_mode=ParseMode.DIRECTORY)
        store.set_folder_config("file:///ws", override)
        # When/Then: looking up that folder returns its override
        assert store.get_config_for_uri("file:///ws") is override

    def test_folder_override_does_not_affect_other_folders(self):
        # Given: a store with one folder's override set
        store = ContextStore()
        store.set_folder_config(
            "file:///ws1", ServerConfig(parse_mode=ParseMode.REPO)
        )
        # When/Then: a different folder still falls back to the default
        assert store.get_config_for_uri("file:///ws2") is store.default_config

    def test_changing_default_config_does_not_touch_folder_overrides(self):
        # Given: a store with a folder-specific override
        store = ContextStore()
        override = ServerConfig(parse_mode=ParseMode.BAZEL)
        store.set_folder_config("file:///ws", override)
        # When: the default config is changed
        store.set_default_config(ServerConfig(parse_mode=ParseMode.REPO))
        # Then: the folder's override is unaffected
        assert store.get_config_for_uri("file:///ws") is override

    def test_none_folder_uri_returns_default(self):
        # Given: a fresh store
        store = ContextStore()
        # When/Then: a None folder_uri resolves to the default config
        assert store.get_config_for_uri(None) is store.default_config
