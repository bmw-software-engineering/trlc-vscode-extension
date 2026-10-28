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
import * as sinon from "sinon";

import {
    BUSY_MESSAGE,
    editMatchesVersions,
    PreviewController,
    PreviewDeps,
    SWITCHED_MESSAGE,
} from "../previewController";
import { HostToWebview, PreviewModel, WebviewToHost } from "../previewProtocol";

const URI = "file:///a.trlc";
const MODEL: PreviewModel = {
    uri: URI,
    package: "P",
    editable: true,
    reason: null,
    recordTypes: ["T"],
    nodes: [],
    version: 1,
    references: {},
    openFiles: [],
};

function setup(): { deps: PreviewDeps & { sendRequest: sinon.SinonStub; applyEdit: sinon.SinonStub; showError: sinon.SinonStub; reveal: sinon.SinonStub; openFile: sinon.SinonStub }; post: sinon.SinonStub; ctl: PreviewController } {
    const deps = {
        sendRequest: sinon.stub(),
        applyEdit: sinon.stub().resolves(true),
        showError: sinon.stub(),
        reveal: sinon.stub(),
        openFile: sinon.stub().resolves(),
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

        await ctl.handleMessage({ type: "setField", uri: URI, record: "R", field: "f", value: "1" });

        sinon.assert.calledWith(deps.sendRequest, "trlc/previewSetField", { uri: URI, record: "R", field: "f", value: "1" });
        sinon.assert.calledWith(deps.applyEdit, { changes: {} });
    });

    it("setField error shows message and refreshes", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
        deps.sendRequest.withArgs("trlc/previewSetField").resolves({ error: "bad" });
        await ctl.setDocument(URI);
        post.resetHistory();

        await ctl.handleMessage({ type: "setField", uri: URI, record: "R", field: "f", value: null });

        sinon.assert.calledWith(deps.showError, "TRLC: bad");
        sinon.assert.calledWith(post, { type: "model", model: MODEL });
        sinon.assert.notCalled(deps.applyEdit);
    });

    it("setField error posts editRejected before the refresh", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
        deps.sendRequest.withArgs("trlc/previewSetField").resolves({ error: "bad" });
        await ctl.setDocument(URI);
        post.resetHistory();

        await ctl.handleMessage({ type: "setField", uri: URI, record: "R", field: "f", value: "keep me" });

        const rejected: HostToWebview = { type: "editRejected", record: "R", field: "f", value: "keep me", message: "TRLC: bad" };
        sinon.assert.calledWith(post, rejected);
        sinon.assert.callOrder(post.withArgs(rejected), post.withArgs({ type: "model", model: MODEL }));
    });

    it("editRejected is not posted for a non-setField error or after a document switch", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
        deps.sendRequest.withArgs("trlc/previewAddRecord").resolves({ error: "bad" });
        await ctl.setDocument(URI);
        await ctl.handleMessage({ type: "addRecord", uri: URI, modelVersion: 1, sectionId: null, recordType: "T", name: "N" });
        assert.ok(!post.getCalls().some((c) => c.args[0].type === "editRejected"));

        let resolveSet: (v: unknown) => void = () => undefined;
        deps.sendRequest.withArgs("trlc/previewSetField").returns(new Promise((r) => (resolveSet = r)));
        const pending = ctl.handleMessage({ type: "setField", uri: URI, record: "R", field: "f", value: "x" });
        await ctl.setDocument("file:///b.trlc");
        resolveSet({ error: "bad" });
        await pending;
        assert.ok(!post.getCalls().some((c) => c.args[0].type === "editRejected"));
    });

    const structure: Array<[WebviewToHost, string, object]> = [
        [{ type: "insertSection", uri: URI, modelVersion: 1, sectionId: null, title: "T" }, "trlc/previewInsertSection", { uri: URI, modelVersion: 1, sectionId: null, title: "T" }],
        [{ type: "renameSection", uri: URI, modelVersion: 1, sectionId: 3, title: "N" }, "trlc/previewRenameSection", { uri: URI, modelVersion: 1, sectionId: 3, title: "N" }],
        [{ type: "dissolveSection", uri: URI, modelVersion: 1, sectionId: 3 }, "trlc/previewDissolveSection", { uri: URI, modelVersion: 1, sectionId: 3 }],
        [
            { type: "move", uri: URI, modelVersion: 1, item: { kind: "record", name: "R" }, targetSectionId: null, before: { kind: "section", sectionId: 2 } },
            "trlc/previewMove",
            { uri: URI, modelVersion: 1, item: { kind: "record", name: "R" }, targetSectionId: null, before: { kind: "section", sectionId: 2 } },
        ],
    ];
    for (const [msg, method, params] of structure) {
        it(`${msg.type} sends ${method}`, async () => {
            const { deps, ctl } = setup();
            deps.sendRequest.resolves(MODEL);
            await ctl.setDocument(URI);
            deps.sendRequest.resolves({ edit: { e: 1 } });

            await ctl.handleMessage(msg);

            sinon.assert.calledWithExactly(deps.sendRequest, method, params);
            sinon.assert.calledWith(deps.applyEdit, { e: 1 });
        });
    }

    it("move is queued behind a pending setField", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
        await ctl.setDocument(URI);
        let resolveSet: (v: unknown) => void = () => undefined;
        deps.sendRequest.withArgs("trlc/previewSetField").returns(new Promise((r) => (resolveSet = r)));
        const move = deps.sendRequest.withArgs("trlc/previewMove").resolves({ edit: {} });

        const first = ctl.handleMessage({ type: "setField", uri: URI, record: "R", field: "f", value: "1" });
        const second = ctl.handleMessage({ type: "move", uri: URI, modelVersion: 1, item: { kind: "record", name: "R" }, targetSectionId: 1, before: null });
        await new Promise((r) => setImmediate(r));
        sinon.assert.notCalled(move);

        resolveSet({ edit: {} });
        await Promise.all([first, second]);
        sinon.assert.calledOnce(move);
        assert.ok(move.firstCall.calledAfter(deps.applyEdit.firstCall));
    });

    it("addRecord maps recordType to type param", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);
        deps.sendRequest.resolves({ edit: {} });

        await ctl.handleMessage({ type: "addRecord", uri: URI, modelVersion: 1, sectionId: 2, recordType: "T", name: "N" });

        sinon.assert.calledWith(deps.sendRequest, "trlc/previewAddRecord", { uri: URI, modelVersion: 1, sectionId: 2, type: "T", name: "N" });
    });

    it("applyEdit false shows error", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);
        deps.sendRequest.resolves({ edit: {} });
        deps.applyEdit.resolves(false);

        await ctl.handleMessage({ type: "setField", uri: URI, record: "R", field: "f", value: "1" });

        sinon.assert.calledWith(deps.showError, "TRLC: Could not apply edit.");
    });

    it("setDocument same uri does not re-request", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);
        await ctl.setDocument(URI);

        sinon.assert.calledOnce(deps.sendRequest);
    });

    it("no document -> ready posts noDocument, other messages are no-ops", async () => {
        const { deps, post, ctl } = setup();

        await ctl.handleMessage({ type: "ready" });
        await ctl.handleMessage({ type: "setField", uri: URI, record: "R", field: "f", value: "1" });
        await ctl.handleMessage({ type: "reveal", line: 3 });

        sinon.assert.notCalled(deps.sendRequest);
        sinon.assert.notCalled(deps.reveal);
        sinon.assert.calledOnceWithExactly(post, { type: "noDocument" });
    });

    it("openFile opens only a file the model on screen offered", async () => {
        const { deps, ctl } = setup();
        const far = "file:///far.trlc";
        deps.sendRequest.resolves({ ...MODEL, openFiles: [{ uri: far, label: "far.trlc", symbols: ["x"] }] });
        await ctl.setDocument(URI);

        await ctl.handleMessage({ type: "openFile", uri: "file:///elsewhere.trlc" });
        sinon.assert.notCalled(deps.openFile);
        await ctl.handleMessage({ type: "openFile", uri: far });
        sinon.assert.calledOnceWithExactly(deps.openFile, far, URI);

        deps.sendRequest.resolves(MODEL); // the next model offers nothing
        await ctl.refresh();
        await ctl.handleMessage({ type: "openFile", uri: far });
        sinon.assert.calledOnce(deps.openFile);
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

        await ctl.handleMessage({ type: "setField", uri: URI, record: "R", field: "f", value: "1" });

        sinon.assert.calledWith(deps.showError, "TRLC: No response from the language server.");
        sinon.assert.calledWith(post, { type: "model", model: MODEL });
    });

    it("rejecting applyEdit posts an error", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);
        deps.sendRequest.resolves({ edit: {} });
        deps.applyEdit.rejects(new Error("nope"));

        await ctl.handleMessage({ type: "setField", uri: URI, record: "R", field: "f", value: "1" });

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

    it("a refresh during an in-flight edit does not drop the edit", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
        await ctl.setDocument(URI);
        let resolveEdit: (r: unknown) => void = () => undefined;
        deps.sendRequest.withArgs("trlc/previewSetField").returns(new Promise((r) => (resolveEdit = r)));

        const pending = ctl.handleMessage({ type: "setField", uri: URI, record: "R", field: "f", value: "1" });
        await ctl.refresh();
        resolveEdit({ edit: { changes: {} } });
        await pending;

        sinon.assert.calledWith(deps.applyEdit, { changes: {} });
    });

    it("a stale edit rejection after a document switch posts nothing", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
        await ctl.setDocument(URI);
        let rejectEdit: (e: Error) => void = () => undefined;
        deps.sendRequest.withArgs("trlc/previewSetField").returns(new Promise((_, rej) => (rejectEdit = rej)));

        const pending = ctl.handleMessage({ type: "setField", uri: URI, record: "R", field: "f", value: "1" });
        await ctl.setDocument("file:///b.trlc");
        post.resetHistory();
        rejectEdit(new Error("late"));
        await pending;

        sinon.assert.notCalled(post);
        sinon.assert.notCalled(deps.applyEdit);
    });

    describe("edit queue", () => {
        const tick = (): Promise<void> => new Promise((r) => setImmediate(r));
        const set = (value: string): WebviewToHost => ({ type: "setField", uri: URI, record: "R", field: "f", value });

        async function ready(): Promise<ReturnType<typeof setup> & { edits: sinon.SinonStub; models: sinon.SinonStub }> {
            const s = setup();
            const models = s.deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
            const edits = s.deps.sendRequest.withArgs("trlc/previewSetField");
            await s.ctl.setDocument(URI);
            return { ...s, edits, models };
        }

        it("sends the second edit only after the first applyEdit resolved", async () => {
            const { deps, ctl, edits } = await ready();
            edits.callsFake(async (_m: string, p: { value: string }) => ({ edit: { v: p.value } }));
            let resolveApply: (ok: boolean) => void = () => undefined;
            deps.applyEdit.onFirstCall().returns(new Promise((r) => (resolveApply = r)));

            const first = ctl.handleMessage(set("a"));
            const second = ctl.handleMessage(set("b"));
            await tick();

            sinon.assert.calledOnce(edits);
            sinon.assert.calledOnce(deps.applyEdit);

            resolveApply(true);
            await Promise.all([first, second]);

            sinon.assert.calledTwice(edits);
            assert.strictEqual(edits.secondCall.args[1].value, "b");
            assert.ok(edits.secondCall.calledAfter(deps.applyEdit.firstCall));
            sinon.assert.calledWith(deps.applyEdit.secondCall, { v: "b" });
        });

        it("a failed edit does not block the next one", async () => {
            const { ctl, edits } = await ready();
            edits.onFirstCall().rejects(new Error("boom"));
            edits.onSecondCall().resolves({ edit: {} });

            await Promise.all([ctl.handleMessage(set("a")), ctl.handleMessage(set("b"))]);

            sinon.assert.calledTwice(edits);
        });

        it("a stale rejection waits silently and resends after the next editable model", async () => {
            const { deps, ctl, edits } = await ready();
            edits.onFirstCall().resolves({ error: "wait", code: "stale" });
            edits.onSecondCall().resolves({ edit: { ok: 1 } });

            const pending = ctl.handleMessage(set("a"));
            await tick();
            sinon.assert.calledOnce(edits);
            sinon.assert.notCalled(deps.showError);

            await ctl.refresh();
            await pending;

            sinon.assert.calledTwice(edits);
            sinon.assert.calledWith(deps.applyEdit, { ok: 1 });
            sinon.assert.notCalled(deps.showError);
        });

        it("a non-editable model does not trigger the resend", async () => {
            const { ctl, edits, models } = await ready();
            edits.onFirstCall().resolves({ error: "wait", code: "stale" });
            edits.onSecondCall().resolves({ edit: {} });

            const pending = ctl.handleMessage(set("a"));
            await tick();
            models.resolves({ ...MODEL, editable: false, reason: "Waiting" });
            await ctl.refresh();
            await tick();
            sinon.assert.calledOnce(edits);

            models.resolves(MODEL);
            await ctl.refresh();
            await pending;
            sinon.assert.calledTwice(edits);
        });

        it("a second stale rejection is shown as busy and may be committed again", async () => {
            const { deps, post, ctl, edits } = await ready();
            edits.resolves({ error: "wait", code: "stale" });

            const pending = ctl.handleMessage(set("a"));
            await tick();
            await ctl.refresh();
            await pending;

            sinon.assert.calledTwice(edits);
            sinon.assert.calledOnceWithExactly(deps.showError, BUSY_MESSAGE);
            sinon.assert.calledWith(post, {
                type: "editRejected",
                record: "R",
                field: "f",
                value: "a",
                message: BUSY_MESSAGE,
                retry: true,
            });
            sinon.assert.notCalled(deps.applyEdit);
        });

        it("a document switch ends a waiting stale edit with a busy message", async () => {
            const { deps, post, ctl, edits } = await ready();
            edits.resolves({ error: "wait", code: "stale" });

            const pending = ctl.handleMessage(set("a"));
            await tick();
            await ctl.setDocument("file:///b.trlc");
            await pending;

            sinon.assert.calledOnce(edits);
            sinon.assert.calledOnceWithExactly(deps.showError, BUSY_MESSAGE);
            assert.ok(!post.getCalls().some((c) => c.args[0].type === "editRejected"));
        });

        it("an edit queued behind a waiting stale edit is sent after the retry", async () => {
            const { ctl, edits } = await ready();
            edits.onFirstCall().resolves({ error: "wait", code: "stale" });
            edits.resolves({ edit: {} });

            const first = ctl.handleMessage(set("a"));
            const second = ctl.handleMessage(set("b"));
            await tick();
            sinon.assert.calledOnce(edits);

            await ctl.refresh();
            await Promise.all([first, second]);

            assert.deepStrictEqual(
                edits.getCalls().map((c) => c.args[1].value),
                ["a", "a", "b"]
            );
        });
    });
});

describe("PreviewController id-based edits", () => {
    const tick = (): Promise<void> => new Promise((r) => setImmediate(r));
    const B = "file:///b.trlc";
    const dissolve: WebviewToHost = { type: "dissolveSection", uri: URI, modelVersion: 1, sectionId: 3 };
    const idBased: WebviewToHost[] = [
        { type: "addRecord", uri: URI, modelVersion: 1, sectionId: null, recordType: "T", name: "N" },
        { type: "insertSection", uri: URI, modelVersion: 1, sectionId: null, title: "T" },
        { type: "renameSection", uri: URI, modelVersion: 1, sectionId: 3, title: "N" },
        dissolve,
        { type: "move", uri: URI, modelVersion: 1, item: { kind: "record", name: "R" }, targetSectionId: null, before: null },
    ];

    for (const code of ["stale", "outdated"]) {
        for (const msg of idBased) {
            it(`${msg.type} is not resent on "${code}": shown and refreshed`, async () => {
                const { deps, post, ctl } = setup();
                const models = deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
                await ctl.setDocument(URI);
                deps.sendRequest.callsFake(async (method: string) =>
                    method === "trlc/previewModel" ? MODEL : { error: "Nope.", code }
                );
                models.resetHistory();
                post.resetHistory();

                await ctl.handleMessage(msg);
                await ctl.refresh(); // an editable model would release a held edit
                await tick();

                const edits = deps.sendRequest.getCalls().filter((c) => c.args[0] !== "trlc/previewModel");
                assert.strictEqual(edits.length, 1);
                sinon.assert.calledOnceWithExactly(deps.showError, "TRLC: Nope.");
                sinon.assert.calledWith(post, { type: "model", model: MODEL });
                assert.ok(!post.getCalls().some((c) => c.args[0].type === "editRejected"));
                sinon.assert.notCalled(deps.applyEdit);
            });
        }
    }

    it("passes uri and modelVersion to the server", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(URI);
        deps.sendRequest.resolves({ edit: {} });

        await ctl.handleMessage({ ...dissolve, modelVersion: 7 } as WebviewToHost);

        sinon.assert.calledWithExactly(deps.sendRequest, "trlc/previewDissolveSection", {
            uri: URI,
            modelVersion: 7,
            sectionId: 3,
        });
    });

    it("an intent made from another document is cancelled", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(B);
        deps.sendRequest.resetHistory();

        for (const msg of idBased) {
            await ctl.handleMessage(msg);
        }

        sinon.assert.notCalled(deps.sendRequest);
        sinon.assert.callCount(deps.showError, idBased.length);
        sinon.assert.alwaysCalledWithExactly(deps.showError, SWITCHED_MESSAGE);
    });

    it("a failed applyEdit refreshes so the webview leaves its busy state", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
        deps.sendRequest.withArgs("trlc/previewDissolveSection").resolves({ edit: {} });
        deps.applyEdit.resolves(false);
        await ctl.setDocument(URI);
        post.resetHistory();

        await ctl.handleMessage(dissolve);

        sinon.assert.calledWith(deps.showError, "TRLC: Could not apply edit.");
        sinon.assert.calledWith(post, { type: "model", model: MODEL });
    });

    it("a refused intent of a document no longer shown is not refreshed", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
        let resolveEdit: (r: unknown) => void = () => undefined;
        deps.sendRequest.withArgs("trlc/previewDissolveSection").returns(new Promise((r) => (resolveEdit = r)));
        await ctl.setDocument(URI);

        const pending = ctl.handleMessage(dissolve);
        await ctl.setDocument(B);
        deps.sendRequest.resetHistory();
        post.resetHistory();
        resolveEdit({ error: "Nope.", code: "outdated" });
        await pending;

        sinon.assert.calledWith(deps.showError, "TRLC: Nope.");
        sinon.assert.notCalled(deps.sendRequest);
        sinon.assert.notCalled(post);
    });
});

describe("PreviewController setField after a document switch", () => {
    const B = "file:///b.trlc";
    const setA: WebviewToHost = { type: "setField", uri: URI, record: "R", field: "f", value: "1" };

    it("is sent to its own document and applied", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(B);
        deps.sendRequest.resetHistory();
        deps.sendRequest.resolves({ edit: { a: 1 } });

        await ctl.handleMessage(setA);

        sinon.assert.calledOnceWithExactly(deps.sendRequest, "trlc/previewSetField", {
            uri: URI,
            record: "R",
            field: "f",
            value: "1",
        });
        sinon.assert.calledWith(deps.applyEdit, { a: 1 });
    });

    it("an in-flight result is applied after the switch", async () => {
        const { deps, ctl } = setup();
        deps.sendRequest.withArgs("trlc/previewModel").resolves(MODEL);
        let resolveEdit: (r: unknown) => void = () => undefined;
        deps.sendRequest.withArgs("trlc/previewSetField").returns(new Promise((r) => (resolveEdit = r)));
        await ctl.setDocument(URI);

        const pending = ctl.handleMessage(setA);
        await ctl.setDocument(B);
        resolveEdit({ edit: { late: 1 } });
        await pending;

        sinon.assert.calledWith(deps.applyEdit, { late: 1 });
    });

    it("a refusal is shown without editRejected or refresh", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(B);
        deps.sendRequest.resetHistory();
        post.resetHistory();
        deps.sendRequest.resolves({ error: "bad" });

        await ctl.handleMessage(setA);

        sinon.assert.calledOnceWithExactly(deps.showError, "TRLC: bad");
        sinon.assert.calledOnce(deps.sendRequest); // no refresh
        sinon.assert.notCalled(post);
    });

    it("a stale rejection for a document no longer shown is not held", async () => {
        const { deps, post, ctl } = setup();
        deps.sendRequest.resolves(MODEL);
        await ctl.setDocument(B);
        deps.sendRequest.resetHistory();
        post.resetHistory();
        deps.sendRequest.resolves({ error: "wait", code: "stale" });

        await ctl.handleMessage(setA);

        sinon.assert.calledOnce(deps.sendRequest);
        sinon.assert.calledOnceWithExactly(deps.showError, BUSY_MESSAGE);
        sinon.assert.notCalled(post);
    });
});

describe("editMatchesVersions", () => {
    const edit = (version: number | null | undefined): unknown => ({
        documentChanges: [{ textDocument: { uri: URI, version }, edits: [] }],
    });
    const versionOf = (uri: string): number | undefined => (uri === URI ? 3 : undefined);

    it("accepts the open version and unversioned edits", () => {
        assert.ok(editMatchesVersions(edit(3), versionOf));
        assert.ok(editMatchesVersions(edit(null), versionOf));
        assert.ok(editMatchesVersions({ changes: {} }, versionOf));
    });

    it("rejects an edit for another version or a document that is not open", () => {
        assert.ok(!editMatchesVersions(edit(2), versionOf));
        assert.ok(!editMatchesVersions(edit(3), () => undefined));
    });
});
