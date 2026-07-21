/* -------------------------------------------------------------------------
 * TRLC VSCode Extension
 * Copyright (C) 2025 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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
 * Status bar item showing the active document's TRLC parse state: whether
 * the server is currently parsing (from LSP work-done progress), and once
 * idle, the parse mode + file count of the active document's scope (from
 * the server's custom `trlc/scopeStatus` request — see
 * server/handlers/status.py). Formatting itself lives in statusBarFormat.ts
 * (kept `vscode`-free so it's unit-testable); this class is the thin
 * `vscode.StatusBarItem` wiring around it.
 */

import { StatusBarAlignment, StatusBarItem, window } from "vscode";

import { formatStatusBarDisplay, ParsingState, ScopeStatus } from "./statusBarFormat";

export type { ParsingState, ScopeStatus } from "./statusBarFormat";

const IDLE: ParsingState = { active: false };

/**
 * Owns the live `vscode.StatusBarItem` and re-renders it on every update.
 * Visibility (show/hide for non-TRLC files) is the caller's call — kept
 * separate from formatting so that decision stays a one-liner in
 * extension.ts instead of being baked into this class.
 */
export class TrlcStatusBarItem {
    private readonly item: StatusBarItem;
    private parsing: ParsingState = IDLE;
    private status: ScopeStatus | null = null;

    constructor(commandId: string) {
        this.item = window.createStatusBarItem(StatusBarAlignment.Right, 100);
        this.item.name = "TRLC";
        this.item.command = commandId;
        this.render();
    }

    setParsing(parsing: ParsingState): void {
        this.parsing = parsing;
        this.render();
    }

    setStatus(status: ScopeStatus | null): void {
        this.status = status;
        this.render();
    }

    show(): void {
        this.item.show();
    }

    hide(): void {
        this.item.hide();
    }

    dispose(): void {
        this.item.dispose();
    }

    private render(): void {
        const display = formatStatusBarDisplay(this.parsing, this.status);
        this.item.text = display.text;
        this.item.tooltip = display.tooltip;
    }
}
