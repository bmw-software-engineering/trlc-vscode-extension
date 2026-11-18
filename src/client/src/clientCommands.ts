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

import type { ClientCapabilities, StaticFeature } from "vscode-languageclient/node";

/**
 * Announces the client-side commands this extension registers as the
 * `experimental.trlcCommands` client capability. The server also runs in
 * other editors (trlc-lsp wheel), so it offers a code action that runs such
 * a command only to a client that announced it (see code_actions.py).
 */
export function clientCommandsFeature(commands: string[]): StaticFeature {
    return {
        fillClientCapabilities: (capabilities: ClientCapabilities): void => {
            capabilities.experimental = { ...capabilities.experimental, trlcCommands: commands };
        },
        initialize: (): void => undefined,
        getState: () => ({ kind: "static" }),
        clear: (): void => undefined,
    };
}
