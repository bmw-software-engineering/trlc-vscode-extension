# TRLC visual studio code extension

`trlc-vscode` is a visual studio code extension that provides editing
features like syntax highlighting, auto completion and error checking
for TRLC files. Get more information about
[TRLC](https://github.com/bmw-software-engineering/trlc/).

## Installation

1. Install [Python](https://www.python.org/downloads/): 3.8 <= Python <= 3.12.
2. Download the `*.vsix` file under the `Assets` tab of the [latest Release](https://github.com/bmw-software-engineering/trlc-vscode-extension/releases/latest) of the extension.
3. Press `F1` in VSCode, type `Extensions: Install from VSIX...` and install the extension.
4. If it is not working out of the box, go to VSCode Settings, search for `python.defaultInterpreterPath` and make sure it leads to your installed python executable.

**IF** you are reinstalling the extension. Press `F1` and make sure to use command: `TRLC: Reset CVC5 Setup` once.

## How to switch from partial (default) to full parsing.

This extension offers `partial` and `full` parsing of **TRLC** files.
In `partial` mode, only a subset of files within the current workspace or
folder is parsed — specifically, the ones you have opened in your editor. On
the other hand, in `full` mode, the entire workspace or folder is parsed,
resulting in a longer processing time.

1. Open the Settings either through the gear icon or through the menu:
   - On Windows/Linux, go to `File > Preferences > Settings`.
   - On macOS, go to `Code > Preferences > Settings`.

2. Search for `trlc` using the search bar at the top.

3. Modify the setting `Trlc Server: Parsing` and enter either **full** or
**partial**

1. Close the Settings, there is no need for saving.

2. Press any key on the keyboard in any TRLC file and the updated settings will
take effect.

**Note:** The last step is necessary as Visual Studio Code applies the setting only
when a change is made in a file.

## Development

### Dependencies

The extension has three dependencies:  
1. [pygls](https://pypi.org/project/pygls) - A Python Language Server implementation for the Language Server Protocol. -> bundled into the extension.
2. [TRLC](https://github.com/bmw-software-engineering/trlc) - A tool for the formal verification of requirements. -> bundled into the extension.
3. [CVC5](https://cvc5.github.io) - An open-source automatic theorem prover for SMT theories. -> will be installed automatically after the extension is installed. When reinstalling the extension, use the command `TRLC: Reset CVC5 Setup` to reinstall CVC5 (reason why it is not bundled: there are different versions for different operating systems).

### Build from source

1. Use [make](https://www.gnu.org/software/make/) to
   install required python packages.

   ```bash
   make install-python-deps
   ```
   **NOTE**: Use only python 3.9.X to create the dependency packages.

2. Make sure you have `Node.js` installed. You will likely need to
   upgrade from the system installed one on Debian/Ubuntu (as root):

   ```bash
   cd ~
   curl -sL https://deb.nodesource.com/setup_18.x -o /tmp/nodesource_setup.sh
   sudo bash /tmp/nodesource_setup.sh
   sudo apt-get install -y nodejs
   ```

3. Then use [make](https://www.gnu.org/software/make/) again to build the extension.

   ```bash
   make build
   ```
   **OR**
   ```bash
   make install
   ```
   to build and automatically install the extension.

### Run extension in debugger mode

1. Open the source code in VS Code.
2. Run `npm install` in this folder.
3. Open debug view (ctrl + shift + D).
4. Select Server + Client and press F5.


## Copyright and License

The TRLC VSCode Extension is licensed under the [GPL-3.0](LICENSE) and
the main copyright holder is the Bayerische Motoren Werke
Aktiengesellschaft (BMW AG).

Parts of the extension are derived from the samples provided by
[pygls](https://pypi.org/project/pygls) (licensed under the Apache 2.0
license) and the Microsoft Corporation (also licensed under the Apache
2.0 license).
