# VSCODE new features

## Introduction

This document outlines potential approaches to extend the functionality of the `trlc-vscode-extension` to address specific requirements, such as those of BMW. It explores 2 options: 1) to create a BMW-specific fork of the extension or 2) To implement a plugin system through a second vscode extenstion. On both approaches tha changes to the existing architecture are adressed to eneble the functionalities. In the second option the modifying the core extension was minimized as much as possible by sugesting an elegant solution for it: exposing its data through its own `API`.

## Context

The `trlc-vscode-extension` uses a so-called “Language Server Protocol” (LSP) to comply with the TRLC programming language. With this server, the extension provides features like syntax checking for `.rsl` and `.trlc` files directly in VSCode. The heart of the VSCode extension uses an open-source tool called: **Pygls**.

The Language Server Protocol (LSP) is a standard created by Microsoft to simplify adding language features like autocompletion, syntax checking, and error diagnostics to code editors. It separates the editor's user interface (client) from language-specific logic (server), enabling reusability of the same server across multiple tools.

**Pygls** implements the TRLC rules within its own created `TrlcLanguageServer` in a easy way by providing built-in classes and methods to create the specific LSP servers in Python.

## Architecture

The extension follows a client-server model, utilizing the LSP to separate the editor's interface (client) from the language-specific logic (server). The server is implemented in Python using the Pygls library, while the client is developed in TypeScript.

The client is implemented [here](https://github.com/bmw-software-engineering/trlc-vscode-extension/blob/main/client/src/extension.ts). It handles the communication between the VSCode editor and the Python-based server.

The server is implemented [here](https://github.com/bmw-software-engineering/trlc-vscode-extension/blob/main/server/__main__.py). It performs all the heavy lifting for parsing and analyzing `.rsl` and `.trlc` files aided from the back-end `TRLC` software with its modulesx. 

The LSP (Language Server Protocol) operates on a client-server mode:

- The Language Server (implemented in `pygls`) provides features like hover, diagnostics, and completion.
- The VS Code Client sends requests (e.g., hover requests) and displays responses.

### Possible approaches to deal with BMW specific needs:

### 1. BMW Fork of the Extension

Branch concept: [PR #31](https://github.com/bmw-software-engineering/trlc-vscode-extension/pull/31)

A BMW fork of the extension provides complete control over the codebase but comes with the downside of maintaining and syncing changes with the upstream open-source repository.

- New handlers in `server/server.py` would need to be added for BMW-specific checks.
- Provides full control of current file parsing/processing as a post-processing step and new handlers within existing parsing are added.
- no API needs to be exposed nor implemented, since data is already on parsing time.

_Disclaimer_: The changes represented in PR 31 try to point into the most proable code positions and functions to be edited to make specific enhancements. But these sugestions were not tested, therefore don't consider them as the absolute truth. This is so, because this document just represents a first concept approach.

### 2. Plugin System for Extensibility

Branch with the concept: [PR #32](https://github.com/bmw-software-engineering/trlc-vscode-extension/pull/32)

Implementing a plugin system allows isolating almost completely the original `vscode-trlc-extension`. BMW-specific logic and needs would be packaged into a new `vscode-trlc-bmw-expansion-extension` that the original open-source extension dynamically feeds through a yet-to-be implemented API. This avoids modifying too much the core extension but requires compatibility management. And depending on the complexity of the future features could require its own LSP. At the moment of writing this document and the discussed bmw specific needs, a "client-only" second extension seems to be enough to fulfill the task. Nonetheless, a second LSP would provide advanced language processing posibilities.

To enable communication with the current extension, the client needs to expose an API. Currently, this is **not the case**, but a proper API could be built in the current `extension.ts`. Microsoft Examples: [VSCode API](https://code.visualstudio.com/api/references/vscode-api#extensions:~:text=open%20was%20successful.-,extensions,-Namespace%20for%20dealing)

### Example:

In the current `extensions.ts`, the TRLC data should be exposed in the `activate` function as:

```javascript
export function activate(context: vscode.ExtensionContext) {
  let api = {
    sum(a, b) {
      return a + b;
    },
    mul(a, b) {
      return a * b;
    }
  };
  // 'export' public api-surface
  return api;
}
```

## Implementation Highlights

1. Expand the current client to expose an API with the relevant data. (CB-ID, ASIL level, and Syml objects detections must be returned from the API).
2. Establish Dependency to the Original Extension. The new plug-in extension loads after the original TRLC extension has loaded via the file `package.json`:

```json
{
  "extensionDependencies": ["bmw.trlc-vscode-extension"]
}
```

### Explanation of suggestions to expand `trlc-vscode-extension` with a **Client-Only** Extension

Modifications are needed in both the original extension. These changes are in PR 32.

In the original extension, edit `client/src/extension.ts` to expose a function like `getHoverDetails(uri: string, position: Position)` in its `activate` function, which sends a `textDocument/hover` request to the language server via `client.sendRequest` and returns hover data. In the language server (`server/server.py`), ensure the `hover` function, registered with `@trlc_server.feature(TEXT_DOCUMENT_HOVER)`, processes requests by identifying tokens under the cursor (e.g., CodeBeamer IDs or SYML references) and returns relevant details in a `Hover` object. In similar words within `server.py`, the hover function decorated with `@trlc_server.feature(TEXT_DOCUMENT_HOVER)` intercepts the `textDocument/hover` request from `extension.ts` (the client). Finally, use `context.subscriptions` for proper lifecycle management (ending) and assign the extended API object to `context.exports` to make it accessible to any other extension.

In the new extension (`extension2.ts`), use the exposed API by accessing `trlc-vscode-extension` via `vscode.extensions.getExtension`, calling `getHoverDetails` to fetch hover data, and enhancing functionality, such as formatting text using `TextEditorDecorationType` [see here](https://code.visualstudio.com/api/references/vscode-api#window.createTextEditorDecorationType:~:text=createTextEditorDecorationType(options%3A%20DecorationRenderOptions)%3A%20TextEditorDecorationType) or making API requests to external services like CodeBeamer.

Also, inside `extension2.ts`, enable sanity checks with a proposed tool called `sanity_checks.py`. This Python script, analogous to `server.py`, can use relevant modules of TRLC’s backend. **This can be tricky** and/or very time-consuming and must be planned correctly. If any sanity errors were found, the UI changes can be handled. The Python script could be run with Node.js's `child_process` module and pass the workspace to parse as an argument.

Later on in the CI the `sanity_checks.py` can be run as well.

### Why a New LSP is Not Needed (probably)

A new LSP is not needed because the existing `trlc-vscode-extension` already provides the necessary infrastructure to handle language-specific logic via its `server.py`. The `hover` function in the server is capable of identifying and returning relevant metadata (e.g., CodeBeamer IDs, SYML references) for tokens under the cursor, and this data can be exposed to other extensions through an API like `getHoverDetails` in `client/src/extension.ts` or any other exposed function.

Since the new extension (`extension2.ts`) is only enhancing the UI's behavior by consuming this hovered/clicked/opened data, formatting it, or using it for external API requests, it doesn’t require its own LSP. The existing LSP server already parses, validates, and understands the TRLC language, so the new extension can simply extend functionality at the client level.

_NOTE_: A new LSP is required whenever advanced language processing is needed like deep code/text analysis or if it is aimed for cross-editor compatibility or standardized language support. This was the original idea behind LSP’s approach from Microsoft. Since the sanity checks is the only requirement that needs to understand partially how is TRLC built it may not be bad to have an own second LSP. Nonetheless, the sanity checks seem to be possible to be perfectly execute by the proposed Python script and within the client's side. This also ensures CI execution of that script in the CI, by returning exit codes.

_Disclaimer_: The changes represented in PR 32 try to point into the most proable code positions and functions to be edited to make specific enhancements. But these sugestions were not tested, therefore don't consider them as the absolute truth. This is so, because this document just represents a first concept approach.

### Conclusion

The fork approach simplifies very much the development and grants full control over the data, making it an attractive option for a quicker solution. However, in the long term, it creates a higher burden in terms of synchronization and maintenance. Maintaining a fork may require continuous updates to ensure compatibility with upstream changes, which can result in significant overhead.

On the other hand, implementing a plugin system via a second extension offers a decoupled solution. By exposing an API of the original trlc-vscode-extension, the second extension can just dynamically consume and extend functionality without tightly coupling with the core extension. This approach maintains the integrity of the upstream project, reducing the risk of code conflicts and making it easier to adopt updates from the open-source repository. However, this second approach involves a steeper initial development effort, requiring both architectural adjustments to the existing extension and careful planning to ensure API reliability. Additionally, the actual need for a second Language Server Protocol (LSP) must be carefully evaluated. For now, the proposed architecture suggests that the existing LSP can handle the required language processing tasks, while the second extension focuses on enhancing user interface behaviors and performing additional logic via external processes like sanity checks. This assumption must be tested and validated to avoid unforeseen complexities.

### Useful Links

- [LSP Specification](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/)
- [Pygls GitHub](https://github.com/openlawlibrary/pygls)
- [VSCode Extension Anatomy](https://code.visualstudio.com/api/get-started/extension-anatomy)
