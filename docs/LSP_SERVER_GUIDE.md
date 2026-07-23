# LSP Server User Guide

**Audience: Integrator.** For configuring `trlc-lsp` in an editor other than
VSCode. No TRLC-server-internals knowledge is assumed; familiarity with your
editor's LSP client configuration is.

`trlc-lsp` is a standalone [Language Server Protocol](https://microsoft.github.io/language-server-protocol/)
implementation for the [TRLC](https://github.com/bmw-software-engineering/trlc)
requirements language. Works with **any** LSP-capable editor (Neovim, Emacs,
Helix, JetBrains IDEs via `lsp4ij`, …).

If you're using **VSCode**, you don't need this guide — install the
extension instead and see [USER_GUIDE.md](USER_GUIDE.md). This guide is for
every other editor.

## Installation

```bash
pip install .        # from the repo root
```

This installs all required dependencies: `pygls`, `lsprotocol`, `trlc`, and
`cvc5` (via `trlc` → `PyVCG`). No extra steps needed for CVC5.

## Usage

The server communicates over **stdio** by default:

```bash
trlc-lsp            # stdio (default)
trlc-lsp --stdio    # explicit
trlc-lsp --tcp      # TCP on 127.0.0.1:5678
trlc-lsp --tcp --host 0.0.0.0 --port 9999
```

Or run as a Python module:

```bash
python -m server
```

## Editor Configuration

### Neovim (nvim-lspconfig)

```lua
local lspconfig = require('lspconfig')
local configs = require('lspconfig.configs')

if not configs.trlc then
  configs.trlc = {
    default_config = {
      cmd = { 'trlc-lsp' },
      filetypes = { 'trlc' },
      root_dir = lspconfig.util.root_pattern('.git'),
      settings = {
        trlcServer = {
          parseMode = 'workspace',
          verify = true,
        },
      },
    },
  }
end

lspconfig.trlc.setup({})
```

You also need to register the filetype:

```lua
vim.filetype.add({
  extension = {
    rsl = 'trlc',
    trlc = 'trlc',
  },
})
```

## Settings

The server supports these configuration keys under `trlcServer` (sent via
`workspace/configuration`, or set in your editor's LSP client config).
Descriptions match the VSCode extension's `package.json` verbatim — the
canonical source for all of these:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `parseMode` | `string` | `"workspace"` | Controls file discovery strategy. `workspace`: open files + includes (fast, interactive); `directory`: single directory non-recursive (monorepos); `repo`: all `.rsl`/`.trlc` files (needed for rename/references); `bazel`: Bazel TRLC targets (requires WORKSPACE/MODULE.bazel). |
| `verify` | `boolean` | `true` | Enable CVC5 formal verification of checks. Significantly increases parse time. |
| `excludePatterns` | `string[]` | `[]` | Regex patterns matched against directory names to exclude from TRLC include scanning. The pattern `^bazel-.*$` is always applied by default. |
| `bazel.executable` | `string` | `"bazel"` | Path to the bazel executable for `parseMode: bazel`. |
| `bazel.ruleClasses` | `string[]` | `["_trlc_specification", "_trlc_requirement", "_trlc_rst", "_score_requirements_rule", "feature_requirements", "component_requirements", "assumed_system_requirements", "fmea"]` | Bazel rule class names (internal/private rule names from `trlc.bzl`) to include in `parseMode: bazel` discovery. Customize for third-party TRLC macros. |
| `bazel.useSharedServer` | `boolean` | `false` | If true, reuse the default Bazel server (faster but may interfere with your build). If false, use a dedicated output_base per workspace. |
| `bazel.queryTimeoutSeconds` | `number` | `180` | Timeout (seconds) for a single `bazel query` call. The first query against a fresh/cold Bazel server can take much longer than a warm-server query. |
| `hover.descriptionComponent` | `string` | `"description"` | Component name whose string value is shown on hover when hovering a reference to a record object. Empty string disables the feature. |
| `logLevel` | `string` | `"warning"` | Verbosity of TRLC's diagnostic logging (`debug`/`info`/`warning`/`error`). |

The legacy key `parsing` (`"partial"`/`"full"`) is still accepted for
backwards compatibility and maps to `parseMode` (`partial` → `workspace`,
`full` → `repo`), but new configs should use `parseMode` directly.

## LSP Features

- Diagnostics (errors, warnings)
- Completion
- Hover (user-defined descriptions)
- Go to Type Definition
- Find All References
- Rename Symbol (`parseMode: repo` for a workspace-wide rename; otherwise scope-local)
- Semantic Tokens (operators)
- Code Actions (quick fixes for TRLC diagnostics)

## License

GPL-3.0-or-later
