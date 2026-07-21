/* -------------------------------------------------------------------------
 * TRLC VSCode Extension
 * Copyright (C) 2023 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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

/**
 * Client-side logger. Writes into the same output channel the Python
 * server's `window_log_message` notifications and the LSP wire trace
 * already land in, so client + server + protocol logs read as one stream
 * instead of being split across a devtools console and a VSCode channel.
 *
 * Verbosity is gated by the single `trlcServer.logLevel` setting shared
 * with the server (see server_config.py / logging_config.py) — there is no
 * separate client-only setting. `warn`/`error` always write; `info` is
 * dropped unless the level is "info" or "debug". `error(msg, {notify:
 * true})` is the one call site for "log it and show a popup", replacing
 * the manually-duplicated console.error + showErrorMessage pattern that
 * used to live in installer.ts.
 *
 * Deliberately has no runtime `vscode` import (only a type-only one, erased
 * at compile time) — the channel and the popup callback are both injected,
 * so this module can be unit-tested with plain mocha/node, following the
 * same pure-module convention as installerCore.ts/serverEnv.ts.
 */

import type { OutputChannel } from "vscode";

export interface Logger {
    info(message: string): void;
    warn(message: string): void;
    error(message: string, opts?: { notify?: boolean }): void;
}

const LEVEL_ORDER: Record<string, number> = {
    debug: 0,
    info: 1,
    warning: 2,
    error: 3,
};

function write(channel: OutputChannel, levelLabel: string, message: string): void {
    const timestamp = new Date().toISOString();
    channel.appendLine(`[${timestamp}] [${levelLabel}] ${message}`);
}

/**
 * @param channel   Output channel to write into — pass the same instance
 *                  given to `LanguageClientOptions.outputChannel` so client
 *                  and server/trace output share one channel.
 * @param getLevel  Reads the current `trlcServer.logLevel` value (re-read
 *                  on every call rather than cached, so a live
 *                  `workspace.onDidChangeConfiguration` is picked up
 *                  without a reload).
 * @param notify    Called for `error(msg, {notify: true})` — the caller
 *                  (extension.ts) passes `window.showErrorMessage`.
 */
export function createLogger(
    channel: OutputChannel,
    getLevel: () => string,
    notify: (message: string) => void
): Logger {
    const enabled = (levelLabel: string): boolean =>
        LEVEL_ORDER[levelLabel] >= (LEVEL_ORDER[getLevel()] ?? LEVEL_ORDER.warning);

    return {
        info(message: string): void {
            if (enabled("info")) {
                write(channel, "info", message);
            }
        },
        warn(message: string): void {
            write(channel, "warning", message);
        },
        error(message: string, opts?: { notify?: boolean }): void {
            write(channel, "error", message);
            if (opts?.notify) {
                notify(message);
            }
        },
    };
}
