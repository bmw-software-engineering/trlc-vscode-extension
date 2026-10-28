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

import { canDrop, isNoOpMove } from "./previewDnd";
import { HostToWebview, ItemRef, PreviewField, PreviewModel, PreviewNode, WebviewToHost } from "./previewProtocol";
import { focusLeavesPreview, selectCommitValue, shouldRenderNow, textCommitValue } from "./previewFocus";
import { MarkdownRejection, markdownCommitValue, rejectionApplies } from "./previewMarkdown";
import { mountMarkdownField } from "./previewMarkdownEditor";
import { escapeHtml, LastNodes, renderErrorBanner, renderNoDocument, renderPreview } from "./previewRender";

declare function acquireVsCodeApi(): { postMessage(msg: WebviewToHost): void };

const vscode = acquireVsCodeApi();
const root = document.getElementById("root") as HTMLElement;

let lastNodes: LastNodes | null = null;
let currentModel: PreviewModel | null = null;
let pending: HostToWebview | null = null;
/** TOC collapsed state; survives re-renders. */
let tocCollapsed = false;
/** Item being dragged; non-null from dragstart to dragend. */
let dragItem: ItemRef | null = null;
/** Mouse is down on a drag handle: hold renders so the handle is not detached before dragstart. */
let pressing = false;
/** Mounted Markdown editors of the current DOM; destroyed before it is replaced. */
const mdMounts = new Map<HTMLElement, { destroy(): void }>();
/** A Markdown edit the server refused: its text stays in the editor until committed again. */
let rejection: (MarkdownRejection & { value: string; message: string }) | null = null;

/** Nodes of the tree on screen (the fallback tree when the model has none). */
function activeNodes(): PreviewNode[] {
    return currentModel?.nodes ?? lastNodes?.nodes ?? [];
}

function applyToc(toc: Element | null): void {
    toc?.classList.toggle("collapsed", tocCollapsed);
    const toggle = toc?.querySelector(".toc-toggle");
    if (toggle) {
        toggle.textContent = tocCollapsed ? "\u25b8" : "\u25be";
    }
}

/** Ends an inline form: drops focus out of it, removes it, lets a pending model render. */
function finishForm(form: HTMLElement): void {
    const a = document.activeElement;
    if (a instanceof HTMLElement && form.contains(a)) {
        a.blur();
    }
    form.remove();
    flushPending();
}

function formOpen(mode: string, sectionId: string): boolean {
    return Array.from(root.querySelectorAll<HTMLElement>(".add-form")).some(
        (f) => f.dataset.mode === mode && f.dataset.sectionId === sectionId
    );
}

function post(msg: WebviewToHost): void {
    vscode.postMessage(msg);
}

function destroyMarkdownFields(): void {
    mdMounts.forEach((m) => m.destroy());
    mdMounts.clear();
}

function findField(nodes: PreviewNode[], record: string, field: string): PreviewField | null {
    for (const n of nodes) {
        if (n.kind === "record" && n.name === record) {
            return n.fields.find((f) => f.name === field) ?? null;
        }
        const found = n.kind === "section" ? findField(n.children, record, field) : null;
        if (found) {
            return found;
        }
    }
    return null;
}

function commitMarkdown(el: HTMLElement, md: string): void {
    const record = recordOf(el);
    const field = el.dataset.field;
    if (record === null || field === undefined) {
        return;
    }
    if (rejection !== null && rejection.record === record && rejection.field === field) {
        rejection = null; // committed again: the server decides anew
    }
    const value = markdownCommitValue(md, el.dataset.value ?? "", el.dataset.optional === "true");
    if (value !== undefined) {
        post({ type: "setField", record, field, value });
    }
}

/** (Re-)mounts the editor of one `.md-field`, showing a refused edit instead of the server's value. */
function mountMarkdown(el: HTMLElement): void {
    mdMounts.get(el)?.destroy();
    const record = recordOf(el) ?? "";
    const field = el.dataset.field ?? "";
    const serverValue = el.dataset.value ?? "";
    let initial: string | undefined;
    let note: string | undefined;
    if (rejection !== null && currentModel !== null) {
        if (rejectionApplies(rejection, currentModel.uri, record, field, serverValue)) {
            initial = rejection.value;
            note = rejection.message;
        } else if (rejection.record === record && rejection.field === field) {
            rejection = null; // the document moved on
        }
    }
    mdMounts.set(
        el,
        mountMarkdownField(el, {
            editable: !el.hasAttribute("disabled"),
            onCommit: (md) => commitMarkdown(el, md),
            initial,
            note,
        })
    );
}

function mountMarkdownFields(): void {
    root.querySelectorAll<HTMLElement>(".md-field").forEach(mountMarkdown);
}

/** Keeps a refused Markdown edit on screen (Ruling A); other fields ignore it. */
function handleEditRejected(msg: Extract<HostToWebview, { type: "editRejected" }>): void {
    const f = currentModel ? findField(activeNodes(), msg.record, msg.field) : null;
    if (currentModel === null || f === null || !f.markdown) {
        return;
    }
    rejection = {
        uri: currentModel.uri,
        record: msg.record,
        field: msg.field,
        serverValue: f.value ?? "",
        value: msg.value ?? "",
        message: msg.message,
    };
    const el = Array.from(mdMounts.keys()).find(
        (e) => e.isConnected && recordOf(e) === msg.record && e.dataset.field === msg.field
    );
    if (el && !el.classList.contains("editing")) {
        mountMarkdown(el);
    }
}

function render(msg: HostToWebview): void {
    if (msg.type === "error") {
        // Keep the tree: replace only a previous error banner.
        root.querySelector(".error-banner")?.remove();
        root.insertAdjacentHTML("afterbegin", renderErrorBanner(msg.message));
        return;
    }
    if (msg.type === "editRejected") {
        handleEditRejected(msg);
        return;
    }
    destroyMarkdownFields();
    if (msg.type === "noDocument") {
        currentModel = null;
        lastNodes = null;
        rejection = null;
        root.innerHTML = renderNoDocument();
        return;
    }
    if (rejection !== null && rejection.uri !== msg.model.uri) {
        rejection = null;
    }
    currentModel = msg.model;
    root.innerHTML = renderPreview(msg.model, lastNodes);
    if (msg.model.nodes !== null) {
        lastNodes = { uri: msg.model.uri, nodes: msg.model.nodes };
    }
    applyToc(root.querySelector(".toc"));
    mountMarkdownFields();
}

function inputHasFocus(): boolean {
    const a = document.activeElement;
    if (a === null || !root.contains(a)) {
        return false;
    }
    // A Markdown editor being edited (its contenteditable, toolbar or link input).
    return /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.closest(".md-field.editing") !== null;
}

function addFormOpen(): boolean {
    return root.querySelector(".add-form") !== null;
}

/** Renders the pending model if that cannot swallow user input. */
function flushPending(): void {
    if (pending !== null && shouldRenderNow({ inputFocused: inputHasFocus(), addFormOpen: addFormOpen(),
            dragging: dragItem !== null,
            pressing,
        })) {
        const msg = pending;
        pending = null;
        render(msg);
    }
}

window.addEventListener("message", (event: MessageEvent<HostToWebview>) => {
    if (event.data.type === "editRejected") {
        // Not a model: must not replace one waiting in `pending`.
        handleEditRejected(event.data);
        return;
    }
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
    } else if (t instanceof HTMLElement && t.classList.contains("toc-link") && e.key === "Enter") {
        t.click();
    } else if (t instanceof HTMLElement && t.classList.contains("section-title")) {
        const form = t.closest<HTMLElement>(".add-form");
        if (e.key === "Enter") {
            form?.querySelector<HTMLButtonElement>("button.section-ok")?.click();
        } else if (e.key === "Escape") {
            form?.querySelector<HTMLButtonElement>("button.cancel")?.click();
        }
    }
});

function findSectionTitle(nodes: PreviewNode[], id: number): string {
    for (const n of nodes) {
        if (n.kind === "section") {
            if (n.sectionId === id) {
                return n.title;
            }
            const found = findSectionTitle(n.children, id);
            if (found !== "") {
                return found;
            }
        }
    }
    return "";
}

/**
 * Inline title form for "+ Section" (insert) and the pencil (rename). Uses the
 * add-form class so it blocks re-rendering while open, like the add-record form.
 */
function showSectionForm(btn: HTMLButtonElement, mode: "insert" | "rename", sectionId: string, title: string): void {
    if (formOpen(mode, sectionId)) {
        return;
    }
    const form = document.createElement("div");
    form.className = "add-form";
    form.dataset.mode = mode;
    form.dataset.sectionId = sectionId;
    form.innerHTML =
        `<input type="text" class="section-title" placeholder="Section title" value="${escapeHtml(title)}"> ` +
        `<button class="section-ok">${mode === "insert" ? "Create" : "Rename"}</button> ` +
        `<button class="cancel">Cancel</button>`;
    if (mode === "insert") {
        btn.replaceWith(form);
    } else {
        (btn.closest("h1,h2,h3,h4,h5,h6") ?? btn).insertAdjacentElement("afterend", form);
    }
    const input = form.querySelector<HTMLInputElement>("input.section-title");
    input?.focus();
    input?.select();
}

function submitSectionForm(button: HTMLButtonElement): void {
    const form = button.closest<HTMLElement>(".add-form");
    const title = (form?.querySelector<HTMLInputElement>("input.section-title")?.value ?? "").trim();
    if (!form || title === "") {
        return;
    }
    const sid = form.dataset.sectionId ?? "";
    if (form.dataset.mode === "rename") {
        post({ type: "renameSection", sectionId: Number(sid), title });
    } else {
        post({ type: "insertSection", sectionId: sid === "" ? null : Number(sid), title });
    }
    finishForm(form);
}

function showAddForm(btn: HTMLButtonElement): void {
    if (formOpen("add-record", btn.dataset.sectionId ?? "")) {
        return;
    }
    const types = currentModel?.recordTypes ?? [];
    const form = document.createElement("div");
    form.className = "add-form";
    form.dataset.mode = "add-record";
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
                finishForm(form);
            }
        } else if (button.classList.contains("add-section")) {
            showSectionForm(button, "insert", button.dataset.sectionId ?? "", "");
        } else if (button.classList.contains("rename-section")) {
            const sid = button.dataset.sectionId ?? "";
            showSectionForm(button, "rename", sid, findSectionTitle(activeNodes(), Number(sid)));
        } else if (button.classList.contains("dissolve-section")) {
            post({ type: "dissolveSection", sectionId: Number(button.dataset.sectionId) });
        } else if (button.classList.contains("section-ok")) {
            submitSectionForm(button);
        } else if (button.classList.contains("toc-toggle")) {
            tocCollapsed = !tocCollapsed;
            applyToc(button.closest(".toc"));
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
    const tocLink = target.closest<HTMLElement>("a.toc-link");
    if (tocLink) {
        document.getElementById(`section-${tocLink.dataset.sectionId}`)?.scrollIntoView({ block: "start" });
        return;
    }
    const link = target.closest<HTMLElement>("a.record-name");
    if (link) {
        post({ type: "reveal", line: Number(link.dataset.line) });
    }
});

// ---- Drag & drop -------------------------------------------------------
// Order: dragstart (handle) -> dragover/dragleave (zones) -> drop -> dragend.
// `dragItem` blocks rendering from dragstart until dragend (shouldRenderNow),
// so a model arriving mid-drag is held in `pending` and flushed on dragend.

const DRAG_MIME = "application/x-trlc-item";

function clearActiveZones(): void {
    root.querySelectorAll(".drop-zone.active").forEach((z) => z.classList.remove("active"));
}

function itemFromHandle(h: HTMLElement): ItemRef | null {
    const id = h.dataset.dragId ?? "";
    if (h.dataset.dragKind === "record") {
        return { kind: "record", name: id };
    }
    return h.dataset.dragKind === "section" ? { kind: "section", sectionId: Number(id) } : null;
}

/** The zone, if the dragged item may legally be dropped there. */
function acceptingZone(target: EventTarget | null): HTMLElement | null {
    if (dragItem === null || !(target instanceof Element)) {
        return null;
    }
    const zone = target.closest<HTMLElement>(".drop-zone");
    if (!zone || !currentModel?.editable) {
        return null;
    }
    if (dragItem.kind === "section" && zone.closest(`.section-block[data-section-id="${dragItem.sectionId}"]`)) {
        return null; // inside the subtree of the dragged section itself
    }
    const nodes = activeNodes();
    const sid = zone.dataset.targetSectionId ?? "";
    return canDrop(nodes, dragItem, sid === "" ? null : Number(sid)) ? zone : null;
}

root.addEventListener("dragstart", (e) => {
    const t = e.target;
    const handle = t instanceof Element ? t.closest<HTMLElement>(".drag-handle") : null;
    const item = handle ? itemFromHandle(handle) : null;
    if (item === null || !e.dataTransfer) {
        return;
    }
    dragItem = item;
    root.classList.add("dragging");
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify(item));
    e.dataTransfer.effectAllowed = "move";
});

root.addEventListener("dragover", (e) => {
    const zone = acceptingZone(e.target);
    clearActiveZones();
    if (zone) {
        e.preventDefault(); // marks the zone as a valid drop target
        zone.classList.add("active");
    }
});

root.addEventListener("dragleave", (e) => {
    if (e.target instanceof Element) {
        e.target.closest(".drop-zone")?.classList.remove("active");
    }
});

root.addEventListener("drop", (e) => {
    const zone = acceptingZone(e.target);
    clearActiveZones();
    if (!zone || dragItem === null) {
        return;
    }
    e.preventDefault();
    const sid = zone.dataset.targetSectionId ?? "";
    const target = sid === "" ? null : Number(sid);
    const beforeId = zone.dataset.beforeId ?? "";
    let before: ItemRef | null = null;
    if (zone.dataset.beforeKind === "record") {
        before = { kind: "record", name: beforeId };
    } else if (zone.dataset.beforeKind === "section") {
        before = { kind: "section", sectionId: Number(beforeId) };
    }
    if (!isNoOpMove(activeNodes(), dragItem, target, before)) {
        post({ type: "move", item: dragItem, targetSectionId: target, before });
    }
});

// A press on a handle must not be interrupted by a re-render (the blur of a focused
// input would otherwise flush a pending model and detach the handle before dragstart).
root.addEventListener("mousedown", (e) => {
    if (e.target instanceof Element && e.target.closest(".drag-handle")) {
        pressing = true;
    }
});

document.addEventListener("mouseup", () => {
    if (pressing) {
        pressing = false;
        if (dragItem === null) {
            flushPending();
        }
    }
});

// Fires on the source after drop, on Escape and on a drop outside any zone.
root.addEventListener("dragend", () => {
    dragItem = null;
    pressing = false;
    root.classList.remove("dragging");
    clearActiveZones();
    flushPending();
});

// Make record names keyboard reachable.
new MutationObserver(() => {
    root.querySelectorAll("a.record-name:not([tabindex]), a.toc-link:not([tabindex])").forEach((a) => {
        a.setAttribute("tabindex", "0");
        a.setAttribute("role", "link");
    });
}).observe(root, { childList: true, subtree: true });

post({ type: "ready" });
