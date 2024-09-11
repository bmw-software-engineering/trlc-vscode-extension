lint: style
	@python3 -m pylint --rcfile=pylint3.cfg \
		--reports=no \
		server

style:
	@python3 -m pycodestyle server

build:
	npm install
	npx vsce package

install-python-deps:
	@python3 -m pip install --target python-deps --isolated -I --platform any --only-binary :all: pygls==1.0.2
	@python3 -m pip install --no-deps --target python-deps --isolated -I --platform any --only-binary :all: trlc>=2.0.0 pyvcg==1.0.7

install: build
	code --uninstall-extension "bmw-group.trlc-vscode-extension"
	code --install-extension trlc-vscode-extension-*.vsix

install-link: install
	rm -rf ~/.vscode/extensions/bmw-group.trlc-vscode-extension-3.?.?/server
	ln -s $(shell pwd)/server ~/.vscode/extensions/bmw-group.trlc-vscode-extension-3.?.?/
