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
 * WYSIWYG Markdown field of the preview webview (Milkdown, GFM). Browser
 * only: the bundled editor is never loaded by the mocha tests.
 *
 * A field shows rendered Markdown read-only. Clicking it (or tabbing to it)
 * makes it editable and shows the toolbar; focus leaving the field (the
 * toolbar is part of it) commits the Markdown when the document changed.
 * Raw HTML in the text is shown as text (Milkdown's html node), and links
 * are never followed inside the webview.
 */

import { defaultValueCtx, Editor, editorViewCtx, editorViewOptionsCtx, rootCtx } from "@milkdown/kit/core";
import {
    commonmark,
    createCodeBlockCommand,
    toggleEmphasisCommand,
    toggleInlineCodeCommand,
    toggleStrongCommand,
    turnIntoTextCommand,
    wrapInBulletListCommand,
    wrapInHeadingCommand,
    wrapInOrderedListCommand,
} from "@milkdown/kit/preset/commonmark";
import {
    addColAfterCommand,
    addRowAfterCommand,
    gfm,
    insertTableCommand,
    toggleStrikethroughCommand,
} from "@milkdown/kit/preset/gfm";
import { history } from "@milkdown/kit/plugin/history";
import type { Node as ProseNode } from "@milkdown/kit/prose/model";
import type { Command, EditorState } from "@milkdown/kit/prose/state";
import { deleteColumn, deleteRow, isInTable, selectedRect } from "@milkdown/kit/prose/tables";
import type { EditorView } from "@milkdown/kit/prose/view";
import { callCommand, getMarkdown } from "@milkdown/kit/utils";

export interface MarkdownFieldOptions {
    editable: boolean;
    /** Called with the editor's Markdown when focus leaves a changed field. */
    onCommit(md: string): void;
    /** Text to show instead of the element's data-value (a refused edit). */
    initial?: string;
    /** Shown above the editor until the next commit (why `initial` was refused). */
    note?: string;
}

interface ToolbarAction {
    label: string;
    title: string;
    run(editor: Editor, view: EditorView): void;
}

/** Whether the cursor sits in a heading. */
function inHeading(state: EditorState): boolean {
    return state.selection.$from.parent.type.name === "heading";
}

/** The table header row cannot be deleted (a GFM table needs one). */
const deleteBodyRow: Command = (state, dispatch) =>
    isInTable(state) && selectedRect(state).top > 0 ? deleteRow(state, dispatch) : false;

/**
 * Sets (or with an empty URL removes) the link on the selection; with no
 * selection the URL is inserted as linked text.
 */
function applyLink(view: EditorView, url: string): void {
    const { state } = view;
    const link = state.schema.marks.link;
    if (link === undefined) {
        return;
    }
    const { from, to, empty } = state.selection;
    if (url === "") {
        if (!empty) {
            view.dispatch(state.tr.removeMark(from, to, link));
        }
        return;
    }
    const mark = link.create({ href: url });
    if (empty) {
        view.dispatch(state.tr.insert(from, state.schema.text(url, [mark])));
    } else {
        view.dispatch(state.tr.removeMark(from, to, link).addMark(from, to, mark));
    }
}

const ACTIONS: ToolbarAction[] = [
    { label: "B", title: "Bold", run: (e) => e.action(callCommand(toggleStrongCommand.key)) },
    { label: "I", title: "Italic", run: (e) => e.action(callCommand(toggleEmphasisCommand.key)) },
    { label: "S", title: "Strikethrough", run: (e) => e.action(callCommand(toggleStrikethroughCommand.key)) },
    {
        label: "H",
        title: "Heading (toggle)",
        run: (e, v) =>
            e.action(inHeading(v.state) ? callCommand(turnIntoTextCommand.key) : callCommand(wrapInHeadingCommand.key, 3)),
    },
    { label: "\u2022", title: "Bullet list", run: (e) => e.action(callCommand(wrapInBulletListCommand.key)) },
    { label: "1.", title: "Numbered list", run: (e) => e.action(callCommand(wrapInOrderedListCommand.key)) },
    { label: "`", title: "Inline code", run: (e) => e.action(callCommand(toggleInlineCodeCommand.key)) },
    { label: "{ }", title: "Code block", run: (e) => e.action(callCommand(createCodeBlockCommand.key)) },
    { label: "\u229e", title: "Insert table", run: (e) => e.action(callCommand(insertTableCommand.key, { row: 3, col: 2 })) },
    { label: "+R", title: "Add table row", run: (e) => e.action(callCommand(addRowAfterCommand.key)) },
    { label: "\u2212R", title: "Remove table row", run: (_e, v) => deleteBodyRow(v.state, (tr) => v.dispatch(tr)) },
    { label: "+C", title: "Add table column", run: (e) => e.action(callCommand(addColAfterCommand.key)) },
    { label: "\u2212C", title: "Remove table column", run: (_e, v) => deleteColumn(v.state, (tr) => v.dispatch(tr)) },
];

function makeToolbar(): HTMLElement {
    const bar = document.createElement("div");
    bar.className = "md-toolbar";
    bar.hidden = true;
    for (const [i, a] of ACTIONS.entries()) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "md-action";
        b.tabIndex = -1; // mouse only (ProseMirror shortcuts cover the keyboard); keeps Shift+Tab leaving the field
        b.dataset.action = String(i);
        b.title = a.title;
        b.textContent = a.label;
        bar.appendChild(b);
    }
    const linkBtn = document.createElement("button");
    linkBtn.type = "button";
    linkBtn.className = "md-link";
    linkBtn.tabIndex = -1;
    linkBtn.title = "Link (empty URL removes it)";
    linkBtn.textContent = "Link";
    const url = document.createElement("input");
    url.type = "text";
    url.className = "md-link-url";
    url.placeholder = "https://\u2026 (Enter to apply)";
    url.hidden = true;
    bar.append(linkBtn, url);
    return bar;
}

/**
 * Mounts a Markdown editor into `el` (a `.md-field`). The element's
 * `data-value` is the initial Markdown unless `opts.initial` is given.
 */
export interface MarkdownFieldHandle {
    destroy(): void;
    /** Shows (message) or hides (null) the "Not saved" note in place. */
    setNote(message: string | null): void;
}

export function mountMarkdownField(el: HTMLElement, opts: MarkdownFieldOptions): MarkdownFieldHandle {
    const toolbar = makeToolbar();
    const note = document.createElement("div");
    note.className = "md-note";
    const setNote = (message: string | null): void => {
        note.hidden = message === null;
        note.textContent = message === null ? "" : `Not saved: ${message}`;
    };
    setNote(opts.note ?? null);
    /** Removes every listener added to `el` (it outlives this mount on re-mount). */
    const listeners = new AbortController();
    const signal = listeners.signal;
    const host = document.createElement("div");
    host.className = "md-host";
    el.replaceChildren(note, toolbar, host);
    el.classList.remove("editing");
    el.removeAttribute("tabindex");
    if (opts.editable) {
        el.tabIndex = 0;
    }

    let editing = false;
    let destroyed = false;
    let editor: Editor | null = null;
    /** Document at mount / last commit: leaving without changes commits nothing. */
    let baseline: ProseNode | null = null;

    const view = (): EditorView | null => (editor === null ? null : editor.ctx.get(editorViewCtx));

    function setEditing(on: boolean): void {
        editing = on;
        el.classList.toggle("editing", on);
        // Out of the tab order while editing, so Shift+Tab from the editor
        // leaves the field instead of landing on `el` (which refocuses it).
        el.tabIndex = on ? -1 : 0;
        toolbar.hidden = !on;
        view()?.setProps({ editable: () => editing });
    }

    function activate(): void {
        if (!opts.editable || editing || view() === null) {
            return;
        }
        setEditing(true);
    }

    function leave(): void {
        const v = view();
        if (!editing || v === null || editor === null) {
            return;
        }
        hideLinkInput();
        setEditing(false);
        if (baseline !== null && v.state.doc.eq(baseline)) {
            return;
        }
        baseline = v.state.doc;
        note.hidden = true;
        opts.onCommit(editor.action(getMarkdown()));
    }

    const urlInput = toolbar.querySelector<HTMLInputElement>("input.md-link-url") as HTMLInputElement;

    function hideLinkInput(): void {
        urlInput.hidden = true;
        urlInput.value = "";
    }

    // Activate before ProseMirror's own mousedown so the click places the caret.
    el.addEventListener(
        "mousedown",
        (e) => {
            if (!toolbar.contains(e.target as Node)) {
                activate();
            }
        },
        { capture: true, signal }
    );
    el.addEventListener("focusin", (e) => {
        if (e.target === el && opts.editable) {
            activate();
            view()?.focus();
        }
    }, { signal });
    el.addEventListener("focusout", (e) => {
        const next = e.relatedTarget;
        if (!(next instanceof Node && el.contains(next))) {
            leave();
        }
    }, { signal });
    // Links are never followed, in display and edit mode: VS Code's webview
    // host opens a link on any click that bubbles to the window (it ignores
    // defaultPrevented), so the click must stop here. Bubble phase, so
    // ProseMirror's own click handling on the editor still runs.
    el.addEventListener(
        "click",
        (e) => {
            if (e.target instanceof Element && e.target.closest("a")) {
                e.preventDefault();
                e.stopPropagation();
            }
        },
        { signal }
    );

    // Toolbar buttons keep the focus in the editor.
    toolbar.addEventListener("mousedown", (e) => {
        if (e.target instanceof Element && e.target.closest("button")) {
            e.preventDefault();
        }
    });
    toolbar.addEventListener("click", (e) => {
        e.stopPropagation();
        const v = view();
        const b = e.target instanceof Element ? e.target.closest<HTMLButtonElement>("button") : null;
        if (b === null || v === null || editor === null || !editing) {
            return;
        }
        if (b.classList.contains("md-link")) {
            const href = v.state.selection.$from.marks().find((m) => m.type.name === "link")?.attrs.href;
            urlInput.value = typeof href === "string" ? href : "";
            urlInput.hidden = false;
            urlInput.focus();
            urlInput.select();
            return;
        }
        ACTIONS[Number(b.dataset.action)]?.run(editor, v);
        v.focus();
    });
    urlInput.addEventListener("keydown", (e) => {
        const v = view();
        if (v === null || (e.key !== "Enter" && e.key !== "Escape")) {
            return;
        }
        e.preventDefault();
        e.stopPropagation();
        if (e.key === "Enter") {
            applyLink(v, urlInput.value.trim());
        }
        hideLinkInput();
        v.focus();
    });

    const initial = opts.initial ?? el.dataset.value ?? "";
    Editor.make()
        .config((ctx) => {
            ctx.set(rootCtx, host);
            ctx.set(defaultValueCtx, initial);
            ctx.update(editorViewOptionsCtx, (prev) => ({ ...prev, editable: () => editing }));
        })
        .use(commonmark)
        .use(gfm)
        .use(history)
        .create()
        .then((created) => {
            if (destroyed) {
                void created.destroy();
                return;
            }
            editor = created;
            baseline = created.ctx.get(editorViewCtx).state.doc;
        })
        .catch((err: unknown) => {
            // Fall back to the raw text; the field stays read-only.
            host.textContent = initial;
            console.error("TRLC preview: Markdown editor failed", err);
        });

    return {
        setNote,
        destroy(): void {
            destroyed = true;
            listeners.abort();
            if (editor !== null) {
                void editor.destroy();
                editor = null;
            }
        },
    };
}
