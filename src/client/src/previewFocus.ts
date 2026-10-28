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

export interface RenderState {
    /** An input/textarea/select inside the preview has focus. */
    inputFocused: boolean;
    /** The inline "add record" form is open. */
    addFormOpen: boolean;
    /** A drag & drop gesture is in progress (a re-render would swallow the drop). */
    dragging: boolean;
}

/** A pending model may replace the DOM only when it cannot eat user input. */
export function shouldRenderNow(state: RenderState): boolean {
    return !state.inputFocused && !state.addFormOpen && !state.dragging;
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
