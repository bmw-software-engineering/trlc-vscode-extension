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

/**
 * Pure HTML rendering of the TRLC preview model. No `vscode` import so it
 * can be unit-tested with plain mocha; the webview script relies on the
 * data-* attributes and class names emitted here.
 */

import { PreviewField, PreviewModel, PreviewNode } from "./previewProtocol";

export function escapeHtml(s: string): string {
    return s
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function option(value: string, current: string | null): string {
    const sel = value === current ? " selected" : "";
    const v = escapeHtml(value);
    return `<option value="${v}"${sel}>${v}</option>`;
}

function renderControl(f: PreviewField, locked: boolean): string {
    const attrs =
        `data-field="${escapeHtml(f.name)}" data-kind="${escapeHtml(f.kind)}"` +
        `${f.optional ? ` data-optional="true"` : ""}${locked ? " disabled" : ""}`;
    const value = f.value ?? "";
    if (f.kind === "string") {
        return `<textarea ${attrs} rows="2">${escapeHtml(value)}</textarea>`;
    }
    if (f.kind === "boolean" || f.kind === "enum") {
        const values = f.kind === "boolean" ? ["true", "false"] : (f.enumValues ?? []);
        // An empty option makes "unset" representable (and is shown for
        // required-but-missing values so nothing is committed implicitly).
        const blank =
            f.value === null
                ? '<option value="" selected></option>'
                : f.optional
                  ? '<option value=""></option>'
                  : "";
        return `<select ${attrs}>${blank}${values.map((v) => option(v, f.value)).join("")}</select>`;
    }
    return `<input type="text" ${attrs} value="${escapeHtml(value)}">`;
}

function renderField(f: PreviewField, modelLocked: boolean): string {
    const locked = modelLocked || !f.editable;
    const remove =
        f.optional && f.value !== null && !locked
            ? '<button class="remove" title="Remove value">&times;</button>'
            : "";
    return (
        `<div class="field"><label>${escapeHtml(f.name)}</label>` + `${renderControl(f, locked)}${remove}</div>`
    );
}

function addRecordButton(sectionId: number | null, locked: boolean): string {
    const id = sectionId === null ? "" : String(sectionId);
    return `<button class="add-record" data-section-id="${id}"${locked ? " disabled" : ""}>+ Add record</button>`;
}

function renderNode(node: PreviewNode, locked: boolean): string {
    if (node.kind === "section") {
        const level = Math.min(Math.max(node.depth, 1), 6);
        return (
            `<div class="section-block">` +
            `<h${level} class="section"><span class="hashes">${"#".repeat(node.depth)}</span> ` +
            `${escapeHtml(node.title)}</h${level}>` +
            node.children.map((c) => renderNode(c, locked)).join("") +
            // A section still open at EOF has no place to insert into.
            (node.closed ? addRecordButton(node.sectionId, locked) : "") +
            `</div>`
        );
    }
    return (
        `<div class="record" data-record="${escapeHtml(node.name)}">` +
        `<div class="record-head"><a class="record-name" data-line="${node.line}">${escapeHtml(node.name)}</a>` +
        ` <span class="record-type">${escapeHtml(node.type)}</span></div>` +
        node.fields.map((f) => renderField(f, locked)).join("") +
        `</div>`
    );
}

/** The nodes of the last model that had some, and the document they belong to. */
export interface LastNodes {
    uri: string;
    nodes: PreviewNode[];
}

/** Body HTML for the model; falls back to `last` (same document only) when it has no nodes. */
export function renderPreview(model: PreviewModel, last: LastNodes | null): string {
    const locked = !model.editable;
    const fallback = last !== null && last.uri === model.uri ? last.nodes : null;
    const nodes = model.nodes ?? fallback ?? [];
    const banner = locked ? `<div class="banner">${escapeHtml(model.reason ?? "")}</div>` : "";
    return banner + nodes.map((n) => renderNode(n, locked)).join("") + addRecordButton(null, locked);
}

/** Banner for a request error; shown above the current tree. */
export function renderErrorBanner(message: string): string {
    return `<div class="banner error-banner">${escapeHtml(message)}</div>`;
}

export const NO_DOCUMENT_MESSAGE = "Open a .trlc file to preview it.";

/** Body HTML when no .trlc document is being previewed. */
export function renderNoDocument(): string {
    return `<div class="placeholder">${escapeHtml(NO_DOCUMENT_MESSAGE)}</div>`;
}

const STYLE = `
body { color: var(--vscode-editor-foreground); background: var(--vscode-editor-background);
       font-family: var(--vscode-font-family); padding: 8px 16px; }
.banner { background: var(--vscode-inputValidation-warningBackground);
          border: 1px solid var(--vscode-inputValidation-warningBorder); padding: 6px 10px; margin-bottom: 12px; }
h1.section, h2.section, h3.section, h4.section, h5.section, h6.section { margin: 16px 0 8px; }
.hashes { color: var(--vscode-descriptionForeground); }
.record { border: 1px solid var(--vscode-panel-border); border-radius: 4px; padding: 8px; margin: 8px 0; }
.record-name { font-weight: bold; cursor: pointer; color: var(--vscode-textLink-foreground); }
.record-type { color: var(--vscode-descriptionForeground); }
.field { display: flex; gap: 8px; align-items: flex-start; margin: 4px 0; }
.field label { flex: 0 0 140px; padding-top: 4px; }
.field input, .field select, .field textarea { flex: 1; color: var(--vscode-input-foreground);
    background: var(--vscode-input-background); border: 1px solid var(--vscode-input-border, transparent);
    font-family: var(--vscode-editor-font-family); padding: 3px 6px; }
button { color: var(--vscode-button-foreground); background: var(--vscode-button-background);
         border: none; padding: 3px 10px; cursor: pointer; }
button:disabled { opacity: 0.5; cursor: default; }
button.remove { background: transparent; color: var(--vscode-foreground); }
.error-banner { background: var(--vscode-inputValidation-errorBackground);
                border-color: var(--vscode-inputValidation-errorBorder); }
.placeholder { color: var(--vscode-descriptionForeground); }
`;

export function previewShellHtml(nonce: string, cspSource: string, scriptUri: string): string {
    const csp = `default-src 'none'; style-src ${cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}'`;
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>${STYLE}</style>
</head>
<body>
<div id="root"></div>
<script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
}
