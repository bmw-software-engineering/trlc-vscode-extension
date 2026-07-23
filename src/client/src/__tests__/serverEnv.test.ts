/* -------------------------------------------------------------------------
 * TRLC VSCode Extension
 * Copyright (C) 2023 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
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
import * as path from "path";

import { buildServerEnv, MissingInterpreterError, resolveInterpreter } from "../serverEnv";

describe("buildServerEnv", () => {
    it("sets PYTHONPATH to the bundled deps path when none is set", () => {
        // Given: an environment with no PYTHONPATH
        const baseEnv = { FOO: "bar" };

        // When: building the server env
        const env = buildServerEnv("/ext/python-deps", baseEnv);

        // Then: PYTHONPATH is the bundled deps path, other vars untouched
        assert.strictEqual(env.PYTHONPATH, "/ext/python-deps");
        assert.strictEqual(env.FOO, "bar");
    });

    it("prepends the bundled deps path to an existing PYTHONPATH", () => {
        // Given: a user environment with its own PYTHONPATH already set
        const baseEnv = { PYTHONPATH: "/usr/local/lib/python-site" };

        // When: building the server env
        const env = buildServerEnv("/ext/python-deps", baseEnv);

        // Then: bundled deps come first, user's PYTHONPATH is preserved, not
        // overwritten (this is the fix for the PYTHONPATH-clobber bug).
        assert.strictEqual(
            env.PYTHONPATH,
            `/ext/python-deps${path.delimiter}/usr/local/lib/python-site`
        );
    });

    it("does not mutate the passed-in baseEnv object", () => {
        // Given: a base environment object
        const baseEnv = { PYTHONPATH: "/existing" };

        // When: building a server env from it
        buildServerEnv("/ext/python-deps", baseEnv);

        // Then: the original object is untouched (caller may reuse it)
        assert.strictEqual(baseEnv.PYTHONPATH, "/existing");
    });
});

describe("resolveInterpreter", () => {
    it("returns the configured interpreter path unchanged", () => {
        // Given/When: a configured interpreter path
        // Then: it is returned as-is
        assert.strictEqual(
            resolveInterpreter("/usr/bin/python3"),
            "/usr/bin/python3"
        );
    });

    it("throws MissingInterpreterError when the setting is undefined", () => {
        // Given/When/Then: no configured value throws the typed error
        assert.throws(
            () => resolveInterpreter(undefined),
            MissingInterpreterError
        );
    });

    it("throws MissingInterpreterError when the setting is an empty string", () => {
        // Given/When/Then: an empty string is treated the same as unset
        assert.throws(() => resolveInterpreter(""), MissingInterpreterError);
    });
});
