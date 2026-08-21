from pathlib import Path

import pytest

from hermes_state import ConversationDeleteConflict, SessionDB


def seed(
    db: SessionDB,
    sid: str,
    *,
    source="ios",
    parent=None,
    end_reason=None,
    model_config=None,
):
    db.create_session(
        sid,
        source=source,
        parent_session_id=parent,
        model_config=model_config or {},
    )
    if end_reason:
        db.end_session(sid, end_reason)


def test_conversation_identity_follows_only_compression_edges(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    seed(db, "root", end_reason="compression")
    seed(db, "tip", parent="root")
    seed(db, "branch", parent="root", model_config={"_branched_from": "root"})

    assert db.get_compression_conversation("tip") == (
        "root",
        "tip",
        ["root", "tip"],
    )
    assert db.get_compression_conversation("branch") == (
        "branch",
        "branch",
        ["branch"],
    )


def test_delete_preview_and_delete_cover_lineage_and_delegates_but_keep_branch(
    tmp_path: Path,
):
    db = SessionDB(tmp_path / "state.db")
    seed(db, "root")
    db.append_message(session_id="root", role="user", content="eins")
    db.end_session("root", "compression")
    seed(db, "tip", parent="root")
    seed(
        db,
        "delegate",
        parent="tip",
        source="tool",
        model_config={"_delegate_from": "tip"},
    )
    seed(db, "branch", parent="root", model_config={"_branched_from": "root"})
    db.append_message(session_id="tip", role="assistant", content="zwei")
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    for sid in ("root", "tip", "delegate", "branch"):
        (sessions_dir / f"{sid}.jsonl").write_text(sid, encoding="utf-8")

    preview = db.preview_conversation_delete("root")
    assert preview.app_chat_id == "root"
    assert preview.delete_ids == ("root", "tip", "delegate")
    assert preview.message_count == 2
    assert db.delete_conversation(
        "root",
        preview.revision,
        sessions_dir=sessions_dir,
    )
    assert db.get_session("root") is None
    assert db.get_session("tip") is None
    assert db.get_session("delegate") is None
    assert db.get_session("branch") is not None
    assert db.get_session("branch")["parent_session_id"] is None
    assert not (sessions_dir / "root.jsonl").exists()
    assert not (sessions_dir / "tip.jsonl").exists()
    assert not (sessions_dir / "delegate.jsonl").exists()
    assert (sessions_dir / "branch.jsonl").exists()


def test_delete_revision_fails_closed_when_scope_changes(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    seed(db, "root")
    preview = db.preview_conversation_delete("root")
    seed(
        db,
        "delegate",
        parent="root",
        source="tool",
        model_config={"_delegate_from": "root"},
    )

    with pytest.raises(ConversationDeleteConflict):
        db.delete_conversation("root", preview.revision)
    assert db.get_session("root") is not None


def test_delete_revision_fails_closed_when_same_size_transcript_is_replaced(
    tmp_path: Path,
):
    db = SessionDB(tmp_path / "state.db")
    seed(db, "root")
    db.append_message(session_id="root", role="user", content="before")
    preview = db.preview_conversation_delete("root")

    db.replace_messages("root", [{"role": "user", "content": "after"}])

    changed = db.preview_conversation_delete("root")
    assert (
        db.message_count("root")
        == changed.message_count
        == preview.message_count
        == 1
    )
    assert changed.latest_message_row_id != preview.latest_message_row_id
    with pytest.raises(ConversationDeleteConflict):
        db.delete_conversation("root", preview.revision)
    assert db.get_session("root") is not None


def test_delete_preview_counts_active_and_inactive_messages(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    seed(db, "root")
    db.append_message(session_id="root", role="user", content="before")
    db.replace_messages(
        "root",
        [{"role": "user", "content": "after"}],
        archive_dropped=True,
    )

    preview = db.preview_conversation_delete("root")

    assert db.message_count("root") == 2
    assert preview.message_count == 2


def test_set_conversation_title_targets_resumable_tip(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    seed(db, "root", end_reason="compression")
    seed(db, "tip", parent="root")

    assert db.set_conversation_title("root", "  Shared title  ") is True

    assert db.get_session_title("root") is None
    assert db.get_session_title("tip") == "Shared title"
    assert db.get_session_title_source("tip") == SessionDB.TITLE_SOURCE_USER
    preview = db.preview_conversation_delete("root")
    assert preview.session_id == "tip"
    assert preview.title == "Shared title"


def test_bulk_delete_expected_targets_fail_closed_when_delegate_appears(
    tmp_path: Path,
):
    db = SessionDB(tmp_path / "state.db")
    seed(db, "root")
    expected = db.get_session_delete_targets("root")
    seed(
        db,
        "delegate",
        parent="root",
        source="tool",
        model_config={"_delegate_from": "root"},
    )

    assert db.delete_sessions(["root"], expected_delete_ids=expected) == 0
    assert db.get_session("root") is not None
    assert db.get_session("delegate") is not None
