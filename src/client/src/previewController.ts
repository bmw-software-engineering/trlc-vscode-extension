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
    PREVIEW_MODEL_METHOD,
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

/** Host-side logic of the preview: mediates between webview and server. */
export class PreviewController {
    private currentUri: string | undefined;
    /** Bumped only by setDocument; edit results of a previous document are dropped. */
    private docGen = 0;
    /** Bumped on every document change and refresh; stale model/error posts are dropped. */
    private seq = 0;
    /**
     * Tail of the edit queue. Edits run one at a time: the server computes
     * positions against its last parse, so the next edit may only be
     * requested once the previous one has been applied (or has failed).
     */
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
        this.docGen++;
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
            case "setField":
                await this.edit(PREVIEW_SET_FIELD_METHOD, {
                    uri,
                    record: msg.record,
                    field: msg.field,
                    value: msg.value,
                });
                break;
            case "addRecord":
                await this.edit(PREVIEW_ADD_RECORD_METHOD, {
                    uri,
                    sectionId: msg.sectionId,
                    type: msg.recordType,
                    name: msg.name,
                });
                break;
            case "reveal":
                this.deps.reveal(uri, msg.line);
                break;
        }
    }

    /** Queues an edit behind any edit still in flight. */
    private edit(method: string, params: object): Promise<void> {
        const gen = this.docGen;
        const run = this.editQueue.then(() => this.runEdit(gen, method, params));
        this.editQueue = run.catch(() => undefined);
        return run;
    }

    private async runEdit(gen: number, method: string, params: object): Promise<void> {
        const stale = (): boolean => gen !== this.docGen;
        if (stale()) {
            return;
        }
        try {
            let result = await this.deps.sendRequest<EditResult | null | undefined>(method, params);
            if (stale()) {
                return;
            }
            if (result && "error" in result && result.code === STALE_CODE) {
                // The server has not parsed the latest buffer yet (typically
                // the previous edit): keep this edit and resend it once.
                if (!(await this.waitForEditableModel()) || stale()) {
                    return;
                }
                result = await this.deps.sendRequest<EditResult | null | undefined>(method, params);
                if (stale()) {
                    return;
                }
            }
            if (result === null || result === undefined) {
                this.deps.showError("TRLC: No response from the language server.");
                await this.refresh();
                return;
            }
            if ("error" in result) {
                this.deps.showError("TRLC: " + result.error);
                await this.refresh();
                return;
            }
            if (!(await this.deps.applyEdit(result.edit)) && !stale()) {
                this.deps.showError("TRLC: Could not apply edit.");
            }
        } catch (e) {
            if (!stale()) {
                this.postError(e);
            }
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
