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

import * as fs from "fs";
import * as path from "path";
import Mocha = require("mocha");

function findE2eFiles(dir: string): string[] {
    // Not fs.globSync: this runs inside the extension host, which is
    // VSCode's own bundled (older) Node runtime, not the toolchain Node
    // that compiled this file - no fs.globSync there.
    const found: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const entryPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            found.push(...findE2eFiles(entryPath));
        } else if (entry.name.endsWith(".e2e.js")) {
            found.push(entryPath);
        }
    }
    return found;
}

/** Entry point @vscode/test-electron loads inside the extension host. */
export function run(): Promise<void> {
    const mocha = new Mocha({ ui: "tdd", timeout: 30000 });
    const testsRoot = __dirname;

    for (const file of findE2eFiles(testsRoot).sort()) {
        mocha.addFile(file);
    }

    return new Promise((resolve, reject) => {
        try {
            mocha.run(failures => {
                if (failures > 0) {
                    reject(new Error(`${failures} e2e test(s) failed.`));
                } else {
                    resolve();
                }
            });
        } catch (error) {
            reject(error);
        }
    });
}
