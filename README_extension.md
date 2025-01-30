# Extending the TRLC Extension

## Introduction

This document outlines potential approaches to extend the functionality of the `trlc-vscode-extension` to address specific requirements, such as those of a third company.
Let's call it "Other Company".
Consider the case where "Other Company" has got a requirements engineering process in place that relies on TRLC,
but it has got additional needs regarding the interoperability of TRLC and some other tools,
For examples, "Other Company" might require that
the VSCode extension for TRLC is enriched with information specific to "Other Company".
Imagine their process defines a TRLC attribute called `Jira_ID`, which is the key of a Jira ticket given as `String`.
The VSCode extension shall be able to understand this `String`.
Whenever the user hovers over it with the cursor, the IDE shall offer to open that Jira ticket in a web browser window.

This use case is very specific to "Other Company".

In this document we outline how to develop an extension for Visual Studio Code that adds such a feature to the existing TRLC extension.

This document explores two options:
1. to create a fork of the extension specific to "Other Company", and
2. to implement a plugin system through a second VSCode extension. 

On both approaches the changes to the existing architecture are addressed to enable the functionalities.
In the second option modifying the core extension was minimized as much as possible by suggesting an elegant solution for it: *"exposing its data through its own `API`"*.

The second approach is the recommended approach, and we sketch the first approach only briefly.

## Context

The `trlc-vscode-extension` uses a so-called “Language Server Protocol” (LSP) as a wrapper around the TRLC api.
With this server, the extension provides features like syntax checking for `.rsl` and `.trlc` files directly in VSCode.
The heart of the VSCode extension uses an open-source tool called [**Pygls**](https://github.com/openlawlibrary/pygls).

The Language Server Protocol (LSP) is a standard created by Microsoft to simplify adding language features like auto-completion, syntax checking, and error diagnostics to any code editor.
It separates the editor's user interface (client) from language-specific logic (server),
enabling reusability of the same server across multiple editors.

## Current Architecture

The extension follows a client-server model,
utilizing the LSP to separate the editor's interface (client) from the language-specific logic (server).
The server is implemented in Python using the Pygls library, while the client is developed in TypeScript.

The client is implemented [here](https://github.com/bmw-software-engineering/trlc-vscode-extension/blob/main/client/src/extension.ts).
It handles the communication between the VSCode editor and the Python-based server.

The server is implemented [here](https://github.com/bmw-software-engineering/trlc-vscode-extension/blob/main/server/__main__.py).
It performs all the heavy lifting for parsing and analyzing `.rsl` and `.trlc` files.

The LSP operates on a client-server mode:
- The language server (provided by `pygls`) offers features like hover, diagnostics, and completion.
- The VSCode client sends requests (e.g., hover requests) and displays responses.

### Possible Approaches
We now go into the details of the two possible approaches to deal with specific needs of "Other Company".

### 1. Fork of the Extension
A "Other Company" fork of the extension provides complete control over the codebase but comes with the downside of maintaining and synchronizing changes with the upstream open-source repository.

- New handlers in `server/server.py` are needed to be added for specific checks of "Other Company".
- Full control of current file parsing/processing as a post-processing step is given, and new handlers within existing parsing can be added.
- No API needs to be exposed nor implemented.

The code below illustrates how to add features specific to "Other Company" to the code.

```python
class OtherCompanySpecificChecks:
    def __init__(self, server):
        """
        Initialize the handler specific to the needs of 'Other Company'.
        """
        self.server = server

    async def fetch_jira_preview(self, jira_id):
        # Simulate async API call for sneak preview
        print(f"Fetching Jira preview for ID: {jira_id}")

    def validate_signals(self, uri, content):
        # Add signal parsing and validation logic here
        print(f"Validating signals in: {uri}")

    def validate_asil_levels(self, uri, content):
        # Add ASIL validation logic here
        print(f"Validating ASIL levels in: {uri}")

    def pretty_print(self, uri):
        # Add pretty-printing logic here
        print(f"Pretty-printing {uri}")
```

This code has to be made known to `TrlcLanguageServer` in [server.py](https://github.com/bmw-software-engineering/trlc-vscode-extension/blob/main/server/server.py).
Create a new instance in its `__init__` function:
```python
def __init__(self, *args):
  # ...
  self.other_company_checks = OtherCompanySpecificChecks(self)
  # ...
```

Then extend its [validate](https://github.com/bmw-software-engineering/trlc-vscode-extension/blob/b7f74006af0f1e17dd1171af82b0b3bc4f1f7de9/server/server.py#L105) function:
```python
def validate(self):
  # ..
  for uri, document in self.workspace.documents.items():
    if uri.endswith('.trlc'):
        print(f"Post-parsing checks for: {uri}")

        # Call "Other Company" specific methods
        self.other_company_checks.validate_signals(uri, document.source)
        self.other_company_checks.validate_asil_levels(uri, document.source)
      # more methods...
```

Also update function [did_change](https://github.com/bmw-software-engineering/trlc-vscode-extension/blob/b7f74006af0f1e17dd1171af82b0b3bc4f1f7de9/server/server.py#L269):
```python
@trlc_server.feature(TEXT_DOCUMENT_DID_CHANGE)
async def did_change(ls, params: DidChangeTextDocumentParams):
    # ...
    # Perform post-parsing checks specific to "Other Company"
    if uri.endswith('.trlc'):
        ls.other_company_checks.validate_signals(uri, content)
        ls.other_company_checks.validate_asil_levels(uri, content)
        # MORE FEATURES...

    # Publish diagnostics after company-specific checks
    ls.publish_diagnostics(uri, [])
```

_Disclaimer_: The changes presented above try to point into the most probable code positions and functions to be edited to make specific enhancements.

### 2. Plugin System for Extensibility
The second (and recommended) option is to enhance the existing TRLC extension to expose an API, which can then be accessed by any other VSCode extension.
"Other Company" would then develop its own VSCode extension and simply access that API.

This allows isolating almost completely the original `vscode-trlc-extension`.
"Other Company" specific logic would be packaged into a new `vscode-trlc-other-company-expansion-extension` that the original open-source extension dynamically feeds through a yet-to-be implemented API.
This avoids modifying the core extension, but requires compatibility management.
And depending on the complexity of the future features this could require its own LSP.
At the moment of writing this document and the imagined use case,
a "*client-only*" extension seems to be enough to fulfill the task.
Nonetheless, a second LSP would provide advanced language processing possibilities for "Other Company".

To enable communication with the current TRLC extension,
the current client needs to expose an API.
And currently this is **not the case**, but a proper API could be built in [extension.ts](https://github.com/bmw-software-engineering/trlc-vscode-extension/blob/main/client/src/extension.ts).
Microsoft offers examples on this topic, see [VSCode API](https://code.visualstudio.com/api/references/vscode-api#extensions:~:text=open%20was%20successful.-,extensions,-Namespace%20for%20dealing).

### API Example

In the current `extension.ts`, the TRLC data should be exposed in the `activate` function as follows:

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

## Implementation Outline

This section outlines the implementation.

1. Expand the current client to expose an API with the relevant data.
   All TRLC objects must be returned by the API.
2. The new plug-in extension for "Other Company" must load after the TRLC extension has loaded.
   Establish this dependency via the file `package.json` in your new `vscode-trlc-other-company-expansion-extension`:
   ```json
   {
     "extensionDependencies": ["bmw.trlc-vscode-extension"]
   }
   ```

### Details
In the TRLC extension, edit `client/src/extension.ts` to expose a function like `getHoverDetails(uri: string, position: Position)` in its `activate` function,
which sends a `textDocument/hover` request to the language server via `client.sendRequest` and returns hover data.

So at the end of [activate](https://github.com/bmw-software-engineering/trlc-vscode-extension/blob/b7f74006af0f1e17dd1171af82b0b3bc4f1f7de9/client/src/extension.ts#L139) insert this piece of code:
```typescript
    // Expose API when hovering - this makes functions like "getHoverDetails" etc. available to other extensions
    const api = {
        async getHoverDetails(uri: string, position: Position): Promise<Hover | null> {
            const params = { textDocument: { uri }, position };
            return await client.sendRequest("textDocument/hover", params);
        },
        async getTrlcObject(uri: string, position: Position): Promise<CBIDDetails | null> {
            // Placeholder: Implement logic to retrieve TRLC object at curser position
        },
        // add more exposures as needed
    };

    // Export the API to make it available to other extensions
    // and terminate properly when done
    // see: https://stackoverflow.com/questions/55554018/purpose-for-subscribing-a-command-in-vscode-extension
    context.subscriptions.push(client);
    context.exports = api;
    return api;
```

In the language server (`server/server.py`), ensure the `hover` function,
registered with `@trlc_server.feature(TEXT_DOCUMENT_HOVER)`,
processes requests by identifying tokens under the cursor and returns relevant details in a `Hover` object.
In other words, within `server.py`, the hover function decorated with
`@trlc_server.feature(TEXT_DOCUMENT_HOVER)`
intercepts the `textDocument/hover` request from `extension.ts` (the client).
Finally, use `context.subscriptions` for proper lifecycle management (ending) and assign the extended API object to `context.exports` to make it accessible to any other extension.

In the new extension (`other-company-extension.ts`), use the exposed API by accessing `trlc-vscode-extension` via `vscode.extensions.getExtension`,
calling `getHoverDetails` to fetch hover data,
and enhancing functionality, such as formatting text using `TextEditorDecorationType` [see here](https://code.visualstudio.com/api/references/vscode-api#window.createTextEditorDecorationType:~:text=createTextEditorDecorationType(options%3A%20DecorationRenderOptions)%3A%20TextEditorDecorationType) or making API requests to external services like Jira.

`other-company-extension.ts` could look like this:
```typescript
import * as vscode from 'vscode';

export function activate(context: vscode.ExtensionContext) {
    // very important step to set up dependency to TRLC extension!
    // do not forget also to set it up in package.json
    const trlcExtension = vscode.extensions.getExtension('trlc.vscode-extension');

    if (trlcExtension) {
        // - set formating and/or decortations for relevant properties like Jira ID etc
        // - call web browser to open a webview preview with the Jira ticket
        // - call an external Python script called 'sanity checks'
        trlcExtension.activate().then((api) => {
            if (api) {
                applyDecorations(api);
                fetchJiraDetails(api);
                setupSanityChecks(context, api);
            }
        }).catch((error) => {
            console.error("Failed to activate trlc-vscode-extension:", error);
        });
    } else {
        console.warn("trlc-vscode-extension not found.");
    }
}


function applyDecorations(api) {
    // Format in UI any SYML and CodeBeamer elements with decorations.
    // Use the original extension's API (`api.getHoverDetails`) for example
    // to fetch hover details and enhance UI.
}

function fetchJiraDetails(api) {
    // Trigger an API request to JIra and display the result.
}


function setupSanityChecks(context: vscode.ExtensionContext, api: any) {
    // Set up triggers for running sanity checks, e.g., upon saving the file
    // if condition is met, then run "runSanityChecks" with the 
    // worskpace path as argument for example
    runSanityChecks(vscode.workspace.workspaceFolders);

}

function runSanityChecks(workspaceFolders: readonly vscode.WorkspaceFolder[] | undefined) {
    const workspacePath = workspaceFolders;
    const scriptPath = '/path/to/sanity_checks.py';

    // run the CLI Tool with python from client
    const command = `python ${scriptPath} "${workspacePath}"`;

    // now adjust UI to show up errors or warnings
    // ...
}
```

### Use Case Suggestions

Independently of the approach (fork or extension) the following suggestions apply.

All the following suggestions are thought to be implemented on the client's side in `TypeScript` and it does not mean that these cannot be done on the server's side.
They are just focused on minimizing changes to the original codebase and to avoid the development of a custom Language Server Protocol (LSP).
It is just a possible design approach/suggestion.

#### 1. Web Preview from Jira

In order to show the contents of a Jira ticket (when hoovered or any other desired trigger),
we would need to make an HTTP request to the Jira server. If done with server, expose html contents through the API or if done with client handle the request with Node.js directly.
In either way the response has to be "rendered" properly into VSCode.

You can use `Webviews` to create a browser-like rendering environment, see [createWebviewPanel](https://code.visualstudio.com/api/references/vscode-api)

This is how the code could look like:

```javascript
// client's side
export function activate(context: vscode.ExtensionContext) {
    let disposable = vscode.commands.registerCommand('extension.fetchAndRenderHtml', async () => {
        const url = 'https://example.com';
        
        try {
            // handle log in
            // Fetch webpage's content
            const response = await fetch(url);

            // Create and show a new Webview panel
            const panel = vscode.window.createWebviewPanel(
                'htmlPreview', // Identifier
                'HTML Preview', // Title
                // other properties if needed....
            );

            // Set the HTML content of the Webview
            panel.webview.html = html;
        } catch (err) {
            vscode.window.showErrorMessage(`Error fetching data: ${err.message}`);
        }
    });

    context.subscriptions.push(disposable);
}

```

The previous suggestion is assuming a simple command identifier `fetchAndRenderHtml` that could be programmatically triggered.
But the proper event listener must yet be at best defined,
like hover over a TRLC object or clicking it.

#### 2. Pretty-printing TRLC files

Maybe "Other Company" decided to allow CommonMark in the `string` of a TRLC value.
To nicely print that there is an open source tool called [`markdown-it`](https://github.com/markdown-it/markdown-it).
It is implemented in `Node.js` and therefore can be integrated into the client inside VSCode.
It can interpret text formatted as common mark, like: `\*\*Some string in bold\*\*` could be rendered to **Some string in bold**.
This, of course, implies **first** to adjust/edit the desired strings before giving those to `markdown-it`.
View a demo [here](https://markdown-it.github.io/).

#### 3. Highlighting External References

If the extension of "Other Company" shall apply additional styling to the TRLC files, then most probably the `TextEditorDecorationType` can be used.

Here is a basic example:
```javascript
import * as vscode from 'vscode';

export function activate(context: vscode.ExtensionContext) {
    // Set up a trigger, like when file is opened.
    // set some styling
        const redText = vscode.window.createTextEditorDecorationType({ color: 'red' });
        const blueText = vscode.window.createTextEditorDecorationType({ color: 'blue' });
        const boldText = vscode.window.createTextEditorDecorationType({ fontWeight: 'bold' });

        // Regex to match the pattern {{OTHERCOMPANY:placeholder}}
        const regex = /\{\{(OTHERCOMPANY):([^}]+)\}\}/g;

        // Get text and parse it
        // ...
        // Find matches and calculate ranges
        // ...
        // Apply decorations to the editor, very similar to:
        editor.setDecorations(redText, redRanges);
        editor.setDecorations(blueText, blueRanges);
        editor.setDecorations(boldText, boldRanges);
    });

    // more code...
}
```

For more details visit Microsoft's support page in section [createTextEditorDecorationType](https://code.visualstudio.com/api/references/vscode-api#window.createTextEditorDecorationType) and its related instances.

Also see the official node.js documentation [child_process](https://nodejs.org/api/child_process.html#child_processexeccommand-options-callback)

### Conclusion

The fork approach grants direct control over the data at parsing time, making it an attractive option for a quicker solution.
However, in the long term, it creates a higher burden in terms of synchronization and maintenance.
Maintaining a fork may require continuous updates to ensure compatibility with upstream changes,
which can result in significant overhead.

On the other hand, implementing a plugin system via a second extension offers a decoupled solution.
By exposing an API of the original `trlc-vscode-extension`,
the second extension can just dynamically consume and extend functionality without tightly coupling with the core extension.
Please consider that "catching" and "exposing" the data may be tricky.
This second approach involves a steeper initial development effort, requiring both architectural adjustments to the existing extension, yet minimal, and careful planning to ensure API reliability.
Additionally, the actual need for a second Language Server Protocol (LSP) must be carefully evaluated.

### Useful Links

- [LSP Specification](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/)
- [Pygls GitHub](https://github.com/openlawlibrary/pygls)
- [VSCode Extension Anatomy](https://code.visualstudio.com/api/get-started/extension-anatomy)
