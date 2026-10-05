from __future__ import annotations

from types import SimpleNamespace

from tui_gateway import rpc_extensions


def test_extension_registered_before_server_initialization_is_installed_once(monkeypatch):
    monkeypatch.setattr(rpc_extensions, "_installers", {})
    monkeypatch.setattr(rpc_extensions, "_installed", set())
    monkeypatch.setattr(rpc_extensions, "_installing", set())
    monkeypatch.setattr(rpc_extensions, "_server", None)
    calls = []
    server = SimpleNamespace()

    rpc_extensions.register_rpc_extension("test", calls.append)
    rpc_extensions.install_rpc_extensions(server)
    rpc_extensions.install_rpc_extensions(server)

    assert calls == [server]


def test_extension_registered_after_initialization_installs_immediately(monkeypatch):
    monkeypatch.setattr(rpc_extensions, "_installers", {})
    monkeypatch.setattr(rpc_extensions, "_installed", set())
    monkeypatch.setattr(rpc_extensions, "_installing", set())
    server = SimpleNamespace()
    monkeypatch.setattr(rpc_extensions, "_server", server)
    calls = []

    rpc_extensions.register_rpc_extension("test", calls.append)

    assert calls == [server]
