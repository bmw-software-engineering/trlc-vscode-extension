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
        assert.ok(!html.includes('data-section-id="3"'));
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

    it("shell html carries CSP, root and script", () => {
        const html = previewShellHtml("N0nce", "vscode-resource:", "file:///s.js");
        assert.ok(
            html.includes(
                "default-src 'none'; style-src vscode-resource: 'unsafe-inline'; script-src 'nonce-N0nce'"
            )
        );
        assert.ok(html.includes('<div id="root"></div>'));
        assert.ok(html.includes('<script nonce="N0nce" src="file:///s.js"></script>'));
    });
});
