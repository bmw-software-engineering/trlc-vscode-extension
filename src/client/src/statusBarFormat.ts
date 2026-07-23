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
 * Pure formatting for the TRLC status bar item — deliberately has no
 * `vscode` import (not even type-only) so it can be unit-tested with plain
 * mocha/node, following the same pure-module convention as log.ts /
 * installerCore.ts. `statusBar.ts` is the thin `vscode.StatusBarItem`
 * wiring around this.
 */

/** Mirrors the JSON shape returned by the server's `trlc/scopeStatus` request. */
export interface ScopeStatus {
    scopeId: string;
    parseMode: string;
    verifyMode: boolean;
    fileCount: number;
    openFileCount: number;
    errorCount: number;
    warningCount: number;
    /** Set when this cycle's discovery fell back to directory-mode parsing
     * instead of the configured mode (BAZEL only, and only for its sole
     * remaining quiet fallback: no Bazel workspace found at all — a plain,
     * non-Bazel folder in a mixed workspace). Every other "Bazel found
     * nothing usable" condition (no targets, file owned by no target, a
     * resolved target with no files) instead shows a popup and leaves this
     * scope with no published status at all until fixed and reparsed, so
     * it never surfaces here as a fallback. `null` when the configured
     * strategy ran normally. */
    fallbackReason: string | null;
}

export interface ParsingState {
    active: boolean;
    /** LSP work-done progress percentage (0-100), when the server reports one. */
    percentage?: number;
}

export interface StatusBarDisplay {
    text: string;
    tooltip: string;
}

function scopeTooltip(status: ScopeStatus): string {
    const lines = [
        `TRLC parse scope: ${status.scopeId}`,
        `Parse mode: ${status.parseMode}`,
        `Verify (CVC5): ${status.verifyMode ? "on" : "off"}`,
        `Files in scope: ${status.fileCount} (${status.openFileCount} open in editor)`,
        `Diagnostics: ${status.errorCount} error(s), ${status.warningCount} warning(s)`,
    ];
    if (status.fallbackReason) {
        lines.push(
            `Fell back to directory-mode parsing: ${status.fallbackReason}`
        );
    }
    return lines.join("\n");
}

/** Computes the status bar text/tooltip for one (parsing, status) pair. */
export function formatStatusBarDisplay(
    parsing: ParsingState,
    status: ScopeStatus | null
): StatusBarDisplay {
    if (parsing.active) {
        const pct = typeof parsing.percentage === "number" ? ` ${parsing.percentage}%` : "";
        return {
            text: `$(sync~spin) TRLC: Parsing${pct}`,
            tooltip: status ? scopeTooltip(status) : "TRLC: Parsing…",
        };
    }
    if (!status) {
        return {
            text: "$(circle-outline) TRLC: no scope",
            tooltip: "This file is not part of any active TRLC parse scope yet.",
        };
    }
    if (status.fallbackReason) {
        // The configured mode (e.g. "bazel") didn't actually run this cycle
        // — the only remaining fallback reason is "no Bazel workspace found",
        // which always degrades to directory-mode parsing, so that's the
        // mode actually reflected here, not status.parseMode (which is what's
        // configured, not what ran).
        const fileWord = status.fileCount === 1 ? "file" : "files";
        return {
            text: `$(warning) TRLC: directory (${status.parseMode} fallback) · ${status.fileCount} ${fileWord}`,
            tooltip: scopeTooltip(status),
        };
    }
    const icon = status.errorCount > 0 ? "$(error)" : status.warningCount > 0 ? "$(warning)" : "$(check)";
    const fileWord = status.fileCount === 1 ? "file" : "files";
    return {
        text: `${icon} TRLC: ${status.parseMode} · ${status.fileCount} ${fileWord}`,
        tooltip: scopeTooltip(status),
    };
}

/** One entry in the "files in scope" QuickPick — a superset of
 * `vscode.QuickPickItem`'s required `label`, plus the full path needed to
 * open it (kept off `label`/`description` so those stay purely display). */
export interface ScopeFileItem {
    label: string;
    description: string;
    path: string;
}

/** Splits each absolute path into a filename label + directory description,
 * sorted by label so same-named files in different directories still read
 * clearly in a searchable list. Handles both `/` and `\` separators since
 * paths come from the (possibly Windows) server's filesystem. */
export function toScopeFileItems(files: string[]): ScopeFileItem[] {
    return files
        .map((path) => {
            const idx = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
            return {
                label: idx >= 0 ? path.slice(idx + 1) : path,
                description: idx >= 0 ? path.slice(0, idx) : "",
                path,
            };
        })
        .sort((a, b) => a.label.localeCompare(b.label));
}
