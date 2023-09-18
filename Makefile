build:
	npm install @vscode/vsce
	vsce package

install: build
	-code --uninstall-extension "BMW Group.trlc-vscode-extension"
	code --install-extension trlc-vscode-extension-*.vsix
