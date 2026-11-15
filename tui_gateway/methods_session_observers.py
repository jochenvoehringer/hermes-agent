"""Live resume and durable mobile subscriber reconciliation."""
from __future__ import annotations
from .method_ctx import bind_module

def _resume_reuse_live_locked(ctx: _Resume, sid: str, session: dict) -> dict:
    """Reuse with _session_resume_lock already held (including the eager double-check)."""
    if (refusal := _reattach_refusal(ctx.rid, sid, session)) is not None:
        return refusal
    session["required_prompt_handler"] = ctx.required_prompt_handler
    _cancel_ws_orphan_reap(sid)  # unconditionally: the fast path must never race the reap Timer
    payload = _live_session_payload(sid, session, cols=ctx.cols, touch=True, omit_messages=ctx.omit_messages,
                                    transport=current_transport() or _stdio_transport,
                                    inline_images=ctx.inline_images)
    payload["resumed"] = ctx.target
    _resume_subscribe_ios(ctx, str(payload.get("session_key") or ctx.target), session)
    if ctx.defer_history:
        payload.update(messages=[], hydrating=bool(session.get("resume_hydrating")),
                       message_count=int(session.get("resume_message_count") or payload["message_count"]))
    # A lazy watch session never owns a run loop — overlay the child-run registry.
    if session.get("agent") is None and _child_run_active(ctx.target, ctx.profile_home):
        payload.update(running=True, status="streaming")
    return _ok(ctx.rid, payload)


def _resume_subscribe_ios(ctx: _Resume, runtime_id: str, session: dict | None = None) -> None:
    """Bind an iOS transport to its stable app-chat after every resume path.

    iOS reconnects can outlive a reaped gateway runtime. Keep the transport set
    keyed by the durable app chat while rebinding the active session key.
    """
    transport = current_transport()
    if not transport or not isinstance(ctx.found, dict) or ctx.found.get("source") != "ios":
        return
    try:
        app_chat_id, _tip, _lineage = ctx.db.get_compression_conversation(ctx.target)
        _session_subscribers.subscribe(str(app_chat_id), runtime_id, transport)
        if session is not None:
            lock = session.get("history_lock")
            if lock is None:
                session["app_chat_id"] = str(app_chat_id)
            else:
                with lock:
                    session["app_chat_id"] = str(app_chat_id)
    except Exception:
        logger.debug("iOS session subscriber registration failed", exc_info=True)


def register(server) -> None:
    bind_module(globals(), server)
