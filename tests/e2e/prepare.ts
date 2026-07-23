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

let runfilesManifest: Map<string, string> | undefined;

/**
 * Parses the Bazel runfiles manifest (RUNFILES_MANIFEST_FILE), if any, into
 * an rlocation-path -> real-path map. Each line is "<key> <value>", split on
 * the first space only - keys (Bazel-controlled repo/pkg/target strings)
 * never contain one, but values (real filesystem paths) can, e.g. this
 * suite's own "Visual Studio Code.app/...".
 */
function loadRunfilesManifest(): Map<string, string> | undefined {
    if (runfilesManifest) {
        return runfilesManifest;
    }
    const manifestPath = process.env.RUNFILES_MANIFEST_FILE;
    if (!manifestPath || !fs.existsSync(manifestPath)) {
        return undefined;
    }
    const manifest = new Map<string, string>();
    for (const line of fs.readFileSync(manifestPath, "utf-8").split(/\r?\n/)) {
        const sep = line.indexOf(" ");
        if (sep === -1) {
            continue;
        }
        manifest.set(line.slice(0, sep), line.slice(sep + 1));
    }
    runfilesManifest = manifest;
    return manifest;
}

/**
 * Dumps the runfiles-related environment to stderr - only called right
 * before falling through to the last-resort guess in resolveRepoPath, so a
 * failure (like the macOS ENOENT this is diagnosing) shows in CI output
 * exactly what Bazel actually gave this process to work with, instead of
 * guessing again blind.
 */
function logRunfilesDiagnostics(relative: string): void {
    const manifest = loadRunfilesManifest();
    console.error(`[resolveRepoPath] could not resolve "${relative}" via runfiles`);
    console.error(`[resolveRepoPath] RUNFILES_MANIFEST_FILE=${process.env.RUNFILES_MANIFEST_FILE ?? "(unset)"}`);
    console.error(`[resolveRepoPath] RUNFILES_DIR=${process.env.RUNFILES_DIR ?? "(unset)"}`);
    console.error(`[resolveRepoPath] JS_BINARY__RUNFILES=${process.env.JS_BINARY__RUNFILES ?? "(unset)"}`);
    console.error(`[resolveRepoPath] TEST_SRCDIR=${process.env.TEST_SRCDIR ?? "(unset)"}`);
    if (manifest) {
        const repoPrefix = relative.split("/")[0];
        const sample = [...manifest.keys()].filter((k) => k.startsWith(repoPrefix)).slice(0, 5);
        console.error(
            `[resolveRepoPath] manifest has ${manifest.size} entries; ` +
                `${sample.length} keys starting with "${repoPrefix}": ${JSON.stringify(sample)}`
        );
    }
}

/**
 * Resolves a $(rlocationpath) value (e.g. "_main/tests/e2e/extension_dev_dir"
 * or, for an external repo, "+vscode_test+vscode_test_darwin_arm64/...")
 * against Bazel's own runfiles-discovery protocol - RUNFILES_MANIFEST_FILE
 * when the runfiles tree isn't a real symlink forest (observed on macOS test
 * execution: external-repo entries aren't materialized on disk the way
 * `main`-repo ones are, so guessing candidate directory shapes and checking
 * existsSync silently falls through to a wrong path), else RUNFILES_DIR /
 * JS_BINARY__RUNFILES (the latter is aspect_rules_js's own guaranteed
 * runfiles-root env var for js_binary/js_test - see bash.bzl's
 * BASH_INITIALIZE_RUNFILES - kept as a fallback in case RUNFILES_DIR isn't
 * propagated to the spawned Node process in this rules_js version). Returns
 * undefined if none of these resolve it, e.g. outside `bazel test`.
 */
function resolveRlocation(relative: string): string | undefined {
    const manifestHit = loadRunfilesManifest()?.get(relative);
    if (manifestHit) {
        return manifestHit;
    }
    for (const runfilesDir of [
        process.env.RUNFILES_DIR,
        process.env.JS_BINARY__RUNFILES,
    ]) {
        if (!runfilesDir) {
            continue;
        }
        const candidate = path.join(runfilesDir, relative);
        if (fs.existsSync(candidate)) {
            return candidate;
        }
    }
    return undefined;
}

/**
 * Resolves a path against the Bazel runfiles tree, accepting both plain
 * repo-relative paths (e.g. "tests/fixtures") and $(rlocationpath) values.
 * Tries Bazel's own runfiles resolution first (see resolveRlocation); falls
 * back to cwd-relative guessing (cwd under `bazel test` is already inside
 * the main repo's runfiles subtree) and finally to walking up from this file
 * for local (non-Bazel) `pnpm test:e2e` runs.
 */
export function resolveRepoPath(relative: string): string {
    // Strip a single layer of surrounding quotes. The macOS runfiles value
    // for the pinned VSCode binary ($(rlocationpath @vscode_test_darwin_arm64
    // //:code)) is "+vscode_test+.../Visual Studio Code.app/.../Electron" -
    // it contains a space ("Visual Studio Code.app"), so aspect_rules_js's
    // js_binary launcher shell-quotes the value when it exports it into the
    // env, and those literal quotes leak through into VSCODE_TEST_EXECUTABLE.
    // Left in, every runfiles/manifest lookup below misses (the real key has
    // no quotes) and the path spawns as .../_main/'+vscode_test.../Electron'
    // -> ENOENT. Bazel repo/target strings and real runfiles paths never
    // start with a quote, so this only ever unwraps that leaked quoting.
    relative = relative.replace(/^(['"])(.*)\1$/, "$2");
    const viaRunfiles = resolveRlocation(relative);
    if (viaRunfiles) {
        return viaRunfiles;
    }
    const candidates = [
        path.resolve(process.cwd(), relative),
        path.resolve(process.cwd(), "..", relative),
    ];
    for (const candidate of candidates) {
        if (fs.existsSync(candidate)) {
            return candidate;
        }
    }
    logRunfilesDiagnostics(relative);
    return path.resolve(__dirname, "..", "..", relative);
}

function copyRecursive(src: string, dest: string): void {
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
        const srcPath = path.join(src, entry.name);
        const destPath = path.join(dest, entry.name);
        if (entry.isDirectory()) {
            copyRecursive(srcPath, destPath);
        } else {
            fs.copyFileSync(srcPath, destPath);
            // Bazel runfiles sources are read-only; copyFileSync carries
            // that bit over to the copy, but this tree needs to stay
            // writable (sample.trlc gets overwritten below, VSCode itself
            // writes workspace storage, etc).
            fs.chmodSync(destPath, 0o644);
        }
    }
}

export interface PreparedExtension {
    /** Writable copy of the packaged extension (unzipped-vsix shape). */
    extensionDevelopmentPath: string;
    /** Writable workspace folder containing the copied test fixtures. */
    workspaceDir: string;
    /**
     * Short (`/tmp/...`) user-data-dir/extensions-dir root. @vscode/test-
     * electron defaults both under cwd/.vscode-test/ - under Bazel that's a
     * deeply nested runfiles path, long enough to blow the ~107 char AF_UNIX
     * socket path limit on the singleton-instance lock socket it creates
     * there.
     */
    shortDataRoot: string;
}

/**
 * Assembles a writable extension install dir + workspace for one e2e run:
 *  - copies the Bazel-built "unzipped vsix" (EXTENSION_DEV_DIR) so
 *    `python-deps/` can be seeded into it (Bazel runfiles are read-only)
 *  - seeds `python-deps/` with the real trlc/pygls/lsprotocol packages
 *    (PYTHON_DEPS_SEED) so `ensureDependencies()` skips its pip install
 *  - copies tests/fixtures/ into a workspace folder, optionally overwriting
 *    sample.trlc with scenario-specific content
 */
export function prepareExtension(sampleTrlcOverride?: string): PreparedExtension {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "trlc-e2e-"));

    const extensionDevelopmentPath = path.join(root, "extension");
    const extensionDevDirSrc = process.env.EXTENSION_DEV_DIR;
    if (!extensionDevDirSrc) {
        throw new Error("EXTENSION_DEV_DIR env var must point at the built extension directory");
    }
    copyRecursive(resolveRepoPath(extensionDevDirSrc), extensionDevelopmentPath);

    const pythonDepsSeedSrc = process.env.PYTHON_DEPS_SEED;
    if (!pythonDepsSeedSrc) {
        throw new Error("PYTHON_DEPS_SEED env var must point at the seeded python-deps directory");
    }
    copyRecursive(
        resolveRepoPath(pythonDepsSeedSrc),
        path.join(extensionDevelopmentPath, "python-deps")
    );

    const workspaceDir = path.join(root, "workspace");
    copyRecursive(resolveRepoPath("tests/fixtures"), workspaceDir);
    // e2e_smoke.json has moved to tests/e2e/ (it is only read by this e2e
    // suite, not by the pytest-lsp integration suite).  Copy it explicitly
    // into the workspace so smoke.e2e.ts — running inside a separate Electron
    // extension-host process with its own cwd — can still read it via the
    // VSCode workspace-folder API without guessing a Bazel runfiles path.
    fs.copyFileSync(
        resolveRepoPath("tests/e2e/e2e_smoke.json"),
        path.join(workspaceDir, "e2e_smoke.json")
    );
    fs.chmodSync(path.join(workspaceDir, "e2e_smoke.json"), 0o644);

    if (sampleTrlcOverride !== undefined) {
        fs.writeFileSync(path.join(workspaceDir, "sample.trlc"), sampleTrlcOverride);
    }

    // package.json declares "extensionDependencies": ["ms-python.python"] -
    // VSCode won't activate an extension whose declared dependency isn't
    // installed. Installing the real (huge, marketplace-fetched) Python
    // extension would defeat the point of a hermetic test, and our own
    // code only reads the plain "python.defaultInterpreterPath" *setting*
    // (not any ms-python.python API) - so a bare, code-free stub satisfying
    // just the id/version is enough to unblock activation.
    const stubDir = path.join(root, "extensions", "ms-python.python-0.0.1");
    fs.mkdirSync(stubDir, { recursive: true });
    fs.writeFileSync(
        path.join(stubDir, "package.json"),
        JSON.stringify(
            {
                name: "python",
                displayName: "Python (e2e stub)",
                publisher: "ms-python",
                version: "0.0.1",
                engines: { vscode: "^1.91.0" },
            },
            null,
            4
        )
    );

    fs.mkdirSync(path.join(workspaceDir, ".vscode"), { recursive: true });
    fs.writeFileSync(
        path.join(workspaceDir, ".vscode", "settings.json"),
        JSON.stringify(
            {
                // Packages come from the seeded python-deps/ above (via
                // PYTHONPATH, see serverEnv.ts), so any working interpreter
                // does - no trlc/pygls/lsprotocol needs to be preinstalled
                // on it.
                "python.defaultInterpreterPath":
                    process.env.E2E_PYTHON_EXECUTABLE ?? "/usr/bin/python3",
                // Maximum verbosity so a failed run's pygls-<pid>.log
                // (dumped by runTest.ts on failure) has the most detail to
                // diagnose from, without needing to reproduce interactively
                // first.
                "trlcServer.logLevel": "debug",
            },
            null,
            4
        )
    );

    return { extensionDevelopmentPath, workspaceDir, shortDataRoot: root };
}
