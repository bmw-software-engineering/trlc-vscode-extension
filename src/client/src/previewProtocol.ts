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
export const PREVIEW_INSERT_SECTION_METHOD = "trlc/previewInsertSection";
export const PREVIEW_RENAME_SECTION_METHOD = "trlc/previewRenameSection";
export const PREVIEW_DISSOLVE_SECTION_METHOD = "trlc/previewDissolveSection";
export const PREVIEW_MOVE_METHOD = "trlc/previewMove";
/** `code` of an edit rejected because the server's parse is behind the buffer. */
export const STALE_CODE = "stale";
/** `code` of an id-based edit made from an older model than the server's parse. */
export const OUTDATED_CODE = "outdated";

export type PreviewFieldKind = "string" | "integer" | "decimal" | "boolean" | "enum" | "reference" | "array" | "other";

/** Value of a field edit: a list of element inputs for "array" fields; null unsets. */
export type FieldValue = string | string[] | null;

export interface PreviewField {
    name: string;
    kind: PreviewFieldKind;
    /** TRLC source text (string: the unescaped value); null when unset. */
    value: string | null;
    optional: boolean;
    /** false for frozen components. */
    editable: boolean;
    /** Literal names; null unless kind (or elementKind) is "enum". */
    enumValues: string[] | null;
    /** Kind of the elements of an "array" field; null for other kinds. */
    elementKind: PreviewFieldKind | null;
    /** Elements of a set "array" field (string: unescaped, enum: literal name); null otherwise. */
    items: string[] | null;
    /** Key into PreviewModel.references for a reference (or list-of-references) field; null otherwise. */
    referenceType: string | null;
    /** true for a string field edited as Markdown (configured via trlcServer.preview.markdownComponents). */
    markdown: boolean;
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
    /** false for a section still open at end of file (no add-record target). */
    closed: boolean;
    children: PreviewNode[];
}

/** A movable item: a record by name or a section by id. */
export type ItemRef = { kind: "record"; name: string } | { kind: "section"; sectionId: number };

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
    /**
     * Buffer version the server's parse matches (null: unavailable or not
     * open). Id-based edits send it back as `modelVersion`: section ids
     * renumber after structural edits, so the server refuses an edit made
     * from another version as outdated.
     */
    version: number | null;
    /** Names of the records each `referenceType` accepts (package-qualified outside the file's package). */
    references: Record<string, string[]>;
    /**
     * Closed .trlc files declaring records this file references but TRLC
     * reported unknown (parse mode "workspace" parses open files only).
     */
    openFiles: OpenableFile[];
}

/** A closed file the preview offers to open. */
export interface OpenableFile {
    uri: string;
    /** Path relative to the workspace folder. */
    label: string;
    /** The unknown symbols it declares. */
    symbols: string[];
}

/**
 * `edit` is an LSP WorkspaceEdit (JSON) with one versioned TextDocumentEdit
 * in `documentChanges`. `code` is STALE_CODE when the server's parse is
 * behind the buffer (the edit can be retried after the next parse) and
 * OUTDATED_CODE when an id-based edit was made from another model version.
 */
export type EditResult = { edit: unknown } | { error: string; code?: string };

export type HostToWebview =
    | { type: "model"; model: PreviewModel }
    | { type: "error"; message: string }
    | {
          type: "editRejected";
          record: string;
          field: string;
          value: FieldValue;
          message: string;
          /** The parser was busy: committing the same value again may succeed. */
          retry?: boolean;
      }
    | { type: "noDocument" };

/**
 * What an id-based edit was made from: the document and the `version` of
 * the model on screen.
 */
export interface ModelRef {
    uri: string;
    modelVersion: number | null;
}

/** Every edit message names the document (`uri`) of the model it was made from. */
export type WebviewToHost =
    | { type: "ready" }
    | { type: "setField"; uri: string; record: string; field: string; value: FieldValue }
    | ({ type: "addRecord"; sectionId: number | null; recordType: string; name: string } & ModelRef)
    | ({ type: "insertSection"; sectionId: number | null; title: string } & ModelRef)
    | ({ type: "renameSection"; sectionId: number; title: string } & ModelRef)
    | ({ type: "dissolveSection"; sectionId: number } & ModelRef)
    | ({ type: "move"; item: ItemRef; targetSectionId: number | null; before: ItemRef | null } & ModelRef)
    | { type: "reveal"; line: number }
    /** Opens one of the model's `openFiles` as a background editor tab. */
    | { type: "openFile"; uri: string };
