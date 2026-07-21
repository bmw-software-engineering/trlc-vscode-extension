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
 * Pure / dependency-injectable pieces of the Python dependency installer.
 *
 * Deliberately has no `vscode` import, so it can be unit-tested with plain
 * mocha/node — importing `vscode` outside the extension host throws
 * "Cannot find module 'vscode'". installer.ts (the vscode-facing entrypoint)
 * calls into this module.
 */

import { execFile, ExecFileException } from "child_process";
import * as fs from "fs";

/** The packages installed into `python-deps/` on first activation. */
export const REQUIRED_PACKAGES: readonly string[] = [
    "lsprotocol>=2025.0.0",
    "pygls==2.1.1",
    "trlc>=3.0.0",
];

/**
 * Build the `pip install` argument vector for one package.
 *
 * Exposed separately from `runPipInstall` so tests can assert on the exact
 * command without spawning a process.
 */
export function buildPipInstallArgs(
    targetDirectory: string,
    packageName: string
): string[] {
    return [
        "-m", "pip", "install",
        "--target", targetDirectory,
        "--upgrade",
        packageName,
    ];
}

/** Signature of Node's `child_process.execFile`, narrowed to what we use. */
export type ExecFileFn = (
    file: string,
    args: readonly string[],
    options: { timeout: number },
    callback: (
        error: ExecFileException | null,
        stdout: string,
        stderr: string
    ) => void
) => void;

/** Signature of a plain log sink, so this vscode-free module never needs
 * a `vscode` import to report failures - see the module docstring. */
export type LogFn = (message: string) => void;

/**
 * Run `pip install --target <targetDirectory> --upgrade <packageName>`.
 *
 * `execFileImpl` defaults to Node's real `child_process.execFile` but can be
 * replaced with a stub in tests to exercise the failure/timeout paths
 * without actually invoking pip. `logImpl` defaults to `console.error` but
 * callers with a real output channel (installer.ts) pass their own logger.
 */
export function runPipInstall(
    python: string,
    targetDirectory: string,
    packageName: string,
    execFileImpl: ExecFileFn = execFile as unknown as ExecFileFn,
    logImpl: LogFn = console.error
): Promise<void> {
    const args = buildPipInstallArgs(targetDirectory, packageName);
    return new Promise((resolve, reject) => {
        execFileImpl(python, args, { timeout: 120_000 }, (error, _stdout, stderr) => {
            if (error) {
                logImpl(`TRLC: pip install failed for ${packageName}: ${stderr}`);
                if (error.killed && error.signal === "SIGTERM") {
                    reject(new Error(`pip install timed out for ${packageName}`));
                } else {
                    reject(error);
                }
            } else {
                resolve();
            }
        });
    });
}

/** Signature of Node's `fs.existsSync`, narrowed to what we use. */
export type ExistsSyncFn = (path: string) => boolean;

/**
 * Install every package in `REQUIRED_PACKAGES` into `targetDirectory`,
 * unless `markerPath` already exists (the "already installed" sentinel —
 * see `ensureDependencies` in installer.ts).
 *
 * Returns `true` if installation ran, `false` if it was skipped because the
 * marker was already present. `existsSyncImpl`/`runPipInstallImpl` default
 * to the real implementations but can be replaced with stubs in tests.
 * `logImpl` defaults to `console.log`; installer.ts passes its own logger
 * so status lines land in the shared output channel instead.
 */
export async function installMissing(
    python: string,
    targetDirectory: string,
    markerPath: string,
    existsSyncImpl: ExistsSyncFn = fs.existsSync,
    runPipInstallImpl: typeof runPipInstall = runPipInstall,
    logImpl: LogFn = console.log
): Promise<boolean> {
    if (existsSyncImpl(markerPath)) {
        return false;
    }
    logImpl("TRLC: Installing Python dependencies...");
    for (const packageName of REQUIRED_PACKAGES) {
        await runPipInstallImpl(python, targetDirectory, packageName);
    }
    logImpl("TRLC: Python dependencies installed successfully.");
    return true;
}

/** Signature of Node's `fs.promises.rm`, narrowed to what we use. */
export type RmFn = (
    path: string,
    options: { recursive: boolean; force: boolean }
) => Promise<void>;

/**
 * Remove `depsPath` (the `python-deps/` directory) so dependencies are
 * re-installed on next activation. `rmImpl` defaults to the real
 * `fs.promises.rm` but can be replaced with a stub in tests to exercise the
 * failure path without touching the filesystem.
 */
export function removeDeps(
    depsPath: string,
    rmImpl: RmFn = fs.promises.rm as unknown as RmFn
): Promise<void> {
    return rmImpl(depsPath, { recursive: true, force: true });
}
