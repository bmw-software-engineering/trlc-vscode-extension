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

import {
    focusLeavesPreview,
    intentModelVersion,
    isIdBasedIntent,
    selectCommitValue,
    shouldRenderNow,
    textCommitValue,
    arrayCommitValue,
} from "../previewFocus";
import { PreviewModel, PreviewNode } from "../previewProtocol";

describe("previewFocus", () => {
    it("renders only when no input is focused and no add form is open", () => {
        assert.strictEqual(shouldRenderNow({ inputFocused: false, addFormOpen: false, dragging: false, pressing: false }), true);
        assert.strictEqual(shouldRenderNow({ inputFocused: true, addFormOpen: false, dragging: false, pressing: false }), false);
        assert.strictEqual(shouldRenderNow({ inputFocused: false, addFormOpen: true, dragging: false, pressing: false }), false);
    });

    it("does not render while dragging", () => {
        assert.strictEqual(shouldRenderNow({ inputFocused: false, addFormOpen: false, dragging: true, pressing: false }), false);
    });

    it("does not render while a drag handle is pressed", () => {
        assert.strictEqual(
            shouldRenderNow({ inputFocused: false, addFormOpen: false, dragging: false, pressing: true }),
            false
        );
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

describe("arrayCommitValue", () => {
    const row = (value: string, added = false) => ({ value, added });

    it("posts the elements in order", () => {
        assert.deepStrictEqual(arrayCommitValue([row("a"), row("b", true)], ["a"]), ["a", "b"]);
    });

    it("posts nothing when the list is unchanged", () => {
        assert.strictEqual(arrayCommitValue([row("a"), row("b")], ["a", "b"]), undefined);
        assert.strictEqual(arrayCommitValue([], []), undefined);
    });

    it("ignores added rows left empty, keeps existing empty elements", () => {
        assert.strictEqual(arrayCommitValue([row("a"), row(" ", true)], ["a"]), undefined);
        assert.deepStrictEqual(arrayCommitValue([row(""), row("b", true)], [""]), ["", "b"]);
    });

    it("posts the shorter list after a removal, down to an empty list", () => {
        assert.deepStrictEqual(arrayCommitValue([row("b")], ["a", "b"]), ["b"]);
        assert.deepStrictEqual(arrayCommitValue([], ["a"]), []);
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

    it("id-based intents are add record and the structure edits", () => {
        for (const type of ["addRecord", "insertSection", "renameSection", "dissolveSection", "move"]) {
            assert.strictEqual(isIdBasedIntent({ type }), true, type);
        }
        for (const type of ["setField", "reveal", "ready"]) {
            assert.strictEqual(isIdBasedIntent({ type }), false, type);
        }
    });

    describe("intentModelVersion", () => {
        const rec = (name: string, value = "1"): PreviewNode => ({
            kind: "record",
            name,
            type: "T",
            line: 0,
            fields: [{ name: "x", kind: "integer", value, optional: false, editable: true, enumValues: null, elementKind: null, items: null, markdown: false }],
        });
        const sec = (sectionId: number, title: string, children: PreviewNode[]): PreviewNode => ({
            kind: "section",
            sectionId,
            title,
            depth: 1,
            closed: true,
            children,
        });
        const model = (version: number, nodes: PreviewNode[] | null, extra: Partial<PreviewModel> = {}): PreviewModel => ({
            uri: "file:///a.trlc",
            package: "P",
            editable: true,
            reason: null,
            recordTypes: ["T"],
            nodes,
            version,
            ...extra,
        });
        const shown = model(3, [sec(0, "A", [rec("r")]), sec(1, "B", [])]);

        it("is the rendered model's version without a held model", () => {
            assert.strictEqual(intentModelVersion(shown, null), 3);
        });

        it("takes a held model's version when only values changed", () => {
            const held = model(4, [sec(0, "A", [rec("r", "2")]), sec(1, "B", [])]);
            assert.strictEqual(intentModelVersion(shown, held), 4);
        });

        it("keeps the rendered version when the layout changed", () => {
            const variants = [
                [sec(0, "B", []), sec(1, "A", [rec("r")])],
                [sec(0, "A", []), sec(1, "B", [rec("r")])],
                [sec(0, "A", [rec("r"), rec("s")]), sec(1, "B", [])],
                [sec(0, "A2", [rec("r")]), sec(1, "B", [])],
            ];
            for (const nodes of variants) {
                assert.strictEqual(intentModelVersion(shown, model(4, nodes)), 3);
            }
        });

        it("keeps the rendered version for another document, a locked or unavailable model", () => {
            const nodes = shown.nodes;
            assert.strictEqual(intentModelVersion(shown, model(4, nodes, { uri: "file:///b.trlc" })), 3);
            assert.strictEqual(intentModelVersion(shown, model(4, nodes, { editable: false })), 3);
            assert.strictEqual(intentModelVersion(shown, model(4, null)), 3);
        });
    });
});
