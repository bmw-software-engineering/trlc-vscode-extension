# *******************************************************************************
# TRLC VSCode Extension
# Copyright (C) 2023 Bayerische Motoren Werke Aktiengesellschaft (BMW AG)
#
# This file is part of the TRLC VSCode Extension.
#
# The TRLC VSCode Extension is free software: you can redistribute it
# and/or modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation, either version 3 of
# the License, or (at your option) any later version.
#
# The TRLC VSCode Extension is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty
# of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with TRLC. If not, see <https://www.gnu.org/licenses/>.
# *******************************************************************************

"""Structural Protocol type for the TRLC language server.

Handler modules accept ``ls: ServerProtocol`` instead of the concrete
``TrlcLanguageServer`` class.  This means:

* Handler functions can be unit-tested by passing a lightweight
  ``FakeLanguageServer`` dataclass — no live pygls server required.
* mypy / pyright can verify that handlers only access documented
  server attributes (they cannot accidentally call private methods).
* Future refactors can change ``TrlcLanguageServer`` internals without
  touching handler signatures.

``TrlcLanguageServer`` satisfies this Protocol structurally (no base class
needed).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Protocol

from .bazel import BazelManagerCache
from .context_store import ContextStore
from .file_handler import File_Handler
from .reference_resolver import ReferenceResolver
from .server_config import ServerConfig

__all__ = ["ServerProtocol"]


class TextDocumentProtocol(Protocol):  # pylint: disable=too-few-public-methods  # structural Protocol interface, one attribute by design
    """The subset of pygls's ``TextDocument`` handlers actually read."""

    source: str


class WorkspaceProtocol(Protocol):  # pylint: disable=too-few-public-methods  # structural Protocol interface, one method by design
    """The subset of pygls's ``Workspace`` handlers actually read."""

    folders: Dict[str, Any]  # folder_uri -> WorkspaceFolder (or plain name)

    # pylint: disable-next=missing-function-docstring  # Protocol stub
    def get_text_document(self, uri: str) -> TextDocumentProtocol: ...


class HandlerModule(Protocol):  # pylint: disable=too-few-public-methods  # structural Protocol interface, one method by design
    """Structural contract for a ``handlers/*.py`` module.

    ``language_server.py`` registers each handler module by calling
    ``mod.register(self)`` on it via plain duck-typing; this Protocol lets a
    type checker verify every module in that registration tuple actually
    exposes ``register`` with the right signature, instead of only failing
    at runtime on a typo'd module.
    """

    # pylint: disable-next=missing-function-docstring  # Protocol stub
    def register(self, server: "ServerProtocol") -> None: ...


class MessagingProtocol(Protocol):
    """Surfacing messages to the client: popups + the Output-channel log.
    The narrow surface most handlers need for user-facing warnings."""

    # pylint: disable=missing-function-docstring  # Protocol stubs use ... body; docstrings would require multi-line reformat
    def window_show_message(self, params) -> None: ...

    def window_log_message(self, params) -> None: ...


class ConfigProtocol(Protocol):
    """Reading/applying server configuration."""

    config: ServerConfig

    # pylint: disable=missing-function-docstring  # Protocol stubs use ... body; docstrings would require multi-line reformat
    def apply_config(self, config) -> None: ...

    def get_config_for_uri(self, uri: str): ...


class SchedulingProtocol(Protocol):
    """Driving the parse engine's event queue/lifecycle and resolving a
    document's scope/context."""

    # pylint: disable=missing-function-docstring  # Protocol stubs use ... body; docstrings would require multi-line reformat
    def queue_event(
        self,
        kind: str,
        uri: str | None = None,
        content: str | None = None,
    ) -> None: ...

    def stop_engine(self) -> None: ...

    def uri_to_scope_id(self, uri: str) -> str | None: ...

    def get_context_for_uri(self, uri: str): ...


class DiagnosticsPublisherProtocol(Protocol):  # pylint: disable=too-few-public-methods  # structural Protocol interface, one method by design
    """The one method :class:`~server.parse_engine.ParseEngine` calls to
    publish diagnostics — the narrowest surface it actually needs from the
    server."""

    # pylint: disable-next=missing-function-docstring  # Protocol stub
    def text_document_publish_diagnostics(self, params) -> None: ...


class RegistrationProtocol(Protocol):
    """Feature/command registration surface used by each handler module's
    own ``register(server)`` (see :class:`HandlerModule`)."""

    # pylint: disable=missing-function-docstring  # Protocol stubs use ... body; docstrings would require multi-line reformat
    def feature(self, feature_id: Any, *args, **kwargs) -> Callable: ...

    def command(self, command_name: str, **kwargs) -> Callable: ...


class ServerProtocol(
    MessagingProtocol,
    ConfigProtocol,
    SchedulingProtocol,
    DiagnosticsPublisherProtocol,
    RegistrationProtocol,
    Protocol,
):
    """Structural contract for the language-server object passed to handlers
    and to :class:`~server.parse_engine.ParseEngine`.

    Composed from the narrower protocols above (messaging, config,
    scheduling, diagnostics publishing, registration) so a handler or module
    that only needs one concern can depend on that Protocol alone instead of
    this full one — this composed version stays here as the complete
    contract for code (like each handler's ``register(server)``) that
    genuinely spans several of them.

    All attributes and methods that any handler or the engine accesses on the
    server must be declared here (or on one of the Protocols above) so that:

    * Unit tests can use a lightweight ``FakeLanguageServer`` stub.
    * Static type checkers can verify that code stays within this boundary.
    """

    # ── Shared state objects ───────────────────────────────────────────
    store: ContextStore
    fh: File_Handler
    refs: ReferenceResolver
    bazel_cache: BazelManagerCache
    workspace: WorkspaceProtocol
    work_done_progress: Any  # pygls WorkDoneProgressManager
    CONFIGURATION_SECTION: str
