"""Synchronous registration seam for optional JSON-RPC extensions."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

Installer = Callable[[Any], None]

_lock = threading.RLock()
_installers: dict[str, Installer] = {}
_installed: set[str] = set()
_installing: set[str] = set()
_server: Any | None = None


def register_rpc_extension(name: str, installer: Installer) -> None:
    """Register *installer* once and install it immediately when initialized."""
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("RPC extension name must not be empty")
    if not callable(installer):
        raise TypeError("RPC extension installer must be callable")
    with _lock:
        if normalized not in _installers:
            _installers[normalized] = installer
        initialized = _server is not None and normalized not in _installed
    if initialized:
        _try_install(normalized)


def install_rpc_extensions(server: Any) -> None:
    """Record the initialized server and synchronously install pending entries."""
    global _server
    with _lock:
        _server = server
        names = tuple(_installers)
    for name in names:
        _try_install(name)


def _try_install(name: str) -> bool:
    with _lock:
        if name in _installed or name in _installing or _server is None:
            return name in _installed
        installer = _installers.get(name)
        if installer is None:
            return False
        server = _server
        _installing.add(name)
    try:
        installer(server)
    except Exception:
        logger.exception("RPC extension installation failed: %s", name)
        return False
    else:
        with _lock:
            _installed.add(name)
        return True
    finally:
        with _lock:
            _installing.discard(name)
