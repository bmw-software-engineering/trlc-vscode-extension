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
    WebviewToHost,
} from "./previewProtocol";

/** VS Code interaction injected into the controller (keeps it `vscode`-free). */
export interface PreviewDeps {
    sendRequest<T>(method: string, params: object): Promise<T>;
    applyEdit(lspWorkspaceEdit: unknown): Promise<boolean>;
    showError(message: string): void;
    reveal(uri: string, line: number): void;
}

/** Host-side logic of the preview: mediates between webview and server. */
export class PreviewController {
    private currentUri: string | undefined;

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
        await this.refresh();
    }

    async refresh(): Promise<void> {
        const uri = this.currentUri;
        if (uri === undefined) {
            return;
        }
        await this.guarded(async () => {
            const model = await this.deps.sendRequest<PreviewModel>(PREVIEW_MODEL_METHOD, { uri });
            this.post({ type: "model", model });
        });
    }

    async handleMessage(msg: WebviewToHost): Promise<void> {
        const uri = this.currentUri;
        if (uri === undefined) {
            return;
        }
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

    private async edit(method: string, params: object): Promise<void> {
        await this.guarded(async () => {
            const result = await this.deps.sendRequest<EditResult>(method, params);
            if ("error" in result) {
                this.deps.showError("TRLC: " + result.error);
                await this.refresh();
                return;
            }
            if (!(await this.deps.applyEdit(result.edit))) {
                this.deps.showError("TRLC: Could not apply edit.");
            }
        });
    }

    private async guarded(fn: () => Promise<void>): Promise<void> {
        try {
            await fn();
        } catch (e) {
            this.post({ type: "error", message: e instanceof Error ? e.message : String(e) });
        }
    }
}
