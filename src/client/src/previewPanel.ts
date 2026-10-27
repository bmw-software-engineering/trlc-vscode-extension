/* -------------------------------------------------------------------------
 * TRLC VSCode Extension
 * Copyright (C) 2026 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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

import * as path from "path";
import { randomBytes } from "crypto";
import { commands, ExtensionContext, Range, TextEditor, Uri, ViewColumn, WebviewPanel, window, workspace } from "vscode";
import { LanguageClient, WorkspaceEdit as LspWorkspaceEdit } from "vscode-languageclient/node";

import { PreviewController } from "./previewController";
import { HostToWebview, WebviewToHost } from "./previewProtocol";
import { previewShellHtml } from "./previewRender";

/** Matches trlc-language-configuration.json's `languages[0].id`. */
const TRLC_LANGUAGE_ID = "TRLC";
const OPEN_PREVIEW_COMMAND = "extension.openTrlcPreview";

function isTrlcEditor(editor: TextEditor | undefined): editor is TextEditor {
    return (
        editor !== undefined &&
        editor.document.languageId === TRLC_LANGUAGE_ID &&
        editor.document.uri.path.toLowerCase().endsWith(".trlc")
    );
}

export interface TrlcPreview {
    refresh(): void;
    isPreviewing(uri: string): boolean;
}

export function registerPreview(context: ExtensionContext, client: LanguageClient): TrlcPreview {
    let panel: WebviewPanel | undefined;

    const controller = new PreviewController(
        {
            sendRequest: (method, params) => client.sendRequest(method, params),
            applyEdit: async (edit) => {
                const converted = await client.protocol2CodeConverter.asWorkspaceEdit(edit as LspWorkspaceEdit);
                return converted !== undefined && (await workspace.applyEdit(converted));
            },
            showError: (message) => void window.showErrorMessage(message),
            reveal: (uri, line) => {
                void window.showTextDocument(Uri.parse(uri), {
                    viewColumn: ViewColumn.One,
                    selection: new Range(line, 0, line, 0),
                });
            },
        },
        (msg: HostToWebview) => {
            void panel?.webview.postMessage(msg);
        },
    );

    const follow = (editor: TextEditor): void => {
        const uri = editor.document.uri.toString();
        if (panel) {
            panel.title = `Preview ${path.basename(editor.document.uri.path)}`;
        }
        void controller.setDocument(uri);
    };

    const open = (arg?: unknown): void => {
        if (panel) {
            panel.reveal(undefined, true);
        } else {
            const outDir = Uri.joinPath(context.extensionUri, "src", "client", "out");
            const created = window.createWebviewPanel(
                "trlcPreview",
                "Preview",
                { viewColumn: ViewColumn.Beside, preserveFocus: true },
                { enableScripts: true, localResourceRoots: [outDir], retainContextWhenHidden: false },
            );
            panel = created;
            const nonce = randomBytes(16).toString("hex");
            const scriptUri = created.webview.asWebviewUri(Uri.joinPath(outDir, "preview.js"));
            created.webview.html = previewShellHtml(nonce, created.webview.cspSource, scriptUri.toString());
            created.webview.onDidReceiveMessage((msg: WebviewToHost) => void controller.handleMessage(msg));
            created.onDidDispose(() => {
                panel = undefined;
                void controller.setDocument(undefined);
            });
        }
        if (arg instanceof Uri) {
            if (arg.path.toLowerCase().endsWith(".trlc")) {
                if (panel) {
                    panel.title = `Preview ${path.basename(arg.path)}`;
                }
                void controller.setDocument(arg.toString());
            }
            return;
        }
        const editor = window.activeTextEditor;
        if (isTrlcEditor(editor)) {
            follow(editor);
        }
    };

    context.subscriptions.push(
        commands.registerCommand(OPEN_PREVIEW_COMMAND, open),
        { dispose: () => panel?.dispose() },
        window.onDidChangeActiveTextEditor((editor) => {
            // Ignores undefined (e.g. webview focused) and non-TRLC editors.
            if (panel && isTrlcEditor(editor)) {
                follow(editor);
            }
        }),
    );

    return {
        refresh: () => void controller.refresh(),
        isPreviewing: (uri) => panel !== undefined && controller.uri === uri,
    };
}
