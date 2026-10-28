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

/** Pure drag & drop rules of the preview (no DOM, unit-testable). */

import { ItemRef, PreviewNode } from "./previewProtocol";

function sameItem(node: PreviewNode, ref: ItemRef): boolean {
    return node.kind === "record"
        ? ref.kind === "record" && ref.name === node.name
        : ref.kind === "section" && ref.sectionId === node.sectionId;
}

/** Ids of the sections enclosing `sectionId`, nearest first. */
export function sectionAncestors(nodes: PreviewNode[], sectionId: number): number[] {
    const walk = (list: PreviewNode[], path: number[]): number[] | null => {
        for (const n of list) {
            if (n.kind !== "section") {
                continue;
            }
            if (n.sectionId === sectionId) {
                return path;
            }
            const found = walk(n.children, [n.sectionId, ...path]);
            if (found !== null) {
                return found;
            }
        }
        return null;
    };
    return walk(nodes, []) ?? [];
}

/** A section may not be dropped into itself or one of its descendants. */
export function canDrop(nodes: PreviewNode[], item: ItemRef, targetSectionId: number | null): boolean {
    if (item.kind === "record" || targetSectionId === null) {
        return true;
    }
    return targetSectionId !== item.sectionId && !sectionAncestors(nodes, targetSectionId).includes(item.sectionId);
}

/** Finds the sibling list holding `item` and the id of its section (null = file). */
function locate(
    list: PreviewNode[],
    item: ItemRef,
    parent: number | null,
): { siblings: PreviewNode[]; parent: number | null } | null {
    for (const n of list) {
        if (sameItem(n, item)) {
            return { siblings: list, parent };
        }
        if (n.kind === "section") {
            const found = locate(n.children, item, n.sectionId);
            if (found !== null) {
                return found;
            }
        }
    }
    return null;
}

/** True when the move would leave the item exactly where it already is. */
export function isNoOpMove(
    nodes: PreviewNode[],
    item: ItemRef,
    targetSectionId: number | null,
    before: ItemRef | null,
): boolean {
    const here = locate(nodes, item, null);
    if (here === null || here.parent !== targetSectionId) {
        return false;
    }
    const idx = here.siblings.findIndex((n) => sameItem(n, item));
    const next = here.siblings[idx + 1];
    if (before === null) {
        return next === undefined;
    }
    return sameItem(here.siblings[idx], before) || (next !== undefined && sameItem(next, before));
}
