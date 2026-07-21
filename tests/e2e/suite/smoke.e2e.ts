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
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

/**
 * Golden-path smoke suite for the packaged extension inside a real VSCode
 * host. Mirrors two scenarios already covered at the LSP protocol boundary
 * by tests/integration/ (see tests/e2e/e2e_smoke.json), plus a basic
 * activation/command-registration check. It intentionally does not attempt
 * parity with the full integration matrix - see docs/DEVELOPER_GUIDE.md.
 */

interface SmokeScenarios {
    completionDotTrigger: {
        typesFile: string;
        sampleFile: string;
        position: { line: number; character: number };
        triggerCharacter: string;
        expectedLabels: string[];
    };
    diagnosticsOnInvalidFile: {
        typesFile: string;
        invalidFile: string;
        expectAtLeastOneErrorDiagnostic: boolean;
    };
}

function loadScenarios(workspaceFolder: vscode.WorkspaceFolder): SmokeScenarios {
    // This suite runs inside a separate Electron extension-host process
    // launched by @vscode/test-electron - it doesn't share the outer Bazel
    // js_test's cwd/runfiles, so read the JSON (copied alongside the
    // fixtures into the workspace by prepare.ts) via the workspace folder
    // VSCode itself opened, rather than guessing a filesystem path here.
    const jsonPath = path.join(workspaceFolder.uri.fsPath, "e2e_smoke.json");
    return JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
}

async function waitForDiagnostics(
    uri: vscode.Uri,
    timeoutMs = 20000
): Promise<vscode.Diagnostic[]> {
    const existing = vscode.languages.getDiagnostics(uri);
    if (existing.length > 0) {
        return existing;
    }

    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            disposable.dispose();
            reject(new Error(`Timed out waiting for diagnostics on ${uri.toString()}`));
        }, timeoutMs);

        const disposable = vscode.languages.onDidChangeDiagnostics(event => {
            if (event.uris.some(changed => changed.toString() === uri.toString())) {
                const diags = vscode.languages.getDiagnostics(uri);
                if (diags.length > 0) {
                    clearTimeout(timer);
                    disposable.dispose();
                    resolve(diags);
                }
            }
        });
    });
}

suite("TRLC extension e2e smoke", () => {
    const [workspaceFolder] = vscode.workspace.workspaceFolders ?? [];
    let scenarios: SmokeScenarios;

    suiteSetup(async function () {
        this.timeout(60000);
        assert.ok(workspaceFolder, "Expected a workspace folder to be open");
        scenarios = loadScenarios(workspaceFolder);

        // Force + await activation directly, rather than relying on the
        // "onLanguage:TRLC" activation event firing (and completing) as a
        // side effect of a later test opening a .trlc/.rsl file - that's a
        // race, and "extension.parseAll" etc are only registered once
        // activate() (which awaits the LanguageClient's client.start())
        // has actually finished.
        const extension = vscode.extensions.getExtension(
            "bmw-group.trlc-vscode-extension"
        );
        assert.ok(extension, "Expected the TRLC extension to be loaded");
        await extension.activate();
    });

    test("activation registers the extension's commands", async () => {
        const commands = await vscode.commands.getCommands(true);
        for (const expected of [
            "extension.parseAll",
            "extension.resetState",
        ]) {
            assert.ok(
                commands.includes(expected),
                `Expected command "${expected}" to be registered - activation likely failed`
            );
        }
    });

    test("dot-completion on an enum type returns its literals", async () => {
        const scenario = scenarios.completionDotTrigger;
        const typesUri = vscode.Uri.joinPath(workspaceFolder.uri, scenario.typesFile);
        const sampleUri = vscode.Uri.joinPath(workspaceFolder.uri, scenario.sampleFile);

        await vscode.workspace.openTextDocument(typesUri);
        await vscode.workspace.openTextDocument(sampleUri);
        await waitForDiagnostics(sampleUri);

        const position = new vscode.Position(
            scenario.position.line,
            scenario.position.character
        );
        const list = await vscode.commands.executeCommand<vscode.CompletionList>(
            "vscode.executeCompletionItemProvider",
            sampleUri,
            position,
            scenario.triggerCharacter
        );

        const labels = (list?.items ?? []).map(item =>
            typeof item.label === "string" ? item.label : item.label.label
        );
        for (const expected of scenario.expectedLabels) {
            assert.ok(
                labels.includes(expected),
                `Expected completion label "${expected}", got: ${JSON.stringify(labels)}`
            );
        }
    });

    test("an invalid file produces an error diagnostic", async () => {
        const scenario = scenarios.diagnosticsOnInvalidFile;
        const typesUri = vscode.Uri.joinPath(workspaceFolder.uri, scenario.typesFile);
        const invalidUri = vscode.Uri.joinPath(workspaceFolder.uri, scenario.invalidFile);

        await vscode.workspace.openTextDocument(typesUri);
        await vscode.workspace.openTextDocument(invalidUri);
        const diagnostics = await waitForDiagnostics(invalidUri);

        assert.ok(diagnostics.length >= 1, "Expected at least one diagnostic");
        assert.ok(
            diagnostics.some(d => d.severity === vscode.DiagnosticSeverity.Error),
            `Expected an error-severity diagnostic, got: ${JSON.stringify(
                diagnostics.map(d => d.severity)
            )}`
        );
    });
});
