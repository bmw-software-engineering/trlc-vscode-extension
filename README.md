# TRLC visual studio code extension

`trlc-vscode` is a visual studio code extension that provides editing
features like syntax highlighting, auto completion and error checking
for TRLC files. Get more information about
[TRLC](https://github.com/bmw-software-engineering/trlc/).


## Usage

This extension offers `partial` and `full` parsing of **TRLC** files.
In `partial` mode, only a subset of files within the current workspace or
folder is parsed — specifically, the ones you have opened in your editor. On
the other hand, in `full` mode, the entire workspace or folder is parsed,
resulting in a longer processing time.

### How to switch from partial (default) to full parsing.

1. Open the Settings either through the gear icon or through the menu:
   - On Windows/Linux, go to `File > Preferences > Settings`.
   - On macOS, go to `Code > Preferences > Settings`.

2. Search for `trlc` using the search bar at the top.

3. Modify the setting `Trlc Server: Parsing` and enter either **full** or
**partial**

4. Close the Settings, there is no need for saving.

5. Press any key on the keyboard in any TRLC file and the updated settings will
take effect.

Note: The last step is necessary as Visual Studio Code applies the setting only
when a change is made in a file.


## Dependencies

1. Install [Python](https://www.python.org/downloads/) >= 3.8.
2. Download and Install [VSCode](https://code.visualstudio.com/download).
3. Create `.vscode/settings.json` file and set
   `python.defaultInterpreterPath` to point to your python
   installation.

## Build from source

1. Use the package manager [pip](https://pip.pypa.io/en/stable/) to
   install required python packages.

   ```bash
   pip install -r requirements.txt
   ```

2. Make sure you have `Node.js` installed. You will likely need to
   upgrade from the system installed one on Debian/Ubuntu (as root):

   ```bash
   curl -fsSL https://deb.nodesource.com/setup_current.x | bash -
   apt-get install -y nodejs
   ```

   Then install `vsce` by running:

   ```bash
   npm install -g @vscode/vsce
   ```

3. Use `vsce` to package the extension:

   ```bash
   cd ./trlc-new-vscode-plugin
   vsce package
   ```

## Run extension in debugger mode

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
