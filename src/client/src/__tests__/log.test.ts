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

import * as assert from "assert";
import type { OutputChannel } from "vscode";

import { createLogger } from "../log";

/** Minimal fake satisfying just the OutputChannel surface createLogger uses. */
function fakeChannel(): OutputChannel & { lines: string[] } {
    const lines: string[] = [];
    return {
        lines,
        appendLine: (value: string) => lines.push(value),
    } as unknown as OutputChannel & { lines: string[] };
}

describe("createLogger", () => {
    it("writes info lines when the level is info", () => {
        const channel = fakeChannel();
        const logger = createLogger(channel, () => "info", () => undefined);

        logger.info("hello");

        assert.strictEqual(channel.lines.length, 1);
        assert.match(channel.lines[0], /\[info] hello$/);
    });

    it("drops info lines when the level is warning", () => {
        const channel = fakeChannel();
        const logger = createLogger(channel, () => "warning", () => undefined);

        logger.info("should not appear");

        assert.strictEqual(channel.lines.length, 0);
    });

    it("always writes warn lines regardless of level", () => {
        const channel = fakeChannel();
        const logger = createLogger(channel, () => "error", () => undefined);

        logger.warn("still shown");

        assert.strictEqual(channel.lines.length, 1);
        assert.match(channel.lines[0], /\[warning] still shown$/);
    });

    it("always writes error lines regardless of level", () => {
        const channel = fakeChannel();
        const logger = createLogger(channel, () => "error", () => undefined);

        logger.error("broke");

        assert.strictEqual(channel.lines.length, 1);
        assert.match(channel.lines[0], /\[error] broke$/);
    });

    it("does not notify by default", () => {
        const channel = fakeChannel();
        const notified: string[] = [];
        const logger = createLogger(channel, () => "warning", (msg) => notified.push(msg));

        logger.error("quiet failure");

        assert.deepStrictEqual(notified, []);
    });

    it("notifies when error is called with {notify: true}", () => {
        const channel = fakeChannel();
        const notified: string[] = [];
        const logger = createLogger(channel, () => "warning", (msg) => notified.push(msg));

        logger.error("loud failure", { notify: true });

        assert.deepStrictEqual(notified, ["loud failure"]);
        // The channel still gets the line too - notify doesn't replace it.
        assert.strictEqual(channel.lines.length, 1);
    });

    it("re-reads the level on every call rather than caching it", () => {
        const channel = fakeChannel();
        let level = "warning";
        const logger = createLogger(channel, () => level, () => undefined);

        logger.info("first, dropped");
        level = "debug";
        logger.info("second, kept");

        assert.strictEqual(channel.lines.length, 1);
        assert.match(channel.lines[0], /second, kept$/);
    });
});
