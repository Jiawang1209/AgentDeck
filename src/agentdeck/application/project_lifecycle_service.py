"""Project-wide dispatch barrier and exact pause/resume commands."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from hmac import compare_digest
import json

from agentdeck.kernel.diagnostics import Diagnostic
from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot, ExitRequest
from agentdeck.ports.clock import Clock
from agentdeck.ports.execution_resume import ExecutionResumeSnapshot
from agentdeck.ports.store import CommandResult, Store, StoreTransaction, _session_identity


_PENDING_EXIT_FIELDS = (
    "pending_exit_id",
    "pending_exit_attempt_id",
    "canonical_pending_exit_attempt_facts",
    "pending_exit_attempt_hash",
    "pending_exit_requested_at",
)
_TERMINAL_ATTEMPT_STATES = frozenset({
    "completed", "failed", "cancelled", "interrupted", "outcome_unknown",
})
_MAX_AUTHORITY_BYTES = 1_048_576
_MAX_RESUME_GENERATIONS = 64


class ProjectDispatchBlocked(RuntimeError):
    """Raised when durable project facts do not authorize a new Worker."""


@dataclass(frozen=True)
class ProjectLifecycleResult:
    mode: str
    session_id: str
    should_start: bool
    snapshot_hash: str | None = None
    diagnostic: Diagnostic | None = None

    def __post_init__(self) -> None:
        _session_identity(self.session_id)
        if type(self.mode) is not str or not self.mode:
            raise ValueError("project lifecycle mode must be nonempty")
        if type(self.should_start) is not bool:
            raise TypeError("should_start must be an exact bool")
        if self.snapshot_hash is not None and not _is_hash(self.snapshot_hash):
            raise ValueError("snapshot_hash must be 64 lowercase hex or None")
        if self.diagnostic is not None and type(self.diagnostic) is not Diagnostic:
            raise TypeError("diagnostic must be a Diagnostic or None")


@dataclass(frozen=True)
class _PauseAuthority:
    session: dict[str, object]
    active_attempts: tuple[ExitAttemptSnapshot, ...]
    stale_request: ExitRequest | None
    content_hash: str


def _is_hash(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _now(clock: Clock) -> str:
    value = clock.now()
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.isoformat()


def _null_pending(session: Mapping[str, object]) -> bool:
    return all(session.get(field) is None for field in _PENDING_EXIT_FIELDS)


def _exact_running_null(session: object, session_id: str) -> bool:
    return (
        type(session) is dict
        and session.get("session_id") == session_id
        and session.get("state") == "running"
        and _null_pending(session)
    )


def _snapshot_for_session(
    snapshot: object, session_id: str
) -> ExecutionResumeSnapshot:
    if type(snapshot) is not ExecutionResumeSnapshot:
        raise TypeError("resume requires ExecutionResumeSnapshot")
    snapshot.validate_hash()
    if (
        snapshot.facts.session_id != session_id
        or snapshot.facts.session_state != "paused"
    ):
        raise ValueError("resume snapshot does not match the paused project")
    return snapshot


def _lifecycle_result(
    result: object, *, mode: str, session_id: str, should_start: bool,
    snapshot_hash: str,
) -> ProjectLifecycleResult:
    expected = {
        "mode": mode,
        "session_id": session_id,
        "should_start": should_start,
        "snapshot_hash": snapshot_hash,
    }
    if type(result) is not dict or result != expected:
        raise ValueError("stored project lifecycle result is malformed")
    return ProjectLifecycleResult(
        mode, session_id, should_start, snapshot_hash=snapshot_hash
    )


def _event(
    command_id: str, kind: str, session_id: str, payload: dict[str, object],
    occurred_at: str,
) -> DomainEvent:
    digest = sha256(f"{command_id}:{kind}".encode("utf-8")).hexdigest()[:32]
    return DomainEvent(
        event_id=f"evt_{digest}",
        kind=kind,
        aggregate_type="product_session",
        aggregate_id=session_id,
        payload=tuple((key, payload[key]) for key in sorted(payload)),
        occurred_at=occurred_at,
    )


def _pending_request(session: Mapping[str, object]) -> ExitRequest | None:
    values = tuple(session.get(field) for field in _PENDING_EXIT_FIELDS)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ProjectDispatchBlocked("project exit authority is malformed")
    try:
        payload = json.loads(values[2])
        if type(payload) is not dict:
            raise ValueError
        attempt = ExitAttemptSnapshot(
            payload["attempt_id"], payload["task_id"],
            payload["agent_instance_id"], payload["ordinal"],
            AttemptState(payload["state"]), payload["acp_session_id"],
            payload["effect_observed"], payload["durable_fingerprint"],
        )
        request = ExitRequest(values[0], attempt, values[3], values[4])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ProjectDispatchBlocked("project exit authority is malformed") from None
    if request.attempt.attempt_id != values[1]:
        raise ProjectDispatchBlocked("project exit authority is malformed")
    return request


def _terminal_stale_request(
    loader: Store | StoreTransaction, request: ExitRequest
) -> None:
    attempt = loader.load_aggregate("attempts", request.attempt.attempt_id)
    expected = request.attempt
    if (
        type(attempt) is not dict
        or attempt.get("state") not in _TERMINAL_ATTEMPT_STATES
        or (
            attempt.get("attempt_id"), attempt.get("task_id"),
            attempt.get("agent_instance_id"), attempt.get("ordinal"),
            attempt.get("acp_session_id"), attempt.get("effect_observed"),
        ) != (
            expected.attempt_id, expected.task_id, expected.agent_instance_id,
            expected.ordinal, expected.acp_session_id, expected.effect_observed,
        )
    ):
        raise ProjectDispatchBlocked("project exit authority is not terminal")


def _pause_authority(
    loader: Store | StoreTransaction, session_id: str
) -> _PauseAuthority:
    session = loader.load_aggregate("product_sessions", session_id)
    if type(session) is not dict or session.get("session_id") != session_id:
        raise ProjectDispatchBlocked("project session authority is unavailable")
    active = loader.list_active_exit_attempts(session_id)
    if type(active) is not tuple or any(
        type(item) is not ExitAttemptSnapshot for item in active
    ):
        raise ProjectDispatchBlocked("project attempt authority is malformed")
    request = _pending_request(session)
    if session.get("state") == "running":
        if active:
            raise ProjectDispatchBlocked("project has an active Attempt")
        if request is not None:
            _terminal_stale_request(loader, request)
    projection = {
        "session": session,
        "active_attempts": [item.canonical_facts() for item in active],
        "stale_request": None if request is None else {
            "request_id": request.request_id,
            "attempt_hash": request.attempt_hash,
            "requested_at": request.requested_at,
        },
    }
    try:
        encoded = json.dumps(
            projection, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ProjectDispatchBlocked("project pause authority is malformed") from None
    if not encoded or len(encoded) > _MAX_AUTHORITY_BYTES:
        raise ProjectDispatchBlocked("project pause authority is malformed")
    return _PauseAuthority(
        dict(session), active, request, sha256(encoded).hexdigest()
    )


class ProjectLifecycleService:
    def __init__(self, *, store: Store, clock: Clock, session_id: str) -> None:
        if any(not callable(getattr(store, name, None)) for name in (
            "load_aggregate", "lookup_command", "execute_once",
            "list_active_exit_attempts",
        )):
            raise TypeError("store does not satisfy project lifecycle dependency")
        if not callable(getattr(clock, "now", None)):
            raise TypeError("clock does not satisfy project lifecycle dependency")
        self._store = store
        self._clock = clock
        self._session_id = _session_identity(session_id)
        self._barrier = asyncio.Lock()

    @asynccontextmanager
    async def dispatch_lease(self):
        async with self._barrier:
            yield

    @asynccontextmanager
    async def stop_lease(self):
        async with self._barrier:
            yield

    def require_dispatchable(self) -> None:
        try:
            session = self._store.load_aggregate(
                "product_sessions", self._session_id
            )
        except (TypeError, ValueError, RuntimeError):
            raise ProjectDispatchBlocked("project dispatch is paused") from None
        if not _exact_running_null(session, self._session_id):
            raise ProjectDispatchBlocked("project dispatch is paused")

    def pause_between_stages(self) -> ProjectLifecycleResult:
        authority = _pause_authority(self._store, self._session_id)
        if authority.session.get("state") != "running":
            return ProjectLifecycleResult(
                "project_not_executing", self._session_id, False,
                snapshot_hash=authority.content_hash,
            )
        command_id = (
            f"project:pause:{self._session_id}:{authority.content_hash[:16]}"
        )

        def persist(transaction: StoreTransaction) -> CommandResult:
            live = _pause_authority(transaction, self._session_id)
            if not compare_digest(live.content_hash, authority.content_hash):
                raise ProjectDispatchBlocked("project pause authority changed")
            session = dict(live.session)
            session["state"] = "paused"
            for field in _PENDING_EXIT_FIELDS:
                session[field] = None
            transaction.save_session(session)
            transaction.append_event(_event(
                command_id, "project_paused", self._session_id,
                {"authority_hash": authority.content_hash}, _now(self._clock),
            ))
            transaction.append_event(_event(
                command_id, "exit_confirmed", self._session_id,
                {"authority_hash": authority.content_hash}, _now(self._clock),
            ))
            return {
                "mode": "project_paused",
                "session_id": self._session_id,
                "should_start": False,
                "snapshot_hash": authority.content_hash,
            }

        result = self._store.execute_once(
            command_id, "pause_project_between_stages", persist
        )
        return _lifecycle_result(
            result, mode="project_paused", session_id=self._session_id,
            should_start=False, snapshot_hash=authority.content_hash,
        )

    async def resume(
        self, snapshot: ExecutionResumeSnapshot
    ) -> ProjectLifecycleResult:
        async with self._barrier:
            snapshot = _snapshot_for_session(snapshot, self._session_id)
            command_base = (
                f"project:resume:{self._session_id}:"
                f"{snapshot.content_hash[:16]}"
            )
            command_id = command_base
            for generation in range(_MAX_RESUME_GENERATIONS):
                command_id = (
                    command_base if generation == 0
                    else f"{command_base}:generation:{generation}"
                )
                replay = self._store.lookup_command(command_id, "resume_project")
                if replay is None:
                    break
                restored = _lifecycle_result(
                    replay, mode="project_resumed", session_id=self._session_id,
                    should_start=True, snapshot_hash=snapshot.content_hash,
                )
                live_session = self._store.load_aggregate(
                    "product_sessions", self._session_id
                )
                if _exact_running_null(live_session, self._session_id):
                    return restored
                if not (
                    type(live_session) is dict
                    and live_session.get("session_id") == self._session_id
                    and live_session.get("state") == "paused"
                    and _null_pending(live_session)
                ):
                    raise ValueError("resume replay authority changed")
            else:
                raise ValueError("resume generation budget exhausted")

            def persist(transaction: StoreTransaction) -> CommandResult:
                live = transaction.load_execution_resume(self._session_id)
                if not compare_digest(live.content_hash, snapshot.content_hash):
                    raise ValueError("resume snapshot authority changed")
                session = transaction.load_aggregate(
                    "product_sessions", self._session_id
                )
                if type(session) is not dict or not _null_pending(session) or (
                    session.get("state") != "paused"
                ):
                    raise ValueError("resume project authority changed")
                saved = dict(session)
                saved["state"] = "running"
                transaction.save_session(saved)
                transaction.append_event(_event(
                    command_id, "project_resumed", self._session_id,
                    {"snapshot_hash": snapshot.content_hash}, _now(self._clock),
                ))
                return {
                    "mode": "project_resumed",
                    "session_id": self._session_id,
                    "should_start": True,
                    "snapshot_hash": snapshot.content_hash,
                }

            result = self._store.execute_once(
                command_id, "resume_project", persist
            )
            return _lifecycle_result(
                result, mode="project_resumed", session_id=self._session_id,
                should_start=True, snapshot_hash=snapshot.content_hash,
            )


__all__ = [
    "ProjectDispatchBlocked",
    "ProjectLifecycleResult",
    "ProjectLifecycleService",
]
