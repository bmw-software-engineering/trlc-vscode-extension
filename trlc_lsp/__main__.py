#!/usr/bin/env python3
#
# TRLC LSP Server
# Copyright (C) 2023 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
#
# This file is part of the TRLC LSP Server.
#
# The TRLC LSP Server is free software: you can redistribute it
# and/or modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation, either version 3 of
# the License, or (at your option) any later version.
#
# The TRLC LSP Server is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty
# of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with TRLC. If not, see <https://www.gnu.org/licenses/>.

# This server is derived from the pygls example server, licensed under
# the Apache License, Version 2.0.

# pylint: disable=wrong-import-position

import argparse
import logging
import os
import sys
import tempfile

# Uncomment for debugging purposes
# import debugpy
# debugpy.connect(5678)
# debugpy.breakpoint()
# logging.basicConfig(
#     filename=os.path.join(tempfile.gettempdir(), "pygls.log"),
#     level=logging.DEBUG, filemode="w")
logging.basicConfig(
    filename=os.path.join(tempfile.gettempdir(), "pygls.log"),
    level=logging.WARNING,
    filemode="w")

# All extension dependencies are installed into a python-deps/ directory
# next to this package.  Locate it relative to __file__ so it works
# regardless of the working directory.
_python_deps = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "python-deps"
)
if os.path.isdir(_python_deps):
    if len(sys.path) <= 1:
        sys.path.append(_python_deps)
    else:
        sys.path.insert(1, _python_deps)

from .server import trlc_server


def add_arguments(parser):
    parser.description = "TRLC Language Server"

    parser.add_argument("--stdio",
                        action="store_true",
                        help="Use stdio transport (default)")
    parser.add_argument("--tcp",
                        action="store_true",
                        help="Use TCP server")
    parser.add_argument("--ws",
                        action="store_true",
                        help="Use WebSocket server")
    parser.add_argument("--host",
                        default="127.0.0.1",
                        help="Bind to this address")
    parser.add_argument("--port",
                        type=int,
                        default=5678,
                        help="Bind to this port")


def main():
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    args = parser.parse_args()

    if args.tcp:
        trlc_server.start_tcp(args.host, args.port)
    elif args.ws:
        trlc_server.start_ws(args.host, args.port)
    else:
        trlc_server.start_io()


if __name__ == "__main__":
    main()
