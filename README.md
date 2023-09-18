# TRLC visual studio code extension

`trlc-vscode` is a visual studio code extension that provides editing
features like syntax highlighting, auto completion and error checking
for TRLC files. Get more information about
[TRLC](https://github.com/bmw-software-engineering/trlc/).

## Dependencies

1. Install [Python](https://www.python.org/downloads/).
2. Use the package manager [pip](https://pip.pypa.io/en/stable/) to
   install pygls.

   ```bash
   pip install pygls
   ```

3. Download and Install [VSCode](https://code.visualstudio.com/download).
4. Download the python extension for VS Code.
5. Create `.vscode/settings.json` file and set
   `python.defaultInterpreterPath` to point to your python
   installation where `pygls` is installed.

## Build from source

1. Make sure you have `Node.js` installed. You will likely need to
   upgrade from the system installed one on Debian/Ubuntu (as root):

   ```bash
   curl -fsSL https://deb.nodesource.com/setup_current.x | bash -
   apt-get install -y nodejs
   ```

   Then install `vsce` by running:

   ```bash
   npm install -g @vscode/vsce
   ```

2. Use `vsce` to package the extension:

   ```bash
   cd ./trlc-new-vscode-plugin
   vsce package
   ```

## Run extension in debugger mode

1. Open the source code in VS Code.
2. Run `npm install` in this folder.
2. Open debug view (ctrl + shift + D).
3. Select Server + Client and press F5.


## Usage

Comming soon.

## Copyright and License

The TRLC VSCode Extension is licensed under the [GPL-3.0](LICENSE) and
the main copyright holder is the Bayerische Motoren Werke
Aktiengesellschaft (BMW AG).

Parts of the extension are derived from the samples provided by
[pygls](https://pypi.org/project/pygls) (licensed under the Apache 2.0
license) and the Microsoft Corporation (also licensed under the Apache
2.0 licene).
