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
 * Opens a .trlc file as a background editor tab: in parse mode "workspace"
 * the server parses open .trlc files only, so opening the file that
 * declares an unknown record resolves it. The file being worked on stays
 * in front, and the preview keeps showing it.
 */

import { Uri, ViewColumn, window } from "vscode";

/** Command run by the server's "Open <file>" quick fix (argument: file URI). */
export const OPEN_IN_BACKGROUND_COMMAND = "trlc.openFileInBackground";

/** How long a background-opened file is ignored by the preview's follow. */
const FOLLOW_GRACE_MS = 1000;

const opening = new Set<string>();

/** True while `uri` is being opened in the background (the preview must not follow it). */
export function isOpeningInBackground(uri: string): boolean {
    return opening.has(uri);
}

/** Opens `target` as a pinned tab in the group showing `keep`, then brings `keep` back to the front. */
export async function openInBackground(target: Uri, keep: Uri | undefined): Promise<void> {
    const key = target.toString();
    const keepEditor = keep && window.visibleTextEditors.find((e) => e.document.uri.toString() === keep.toString());
    const viewColumn = keepEditor?.viewColumn ?? ViewColumn.One;
    opening.add(key);
    try {
        await window.showTextDocument(target, { viewColumn, preserveFocus: true, preview: false });
        if (keepEditor) {
            await window.showTextDocument(keepEditor.document, { viewColumn, preserveFocus: true });
        }
    } finally {
        // Editor-change events may arrive after the calls above resolve.
        setTimeout(() => opening.delete(key), FOLLOW_GRACE_MS);
    }
}
