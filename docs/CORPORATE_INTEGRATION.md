# Corporate Integration Guide

**Audience: Integrator.** This document is for teams building on top of the
TRLC VSCode extension (LSP client/server API), not modifying its internals.
For internal design see [ARCHITECTURE.md](ARCHITECTURE.md); for the LSP
server's own settings/protocol surface see
[LSP_SERVER_GUIDE.md](LSP_SERVER_GUIDE.md).

This document outlines approaches to extend the TRLC VSCode extension for
third-party requirements, such as tool interoperability or custom validation.

## Scenario

"Other Company" has a requirements engineering process using TRLC but needs
additional interoperability with external tools. For example, they might
define a custom TRLC attribute `Jira_ID` (a string). When the user hovers
over it, the IDE should offer to open the corresponding Jira ticket.

Two approaches are available:

1. **Fork the extension** — Full control, but requires ongoing synchronization
   with upstream.
2. **Plugin system** (recommended) — Develop a separate VSCode extension that
   consumes a public API from the core TRLC extension. Minimal coupling,
   but requires careful API design.

---

## Approach 1: Fork

A fork provides complete control but with maintenance burden.

### Implementation outline

Add company-specific handlers to `src/server/language_server.py`:

```python
class OtherCompanySpecificChecks:
    def __init__(self, server):
        self.server = server

    async def fetch_jira_preview(self, jira_id):
        print(f"Fetching Jira preview for ID: {jira_id}")

    def validate_signals(self, uri, content):
        print(f"Validating signals in: {uri}")

    def validate_asil_levels(self, uri, content):
        print(f"Validating ASIL levels in: {uri}")
```

Register the handler in `TrlcLanguageServer.__init__`:

```python
def __init__(self, *args):
    # ...
    self.other_company_checks = OtherCompanySpecificChecks(self)
```

Extend the parse cycle in `parse_engine.py`'s `ParseEngine._validate_scope()`,
after the atomic `context.result = ParseResult(...)` swap (see
[ARCHITECTURE.md's Concurrency section](ARCHITECTURE.md#concurrency) for why
that swap must stay a single, uninterrupted assignment — insert
company-specific work after it, not inside it):

```python
context.result = ParseResult(vsm=vsm, diagnostics=new_diagnostic_state)

# Company-specific post-processing: vsm.all_files maps each parsed file's
# absolute path to its parsed File object (source_manager.py's Source_File).
for file_path in vsm.all_files:
    if file_path.endswith(".trlc"):
        uri = uri_from_file(file_path)
        self._ls.other_company_checks.validate_signals(uri, file_path)
        self._ls.other_company_checks.validate_asil_levels(uri, file_path)
```

### Trade-offs

✓ Direct access to symbol table during parse  
✓ Full control over validation timing  
✗ Requires fork maintenance  
✗ Ongoing sync burden with upstream  

---

## Approach 2: Plugin System (Recommended)

A second VSCode extension consumes a public API from the core TRLC extension.

### API Exposure

In `src/client/src/extension.ts`, expose data in the `activate` function:

```typescript
export function activate(context: vscode.ExtensionContext) {
    const api = {
        async getHoverDetails(uri: string, position: Position): Promise<Hover | null> {
            const params = { textDocument: { uri }, position };
            return await client.sendRequest("textDocument/hover", params);
        },
        
        async getTrlcSymbol(uri: string, position: Position): Promise<Symbol | null> {
            // Fetch symbol at cursor position
            const params = { textDocument: { uri }, position };
            return await client.sendRequest("custom/getTrlcSymbol", params);
        },
    };

    context.subscriptions.push(client);
    context.exports = api;
    return api;
}
```

### Companion Extension

In your company extension's `package.json`, declare a dependency:

```json
{
    "name": "vscode-trlc-company-expansion",
    "extensionDependencies": ["bmw-group.trlc-vscode-extension"]
}
```

In `extension.ts`:

```typescript
export function activate(context: vscode.ExtensionContext) {
    const trlcExt = vscode.extensions.getExtension('bmw-group.trlc-vscode-extension');
    
    if (trlcExt) {
        trlcExt.activate().then((api) => {
            if (api) {
                setupJiraIntegration(context, api);
                applyCustomHighlighting(context, api);
            }
        });
    } else {
        console.warn("TRLC extension not found");
    }
}

function setupJiraIntegration(context: vscode.ExtensionContext, api: any) {
    vscode.window.onDidChangeActiveTextEditor((editor) => {
        if (editor && editor.document.languageId === 'TRLC') {
            const uri = editor.document.uri.toString();
            api.getHoverDetails(uri, editor.selection.active)
                .then((details) => {
                    // Fetch Jira ticket if Jira_ID found
                });
        }
    });
}
```

### Trade-offs

✓ Decoupled from core extension  
✓ Zero impact on core codebase  
✓ Company logic packaged separately  
✗ Higher initial API design effort  
✗ Dependency on stable API contract  

---

## Use Case Examples

### 1. Jira Preview on Hover

Fetch Jira ticket preview when hovering over a `Jira_ID` attribute:

```typescript
async function showJiraPreview(api: any, jiraId: string) {
    const url = `https://jira.company.com/rest/api/2/issue/${jiraId}`;
    
    try {
        const response = await fetch(url, { headers: { Authorization: "..." } });
        const data = await response.json();
        
        const panel = vscode.window.createWebviewPanel(
            'jiraPreview',
            `Jira: ${jiraId}`,
            vscode.ViewColumn.Beside
        );
        
        panel.webview.html = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
    } catch (err) {
        vscode.window.showErrorMessage(`Failed to fetch Jira: ${err.message}`);
    }
}
```

### 2. Custom Highlighting

Apply decorations to company-specific constructs:

```typescript
function applyCustomHighlighting(context: vscode.ExtensionContext, api: any) {
    const jiraIdDecoration = vscode.window.createTextEditorDecorationType({
        backgroundColor: 'rgba(100, 200, 100, 0.3)',
        borderColor: 'green',
        borderWidth: '1px'
    });

    vscode.window.onDidChangeActiveTextEditor((editor) => {
        if (!editor || editor.document.languageId !== 'TRLC') return;

        const regex = /Jira_ID\s*=\s*"([^"]+)"/g;
        let match;
        const ranges = [];

        while ((match = regex.exec(editor.document.getText()))) {
            const startPos = editor.document.positionAt(match.index);
            const endPos = editor.document.positionAt(match.index + match[0].length);
            ranges.push(new vscode.Range(startPos, endPos));
        }

        editor.setDecorations(jiraIdDecoration, ranges);
    });
}
```

### 3. Custom Validation

Run company-specific checks on file save:

```typescript
function setupSanityChecks(context: vscode.ExtensionContext, api: any) {
    vscode.workspace.onDidSaveTextDocument(async (document) => {
        if (document.languageId !== 'TRLC') return;

        const workspacePath = vscode.workspace.getWorkspaceFolder(document.uri)?.uri.fsPath;
        if (!workspacePath) return;

        const scriptPath = path.join(__dirname, 'sanity_checks.py');
        const result = await exec(`python ${scriptPath} "${workspacePath}"`);

        if (result.stderr) {
            vscode.window.showWarningMessage(`Sanity check failed: ${result.stderr}`);
        }
    });
}
```

---

## Recommendation

Use **Approach 2 (plugin system)** unless you need:
- Direct control over parse timing, or
- Modifications to core diagnostics/validation logic

The plugin system keeps the core extension lean and allows multiple
independent extensions to coexist without conflicts.
