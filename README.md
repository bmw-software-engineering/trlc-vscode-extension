# TRLC visual studio code extension

`trlc-vscode` is a visual studio code extension that provides editing
features like syntax highlighting, auto completion and error checking
for TRLC files. Get more information about
[TRLC](https://github.com/bmw-software-engineering/trlc/).

## Installation

1. Install [Python](https://www.python.org/downloads/): 3.8 <= Python <= 3.12.
2. Download the `*.vsix` file under the `Assets` tab of the [latest Release](https://github.com/bmw-software-engineering/trlc-vscode-extension/releases/latest) of the extension.
3. Press `F1` in VSCode, type `Extensions: Install from VSIX...` and install the extension.
4. If it is not working out of the box, go to VSCode Settings, search for `python.defaultInterpreterPath` and make sure it leads to your installed python executable.

On first use, the extension automatically installs all required Python
dependencies (`pygls`, `trlc`, and `lsprotocol`) into an
isolated `python-deps/` folder inside the extension directory — no
manual `pip install` is needed.

**Reinstalling the extension?** Press `F1` and run: `TRLC: Reset Setup`
once so that dependencies are re-installed cleanly.

## Use with other editors

The `trlc_lsp` server is a standalone Python package that works with any
LSP-capable editor.

### CLion and other JetBrains IDEs

A ready-made [lsp4ij](https://github.com/redhat-developer/lsp4ij) template is bundled in [`lsp4ij-template/`](lsp4ij-template/) and is also attached to every [GitHub Release](https://github.com/bmw-software-engineering/trlc-vscode-extension/releases/latest) as `lsp4ij-template.zip`. Full setup and configuration instructions are in [lsp4ij-template/README.md](lsp4ij-template/README.md).

**Quick start:**
1. Install the **lsp4ij** plugin in your JetBrains IDE.
2. Open **Settings → Languages & Frameworks → Language Servers**.
3. Click **[+] → New Language Server → Import from custom template...** and
   select the `lsp4ij-template/` folder (or the downloaded `lsp4ij-template.zip`).
4. Confirm — on first file open lsp4ij automatically downloads and installs
   the `trlc_lsp` wheel from GitHub Releases, with a local `trlc_lsp*.whl`
   in your home directory as a fallback.

### Neovim, Emacs, Helix, and other LSP clients

Install the server as a pip package and point your editor's LSP client at it.
See [trlc_lsp/README.md](trlc_lsp/README.md) for details.

## How to switch from partial (default) to full parsing.

This extension offers `partial` and `full` parsing of **TRLC** files.
In `partial` mode, only a subset of files within the current workspace or
folder is parsed — specifically, the ones you have opened in your editor. On
the other hand, in `full` mode, the entire workspace or folder is parsed,
resulting in a longer processing time.

1. Open the Settings either through the gear icon or through the menu:
   - On Windows/Linux, go to `File > Preferences > Settings`.
   - On macOS, go to `Code > Preferences > Settings`.

2. Search for `trlc` using the search bar at the top.

3. Modify the setting `Trlc Server: Parsing` and enter either **full** or
**partial**

1. Close the Settings, there is no need for saving.

2. Press any key on the keyboard in any TRLC file and the updated settings will
take effect.

**Note:** The last step is necessary as Visual Studio Code applies the setting only
when a change is made in a file.

## Copyright and License

The TRLC VSCode Extension is licensed under the [GPL-3.0](LICENSE) and
the main copyright holder is the Bayerische Motoren Werke
Aktiengesellschaft (BMW AG).

Parts of the extension are derived from the samples provided by
[pygls](https://pypi.org/project/pygls) (licensed under the Apache 2.0
license) and the Microsoft Corporation (also licensed under the Apache
2.0 license).
