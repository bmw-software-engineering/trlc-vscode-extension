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
import { ExecFileException } from "child_process";

import {
    buildPipInstallArgs,
    installMissing,
    REQUIRED_PACKAGES,
    removeDeps,
    runPipInstall,
} from "../installerCore";

describe("buildPipInstallArgs", () => {
    it("builds the expected pip install argument vector", () => {
        // Given/When: building args for a target dir + package
        const args = buildPipInstallArgs("/ext/python-deps", "trlc>=3.0.0");

        // Then: matches `pip install --target <dir> --upgrade <package>`
        assert.deepStrictEqual(args, [
            "-m", "pip", "install",
            "--target", "/ext/python-deps",
            "--upgrade",
            "trlc>=3.0.0",
        ]);
    });
});

describe("REQUIRED_PACKAGES", () => {
    it("only ever contains the three hardcoded server dependencies", () => {
        // The installer never installs an externally-influenced package
        // name — this pins the exact, unparameterized list.
        assert.deepStrictEqual(REQUIRED_PACKAGES, [
            "lsprotocol>=2025.0.0",
            "pygls==2.1.1",
            "trlc>=3.0.0",
        ]);
    });
});

describe("runPipInstall", () => {
    it("resolves when the injected execFile succeeds", async () => {
        // Given: a stub execFile that succeeds
        const calls: unknown[][] = [];
        const stub = (
            file: string,
            args: readonly string[],
            options: { timeout: number },
            callback: (
                error: ExecFileException | null,
                stdout: string,
                stderr: string
            ) => void
        ) => {
            calls.push([file, args, options]);
            callback(null, "", "");
        };

        // When: running the install
        await runPipInstall("python3", "/deps", "trlc>=3.0.0", stub);

        // Then: it invoked execFile once with the expected arguments
        assert.strictEqual(calls.length, 1);
        assert.strictEqual(calls[0][0], "python3");
        assert.deepStrictEqual(calls[0][1], buildPipInstallArgs("/deps", "trlc>=3.0.0"));
    });

    it("rejects with a timeout-specific error when execFile times out", async () => {
        // Given: a stub execFile reporting a SIGTERM-killed (timed out) process
        const stub = (
            _file: string,
            _args: readonly string[],
            _options: { timeout: number },
            callback: (
                error: ExecFileException | null,
                stdout: string,
                stderr: string
            ) => void
        ) => {
            const err = new Error("terminated") as ExecFileException;
            err.killed = true;
            err.signal = "SIGTERM";
            callback(err, "", "");
        };

        // When/Then: the promise rejects with a message identifying the
        // package and that it timed out (not the raw execFile error)
        await assert.rejects(
            runPipInstall("python3", "/deps", "trlc>=3.0.0", stub),
            /timed out for trlc>=3\.0\.0/
        );
    });

    it("rejects with the original error on a non-timeout failure", async () => {
        // Given: a stub execFile reporting a plain failure (e.g. pip not found)
        const original = new Error("pip: command not found") as ExecFileException;
        const stub = (
            _file: string,
            _args: readonly string[],
            _options: { timeout: number },
            callback: (
                error: ExecFileException | null,
                stdout: string,
                stderr: string
            ) => void
        ) => {
            callback(original, "", "pip: command not found");
        };

        // When/Then: the original error propagates unchanged
        await assert.rejects(
            runPipInstall("python3", "/deps", "trlc>=3.0.0", stub),
            (err: Error) => err === original
        );
    });
});

describe("installMissing", () => {
    it("skips installation when the marker already exists", async () => {
        // Given: a marker path that already exists, and a pip stub that
        // would fail the test if it were ever invoked
        const existsSync = () => true;
        const runPipInstallImpl = async () => {
            throw new Error("must not be called when marker exists");
        };

        // When: installMissing is called
        const installed = await installMissing(
            "python3", "/deps", "/deps/trlc", existsSync, runPipInstallImpl
        );

        // Then: it reports "skipped" and never touched pip
        assert.strictEqual(installed, false);
    });

    it("installs every required package when the marker is absent", async () => {
        // Given: no marker on disk, and a pip stub recording each call
        const existsSync = () => false;
        const calls: string[] = [];
        const runPipInstallImpl = async (
            _python: string,
            _targetDirectory: string,
            packageName: string
        ) => {
            calls.push(packageName);
        };

        // When: installMissing is called
        const installed = await installMissing(
            "python3", "/deps", "/deps/trlc", existsSync, runPipInstallImpl
        );

        // Then: it reports "installed" and called pip once per package, in order
        assert.strictEqual(installed, true);
        assert.deepStrictEqual(calls, REQUIRED_PACKAGES);
    });

    it("propagates a failure from any package install and stops early", async () => {
        // Given: no marker on disk, and a pip stub that fails on the second package
        const existsSync = () => false;
        const calls: string[] = [];
        const failure = new Error("pip: network error");
        const runPipInstallImpl = async (
            _python: string,
            _targetDirectory: string,
            packageName: string
        ) => {
            calls.push(packageName);
            if (calls.length === 2) {
                throw failure;
            }
        };

        // When/Then: the failure propagates, and no package after the
        // failing one was attempted (partial-install state is visible to
        // the caller via the thrown error, not silently swallowed)
        await assert.rejects(
            installMissing("python3", "/deps", "/deps/trlc", existsSync, runPipInstallImpl),
            (err: Error) => err === failure
        );
        assert.strictEqual(calls.length, 2);
    });
});

describe("removeDeps", () => {
    it("removes the deps directory recursively and forcefully", async () => {
        // Given: an rm stub recording its call
        const calls: unknown[][] = [];
        const rmImpl = async (path: string, options: { recursive: boolean; force: boolean }) => {
            calls.push([path, options]);
        };

        // When: removeDeps is called
        await removeDeps("/ext/python-deps", rmImpl);

        // Then: it invoked rm once with recursive+force on the given path
        assert.strictEqual(calls.length, 1);
        assert.deepStrictEqual(calls[0], [
            "/ext/python-deps",
            { recursive: true, force: true },
        ]);
    });

    it("propagates a failure from the underlying rm call", async () => {
        // Given: an rm stub that fails (e.g. permission denied)
        const failure = new Error("EACCES: permission denied");
        const rmImpl = async () => {
            throw failure;
        };

        // When/Then: the failure propagates unchanged to the caller
        await assert.rejects(
            removeDeps("/ext/python-deps", rmImpl),
            (err: Error) => err === failure
        );
    });
});
