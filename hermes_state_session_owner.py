"""Durable owner filtering for compression-chain session listings."""
from __future__ import annotations
from typing import Any

def _append_owner_scope(where: list[str], params: list[Any], user_id: str | None) -> None:
    """Restrict a listing to one durable owner and fail closed on mixed compression chains."""
    if user_id is None:
        return
    where.append("s.user_id = ?")
    params.append(user_id)
    where.append(
        """NOT EXISTS (
            WITH RECURSIVE owner_chain(id) AS (
                SELECT child.id FROM sessions child
                WHERE child.parent_session_id = s.id AND s.end_reason = 'compression'
                  AND json_extract(COALESCE(child.model_config, '{}'), '$._branched_from') IS NULL
                  AND json_extract(COALESCE(child.model_config, '{}'), '$._delegate_from') IS NULL
                  AND COALESCE(child.source, '') != 'tool'
                UNION ALL
                SELECT child.id FROM owner_chain chain_row
                JOIN sessions parent ON parent.id = chain_row.id
                JOIN sessions child ON child.parent_session_id = chain_row.id
                WHERE parent.end_reason = 'compression'
                  AND json_extract(COALESCE(child.model_config, '{}'), '$._branched_from') IS NULL
                  AND json_extract(COALESCE(child.model_config, '{}'), '$._delegate_from') IS NULL
                  AND COALESCE(child.source, '') != 'tool'
            )
            SELECT 1 FROM owner_chain
            JOIN sessions owner_row ON owner_row.id = owner_chain.id
            WHERE owner_row.user_id IS NOT ? OR owner_row.source IS NOT s.source
        )"""
    )
    params.append(user_id)
