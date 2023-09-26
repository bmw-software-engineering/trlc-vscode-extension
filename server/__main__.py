#!/usr/bin/env python3
#
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

# This server is derived from the pygls example server, licensed under
# the Apache License, Version 2.0.

# pylint: disable=wrong-import-position

import argparse
import logging
import sys

logging.basicConfig(filename="pygls.log", level=logging.WARNING, filemode="w")

if len(sys.path) <= 1:
    sys.path.append("python-deps")
else:
    sys.path.insert(1, "python-deps")

from .server import trlc_server


def add_arguments(parser):
    parser.description = "trlc langage server"

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
                        default=2087,
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
