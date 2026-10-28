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
    markdownChanged,
    markdownCommitValue,
    markdownFieldKey,
    normalizeMarkdown,
    planMarkdownReuse,
    rejectionApplies,
} from "../previewMarkdown";

describe("previewMarkdown", () => {
    describe("normalizeMarkdown", () => {
        it("converts CRLF and CR to LF", () => {
            assert.strictEqual(normalizeMarkdown("a\r\nb\rc"), "a\nb\nc");
        });

        it("trims trailing whitespace on each line", () => {
            assert.strictEqual(normalizeMarkdown("a  \nb\t\n  c "), "a\nb\n  c");
        });

        it("drops leading and trailing blank lines but keeps inner ones", () => {
            assert.strictEqual(normalizeMarkdown("\n  \n- a\n\n- b\n \n\n"), "- a\n\n- b");
        });

        it("keeps leading indentation of the first content line", () => {
            assert.strictEqual(normalizeMarkdown("\n    code\n"), "    code");
        });

        it("maps whitespace-only text to the empty string", () => {
            assert.strictEqual(normalizeMarkdown(" \n\t\n"), "");
            assert.strictEqual(normalizeMarkdown(""), "");
        });
    });

    describe("markdownChanged", () => {
        it("ignores whitespace-only differences the server normalises away", () => {
            assert.strictEqual(markdownChanged("a\r\nb", "a  \nb\n"), false);
            assert.strictEqual(markdownChanged("text", "\ntext\n\n"), false);
        });

        it("reports real changes", () => {
            assert.strictEqual(markdownChanged("a", "b"), true);
            assert.strictEqual(markdownChanged("- a\n  - b", "- a\n- b"), true);
        });
    });

    describe("markdownCommitValue", () => {
        it("posts nothing when the text is unchanged", () => {
            assert.strictEqual(markdownCommitValue("**x**\n", "**x**", false), undefined);
        });

        it("posts the normalised text when changed", () => {
            assert.strictEqual(markdownCommitValue("a  \r\nb\n", "a", false), "a\nb");
        });

        it("clears an optional field emptied by the user", () => {
            assert.strictEqual(markdownCommitValue("\n", "old", true), null);
        });

        it("posts the empty string for a required field and lets the server decide", () => {
            assert.strictEqual(markdownCommitValue("", "old", false), "");
        });

        it("posts nothing when an already empty field stays empty", () => {
            assert.strictEqual(markdownCommitValue("\n", "", true), undefined);
        });
    });

    describe("rejectionApplies", () => {
        const rejection = { uri: "file:///a.trlc", record: "r1", field: "description", serverValue: "old" };

        it("applies to the same field while the server value is unchanged", () => {
            assert.strictEqual(rejectionApplies(rejection, "file:///a.trlc", "r1", "description", "old"), true);
            assert.strictEqual(rejectionApplies(rejection, "file:///a.trlc", "r1", "description", "old\n"), true);
        });

        it("does not apply to other fields, records or documents", () => {
            assert.strictEqual(rejectionApplies(rejection, "file:///a.trlc", "r1", "other", "old"), false);
            assert.strictEqual(rejectionApplies(rejection, "file:///a.trlc", "r2", "description", "old"), false);
            assert.strictEqual(rejectionApplies(rejection, "file:///b.trlc", "r1", "description", "old"), false);
        });

        it("stops applying once the server value changed", () => {
            assert.strictEqual(rejectionApplies(rejection, "file:///a.trlc", "r1", "description", "new"), false);
        });
    });

    describe("markdownFieldKey", () => {
        const base = { uri: "file:///a.trlc", record: "r", field: "f", value: "x", editable: true, optional: false };

        it("is equal for an unchanged field", () => {
            assert.strictEqual(markdownFieldKey(base), markdownFieldKey({ ...base }));
        });

        it("changes with document, record, field, value, editability and optionality", () => {
            const changes = [
                { uri: "file:///b.trlc" },
                { record: "s" },
                { field: "g" },
                { value: "y" },
                { editable: false },
                { optional: true },
            ];
            for (const c of changes) {
                assert.notStrictEqual(markdownFieldKey({ ...base, ...c }), markdownFieldKey(base), JSON.stringify(c));
            }
        });

        it("does not collide when parts contain separators", () => {
            assert.notStrictEqual(
                markdownFieldKey({ ...base, record: "a,b", field: "c" }),
                markdownFieldKey({ ...base, record: "a", field: "b,c" })
            );
        });
    });

    describe("planMarkdownReuse", () => {
        it("keeps unchanged editors, mounts new fields and destroys the rest", () => {
            const plan = planMarkdownReuse(
                [
                    { key: "a", reusable: true },
                    { key: "b", reusable: true },
                    { key: "c", reusable: true },
                ],
                ["c", "new", "a"]
            );
            assert.deepStrictEqual(plan.reuse, [2, null, 0]);
            assert.deepStrictEqual(plan.destroy, [1]);
        });

        it("never reuses a dirty editor", () => {
            const plan = planMarkdownReuse([{ key: "a", reusable: false }], ["a"]);
            assert.deepStrictEqual(plan.reuse, [null]);
            assert.deepStrictEqual(plan.destroy, [0]);
        });

        it("hands each editor to one field only", () => {
            const plan = planMarkdownReuse([{ key: "a", reusable: true }], ["a", "a"]);
            assert.deepStrictEqual(plan.reuse, [0, null]);
            assert.deepStrictEqual(plan.destroy, []);
        });

        it("with nothing mounted mounts everything", () => {
            assert.deepStrictEqual(planMarkdownReuse([], ["a", "b"]), { reuse: [null, null], destroy: [] });
        });
    });
});
