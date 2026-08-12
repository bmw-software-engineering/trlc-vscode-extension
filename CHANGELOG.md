# Changelog

All notable changes to the TRLC VSCode Extension are documented here.

---

## [4.0.0] - 2026-08-12

### New Features

- **Bazel-aware parsing** — New `bazel` parse mode scopes a file to its owning Bazel
  target's full transitive dependency closure (via `bazel query`), including
  cross-repository `spec`/`deps` targets. Falls back to directory mode when no Bazel
  workspace/target is found, with status-bar indication when that happens.
- **Per-scope symbol table isolation** — Each parse scope (workspace/directory/repo/Bazel
  target) now gets its own isolated symbol table, eliminating spurious "duplicate
  definition" diagnostics between same-named files in different directories.
- **Status bar item** — Shows live parse progress, the active file's parse mode, and
  its scope's file count; click to browse and open every file currently in scope.
- **`trlcServer.logLevel: debug`** now also enables wire-level tracing, with a stable
  trace client ID so logs persist across language server restarts.
- **Config changes apply immediately** — Changing `trlcServer.parseMode`,
  `trlcServer.bazel.ruleClasses`, etc. now re-scopes and reparses open files right away,
  no window reload needed.
- **External file changes trigger a reparse** — Files changed outside the editor
  (`git checkout`, other tools) are now picked up via `workspace/didChangeWatchedFiles`
  instead of being silently ignored.
- **Python 3.10–3.13 compatibility** — the server is now tested against all four
  versions, each with its own pinned `requirements_dev_3_1x.txt` lockfile.
- **Bazel build system** — build, lint, format, and test now run through Bazel
  (`MODULE.bazel`/`BUILD.bazel`), replacing the old `Makefile`-based setup.
- **CI split into reusable workflows** — Python tests, TS tests, format/lint, copyright check and
  e2e tests each run as their own reusable workflow, matrixed across Python
  versions and reused by both `build.yml` and the new `publish.yml`.
- **Integration and e2e test coverage added** — `tests/standalone/` drives the real
  LSP server process end-to-end via `pytest-lsp` (no mocking) across diagnostics,
  completion, navigation, rename, code actions, semantic tokens, and Bazel mode;
  `tests/e2e/` adds a VS Code extension smoke test.

### Breaking Changes

- **`trlcServer.scope` and "TRLC: Select Workspace Folder" removed** — superseded by
  per-folder/directory/Bazel scoping, which now isolates parsing per workspace folder,
  directory, or Bazel target on its own.

### Bug Fixes

- **Go to Definition (Ctrl+click/F12) fixed and hardened** — was never registered for
  plain `textDocument/definition` (only "Go to Type Definition"); now also resolves
  and navigates cross-package references that TRLC itself left unresolved due to an
  unrelated parse error elsewhere in the file, instead of crashing or doing nothing.
- **`Package.` autocomplete improved** — now suggests record instances (not just
  types), filters candidates to the target field's declared type, and keeps working
  while the file has a syntax error anywhere in it (including mid-edit).

---

## [3.2.0] — 2026-07-14

- Fix support for VSCode v1.105 till latest release

## [3.1.0] — 2026-03-11

### New Features

- **Standalone LSP server** — The Python language server has been extracted into an
  independently installable package (`server`, distributed as `trlc-lsp`). Any LSP-capable editor (Neovim,
  Emacs, Helix, …) can now use it directly: `pip install . && trlc-lsp`.
  See [`docs/LSP_SERVER_GUIDE.md`](docs/LSP_SERVER_GUIDE.md) for editor configuration examples.

- **`pyproject.toml`** — The server package now ships a `pyproject.toml` with a
  declared `trlc-lsp` console-script entry point and pinned dependency versions.

- **Configurable formal verification** — A new setting `trlcServer.verify` (boolean,
  default `true`) controls whether CVC5 formal verification is enabled. Previously
  this was always on and could not be turned off without modifying source code.

### Bug Fixes

- **Semantic tokens column bug** — Column positions for operators on the same line
  were calculated relative to the previous token instead of the line start, causing
  shifted highlighting.

- **Semantic tokens double-lexing** — The semantic tokens handler was re-lexing every
  file from scratch; it now reuses the already-parsed token stream from the last
  full parse, with a lex-only fallback for files not yet parsed.

- **Stale diagnostics not cleared** — Old diagnostics were sometimes not removed when
  a file was fixed, because clearing was gated on `workspace.documents` being
  non-empty. Clearing now always happens unconditionally.

- **Config fetched on every keystroke** — `workspace/configuration` was requested
  inside `did_change`, which fires for every edit. Configuration is now fetched once
  on `did_open` and again only when the user changes settings
  (`workspace/didChangeConfiguration`).

- **Thread safety** — `symbols` and `all_files` (read by LSP request handlers,
  written by the background parser thread) were accessed without a lock.
  A `threading.Lock` (`data_lock`) now guards all access.

- **Debounce missing** — The background parser triggered immediately on every
  keystroke, causing redundant full parses. A 300 ms debounce is now applied before
  parsing starts.

- **Wrong activation event** — The extension activated on every VS Code startup
  (`onStartupFinished`) instead of only when a TRLC file was opened. Changed to
  `onLanguage:TRLC`.

- **Command injection via `exec`** — `installPythonPackage` used `child_process.exec`
  with a shell-interpolated command string. Replaced with `execFile` and an explicit
  argument array.

### Dependency Changes

- All Python runtime dependencies (`lsprotocol`, `pygls`, `trlc`) are now installed
  automatically into the extension's `python-deps/` directory on first activation.

- `lsprotocol` is now required at `>=2025.0.0`, matching the `pygls==2.1.1` API.

- `cvc5` is no longer installed as a separate step — it is pulled in transitively
  by `trlc` via `PyVCG`.

- `python-deps/` lookup in `__main__.py` now uses a `__file__`-relative path
  instead of `os.getcwd()`

---

## [3.0.4] — 2025-12 (previous release)

- Updated TRLC version requirement to `>=2.0.1`.

## [3.0.3] and earlier

See the [GitHub releases page](https://github.com/bmw-software-engineering/trlc-vscode-extension/releases)
for earlier release notes.
