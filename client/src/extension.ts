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
import { ExtensionContext, ExtensionMode, workspace } from "vscode";
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
    cwd: string
): LanguageClient {
    const serverOptions: ServerOptions = {
        args,
        command,
        options: { cwd },
    };

    return new LanguageClient(command, serverOptions, getClientOptions());
}

import { exec } from 'child_process';

async function setup(context: ExtensionContext): Promise<void> {
    const isSetupDone = context.workspaceState.get<boolean>('setupDone', false);

    const targetDirectory = path.join(context.extensionPath, 'python-deps');

    if (!isSetupDone) {
        console.log("Running setup script...");
        try {
            await installPythonPackage(targetDirectory, 'cvc5==1.2.0');
            await context.workspaceState.update('setupDone', true);
        } catch (error) {
            console.error("Setup failed during package installation of CVC5", error);
        }
    }
}

function installPythonPackage(targetDirectory: string, packageName: string): Promise<string> {
    return new Promise((resolve, reject) => {
        const pipCommand = process.platform === 'win32' ? 'python -m pip' : 'python3 -m pip';
        const command = `${pipCommand} install --target ${targetDirectory} ${packageName}`;

        exec(command, (error, stdout, stderr) => {
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
import { commands } from 'vscode';
export async function activate(context: ExtensionContext): Promise<void> {
    context.subscriptions.push(
        commands.registerCommand('extension.resetState', async () => {
            await context.workspaceState.update('setupDone', undefined);
            console.log("State has been reset.");
        })
    )
    try {
        await setup(context);
        console.log("Setup completed successfully.");
    } catch (error) {
        console.error("Setup failed.", error);
    }

    if (context.extensionMode === ExtensionMode.Development) {
        const cwd = path.join(__dirname, "..", "..");
        console.log("Activate server");
        const pythonPath = workspace
            .getConfiguration("python")
            .get<string>("defaultInterpreterPath");

        if (!pythonPath) {
            throw new Error("`python.pythonPath` is not set");
        }

        client = startLangServer(pythonPath, ["-m", "server"], cwd);
        // Development - Run the server manually
        // client = startLangServerTCP(2087);
    } else {
        // workspace.getConfiguration("python"));
        // Production - Client is going to run the server (for use within `.vsix` package)
        const cwd = path.join(__dirname, "..", "..");
        const pythonPath = workspace
            .getConfiguration("python")
            .get<string>("defaultInterpreterPath");

        if (!pythonPath) {
            throw new Error("`python.pythonPath` is not set");
        }

        client = startLangServer(pythonPath, ["-O", "-m", "server"], cwd);
    }

    await client.start();
    // Expose API for example when a SYML element is hoovered over
    // this makes "getHoverDetails(uri, position)" function available to other extensions
    return {
        async getHoverDetails(uri: string, position: Position): Promise<Hover | null> {
            const params = { textDocument: { uri }, position };
            return await client.sendRequest("textDocument/hover", params);
        }
}

export function deactivate(): Thenable<void> {
    return client ? client.stop() : Promise.resolve();
}
