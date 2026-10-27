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
 * Wire types of the server's trlc/preview* requests and of the messages
 * exchanged between the extension host and the preview webview. Pure types
 * and constants (no `vscode` import).
 */

export const PREVIEW_MODEL_METHOD = "trlc/previewModel";
export const PREVIEW_SET_FIELD_METHOD = "trlc/previewSetField";
export const PREVIEW_ADD_RECORD_METHOD = "trlc/previewAddRecord";

export type PreviewFieldKind = "string" | "integer" | "decimal" | "boolean" | "enum" | "reference" | "other";

export interface PreviewField {
    name: string;
    kind: PreviewFieldKind;
    /** TRLC source text (string: the unescaped value); null when unset. */
    value: string | null;
    optional: boolean;
    /** false for frozen components. */
    editable: boolean;
    /** Literal names; null unless kind is "enum". */
    enumValues: string[] | null;
}

export interface PreviewRecordNode {
    kind: "record";
    name: string;
    type: string;
    /** 0-based line of the record name. */
    line: number;
    fields: PreviewField[];
}

export interface PreviewSectionNode {
    kind: "section";
    sectionId: number;
    title: string;
    depth: number;
    children: PreviewNode[];
}

export type PreviewNode = PreviewRecordNode | PreviewSectionNode;

export interface PreviewModel {
    uri: string;
    package: string | null;
    editable: boolean;
    /** Why the file cannot be edited; null when editable. */
    reason: string | null;
    recordTypes: string[];
    /** null when the model is unavailable. */
    nodes: PreviewNode[] | null;
}

/** `edit` is an LSP WorkspaceEdit (JSON). */
export type EditResult = { edit: unknown } | { error: string };

export type HostToWebview = { type: "model"; model: PreviewModel } | { type: "error"; message: string };

export type WebviewToHost =
    | { type: "ready" }
    | { type: "setField"; record: string; field: string; value: string | null }
    | { type: "addRecord"; sectionId: number | null; recordType: string; name: string }
    | { type: "reveal"; line: number };
