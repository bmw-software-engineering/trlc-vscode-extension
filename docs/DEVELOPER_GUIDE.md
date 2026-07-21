# Developer Guide

**Audience: Developer.** This document covers build/test/debug workflows and
assumes familiarity with the codebase. For internal design (why the code is
structured this way) see [ARCHITECTURE.md](ARCHITECTURE.md).

## Project Layout

- **Client** (`src/client/src/extension.ts`) — TypeScript, runs in the
  VSCode extension host.
- **Server** (`src/server/`) — Python, runs as a separate process over
  JSON-RPC/stdio.

| File | Purpose |
|------|---------|
| `src/client/src/extension.ts` | VSCode extension entry point |
| `src/server/language_server.py` | `TrlcLanguageServer` class, handler registration |
| `src/server/parse_engine.py` | Background parse thread, per-scope validation |
| `src/server/parse_context.py` | `ParseContext` dataclass, scope-id management |
| `src/server/context_store.py` | `ContextStore` — thread-safe scope/config storage |
| `src/server/scope_strategies.py` | Parse discovery strategies (WORKSPACE/REPO/DIRECTORY/BAZEL) |
| `src/server/bazel.py` | `bazel query` client, `BazelManagerCache` |
| `src/server/reference_resolver.py` | Cross-file find-references, shared by navigation/rename |
| `src/server/server_protocol.py` | Structural `ServerProtocol` type handlers accept |
| `src/server/handlers/` | LSP feature handlers (completion, hover, etc.) |
| `tests/unit/` | pytest unit tests |
| `tests/integration/` | pytest-lsp integration tests over a real server subprocess |
| `tests/e2e/` | golden-path smoke test of the packaged extension inside a real VSCode host |

## Building

Both artifacts build hermetically via Bazel — no local Node.js or Python
toolchain required (`MODULE.bazel` pins both):

```bash
# Package .vsix (ts_project type-check -> esbuild bundle -> vsce package)
bazel build //:vsix

# Standalone server wheel (rules_python's py_wheel)
bazel build //:wheel

# Install the built .vsix into VS Code
code --install-extension bazel-bin/trlc-vscode-extension.vsix
```

Lint/format run the same way — see `.bazelrc`'s `--config=lint` and
`bazel run //:format`.

## Running Tests

Every test suite — Python unit, Python integration, TypeScript
client — runs via Bazel, one `py_test`/`mocha_test` per test file (not one
per suite), aggregated under a single target so CI and local runs use the
exact same command:

```bash
# Everything, exactly like CI
bazel test //:test

# One file (target names are derived from the file path, e.g.
# tests/unit/handlers/test_lifecycle.py -> unit_handlers_test_lifecycle)
bazel test //tests:unit_test_parse_engine

# All Python tests (unit + integration), skipping the TS client suite
bazel test //tests:all_tests

# VSCode e2e smoke test - not part of //:test (see below), run explicitly
bazel test //tests/e2e:vscode_smoke_e2e --test_output=errors
```

`tests/integration/` spawns a real `python -m server --stdio` subprocess and
drives it through actual LSP requests (pytest-lsp)

## Debug Mode

1. Open the repo in VSCode.
2. `npm install` at root (local dev-loop tooling only; the Bazel build
   above doesn't need this).
3. **Run and Debug** view → select **Server + Client** → F5.

Launches a debug extension host with the server running under the Python
debugger.

## Common Tasks

### Add a new LSP feature/handler

1. Create `src/server/handlers/newfeature.py`:
   ```python
   def register(ls):
       @ls.feature(TEXT_DOCUMENT_SOME_REQUEST)
       def handler(params):
           context = ls.get_context_for_uri(params.textDocument.uri)
           if not context:
               return None  # or [] - not in any active scope
           # Scope-local logic using context.snapshot().vsm.stab
           return result
   ```
2. Import and call `register()` from `language_server.py`.
3. Add any new methods the handler calls on `ls` to the `ServerProtocol`
   structural type in `server_protocol.py` (type safety and clarity).
4. Write unit tests using the `FakeLanguageServer` fixture.

### Add a new setting

1. Add to `package.json` → `contributes.configuration`:
   ```json
   "trlcServer.newSetting": {
       "type": "string|boolean|number",
       "default": "...",
       "description": "..."
   }
   ```
   This `description` is what VSCode's Settings UI shows as the tooltip —
   it's the single source of truth for the user-facing wording, so keep it
   accurate and don't restate it differently elsewhere (see
   [USER_GUIDE.md](USER_GUIDE.md)'s settings table, which quotes it
   directly).
2. Add a field to `ServerConfig` in `server_config.py` (it's a frozen
   dataclass — a new field is just a new attribute with a default; list-like
   settings should be `Tuple[str, ...]`, not `List[str]`, to keep instances
   safely shareable across threads).
3. Add mapping in `ServerConfig.from_dict()` (handles both new and legacy
   keys).
4. Use the setting in a handler or strategy via `ls.config.new_setting` or
   `ls.get_config_for_uri(uri).new_setting`.

## Debugging Tips

### Trace server logs vs. log level

Two separate settings, easy to confuse:

- **`trlcServer.logLevel`** (`debug`/`info`/`warning`/`error`, default
  `warning`) — verbosity of TRLC's own diagnostic logging, on *both* sides.
  On the server it sets the root `logging` level (written to a rotating
  `pygls-<pid>.log` in the OS temp dir, PID-suffixed so concurrent server
  instances never collide on the same file — see `logging_config.py`) **and** forwards
  records at/above that level into the shared output channel via
  `window_log_message`. On the client it gates `log.ts`'s `info` lines the
  same way. One setting, one dropdown, both sides.
- **`trlcServer.trace.server`** (`off`/`messages`/`verbose`) — traces the
  raw LSP JSON-RPC wire protocol between VSCode and the server. Unrelated to
  application logging; only useful for diagnosing protocol-level issues
  (wrong params, missing capabilities), not parse/scope behavior.

Both write to the same **"[pygls] TrlcLanguageServer"** output channel
(`extension.ts` passes one shared `LogOutputChannel` into
`LanguageClientOptions.outputChannel`, which `vscode-languageclient` also
uses for wire trace when `traceOutputChannel` is left unset). Raising
`trlcServer.logLevel` to `debug` is almost always the first thing to try
when something looks wrong — it surfaces far more than the default
`warning` popups/log lines.

### Attaching a debugger to the server process

`src/server/__main__.py` accepts `--debugpy`: it lazily imports `debugpy`
(kept an optional, dev-only import — never required for end users),
calls `debugpy.connect(5678)`, then `debugpy.breakpoint()`, and waits for a
debugger before starting the transport. This is what the **"Python debug
server attached"** launch config (`.vscode/launch.json`) listens for — that
config puts the VSCode debug adapter in *listen* mode on port 5678, and
`debugpy.connect()` is the debuggee dialing in, not the other way round.
Combine with `--log-level debug` for maximum detail while stepping through:

```
python -m server --stdio --debugpy --log-level debug
```

### Inspect the symbol table

Inside a handler:
```python
context = ls.get_context_for_uri(uri)
if context:
    stab = context.snapshot().vsm.stab
    # stab._root is the root Requirement, dive into .children
```

### Verify scope boundaries

Check `ls.store.snapshot_contexts()` to see which scopes are active:
```python
for scope_id, context in ls.store.snapshot_contexts().items():
    print(f"Scope: {scope_id}")
    print(f"  Open files: {list(context.open_files.keys())}")
    print(f"  Mode: {context.parse_mode}")
```

### Understand why a scope didn't reparse

Each scope skips the expensive TRLC parse when its input content hasn't
changed and nothing in it was just edited — see `ParseEngine._scope_signatures`
and `Vscode_Source_Manager.compute_input_signature()`. If diagnostics look
stale after an edit that *should* have changed them, check:
- Is the edited file actually inside this scope's boundary (parseMode)?
- For DIRECTORY/BAZEL mode, was the file saved, or is it relying on the
  open-buffer content path (`_read_file()` in `scope_strategies.py`)?
- For BAZEL mode, is `ls.bazel_cache` (a `BazelManagerCache`) serving a
  stale cached `bazel query` result? It's cleared on any `"reparse"` event
  (config change, folder change, `parseAll`) — force one of those, or call
  `ls.bazel_cache.clear()` directly when debugging. A prior query failure is
  also cached (as a `BazelQueryError`) until the next clear — check the
  server log for the original error if a BAZEL-mode scope never resolves.

### Manually verify per-scope isolation

1. Open two same-named files from different directories simultaneously,
   e.g. `trlc/tests-system/arrays-1/example.rsl` and
   `trlc/api-examples/filename-check/example.rsl`.
2. Set `trlcServer.parseMode` to `directory`.
3. Both files should parse cleanly with no cross-scope "duplicate
   definition" diagnostics — each directory gets its own `ParseContext`.

### Debugging e2e failures

`tests/e2e/prepare.ts` writes a `.vscode/settings.json` into the temporary
workspace it builds, defaulting `trlcServer.logLevel` to `debug` — so every
e2e run already captures maximum server verbosity in `pygls-<pid>.log`
without any extra flag. On failure, `tests/e2e/runTest.ts`'s catch block
finds that log file (the most recently modified `pygls-*.log` in the OS
temp dir — there's only one VSCode window, and therefore one server
process, alive during this run) and prints its tail before exiting, so the
CI log for a failed e2e run includes the server's own diagnostic detail,
not just the mocha assertion message.

Known limitation: the **"[pygls] TrlcLanguageServer"** output channel
itself (client-side log lines + LSP wire trace) lives inside the separate
Extension Development Host process spawned by the e2e harness, and isn't
scraped by `runTest.ts` — only `pygls-<pid>.log` is. If a failure looks
client-side rather than server-side, reproduce interactively via **Debug
Mode** above instead.

### Debugging the installed extension from the command line

Extension code (`extension.ts`, `installer.ts`, `log.ts`, ...) always runs
in a separate Node.js **Extension Host** process — distinct from
Electron's renderer (the workbench UI). `Help > Toggle Developer Tools`
only reaches the renderer; it cannot see or break into extension code.
Since the Extension Host is a plain Node.js process, it speaks the same V8
Inspector Protocol as any other — fully drivable from a terminal, no
VSCode debug UI required.

1. **Build and install the real `.vsix`** (not a `--extensionDevelopmentPath`
   dev host — the actual packaged artifact):
   ```
   bazel build //:vsix
   code --install-extension bazel-bin/trlc-vscode-extension.vsix
   ```
2. **Turn on the inspector** for that instance's Extension Host:
   - Launching fresh: `code --inspect-extensions=9229`
   - Already running, no flag was passed: Node can enable the inspector on
     a live process via signal, no restart —
     ```
     ps aux | grep -i extensionHost   # find the Extension Host PID
     kill -USR1 <pid>
     ```
     opens the inspector on the default port (9229) in place.
3. **Attach and drive it — no GUI needed**:
   - `curl -s http://127.0.0.1:9229/json | python3 -m json.tool` lists
     debuggable targets and each one's `webSocketDebuggerUrl` (match by
     `title` if more than one Node process is listening).
   - Simplest: Node's built-in CLI debugger client attaches directly —
     ```
     node inspect 127.0.0.1:9229
     ```
     giving a `debug>` REPL: `cont`, `next`, `step`,
     `setBreakpoint('extension.js', <line>)`, `watch(expr)`, `repl` to
     evaluate expressions in the paused frame.
   - Scriptable/agent-driven: speak the Chrome DevTools Protocol directly
     over the `webSocketDebuggerUrl` websocket (`Debugger.enable`,
     `Debugger.setBreakpointByUrl`, `Runtime.evaluate`, listen for
     `Debugger.paused`) — lets a script (or an agent) set breakpoints and
     inspect state without a human driving a GUI at all.

**Source-map caveat**: the release bundle (`extension_bundle` in
`BUILD.bazel`) is minified with no source map, so breakpoints from the
steps above land in the minified `extension.js`, not the original `.ts`
files. `BUILD.bazel`'s `esbuild()` rule has a commented-out
`sourcemap = "linked"` line for exactly this — uncomment it and rebuild
`//:vsix` when you need TS-level breakpoints; leave it commented out
otherwise; it adds a `.js.map` to the shipped artifact.
