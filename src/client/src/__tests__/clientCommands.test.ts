/* -------------------------------------------------------------------------
 * TRLC VSCode Extension
 * Copyright (C) 2026 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
 *
 * This file is part of the TRLC VSCode Extension.
 *
 * The TRLC VSCode Extension is free software: you can redistribute it
 * and/or modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation, either version 3 of
 * the License, or (at your option) any later version.
 *
 * The TRLC VSCode Extension is distributed in the hope that it will be
 * useful, but WITHOUT ANY WARRANTY; without even the implied warranty
 * of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with TRLC. If not, see <https://www.gnu.org/licenses/>.
 * ----------------------------------------------------------------------- */
"use strict";

import * as assert from "assert";
import type { ClientCapabilities } from "vscode-languageclient/node";

import { clientCommandsFeature } from "../clientCommands";

describe("clientCommandsFeature", () => {
    it("announces the commands as experimental.trlcCommands", () => {
        const capabilities: ClientCapabilities = {};
        clientCommandsFeature(["trlc.a", "trlc.b"]).fillClientCapabilities(capabilities);
        assert.deepStrictEqual(capabilities.experimental, { trlcCommands: ["trlc.a", "trlc.b"] });
    });

    it("keeps experimental capabilities other features announced", () => {
        const capabilities: ClientCapabilities = { experimental: { other: true } };
        clientCommandsFeature(["trlc.a"]).fillClientCapabilities(capabilities);
        assert.deepStrictEqual(capabilities.experimental, { other: true, trlcCommands: ["trlc.a"] });
    });
});
