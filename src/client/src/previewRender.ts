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

import { PreviewField, PreviewModel, PreviewNode, PreviewSectionNode } from "./previewProtocol";

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
    if (f.kind === "string" && f.markdown) {
        return `<div class="md-field" ${attrs} data-markdown="1" data-value="${escapeHtml(value)}"></div>`;
    }
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
    const required = f.optional ? "" : ' <span class="required">*</span>';
    const remove =
        f.optional && f.value !== null && !locked
            ? '<button class="remove" title="Remove value">&times;</button>'
            : "";
    return (
        `<div class="field"><label>${escapeHtml(f.name)}${required}</label>` +
        `${renderControl(f, locked)}${remove}</div>`
    );
}

function idAttr(sectionId: number | null): string {
    return sectionId === null ? "" : String(sectionId);
}

function addRecordButton(sectionId: number | null, locked: boolean): string {
    return `<button class="add-record" data-section-id="${idAttr(sectionId)}"${locked ? " disabled" : ""}>+ Add record</button>`;
}

function addSectionButton(sectionId: number | null, locked: boolean): string {
    return `<button class="add-section" data-section-id="${idAttr(sectionId)}"${locked ? " disabled" : ""}>+ Section</button>`;
}

function dragHandle(kind: "record" | "section", id: string, locked: boolean): string {
    const draggable = locked ? "" : ' draggable="true"';
    return `<span class="drag-handle"${draggable} data-drag-kind="${kind}" data-drag-id="${escapeHtml(id)}">\u283f</span>`;
}

/** Drop target before `before` (null: at the end) inside section `target` (null: file level). */
function dropZone(target: number | null, before: PreviewNode | null): string {
    let kind = "";
    let id = "";
    if (before !== null) {
        kind = before.kind;
        id = before.kind === "section" ? String(before.sectionId) : before.name;
    }
    return (
        `<div class="drop-zone" data-target-section-id="${idAttr(target)}" ` +
        `data-before-kind="${kind}" data-before-id="${escapeHtml(id)}"></div>`
    );
}

/** Children with a drop zone before each one. */
function renderChildren(nodes: PreviewNode[], target: number | null, locked: boolean): string {
    return nodes.map((n) => dropZone(target, n) + renderNode(n, locked)).join("");
}

function renderToc(nodes: PreviewNode[]): string {
    const items = nodes
        .filter((n): n is PreviewSectionNode => n.kind === "section")
        .map(
            (s) =>
                `<li><a class="toc-link" data-section-id="${s.sectionId}">${escapeHtml(s.title)}</a>` +
                `${renderToc(s.children)}</li>`
        );
    return items.length === 0 ? "" : `<ul>${items.join("")}</ul>`;
}

function renderMissing(fields: PreviewField[]): string {
    return fields
        .filter((f) => f.value === null && !f.optional)
        .map((f) => `<div class="missing">Required field "${escapeHtml(f.name)}" is missing</div>`)
        .join("");
}

function renderNode(node: PreviewNode, locked: boolean): string {
    const dis = locked ? " disabled" : "";
    if (node.kind === "section") {
        const level = Math.min(Math.max(node.depth, 1), 6);
        const sid = node.sectionId;
        return (
            `<div class="section-block" id="section-${sid}" data-section-id="${sid}">` +
            `<h${level} class="section"><span class="hashes">${"#".repeat(node.depth)}</span> ` +
            `${escapeHtml(node.title)}` +
            ` ${dragHandle("section", String(sid), locked)}` +
            `<button class="rename-section" data-section-id="${sid}"${dis}>\u270e</button>` +
            `<button class="dissolve-section" data-section-id="${sid}" title="Dissolve section"${dis}>\ud83d\uddd1</button>` +
            `</h${level}>` +
            `<div class="section-body">` +
            renderChildren(node.children, sid, locked) +
            // A section still open at EOF has no place to insert into.
            (node.closed
                ? dropZone(sid, null) + addRecordButton(sid, locked) + addSectionButton(sid, locked)
                : "") +
            `</div></div>`
        );
    }
    return (
        `<div class="record" data-record="${escapeHtml(node.name)}">` +
        `<div class="record-head">${dragHandle("record", node.name, locked)} ` +
        `<a class="record-name" data-line="${node.line}">${escapeHtml(node.name)}</a>` +
        ` <span class="record-type">${escapeHtml(node.type)}</span></div>` +
        renderMissing(node.fields) +
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
    const toc = renderToc(nodes);
    return (
        (toc === "" ? "" : `<nav class="toc"><button class="toc-toggle">\u25be</button>${toc}</nav>`) +
        banner +
        renderChildren(nodes, null, locked) +
        dropZone(null, null) +
        addRecordButton(null, locked) +
        addSectionButton(null, locked)
    );
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
.toc { position: sticky; top: 0; z-index: 1; background: var(--vscode-editor-background);
       padding: 4px 0; margin-bottom: 12px; border-bottom: 1px solid var(--vscode-panel-border); }
.toc ul { margin: 2px 0; padding-left: 16px; list-style: none; }
.toc.collapsed > ul { display: none; }
.toc-link { cursor: pointer; color: var(--vscode-textLink-foreground); }
.toc-toggle { background: transparent; color: var(--vscode-foreground); padding: 0 6px; }
.section-body { margin-left: 12px; padding-left: 12px; border-left: 2px solid var(--vscode-panel-border); }
.drag-handle { cursor: grab; color: var(--vscode-descriptionForeground); margin: 0 4px; user-select: none; }
.drop-zone { height: 6px; }
.dragging .drop-zone { background: var(--vscode-editorWidget-border, var(--vscode-panel-border)); opacity: 0.6; }
.dragging .drop-zone.active { background: var(--vscode-focusBorder); opacity: 1; }
.required { color: var(--vscode-errorForeground); }
.missing { color: var(--vscode-errorForeground); margin: 2px 0; }
.md-field { flex: 1; min-height: 2em; border: 1px solid var(--vscode-input-border, transparent);
            background: var(--vscode-input-background); padding: 3px 6px; }
.md-field.editing { border-color: var(--vscode-focusBorder); }
.md-field:not([disabled]) .ProseMirror { cursor: text; }
.md-field .ProseMirror { outline: none; white-space: pre-wrap; word-wrap: break-word; }
.md-field p { margin: 0.25em 0; }
.md-field a { color: var(--vscode-textLink-foreground); }
.md-field code, .md-field pre { font-family: var(--vscode-editor-font-family);
    background: var(--vscode-textCodeBlock-background); }
.md-field pre { padding: 4px 8px; }
.md-field table { border-collapse: collapse; margin: 4px 0; }
.md-field th, .md-field td { border: 1px solid var(--vscode-panel-border); padding: 2px 6px; }
.md-field .selectedCell { background: var(--vscode-editor-selectionBackground); }
.md-toolbar { display: flex; flex-wrap: wrap; gap: 2px; margin-bottom: 4px; }
.md-toolbar button { padding: 1px 6px; color: var(--vscode-button-secondaryForeground);
    background: var(--vscode-button-secondaryBackground); }
.md-toolbar input.md-link-url { flex: 1; min-width: 12em; }
.md-toolbar[hidden], .md-note[hidden], .md-link-url[hidden] { display: none; }
.md-note { color: var(--vscode-errorForeground); margin-bottom: 4px; }
button.rename-section, button.dissolve-section { background: transparent; color: var(--vscode-foreground); padding: 0 4px; }
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
