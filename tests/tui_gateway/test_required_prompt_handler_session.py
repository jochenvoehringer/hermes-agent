"""Session propagation tests for the client-required pre-prompt handler."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tui_gateway import server
from tui_gateway.contracts.sessions import SessionCreateParams, SessionResumeParams


class _StoredSessionDB:
    def __init__(self, session_id: str, cwd: str):
        self.session_id = session_id
        self.cwd = cwd

    def get_session(self, session_id: str):
        if session_id != self.session_id:
            return None
        return {"id": session_id, "cwd": self.cwd, "message_count": 0}

    def get_session_by_title(self, _title: str):
        return None

    def resolve_resume_session_id(self, session_id: str) -> str:
        return session_id

    def assert_resume_safe(self, _session_id: str, **_kwargs) -> None:
        return None

    def reopen_session(self, _session_id: str) -> None:
        return None

    def get_resume_conversations(self, _session_id: str):
        return [], []

    def get_messages_as_conversation(self, _session_id: str, **_kwargs):
        return []

    def get_ancestor_display_prefix(self, _session_id: str):
        return []


def _prepare_resume(monkeypatch, tmp_path, target: str) -> None:
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server, "_get_db", lambda: _StoredSessionDB(target, str(tmp_path)))
    monkeypatch.setattr(server, "_profile_home", lambda _profile: None)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_maybe_schedule_auto_continue", lambda *_args: False)
    monkeypatch.setattr(server, "_schedule_resume_hydration", lambda *_args, **_kwargs: None)


@pytest.mark.parametrize("params_type", [SessionCreateParams, SessionResumeParams])
def test_session_contract_accepts_required_prompt_handler(params_type):
    """Removing the field from either closed RPC contract must reject the iOS policy."""
    params = params_type.model_validate({
        **({"session_id": "stored-ios"} if params_type is SessionResumeParams else {}),
        "required_prompt_handler": "hoppe_ocr_approval",
    })

    assert params.required_prompt_handler == "hoppe_ocr_approval"


def test_session_create_retains_normalized_required_prompt_handler(monkeypatch, tmp_path):
    """Dropping or failing to normalize the create parameter removes iOS fail-closed policy."""
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)
    monkeypatch.setattr(server, "_completion_cwd", lambda _params=None: str(tmp_path))

    response = server._methods["session.create"](
        "create-required-handler",
        {"source": "ios", "required_prompt_handler": "  hoppe_ocr_approval  "},
    )
    sid = response["result"]["session_id"]
    try:
        assert server._sessions[sid]["required_prompt_handler"] == "hoppe_ocr_approval"
        assert response["result"]["required_prompt_handler"] == "hoppe_ocr_approval"
    finally:
        server._sessions.pop(sid, None)


@pytest.mark.parametrize(
    "resume_mode",
    [{}, {"lazy": True}, {"defer_history": True}],
    ids=["cold", "lazy-watch", "deferred-history"],
)
def test_session_resume_retains_required_prompt_handler(monkeypatch, tmp_path, resume_mode):
    """Every cold-resume branch must preserve the same client policy."""
    target = "stored-ios"
    _prepare_resume(monkeypatch, tmp_path, target)

    response = server._methods["session.resume"](
        "resume-required-handler",
        {
            "session_id": target,
            "source": "ios",
            "required_prompt_handler": "hoppe_ocr_approval",
            **resume_mode,
        },
    )

    sid = response["result"]["session_id"]
    assert server._sessions[sid]["required_prompt_handler"] == "hoppe_ocr_approval"
    assert response["result"]["required_prompt_handler"] == "hoppe_ocr_approval"


def test_live_session_resume_refreshes_required_prompt_handler(monkeypatch, tmp_path):
    """Reconnect must reassert policy even when Hermes reuses a live session."""
    target = "stored-ios-live"
    _prepare_resume(monkeypatch, tmp_path, target)
    record = server._deferred_session_record(
        target, cols=80, cwd=str(tmp_path), history=[], lease=None, source="ios"
    )
    server._sessions["live-ios-ui"] = record

    response = server._methods["session.resume"](
        "resume-live-required-handler",
        {
            "session_id": target,
            "source": "ios",
            "required_prompt_handler": "hoppe_ocr_approval",
        },
    )

    assert response["result"]["session_id"] == "live-ios-ui"
    assert record["required_prompt_handler"] == "hoppe_ocr_approval"
    assert response["result"]["required_prompt_handler"] == "hoppe_ocr_approval"


def test_eager_session_constructor_retains_required_prompt_handler(monkeypatch, tmp_path):
    """The eager constructor must retain the policy just like deferred records."""
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)
    monkeypatch.setattr(server, "_wire_session_agent", lambda *_args: None)
    monkeypatch.setattr(server, "_start_session_services", lambda *_args: None)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_schedule_mcp_late_refresh", lambda *_args: None)
    monkeypatch.setattr(server, "_hydrate_session_cwd", lambda *_args: None)

    server._init_session(
        "eager-ios-ui",
        "stored-ios-eager",
        SimpleNamespace(model="test"),
        [],
        cwd=str(tmp_path),
        source="ios",
        required_prompt_handler="hoppe_ocr_approval",
    )

    assert server._sessions["eager-ios-ui"]["required_prompt_handler"] == "hoppe_ocr_approval"
