import threading
from types import SimpleNamespace

from tui_gateway import server
from tui_gateway.session_subscribers import SessionSubscriberHub
from tui_gateway.transport import bind_transport, reset_transport


class RecordingTransport:
    def __init__(self, *, succeeds: bool = True):
        self.frames = []
        self.succeeds = succeeds

    def write(self, frame):
        self.frames.append(frame)
        return self.succeeds

    def close(self):
        return None


def test_broadcast_reaches_each_subscriber_once_and_disconnect_is_local():
    hub = SessionSubscriberHub()
    first, second = RecordingTransport(), RecordingTransport()
    hub.subscribe("app-root", "runtime-1", first)
    hub.subscribe("app-root", "runtime-1", second)

    assert hub.broadcast_secondary("runtime-1", first, {"method": "event"}) is True
    assert len(first.frames) == 0
    assert len(second.frames) == 1

    hub.unsubscribe_transport(first)
    hub.broadcast_secondary("runtime-1", None, {"method": "event-2"})
    assert len(first.frames) == 0
    assert len(second.frames) == 2


def test_runtime_rotation_preserves_chat_subscribers():
    hub = SessionSubscriberHub()
    transport = RecordingTransport()
    hub.subscribe("app-root", "runtime-old", transport)
    hub.move_runtime("runtime-old", "runtime-new", "app-root")

    assert hub.broadcast_secondary("runtime-new", None, {"method": "event"}) is True
    assert len(transport.frames) == 1


def test_idle_reap_then_new_runtime_preserves_other_observers():
    hub = SessionSubscriberHub()
    sender, observer = RecordingTransport(), RecordingTransport()
    hub.subscribe("app-root", "runtime-old", sender)
    hub.subscribe("app-root", "runtime-old", observer)
    hub.detach_runtime("runtime-old")

    hub.subscribe("app-root", "runtime-new", sender)
    hub.broadcast_secondary("runtime-new", sender, {"method": "event"})
    assert len(observer.frames) == 1


def test_scope_broadcast_reaches_clients_without_opening_that_chat():
    hub = SessionSubscriberHub()
    first, second = RecordingTransport(), RecordingTransport()
    hub.subscribe_scope("router:ios:personal", first)
    hub.subscribe_scope("router:ios:personal", second)

    assert (
        hub.broadcast_scope("router:ios:personal", {"method": "hoppe.chat.running"})
        is True
    )
    assert len(first.frames) == 1
    assert len(second.frames) == 1


def test_failed_subscriber_does_not_abort_peers_and_is_removed():
    hub = SessionSubscriberHub()
    failed, healthy = RecordingTransport(succeeds=False), RecordingTransport()
    hub.subscribe("app-root", "runtime-1", failed)
    hub.subscribe("app-root", "runtime-1", healthy)

    assert hub.broadcast_secondary("runtime-1", None, {"method": "first"}) is True
    assert len(failed.frames) == 1
    assert len(healthy.frames) == 1

    assert hub.broadcast_secondary("runtime-1", None, {"method": "second"}) is True
    assert len(failed.frames) == 1
    assert len(healthy.frames) == 2


def test_subscriber_disconnect_does_not_change_primary_transport(monkeypatch):
    primary = RecordingTransport()
    observer = RecordingTransport()
    session = {
        "transport": primary,
        "running": False,
        "close_on_disconnect": False,
    }
    monkeypatch.setattr(server, "_sessions", {"runtime-1": session})
    monkeypatch.setattr(server, "_WS_ORPHAN_REAP_GRACE_S", 0)
    server._session_subscribers.subscribe("app-root", "runtime-1", observer)

    server._session_subscribers.unsubscribe_transport(observer)

    assert session["transport"] is primary


def test_emit_delivers_primary_and_each_secondary_exactly_once(monkeypatch):
    hub = SessionSubscriberHub()
    primary, observer = RecordingTransport(), RecordingTransport()
    session = {"session_key": "runtime-1", "transport": primary}
    monkeypatch.setattr(server, "_sessions", {"ui-1": session})
    monkeypatch.setattr(server, "_session_subscribers", hub)
    hub.subscribe("app-root", "runtime-1", primary)
    hub.subscribe("app-root", "runtime-1", observer)

    server._emit("message.delta", "ui-1", {"text": "hello"})

    assert len(primary.frames) == 1
    assert observer.frames == primary.frames
    frame = primary.frames[0]
    assert frame["jsonrpc"] == "2.0"
    assert frame["method"] == "event"
    assert frame["params"]["type"] == "message.delta"
    assert frame["params"]["session_id"] == "ui-1"
    assert frame["params"]["payload"] == {"text": "hello"}
    assert isinstance(frame["params"]["seq"], int)
    assert frame["params"]["seq"] > 0


def test_emit_uses_session_primary_precedence_when_observer_context_is_bound(
    monkeypatch,
):
    hub = SessionSubscriberHub()
    primary, observer = RecordingTransport(), RecordingTransport()
    session = {"session_key": "runtime-1", "transport": primary}
    monkeypatch.setattr(server, "_sessions", {"ui-1": session})
    monkeypatch.setattr(server, "_session_subscribers", hub)
    hub.subscribe("app-root", "runtime-1", primary)
    hub.subscribe("app-root", "runtime-1", observer)

    token = bind_transport(observer)
    try:
        server._emit("message.delta", "ui-1", {"text": "hello"})
    finally:
        reset_transport(token)

    assert len(primary.frames) == 1
    assert observer.frames == primary.frames
    frame = primary.frames[0]
    assert frame["params"]["type"] == "message.delta"
    assert frame["params"]["session_id"] == "ui-1"
    assert frame["params"]["payload"] == {"text": "hello"}
    assert isinstance(frame["params"]["seq"], int)
    assert frame["params"]["seq"] > 0


def test_compute_host_rpc_forwarding_delivers_secondary_once(monkeypatch):
    hub = SessionSubscriberHub()
    primary, observer = RecordingTransport(), RecordingTransport()
    session = {"session_key": "runtime-1", "transport": primary}
    monkeypatch.setattr(server, "_sessions", {"ui-1": session})
    monkeypatch.setattr(server, "_session_subscribers", hub)
    hub.subscribe("app-root", "runtime-1", primary)
    hub.subscribe("app-root", "runtime-1", observer)
    frame = server._event_frame("message.delta", "ui-1", {"text": "host"})

    server._forward_compute_host_rpc(frame)

    assert primary.frames == [frame]
    assert observer.frames == [frame]


def test_compute_host_rotation_moves_parent_runtime_binding(monkeypatch):
    hub = SessionSubscriberHub()
    primary, observer = RecordingTransport(), RecordingTransport()
    session = {"session_key": "runtime-old", "transport": primary}
    monkeypatch.setattr(server, "_sessions", {"ui-1": session})
    monkeypatch.setattr(server, "_session_subscribers", hub)
    hub.subscribe("app-root", "runtime-old", primary)
    hub.subscribe("app-root", "runtime-old", observer)
    frame = server._event_frame(
        "hoppe.chat.session_rotated",
        "ui-1",
        {"app_chat_id": "app-root", "session_id": "runtime-new"},
    )

    server._forward_compute_host_rpc(frame)

    assert primary.frames == [frame]
    assert observer.frames == [frame]
    assert session["session_key"] == "runtime-new"
    assert hub.broadcast_secondary("runtime-old", None, {"method": "old"}) is False
    assert hub.broadcast_secondary("runtime-new", primary, {"method": "new"}) is True
    assert observer.frames[-1] == {"method": "new"}


class _ResumeDB:
    def __init__(self, target: str, cwd: str, *, source: str):
        self.target = target
        self.cwd = cwd
        self.source = source
        self.closed = False

    def get_session(self, session_id):
        if session_id != self.target:
            return None
        return {
            "id": self.target,
            "cwd": self.cwd,
            "message_count": 0,
            "source": self.source,
        }

    def get_session_by_title(self, _title):
        return None

    def resolve_resume_session_id(self, session_id):
        return session_id

    def assert_resume_safe(self, _session_id):
        return None

    def reopen_session(self, _session_id):
        return None

    def get_resume_conversations(self, _session_id):
        return [], []

    def get_messages_as_conversation(self, _session_id, **_kwargs):
        return []

    def get_ancestor_display_prefix(self, _session_id):
        return []

    def get_compression_conversation(self, _session_id):
        if self.closed:
            raise RuntimeError("database already closed")
        return "app-root", self.target, ["app-root", self.target]


def _resume_with_transport(
    monkeypatch,
    tmp_path,
    *,
    source: str,
    defer_history=False,
    eager_build=False,
    lazy=False,
):
    target = f"stored-{source}"
    db = _ResumeDB(target, str(tmp_path), source=source)
    hub = SessionSubscriberHub()
    desktop = RecordingTransport()
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server, "_session_subscribers", hub)
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_profile_home", lambda _profile: None)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_maybe_schedule_auto_continue", lambda *_args: False)
    monkeypatch.setattr(
        server,
        "_schedule_resume_hydration",
        lambda *_args, **_kwargs: setattr(db, "closed", True),
    )
    if eager_build:
        winner = server._deferred_session_record(
            target,
            cols=80,
            cwd=str(tmp_path),
            history=[],
            lease=None,
            source="desktop",
        )

        def build_then_lose_race(*_args, **_kwargs):
            server._sessions["winner-ui"] = winner
            return SimpleNamespace(close=lambda: None)

        monkeypatch.setattr(server, "_make_agent", build_then_lose_race)
        monkeypatch.setattr(server, "_set_session_context", lambda _target: [])
        monkeypatch.setattr(server, "_clear_session_context", lambda _tokens: None)

    token = bind_transport(desktop)
    try:
        response = server._methods["session.resume"](
            "resume-desktop",
            {
                "session_id": target,
                "source": "desktop",
                "defer_history": defer_history,
                "eager_build": eager_build,
                "lazy": lazy,
            },
        )
    finally:
        reset_transport(token)
    return response, hub, desktop, target


def test_desktop_resume_subscribes_only_durable_ios_sessions(monkeypatch, tmp_path):
    response, hub, desktop, runtime_id = _resume_with_transport(
        monkeypatch, tmp_path, source="ios"
    )

    assert response["result"]["session_key"] == runtime_id
    sid = response["result"]["session_id"]
    assert server._sessions[sid]["source"] == "desktop"
    assert server._sessions[sid]["app_chat_id"] == "app-root"
    assert hub.broadcast_secondary(runtime_id, None, {"method": "event"}) is True
    assert desktop.frames == [{"method": "event"}]


def test_desktop_resume_does_not_subscribe_non_ios_session(monkeypatch, tmp_path):
    _response, hub, desktop, runtime_id = _resume_with_transport(
        monkeypatch, tmp_path, source="desktop"
    )

    assert hub.broadcast_secondary(runtime_id, None, {"method": "event"}) is False
    assert desktop.frames == []


def test_deferred_desktop_resume_subscribes_before_hydration_closes_db(
    monkeypatch, tmp_path
):
    response, hub, desktop, runtime_id = _resume_with_transport(
        monkeypatch, tmp_path, source="ios", defer_history=True
    )

    assert response["result"]["hydrating"] is True
    assert hub.broadcast_secondary(runtime_id, None, {"method": "event"}) is True
    assert desktop.frames == [{"method": "event"}]


def test_lazy_desktop_resume_subscribes_durable_ios_session(monkeypatch, tmp_path):
    response, hub, desktop, runtime_id = _resume_with_transport(
        monkeypatch, tmp_path, source="ios", lazy=True
    )

    sid = response["result"]["session_id"]
    assert server._sessions[sid]["app_chat_id"] == "app-root"
    assert hub.broadcast_secondary(runtime_id, None, {"method": "event"}) is True
    assert desktop.frames == [{"method": "event"}]


def test_eager_resume_race_registers_desktop_and_stable_chat_metadata(
    monkeypatch, tmp_path
):
    response, hub, desktop, runtime_id = _resume_with_transport(
        monkeypatch, tmp_path, source="ios", eager_build=True
    )

    assert response["result"]["session_id"] == "winner-ui"
    assert server._sessions["winner-ui"]["app_chat_id"] == "app-root"
    assert hub.broadcast_secondary(runtime_id, None, {"method": "event"}) is True
    assert desktop.frames == [{"method": "event"}]


def test_desktop_resumed_ios_inline_compression_rotates_stable_chat(
    monkeypatch, tmp_path
):
    response, hub, desktop, runtime_id = _resume_with_transport(
        monkeypatch, tmp_path, source="ios"
    )
    sid = response["result"]["session_id"]
    session = server._sessions[sid]
    app = RecordingTransport()
    hub.subscribe("app-root", runtime_id, app)
    session["transport"] = app
    session["agent"] = SimpleNamespace(session_id="runtime-new")
    monkeypatch.setattr(
        server, "_transfer_active_session_slot", lambda *_args, **_kwargs: True
    )

    monkeypatch.setattr(
        server,
        "_session_db",
        lambda _session: (_ for _ in ()).throw(
            AssertionError("stable metadata must avoid source/DB inference")
        ),
    )

    server._sync_session_key_after_compress(
        sid, session, clear_pending_title=False, restart_slash_worker=False
    )

    assert len(app.frames) == 1
    assert desktop.frames == app.frames
    frame = app.frames[0]
    assert frame["jsonrpc"] == "2.0"
    assert frame["method"] == "event"
    assert frame["params"]["type"] == "hoppe.chat.session_rotated"
    assert frame["params"]["session_id"] == sid
    assert frame["params"]["payload"] == {
        "app_chat_id": "app-root",
        "session_id": "runtime-new",
    }
    assert isinstance(frame["params"]["seq"], int)
    assert frame["params"]["seq"] > 0
    assert hub.broadcast_secondary(runtime_id, None, {"method": "old"}) is False


def test_desktop_resumed_ios_isolated_rotation_reaches_app_and_desktop(
    monkeypatch, tmp_path
):
    response, hub, desktop, runtime_id = _resume_with_transport(
        monkeypatch, tmp_path, source="ios"
    )
    sid = response["result"]["session_id"]
    session = server._sessions[sid]
    app = RecordingTransport()
    hub.subscribe("app-root", runtime_id, app)
    session["transport"] = app
    frame = server._event_frame(
        "hoppe.chat.session_rotated",
        sid,
        {"app_chat_id": "app-root", "session_id": "runtime-new"},
    )

    server._forward_compute_host_rpc(frame)

    assert app.frames == [frame]
    assert desktop.frames == [frame]
    assert session["session_key"] == "runtime-new"
    assert session["app_chat_id"] == "app-root"
    assert hub.broadcast_secondary("runtime-old", None, {"method": "old"}) is False
    assert hub.broadcast_secondary("runtime-new", app, {"method": "new"}) is True
    assert desktop.frames[-1] == {"method": "new"}


def test_runtime_teardown_detaches_binding_but_preserves_subscribers(monkeypatch):
    hub = SessionSubscriberHub()
    observer = RecordingTransport()
    hub.subscribe("app-root", "runtime-old", observer)
    monkeypatch.setattr(server, "_session_subscribers", hub)
    monkeypatch.setattr(server, "_finalize_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_announce_session_reclaimed", lambda *_args: None)

    server._teardown_session({"session_key": "runtime-old", "agent": None})
    assert hub.broadcast_secondary("runtime-old", None, {"method": "detached"}) is False

    hub.bind_runtime("app-root", "runtime-new")
    assert hub.broadcast_secondary("runtime-new", None, {"method": "rebound"}) is True
    assert observer.frames == [{"method": "rebound"}]
