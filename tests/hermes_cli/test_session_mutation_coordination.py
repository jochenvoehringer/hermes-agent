from __future__ import annotations

import pytest


def _request():
    from tui_gateway.session_mutation_extensions import SessionMutationRequest

    return SessionMutationRequest(
        profile="router",
        session_id="chat-root",
        source="ios",
        action="delete",
    )


def test_unclaimed_mutation_runs_directly():
    from tui_gateway.session_mutation_extensions import run_session_mutation

    calls = []

    assert run_session_mutation(_request(), lambda: calls.append("db") or 42) == 42
    assert calls == ["db"]


def test_matching_extension_wraps_mutation_exactly_once():
    from tui_gateway.session_mutation_extensions import (
        register_session_mutation_extension,
        run_session_mutation,
    )

    calls = []

    def extension(request):
        if request.profile != "router" or request.source != "ios":
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
            run_session_mutation(
                _request(), lambda: calls.append("db")
            )
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
