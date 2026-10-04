"""User-visible compression conversation operations for :class:`SessionDB`."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from hermes_state_common import escape_like as _escape_like
from hermes_state_sessions import _collect_delegate_child_ids


@dataclass(frozen=True)
class ConversationDeletePreview:
    app_chat_id: str
    session_id: str
    title: str
    message_count: int
    latest_message_row_id: int
    delete_ids: Tuple[str, ...]
    revision: str


@dataclass(frozen=True)
class ConversationDeleteTargets:
    conversations: Tuple[ConversationDeletePreview, ...]
    delete_ids: Tuple[str, ...]


class ConversationDeleteConflict(RuntimeError):
    pass


def _conversation_revision(
    delete_ids: Iterable[str], message_count: int, latest_message_row_id: int
) -> str:
    canonical = json.dumps(
        {
            "delete_ids": sorted(delete_ids),
            "message_count": int(message_count),
            "latest_message_row_id": int(latest_message_row_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class SessionConversationMixin:
    """Conversation identity, title, preview, and revision-fenced deletion."""

    def _resolve_session_id_on_conn(
        self, conn: sqlite3.Connection, session_id_or_prefix: str
    ) -> Optional[str]:
        row = conn.execute(
            "SELECT id FROM sessions WHERE id = ?", (session_id_or_prefix,)
        ).fetchone()
        if row is not None:
            return str(row["id"])
        rows = conn.execute(
            "SELECT id FROM sessions WHERE id LIKE ? ESCAPE '\\' "
            "ORDER BY started_at DESC LIMIT 2",
            (f"{_escape_like(session_id_or_prefix)}%",),
        ).fetchall()
        return str(rows[0]["id"]) if len(rows) == 1 else None

    def _get_compression_lineage_on_conn(
        self, conn: sqlite3.Connection, session_id: str
    ) -> List[str]:
        """Transaction-local form of :meth:`get_compression_lineage`."""

        def _row(sid: str) -> Optional[dict]:
            found = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (sid,)
            ).fetchone()
            return dict(found) if found else None

        session = _row(session_id)
        if not session or self._is_explicit_fork_child_row(session):
            return [session_id] if session else []

        root = session
        ancestors = {str(root["id"])}
        while root.get("parent_session_id"):
            if self._is_explicit_fork_child_row(root, include_reset=True):
                break
            parent = _row(str(root["parent_session_id"]))
            if (
                not parent
                or parent["id"] in ancestors
                or parent.get("end_reason") != "compression"
            ):
                break
            root = parent
            ancestors.add(str(root["id"]))

        lineage = [str(root["id"])]
        seen = {str(root["id"])}
        current = root
        while current.get("end_reason") == "compression":
            rows = conn.execute(
                "SELECT * FROM sessions WHERE parent_session_id = ? "
                "ORDER BY started_at ASC",
                (current["id"],),
            ).fetchall()
            next_child = next(
                (
                    dict(row)
                    for row in rows
                    if not self._is_explicit_fork_child_row(
                        dict(row), include_reset=True
                    )
                ),
                None,
            )
            if not next_child or next_child["id"] in seen:
                break
            lineage.append(str(next_child["id"]))
            seen.add(str(next_child["id"]))
            current = next_child
        return lineage if session_id in lineage else [session_id]

    def get_compression_conversation(
        self, session_id: str
    ) -> Tuple[str, str, List[str]]:
        """Return ``(app_chat_id, resumable_tip, compression_lineage)``."""
        with self._read_ctx() as conn:
            lineage = self._get_compression_lineage_on_conn(conn, session_id)
        if not lineage:
            return session_id, session_id, []
        app_chat_id = lineage[0]
        resumable_tip = self.resolve_resume_session_id(lineage[-1])
        return app_chat_id, resumable_tip, lineage

    def set_conversation_title(self, app_chat_id: str, title: str) -> bool:
        """Set a user-authoritative title on the conversation's resumable tip."""
        _root, session_id, _lineage = self.get_compression_conversation(app_chat_id)
        return self.set_session_title(session_id, title)

    def _conversation_delete_preview_on_conn(
        self, conn: sqlite3.Connection, app_chat_id: str
    ) -> ConversationDeletePreview:
        lineage = self._get_compression_lineage_on_conn(conn, app_chat_id)
        if not lineage:
            raise ValueError(f"conversation not found: {app_chat_id}")
        canonical_id = lineage[0]
        session_id = lineage[-1]
        delegate_ids = sorted(_collect_delegate_child_ids(conn, lineage))
        delete_ids = tuple([*lineage, *delegate_ids])
        placeholders = ",".join("?" * len(delete_ids))
        message_row = conn.execute(
            f"SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS latest_id "
            f"FROM messages WHERE session_id IN ({placeholders})",
            delete_ids,
        ).fetchone()
        title_row = conn.execute(
            "SELECT COALESCE(title, '') AS title FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        message_count = int(message_row["count"])
        latest_message_row_id = int(message_row["latest_id"])
        return ConversationDeletePreview(
            app_chat_id=canonical_id,
            session_id=session_id,
            title=str(title_row["title"] if title_row else ""),
            message_count=message_count,
            latest_message_row_id=latest_message_row_id,
            delete_ids=delete_ids,
            revision=_conversation_revision(
                delete_ids, message_count, latest_message_row_id
            ),
        )

    def preview_conversation_delete(
        self, app_chat_id: str
    ) -> ConversationDeletePreview:
        """Describe the complete compression-lineage delete scope."""
        with self._read_ctx() as conn:
            return self._conversation_delete_preview_on_conn(conn, app_chat_id)

    def _conversation_delete_targets_on_conn(
        self,
        conn: sqlite3.Connection,
        requested_ids: Iterable[str],
    ) -> ConversationDeleteTargets:
        """Resolve and expand visible conversations inside one transaction."""
        conversations: Dict[str, ConversationDeletePreview] = {}
        for requested_id in requested_ids:
            resolved_id = self._resolve_session_id_on_conn(conn, requested_id)
            if not resolved_id:
                continue
            preview = self._conversation_delete_preview_on_conn(conn, resolved_id)
            conversations[preview.app_chat_id] = preview
        ordered = tuple(conversations[key] for key in sorted(conversations))
        delete_ids = tuple(
            sorted(
                {
                    session_id
                    for preview in ordered
                    for session_id in preview.delete_ids
                }
            )
        )
        return ConversationDeleteTargets(
            conversations=ordered,
            delete_ids=delete_ids,
        )

    def delete_conversation(
        self,
        app_chat_id: str,
        revision: str,
        sessions_dir: Optional[Path] = None,
    ) -> bool:
        """Delete a conversation when its preview revision is still current."""
        removed_ids: List[str] = []

        def _do(conn):
            preview = self._conversation_delete_preview_on_conn(conn, app_chat_id)
            if preview.revision != revision:
                raise ConversationDeleteConflict(
                    f"conversation changed since preview: {app_chat_id}"
                )
            count, deleted_ids = self._delete_sessions_on_conn(
                conn,
                list(preview.delete_ids),
                expected_delete_ids=preview.delete_ids,
            )
            if set(deleted_ids) != set(preview.delete_ids):
                raise ConversationDeleteConflict(
                    f"conversation delete scope changed: {app_chat_id}"
                )
            removed_ids.extend(deleted_ids)
            return count > 0

        deleted = self._execute_write(_do)
        if deleted:
            for session_id in removed_ids:
                self._remove_session_files(sessions_dir, session_id)
        return bool(deleted)
