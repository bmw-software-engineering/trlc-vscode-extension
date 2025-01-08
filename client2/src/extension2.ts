import * as vscode from 'vscode';

export function activate(context: vscode.ExtensionContext) {
    const trlcExtension = vscode.extensions.getExtension('trlc.vscode-extension');

    if (trlcExtension) {
        // set formating for relevant properties like codebeamer ID, syml or commonmark
        // call codebeamer to make a webview with the codebeamer returned data
    }
}

function applyDecorations(api) {
    // Format SYML and CodeBeamer elements with decorations.
    // Use the original extension's API (`api.getHoverDetails`) to fetch hover details and enhance UI.
}

function fetchCodeBeamerDetails(api) {
    // Trigger an API request to CodeBeamer and display the result.
    // Use `api.getHoverDetails` for hover metadata and link it to a webview or another UI feature.
}
