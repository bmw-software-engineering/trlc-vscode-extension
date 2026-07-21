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
"""Unit tests for trlc_lsp.logging_config."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# Test classes are thin scenario groupings; each test method documents
# its own scenario via a Given/When/Then comment.

import logging

import pytest
from lsprotocol.types import MessageType

from server import logging_config
from server.logging_config import (
    OutputChannelLogHandler,
    attach_output_channel_handler,
    level_from_name,
    set_log_level,
)


@pytest.fixture(autouse=True)
def _isolate_root_logger():
    """logging_config mutates the process-global root logger, the "server"
    app-logger namespace (see logging_config._APP_LOGGER_NAME), and its own
    module-level _output_handler singleton. Snapshot and restore all three
    around every test so these tests can't leak handlers/level into the
    rest of the suite (or into each other)."""
    root = logging.getLogger()
    app_logger = logging.getLogger(logging_config._APP_LOGGER_NAME)  # pylint: disable=protected-access
    original_handlers = list(root.handlers)
    original_app_handlers = list(app_logger.handlers)
    original_level = root.level
    original_output_handler = logging_config._output_handler  # pylint: disable=protected-access
    yield
    root.handlers[:] = original_handlers
    app_logger.handlers[:] = original_app_handlers
    root.setLevel(original_level)
    logging_config._output_handler = original_output_handler  # pylint: disable=protected-access


class _FakeLs:
    """Just enough of ServerProtocol for OutputChannelLogHandler."""

    def __init__(self):
        self.logs = []
        self.shown = []

    def window_log_message(self, params):
        self.logs.append(params)

    def window_show_message(self, params):
        """Must never be called by OutputChannelLogHandler - see the
        module docstring's rationale for keeping popups hand-placed."""
        self.shown.append(params)


class TestLevelFromName:
    def test_debug(self):
        assert level_from_name("debug") == logging.DEBUG

    def test_info(self):
        assert level_from_name("info") == logging.INFO

    def test_warning(self):
        assert level_from_name("warning") == logging.WARNING

    def test_error(self):
        assert level_from_name("error") == logging.ERROR

    def test_case_insensitive(self):
        assert level_from_name("DEBUG") == logging.DEBUG

    def test_unknown_falls_back_to_warning(self):
        assert level_from_name("nonsense") == logging.WARNING


class TestSetLogLevel:
    def test_changes_root_logger_effective_level(self):
        # Given: the root logger currently below DEBUG verbosity, and a
        # child logger with no level of its own (inherits from root)
        set_log_level(logging.WARNING)
        logger = logging.getLogger("server.logging_config_test")
        assert not logger.isEnabledFor(logging.DEBUG)

        # When: set_log_level raises verbosity to DEBUG
        set_log_level(logging.DEBUG)

        # Then: the child logger now allows debug records through
        assert logger.isEnabledFor(logging.DEBUG)

    def test_lowering_level_suppresses_debug_records(self):
        # Given: verbosity raised to DEBUG
        set_log_level(logging.DEBUG)
        logger = logging.getLogger("server.logging_config_test2")
        assert logger.isEnabledFor(logging.DEBUG)

        # When: verbosity is lowered back to WARNING
        set_log_level(logging.WARNING)

        # Then: a debug record would no longer be emitted by this logger
        # (it inherits the root logger's level since it has none of its own)
        assert not logger.isEnabledFor(logging.DEBUG)
        assert logger.isEnabledFor(logging.WARNING)

    def test_also_updates_attached_output_channel_handler(self):
        # Given: an output-channel handler attached at WARNING
        ls = _FakeLs()
        handler = attach_output_channel_handler(ls, logging.WARNING)

        # When: set_log_level raises verbosity to DEBUG
        set_log_level(logging.DEBUG)

        # Then: the handler's own threshold tracks the new level
        assert handler.level == logging.DEBUG


class TestOutputChannelLogHandler:
    def test_forwards_warning_via_window_log_message(self):
        # Given: a handler attached to a fake language server
        ls = _FakeLs()
        logger = logging.getLogger("server.logging_config_test3")
        logger.addHandler(OutputChannelLogHandler(ls, logging.WARNING))
        logger.setLevel(logging.WARNING)

        # When: a warning is logged
        logger.warning("bazel query failed: %s", "boom")

        # Then: it is forwarded to the output channel, not shown as a popup
        assert len(ls.logs) == 1
        assert "bazel query failed: boom" in ls.logs[0].message
        assert ls.logs[0].type == MessageType.Warning
        assert not ls.shown

    def test_records_below_handler_level_are_not_forwarded(self):
        # Given: a handler attached at WARNING
        ls = _FakeLs()
        logger = logging.getLogger("server.logging_config_test4")
        logger.addHandler(OutputChannelLogHandler(ls, logging.WARNING))
        logger.setLevel(logging.DEBUG)

        # When: an info-level record is logged
        logger.info("just a status update")

        # Then: it is not forwarded
        assert not ls.logs

    def test_error_level_maps_to_message_type_error(self):
        ls = _FakeLs()
        logger = logging.getLogger("server.logging_config_test5")
        logger.addHandler(OutputChannelLogHandler(ls, logging.WARNING))
        logger.setLevel(logging.WARNING)

        logger.error("something broke")

        assert ls.logs[0].type == MessageType.Error

    def test_debug_level_maps_to_message_type_log(self):
        ls = _FakeLs()
        logger = logging.getLogger("server.logging_config_test6")
        handler = OutputChannelLogHandler(ls, logging.DEBUG)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        logger.debug("verbose detail")

        assert ls.logs[0].type == MessageType.Log


class TestAttachOutputChannelHandler:
    def test_attaches_to_the_app_logger_not_root(self):
        # Given: a fake language server
        ls = _FakeLs()

        # When: the handler is attached
        handler = attach_output_channel_handler(ls, logging.WARNING)

        # Then: it is registered on the "server" app-logger namespace (not
        # root) and remembered
        assert handler in logging.getLogger("server").handlers
        assert handler not in logging.getLogger().handlers
        assert logging_config._output_handler is handler  # pylint: disable=protected-access

    def test_pygls_own_transport_logging_is_never_forwarded(self):
        """Regression test for the feedback loop this attachment point
        avoids: pygls's own json_rpc logger logs a DEBUG line for every
        notification it sends, including window/logMessage itself. If the
        handler were attached to the root logger, forwarding that record
        would send another window/logMessage, which pygls would log
        sending, forwarded again — an unbounded loop, only visible once
        logLevel is "debug" (see json_rpc.py's "Sending notification: ..."
        call sites)."""
        # Given: the handler attached at DEBUG (as it would be with
        # trlcServer.logLevel: "debug")
        ls = _FakeLs()
        attach_output_channel_handler(ls, logging.DEBUG)
        set_log_level(logging.DEBUG)

        # When: pygls's own transport logger logs as it does for every
        # notification it sends
        logging.getLogger("pygls.protocol.json_rpc").debug(
            "Sending notification: '%s' %s", "window/logMessage", {}
        )

        # Then: it never reaches the output channel
        assert not ls.logs

    def test_apps_own_debug_logging_is_still_forwarded(self):
        # Given: the handler attached at DEBUG
        ls = _FakeLs()
        attach_output_channel_handler(ls, logging.DEBUG)
        set_log_level(logging.DEBUG)

        # When: one of this package's own loggers logs at debug
        logging.getLogger("server.some_module").debug("verbose detail")

        # Then: it is forwarded as usual
        assert len(ls.logs) == 1
        assert "verbose detail" in ls.logs[0].message
