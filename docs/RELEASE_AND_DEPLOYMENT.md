# Release Process

**Audience: Developer.** Assumes familiarity with this repo's Bazel build and
GitHub Actions workflows.

This document describes how a new release of the `trlc-vscode-extension` is produced.

Version bumping is checked (not yet automated) and publishing to the VS Code
Marketplace / PyPI is a manual, human-triggered step — see
[Publishing](#publishing) below. Consumers can always install the extension
by downloading the `.vsix` file from the GitHub Release (see
[README.md](../README.md#installation)) regardless of whether the Marketplace/
PyPI publish step has been run for a given release.

## Steps to cut a release

1. **Bump [`VERSION`](../VERSION)** (single source of truth — a plain-text
   file containing just the version number, e.g. `3.3.0`).

2. **Run the sync script** to propagate it to the 3 places that can't read
   `VERSION` directly:

   ```bash
   bazel run //tools/version_synch:sync_version
   ```

   This updates [`package.json`](../package.json)'s `version` field (drives
   the VSCode extension version and built `.vsix` file name),
   [`pyproject.toml`](../pyproject.toml)'s `version` field (drives the
   standalone `trlc-lsp` distribution version), and
   [`src/server/BUILD.bazel`](../src/server/BUILD.bazel)'s `py_wheel`
   `version` attr (`py_wheel` doesn't read `pyproject.toml`).

   `bazel run //tools/version_synch:sync_version -- --check` (also run in CI,
   see below) fails if any of the 3 have drifted out of sync with `VERSION`
   without writing anything — use it to double check before committing.

3. **Update [`CHANGELOG.md`](../CHANGELOG.md)** with a new section for the
   release (version number, date, notable changes).

4. **Commit** these changes and merge them into `main` (e.g. via a pull
   request).

5. **Tag the release commit** on `main` with a tag matching `vX.Y.Z`, where
   `X.Y.Z` matches the version set in step 1, and push the tag:

   ```bash
   git tag v3.3.0
   git push origin v3.3.0
   ```

## What happens automatically

Pushing a tag matching `v*` triggers the `TRLC Extension Build` workflow
([`.github/workflows/build.yml`](../.github/workflows/build.yml)):

- The **`package`** job always runs (also on every push to `main` and every
  pull request). It builds:
  - the `.vsix` package (`bazel build //:vsix`, see
    [`BUILD.bazel`](../BUILD.bazel)'s `vsix` target),
  - the `trlc_lsp` Python wheel (`bazel build //:wheel`, via
    [`rules_python`'s `py_wheel`](../src/server/BUILD.bazel)), and
  - `lsp4ij-template.zip` (packaged from [`lsp4ij-template/`](../lsp4ij-template/)).

  Both `.vsix` and wheel builds are fully hermetic under Bazel (pinned
  Node/Python toolchains, no system npm/pip/setuptools involved).

- The **`release`** job only runs when the pushed ref is a tag starting with
  `v`. It first verifies the tag matches `VERSION`
  (`bazel run //tools/version_synch:sync_version -- --check-tag`), failing
  the release outright on a mismatch, then downloads the three artifacts
  built above and uses
  [`softprops/action-gh-release`](https://github.com/softprops/action-gh-release)
  to publish/update a GitHub Release for that tag, attaching:
  - `*.vsix`
  - `trlc_lsp*.whl`
  - `lsp4ij-template.zip`

No manual upload of build artifacts is required — only the tag needs to be
pushed. `lint`/`python-test`/`ts-test` (the same reusable workflows CI runs
on every PR, see
[`reusable-format-lint.yml`](../.github/workflows/reusable-format-lint.yml),
[`reusable-python-test.yml`](../.github/workflows/reusable-python-test.yml),
[`reusable-ts-test.yml`](../.github/workflows/reusable-ts-test.yml)) must
also pass before `package` runs, so a tag pushed with no preceding PR is still
gated on the full CI suite rather than skipping it.

## Publishing

Publishing to the VS Code Marketplace and PyPI is a **separate, manual**
workflow —
[`.github/workflows/publish.yml`](../.github/workflows/publish.yml),
`workflow_dispatch`-only. It does not run automatically on any push or tag;
someone has to trigger it:

1. GitHub → **Actions** tab → **Publish Release** → **Run workflow**.
2. Enter the tag to publish (e.g. `v3.3.0`) — it must already exist as a
   GitHub Release (i.e. steps 1-5 above have already happened).
3. The workflow re-checks out that exact tag and rebuilds the `.vsix`/wheel
   from scratch with Bazel (hermetic, byte-reproducible — deliberately does
   **not** reuse the `package` job's `upload-artifact` output, since that's
   only guaranteed to still exist within its retention window, and this
   workflow can run much later than the tag was cut).
4. `publish-vscode-marketplace` and `publish-pypi` then run independently,
   each gated behind a GitHub **Environment** with required reviewers — a
   human has to approve each publish individually in the Actions UI, since
   both are irreversible (a published PyPI version can never be re-uploaded;
   Marketplace publishes are similarly one-way).
