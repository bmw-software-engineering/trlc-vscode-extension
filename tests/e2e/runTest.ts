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
import * as os from "os";
import * as path from "path";
import { runTests } from "@vscode/test-electron";

import { prepareExtension, resolveRepoPath } from "./prepare";

interface SmokeScenarios {
    completionDotTrigger: { sampleText: string };
}

/**
 * logging_config.LOG_FILE is PID-suffixed ("pygls-<pid>.log" in the OS temp
 * dir) so concurrent server instances never collide on the same file - this
 * e2e run only ever has one VSCode window (and therefore one server
 * process) alive at a time, so the most recently modified match is
 * unambiguously "the" current log.
 */
function findPyglsLogPath(): string | undefined {
    const dir = os.tmpdir();
    const candidates = fs
        .readdirSync(dir)
        .filter((name) => /^pygls-\d+\.log$/.test(name))
        .map((name) => path.join(dir, name))
        .map((p) => ({ path: p, mtime: fs.statSync(p).mtimeMs }))
        .sort((a, b) => b.mtime - a.mtime);
    return candidates[0]?.path;
}

/**
 * Dumps the tail of pygls-<pid>.log so a failed e2e run's CI output includes
 * the server's own diagnostic detail (captured at "debug" - see prepare.ts's
 * workspace settings.json), not just the mocha assertion message.
 */
function dumpServerLogOnFailure(): void {
    const logPath = findPyglsLogPath();
    if (!logPath) {
        console.error(`No pygls-<pid>.log found in ${os.tmpdir()}`);
        return;
    }
    try {
        const contents = fs.readFileSync(logPath, "utf-8");
        const tail = contents.split("\n").slice(-200).join("\n");
        console.error(`--- pygls log tail (${logPath}) ---\n${tail}`);
    } catch (readError) {
        console.error(`Could not read ${logPath}: ${readError}`);
    }
}

async function main(): Promise<void> {
    const scenarios: SmokeScenarios = JSON.parse(
        fs.readFileSync(resolveRepoPath("tests/e2e/e2e_smoke.json"), "utf-8")
    );

    const { extensionDevelopmentPath, workspaceDir, shortDataRoot } = prepareExtension(
        scenarios.completionDotTrigger.sampleText
    );
    const extensionTestsPath = path.resolve(__dirname, "suite", "index");

    // VSCODE_TEST_EXECUTABLE is set by the Bazel `js_test` target to the
    // pinned, hermetically-fetched VSCode binary (see MODULE.bazel's
    // vscode_test extension). Left unset, @vscode/test-electron downloads
    // and caches its own copy - keeps `pnpm test:e2e` usable outside Bazel.
    const vscodeExecutablePath = process.env.VSCODE_TEST_EXECUTABLE
        ? resolveRepoPath(process.env.VSCODE_TEST_EXECUTABLE)
        : undefined;

    try {
        await runTests({
            vscodeExecutablePath,
            extensionDevelopmentPath,
            extensionTestsPath,
            launchArgs: [
                workspaceDir,
                "--no-sandbox",
                `--user-data-dir=${path.join(shortDataRoot, "user-data")}`,
                `--extensions-dir=${path.join(shortDataRoot, "extensions")}`,
            ],
        });
    } catch (error) {
        console.error("VSCode e2e smoke suite failed:", error);
        dumpServerLogOnFailure();
        process.exit(1);
    }
}

main();
