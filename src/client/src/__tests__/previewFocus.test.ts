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

import * as assert from "assert";

import { focusLeavesPreview, selectCommitValue, shouldRenderNow, textCommitValue } from "../previewFocus";

describe("previewFocus", () => {
    it("renders only when no input is focused and no add form is open", () => {
        assert.strictEqual(shouldRenderNow({ inputFocused: false, addFormOpen: false }), true);
        assert.strictEqual(shouldRenderNow({ inputFocused: true, addFormOpen: false }), false);
        assert.strictEqual(shouldRenderNow({ inputFocused: false, addFormOpen: true }), false);
    });

    it("does not flush when focus moves within the preview", () => {
        assert.strictEqual(focusLeavesPreview(true), false);
        assert.strictEqual(focusLeavesPreview(false), true);
    });

    it("maps select commits", () => {
        assert.strictEqual(selectCommitValue("a", "b"), "a");
        assert.strictEqual(selectCommitValue("", "b"), null);
        assert.strictEqual(selectCommitValue("", ""), undefined);
    });
});

describe("textCommitValue", () => {
    it("clearing an optional non-string field removes it", () => {
        assert.strictEqual(textCommitValue("", "integer", true), null);
        assert.strictEqual(textCommitValue("  ", "decimal", true), null);
        assert.strictEqual(textCommitValue("", "reference", true), null);
        assert.strictEqual(textCommitValue("", "other", true), null);
    });

    it("clearing an optional string removes it, whitespace is a value", () => {
        assert.strictEqual(textCommitValue("", "string", true), null);
        assert.strictEqual(textCommitValue(" ", "string", true), " ");
    });

    it("required fields post the text as-is", () => {
        assert.strictEqual(textCommitValue("", "integer", false), "");
        assert.strictEqual(textCommitValue("", "string", false), "");
    });

    it("non-empty input is posted unchanged", () => {
        assert.strictEqual(textCommitValue(" 5 ", "integer", true), " 5 ");
    });
});
