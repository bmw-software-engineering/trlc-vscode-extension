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

import { PreviewField, PreviewModel, PreviewNode } from "../previewProtocol";
import {
    escapeHtml,
    previewShellHtml,
    renderErrorBanner,
    renderNoDocument,
    renderArrayRow,
    renderPreview,
} from "../previewRender";

function field(overrides: Partial<PreviewField> = {}): PreviewField {
    return {
        name: "x",
        kind: "integer",
        value: "1",
        optional: false,
        editable: true,
        enumValues: null,
        elementKind: null,
        items: null,
        referenceType: null,
        markdown: false,
        ...overrides,
    };
}

function record(fields: PreviewField[], name = "R1"): PreviewNode {
    return { kind: "record", name, type: "Pkg.T", line: 4, fields };
}

function section(
    depth: number,
    title: string,
    children: PreviewNode[] = [],
    sectionId = 0,
    closed = true
): PreviewNode {
    return { kind: "section", sectionId, title, depth, closed, children };
}

function model(nodes: PreviewNode[] | null, overrides: Partial<PreviewModel> = {}): PreviewModel {
    return {
        uri: "file:///a.trlc",
        package: "Pkg",
        editable: true,
        reason: null,
        recordTypes: ["T"],
        nodes,
        version: 1,
        references: {},
        openFiles: [],
        ...overrides,
    };
}

describe("previewRender", () => {
    it("renders nested sections with one # per depth", () => {
        const html = renderPreview(model([section(3, "Inner")]), null);
        assert.ok(html.includes('<h3 class="section"><span class="hashes">###</span> Inner'));
    });

    it("caps heading level at h6 but keeps all hashes", () => {
        const html = renderPreview(model([section(8, "Deep")]), null);
        assert.ok(html.includes("<h6"));
        assert.ok(html.includes("########"));
        assert.ok(!html.includes("<h8"));
    });

    it("escapes titles and values", () => {
        const html = renderPreview(
            model([section(1, "<b>&", [record([field({ kind: "string", value: '"<i>"' })])])]),
            null
        );
        assert.ok(html.includes("&lt;b&gt;&amp;"));
        assert.ok(!html.includes("<b>"));
        assert.ok(html.includes("&quot;&lt;i&gt;&quot;"));
        assert.strictEqual(escapeHtml("<&>\"'"), "&lt;&amp;&gt;&quot;&#39;");
    });

    it("renders record card with name link and field attributes", () => {
        const html = renderPreview(model([record([field()])]), null);
        assert.ok(html.includes('<div class="record" data-record="R1">'));
        assert.ok(html.includes('class="record-name" data-line="4"'));
        assert.ok(html.includes('data-field="x"'));
        assert.ok(html.includes('data-kind="integer"'));
    });

    it("enum field renders a select with the current literal selected", () => {
        const html = renderPreview(
            model([record([field({ name: "e", kind: "enum", value: "B", enumValues: ["A", "B"] })])]),
            null
        );
        assert.ok(html.includes("<select"));
        assert.ok(html.includes('<option value="B" selected>B</option>'));
        assert.ok(html.includes('<option value="A">A</option>'));
    });

    it("array field renders one control per element and an add button", () => {
        const html = renderPreview(
            model([
                record([
                    field({ name: "tags", kind: "array", elementKind: "string", value: '["a", "<b>"]', items: ["a", "<b>"] }),
                ]),
            ]),
            null
        );
        assert.ok(html.includes('<div class="array-field" data-field="tags" data-kind="array">'));
        assert.ok(html.includes('class="array-value" value="a"'));
        assert.ok(html.includes('class="array-value" value="&lt;b&gt;"'));
        assert.strictEqual(html.split('class="array-remove"').length - 1, 2);
        assert.ok(html.includes('class="array-add"'));
    });

    it("array of enums renders a select per element", () => {
        const html = renderArrayRow({ elementKind: "enum", enumValues: ["A", "B"], referenceType: null }, "B", false, false);
        assert.ok(html.includes('<select class="array-value">'));
        assert.ok(html.includes('<option value="B" selected>B</option>'));
        const added = renderArrayRow({ elementKind: "boolean", enumValues: null, referenceType: null }, "", false, true);
        assert.ok(added.includes('data-added="1"'));
        assert.ok(added.includes('<option value="" selected></option>'));
        assert.ok(added.includes('<option value="true">true</option>'));
    });

    it("locked array field has disabled controls and no add/remove buttons", () => {
        const html = renderPreview(
            model([record([field({ kind: "array", elementKind: "integer", value: "[1]", items: ["1"], editable: false })])]),
            null
        );
        assert.ok(html.includes('class="array-value" value="1" disabled'));
        assert.ok(!html.includes("array-add"));
        assert.ok(!html.includes("array-remove"));
    });

    it("unset array field renders an empty list with an add button", () => {
        const html = renderPreview(
            model([record([field({ kind: "array", elementKind: "integer", value: null, items: null, optional: true })])]),
            null
        );
        assert.ok(!html.includes("array-value"));
        assert.ok(html.includes('class="array-add"'));
    });

    it("a locked model lists closed files to open, with an enabled button", () => {
        const html = renderPreview(
            model(null, {
                editable: false,
                reason: "not open",
                openFiles: [{ uri: "file:///w/sys/tsc.trlc", label: "sys/<tsc>.trlc", symbols: ["a", "b"] }],
            }),
            null
        );
        assert.ok(html.includes("<code>a</code>, <code>b</code>"));
        assert.ok(html.includes("sys/&lt;tsc&gt;.trlc"));
        assert.ok(html.includes('<button class="open-file" data-uri="file:///w/sys/tsc.trlc">Open &lt;tsc&gt;.trlc</button>'));
    });

    it("reference fields suggest the records of their type", () => {
        const html = renderPreview(
            model(
                [
                    record([
                        field({ name: "r", kind: "reference", value: null, referenceType: "P.A" }),
                        field({ name: "rs", kind: "array", elementKind: "reference", items: ["a1"], referenceType: "P.A" }),
                    ]),
                ],
                { references: { "P.A": ["P.a1", "b<1>"] } }
            ),
            null
        );
        assert.ok(html.includes('<datalist id="trlc-refs:P.A">'));
        assert.ok(html.includes('<option value="P.a1"></option><option value="b&lt;1&gt;"></option>'));
        assert.ok(html.includes('data-field="r" data-kind="reference" value="" list="trlc-refs:P.A"'));
        assert.ok(html.includes('class="array-value" value="a1" list="trlc-refs:P.A"'));
    });

    it("unset optional field shows empty input and no remove button", () => {
        const html = renderPreview(model([record([field({ optional: true, value: null })])]), null);
        assert.ok(html.includes('value=""'));
        assert.ok(!html.includes("remove"));
    });

    it("unset optional enum has an empty option selected", () => {
        const html = renderPreview(
            model([record([field({ kind: "enum", optional: true, value: null, enumValues: ["A"] })])]),
            null
        );
        assert.ok(html.includes('<option value="" selected></option>'));
    });

    it("set optional field has a remove button", () => {
        const html = renderPreview(model([record([field({ optional: true })])]), null);
        assert.ok(html.includes('<button class="remove"'));
    });

    it("frozen field is disabled and has no remove button", () => {
        const html = renderPreview(model([record([field({ optional: true, editable: false })])]), null);
        assert.ok(html.includes("disabled"));
        assert.ok(!html.includes("remove"));
    });

    it("non-editable model shows banner, disables inputs, and falls back to lastNodes", () => {
        const last = [record([field({ optional: true })])];
        const html = renderPreview(model(null, { editable: false, reason: "has <errors>" }), {
            uri: "file:///a.trlc",
            nodes: last,
        });
        assert.ok(html.includes('<div class="banner">has &lt;errors&gt;</div>'));
        assert.ok(html.includes('data-record="R1"'));
        assert.ok(html.includes("disabled"));
        assert.ok(!/<(input|button|select|textarea)(?![^>]*disabled)[^>]*>/.test(html));
    });

    it("add-record button per section and at file level", () => {
        const html = renderPreview(model([section(1, "S", [], 0)]), null);
        assert.ok(html.includes('<button class="add-record" data-section-id="0">'));
        assert.ok(html.includes('<button class="add-record" data-section-id="">'));
    });

    it("does not fall back to the last nodes of another document", () => {
        const last = { uri: "file:///other.trlc", nodes: [record([field()], "Foreign")] };
        const html = renderPreview(model(null, { editable: false, reason: "wait" }), last);
        assert.ok(!html.includes("Foreign"));
    });

    it("a section still open at EOF has no add-record button", () => {
        const html = renderPreview(model([section(1, "S", [], 3, false)]), null);
        assert.ok(!html.includes('class="add-record" data-section-id="3"'));
        assert.ok(!html.includes('class="add-section" data-section-id="3"'));
        assert.ok(html.includes('<button class="add-record" data-section-id="">'));
    });

    it("optional fields carry data-optional", () => {
        const html = renderPreview(model([record([field({ optional: true }), field({ name: "y" })])]), null);
        assert.ok(html.includes('data-field="x" data-kind="integer" data-optional="true"'));
        assert.ok(html.includes('data-field="y" data-kind="integer" value="1"'));
        assert.ok(!html.includes('data-field="y" data-kind="integer" data-optional'));
    });

    it("error banner and no-document placeholder are escaped", () => {
        assert.strictEqual(renderErrorBanner("<x>"), '<div class="banner error-banner">&lt;x&gt;</div>');
        assert.ok(renderNoDocument().includes("Open a .trlc file to preview it."));
    });

    it("renders a TOC with nested links", () => {
        const html = renderPreview(
            model([section(1, "A<", [section(2, "B", [], 1)], 0), section(1, "C", [], 2)]),
            null
        );
        assert.ok(html.startsWith('<nav class="toc"><button class="toc-toggle">'));
        assert.ok(
            html.includes(
                '<li><a class="toc-link" data-section-id="0">A&lt;</a><ul><li><a class="toc-link" data-section-id="1">B</a></li></ul></li>'
            )
        );
        assert.ok(html.includes('<li><a class="toc-link" data-section-id="2">C</a></li>'));
        assert.ok(html.includes('id="section-1" data-section-id="1"'));
        assert.ok(html.includes('<div class="section-body">'));
    });

    it("omits the TOC without sections", () => {
        assert.ok(!renderPreview(model([record([field()])]), null).includes("toc"));
    });

    it("marks required fields with *", () => {
        const html = renderPreview(
            model([record([field({ optional: true, name: "o" }), field({ name: "r" })])]),
            null
        );
        assert.ok(html.includes('<label>r <span class="required">*</span></label>'));
        assert.ok(html.includes("<label>o</label>"));
    });

    it("shows a hint per missing required field", () => {
        const html = renderPreview(
            model([
                record([
                    field({ name: "a", value: null }),
                    field({ name: "b", value: null, optional: true }),
                    field({ name: "c<", value: null }),
                ]),
            ]),
            null
        );
        assert.ok(html.includes('<div class="missing">Required field "a" is missing</div>'));
        assert.ok(html.includes('Required field "c&lt;" is missing'));
        assert.ok(!html.includes('Required field "b"'));
        assert.strictEqual((html.match(/class="missing"/g) ?? []).length, 2);
    });

    it("emits drop zones before children and at section end", () => {
        const html = renderPreview(
            model([section(1, "S", [record([field()], "R<")], 5), record([field()], "R2")]),
            null
        );
        assert.ok(
            html.includes(
                '<div class="drop-zone" data-target-section-id="" data-before-kind="section" data-before-id="5">'
            )
        );
        assert.ok(html.includes('data-target-section-id="5" data-before-kind="record" data-before-id="R&lt;"'));
        assert.ok(html.includes('data-target-section-id="5" data-before-kind="" data-before-id=""'));
        assert.ok(html.includes('data-target-section-id="" data-before-kind="record" data-before-id="R2"'));
        assert.ok(html.includes('data-target-section-id="" data-before-kind="" data-before-id=""'));
        const open = renderPreview(model([section(1, "S", [], 6, false)]), null);
        assert.ok(!open.includes('data-target-section-id="6"'));
    });

    it("emits drag handles only when editable", () => {
        const nodes = [section(1, "S", [record([field()])], 2)];
        const html = renderPreview(model(nodes), null);
        assert.ok(html.includes('draggable="true" data-drag-kind="section" data-drag-id="2"'));
        assert.ok(html.includes('draggable="true" data-drag-kind="record" data-drag-id="R1"'));
        const locked = renderPreview(model(nodes, { editable: false, reason: "r" }), null);
        assert.ok(locked.includes('data-drag-kind="record"'));
        assert.ok(!locked.includes("draggable"));
        assert.ok(!/<button(?![^>]*disabled)[^>]*>/.test(locked.replace(/<button class="toc-toggle">/g, "")));
    });

    it("renders markdown fields as md-field with escaped data-value", () => {
        const html = renderPreview(
            model([record([field({ kind: "string", markdown: true, optional: true, value: 'a "<b>"' })])]),
            null
        );
        assert.ok(
            html.includes(
                '<div class="md-field" data-field="x" data-kind="string" data-optional="true" data-markdown="1" data-value="a &quot;&lt;b&gt;&quot;"></div>'
            )
        );
        assert.ok(!html.includes("<textarea"));
    });

    it("emits add-section, rename and dissolve buttons", () => {
        const html = renderPreview(model([section(1, "S", [], 4)]), null);
        assert.ok(html.includes('<button class="add-section" data-section-id="4">+ Section</button>'));
        assert.ok(html.includes('<button class="add-section" data-section-id="">+ Section</button>'));
        assert.ok(html.includes('<button class="rename-section" data-section-id="4">'));
        assert.ok(html.includes('<button class="dissolve-section" data-section-id="4" title="Dissolve section">'));
    });

    it("shell html carries CSP, root and script", () => {
        const html = previewShellHtml("N0nce", "vscode-resource:", "file:///s.js");
        assert.ok(
            html.includes(
                "default-src 'none'; style-src vscode-resource: 'unsafe-inline'; script-src 'nonce-N0nce'"
            )
        );
        assert.ok(html.includes('<div id="root"></div>'));
        assert.ok(/\.toc \{[^}]*position: sticky; top: 0;/.test(html));
        assert.ok(html.includes(".dragging .drop-zone"));
        assert.ok(html.includes('<script nonce="N0nce" src="file:///s.js"></script>'));
    });

    it("caps the sticky TOC height and scrolls it", () => {
        const html = previewShellHtml("N0nce", "vscode-resource:", "file:///s.js");
        const toc = /\.toc \{([^}]*)\}/.exec(html)?.[1] ?? "";
        assert.ok(toc.includes("max-height: 35vh;"), toc);
        assert.ok(toc.includes("overflow-y: auto;"), toc);
    });
});
