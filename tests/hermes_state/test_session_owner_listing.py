"""Session listings enforce durable ownership before pagination/projection."""

from hermes_state import SessionDB


def test_owner_filter_excludes_foreign_and_mixed_compression_lineages(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("own-root", "ios", user_id="alice")
        db.end_session("own-root", end_reason="compression")
        db.create_session("own-tip", "ios", parent_session_id="own-root")

        db.create_session("mixed-root", "ios", user_id="alice")
        db.end_session("mixed-root", end_reason="compression")
        db.create_session("mixed-tip", "ios", user_id="bob", parent_session_id="mixed-root")
        # Simulate a legacy/corrupt continuation, bypassing normal inheritance.
        with db._lock:
            db._conn.execute(
                "UPDATE sessions SET user_id = 'bob' WHERE id = 'mixed-tip'"
            )
            db._conn.commit()

        db.create_session("foreign-root", "ios", user_id="bob")
        rows = db.list_sessions_rich(
            source="ios", user_id="alice", limit=20, project_compression_tips=False,
        )

        ids = {row["id"] for row in rows}
        assert "own-root" in ids
        assert "foreign-root" not in ids
        assert "mixed-root" not in ids
    finally:
        db.close()
