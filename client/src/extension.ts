/* -------------------------------------------------------------------------
 * Original work Copyright (c) Microsoft Corporation. All rights reserved.
 * Original work licensed under the MIT License.
 * See ThirdPartyNotices.txt in the project root for license information.
 * All modifications Copyright (c) Open Law Library. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License")
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http: // www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 * ----------------------------------------------------------------------- */
"use strict";

import * as path from "path";
import { ExtensionContext, ExtensionMode, commands, window, workspace } from "vscode";
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
} from "vscode-languageclient/node";

let client: LanguageClient;

function getClientOptions(): LanguageClientOptions {
    return {
        // Register the server for plain text documents
        documentSelector: [
            { scheme: 'file', pattern: '**/*.rsl' },
            { scheme: 'file', pattern: '**/*.trlc' }
        ],
        outputChannelName: "[pygls] TrlcLanguageServer",
        synchronize: {
            // Notify the server about file changes to '.clientrc files contain in the workspace
            fileEvents: workspace.createFileSystemWatcher("**/.clientrc"),
        },
    };
}

function startLangServer(
    command: string,
    args: string[],
    cwd: string,
    pythonPath: string
): LanguageClient {
    const env = { ...process.env, PYTHONPATH: pythonPath };
    const serverOptions: ServerOptions = {
        args,
        command,
        options: { cwd, env },
    };

    return new LanguageClient(command, serverOptions, getClientOptions());
}

import { execFile } from 'child_process';
import * as fs from 'fs';

async function setup(context: ExtensionContext): Promise<void> {
    const targetDirectory = path.join(context.extensionPath, 'python-deps');
    const trlcMarker = path.join(targetDirectory, 'trlc');

    const needsInstall = !fs.existsSync(trlcMarker);

    if (needsInstall) {
        console.log("Running setup script...");
        try {
            await installPythonPackage(targetDirectory, 'lsprotocol>=2025.0.0');
            await installPythonPackage(targetDirectory, 'pygls==2.1.1');
            await installPythonPackage(targetDirectory, 'trlc>=2.0.5');
        } catch (error) {
            console.error("Setup failed during package installation", error);
        }
    }
}

function installPythonPackage(targetDirectory: string, packageName: string): Promise<string> {
    return new Promise((resolve, reject) => {
        const python = process.platform === 'win32' ? 'python' : 'python3';
        const args = ['-m', 'pip', 'install', '--target', targetDirectory, '--upgrade', packageName];

        execFile(python, args, (error, stdout, stderr) => {
            if (error) {
                console.error(`Error installing package: ${stderr}`);
                reject(error);
            } else {
                console.log(`Package installed successfully: ${stdout}`);
                resolve(stdout);
            }
        });
    });
}
export async function activate(context: ExtensionContext): Promise<void> {
    const depsPath = path.join(context.extensionPath, 'python-deps');

    context.subscriptions.push(
        commands.registerCommand('extension.resetState', async () => {
            try {
                await fs.promises.rm(depsPath, { recursive: true, force: true });
                window.showInformationMessage(
                    'TRLC: python-deps removed. Reload the window to reinstall dependencies.');
            } catch (error) {
                console.error('Failed to remove python-deps:', error);
            }
        })
    );

    try {
        await setup(context);
        console.log("Setup completed successfully.");
    } catch (error) {
        console.error("Setup failed.", error);
    }

    const cwd = path.join(__dirname, "..", "..");
    const pythonInterpreter = workspace
        .getConfiguration("python")
        .get<string>("defaultInterpreterPath");

    if (!pythonInterpreter) {
        throw new Error("`python.defaultInterpreterPath` is not set");
    }

    if (context.extensionMode === ExtensionMode.Development) {
        console.log("Activate server");
        client = startLangServer(pythonInterpreter, ["-m", "trlc_lsp"], cwd, depsPath);
        // Development - Run the server manually
        // client = startLangServerTCP(2087);
    } else {
        // Production - Client is going to run the server (for use within `.vsix` package)
        client = startLangServer(pythonInterpreter, ["-O", "-m", "trlc_lsp"], cwd, depsPath);
    }

    await client.start();
}

export function deactivate(): Thenable<void> {
    return client ? client.stop() : Promise.resolve();
}
