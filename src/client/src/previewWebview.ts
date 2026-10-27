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

import { HostToWebview, PreviewModel, PreviewNode, WebviewToHost } from "./previewProtocol";
import { escapeHtml, renderPreview } from "./previewRender";

declare function acquireVsCodeApi(): { postMessage(msg: WebviewToHost): void };

const vscode = acquireVsCodeApi();
const root = document.getElementById("root") as HTMLElement;

let lastNodes: PreviewNode[] | null = null;
let currentModel: PreviewModel | null = null;
let pending: HostToWebview | null = null;

function post(msg: WebviewToHost): void {
    vscode.postMessage(msg);
}

function render(msg: HostToWebview): void {
    if (msg.type === "error") {
        root.innerHTML = `<div class="banner">${escapeHtml(msg.message)}</div>`;
        return;
    }
    currentModel = msg.model;
    root.innerHTML = renderPreview(msg.model, lastNodes);
    if (msg.model.nodes !== null) {
        lastNodes = msg.model.nodes;
    }
}

function inputHasFocus(): boolean {
    const a = document.activeElement;
    return a !== null && root.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName);
}

window.addEventListener("message", (event: MessageEvent<HostToWebview>) => {
    if (inputHasFocus()) {
        pending = event.data;
        return;
    }
    pending = null;
    render(event.data);
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
        if (el.value === "") {
            return; // unset selection is not an edit
        }
        post({ type: "setField", record, field, value: el.value });
        return;
    }
    if (el.value === el.defaultValue) {
        return; // unchanged from what was rendered
    }
    post({ type: "setField", record, field, value: el.value });
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
    // Defer so document.activeElement reflects the newly focused element.
    setTimeout(() => {
        if (pending !== null && !inputHasFocus()) {
            const msg = pending;
            pending = null;
            render(msg);
        }
    }, 0);
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
            }
        } else if (button.classList.contains("cancel") && currentModel) {
            render({ type: "model", model: currentModel });
        }
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
