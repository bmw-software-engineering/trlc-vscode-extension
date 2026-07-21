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

# This server is derived from the pygls example server, licensed under
# the Apache License, Version 2.0.

"""TRLC language server entry point.

Sets up sys.path for bundled dependencies, then starts the pygls-based
TRLC language server using the transport specified via command-line flags
(default: stdio).
"""

import argparse
import os
import sys

# All extension dependencies are installed into a python-deps/ directory
# next to this package.  Locate it relative to __file__ so it works
# regardless of the working directory.
_python_deps = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python-deps"
)
if os.path.isdir(_python_deps):
    if len(sys.path) <= 1:
        sys.path.append(_python_deps)
    else:
        sys.path.insert(1, _python_deps)

# Import after sys.path setup above, so bundled deps (including
# lsprotocol, used by logging_config) resolve correctly.
from .logging_config import configure_logging, level_from_name  # noqa: E402
from .server import trlc_server  # noqa: E402


def add_arguments(parser):
    """Configure CLI argument parser with transport and host/port options."""

    parser.add_argument(
        "--stdio", action="store_true", help="Use stdio transport (default)"
    )
    parser.add_argument("--tcp", action="store_true", help="Use TCP server")
    parser.add_argument(
        "--ws", action="store_true", help="Use WebSocket server"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind to this address"
    )
    parser.add_argument(
        "--port", type=int, default=5678, help="Bind to this port"
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default="warning",
        help="Initial verbosity for standalone/manual launches; VSCode "
        "sessions instead control this via the trlcServer.logLevel "
        "setting once the client connects.",
    )
    parser.add_argument(
        "--debugpy",
        action="store_true",
        help="Attach a debugpy server on port 5678 and wait for a "
        "debugger to connect before starting the transport.",
    )


def main():
    """Parse CLI arguments and start the TRLC language server."""
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    args = parser.parse_args()

    configure_logging(level_from_name(args.log_level))

    if args.debugpy:
        import debugpy  # pylint: disable=import-outside-toplevel  # optional dev-only dependency

        debugpy.connect(5678)
        debugpy.breakpoint()

    if args.tcp:
        trlc_server.start_tcp(args.host, args.port)
    elif args.ws:
        trlc_server.start_ws(args.host, args.port)
    else:
        trlc_server.start_io()


if __name__ == "__main__":
    main()
