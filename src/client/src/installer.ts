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
 * Python dependency installation for the TRLC language server.
 *
 * Packages are installed into a local `python-deps/` directory inside the
 * extension folder so they do not pollute the user's Python environment.
 * The presence of the `trlc/` sub-directory is used as a marker to skip
 * installation on subsequent activations.
 */

import * as path from "path";
import { commands, ExtensionContext, window } from "vscode";

import { installMissing, removeDeps } from "./installerCore";
import { Logger } from "./log";

/**
 * Ensure all required Python packages are present in `python-deps/`.
 *
 * On first activation (marker directory absent) each package is installed
 * with `pip install --target`.  If any installation fails the error is
 * shown to the user and re-thrown so the caller can abort activation.
 *
 * @param context  The VS Code extension context.
 * @param python   Path to the Python interpreter to use for pip.
 * @param logger   Writes to the shared output channel; notifies on failure.
 */
export async function ensureDependencies(
    context: ExtensionContext,
    python: string,
    logger: Logger
): Promise<void> {
    const targetDirectory = path.join(context.extensionPath, "python-deps");
    const trlcMarker = path.join(targetDirectory, "trlc");

    try {
        await installMissing(
            python, targetDirectory, trlcMarker, undefined, undefined, logger.info
        );
    } catch (error) {
        logger.error(
            `TRLC: Dependency installation failed — the language server will ` +
            `not start. Check the Output panel for details. Error: ${error}`,
            { notify: true }
        );
        throw error; // propagate so activate() stops cleanly
    }
}

/**
 * Register the `extension.resetState` command that removes the local
 * `python-deps/` directory so dependencies are re-installed on next reload.
 *
 * @param context  The VS Code extension context.
 * @param depsPath  Absolute path to the `python-deps/` directory.
 * @param logger   Writes to the shared output channel; notifies on failure.
 */
export function registerResetCommand(
    context: ExtensionContext,
    depsPath: string,
    logger: Logger
): void {
    context.subscriptions.push(
        commands.registerCommand(
            "extension.resetState",
            async () => {
                try {
                    await removeDeps(depsPath);
                    window.showInformationMessage(
                        "TRLC: python-deps removed. " +
                        "Reload the window to reinstall dependencies."
                    );
                } catch (error) {
                    // Single call site (was console.error + showErrorMessage
                    // duplicated here before) - see log.ts's Logger.error.
                    logger.error(
                        `TRLC: Failed to remove python-deps: ${error}`,
                        { notify: true }
                    );
                }
            }
        )
    );
}

