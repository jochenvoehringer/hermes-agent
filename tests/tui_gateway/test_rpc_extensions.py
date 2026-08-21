import importlib
from types import SimpleNamespace


def _fresh_registry():
    from tui_gateway import rpc_extensions

    return importlib.reload(rpc_extensions)


def test_extension_registered_before_server_initialization_installs_synchronously():
    registry = _fresh_registry()
    installed = []
    fake_server = SimpleNamespace(_methods={})

    registry.register_rpc_extension(
        "example",
        lambda server: (
            installed.append(server) or server._methods.update(example=lambda: None)
        ),
    )
    registry.install_rpc_extensions(fake_server)

    assert installed == [fake_server]
    assert "example" in fake_server._methods


def test_extension_registered_after_server_initialization_installs_immediately():
    registry = _fresh_registry()
    installed = []
    fake_server = SimpleNamespace(_methods={})
    registry.install_rpc_extensions(fake_server)

    registry.register_rpc_extension(
        "late",
        lambda server: (
            installed.append(server) or server._methods.update(late=lambda: None)
        ),
    )

    assert installed == [fake_server]
    assert "late" in fake_server._methods


def test_duplicate_extension_name_is_idempotent_before_and_after_initialization():
    registry = _fresh_registry()
    installed = []
    fake_server = SimpleNamespace()

    registry.register_rpc_extension("same", lambda _server: installed.append("first"))
    registry.register_rpc_extension(
        "same", lambda _server: installed.append("duplicate")
    )
    registry.install_rpc_extensions(fake_server)
    registry.register_rpc_extension("same", lambda _server: installed.append("late"))

    assert installed == ["first"]


def test_failed_extension_does_not_block_core_or_later_retry():
    registry = _fresh_registry()
    attempts = []
    installed = []
    fake_server = SimpleNamespace()

    def flaky(_server):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("not ready")
        installed.append("flaky")

    registry.install_rpc_extensions(fake_server)
    registry.register_rpc_extension("flaky", flaky)
    registry.register_rpc_extension(
        "healthy", lambda _server: installed.append("healthy")
    )
    registry.register_rpc_extension(
        "flaky", lambda _server: installed.append("duplicate")
    )

    assert installed == ["healthy", "flaky"]
    assert len(attempts) == 2
