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

"""Central logging configuration for the TRLC language server.

:func:`configure_logging` attaches a rotating file handler to the root
logger once at process start. :func:`set_log_level` adjusts verbosity
afterward (e.g. when the client sends a ``trlcServer.logLevel`` config
update) without re-attaching handlers.

:class:`OutputChannelLogHandler` forwards ``LOGGER`` records at/above the
configured level into the LSP output channel via ``window_log_message``,
so raising verbosity shows more in the editor, not just in the log file.
It never calls ``window_show_message`` — the existing hand-placed popups
at the degradation-pattern call sites (see e.g.
:mod:`server.scope_strategies`, :mod:`server.bazel`) stay
exactly as they are; this handler only adds detail to the output channel.
"""

import logging
import logging.handlers
import os
import tempfile
from typing import Optional

from lsprotocol.types import LogMessageParams, MessageType

#: PID-suffixed so concurrent server instances never collide on the same
#: file - a real user can have multiple VSCode windows open at once, and
#: (as found via a genuine CI failure) Windows Bazel test actions run with
#: strategy "local", not sandboxed per-action the way Linux's
#: "linux-sandbox" is, so several test targets' server subprocesses can
#: otherwise end up writing/rotating the exact same file simultaneously -
#: on Windows, RotatingFileHandler's rollover (rename/delete) fails outright
#: if another process still has the file open. Consumers that need to find
#: "the" current log (tests/e2e/runTest.ts, docs/DEVELOPER_GUIDE.md) glob
#: for "pygls-*.log" and take the most recently modified one rather than
#: assuming a fixed name.
LOG_FILE = os.path.join(tempfile.gettempdir(), f"pygls-{os.getpid()}.log")

_LEVEL_BY_NAME = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_MESSAGE_TYPE_BY_LEVEL = {
    logging.DEBUG: MessageType.Log,
    logging.INFO: MessageType.Log,
    logging.WARNING: MessageType.Warning,
    logging.ERROR: MessageType.Error,
    logging.CRITICAL: MessageType.Error,
}

#: Set by attach_output_channel_handler(); tracked so set_log_level() can
#: keep the forwarding handler's threshold in sync with the root logger.
_output_handler: Optional["OutputChannelLogHandler"] = None


def level_from_name(name: str) -> int:
    """Map a ``trlcServer.logLevel`` string to a :mod:`logging` level,
    defaulting to ``WARNING`` for anything unrecognized."""
    return _LEVEL_BY_NAME.get(name.lower(), logging.WARNING)


def configure_logging(level: int = logging.WARNING) -> None:
    """Attach a rotating file handler to the root logger. Call once at
    process start, before any transport starts."""
    root = logging.getLogger()
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=5_000_000,
        backupCount=2,
        mode="w",
        # Without this, the file opens under the OS locale's default
        # encoding - on Windows that's the ANSI codepage, not UTF-8. Any
        # non-ASCII character in a log message (a file path, a TRLC error
        # message) then raises UnicodeEncodeError out of the logging call
        # itself, which can abort whatever request was being handled
        # mid-flight instead of just mangling the log line.
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)


def set_log_level(level: int) -> None:
    """Change the root logger's (and, if attached, the output-channel
    handler's) effective verbosity. Cheap and idempotent — safe to call on
    every config refresh without re-attaching handlers."""
    logging.getLogger().setLevel(level)
    if _output_handler is not None:
        _output_handler.setLevel(level)


class OutputChannelLogHandler(logging.Handler):
    """Forwards ``LOGGER`` records into the LSP output channel.

    Calls only ``ls.window_log_message`` — never ``window_show_message`` —
    so the existing single-popup degradation-pattern UX is untouched; this
    only adds detail to the output channel, which already shows the
    curated log-message calls.
    """

    def __init__(self, ls, level: int = logging.WARNING):
        super().__init__(level)
        self._ls = ls
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            msg_type = _MESSAGE_TYPE_BY_LEVEL.get(
                record.levelno, MessageType.Log
            )
            self._ls.window_log_message(
                LogMessageParams(type=msg_type, message=message)
            )
        except Exception:  # pylint: disable=broad-exception-caught  # logging handlers must never raise into caller code
            self.handleError(record)


#: Logger namespace every module in this package logs under
#: (``logging.getLogger(__name__)`` where ``__name__`` starts with
#: ``"server."``). The output-channel handler is attached here, not to the
#: root logger — pygls's own transport logger (``pygls.protocol.json_rpc``)
#: logs a DEBUG line for every notification it sends, including
#: ``window/logMessage`` itself. Attaching to root would forward that record
#: too, which would send another ``window/logMessage``, which pygls would
#: log sending, forwarded again, forever — an unbounded feedback loop that
#: only appears once ``trlcServer.logLevel`` is set to "debug" (WARNING/INFO
#: never reach pygls's own DEBUG-level logging), and fast enough to exhaust
#: the process (and, under WSL, the VM) almost immediately.
_APP_LOGGER_NAME = "server"


def attach_output_channel_handler(ls, level: int) -> OutputChannelLogHandler:
    """Attach (once) the handler that forwards this package's ``LOGGER``
    records to *ls*'s output channel, and remember it so :func:`set_log_level`
    can keep its threshold in sync.

    Deliberately attached to :data:`_APP_LOGGER_NAME`, not the root logger —
    see its docstring for the feedback-loop this avoids with pygls's own
    transport-level logging.
    """
    global _output_handler  # pylint: disable=global-statement
    handler = OutputChannelLogHandler(ls, level)
    logging.getLogger(_APP_LOGGER_NAME).addHandler(handler)
    _output_handler = handler
    return handler
