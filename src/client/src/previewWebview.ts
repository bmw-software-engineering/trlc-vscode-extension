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
 * Browser entry of the TRLC preview webview. Renders models posted by the
 * extension host and turns user interaction into WebviewToHost messages.
 * Must not import `vscode` or node modules.
 */

import { HostToWebview, PreviewModel, WebviewToHost } from "./previewProtocol";
import { focusLeavesPreview, selectCommitValue, shouldRenderNow, textCommitValue } from "./previewFocus";
import { escapeHtml, LastNodes, renderErrorBanner, renderNoDocument, renderPreview } from "./previewRender";

declare function acquireVsCodeApi(): { postMessage(msg: WebviewToHost): void };

const vscode = acquireVsCodeApi();
const root = document.getElementById("root") as HTMLElement;

let lastNodes: LastNodes | null = null;
let currentModel: PreviewModel | null = null;
let pending: HostToWebview | null = null;

function post(msg: WebviewToHost): void {
    vscode.postMessage(msg);
}

function render(msg: HostToWebview): void {
    if (msg.type === "error") {
        // Keep the tree: replace only a previous error banner.
        root.querySelector(".error-banner")?.remove();
        root.insertAdjacentHTML("afterbegin", renderErrorBanner(msg.message));
        return;
    }
    if (msg.type === "noDocument") {
        currentModel = null;
        lastNodes = null;
        root.innerHTML = renderNoDocument();
        return;
    }
    currentModel = msg.model;
    root.innerHTML = renderPreview(msg.model, lastNodes);
    if (msg.model.nodes !== null) {
        lastNodes = { uri: msg.model.uri, nodes: msg.model.nodes };
    }
}

function inputHasFocus(): boolean {
    const a = document.activeElement;
    return a !== null && root.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName);
}

function addFormOpen(): boolean {
    return root.querySelector(".add-form") !== null;
}

/** Renders the pending model if that cannot swallow user input. */
function flushPending(): void {
    if (pending !== null && shouldRenderNow({ inputFocused: inputHasFocus(), addFormOpen: addFormOpen() })) {
        const msg = pending;
        pending = null;
        render(msg);
    }
}

window.addEventListener("message", (event: MessageEvent<HostToWebview>) => {
    pending = event.data;
    flushPending();
});

type Control = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

function recordOf(el: Element): string | null {
    const rec = el.closest<HTMLElement>(".record");
    return rec ? (rec.dataset.record ?? null) : null;
}

function commit(el: Control): void {
    const record = recordOf(el);
    const field = el.dataset.field;
    if (record === null || field === undefined) {
        return;
    }
    if (el instanceof HTMLSelectElement) {
        const rendered = Array.from(el.options).find((o) => o.defaultSelected)?.value ?? "";
        const value = selectCommitValue(el.value, rendered);
        if (value !== undefined) {
            post({ type: "setField", record, field, value });
        }
        return;
    }
    if (el.value === el.defaultValue) {
        return; // unchanged from what was rendered
    }
    const value = textCommitValue(el.value, el.dataset.kind ?? "", el.dataset.optional === "true");
    post({ type: "setField", record, field, value });
    // Avoid re-posting the same edit on a following blur.
    el.defaultValue = el.value;
}

root.addEventListener("change", (e) => {
    const t = e.target;
    if ((t instanceof HTMLInputElement || t instanceof HTMLSelectElement) && t.dataset.field !== undefined) {
        commit(t);
    }
});

root.addEventListener("focusout", (e) => {
    const t = e.target;
    if (t instanceof HTMLTextAreaElement && t.dataset.field !== undefined) {
        commit(t);
    }
    // Focus moving within the preview (e.g. onto a button being clicked) must
    // not re-render, or the click would be lost.
    const next = e.relatedTarget;
    if (!focusLeavesPreview(next instanceof Node && root.contains(next))) {
        return;
    }
    // Defer so document.activeElement reflects the newly focused element.
    setTimeout(flushPending, 0);
});

root.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t instanceof HTMLTextAreaElement && t.dataset.field !== undefined && e.key === "Enter" && e.ctrlKey) {
        e.preventDefault();
        commit(t);
    } else if (t instanceof HTMLElement && t.classList.contains("record-name") && e.key === "Enter") {
        post({ type: "reveal", line: Number(t.dataset.line) });
    } else if (t instanceof HTMLElement && t.classList.contains("new-name") && e.key === "Enter") {
        t.closest<HTMLElement>(".add-form")?.querySelector<HTMLButtonElement>("button.create")?.click();
    }
});

function showAddForm(btn: HTMLButtonElement): void {
    const types = currentModel?.recordTypes ?? [];
    const form = document.createElement("div");
    form.className = "add-form";
    form.dataset.sectionId = btn.dataset.sectionId ?? "";
    const options = types.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join("");
    form.innerHTML =
        `<select class="new-type">${options}</select> ` +
        `<input type="text" class="new-name" placeholder="Name"> ` +
        `<button class="create">Create</button> <button class="cancel">Cancel</button>`;
    btn.replaceWith(form);
    form.querySelector<HTMLInputElement>("input.new-name")?.focus();
}

root.addEventListener("click", (e) => {
    const target = e.target;
    if (!(target instanceof Element)) {
        return;
    }
    const button = target.closest("button");
    if (button instanceof HTMLButtonElement) {
        if (button.disabled) {
            return;
        }
        if (button.classList.contains("remove")) {
            const record = recordOf(button);
            const field = button
                .closest<HTMLElement>(".field")
                ?.querySelector<HTMLElement>("[data-field]")?.dataset.field;
            if (record !== null && field !== undefined) {
                post({ type: "setField", record, field, value: null });
            }
        } else if (button.classList.contains("add-record")) {
            showAddForm(button);
        } else if (button.classList.contains("create")) {
            const form = button.closest<HTMLElement>(".add-form");
            const type = form?.querySelector<HTMLSelectElement>("select.new-type")?.value ?? "";
            const name = (form?.querySelector<HTMLInputElement>("input.new-name")?.value ?? "").trim();
            if (form && type !== "" && name !== "") {
                const sid = form.dataset.sectionId ?? "";
                post({ type: "addRecord", sectionId: sid === "" ? null : Number(sid), recordType: type, name });
                // Form is done; let a pending model replace it.
                form.className = "add-form-done";
                flushPending();
            }
        } else if (button.classList.contains("cancel")) {
            const next = pending ?? (currentModel ? ({ type: "model", model: currentModel } as HostToWebview) : null);
            pending = null;
            if (next) {
                render(next);
            }
            return;
        }
        flushPending();
        return;
    }
    const link = target.closest<HTMLElement>("a.record-name");
    if (link) {
        post({ type: "reveal", line: Number(link.dataset.line) });
    }
});

// Make record names keyboard reachable.
new MutationObserver(() => {
    root.querySelectorAll("a.record-name:not([tabindex])").forEach((a) => {
        a.setAttribute("tabindex", "0");
        a.setAttribute("role", "link");
    });
}).observe(root, { childList: true, subtree: true });

post({ type: "ready" });
