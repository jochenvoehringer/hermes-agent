from __future__ import annotations

import pytest


def _request():
    from tui_gateway.session_mutation_extensions import SessionMutationRequest

    return SessionMutationRequest(
        profile="hoppe",
        session_id="chat-root",
        source="ios",
        action="delete",
    )


def test_unclaimed_mutation_runs_directly():
    from tui_gateway.session_mutation_extensions import run_session_mutation

    calls = []

    assert run_session_mutation(_request(), lambda: calls.append("db") or 42) == 42
    assert calls == ["db"]


def test_extension_cannot_swallow_canonical_revision_conflict():
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.session_mutation_extensions import register_session_mutation_extension, run_session_mutation
    def extension(request):
        def swallow(mutation):
            try:
                mutation()
            except RuntimeStoreError:
                return {"ok": True}
        return swallow
    def conflict():
        raise RuntimeStoreError("revision_conflict")
    unregister = register_session_mutation_extension("swallow-test", extension)
    try:
        with pytest.raises(RuntimeStoreError, match="revision_conflict"):
            run_session_mutation(_request(), conflict)
    finally:
        unregister()


def test_matching_extension_wraps_mutation_exactly_once():
    from tui_gateway.session_mutation_extensions import (
        register_session_mutation_extension,
        run_session_mutation,
    )

    calls = []

    def extension(request):
        if request.profile != "hoppe" or request.source != "ios":
            return None

        def runner(mutation):
            calls.append("before")
            result = mutation()
            calls.append("after")
            return result

        return runner

    unregister = register_session_mutation_extension("hoppe", extension)
    try:
        result = run_session_mutation(
            _request(), lambda: calls.append("db") or {"ok": True}
        )
    finally:
        unregister()

    assert result == {"ok": True}
    assert calls == ["before", "db", "after"]


def test_registration_is_idempotent_and_stale_unregister_is_safe():
    from tui_gateway.session_mutation_extensions import (
        register_session_mutation_extension,
        run_session_mutation,
    )

    calls = []
    stale_unregister = register_session_mutation_extension(
        "hoppe",
        lambda request: lambda mutation: calls.append("stale") or mutation(),
    )
    current_unregister = register_session_mutation_extension(
        "hoppe",
        lambda request: lambda mutation: calls.append("current") or mutation(),
    )
    stale_unregister()
    try:
        run_session_mutation(_request(), lambda: calls.append("db"))
    finally:
        current_unregister()

    assert calls == ["current", "db"]


def test_multiple_claims_fail_before_mutation():
    from tui_gateway.session_mutation_extensions import (
        SessionMutationExtensionConflict,
        register_session_mutation_extension,
        run_session_mutation,
    )

    calls = []
    unregister_first = register_session_mutation_extension(
        "first", lambda request: lambda mutation: mutation()
    )
    unregister_second = register_session_mutation_extension(
        "second", lambda request: lambda mutation: mutation()
    )
    try:
        with pytest.raises(SessionMutationExtensionConflict):
            run_session_mutation(_request(), lambda: calls.append("db"))
    finally:
        unregister_second()
        unregister_first()

    assert calls == []


def test_extension_failures_are_stable():
    from tui_gateway.session_mutation_extensions import (
        SessionMutationExtensionError,
        register_session_mutation_extension,
        run_session_mutation,
    )

    def broken(_request):
        raise RuntimeError("implementation detail")

    unregister = register_session_mutation_extension("broken", broken)
    try:
        with pytest.raises(SessionMutationExtensionError) as exc_info:
            run_session_mutation(_request(), lambda: None)
    finally:
        unregister()

    assert str(exc_info.value) == "session mutation extension failed"


def test_postcommit_extension_failure_is_stable_and_does_not_repeat_mutation():
    from tui_gateway.session_mutation_extensions import (
        SessionMutationExtensionError,
        register_session_mutation_extension,
        run_session_mutation,
    )

    calls = []

    def extension(_request):
        def runner(mutation):
            mutation()
            raise RuntimeError("fanout failed")

        return runner

    unregister = register_session_mutation_extension("broken", extension)
    try:
        with pytest.raises(SessionMutationExtensionError):
            run_session_mutation(_request(), lambda: calls.append("db"))
    finally:
        unregister()

    assert calls == ["db"]


@pytest.mark.parametrize("operation,payload,action", [
    ("delete", {}, "delete"), ("sidebar", {"title": "New"}, "rename"),
    ("sidebar", {"archived": True}, "archive"),
    ("sidebar", {"archived": False}, "restore"),
])
def test_canonical_commit_keeps_extension_and_revision_fence(tmp_path, operation, payload, action):
    from hermes_state import SessionDB
    import hermes_state_runtime as rt
    from tui_gateway.session_mutation_extensions import (
        coordinate_session_mutation, register_session_mutation_extension, SessionMutationBusy,
    )
    from functools import partial
    with SessionDB(tmp_path / "state.db") as db:
        db.create_session("s", source="ios")
        epoch = rt.begin_runtime_epoch(db, instance_id="owner")
        params = dict(session_id="s", request_id="change", expected_revision=0,
                      expected_generation=0, operation=operation, payload=payload)
        commit = partial(rt.mutate_runtime_session, db, epoch=epoch, principal_id="human", **params)
        requests = []
        def extension(request):
            requests.append(request)
            def blocked(mutation):
                raise SessionMutationBusy("chat_busy")
            return blocked
        unregister = register_session_mutation_extension("test-busy", extension)
        try:
            with pytest.raises(rt.RuntimeStoreError, match="session_busy"):
                coordinate_session_mutation(db, str(tmp_path / "profiles/hoppe"), params, commit)
        finally:
            unregister()
        assert requests[0].profile == "hoppe"
        assert requests[0].source == "ios" and requests[0].action == action
        assert db.get_session("s")["runtime_revision"] == 0
        # The original canonical transaction still decides freshness.
        stale = partial(rt.mutate_runtime_session, db, epoch=epoch, principal_id="human",
                        **(params | {"expected_revision": 99}))
        with pytest.raises(rt.RuntimeStoreError, match="revision_conflict"):
            coordinate_session_mutation(db, "hoppe", params, stale)
