"""Neutral, process-local coordination seam for session mutations."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable, Dict, Generic, Literal, Optional, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class SessionMutationRequest:
    profile: str
    session_id: str
    source: str
    action: Literal["rename", "archive", "restore", "delete"]


class SessionMutationBusy(RuntimeError):
    """The owning runtime cannot safely accept this mutation now."""


class SessionMutationExtensionError(RuntimeError):
    """A registered extension violated or could not establish its contract."""


class SessionMutationExtensionConflict(SessionMutationExtensionError):
    """More than one extension claimed the same request."""


Mutation = Callable[[], T]
MutationRunner = Callable[[Mutation[T]], T]
MutationExtension = Callable[[SessionMutationRequest], Optional[MutationRunner[T]]]


@dataclass(frozen=True)
class _Registration(Generic[T]):
    extension: MutationExtension[T]


_lock = RLock()
_extensions: Dict[str, _Registration] = {}


def register_session_mutation_extension(name, handler):
    """Register or replace a named extension and return a safe unregister."""
    if not name or not callable(handler):
        raise ValueError("a non-empty name and callable handler are required")
    registration = _Registration(handler)
    with _lock:
        _extensions[str(name)] = registration

    def unregister() -> None:
        with _lock:
            if _extensions.get(str(name)) is registration:
                _extensions.pop(str(name), None)

    return unregister


def run_session_mutation(
    request: SessionMutationRequest,
    mutation: Mutation[T],
) -> T:
    """Run a mutation directly or through its sole claiming extension."""
    with _lock:
        registrations = tuple(_extensions.items())

    claims = []
    for _name, registration in sorted(registrations):
        try:
            runner = registration.extension(request)
        except SessionMutationBusy:
            raise
        except Exception as exc:
            raise SessionMutationExtensionError(
                "session mutation extension failed"
            ) from exc
        if runner is not None:
            claims.append(runner)

    if not claims:
        return mutation()
    if len(claims) > 1:
        raise SessionMutationExtensionConflict(
            "multiple session mutation extensions claimed the request"
        )

    calls = 0
    mutation_error = None

    def call_once():
        nonlocal calls, mutation_error
        calls += 1
        if calls > 1:
            raise SessionMutationExtensionError(
                "session mutation extension called mutation more than once"
            )
        try:
            return mutation()
        except Exception as exc:
            mutation_error = exc
            raise

    try:
        result = claims[0](call_once)
    except SessionMutationBusy:
        raise
    except Exception as exc:
        if exc is mutation_error:
            raise
        raise SessionMutationExtensionError(
            "session mutation extension failed"
        ) from exc
    if calls != 1:
        raise SessionMutationExtensionError(
            "session mutation extension did not call mutation"
        )
    return result
