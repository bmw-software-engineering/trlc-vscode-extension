# Architecture

This document describes the internal structure of the TRLC VSCode Extension and its bundled language server (`trlc_lsp`).

---

## Overview

The extension follows the standard
[Language Server Protocol (LSP)](https://microsoft.github.io/language-server-protocol/)
client/server split:

```
VS Code (editor)
  └── Extension host process  (Node.js / TypeScript)
        client/src/extension.ts     ← LSP client
              │  JSON-RPC over stdio
              ▼
        Language server process  (Python)
              trlc_lsp/__main__.py  ← entry point
              trlc_lsp/server.py    ← LSP handlers
              trlc_lsp/trlc_utils.py ← TRLC integration
```

The client is responsible for lifecycle management (starting/stopping the
server, installing Python dependencies) and for forwarding editor events to the server. All TRLC-specific logic lives in the Python server process.

---

## Repository layout

```
trlc-vscode-extension/
│
├── client/src/extension.ts       VS Code extension entry point (TypeScript)
│
├── trlc_lsp/                     Standalone Python LSP server package
│   ├── __init__.py
│   ├── __main__.py               CLI entry point / transport selection
│   ├── server.py                 All LSP feature handlers
│   └── trlc_utils.py             Bridges pygls ↔ TRLC library
│
├── pyproject.toml                Makes trlc_lsp pip-installable
├── package.json                  VSCode extension manifest
└── trlc-grammar.json             TextMate grammar for syntax highlighting
```

---

## Client (`client/src/extension.ts`)

### Responsibilities

| Task | How |
|---|---|
| Start the server process | `startLangServer()` spawns Python with `-m trlc_lsp` |
| Install Python dependencies | `setup()` runs `pip install --target python-deps/` on first activation |
| Expose VS Code commands | `extension.resetState`, forwarded from `package.json` |

### Activation

The extension activates on `onLanguage:TRLC` — only when the user opens a
`.rsl` or `.trlc` file. This avoids unnecessary Python process startup.

### Dependency installation

On first activation `setup()` installs three packages into
`<extensionDir>/python-deps/` using `execFile` (no shell):

- `lsprotocol>=2025.0.0`
- `pygls==2.1.1`
- `trlc>=2.0.4`

`cvc5` is pulled in transitively by `trlc` via `PyVCG`.

---

## Language server (`trlc_lsp/server.py`)

### Key classes

#### `TrlcLanguageServer(LanguageServer)`

Subclasses `pygls.lsp.server.LanguageServer`. Owns all mutable server state:

| Attribute | Type | Purpose |
|---|---|---|
| `fh` | `File_Handler` | In-memory map of open file URIs → content |
| `symbols` | `trlc.ast.Symbol_Table` | Most-recent parse result (symbol table) |
| `all_files` | `dict` | Most-recent parse result (per-file parsers) |
| `data_lock` | `threading.Lock` | Guards `symbols` and `all_files` |
| `queue` | `list` | Pending parse events |
| `queue_lock` | `threading.Lock` | Guards `queue` |
| `trigger_parse` | `threading.Event` | Signals the validator thread |
| `validator` | `TrlcValidator` | Background parser thread |
| `parse_partial` | `bool` | True = partial parse (open files only) |
| `verify_mode` | `bool` | True = run CVC5 formal verification |
| `diagnostic_history` | `dict` | Last published diagnostics per URI |

`_apply_config(config)` maps the `trlcServer.*` VS Code settings onto the two
mode flags above.

#### `TrlcValidator(threading.Thread)`

A single long-lived daemon thread. It waits on `trigger_parse`, applies a
300 ms debounce (to avoid redundant parses during rapid typing), drains
the `queue`, updates `File_Handler`, then calls `TrlcLanguageServer.validate()`.

### Parse flow

```
User edits file
  → did_change handler
      → queue_event("change", uri, content)
          → trigger_parse.set()
              → TrlcValidator.run() wakes up
                  → debounce 300 ms
                  → drain queue (update File_Handler)
                  → TrlcLanguageServer.validate()
                      → Vscode_Source_Manager.process()
                          → TRLC parser runs
                      → data_lock: update symbols + all_files
                      → publish diagnostics via text_document_publish_diagnostics
```

### Configuration fetch

Configuration is fetched from the client **once per file open** (`did_open`)
and **once on settings change** (`on_config_change`). It is not re-fetched
on every keystroke.

### Thread safety

`symbols` and `all_files` are written exclusively by the validator thread and
read by LSP request handlers (which run on the pygls I/O thread). All access
is guarded by `data_lock`. Handlers take a snapshot (local reference) under
the lock and release it immediately before doing any real work.

### LSP features

| Feature | Handler | Notes |
|---|---|---|
| `textDocument/didOpen` | `did_open` | Fetches config, queues parse |
| `textDocument/didChange` | `did_change` | Queues parse |
| `textDocument/didClose` | `did_close` | Removes file from in-memory map |
| `textDocument/completion` | `completion` | Packages, record fields, enum literals, record references |
| `textDocument/hover` | `hover` | Shows user-defined `description` annotation |
| `textDocument/definition` / `typeDefinition` | `goto_type_definition` | Jumps to the Entity's declaration |
| `textDocument/references` | `references` | Finds all tokens linked to the same AST entity |
| `textDocument/rename` | `rename` | Renames symbol in all files; requires full parse and no errors |
| `textDocument/semanticTokens/full` | `semantic_tokens` | Highlights TRLC operators; reuses cached token stream |
| `workspace/didChangeConfiguration` | `on_config_change` | Re-applies settings, triggers reparse |
| `workspace/didChangeWorkspaceFolders` | `on_workspace_folders_change` | Triggers reparse |

---

## Parsing modes

| Mode | How it is set | What gets parsed |
|---|---|---|
| **Partial** (default) | `trlcServer.parsing = "partial"` | All open (in-editor) files plus their transitive `.rsl` includes |
| **Full** | `trlcServer.parsing = "full"` | Every `.rsl` / `.trlc` file in the workspace folders |

Rename is only available in full-parse mode, because partial mode does not
guarantee that all reference sites are known.

---

## Development

### Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.8 – 3.12 | Running the language server |
| Node.js | 18+ | Building the VS Code extension |
| npm | bundled with Node.js | Installing JS dependencies |

On Debian/Ubuntu the system Node.js is often too old; upgrade it (as root):

```bash
curl -sL https://deb.nodesource.com/setup_18.x -o /tmp/nodesource_setup.sh
sudo bash /tmp/nodesource_setup.sh
sudo apt-get install -y nodejs
```

### Python runtime dependencies

All Python dependencies are auto-installed into `<extensionDir>/python-deps/`
on first activation — no manual `pip install` is needed during development
unless you run the server directly.

| Package | Purpose |
|---|---|
| [pygls](https://pypi.org/project/pygls) | LSP framework |
| [lsprotocol](https://pypi.org/project/lsprotocol) | LSP type definitions |
| [trlc](https://github.com/bmw-software-engineering/trlc) | TRLC parser and type checker |
| [cvc5](https://cvc5.github.io) | SMT solver (pulled in transitively via trlc/PyVCG) |

### Build from source

```bash
make build     # npm install + npx vsce package  →  *.vsix
# or
make install   # build + install the .vsix into VS Code
```

### Run in debugger mode

1. Open the repository in VS Code.
2. Run `npm install` in the root folder.
3. Open the **Run and Debug** view (`Ctrl+Shift+D`).
4. Select **Server + Client** and press **F5**.

