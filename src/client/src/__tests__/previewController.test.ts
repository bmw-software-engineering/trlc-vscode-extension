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

import * as sinon from "sinon";

import { PreviewController, PreviewDeps } from "../previewController";
import { HostToWebview, PreviewModel } from "../previewProtocol";

const URI = "file:///a.trlc";
const MODEL: PreviewModel = {
    uri: URI,
    package: "P",
    editable: true,
    reason: null,
    recordTypes: ["T"],
    nodes: [],
};

function setup(): { deps: PreviewDeps & { sendRequest: sinon.SinonStub; applyEdit: sinon.SinonStub; showError: sinon.SinonStub; reveal: sinon.SinonStub }; post: sinon.SinonStub; ctl: PreviewController } {
    const deps = {
        sendRequest: sinon.stub(),
        applyEdit: sinon.stub().resolves(true),
        showError: sinon.stub(),
        reveal: sinon.stub(),
    };
    const post = sinon.stub<[HostToWebview], void>();
    const ctl = new PreviewController(deps as unknown as PreviewDeps, post);
    return { deps: deps as never, post, ctl };
}

describe("PreviewController", () => {
    it("refresh posts model", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);

        sinon.assert.calledWith(deps.sendRequest, "trlc/previewModel", { uri: URI });
        sinon.assert.calledWith(post, { type: "model", model: MODEL });
    });

    it("setField applies returned edit", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);
        deps.sendRequest.resetHistory();
        deps.sendRequest.resolves({ edit: { changes: {} } });

        await ctl.handleMessage({ type: "setField", record: "R", field: "f", value: "1" });

        sinon.assert.calledWith(deps.sendRequest, "trlc/previewSetField", { uri: URI, record: "R", field: "f", value: "1" });
        sinon.assert.calledWith(deps.applyEdit, { changes: {} });
    });

    it("setField error shows message and refreshes", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
        deps.sendRequest.withArgs("trlc/previewSetField").resolves({ error: "bad" });
        await ctl.setDocument(URI);
        post.resetHistory();

        await ctl.handleMessage({ type: "setField", record: "R", field: "f", value: null });

        sinon.assert.calledWith(deps.showError, "TRLC: bad");
        sinon.assert.calledWith(post, { type: "model", model: MODEL });
        sinon.assert.notCalled(deps.applyEdit);
    });

    it("addRecord maps recordType to type param", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);
        deps.sendRequest.resolves({ edit: {} });

        await ctl.handleMessage({ type: "addRecord", sectionId: 2, recordType: "T", name: "N" });

        sinon.assert.calledWith(deps.sendRequest, "trlc/previewAddRecord", { uri: URI, sectionId: 2, type: "T", name: "N" });
    });

    it("applyEdit false shows error", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);
        deps.sendRequest.resolves({ edit: {} });
        deps.applyEdit.resolves(false);

        await ctl.handleMessage({ type: "setField", record: "R", field: "f", value: "1" });

        sinon.assert.calledWith(deps.showError, "TRLC: Could not apply edit.");
    });

    it("setDocument same uri does not re-request", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);
        await ctl.setDocument(URI);

        sinon.assert.calledOnce(deps.sendRequest);
    });

    it("no document -> handleMessage is a no-op", async () => {
        const { deps, post, ctl } = setup();

        await ctl.handleMessage({ type: "ready" });
        await ctl.handleMessage({ type: "setField", record: "R", field: "f", value: "1" });
        await ctl.handleMessage({ type: "reveal", line: 3 });

        sinon.assert.notCalled(deps.sendRequest);
        sinon.assert.notCalled(deps.reveal);
        sinon.assert.notCalled(post);
    });

    it("reveal delegates to deps", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);

        await ctl.handleMessage({ type: "reveal", line: 7 });

        sinon.assert.calledWith(deps.reveal, URI, 7);
    });

    it("rejected request posts error", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.rejects(new Error("boom"));

        await ctl.setDocument(URI);

        sinon.assert.calledWith(post, { type: "error", message: "boom" });
    });

    it("drops a stale model when the document changed meanwhile", async () => {
        const { deps, post, ctl } = setup();
        const modelB = { ...MODEL, uri: "file:///b.trlc" };
        let resolveA: (m: PreviewModel) => void = () => undefined;
        deps.sendRequest.withArgs("trlc/previewModel", { uri: URI }).returns(new Promise((r) => (resolveA = r)));
        deps.sendRequest.withArgs("trlc/previewModel", { uri: modelB.uri }).resolves(modelB);

        const first = ctl.setDocument(URI);
        await ctl.setDocument(modelB.uri);
        resolveA(MODEL);
        await first;

        sinon.assert.calledOnce(post);
        sinon.assert.calledWith(post, { type: "model", model: modelB });
    });

    it("drops a stale model after the document is cleared", async () => {
        const { deps, post, ctl } = setup();
        let resolveA: (m: PreviewModel) => void = () => undefined;
        deps.sendRequest.returns(new Promise((r) => (resolveA = r)));

        const first = ctl.setDocument(URI);
        await ctl.setDocument(undefined);
        resolveA(MODEL);
        await first;

        sinon.assert.notCalled(post);
    });

    it("refresh with null response posts an error", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.resolves(null);

        await ctl.setDocument(URI);

        sinon.assert.calledOnceWithExactly(post, { type: "error", message: "TRLC: No preview data from the language server." });
    });

    it("edit with null response shows error and refreshes", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
        deps.sendRequest.withArgs("trlc/previewSetField").resolves(null);
        await ctl.setDocument(URI);
        post.resetHistory();

        await ctl.handleMessage({ type: "setField", record: "R", field: "f", value: "1" });

        sinon.assert.calledWith(deps.showError, "TRLC: No response from the language server.");
        sinon.assert.calledWith(post, { type: "model", model: MODEL });
    });

    it("rejecting applyEdit posts an error", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);
        deps.sendRequest.resolves({ edit: {} });
        deps.applyEdit.rejects(new Error("nope"));

        await ctl.handleMessage({ type: "setField", record: "R", field: "f", value: "1" });

        sinon.assert.calledWith(post, { type: "error", message: "nope" });
    });

    it("rejecting reveal posts an error instead of rejecting", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);
        deps.reveal.throws(new Error("gone"));

        await ctl.handleMessage({ type: "reveal", line: 1 });

        sinon.assert.calledWith(post, { type: "error", message: "gone" });
    });
});
