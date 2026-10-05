from __future__ import annotations

from types import SimpleNamespace

from tui_gateway import server
from tui_gateway.session_subscribers import SessionSubscriberHub
from tui_gateway.transport import FanoutTransport, bind_transport, reset_transport


class _Transport:
    def __init__(self):
        self.frames = []
        self.alive = True

    def write(self, frame):
        if not self.alive:
            return False
        self.frames.append(frame)
        return True


def test_runtime_rotation_keeps_stable_chat_subscriptions():
    hub = SessionSubscriberHub()
    first = _Transport()
    second = _Transport()
    hub.subscribe("chat", "old-runtime", first)
    hub.bind_runtime("chat", "new-runtime")
    hub.subscribe("chat", "new-runtime", second)
    frame = {"method": "event"}

    assert hub.broadcast_secondary("new-runtime", None, frame)
    assert first.frames == [frame]
    assert second.frames == [frame]


def test_failed_transport_is_removed_from_every_subscription():
    hub = SessionSubscriberHub()
    transport = _Transport()
    hub.subscribe("chat", "runtime", transport)
    hub.subscribe_scope("scope", transport)
    transport.alive = False

    assert not hub.broadcast_scope("scope", {"method": "event"})
    assert not hub.broadcast_secondary("runtime", None, {"method": "event"})


def test_fanout_primary_is_not_duplicated_through_stable_subscription():
    hub = SessionSubscriberHub()
    peer = _Transport()
    hub.subscribe("chat", "runtime", peer)
    primary = FanoutTransport(peer)

    assert not hub.broadcast_secondary("runtime", primary, {"method": "event"})
    assert peer.frames == []
    primary.close()


class _ResumeDB:
    def __init__(self, target: str, cwd: str, *, source: str):
        self.target, self.cwd, self.source = target, cwd, source
        self.closed = False

    def get_session(self, session_id):
        if session_id != self.target:
            return None
        return {"id": self.target, "cwd": self.cwd, "message_count": 0, "source": self.source}

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


def _resume_ios_with_transport(monkeypatch, tmp_path, *, close_db_after_resume=True):
    target = "stored-ios"
    db = _ResumeDB(target, str(tmp_path), source="ios")
    hub = SessionSubscriberHub()
    desktop = _Transport()
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server, "_session_subscribers", hub)
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_profile_home", lambda _profile: None)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_maybe_schedule_auto_continue", lambda *_args: False)
    monkeypatch.setattr(
        server, "_schedule_resume_hydration",
        lambda *_args, **_kwargs: setattr(db, "closed", True) if close_db_after_resume else None,
    )
    token = bind_transport(desktop)
    try:
        response = server._methods["session.resume"](
            "resume-ios", {"session_id": target, "source": "ios"}
        )
    finally:
        reset_transport(token)
    return response, hub, desktop, target


def test_cold_ios_resume_rebinds_preserved_subscribers_after_idle_reap(monkeypatch, tmp_path):
    """The real resume handler, not a manual hub bind, restores cold iOS observers."""
    first, hub, desktop, durable_id = _resume_ios_with_transport(
        monkeypatch, tmp_path, close_db_after_resume=False
    )
    first_runtime = first["result"]["session_id"]
    app_b = _Transport()
    hub.subscribe("app-root", durable_id, app_b)
    monkeypatch.setattr(server, "_finalize_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_announce_session_reclaimed", lambda *_args: None)
    reaped = server._sessions.pop(first_runtime)
    reaped["_sid"] = first_runtime
    server._teardown_session(reaped, end_reason="idle_timeout")

    app_a = _Transport()
    token = bind_transport(app_a)
    try:
        resumed = server._methods["session.resume"](
            "resume-after-reap", {"session_id": durable_id, "source": "ios"}
        )
    finally:
        reset_transport(token)

    assert "result" in resumed
    assert resumed["result"]["session_id"] != first_runtime
    frame = {"method": "event", "params": {"type": "message.complete"}}
    assert hub.broadcast_secondary(durable_id, None, frame)
    assert app_a.frames == app_b.frames == desktop.frames == [frame]
