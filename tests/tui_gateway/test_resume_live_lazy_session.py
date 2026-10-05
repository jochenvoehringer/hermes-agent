"""Tests: session.resume finds LIVE lazy (never-persisted) sessions.

session.create intentionally writes no state.db row until the first prompt.
Bot Mode creates every fresh non-default bot's canonical Bot Chat exactly
that way (profile-scoped, lazy, hidden), then the open/send path resumes it
by stored key or pending title — which hard-404'd "session not found" for
every bot that had never spoken (community + Teknium repro, Aug 2026).

Contract:
- resume by stored session_key reattaches to the live in-memory record;
- resume by pending title reattaches likewise;
- the match is scoped to the SAME profile home — an unscoped resume of a
  profile-scoped live session still fails closed (no cross-profile leaks);
- a genuinely unknown id still returns 4007.
"""

from __future__ import annotations

from time import monotonic, sleep

import pytest

import tui_gateway.server as srv
from tui_gateway.transport import FanoutTransport, bind_transport, reset_transport


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".hermes"
    (h / "profiles" / "ops").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(h))
    return h


@pytest.fixture
def live_lazy_session(home):
    """A live registry record shaped like session.create's lazy output."""
    sid = "live-lazy-1"
    record = {
        "history": [],
        "last_active": 0.0,
        "pending_title": "Bot Chat",
        "pending_hidden": True,
        "profile_home": str(home / "profiles" / "ops"),
        "running": False,
        "session_key": "20260823_000000_abc123",
        "source": "desktop",
    }
    srv._sessions[sid] = record
    yield sid, record
    srv._sessions.pop(sid, None)


def _resume(params):
    return srv._methods["session.resume"](1, params)


def test_resume_by_stored_key_reattaches(live_lazy_session):
    sid, record = live_lazy_session
    out = _resume({"profile": "ops", "session_id": record["session_key"], "omit_messages": True})
    assert "error" not in out, out
    assert out["result"]["session_id"] == sid
    assert out["result"]["stored_session_id"] == record["session_key"]


def test_resume_by_pending_title_reattaches(live_lazy_session):
    sid, _record = live_lazy_session
    out = _resume({"profile": "ops", "session_id": "Bot Chat", "omit_messages": True})
    assert "error" not in out, out
    assert out["result"]["session_id"] == sid


def test_unscoped_resume_of_profile_session_fails_closed(live_lazy_session):
    _sid, record = live_lazy_session
    out = _resume({"session_id": record["session_key"], "omit_messages": True})
    assert out.get("error", {}).get("code") == 4007


def test_unknown_id_still_404s(home):
    out = _resume({"profile": "ops", "session_id": "ghost-9999", "omit_messages": True})
    assert out.get("error", {}).get("code") == 4007


def test_live_ios_observers_survive_idle_reap_scan_and_resume(live_lazy_session, monkeypatch):
    """A live observer prevents cold reap; resume joins the existing fanout."""
    sid, record = live_lazy_session

    class Peer:
        _closed = False

        def __init__(self):
            self.frames = []

        def write(self, frame):
            self.frames.append(frame)
            return True

        def close(self):
            self._closed = True

    ios_observer, desktop_observer, reconnecting_ios = Peer(), Peer(), Peer()
    fanout = FanoutTransport(ios_observer, desktop_observer)
    record.update(transport=fanout, created_at=1.0, last_active=1.0, lazy=True)
    monkeypatch.setattr(srv, "_SESSION_TTL_S", 1.0)
    monkeypatch.setattr(srv, "_session_has_active_delegations", lambda *_args: False)

    try:
        assert srv._close_session_by_id(
            sid, end_reason="idle_timeout",
            predicate=lambda current: srv._session_is_evictable(sid, current, 100.0),
        ) is False
        assert srv._sessions[sid] is record

        token = bind_transport(reconnecting_ios)
        try:
            resumed = _resume({"profile": "ops", "session_id": record["session_key"], "omit_messages": True})
        finally:
            reset_transport(token)

        assert "error" not in resumed, resumed
        assert resumed["result"]["session_id"] == sid
        assert fanout.contains(reconnecting_ios)
        frame = {"method": "event", "params": {"type": "message.complete"}}
        assert fanout.write(frame)
        for peer in (ios_observer, desktop_observer, reconnecting_ios):
            deadline = monotonic() + 2.0
            while not peer.frames and monotonic() < deadline:
                sleep(0.001)
            assert peer.frames == [frame]
    finally:
        fanout.close()


class _Agent:
    model = "claude-opus-5-5[1m]"
    provider = "claude-subscription-directsdk-experimental"


@pytest.mark.parametrize("shape", ["override_only", "built_agent", "pending_switch"])
def test_live_reattach_reports_the_sessions_own_model_not_the_profile_default(live_lazy_session, monkeypatch, shape):
    """A reload of a still-live session must not flip the Desktop picker to the config default. The eager
    (cold) resume already reports the chat's own model; the warm reattach reported `_resolve_model()`, so
    the pick a user made in the composer appeared to change at random depending on whether the backend had
    dropped the session in between."""
    sid, record = live_lazy_session
    monkeypatch.setattr(srv, "_resolve_model", lambda: "z-ai/glm-5.2")
    expected = ("claude-opus-5-5[1m]", "claude-subscription-directsdk-experimental")
    if shape == "override_only":
        record["model_override"] = {"model": expected[0], "provider": expected[1]}
    elif shape == "built_agent":
        record["agent"] = _Agent()
    else:
        record["agent"] = _Agent()
        record["pending_model_switch"] = {"display_model": "claude-sonnet-5[1m]", "display_provider": expected[1]}
        expected = ("claude-sonnet-5[1m]", expected[1])
    out = _resume({"profile": "ops", "session_id": record["session_key"], "omit_messages": True})
    assert "error" not in out, out
    info = out["result"]["info"]
    assert (info["model"], info["provider"]) == expected
    assert info["lazy"] is True
