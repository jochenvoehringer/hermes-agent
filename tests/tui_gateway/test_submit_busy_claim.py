"""A stale idle observation must not overwrite another turn's running claim."""

from threading import RLock

import tui_gateway.server as server


def test_lock_in_submit_refuses_a_raced_running_turn():
    session = {"history_lock": RLock(), "running": True}

    error, fields = server._lock_in_submit_turn(
        "request-1", "session-1", session, "next", {}, False, None, None, None
    )

    assert error["error"] == {"code": 4094, "message": "chat_busy"}
    assert fields == {}
    assert session["running"] is True
