from __future__ import annotations


def test_list_sessions_rich_filters_owner_before_limit(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        for index in range(6):
            db.create_session(f"foreign-{index}", "ios", user_id="other")
        for index in range(5):
            db.create_session(f"own-{index}", "ios", user_id="personal")
        with db._lock:
            for index in range(6):
                db._conn.execute(
                    "UPDATE sessions SET started_at = ? WHERE id = ?",
                    (100 + index, f"foreign-{index}"),
                )
            for index in range(5):
                db._conn.execute(
                    "UPDATE sessions SET started_at = ? WHERE id = ?",
                    (20 + index, f"own-{index}"),
                )
            db._conn.commit()

        rows = db.list_sessions_rich(source="ios", user_id="personal", limit=5)

        assert [row["id"] for row in rows] == [
            "own-4",
            "own-3",
            "own-2",
            "own-1",
            "own-0",
        ]
    finally:
        db.close()


def test_compression_child_inherits_user_id(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("root", "ios", user_id="personal")
        db.end_session("root", end_reason="compression")
        db.create_session("tip", "ios", parent_session_id="root")

        assert db.get_session("tip")["user_id"] == "personal"
    finally:
        db.close()


def test_owner_filter_hides_inconsistent_compression_lineages(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        for root, root_owner, tip, tip_owner in (
            ("owned-foreign-root", "personal", "owned-foreign-tip", "other"),
            ("owned-null-root", "personal", "owned-null-tip", None),
            ("foreign-owned-root", "other", "foreign-owned-tip", "personal"),
            ("null-owned-root", None, "null-owned-tip", "personal"),
            ("owned-root", "personal", "owned-tip", "personal"),
        ):
            db.create_session(root, "ios", user_id=root_owner)
            db.end_session(root, end_reason="compression")
            db.create_session(
                tip,
                "ios",
                user_id=tip_owner,
                parent_session_id=root,
            )
        # Simulate a pre-boundary broken inheritance row. Normal creation
        # deliberately repairs this NULL from the compression parent.
        with db._lock:
            db._conn.execute(
                "UPDATE sessions SET user_id = NULL WHERE id = ?",
                ("owned-null-tip",),
            )
            db._conn.commit()

        rows = db.list_sessions_rich(
            source="ios",
            user_id="personal",
            project_compression_tips=True,
            limit=5,
        )

        assert [row["id"] for row in rows] == ["owned-tip"]
    finally:
        db.close()
