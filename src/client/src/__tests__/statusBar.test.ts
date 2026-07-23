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

import * as assert from "assert";

import { formatStatusBarDisplay, ScopeStatus, toScopeFileItems } from "../statusBarFormat";

function status(overrides: Partial<ScopeStatus> = {}): ScopeStatus {
    return {
        scopeId: "file:///ws",
        parseMode: "directory",
        verifyMode: true,
        fileCount: 3,
        openFileCount: 1,
        errorCount: 0,
        warningCount: 0,
        fallbackReason: null,
        ...overrides,
    };
}

describe("formatStatusBarDisplay", () => {
    it("shows a spinner and percentage while parsing", () => {
        const display = formatStatusBarDisplay({ active: true, percentage: 42 }, null);

        assert.match(display.text, /^\$\(sync~spin\) TRLC: Parsing 42%$/);
    });

    it("shows a spinner without a percentage when none is reported", () => {
        const display = formatStatusBarDisplay({ active: true }, null);

        assert.strictEqual(display.text, "$(sync~spin) TRLC: Parsing");
    });

    it("reports 'no scope' when idle and the uri has no active scope", () => {
        const display = formatStatusBarDisplay({ active: false }, null);

        assert.strictEqual(display.text, "$(circle-outline) TRLC: no scope");
    });

    it("shows parse mode and file count when idle with a clean scope", () => {
        const display = formatStatusBarDisplay({ active: false }, status({ fileCount: 5 }));

        assert.strictEqual(display.text, "$(check) TRLC: directory · 5 files");
    });

    it("uses singular 'file' for a single-file scope", () => {
        const display = formatStatusBarDisplay({ active: false }, status({ fileCount: 1 }));

        assert.strictEqual(display.text, "$(check) TRLC: directory · 1 file");
    });

    it("switches to an error icon when the scope has errors", () => {
        const display = formatStatusBarDisplay({ active: false }, status({ errorCount: 2 }));

        assert.match(display.text, /^\$\(error\)/);
    });

    it("switches to a warning icon when the scope has warnings but no errors", () => {
        const display = formatStatusBarDisplay({ active: false }, status({ warningCount: 1 }));

        assert.match(display.text, /^\$\(warning\)/);
    });

    it("includes scope details in the tooltip", () => {
        const display = formatStatusBarDisplay(
            { active: false },
            status({ scopeId: "file:///ws/pkg", parseMode: "bazel", verifyMode: false, errorCount: 1, warningCount: 2 })
        );

        assert.match(display.tooltip, /TRLC parse scope: file:\/\/\/ws\/pkg/);
        assert.match(display.tooltip, /Parse mode: bazel/);
        assert.match(display.tooltip, /Verify \(CVC5\): off/);
        assert.match(display.tooltip, /1 error\(s\), 2 warning\(s\)/);
    });

    it("shows a warning icon and 'directory (<mode> fallback)' when a fallback happened", () => {
        const display = formatStatusBarDisplay(
            { active: false },
            status({
                parseMode: "bazel",
                fileCount: 4,
                fallbackReason: "No TRLC targets found in Bazel query",
            })
        );

        assert.strictEqual(
            display.text,
            "$(warning) TRLC: directory (bazel fallback) · 4 files"
        );
    });

    it("includes the fallback reason in the tooltip", () => {
        const display = formatStatusBarDisplay(
            { active: false },
            status({
                parseMode: "bazel",
                fallbackReason: "Bazel query failed: timed out",
            })
        );

        assert.match(
            display.tooltip,
            /Fell back to directory-mode parsing: Bazel query failed: timed out/
        );
    });

    it("does not mention a fallback in the tooltip when none happened", () => {
        const display = formatStatusBarDisplay({ active: false }, status());

        assert.doesNotMatch(display.tooltip, /Fell back/);
    });
});

describe("toScopeFileItems", () => {
    it("splits each path into a filename label and directory description", () => {
        const items = toScopeFileItems(["/ws/pkg/a.trlc"]);

        assert.deepStrictEqual(items, [
            { label: "a.trlc", description: "/ws/pkg", path: "/ws/pkg/a.trlc" },
        ]);
    });

    it("handles Windows-style backslash separators", () => {
        const items = toScopeFileItems(["C:\\ws\\pkg\\a.rsl"]);

        assert.deepStrictEqual(items, [
            { label: "a.rsl", description: "C:\\ws\\pkg", path: "C:\\ws\\pkg\\a.rsl" },
        ]);
    });

    it("treats a bare filename (no separator) as its own label with no description", () => {
        const items = toScopeFileItems(["a.trlc"]);

        assert.deepStrictEqual(items, [
            { label: "a.trlc", description: "", path: "a.trlc" },
        ]);
    });

    it("sorts by label so same-named files in different directories stay adjacent", () => {
        const items = toScopeFileItems([
            "/ws/b/a.trlc",
            "/ws/a/z.trlc",
            "/ws/a/a.trlc",
        ]);

        assert.deepStrictEqual(
            items.map((i) => i.label),
            ["a.trlc", "a.trlc", "z.trlc"]
        );
    });

    it("returns an empty list for an empty input", () => {
        assert.deepStrictEqual(toScopeFileItems([]), []);
    });
});
