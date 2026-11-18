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

/** Pure decisions of the preview webview (unit-testable without a DOM). */

import { PreviewModel, PreviewNode } from "./previewProtocol";

export interface RenderState {
    /** An input/textarea/select inside the preview has focus. */
    inputFocused: boolean;
    /** The inline "add record" form is open. */
    addFormOpen: boolean;
    /** A drag & drop gesture is in progress (a re-render would swallow the drop). */
    dragging: boolean;
    /** The mouse is held down on a drag handle (a drag may be about to start). */
    pressing: boolean;
}

/** A pending model may replace the DOM only when it cannot eat user input. */
export function shouldRenderNow(state: RenderState): boolean {
    return !state.inputFocused && !state.addFormOpen && !state.dragging && !state.pressing;
}

/** Whether a focusout should trigger a deferred flush of the pending model. */
export function focusLeavesPreview(relatedTargetInside: boolean): boolean {
    return !relatedTargetInside;
}

/**
 * Value to post for a select change: null removes the value (blank chosen on
 * a currently set field), undefined posts nothing.
 */
export function selectCommitValue(selected: string, renderedValue: string): string | null | undefined {
    if (selected !== "") {
        return selected;
    }
    return renderedValue === "" ? undefined : null;
}

/**
 * Value to post for a committed text input/textarea. Clearing an optional
 * field removes its value (null); a required field posts the text as-is so
 * the server reports why it is invalid. Whitespace counts as empty except
 * for strings, where it is a legitimate value.
 */
export function textCommitValue(value: string, kind: string, optional: boolean): string | null {
    const empty = kind === "string" ? value === "" : value.trim() === "";
    return optional && empty ? null : value;
}

/** One element row of a rendered list field. */
export interface ArrayRow {
    value: string;
    /** Created in the preview (not part of the rendered list). */
    added: boolean;
}

/**
 * Value to post for a list field: its elements in order. Added rows left
 * empty are ignored; undefined posts nothing (unchanged from `rendered`).
 */
export function arrayCommitValue(rows: ArrayRow[], rendered: string[]): string[] | undefined {
    const items = rows.filter((r) => !(r.added && r.value.trim() === "")).map((r) => r.value);
    const unchanged = items.length === rendered.length && items.every((v, i) => v === rendered[i]);
    return unchanged ? undefined : items;
}

/**
 * Whether a webview message addresses sections by id (add record and the
 * structure edits). Such an intent is only valid for the model it was made
 * from, so structure controls stay disabled until the next model renders.
 */
export function isIdBasedIntent(msg: { type: string }): boolean {
    return ["addRecord", "insertSection", "renameSection", "dissolveSection", "move"].includes(msg.type);
}

/** The layout of a tree as far as id-based intents see it (section ids, titles, nesting; record names). */
function skeleton(nodes: PreviewNode[] | null): string {
    const walk = (list: PreviewNode[]): unknown[] =>
        list.map((n) =>
            n.kind === "record" ? n.name : [n.sectionId, n.title, n.depth, n.closed, walk(n.children)]
        );
    return JSON.stringify(nodes === null ? null : walk(nodes));
}

/**
 * `modelVersion` of an id-based intent made from the `rendered` model. A
 * newer model of the same document can be held back (not rendered while a
 * form is open or a field is being edited); if its sections and records are
 * laid out identically, the rendered ids name the same items in it, so its
 * version is sent instead (otherwise the intent would be refused as
 * outdated, e.g. after a field edit committed by opening the form).
 */
export function intentModelVersion(rendered: PreviewModel, held: PreviewModel | null): number | null {
    if (
        held !== null &&
        held.uri === rendered.uri &&
        held.editable &&
        held.nodes !== null &&
        skeleton(held.nodes) === skeleton(rendered.nodes)
    ) {
        return held.version;
    }
    return rendered.version;
}
