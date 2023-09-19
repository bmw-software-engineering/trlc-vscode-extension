build:
	npm install @vscode/vsce
	vsce package

install: build
	-code --uninstall-extension "bmw-group.trlc-vscode-extension"
	code --install-extension trlc-vscode-extension-*.vsix
