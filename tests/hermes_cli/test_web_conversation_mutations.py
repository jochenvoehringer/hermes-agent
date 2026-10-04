"""Dashboard mutations operate on visible conversations, not raw segments."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(monkeypatch, _isolate_hermes_home):
    import hermes_state
    from hermes_constants import get_hermes_home
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db")
    result = TestClient(app, raise_server_exceptions=False)
    result.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return result


def _db():
    from hermes_state import SessionDB

    return SessionDB()


def _lineage():
    db = _db()
    try:
        db.create_session("root", source="ios")
        db.end_session("root", "compression")
        db.create_session("tip", source="ios", parent_session_id="root")
        db.create_session("branch", source="ios", parent_session_id="root",
                          model_config={"_branched_from": "root"})
    finally:
        db.close()


def test_title_renames_resumable_tip(client):
    _lineage()
    response = client.patch("/api/sessions/root", json={"title": "Shared chat"})
    assert response.status_code == 200, response.text
    assert response.json()["app_chat_id"] == "root"
    db = _db()
    try:
        assert db.get_session_title("tip") == "Shared chat"
        assert db.get_session_title("root") is None
    finally:
        db.close()


def test_single_delete_expands_compression_but_preserves_branch(client):
    _lineage()
    response = client.delete("/api/sessions/root")
    assert response.status_code == 200, response.text
    assert response.json()["deleted_ids"] == ["root", "tip"]
    assert response.json()["app_chat_id"] == "root"
    db = _db()
    try:
        assert db.get_session("root") is None
        assert db.get_session("tip") is None
        assert db.get_session("branch") is not None
    finally:
        db.close()


def test_bulk_delete_reports_authoritative_ids(client):
    _lineage()
    response = client.post("/api/sessions/bulk-delete", json={"ids": ["tip", "missing"]})
    assert response.status_code == 200, response.text
    assert response.json()["deleted_ids"] == ["root", "tip"]
    assert response.json()["deleted_conversations"] == 1


def test_busy_extension_fails_closed_before_delete(client):
    from tui_gateway.session_mutation_extensions import (
        SessionMutationBusy, register_session_mutation_extension,
    )

    _lineage()

    def extension(request):
        if request.source == "ios":
            def refuse(_mutation):
                raise SessionMutationBusy("private runtime detail")
            return refuse
        return None

    unregister = register_session_mutation_extension("test-hoppe", extension)
    try:
        response = client.delete("/api/sessions/root")
    finally:
        unregister()
    assert response.status_code == 409
    assert response.json() == {"detail": "chat_busy"}
    db = _db()
    try:
        assert db.get_session("root") is not None
    finally:
        db.close()
