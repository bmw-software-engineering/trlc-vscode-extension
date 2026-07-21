// Points ts-node at tsconfig.test.json (adds "mocha" ambient types) instead
// of the production tsconfig.json, so client test compilation stays
// isolated from the ts_project/esbuild Bazel targets that consume the
// production config directly.
"use strict";

process.env.TS_NODE_PROJECT = require("path").join(__dirname, "tsconfig.test.json");
require("ts-node/register");
