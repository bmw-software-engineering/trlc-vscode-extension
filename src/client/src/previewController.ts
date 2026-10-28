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

import {
    EditResult,
    HostToWebview,
    PREVIEW_ADD_RECORD_METHOD,
    PREVIEW_DISSOLVE_SECTION_METHOD,
    PREVIEW_INSERT_SECTION_METHOD,
    PREVIEW_MODEL_METHOD,
    PREVIEW_MOVE_METHOD,
    PREVIEW_RENAME_SECTION_METHOD,
    PREVIEW_SET_FIELD_METHOD,
    PreviewModel,
    STALE_CODE,
    WebviewToHost,
} from "./previewProtocol";

/** VS Code interaction injected into the controller (keeps it `vscode`-free). */
export interface PreviewDeps {
    sendRequest<T>(method: string, params: object): Promise<T>;
    applyEdit(lspWorkspaceEdit: unknown): Promise<boolean>;
    showError(message: string): void;
    reveal(uri: string, line: number): void;
}

/**
 * True if every versioned TextDocumentEdit in the LSP WorkspaceEdit `edit`
 * targets the document version that is open now (`versionOf`). The
 * languageclient's converter drops the version, so it is checked here.
 */
export function editMatchesVersions(edit: unknown, versionOf: (uri: string) => number | undefined): boolean {
    const changes = (edit as { documentChanges?: unknown[] } | null)?.documentChanges ?? [];
    return changes.every((change) => {
        const doc = (change as { textDocument?: { uri: string; version?: number | null } }).textDocument;
        return doc?.version === undefined || doc.version === null || versionOf(doc.uri) === doc.version;
    });
}

export const SWITCHED_MESSAGE = "TRLC: The preview switched files; the action was cancelled.";
export const BUSY_MESSAGE = "TRLC: The parser was busy; the edit was not saved. Try again.";
const APPLY_FAILED_MESSAGE = "TRLC: Could not apply edit.";

type IdBasedMessage = Exclude<WebviewToHost, { type: "ready" | "reveal" | "setField" }>;

function isStale(result: EditResult | null | undefined): boolean {
    return result !== null && result !== undefined && "error" in result && result.code === STALE_CODE;
}

/** Server method and params of an id-based edit (carries uri and modelVersion). */
export function idBasedRequest(msg: IdBasedMessage): [string, object] {
    const ref = { uri: msg.uri, modelVersion: msg.modelVersion };
    switch (msg.type) {
        case "addRecord":
            return [PREVIEW_ADD_RECORD_METHOD, { ...ref, sectionId: msg.sectionId, type: msg.recordType, name: msg.name }];
        case "insertSection":
            return [PREVIEW_INSERT_SECTION_METHOD, { ...ref, sectionId: msg.sectionId, title: msg.title }];
        case "renameSection":
            return [PREVIEW_RENAME_SECTION_METHOD, { ...ref, sectionId: msg.sectionId, title: msg.title }];
        case "dissolveSection":
            return [PREVIEW_DISSOLVE_SECTION_METHOD, { ...ref, sectionId: msg.sectionId }];
        case "move":
            return [
                PREVIEW_MOVE_METHOD,
                { ...ref, item: msg.item, targetSectionId: msg.targetSectionId, before: msg.before },
            ];
    }
}

/** Host-side logic of the preview: mediates between webview and server. */
export class PreviewController {
    private currentUri: string | undefined;
    /** Bumped on every document change and refresh; stale model/error posts are dropped. */
    private seq = 0;
    /** Tail of the edit queue (see enqueue). */
    private editQueue: Promise<void> = Promise.resolve();
    /** Set while a stale-rejected edit waits for the next editable model. */
    private modelWaiter: ((retry: boolean) => void) | undefined;

    constructor(
        private readonly deps: PreviewDeps,
        private readonly post: (msg: HostToWebview) => void,
    ) {}

    get uri(): string | undefined {
        return this.currentUri;
    }

    async setDocument(uri: string | undefined): Promise<void> {
        if (uri === this.currentUri) {
            return;
        }
        this.currentUri = uri;
        this.seq++;
        this.releaseWaiter(false);
        await this.refresh();
    }

    async refresh(): Promise<void> {
        const uri = this.currentUri;
        if (uri === undefined) {
            return;
        }
        const seq = ++this.seq;
        await this.guarded(seq, async () => {
            const model = await this.deps.sendRequest<PreviewModel | null | undefined>(PREVIEW_MODEL_METHOD, { uri });
            if (seq !== this.seq) {
                return;
            }
            if (model === null || model === undefined) {
                this.post({ type: "error", message: "TRLC: No preview data from the language server." });
                return;
            }
            this.post({ type: "model", model });
            if (model.editable) {
                // The server has caught up with the buffer: retry a stale edit.
                this.releaseWaiter(true);
            }
        });
    }

    async handleMessage(msg: WebviewToHost): Promise<void> {
        const uri = this.currentUri;
        if (uri === undefined) {
            if (msg.type === "ready") {
                this.post({ type: "noDocument" });
            }
            return;
        }
        try {
            await this.dispatch(uri, msg);
        } catch (e) {
            this.postError(e);
        }
    }

    private async dispatch(uri: string, msg: WebviewToHost): Promise<void> {
        switch (msg.type) {
            case "ready":
                await this.refresh();
                break;
            case "reveal":
                this.deps.reveal(uri, msg.line);
                break;
            case "setField":
                // Versioned against msg.uri: still valid after the preview
                // followed another editor, so it is sent there regardless.
                await this.enqueue(() => this.runSetField(msg));
                break;
            default: {
                if (msg.uri !== uri) {
                    // Made from a model that is no longer on screen.
                    this.deps.showError(SWITCHED_MESSAGE);
                    return;
                }
                const [method, params] = idBasedRequest(msg);
                await this.enqueue(() => this.runIdBasedEdit(msg.uri, method, params));
                break;
            }
        }
    }

    /**
     * Queues an edit behind any edit still in flight. Edits run one at a
     * time: the server computes positions against its last parse, so the
     * next edit may only be requested once the previous one has been
     * applied (or has failed).
     */
    private enqueue(job: () => Promise<void>): Promise<void> {
        const run = this.editQueue.then(job);
        this.editQueue = run.catch(() => undefined);
        return run;
    }

    private request(method: string, params: object): Promise<EditResult | null | undefined> {
        return this.deps.sendRequest<EditResult | null | undefined>(method, params);
    }

    /**
     * A field edit. A stale rejection (the server has not parsed the latest
     * buffer yet, typically the previous edit) is held and resent once on
     * the next editable model. Refusals are reported to the webview
     * (editRejected) and refreshed only while its document is still shown.
     */
    private async runSetField(msg: Extract<WebviewToHost, { type: "setField" }>): Promise<void> {
        const { uri, record, field, value } = msg;
        const shown = (): boolean => this.currentUri === uri;
        const rejected = async (message: string, retry: boolean): Promise<void> => {
            this.deps.showError(message);
            if (shown()) {
                this.post({ type: "editRejected", record, field, value, message, ...(retry ? { retry } : {}) });
                await this.refresh();
            }
        };
        try {
            const params = { uri, record, field, value };
            let result = await this.request(PREVIEW_SET_FIELD_METHOD, params);
            if (isStale(result)) {
                if (!shown() || !(await this.waitForEditableModel())) {
                    // No model of that document to wait for any more.
                    this.deps.showError(BUSY_MESSAGE);
                    return;
                }
                result = await this.request(PREVIEW_SET_FIELD_METHOD, params);
                if (isStale(result)) {
                    await rejected(BUSY_MESSAGE, true);
                    return;
                }
            }
            if (result === null || result === undefined) {
                await this.noResponse(shown());
            } else if ("error" in result) {
                await rejected("TRLC: " + result.error, false);
            } else if (!(await this.deps.applyEdit(result.edit))) {
                this.deps.showError(APPLY_FAILED_MESSAGE);
            }
        } catch (e) {
            this.reportFailure(e, shown());
        }
    }

    /**
     * An edit that addresses sections by id: never resent, since the ids
     * may mean other sections after the next parse. Any refusal (including
     * stale and outdated) is shown and the preview refreshed; the webview
     * keeps its structure controls disabled until that next model.
     */
    private async runIdBasedEdit(uri: string, method: string, params: object): Promise<void> {
        const shown = (): boolean => this.currentUri === uri;
        try {
            const result = await this.request(method, params);
            if (result === null || result === undefined) {
                await this.noResponse(shown());
                return;
            }
            if ("error" in result) {
                this.deps.showError("TRLC: " + result.error);
            } else if (await this.deps.applyEdit(result.edit)) {
                return; // the reparse of the edited buffer brings the next model
            } else {
                this.deps.showError(APPLY_FAILED_MESSAGE);
            }
            if (shown()) {
                await this.refresh();
            }
        } catch (e) {
            this.reportFailure(e, shown());
        }
    }

    private async noResponse(shown: boolean): Promise<void> {
        this.deps.showError("TRLC: No response from the language server.");
        if (shown) {
            await this.refresh();
        }
    }

    private reportFailure(e: unknown, shown: boolean): void {
        if (shown) {
            this.postError(e);
        } else {
            this.deps.showError("TRLC: " + (e instanceof Error ? e.message : String(e)));
        }
    }

    /** Resolves true on the next editable model, false if the document changes first. */
    private waitForEditableModel(): Promise<boolean> {
        this.releaseWaiter(false);
        return new Promise((resolve) => {
            this.modelWaiter = resolve;
        });
    }

    private releaseWaiter(retry: boolean): void {
        const waiter = this.modelWaiter;
        this.modelWaiter = undefined;
        waiter?.(retry);
    }

    private async guarded(seq: number, fn: () => Promise<void>): Promise<void> {
        try {
            await fn();
        } catch (e) {
            if (seq === this.seq) {
                this.postError(e);
            }
        }
    }

    private postError(e: unknown): void {
        this.post({ type: "error", message: e instanceof Error ? e.message : String(e) });
    }
}
