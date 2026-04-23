# TRLC Language Server — lsp4ij Template

This template configures the [TRLC Language Server](https://github.com/bmw-software-engineering/trlc-vscode-extension)
for use in CLion (or any JetBrains IDE) via the [lsp4ij](https://github.com/redhat-developer/lsp4ij) plugin.

## Prerequisites

- Python 3.8+ must be installed and `python3` / `python` available on your `PATH`.
- `pip3` / `pip` must be available on your `PATH`.
- Internet access is required on first start so the installer can download the latest wheel from GitHub Releases.

## Installation

1. Install the **lsp4ij** plugin in your JetBrains IDE.
2. Open **Settings → Languages & Frameworks → Language Servers**.
3. Click **[+]** → **New Language Server**.
4. In the **Template** combo-box select **Import from custom template...** and choose this directory.
5. Confirm with **OK** — lsp4ij will run the installer automatically on first start.

## How it works

On first start lsp4ij downloads the latest `trlc_lsp` wheel directly from the
[GitHub Releases](https://github.com/bmw-software-engineering/trlc-vscode-extension/releases/latest)
page and installs it:

```sh
# Linux / macOS
pip3 install --upgrade --target ~/.lsp4ij/trlc-lsp \
  "$(curl -s https://api.github.com/repos/bmw-software-engineering/trlc-vscode-extension/releases/latest \
     | python3 -c 'import json,sys; d=json.load(sys.stdin); assets=d.get("assets",[]); url=next((a["browser_download_url"] for a in assets if a["name"].endswith(".whl")),None); sys.exit(1) if not url else print(url)')"

# Windows (PowerShell)
$url = (Invoke-RestMethod 'https://api.github.com/repos/bmw-software-engineering/trlc-vscode-extension/releases/latest').assets |
       Where-Object { $_.name -like '*.whl' } |
       Select-Object -First 1 -ExpandProperty browser_download_url
pip install --upgrade --target "$env:USERPROFILE\.lsp4ij\trlc-lsp" $url
```

The version is resolved automatically from the latest GitHub Release — no manual updates needed.
All packages are installed into `~/.lsp4ij/trlc-lsp/` — no system-wide changes are made.
On subsequent starts lsp4ij checks whether the `trlc_lsp` directory exists inside the
target folder and skips the installer if so; use **Reinstall** in the Language Servers
settings to force a re-installation from a new wheel.

The server is then launched via:

```sh
# Linux / macOS
sh -c "PYTHONPATH=~/.lsp4ij/trlc-lsp python3 -m trlc_lsp"

# Windows
cmd /C set PYTHONPATH=%USERPROFILE%\.lsp4ij\trlc-lsp && python -m trlc_lsp
```

The server communicates over **stdio** (no extra flags required).

## File associations

| File extension | Language ID |
|----------------|-------------|
| `*.trlc`       | `trlc`      |
| `*.rsl`        | `trlc`      |

## Configuration

The server reads settings under the `trlcServer` key. For lsp4ij, set them in
**Settings → Languages & Frameworks → Language Servers → \<this server\> → Configuration**:

```json
{
    "trlcServer.parsing": "partial",
    "trlcServer.verify": true,
    "trlcServer.excludePatterns": []
}
```

| Setting | Values | Default | Description |
|---|---|---|---|
| `parsing` | `"partial"` / `"full"` | `"partial"` | `partial`: parse only open files + their `.rsl` includes. `full`: parse all files in the workspace. |
| `verify` | boolean | `true` | Enable CVC5 formal verification of checks (increases parse time). |
| `excludePatterns` | string array | `[]` | Regex patterns matched against directory names to exclude from scanning (`^bazel-.*$` is always excluded). |

## Troubleshooting

- **Installer fails:** make sure `pip3` is on your `PATH` and that you have internet access. The installer fetches the wheel from the [GitHub Releases](https://github.com/bmw-software-engineering/trlc-vscode-extension/releases/latest) page.
- **Reinstall:** Settings → Languages & Frameworks → Language Servers → \<this server\> → Installer tab → **Reinstall**.
- **Server output:** open the **LSP Console** (bottom toolbar → Language Servers) to inspect messages and errors.
