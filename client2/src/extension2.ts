// DISCLAIMER: The following code signals 
// CONCEPTUALLY how a new extension can be 
// plugged in into vscode-trlc-extension current code
// Make relevant changes and full testing.

import * as vscode from 'vscode';

export function activate(context: vscode.ExtensionContext) {
    // very very important step to set up dependency to original extension!
    // do not forget also to setup the package.json
    const trlcExtension = vscode.extensions.getExtension('trlc.vscode-extension');

    if (trlcExtension) {
        // set formating and/or decortations 
        // for relevant properties like codebeamer ID, syml or commonmark printing
        // call codebeamer to make a webview preview with the codebeamer returned data
        // execute separately the sanity_checks.py
        trlcExtension.activate().then((api) => {
            if (api) {
                applyDecorations(api);
                fetchCodeBeamerDetails(api);
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

function fetchCodeBeamerDetails(api) {
    // Trigger an API request to CodeBeamer and display the result.
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

    // from results (like a list of wrong asils with their file paths and list of non-referenced syml objects) of the sanity checks adjust UI to show up errors or warings
}

