# Release Process

This document describes how a new release of the `trlc-vscode-extension` is currently produced.

There is no automated version bumping or Marketplace publishing — releases are
triggered manually by pushing a Git tag, and consumers install the extension
by downloading the `.vsix` file from the resulting GitHub Release (see
[README.md](README.md#installation)). The extension is **not** published to
the VS Code Marketplace.

## Steps to cut a release

1. **Bump the version number** in both:
   - [`package.json`](package.json) (`version` field — this drives the VSCode
     extension version and the built `.vsix` file name), and
   - [`pyproject.toml`](pyproject.toml) (`version` field — this drives the
     standalone `trlc_lsp` wheel version).

   Keep both versions in sync.

2. **Update [`CHANGELOG.md`](CHANGELOG.md)** with a new section for the
   release (version number, date, notable changes).

3. **Commit** these changes and merge them into `main` (e.g. via a pull
   request).

4. **Tag the release commit** on `main` with a tag matching `vX.Y.Z`, where
   `X.Y.Z` matches the version set in step 1, and push the tag:

   ```bash
   git tag v3.3.0
   git push origin v3.3.0
   ```

## What happens automatically

Pushing a tag matching `v*` triggers the `TRLC Extension Build` workflow
([`.github/workflows/build.yml`](.github/workflows/build.yml)):

- The **`package`** job always runs (also on every push to `main` and every
  pull request). It builds:
  - the `.vsix` package (`npx vsce package`),
  - the `trlc_lsp` Python wheel (`python3 -m build --wheel`), and
  - `lsp4ij-template.zip` (packaged from [`lsp4ij-template/`](lsp4ij-template/)).

- The **`release`** job only runs when the pushed ref is a tag starting with
  `v`. It downloads the three artifacts built above and uses
  [`softprops/action-gh-release`](https://github.com/softprops/action-gh-release)
  to publish/update a GitHub Release for that tag, attaching:
  - `*.vsix`
  - `trlc_lsp*.whl`
  - `lsp4ij-template.zip`

No manual upload of build artifacts is required — only the tag needs to be
pushed.

## Notes / limitations

- The workflow does not verify that the pushed tag matches the version in
  `package.json` / `pyproject.toml`; make sure they agree manually.
- There is currently no automated publishing to the VS Code Marketplace or
  Open VSX — the `.vsix` attached to the GitHub Release is the only
  distribution channel.
