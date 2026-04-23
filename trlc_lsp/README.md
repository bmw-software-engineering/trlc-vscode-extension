# trlc-lsp — TRLC Language Server

A [Language Server Protocol](https://microsoft.github.io/language-server-protocol/) implementation for the [TRLC](https://github.com/bmw-software-engineering/trlc) requirements language.

Works with **any** LSP-capable editor (VS Code, Neovim, Emacs, Helix, …).

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
python -m trlc_lsp
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
          parsing = 'partial',
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

### Emacs (lsp-mode)

```elisp
(with-eval-after-load 'lsp-mode
  (add-to-list 'lsp-language-id-configuration '(trlc-mode . "trlc"))
  (lsp-register-client
   (make-lsp-client
    :new-connection (lsp-stdio-connection '("trlc-lsp"))
    :activation-fn (lsp-activate-on "trlc")
    :server-id 'trlc-lsp)))
```

### Helix

In `languages.toml`:

```toml
[[language]]
name = "trlc"
scope = "source.trlc"
file-types = ["rsl", "trlc"]
language-servers = ["trlc-lsp"]

[language-server.trlc-lsp]
command = "trlc-lsp"
```

## Settings

The server supports these configuration keys under `trlcServer`:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `parsing` | `string` | `"partial"` | `"partial"` or `"full"` parsing mode |
| `verify` | `boolean` | `true` | Enable CVC5 formal verification (requires cvc5, which is bundled with trlc) |
| `excludePatterns` | `string[]` | `[]` | Regex patterns matched against directory names to exclude from scanning (`^bazel-.*$` is always excluded) |

## LSP Features

- Diagnostics (errors, warnings)
- Completion
- Hover (user-defined descriptions)
- Go to Type Definition
- Find All References
- Rename Symbol (full parsing mode only)
- Semantic Tokens (operators)

## License

GPL-3.0-or-later
