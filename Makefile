lint: style
	@python3 -m pylint --rcfile=pylint3.cfg \
		--reports=no \
		trlc_lsp

style:
	@python3 -m pycodestyle trlc_lsp

install-python-deps:
	python3 -m pip install -r requirements_dev.txt

build:
	npm install
	npx vsce package
	python3 -m build --wheel

install: build
	code --uninstall-extension "bmw-group.trlc-vscode-extension"
	code --install-extension trlc-vscode-extension-*.vsix

install-link: install
	rm -rf ~/.vscode/extensions/bmw-group.trlc-vscode-extension-3.?.?/trlc_lsp
	ln -s $(shell pwd)/trlc_lsp ~/.vscode/extensions/bmw-group.trlc-vscode-extension-3.?.?/
