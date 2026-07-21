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
 * Pure environment-variable helpers for launching the language server.
 *
 * Deliberately has no `vscode` import: it is invoked from extension.ts (the
 * vscode-facing entrypoint) but lives here so it can be unit-tested with
 * plain mocha/node — importing `vscode` outside the extension host throws
 * "Cannot find module 'vscode'".
 */

import * as path from "path";

/**
 * Build the environment for the language server subprocess.
 *
 * Prepends `pythonPath` (the bundled `python-deps/` directory) to any
 * existing `PYTHONPATH` in `baseEnv` rather than overwriting it, so a user's
 * own `PYTHONPATH` (system packages, other extensions' Python tooling) keeps
 * working alongside the bundled server dependencies.
 */
export function buildServerEnv(
    pythonPath: string,
    baseEnv: NodeJS.ProcessEnv
): NodeJS.ProcessEnv {
    const existing = baseEnv.PYTHONPATH;
    return {
        ...baseEnv,
        PYTHONPATH: existing ? `${pythonPath}${path.delimiter}${existing}` : pythonPath,
    };
}

/** Thrown by `resolveInterpreter` when no interpreter path is configured. */
export class MissingInterpreterError extends Error {}

/**
 * Validate the `python.defaultInterpreterPath` setting value.
 *
 * Pure so the "not configured" case (empty/undefined) can be unit-tested
 * without a live vscode workspace configuration.
 */
export function resolveInterpreter(rawValue: string | undefined): string {
    if (!rawValue) {
        throw new MissingInterpreterError(
            "`python.defaultInterpreterPath` is not set"
        );
    }
    return rawValue;
}
