# TRLC visual studio code extension

**Note: This extension is work in progress and is not yet fit for
general use.**

`trlc-vscode-extension` is a visual studio code language support extension that provides editing
features like syntax highlighting, auto completion and error checking
for the TRLC language files.  
Get more information about
[TRLC](https://github.com/bmw-software-engineering/trlc/).

## Dependencies

1. Download and Install [VSCode](https://code.visualstudio.com/download).
2. For **WINDOWS USERS** only: This extension tries to install python
   automatically!  
   **IMPORTANT**: After installing the .vsix extension file. The TRLC extension will prompt that it failed. Now **RESTART** Visual Studio Code and the extension should work as expected. If it still fails, you may need to install [python](https://www.python.org/downloads/) manually.
   Make sure to add `python` to your PATH environment variable.   
   For **LINUX** and **MACOS**: `python3` should already be in PATH. If not, install it manually and add it to PATH.

## Build from source

1. Use `make` to install required python packages:

   ```bash
   make install-python-deps
   ```

2. Make sure you have `Node.js` installed. You will likely need to
   upgrade from the system installed one on Debian/Ubuntu (as root):

   ```bash
   curl -fsSL https://deb.nodesource.com/setup_current.x | bash -
   apt-get install -y nodejs
   ```

3. Then use `make` to build the extension:

   ```bash
   make build
   ```

## Run extension in debugger mode

1. Open the source code in VSCode.
2. Run `npm install` in this folder.
3. Open debug view (ctrl + shift + D).
4. Select Server + Client and press F5.


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
