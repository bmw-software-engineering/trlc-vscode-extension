# Changelog

All notable changes to the TRLC VSCode Extension are documented here.

---

## [3.1.0] — 2026-03-11

### New Features

- **Standalone LSP server** — The Python language server has been extracted into an
  independently installable package (`trlc_lsp`). Any LSP-capable editor (Neovim,
  Emacs, Helix, …) can now use it directly: `pip install . && trlc-lsp`.
  See [`trlc_lsp/README.md`](trlc_lsp/README.md) for editor configuration examples.

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
