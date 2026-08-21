"""REST contracts for user-visible compression conversations.

These routes are shared by the Web Dashboard and Hermes Desktop.  The tests
exercise real SessionDB rows so a regression to row-level mutations cannot be
hidden by client mocks.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading

import pytest


@dataclass(frozen=True)
class SeededLineage:
    root: str
    tip: str
    delegate: str
    branch: str


@pytest.fixture
def client(monkeypatch, _isolate_hermes_home):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import hermes_state
    from hermes_constants import get_hermes_home
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    monkeypatch.setattr(
        hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db"
    )
    result = TestClient(app, raise_server_exceptions=False)
    result.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return result


def _db():
    from hermes_state import SessionDB

    return SessionDB()


def _seed_lineage(prefix: str = "") -> SeededLineage:
    root = f"{prefix}root"
    tip = f"{prefix}tip"
    delegate = f"{prefix}delegate"
    branch = f"{prefix}branch"
    db = _db()
    try:
        db.create_session(root, source="ios")
        db.append_message(root, role="user", content=f"{prefix} question")
        db.end_session(root, "compression")
        db.create_session(tip, source="ios", parent_session_id=root)
        db.append_message(tip, role="assistant", content=f"{prefix} answer")
        db.create_session(
            delegate,
            source="tool",
            parent_session_id=tip,
            model_config={"_delegate_from": tip},
        )
        db.create_session(
            branch,
            source="ios",
            parent_session_id=root,
            model_config={"_branched_from": root},
        )
    finally:
        db.close()

    return SeededLineage(root, tip, delegate, branch)


def test_patch_title_targets_visible_compression_tip(client):
    seeded = _seed_lineage()

    response = client.patch(
        f"/api/sessions/{seeded.root}", json={"title": "Kunde Hauffa"}
    )

    assert response.status_code == 200
    db = _db()
    try:
        assert db.get_session_title(seeded.tip) == "Kunde Hauffa"
        assert db.get_session_title(seeded.root) is None
    finally:
        db.close()


def test_router_ios_patch_runs_through_registered_extension(client):
    from tui_gateway.session_mutation_extensions import (
        register_session_mutation_extension,
    )

    seeded = _seed_lineage()
    observed = []

    def extension(request):
        if request.source != "ios":
            return None

        def runner(mutation):
            observed.append(request)
            return mutation()

        return runner

    unregister = register_session_mutation_extension("test-hoppe", extension)
    try:
        response = client.patch(
            f"/api/sessions/{seeded.root}",
            json={"title": "Remote title"},
        )
    finally:
        unregister()

    assert response.status_code == 200
    assert len(observed) == 1
    assert observed[0].session_id == seeded.root
    assert observed[0].action == "rename"


def test_busy_extension_returns_stable_conflict_without_mutation(client):
    from tui_gateway.session_mutation_extensions import (
        SessionMutationBusy,
        register_session_mutation_extension,
    )

    seeded = _seed_lineage()

    def extension(request):
        if request.source != "ios":
            return None

        def runner(_mutation):
            raise SessionMutationBusy("runtime detail")

        return runner

    unregister = register_session_mutation_extension("test-hoppe", extension)
    try:
        response = client.delete(f"/api/sessions/{seeded.root}")
    finally:
        unregister()

    assert response.status_code == 409
    assert response.json() == {"detail": "chat_busy"}
    db = _db()
    try:
        assert db.get_session(seeded.root) is not None
        assert db.get_session(seeded.tip) is not None
    finally:
        db.close()


def test_patch_archive_and_restore_target_only_compression_line(client):
    seeded = _seed_lineage()

    archived = client.patch(
        f"/api/sessions/{seeded.root}", json={"archived": True}
    )
    assert archived.status_code == 200

    db = _db()
    try:
        assert db.get_session(seeded.root)["archived"] == 1
        assert db.get_session(seeded.tip)["archived"] == 1
        assert db.get_session(seeded.delegate)["archived"] == 0
        assert db.get_session(seeded.branch)["archived"] == 0
    finally:
        db.close()

    restored = client.patch(
        f"/api/sessions/{seeded.tip}", json={"archived": False}
    )
    assert restored.status_code == 200

    db = _db()
    try:
        assert db.get_session(seeded.root)["archived"] == 0
        assert db.get_session(seeded.tip)["archived"] == 0
    finally:
        db.close()


def test_delete_removes_entire_compression_line_but_preserves_branch(client):
    from hermes_constants import get_hermes_home

    seeded = _seed_lineage()
    sessions_dir = get_hermes_home() / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    for session_id in (seeded.root, seeded.tip, seeded.delegate, seeded.branch):
        (sessions_dir / f"{session_id}.jsonl").write_text(
            "transcript", encoding="utf-8"
        )

    response = client.delete(f"/api/sessions/{seeded.root}")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["deleted_count"] == 3
    assert set(body["deleted_ids"]) == {
        seeded.root,
        seeded.tip,
        seeded.delegate,
    }
    assert body["app_chat_id"] == seeded.root

    db = _db()
    try:
        assert db.get_session(seeded.root) is None
        assert db.get_session(seeded.tip) is None
        assert db.get_session(seeded.delegate) is None
        branch = db.get_session(seeded.branch)
        assert branch is not None
        assert branch["parent_session_id"] is None
    finally:
        db.close()
    assert not (sessions_dir / f"{seeded.root}.jsonl").exists()
    assert not (sessions_dir / f"{seeded.tip}.jsonl").exists()
    assert not (sessions_dir / f"{seeded.delegate}.jsonl").exists()
    assert (sessions_dir / f"{seeded.branch}.jsonl").exists()


def test_delete_resolves_and_expands_inside_write_transaction(
    client, monkeypatch
):
    from hermes_state import SessionDB

    seeded = _seed_lineage()
    original = SessionDB._conversation_delete_targets_on_conn
    observed_transaction = False

    def observe_transaction(self, conn, requested_ids):
        nonlocal observed_transaction
        observed_transaction = conn.in_transaction
        return original(self, conn, requested_ids)

    monkeypatch.setattr(
        SessionDB,
        "_conversation_delete_targets_on_conn",
        observe_transaction,
    )

    response = client.delete(f"/api/sessions/{seeded.root}")

    assert response.status_code == 200
    assert observed_transaction is True


def test_bulk_delete_expands_each_selected_visible_conversation(client):
    from hermes_constants import get_hermes_home

    first = _seed_lineage("a-")
    second = _seed_lineage("b-")
    sessions_dir = get_hermes_home() / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    for session_id in (first.root, first.tip, second.root, second.tip):
        (sessions_dir / f"{session_id}.json").write_text(
            "transcript", encoding="utf-8"
        )

    response = client.post(
        "/api/sessions/bulk-delete", json={"ids": [first.root, second.tip]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted_conversations"] == 2
    assert body["deleted_rows"] == 6
    assert body["deleted"] == 6
    assert set(body["deleted_ids"]) == {
        first.root,
        first.tip,
        first.delegate,
        second.root,
        second.tip,
        second.delegate,
    }

    db = _db()
    try:
        assert db.get_session(first.branch) is not None
        assert db.get_session(second.branch) is not None
    finally:
        db.close()
    for session_id in (first.root, first.tip, second.root, second.tip):
        assert not (sessions_dir / f"{session_id}.json").exists()


def test_bulk_delete_deduplicates_overlapping_root_and_tip(client):
    seeded = _seed_lineage()

    response = client.post(
        "/api/sessions/bulk-delete", json={"ids": [seeded.root, seeded.tip]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted_conversations"] == 1
    assert body["deleted_rows"] == 3
    assert len(body["deleted_ids"]) == 3


def test_single_and_bulk_use_the_same_lineage_target_helper():
    seeded = _seed_lineage()
    db = _db()
    try:
        with db._read_ctx() as conn:
            single = db._conversation_delete_targets_on_conn(
                conn, [seeded.root]
            )
            bulk = db._conversation_delete_targets_on_conn(conn, [seeded.tip])
    finally:
        db.close()

    assert single.delete_ids == bulk.delete_ids


def test_bulk_delete_rejects_expanded_row_limit_without_partial_delete(client):
    db = _db()
    try:
        root = "oversized-0"
        db.create_session(root, source="ios")
        previous = root
        for index in range(1, 501):
            db.end_session(previous, "compression")
            current = f"oversized-{index}"
            db.create_session(current, source="ios", parent_session_id=previous)
            previous = current
    finally:
        db.close()

    response = client.post("/api/sessions/bulk-delete", json={"ids": [root]})

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Deleting these conversations would remove more than 500 stored "
        "session rows. Select fewer conversations and try again."
    )
    db = _db()
    try:
        assert db.get_session(root) is not None
        assert db.get_session("oversized-500") is not None
        assert db.session_count(include_archived=True) == 501
    finally:
        db.close()


def test_two_clients_deleting_same_conversation_are_idempotent(
    client, monkeypatch
):
    from hermes_state import SessionDB

    seeded = _seed_lineage()
    original = SessionDB._conversation_delete_targets_on_conn
    first_inside = threading.Event()
    second_started = threading.Event()
    invocation_count = 0

    def coordinate_writers(self, conn, requested_ids):
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 1:
            assert conn.in_transaction
            first_inside.set()
            assert second_started.wait(timeout=5)
        return original(self, conn, requested_ids)

    monkeypatch.setattr(
        SessionDB,
        "_conversation_delete_targets_on_conn",
        coordinate_writers,
    )

    responses = []

    def first_delete():
        responses.append(client.delete(f"/api/sessions/{seeded.root}"))

    first = threading.Thread(target=first_delete)
    first.start()
    assert first_inside.wait(timeout=5)
    second_started.set()
    second = client.delete(f"/api/sessions/{seeded.root}")
    first.join(timeout=5)

    assert not first.is_alive()
    assert [responses[0].status_code, second.status_code] == [200, 200]
    assert sorted(
        [responses[0].json()["deleted_count"], second.json()["deleted_count"]]
    ) == [0, 3]


def test_bulk_delete_skips_conversation_deleted_by_another_client(
    client, monkeypatch
):
    from hermes_state import SessionDB

    seeded = _seed_lineage()
    survivor = _seed_lineage("survivor-")
    original = SessionDB._conversation_delete_targets_on_conn
    first_inside = threading.Event()
    second_started = threading.Event()
    invocation_count = 0

    def coordinate_writers(self, conn, requested_ids):
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 1:
            first_inside.set()
            assert second_started.wait(timeout=5)
        return original(self, conn, requested_ids)

    monkeypatch.setattr(
        SessionDB,
        "_conversation_delete_targets_on_conn",
        coordinate_writers,
    )

    first_response = []

    def delete_first():
        first_response.append(client.delete(f"/api/sessions/{seeded.root}"))

    first = threading.Thread(target=delete_first)
    first.start()
    assert first_inside.wait(timeout=5)
    second_started.set()
    response = client.post(
        "/api/sessions/bulk-delete",
        json={"ids": [seeded.root, survivor.root]},
    )
    first.join(timeout=5)

    assert not first.is_alive()
    assert first_response[0].status_code == 200
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["deleted_conversations"] == 1
    assert bulk["deleted_rows"] == 3
    assert set(bulk["deleted_ids"]) == {
        survivor.root,
        survivor.tip,
        survivor.delegate,
    }
