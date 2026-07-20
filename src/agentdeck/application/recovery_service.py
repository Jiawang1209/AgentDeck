"""Conservative fresh-process convergence to a paused ProductSession."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import (
    CommandResult,
    STORE_COMMAND_ID_MAX_BYTES,
    Store,
    StoreTransaction,
    _session_identity,
)


_MAX_RUN_ID_BYTES = 128
_PENDING_EXIT_FIELDS = (
    "pending_exit_id", "pending_exit_attempt_id",
    "canonical_pending_exit_attempt_facts", "pending_exit_attempt_hash",
    "pending_exit_requested_at",
)


class RecoveryOutcome(StrEnum):
    INTERRUPTED = "interrupted"
    OUTCOME_UNKNOWN = "outcome_unknown"


class RecoveryError(RuntimeError):
    """Persisted recovery authority was ambiguous or changed."""


@dataclass(frozen=True)
class RecoveryReport:
    interrupted: tuple[str, ...] = ()
    outcome_unknown: tuple[str, ...] = ()

    @property
    def retryable(self) -> tuple[str, ...]:
        return ()

    @property
    def outcomes(self) -> tuple[tuple[str, RecoveryOutcome], ...]:
        return tuple(sorted((
            *((identity, RecoveryOutcome.INTERRUPTED)
              for identity in self.interrupted),
            *((identity, RecoveryOutcome.OUTCOME_UNKNOWN)
              for identity in self.outcome_unknown),
        )))


def _run_identity(value: object) -> str:
    if type(value) is not str:
        raise TypeError("recovery_run_id must be a string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError("recovery_run_id must be strict UTF-8") from None
    if (
        not value or len(encoded) > _MAX_RUN_ID_BYTES
        or any(character.isspace() or character == ":" for character in value)
    ):
        raise ValueError("recovery_run_id must be a nonempty bounded identifier")
    return value


def _pending_group_is_closed(session: dict[str, object]) -> bool:
    present = tuple(session.get(field) is not None for field in _PENDING_EXIT_FIELDS)
    if any(present) and not all(present):
        raise RecoveryError("recovery pending-exit authority is malformed")
    return all(present)


def _active_attempts(loader: Store | StoreTransaction, session_id: str):
    try:
        attempts = loader.list_active_exit_attempts(session_id)
    except (TypeError, ValueError, RuntimeError):
        raise RecoveryError("recovery attempt authority is invalid") from None
    if type(attempts) is not tuple or any(
        type(item) is not ExitAttemptSnapshot for item in attempts
    ):
        raise RecoveryError("recovery attempt authority is invalid")
    if len(attempts) > 1:
        raise RecoveryError("recovery authority is ambiguous")
    return attempts


def _session(loader: Store | StoreTransaction, session_id: str) -> dict[str, object]:
    try:
        value = loader.load_aggregate("product_sessions", session_id)
    except (TypeError, ValueError, RuntimeError):
        raise RecoveryError("recovery session authority is invalid") from None
    if type(value) is not dict or value.get("session_id") != session_id:
        raise RecoveryError("recovery session authority is invalid")
    _pending_group_is_closed(value)
    return value


def _outcome(attempt: ExitAttemptSnapshot) -> RecoveryOutcome:
    return (
        RecoveryOutcome.OUTCOME_UNKNOWN
        if attempt.effect_observed else RecoveryOutcome.INTERRUPTED
    )


def _reason(outcome: RecoveryOutcome) -> str:
    return (
        "effects_may_exist"
        if outcome is RecoveryOutcome.OUTCOME_UNKNOWN
        else "process_binding_lost"
    )


def _event(
    command_id: str, kind: str, aggregate_type: str, aggregate_id: str,
    payload: tuple[tuple[str, object], ...], occurred_at: str,
) -> DomainEvent:
    digest = sha256(f"{command_id}:{kind}:{aggregate_id}".encode()).hexdigest()[:32]
    return DomainEvent(
        event_id=f"evt_{digest}", kind=kind, aggregate_type=aggregate_type,
        aggregate_id=aggregate_id, payload=payload, occurred_at=occurred_at,
    )


def _report(result: object, session_id: str) -> RecoveryReport:
    if type(result) is not dict or set(result) != {
        "interrupted", "mode", "outcome_unknown", "session_id",
    } or result["mode"] != "project_recovered" or result["session_id"] != session_id:
        raise RecoveryError("stored recovery result is malformed")
    values: list[tuple[str, ...]] = []
    for name in ("interrupted", "outcome_unknown"):
        items = result[name]
        if type(items) is not list or any(
            type(item) is not str or not item.startswith("att_") for item in items
        ) or len(items) != len(set(items)):
            raise RecoveryError("stored recovery result is malformed")
        values.append(tuple(items))
    return RecoveryReport(*values)


class RecoveryService:
    def __init__(
        self, *, store: Store, clock: Clock, session_id: str,
        recovery_run_id: str,
    ) -> None:
        if any(not callable(getattr(store, name, None)) for name in (
            "list_active_exit_attempts", "load_aggregate", "lookup_command",
            "execute_once",
        )):
            raise TypeError("store does not satisfy the recovery dependency")
        if not callable(getattr(clock, "now", None)):
            raise TypeError("clock does not satisfy the recovery dependency")
        self._store = store
        self._clock = clock
        self._session_id = _session_identity(session_id)
        self._recovery_run_id = _run_identity(recovery_run_id)

    async def reconcile(self) -> RecoveryReport:
        command_id = f"recover:{self._recovery_run_id}:{self._session_id}"
        if len(command_id.encode("utf-8", "strict")) > STORE_COMMAND_ID_MAX_BYTES:
            raise RecoveryError("recovery command identity exceeds the Store limit")
        replay = self._store.lookup_command(command_id, "recover_project")
        if replay is not None:
            return _report(replay, self._session_id)
        attempts = _active_attempts(self._store, self._session_id)
        session = _session(self._store, self._session_id)
        has_pending_exit = _pending_group_is_closed(session)
        if not attempts and session.get("state") != "running" and not has_pending_exit:
            return RecoveryReport()
        expected_attempt = attempts[0] if attempts else None
        expected_session = dict(session)
        outcome = None if expected_attempt is None else _outcome(expected_attempt)

        def persist(transaction: StoreTransaction) -> CommandResult:
            live_attempts = _active_attempts(transaction, self._session_id)
            live_session = _session(transaction, self._session_id)
            if live_attempts != attempts or live_session != expected_session:
                raise RecoveryError("recovery durable authority changed")
            occurred_at = self._clock.now().isoformat()
            if expected_attempt is not None and outcome is not None:
                transaction.save_attempt({
                    "attempt_id": expected_attempt.attempt_id,
                    "task_id": expected_attempt.task_id,
                    "agent_instance_id": expected_attempt.agent_instance_id,
                    "ordinal": expected_attempt.ordinal,
                    "state": outcome.value,
                    "reason": _reason(outcome),
                    "retryable": False,
                    "acp_session_id": expected_attempt.acp_session_id,
                    "effect_observed": expected_attempt.effect_observed,
                })
                transaction.append_event(_event(
                    command_id, "attempt_recovered", "attempt",
                    expected_attempt.attempt_id,
                    (("outcome", outcome.value), ("reason", _reason(outcome))),
                    occurred_at,
                ))
            paused = dict(live_session)
            paused["state"] = "paused"
            for field in _PENDING_EXIT_FIELDS:
                paused[field] = None
            transaction.save_session(paused)
            transaction.append_event(_event(
                command_id, "project_paused", "product_session",
                self._session_id, (("recovery_run_id", self._recovery_run_id),),
                occurred_at,
            ))
            return {
                "interrupted": (
                    [expected_attempt.attempt_id]
                    if outcome is RecoveryOutcome.INTERRUPTED else []
                ),
                "mode": "project_recovered",
                "outcome_unknown": (
                    [expected_attempt.attempt_id]
                    if outcome is RecoveryOutcome.OUTCOME_UNKNOWN else []
                ),
                "session_id": self._session_id,
            }

        return _report(
            self._store.execute_once(command_id, "recover_project", persist),
            self._session_id,
        )


__all__ = [
    "RecoveryError", "RecoveryOutcome", "RecoveryReport", "RecoveryService",
]
