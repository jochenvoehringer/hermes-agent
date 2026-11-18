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

def test_real_server_import_installs_extensions_before_rpc_dispatch():
    import subprocess
    import sys

    result = subprocess.run([sys.executable, '-c', '''
from tui_gateway.rpc_extensions import register_rpc_extension
calls = []
def install(server):
    assert 'session.create' in server._methods
    server._methods['hoppe.test_probe'] = lambda rid, params: server._ok(rid, {'ready': True})
    calls.append(server)
register_rpc_extension('test-startup', install)
from tui_gateway import server
assert calls == [server]
assert server.handle_request({'jsonrpc': '2.0', 'id': 1, 'method': 'hoppe.test_probe', 'params': {}})['result'] == {'ready': True}
late = []
register_rpc_extension('test-late', late.append)
assert late == [server]
'''], capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
