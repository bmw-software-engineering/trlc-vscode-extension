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
 * Pure Markdown helpers of the preview webview (no DOM, no editor library),
 * so they can be unit-tested with plain mocha. The editor itself lives in
 * previewMarkdownEditor.ts.
 */

import { textCommitValue } from "./previewFocus";

/**
 * Mirrors the server's format_markdown normalisation: LF line ends, no
 * trailing whitespace per line, no leading/trailing blank lines.
 */
export function normalizeMarkdown(md: string): string {
    const lines = md.replace(/\r\n?/g, "\n").split("\n").map((l) => l.trimEnd());
    let start = 0;
    let end = lines.length;
    while (start < end && lines[start] === "") {
        start++;
    }
    while (end > start && lines[end - 1] === "") {
        end--;
    }
    return lines.slice(start, end).join("\n");
}

/** Whether two Markdown texts differ after normalisation. */
export function markdownChanged(before: string, after: string): boolean {
    return normalizeMarkdown(before) !== normalizeMarkdown(after);
}

/**
 * Value to post when a Markdown field is committed: undefined posts nothing
 * (unchanged), null clears an optional field, otherwise the normalised text.
 */
export function markdownCommitValue(md: string, rendered: string, optional: boolean): string | null | undefined {
    if (!markdownChanged(rendered, md)) {
        return undefined;
    }
    return textCommitValue(normalizeMarkdown(md), "string", optional);
}

/** A Markdown edit the server refused; the webview keeps showing the user's text. */
export interface MarkdownRejection {
    uri: string;
    record: string;
    field: string;
    /** The field's value in the model when the edit was refused. */
    serverValue: string;
}

/**
 * Whether a refused edit still replaces the server's value of this field:
 * same document, record and field, and the server's value is unchanged
 * (a changed value means the document moved on).
 */
export function rejectionApplies(
    rejection: MarkdownRejection,
    uri: string,
    record: string,
    field: string,
    serverValue: string
): boolean {
    return (
        rejection.uri === uri &&
        rejection.record === record &&
        rejection.field === field &&
        !markdownChanged(rejection.serverValue, serverValue)
    );
}

/** What a mounted Markdown editor shows; equal keys can keep the editor across renders. */
export interface MarkdownFieldIdentity {
    uri: string;
    record: string;
    field: string;
    /** The server's value (data-value). */
    value: string;
    editable: boolean;
    optional: boolean;
}

export function markdownFieldKey(f: MarkdownFieldIdentity): string {
    return JSON.stringify([f.uri, f.record, f.field, f.value, f.editable, f.optional]);
}

/** A mounted editor of the previous render. */
export interface MountedMarkdownField {
    key: string;
    /** false if it shows other text than its key's value (committed or a refused edit). */
    reusable: boolean;
}

/**
 * Which mounted editors a new render keeps: `reuse[i]` is the index in
 * `mounted` of the editor moved into the i-th new field (null: mount a new
 * one), `destroy` the indices of editors no new field takes over.
 */
export function planMarkdownReuse(
    mounted: MountedMarkdownField[],
    freshKeys: string[]
): { reuse: (number | null)[]; destroy: number[] } {
    const free = new Map<string, number[]>();
    mounted.forEach((m, i) => {
        if (m.reusable) {
            free.set(m.key, [...(free.get(m.key) ?? []), i]);
        }
    });
    const taken = new Set<number>();
    const reuse = freshKeys.map((key) => {
        const i = free.get(key)?.shift();
        if (i === undefined) {
            return null;
        }
        taken.add(i);
        return i;
    });
    const destroy = mounted.map((_, i) => i).filter((i) => !taken.has(i));
    return { reuse, destroy };
}
