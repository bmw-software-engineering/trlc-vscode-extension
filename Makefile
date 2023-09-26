lint: style
	@python3 -m pylint --rcfile=pylint3.cfg \
		--reports=no \
		--score=no \
		server

style:
	@python3 -m pycodestyle server

build:
	npm install @vscode/vsce
	vsce package

install: build
	-code --uninstall-extension "bmw-group.trlc-vscode-extension"
	code --install-extension trlc-vscode-extension-*.vsix
