# TRLC Language Server — lsp4ij Template

This template configures the [TRLC Language Server](https://github.com/bmw-software-engineering/trlc-vscode-extension)
for use in CLion (or any JetBrains IDE) via the [lsp4ij](https://github.com/redhat-developer/lsp4ij) plugin.

## Prerequisites

- Python 3.8+ must be installed and `python3` / `python` available on your `PATH`.
- `pip3` / `pip` must be available on your `PATH`.
- Internet access is recommended so the installer can download the latest wheel from GitHub Releases.
  As a fallback, place a `server*.whl` file in your home directory before the first start.

## Installation

1. Install the **lsp4ij** plugin in your JetBrains IDE.
2. Open **Settings → Languages & Frameworks → Language Servers**.
3. Click **[+]** → **New Language Server**.
4. In the **Template** combo-box select **Import from custom template...** and choose this directory.
5. Confirm with **OK** — lsp4ij will run the installer automatically on first start.

## How it works

On first start lsp4ij tries to download the latest `server` wheel from
[GitHub Releases](https://github.com/bmw-software-engineering/trlc-vscode-extension/releases/latest).
If that fails (no network, rate-limited, no release yet) it falls back to the newest
`server*.whl` found in your home directory:

```sh
# Linux / macOS
WHL=$(curl -sf https://api.github.com/repos/bmw-software-engineering/trlc-vscode-extension/releases/latest \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assets=d.get("assets",[]); \
    url=next((a["browser_download_url"] for a in assets if a["name"].endswith(".whl")),None); \
    print(url or "")' 2>/dev/null)
[ -z "$WHL" ] && WHL=$(ls ~/server*.whl 2>/dev/null | sort -V | tail -n 1)
[ -n "$WHL" ] && pip3 install --upgrade --target ~/.lsp4ij/trlc-lsp "$WHL"

# Windows (PowerShell)
$r = try { Invoke-RestMethod 'https://api.github.com/repos/bmw-software-engineering/trlc-vscode-extension/releases/latest' } catch { $null }
$url = if ($r) { $r.assets | Where-Object { $_.name -like '*.whl' } | Select-Object -First 1 -ExpandProperty browser_download_url } else { $null }
if (-not $url) { $url = Get-ChildItem "$HOME\server*.whl" -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName }
if ($url) { pip install --upgrade --target "$HOME\.lsp4ij\trlc-lsp" $url }
```

The version is resolved automatically from the latest GitHub Release — no manual updates needed.
All packages are installed into `~/.lsp4ij/trlc-lsp/` — no system-wide changes are made.
On subsequent starts lsp4ij checks whether the `server` directory exists inside the
target folder and skips the installer if so; use **Reinstall** in the Language Servers
settings to force a re-installation from a new wheel.

The server is then launched via:

```sh
# Linux / macOS
sh -c "PYTHONPATH=~/.lsp4ij/trlc-lsp python3 -m server"

# Windows
cmd /C set PYTHONPATH=%USERPROFILE%\.lsp4ij\trlc-lsp && python -m server
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
    "trlcServer.parseMode": "workspace",
    "trlcServer.verify": true,
    "trlcServer.excludePatterns": []
}
```

| Setting | Values | Default | Description |
|---|---|---|---|
| `parseMode` | `"workspace"` / `"directory"` / `"repo"` / `"bazel"` | `"workspace"` | `workspace`: open files + includes (fast, interactive). `directory`: single directory, non-recursive (monorepos). `repo`: all `.rsl`/`.trlc` files (needed for rename/references). `bazel`: Bazel TRLC targets (requires WORKSPACE/MODULE.bazel). The legacy key `parsing` (`"partial"`/`"full"`) is still accepted and maps to `workspace`/`repo`. |
| `verify` | boolean | `true` | Enable CVC5 formal verification of checks (increases parse time). |
| `excludePatterns` | string array | `[]` | Regex patterns matched against directory names to exclude from scanning (`^bazel-.*$` is always excluded). |
| `bazel.executable` | string | `"bazel"` | Path to the bazel executable for `parseMode: bazel`. |
| `bazel.ruleClasses` | string array | `["_trlc_specification", "_trlc_requirement", "_trlc_rst", "_score_requirements_rule", "feature_requirements", "component_requirements", "assumed_system_requirements", "fmea"]` | Bazel rule class names to include in `parseMode: bazel` discovery. |
| `bazel.useSharedServer` | boolean | `false` | If true, reuse the default Bazel server (faster but may interfere with your build). If false, use a dedicated output_base per workspace. |
| `bazel.queryTimeoutSeconds` | number | `180` | Timeout (seconds) for a single `bazel query` call. |
| `hover.descriptionComponent` | string | `"description"` | Component name whose string value is shown on hover for a record-object reference. Empty string disables the feature. |
| `logLevel` | string | `"warning"` | Verbosity of TRLC's diagnostic logging (`debug`/`info`/`warning`/`error`). |

## Troubleshooting

- **Installer fails:** make sure `pip3` is on your `PATH`. The installer first tries to fetch the wheel from
  [GitHub Releases](https://github.com/bmw-software-engineering/trlc-vscode-extension/releases/latest);
  if that fails, it looks for a `server*.whl` file in your home directory. Place one there if you are
  working offline or behind a firewall.
- **Reinstall:** Settings → Languages & Frameworks → Language Servers → \<this server\> → Installer tab → **Reinstall**.
- **Server output:** open the **LSP Console** (bottom toolbar → Language Servers) to inspect messages and errors.
