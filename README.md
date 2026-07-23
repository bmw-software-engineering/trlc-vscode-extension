# TRLC visual studio code extension

`trlc-vscode` is a visual studio code extension that provides editing
features like syntax highlighting, auto completion and error checking
for TRLC files. Get more information about
[TRLC](https://github.com/bmw-software-engineering/trlc/).

## Requirements

- **VSCode**: >= 1.91.0
- **Python**: 3.10 <= Python <= 3.13 — **your own install**, not bundled by
  VSCode. This extension is a thin client around a separate Python process;
  it needs a real interpreter on disk to launch.

## Installation

1. Install [Python](https://www.python.org/downloads/): 3.10 <= Python <= 3.13.
2. Download the `*.vsix` file under the `Assets` tab of the [latest Release](https://github.com/bmw-software-engineering/trlc-vscode-extension/releases/latest) of the extension.
3. Press `F1` in VSCode, type `Extensions: Install from VSIX...` and install the extension.
4. If it is not working out of the box, go to VSCode Settings, search for `python.defaultInterpreterPath` and make sure it leads to your installed python executable.

**Building from source?** See the [Developer Guide](docs/DEVELOPER_GUIDE.md)
— the build is fully hermetic via Bazel, no local Node.js/Python toolchain
install required.

On first use, the extension automatically installs all required Python
dependencies (`pygls`, `trlc`, and `lsprotocol`) into an
isolated `python-deps/` folder inside the extension directory

**Reinstalling the extension?** Press `F1` and run: `TRLC: Reset Setup`
once so that dependencies are re-installed cleanly.

## Use with other editors

The `server` (distribution package: `trlc-lsp`) is a standalone Python language
server that works with any LSP-capable editor.

### CLion and other JetBrains IDEs

A ready-made [lsp4ij](https://github.com/redhat-developer/lsp4ij) template is bundled in [`lsp4ij-template/`](lsp4ij-template/) and is also attached to every [GitHub Release](https://github.com/bmw-software-engineering/trlc-vscode-extension/releases/latest) as `lsp4ij-template.zip`. Full setup and configuration instructions are in [lsp4ij-template/README.md](lsp4ij-template/README.md).

**Quick start:**
1. Install the **lsp4ij** plugin in your JetBrains IDE.
2. Open **Settings → Languages & Frameworks → Language Servers**.
3. Click **[+] → New Language Server → Import from custom template...** and
   select the `lsp4ij-template/` folder (or the downloaded `lsp4ij-template.zip`).
4. Confirm — on first file open lsp4ij automatically downloads and installs
   the `trlc-lsp` wheel from GitHub Releases, with a local `trlc_lsp*.whl`
   in your home directory as a fallback.

### Neovim, Emacs, Helix, and other LSP clients

Install the server as a pip package and point your editor's LSP client at it.
See [docs/LSP_SERVER_GUIDE.md](docs/LSP_SERVER_GUIDE.md) for details.

## Documentation

- [User Guide](docs/USER_GUIDE.md) — configuring parse mode, all settings,
  commands, troubleshooting.
- [LSP Server User Guide](docs/LSP_SERVER_GUIDE.md) — using `trlc-lsp` from
  editors other than VSCode.
- [Developer Guide](docs/DEVELOPER_GUIDE.md) — building, testing, and
  extending the extension.
- [Extension Architecture](docs/ARCHITECTURE.md) — internal design.
- [Corporate Integration](docs/CORPORATE_INTEGRATION.md) — building
  internal tooling on top of the extension.
- [Release and Deployment](docs/RELEASE_AND_DEPLOYMENT.md) — cutting a
  release.

## Copyright and License

The TRLC VSCode Extension is licensed under the [GPL-3.0](LICENSE) and
the main copyright holder is the Bayerische Motoren Werke
Aktiengesellschaft (BMW AG).

Parts of the extension are derived from the samples provided by
[pygls](https://pypi.org/project/pygls) and Microsoft's
[vscode-extension-samples](https://github.com/microsoft/vscode-extension-samples)
(both licensed under the Apache 2.0 license).
