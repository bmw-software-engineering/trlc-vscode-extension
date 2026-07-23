# User Guide

**Audience: User.** No programming or TRLC-internals knowledge is assumed.
This guide covers day-to-day use of the TRLC VSCode extension. For
installing the extension itself, see the [README](../README.md).

## Features

Once a `.rsl`/`.trlc` file is open, the extension provides:

- **Syntax highlighting** — instant, as you type (independent of the
  language server).
- **Diagnostics** — errors and warnings from parsing and, optionally,
  formal verification (see `Trlc Server: Verify` below), shown as
  underlines and in the Problems panel.
- **Auto-completion** — suggests known symbols (types, records, etc.) as
  you type.
- **Hover** — shows a symbol's user-written description when you hover
  over it.
- **Go to Type Definition** — jump from a usage to where a type is
  declared.
- **Find All References** — list every place a symbol is used (requires
  `repo` parse mode, see below).
- **Rename Symbol** — rename a symbol and update every usage (also
  requires `repo` parse mode for a workspace-wide rename).
- **Scope status bar** — shows whether the active file's parse scope is
  up to date; click it to see and open every file currently in that
  scope (same as **TRLC: Show Files In Scope** below).

## Why Parse Modes Exist

TRLC only checks for duplicate/clashing definitions and resolves
references *within the set of files it currently has loaded* — it has no
concept of "everything else in the repo that I haven't loaded." That
loaded set is exactly what a **parse mode** controls, and the mode you
pick is a trade-off:

- Load **too little** and cross-file references you'd expect to work
  (jump to definition, rename) come back empty, because the target file
  was never loaded into the same session.
- Load **too much** and two unrelated files that happen to reuse the same
  name (a common pattern across independent teams/directories) collide as
  a false "duplicate definition" error, even though neither file ever
  intended to interact with the other.

Parse mode picks where that boundary sits — see below for what each mode
actually loads, and the settings table for the exact setting.

## How TRLC Discovers Files

Each parse mode is a different **file-discovery strategy** — a different
answer to "starting from the file(s) I have open, what else should be loaded
into the same session?" Picking the right one is a practical trade-off
between three things: how fast typing feels, whether same-named files in
different folders can collide, and whether "Find All References"/rename see
every usage or only nearby ones.

| Mode | What gets loaded | Best for | What you'd notice if it's the wrong choice |
|---|---|---|---|
| **workspace** (default) | The open file(s), plus any schema (`.rsl`) file they transitively `import` in the same workspace folder. | Everyday interactive editing — fastest mode. | "Find All References" and rename come back incomplete (files outside the open set are invisible to them). |
| **directory** | Only the other files in the *same folder* as the open file — nothing from sibling or parent folders. | Large monorepos where two unrelated teams' folders happen to reuse the same definition name. | Cross-folder references (jump to definition into another folder) come back empty. |
| **repo** | Every `.rsl`/`.trlc` file in the open file's entire workspace folder, walked recursively. | "Find All References" and workspace-wide rename — the only mode that sees every usage. | Slower to parse; two unrelated files anywhere in the folder that happen to share a name produce a false "duplicate definition" error. |
| **bazel** | The Bazel target that declares the open file as a source, plus that target's other declared sources. Falls back to **directory** mode, quietly, only when the query *succeeded* but this particular file isn't owned by any target. | Monorepos organized into fine-grained Bazel targets, where "everything in this folder" is too broad or too narrow. | If no Bazel workspace can be found, or the `bazel query` itself fails (bad `bazel` executable, broken `WORKSPACE`/`MODULE.bazel`), you get a one-time error popup and **no** fallback — the affected file(s) show no diagnostics until the setup is fixed and reparsed (`Trlc: Parse All`), rather than silently switching to directory-mode parsing (see
**TRLC: Parse All** below). |

The setting is `Trlc Server: Parse Mode` — see [Configuring Parse
Mode](#configuring-parse-mode) below for where to set it, and [All
Settings](#all-settings) for the exact tooltip text.

## Configuring Parse Mode

1. Open Settings:
   - Windows/Linux: `File > Preferences > Settings`
   - macOS: `Code > Preferences > Settings`
2. Search for `trlc` and find `Trlc Server: Parse Mode`.
3. Select a mode — see the [How TRLC Discovers Files](#how-trlc-discovers-files)
   table above for what each one loads.

**Note:** Settings take effect on the next file edit or save.

## All Settings

Every setting lives under `File > Preferences > Settings`, search `trlc`.
Descriptions below are the exact tooltip text VSCode shows for each
setting (from the extension's `package.json` — the single source for this
wording).

| Setting | Default | Description |
|---|---|---|
| `Trlc Server: Parse Mode` | `workspace` | Controls file discovery strategy. workspace: open files + includes (fast, interactive); directory: single directory non-recursive (monorepos); repo: all .rsl/.trlc files (needed for rename/references); bazel: Bazel TRLC targets (requires WORKSPACE/MODULE.bazel). |
| `Trlc Server: Verify` | `true` | Enable CVC5 formal verification of checks. Significantly increases parse time. |
| `Trlc Server: Exclude Patterns` | `[]` | Regex patterns matched against directory names to exclude from TRLC include scanning. The pattern `^bazel-.*$` is always applied by default. |
| `Trlc Server > Bazel: Executable` | `bazel` | Path to the bazel executable for parseMode: bazel. |
| `Trlc Server > Bazel: Rule Classes` | *(see package.json)* | Bazel rule class names (internal/private rule names from trlc.bzl) to include in parseMode: bazel discovery. Customize for third-party TRLC macros. |
| `Trlc Server > Bazel: Use Shared Server` | `false` | If true, reuse the default Bazel server (faster but may interfere with your build). If false, use a dedicated output_base per workspace. |
| `Trlc Server > Bazel: Query Timeout Seconds` | `180` | Timeout (seconds) for a single 'bazel query' call. The first query against a fresh/cold Bazel server (the default unless useSharedServer is true) can take much longer than a warm-server query, especially on large repos or slow filesystems. |
| `Trlc Server > Hover: Description Component` | `description` | Component name whose string value is shown on hover when hovering a reference to a record object (e.g. the target of a derived_from entry). Set to an empty string to disable. Only affects object instances - hovering a type/tuple/enum name always shows its own TRLC RSL doc comment, independent of this setting. |
| `Trlc Server > Trace: Server` | `off` | Traces communication between VS Code and the TRLC language server. Set to 'verbose' and check the '[pygls] TrlcLanguageServer' Output channel to diagnose why a file isn't parsing as expected. |
| `Trlc Server: Log Level` | `warning` | Sets the verbosity of TRLC's diagnostic logging (client and server), shown in the '[pygls] TrlcLanguageServer' output channel and written to a rotating log file. This is separate from Trlc Server > Trace: Server, which only traces the LSP wire protocol. |

## Commands

Available via `F1` / Command Palette:

- **TRLC: Parse All** — force a full reparse of every open scope.
- **TRLC: Reset Setup** — reinstall the server's Python dependencies from
  scratch. Use this if the extension isn't starting after an update.
- **TRLC: Show Output** — open the "[pygls] TrlcLanguageServer" output
  channel (server/client logs and, if enabled, LSP wire trace).
- **TRLC: Show Files In Scope** — list every file in the active file's
  current parse scope; pick one to open it. Also reachable by clicking
  the scope status bar item.

## Troubleshooting

- **Extension doesn't start / no diagnostics appear**: run **TRLC: Reset
  Setup**, then reload the window.
- **Wrong Python interpreter**: go to Settings, search for
  `python.defaultInterpreterPath`, and confirm it points at a Python
  3.10–3.13 install.
- **Unexpected "duplicate definition" errors** between same-named files in
  different folders: switch `Trlc Server: Parse Mode` to `directory`.
- **Rename or "Find All References" missing results**: these require
  `Trlc Server: Parse Mode` set to `repo`.
- **"Bazel parsing unavailable" popup, no diagnostics in `bazel` mode**: no
  Bazel workspace was found, or `bazel query` itself failed — check the
  `bazel_executable` setting and that a `WORKSPACE`/`MODULE.bazel` file
  exists above the open file. Once fixed, run **TRLC: Parse All** (the
  failure is cached until then, so editing alone won't retry it).
- **Need more detail on a parsing/scope problem**: raise `Trlc Server: Log
  Level` to `debug` and check the "[pygls] TrlcLanguageServer" output
  channel — this is the general-purpose verbosity knob and the first thing
  to try.
- Turn on `Trlc Server > Trace: Server` = `verbose` and check the same
  output channel if the problem looks like a communication issue between
  VS Code and the server specifically (not a parsing/scope problem) — this
  traces the raw LSP protocol, not TRLC's own logging.

## Using TRLC Outside VSCode

Using CLion, another JetBrains IDE, Neovim, Emacs, or another LSP-capable
editor? See the root [README](../README.md#use-with-other-editors) and the
[LSP Server User Guide](LSP_SERVER_GUIDE.md).

## Corporate / Third-Party Extensions

Building on top of this extension for internal tooling? See
[CORPORATE_INTEGRATION.md](CORPORATE_INTEGRATION.md).
