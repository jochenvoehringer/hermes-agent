import time

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    database = SessionDB(tmp_path / "state.db")
    try:
        yield database
    finally:
        database.close()


def _compression_pair(db: SessionDB):
    base = time.time() - 100
    db.create_session("root", source="cli")
    db.create_session("tip", source="cli", parent_session_id="root")
    db._conn.execute(
        "UPDATE sessions SET started_at = ?, ended_at = ?, end_reason = 'compression', message_count = 1 WHERE id = 'root'",
        (base, base + 10),
    )
    db._conn.execute(
        "UPDATE sessions SET started_at = ?, message_count = 1 WHERE id = 'tip'",
        (base + 20,),
    )
    db._conn.commit()


def test_archiving_compression_tip_archives_projected_root(db):
    _compression_pair(db)

    assert db.set_session_archived("tip", True) is True

    assert db.get_session("root")["archived"] == 1
    assert db.get_session("tip")["archived"] == 1
    assert [s["id"] for s in db.list_sessions_rich(order_by_last_active=True)] == []
    assert [s["id"] for s in db.list_sessions_rich(order_by_last_active=True, archived_only=True)] == ["tip"]


def test_unarchiving_compression_tip_unarchives_projected_root(db):
    _compression_pair(db)
    db.set_session_archived("tip", True)

    assert db.set_session_archived("tip", False) is True

    assert db.get_session("root")["archived"] == 0
    assert db.get_session("tip")["archived"] == 0
    assert [s["id"] for s in db.list_sessions_rich(order_by_last_active=True)] == ["tip"]


def test_archiving_compression_root_does_not_archive_branch_or_delegate(db):
    _compression_pair(db)
    db.create_session(
        "branch",
        source="cli",
        parent_session_id="root",
        model_config={"_branched_from": "root"},
    )
    db.create_session(
        "delegate",
        source="tool",
        parent_session_id="root",
        model_config={"_delegate_from": "root"},
    )

    assert db.set_session_archived("root", True) is True

    assert db.get_session("root")["archived"] == 1
    assert db.get_session("tip")["archived"] == 1
    assert db.get_session("branch")["archived"] == 0
    assert db.get_session("delegate")["archived"] == 0


def test_archive_resolves_child_committed_before_write_transaction(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "state.db"
    archive_db = SessionDB(db_path)
    publisher_db = SessionDB(db_path)
    try:
        archive_db.create_session("root", source="cli")
        archive_db.end_session("root", "compression")
        original_execute_write = archive_db._execute_write
        child_published = False

        def _publish_child_before_write(fn, patience_s=None):
            nonlocal child_published
            if not child_published:
                publisher_db.create_session(
                    "tip",
                    source="cli",
                    parent_session_id="root",
                )
                child_published = True
            return original_execute_write(fn, patience_s=patience_s)

        monkeypatch.setattr(
            archive_db,
            "_execute_write",
            _publish_child_before_write,
        )

        assert archive_db.set_session_archived("root", True) is True
        assert archive_db.get_session("root")["archived"] == 1
        assert archive_db.get_session("tip")["archived"] == 1
    finally:
        archive_db.close()
        publisher_db.close()


def test_compression_child_published_after_archive_commit_inherits_archive_state(
    tmp_path,
):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("root", source="cli")
        assert db.set_session_archived("root", True) is True

        db.publish_compression_child(
            parent_session_id="root",
            child_session_id="tip",
            source="cli",
            messages=[{"role": "user", "content": "summary"}],
            require_compression_lease=False,
        )

        assert db.get_session("root")["archived"] == 1
        assert db.get_session("tip")["archived"] == 1
    finally:
        db.close()


def test_maybe_auto_archive_excludes_configured_sources(db):
    old = time.time() - 30 * 86400
    db.create_session("ios-stale", source="ios")
    db.create_session("desktop-stale", source="desktop")
    db._conn.execute(
        "UPDATE sessions SET started_at = ? WHERE id IN (?, ?)",
        (old, "ios-stale", "desktop-stale"),
    )
    db._conn.commit()

    result = db.maybe_auto_archive(
        idle_days=3,
        min_interval_hours=0,
        exclude_sources=["ios"],
    )

    assert result["archived"] == 1
    assert db.get_session("ios-stale")["archived"] == 0
    assert db.get_session("desktop-stale")["archived"] == 1


def test_archive_stale_sessions_default_archives_all_sources(db):
    old = time.time() - 30 * 86400
    db.create_session("ios-stale", source="ios")
    db.create_session("desktop-stale", source="desktop")
    db._conn.execute(
        "UPDATE sessions SET started_at = ? WHERE id IN (?, ?)",
        (old, "ios-stale", "desktop-stale"),
    )
    db._conn.commit()

    assert db.archive_stale_sessions(idle_days=3) == 2
    assert db.get_session("ios-stale")["archived"] == 1
    assert db.get_session("desktop-stale")["archived"] == 1
