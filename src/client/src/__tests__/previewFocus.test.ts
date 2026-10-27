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

import { focusLeavesPreview, selectCommitValue, shouldRenderNow } from "../previewFocus";

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
