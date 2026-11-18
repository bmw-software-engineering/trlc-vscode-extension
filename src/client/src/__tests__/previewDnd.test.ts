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

import { canDrop, isNoOpMove, sectionAncestors } from "../previewDnd";
import { ItemRef, PreviewNode } from "../previewProtocol";

function rec(name: string): PreviewNode {
    return { kind: "record", name, type: "T", line: 0, fields: [] };
}

function sec(sectionId: number, children: PreviewNode[]): PreviewNode {
    return { kind: "section", sectionId, title: `S${sectionId}`, depth: 1, closed: true, children };
}

// file: r0, S1{ r1, S2{ S3{ r3 } }, S4{} }, r5
const nodes: PreviewNode[] = [
    rec("r0"),
    sec(1, [rec("r1"), sec(2, [sec(3, [rec("r3")])]), sec(4, [])]),
    rec("r5"),
];

const r = (name: string): ItemRef => ({ kind: "record", name });
const s = (sectionId: number): ItemRef => ({ kind: "section", sectionId });

describe("sectionAncestors", () => {
    it("lists ancestors nearest first", () => {
        assert.deepStrictEqual(sectionAncestors(nodes, 3), [2, 1]);
        assert.deepStrictEqual(sectionAncestors(nodes, 4), [1]);
    });

    it("is empty for a top-level or unknown section", () => {
        assert.deepStrictEqual(sectionAncestors(nodes, 1), []);
        assert.deepStrictEqual(sectionAncestors(nodes, 99), []);
    });
});

describe("canDrop", () => {
    it("allows a record anywhere", () => {
        assert.strictEqual(canDrop(nodes, r("r0"), null), true);
        assert.strictEqual(canDrop(nodes, r("r0"), 3), true);
    });

    it("rejects a section into itself or a descendant", () => {
        assert.strictEqual(canDrop(nodes, s(1), 1), false);
        assert.strictEqual(canDrop(nodes, s(1), 3), false);
        assert.strictEqual(canDrop(nodes, s(2), 3), false);
    });

    it("allows a section into a sibling, its parent or the file", () => {
        assert.strictEqual(canDrop(nodes, s(2), 4), true);
        assert.strictEqual(canDrop(nodes, s(2), 1), true);
        assert.strictEqual(canDrop(nodes, s(2), null), true);
    });
});

describe("isNoOpMove", () => {
    it("is a no-op before its own next sibling or itself", () => {
        assert.strictEqual(isNoOpMove(nodes, r("r1"), 1, s(2)), true);
        assert.strictEqual(isNoOpMove(nodes, r("r1"), 1, r("r1")), true);
        assert.strictEqual(isNoOpMove(nodes, r("r0"), null, s(1)), true);
    });

    it("is a no-op at the end when already last", () => {
        assert.strictEqual(isNoOpMove(nodes, r("r5"), null, null), true);
        assert.strictEqual(isNoOpMove(nodes, s(4), 1, null), true);
    });

    it("is a real move to another section or position", () => {
        assert.strictEqual(isNoOpMove(nodes, r("r1"), 4, null), false);
        assert.strictEqual(isNoOpMove(nodes, r("r0"), null, null), false);
        assert.strictEqual(isNoOpMove(nodes, r("r1"), 1, null), false);
    });
});
