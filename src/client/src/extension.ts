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

/* -------------------------------------------------------------------------
 * This file is derived from the VS Code language-server extension sample:
 *   Original work Copyright (c) Microsoft Corporation. All rights reserved.
 *     Licensed under the MIT License.
 *   Modifications  Copyright (c) Open Law Library. All rights reserved.
 *     Licensed under the Apache License, Version 2.0.
 *
 * See ThirdPartyNotices.txt in the project root for the full original
 * license texts. Modifications by BMW AG are licensed under the GNU GPL
 * v3 as stated in the header above.
 * ----------------------------------------------------------------------- */
"use strict";

import * as path from "path";
import { commands, ExtensionContext, ExtensionMode, LogOutputChannel, TextEditor, languages, window, workspace } from "vscode";
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
} from "vscode-languageclient/node";

import { createLogger, Logger } from "./log";
import { ensureDependencies, registerResetCommand } from "./installer";
import { buildServerEnv, resolveInterpreter } from "./serverEnv";
import { ScopeStatus, TrlcStatusBarItem } from "./statusBar";
import { toScopeFileItems } from "./statusBarFormat";

let client: LanguageClient;
let statusBar: TrlcStatusBarItem;

/** Matches trlc-language-configuration.json's `languages[0].id`. */
const TRLC_LANGUAGE_ID = "TRLC";
/** Custom requests handled by server/handlers/status.py. */
const SCOPE_STATUS_METHOD = "trlc/scopeStatus";
const SCOPE_FILES_METHOD = "trlc/scopeFiles";

/** Shared by both the LSP client (protocol trace + server log-message
 * notifications) and this extension's own logger, so everything reads as
 * one stream in the panel the extension already tells users to check. */
const OUTPUT_CHANNEL_NAME = "[pygls] TrlcLanguageServer";

/** Reads the single `trlcServer.logLevel` setting shared with the server. */
function getConfiguredLogLevel(): string {
    return workspace.getConfiguration("trlcServer").get<string>("logLevel", "warning");
}

/** When `trlcServer.logLevel` is "debug", auto-promote
 * `trlcServer.trace.server` to "messages" so wire-level LSP tracing turns
 * on too — the two settings serve different purposes (application logs vs.
 * raw JSON-RPC traffic) so both stay, but debugging usually wants both at
 * once. Never overrides a value the user set themselves (checked via
 * `inspect()`), so a deliberate `trace.server: "off"` while `logLevel:
 * "debug"` stays respected. */
function syncTraceWithLogLevel(): void {
    const config = workspace.getConfiguration("trlcServer");
    if (config.get<string>("logLevel") !== "debug") {
        return;
    }
    const inspected = config.inspect<string>("trace.server");
    const hasExplicitValue =
        inspected?.globalValue !== undefined ||
        inspected?.workspaceValue !== undefined ||
        inspected?.workspaceFolderValue !== undefined;
    if (hasExplicitValue) {
        return;
    }
    void config.update("trace.server", "messages");
}

function getClientOptions(channel: LogOutputChannel): LanguageClientOptions {
    return {
        // Register the server for plain text documents
        documentSelector: [
            { scheme: 'file', pattern: '**/*.rsl' },
            { scheme: 'file', pattern: '**/*.trlc' }
        ],
        outputChannel: channel,
        synchronize: {
            // Notify the server about TRLC source file changes, plus Bazel
            // BUILD-graph files (BUILD/BUILD.bazel/*.bzl/WORKSPACE/MODULE.bazel)
            // — a target/dep added or removed there needs to be seen too, or
            // Bazel mode's scope silently goes stale until something else
            // happens to trigger a reparse. See lifecycle.py's
            // on_watched_files_change for how the server reacts differently
            // to each.
            fileEvents: workspace.createFileSystemWatcher(
                "**/{BUILD,BUILD.bazel,WORKSPACE,WORKSPACE.bazel,MODULE.bazel,REPO.bazel,*.bzl,*.rsl,*.trlc}"
            ),
        },
        middleware: {
            // Owns parse progress display via the status bar item instead of
            // the default transient toast — `next` is deliberately not
            // called. `statusBar` is assigned in activate() before the
            // client is started, so it's always set by the time the server
            // sends its first $/progress notification.
            handleWorkDoneProgress: (_token, params) => {
                if (!statusBar) {
                    return;
                }
                if (params.kind === "begin" || params.kind === "report") {
                    statusBar.setParsing({ active: true, percentage: params.percentage });
                } else {
                    statusBar.setParsing({ active: false });
                    void refreshScopeStatus(window.activeTextEditor);
                }
            },
        },
    };
}

function updateStatusBarVisibility(editor: TextEditor | undefined): void {
    if (!editor) {
        // No active editor doesn't mean "switched to a different file" — it
        // also fires when focus moves to a non-editor panel (e.g. Output,
        // triggered by clicking the status bar item itself). Leave the
        // item's current visibility alone rather than hiding it out from
        // under a click.
        return;
    }
    if (editor.document.languageId === TRLC_LANGUAGE_ID) {
        statusBar.show();
    } else {
        statusBar.hide();
    }
}

/** Asks the server for the active editor's scope status and re-renders the status bar. */
async function refreshScopeStatus(editor: TextEditor | undefined): Promise<void> {
    if (!client || !editor || editor.document.languageId !== TRLC_LANGUAGE_ID) {
        return;
    }
    try {
        const status = await client.sendRequest<ScopeStatus | null>(SCOPE_STATUS_METHOD, {
            uri: editor.document.uri.toString(),
        });
        statusBar.setStatus(status);
    } catch {
        // Server not ready yet, or the request isn't understood (older
        // server build) - keep showing the last known status.
    }
}

/** Fetches the active editor's scope file list and lets the user pick one
 * to open. Wired as the status bar item's click command — a click is more
 * useful showing "what's actually in scope right now" than opening the raw
 * output channel (still reachable via "TRLC: Show Output" in the palette). */
async function showFilesInScope(): Promise<void> {
    const editor = window.activeTextEditor;
    if (!client || !editor || editor.document.languageId !== TRLC_LANGUAGE_ID) {
        window.showInformationMessage(
            "TRLC: Open a .trlc/.rsl file to see its parse scope."
        );
        return;
    }
    let response: { files: string[] } | null;
    try {
        response = await client.sendRequest<{ files: string[] } | null>(
            SCOPE_FILES_METHOD,
            { uri: editor.document.uri.toString() }
        );
    } catch {
        window.showWarningMessage("TRLC: Could not fetch files in scope.");
        return;
    }
    if (!response || response.files.length === 0) {
        window.showInformationMessage(
            "TRLC: No files in scope for this file yet."
        );
        return;
    }

    const picked = await window.showQuickPick(toScopeFileItems(response.files), {
        placeHolder: `TRLC: ${response.files.length} file(s) in scope`,
        matchOnDescription: true,
    });
    if (picked) {
        const document = await workspace.openTextDocument(picked.path);
        await window.showTextDocument(document);
    }
}

function registerStatusBar(context: ExtensionContext, clickCommandId: string): void {
    statusBar = new TrlcStatusBarItem(clickCommandId);
    context.subscriptions.push(statusBar);

    updateStatusBarVisibility(window.activeTextEditor);
    void refreshScopeStatus(window.activeTextEditor);

    context.subscriptions.push(
        window.onDidChangeActiveTextEditor((editor) => {
            updateStatusBarVisibility(editor);
            void refreshScopeStatus(editor);
        })
    );

    context.subscriptions.push(
        languages.onDidChangeDiagnostics((event) => {
            const editor = window.activeTextEditor;
            if (!editor) {
                return;
            }
            const activeUri = editor.document.uri.toString();
            if (event.uris.some((uri) => uri.toString() === activeUri)) {
                void refreshScopeStatus(editor);
            }
        })
    );

    context.subscriptions.push(
        workspace.onDidChangeConfiguration((event) => {
            if (event.affectsConfiguration("trlcServer.logLevel")) {
                syncTraceWithLogLevel();
            }
            if (event.affectsConfiguration("trlcServer")) {
                void refreshScopeStatus(window.activeTextEditor);
            }
        })
    );
}

function startLangServer(
    command: string,
    args: string[],
    cwd: string,
    pythonPath: string,
    channel: LogOutputChannel
): LanguageClient {
    const env = buildServerEnv(pythonPath, process.env);
    const serverOptions: ServerOptions = {
        args,
        command,
        options: { cwd, env },
    };

    return new LanguageClient(
        "trlcServer",
        "TRLC Language Server",
        serverOptions,
        getClientOptions(channel)
    );
}

export async function activate(context: ExtensionContext): Promise<void> {
    const channel = window.createOutputChannel(OUTPUT_CHANNEL_NAME, { log: true });
    context.subscriptions.push(channel);
    const logger: Logger = createLogger(
        channel, getConfiguredLogLevel, (msg) => window.showErrorMessage(msg)
    );

    syncTraceWithLogLevel();

    const depsPath = path.join(context.extensionPath, "python-deps");

    registerResetCommand(context, depsPath, logger);
    context.subscriptions.push(
        commands.registerCommand("extension.showTrlcOutput", () => channel.show())
    );
    context.subscriptions.push(
        commands.registerCommand("extension.showTrlcFilesInScope", showFilesInScope)
    );

    let pythonInterpreter: string;
    try {
        pythonInterpreter = resolveInterpreter(
            workspace.getConfiguration("python").get<string>("defaultInterpreterPath")
        );
    } catch (error) {
        logger.error(`TRLC: ${(error as Error).message}`, { notify: true });
        throw error;
    }

    await ensureDependencies(context, pythonInterpreter, logger);

    const cwd = path.join(__dirname, "..", "..");

    if (context.extensionMode === ExtensionMode.Development) {
        logger.info("Activate server");
        client = startLangServer(pythonInterpreter, ["-m", "server"], cwd, depsPath, channel);
    } else {
        // Production - Client is going to run the server (for use within `.vsix` package)
        client = startLangServer(pythonInterpreter, ["-O", "-m", "server"], cwd, depsPath, channel);
    }

    // Created before client.start() so the middleware's handleWorkDoneProgress
    // (registered as part of client's options) always finds it set.
    registerStatusBar(context, "extension.showTrlcFilesInScope");

    try {
        await client.start();
    } catch (error) {
        logger.error(
            `TRLC: Language server failed to start. Check the ` +
            `"${OUTPUT_CHANNEL_NAME}" output channel for details. Error: ${error}`,
            { notify: true }
        );
        throw error;
    }

    // Retries the request that registerStatusBar's initial call likely
    // skipped (client wasn't running yet at that point).
    void refreshScopeStatus(window.activeTextEditor);
}

export function deactivate(): Thenable<void> {
    return client ? client.stop() : Promise.resolve();
}
