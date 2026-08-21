"""Thread-safe subscriptions for stable App chats and authorization scopes."""

from __future__ import annotations

import threading
from collections.abc import Iterable

from tui_gateway.transport import Transport


class SessionSubscriberHub:
    """Map rotating runtime ids onto stable chat-scoped transport sets."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._app_chat_by_runtime: dict[str, str] = {}
        self._transports_by_app_chat: dict[str, set[Transport]] = {}
        self._transports_by_scope: dict[str, set[Transport]] = {}

    def subscribe(
        self, app_chat_id: str, runtime_id: str, transport: Transport
    ) -> None:
        with self._lock:
            self._bind_runtime_locked(app_chat_id, runtime_id)
            self._transports_by_app_chat.setdefault(app_chat_id, set()).add(transport)

    def bind_runtime(self, app_chat_id: str, runtime_id: str) -> None:
        with self._lock:
            self._bind_runtime_locked(app_chat_id, runtime_id)

    def _bind_runtime_locked(self, app_chat_id: str, runtime_id: str) -> None:
        for stale_id, mapped_chat in list(self._app_chat_by_runtime.items()):
            if mapped_chat == app_chat_id and stale_id != runtime_id:
                del self._app_chat_by_runtime[stale_id]
        self._app_chat_by_runtime[runtime_id] = app_chat_id

    def detach_runtime(self, runtime_id: str) -> None:
        with self._lock:
            self._app_chat_by_runtime.pop(runtime_id, None)

    def unsubscribe(self, app_chat_id: str, transport: Transport) -> None:
        with self._lock:
            peers = self._transports_by_app_chat.get(app_chat_id)
            if peers is None:
                return
            peers.discard(transport)
            if not peers:
                del self._transports_by_app_chat[app_chat_id]

    def subscribe_scope(self, scope_key: str, transport: Transport) -> None:
        with self._lock:
            self._transports_by_scope.setdefault(scope_key, set()).add(transport)

    def broadcast_scope(
        self,
        scope_key: str,
        frame: dict,
        exclude: Transport | None = None,
    ) -> bool:
        with self._lock:
            targets = tuple(self._transports_by_scope.get(scope_key, ()))
        return self._write_targets(targets, frame, exclude=exclude)

    def unsubscribe_transport(self, transport: Transport) -> None:
        with self._lock:
            for app_chat_id in list(self._transports_by_app_chat):
                peers = self._transports_by_app_chat[app_chat_id]
                peers.discard(transport)
                if not peers:
                    del self._transports_by_app_chat[app_chat_id]
            for scope_key in list(self._transports_by_scope):
                peers = self._transports_by_scope[scope_key]
                peers.discard(transport)
                if not peers:
                    del self._transports_by_scope[scope_key]

    def move_runtime(
        self, old_runtime_id: str, new_runtime_id: str, app_chat_id: str
    ) -> None:
        with self._lock:
            self._app_chat_by_runtime.pop(old_runtime_id, None)
            self._bind_runtime_locked(app_chat_id, new_runtime_id)

    def broadcast_secondary(
        self,
        runtime_id: str,
        primary: Transport | None,
        frame: dict,
    ) -> bool:
        with self._lock:
            app_chat_id = self._app_chat_by_runtime.get(runtime_id)
            targets = tuple(self._transports_by_app_chat.get(app_chat_id, ()))
        return self._write_targets(targets, frame, exclude=primary)

    def _write_targets(
        self,
        targets: Iterable[Transport],
        frame: dict,
        *,
        exclude: Transport | None,
    ) -> bool:
        wrote = False
        failed: list[Transport] = []
        for transport in targets:
            if transport is exclude:
                continue
            try:
                ok = transport.write(frame)
            except Exception:
                ok = False
            if ok:
                wrote = True
            else:
                failed.append(transport)
        for transport in failed:
            self.unsubscribe_transport(transport)
        return wrote
