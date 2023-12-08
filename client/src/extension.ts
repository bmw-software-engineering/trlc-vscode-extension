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

import * as os from 'os';
import * as path from "path";
import { spawn } from 'child_process';
import { ExtensionContext, window, workspace } from "vscode";
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
            { scheme: 'file', pattern: '**/*.trlc' },
            { scheme: 'file', pattern: '**/*.check' },
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

export function activate(context: ExtensionContext): void {
    const cwd = path.join(__dirname, "..", "..");
    const platform = os.platform();
    let pythonInterpreterPath = 'python3';
    if (platform === 'win32') {
        const batchScriptPath = path.join(cwd, "utils/install-python.bat");
        const childProcess = spawn('cmd', ['/c', batchScriptPath]);

        childProcess.stdout.on('data', (data) => {
            console.log(`stdout: ${data}`);
        });

        childProcess.stderr.on('data', (data) => {
            console.error(`stderr: ${data}`);
        });

        childProcess.on('exit', (code) => {
            if (code === 0) {
                console.log('Batch script executed successfully');
                pythonInterpreterPath = 'python';
                client = startLangServer(pythonInterpreterPath, ["-m", "server"], cwd);
                context.subscriptions.push(client.start());
            } else {
                throw new Error(`Batch script exited with code ${code}`);
            }
        });
    } else if (platform === 'darwin' || platform === 'linux') {
        client = startLangServer(pythonInterpreterPath, ["-m", "server"], cwd);
        // Development - Run the server manually
        // client = startLangServerTCP(2087);
        context.subscriptions.push(client.start());
    } else {
        window.showErrorMessage(`Platform ${platform} is not supported`);
    }
}

export function deactivate(): Thenable<void> {
    return client ? client.stop() : Promise.resolve();
}
