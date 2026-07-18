"""Deterministic restart reconciliation over Store and transport Ports."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.execution import AttemptState
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import (
    CommandResult, RunningAttempt, STORE_COMMAND_ID_MAX_BYTES, Store, StoreTransaction,
)


class ReconnectStatus(StrEnum):
    CONFIRMED = "confirmed"
    LOST = "lost"
    UNCERTAIN = "uncertain"


class RecoveryOutcome(StrEnum):
    RESUMED = "resumed"
    INTERRUPTED = "interrupted"
    OUTCOME_UNKNOWN = "outcome_unknown"


class RecoveryError(RuntimeError):
    """Raised when persisted recovery facts fail their exact contract."""


_MAX_RUN_ID_BYTES = 128


class TransportReconciler(Protocol):
    def reconcile(self, acp_session_id: str) -> ReconnectStatus: ...


@dataclass(frozen=True)
class RecoveryReport:
    resumed: tuple[str, ...] = ()
    interrupted: tuple[str, ...] = ()
    outcome_unknown: tuple[str, ...] = ()

    @property
    def retryable(self) -> tuple[str, ...]:
        return ()

    @property
    def outcomes(self) -> tuple[tuple[str, RecoveryOutcome], ...]:
        items = (
            *((attempt_id, RecoveryOutcome.RESUMED) for attempt_id in self.resumed),
            *((attempt_id, RecoveryOutcome.INTERRUPTED) for attempt_id in self.interrupted),
            *((attempt_id, RecoveryOutcome.OUTCOME_UNKNOWN)
              for attempt_id in self.outcome_unknown),
        )
        return tuple(sorted(items))


def _classify(
    attempt: RunningAttempt, transport: TransportReconciler
) -> tuple[RecoveryOutcome, str | None]:
    status = ReconnectStatus.LOST
    if attempt.acp_session_id is not None:
        try:
            status = transport.reconcile(attempt.acp_session_id)
        except Exception:
            return RecoveryOutcome.OUTCOME_UNKNOWN, "transport_reconcile_failed"
        if type(status) is not ReconnectStatus:
            return RecoveryOutcome.OUTCOME_UNKNOWN, "reconnect_uncertain"
    if status is ReconnectStatus.CONFIRMED:
        return RecoveryOutcome.RESUMED, None
    if attempt.effect_observed:
        return RecoveryOutcome.OUTCOME_UNKNOWN, "side_effect_observed"
    if status is ReconnectStatus.UNCERTAIN:
        return RecoveryOutcome.OUTCOME_UNKNOWN, "reconnect_uncertain"
    return RecoveryOutcome.INTERRUPTED, "acp_session_lost"


def _target_state(outcome: RecoveryOutcome) -> AttemptState:
    return {
        RecoveryOutcome.RESUMED: AttemptState.RUNNING,
        RecoveryOutcome.INTERRUPTED: AttemptState.INTERRUPTED,
        RecoveryOutcome.OUTCOME_UNKNOWN: AttemptState.OUTCOME_UNKNOWN,
    }[outcome]


class RecoveryService:
    def __init__(
        self,
        store: Store,
        transport: TransportReconciler,
        clock: Clock,
        recovery_run_id: str,
    ) -> None:
        for dependency, methods, label in (
            (store, ("list_running_attempts", "lookup_command", "execute_once"), "store"),
            (transport, ("reconcile",), "transport"),
            (clock, ("now",), "clock"),
        ):
            if any(not callable(getattr(dependency, method, None)) for method in methods):
                raise TypeError(f"{label} does not satisfy the recovery dependency")
        if type(recovery_run_id) is not str:
            raise TypeError("recovery_run_id must be a string")
        try:
            encoded_run_id = recovery_run_id.encode("utf-8", "strict")
        except UnicodeEncodeError as error:
            raise ValueError("recovery_run_id must be strict UTF-8") from error
        if (
            not recovery_run_id
            or any(character.isspace() or character == ":" for character in recovery_run_id)
            or len(encoded_run_id) > _MAX_RUN_ID_BYTES
        ):
            raise ValueError("recovery_run_id must be a nonempty bounded identifier")
        self._store = store
        self._transport = transport
        self._clock = clock
        self._recovery_run_id = recovery_run_id

    def _recover_one(self, attempt: RunningAttempt) -> RecoveryOutcome:
        command_id = f"recover:{self._recovery_run_id}:{attempt.attempt_id}"
        if len(command_id.encode("utf-8", "strict")) > STORE_COMMAND_ID_MAX_BYTES:
            raise RecoveryError("recovery command identity exceeds the Store limit")
        existing = self._store.lookup_command(command_id, "recover_attempt")
        if existing is not None:
            return self._result_outcome(existing, attempt)
        outcome, reason = _classify(attempt, self._transport)

        def persist(transaction: StoreTransaction) -> CommandResult:
            transaction.recover_attempt(
                attempt.attempt_id, _target_state(outcome), reason, expected=attempt
            )
            event_digest = sha256(
                f"recovery-event:{command_id}".encode("utf-8", "strict")
            ).hexdigest()[:32]
            transaction.append_event(DomainEvent(
                event_id=f"evt_{event_digest}",
                kind="attempt_recovered",
                aggregate_type="attempt",
                aggregate_id=attempt.attempt_id,
                payload=(
                    ("outcome", outcome.value),
                    ("reason", reason),
                    ("recovery_run_id", self._recovery_run_id),
                ),
                occurred_at=self._clock.now().isoformat(),
            ))
            return {"attempt_id": attempt.attempt_id, "outcome": outcome.value}

        result = self._store.execute_once(command_id, "recover_attempt", persist)
        return self._result_outcome(result, attempt)

    @staticmethod
    def _result_outcome(
        result: CommandResult, attempt: RunningAttempt
    ) -> RecoveryOutcome:
        if (
            type(result) is not dict
            or set(result) != {"attempt_id", "outcome"}
            or result["attempt_id"] != attempt.attempt_id
            or type(result["outcome"]) is not str
        ):
            raise RecoveryError("stored recovery result is malformed")
        try:
            return RecoveryOutcome(result["outcome"])
        except ValueError as error:
            raise RecoveryError("stored recovery outcome is unknown") from error

    def reconcile(self) -> RecoveryReport:
        buckets: dict[RecoveryOutcome, list[str]] = {
            outcome: [] for outcome in RecoveryOutcome
        }
        attempts = sorted(self._store.list_running_attempts(), key=lambda item: item.attempt_id)
        identities = tuple(attempt.attempt_id for attempt in attempts)
        if len(identities) != len(set(identities)):
            raise RecoveryError("store returned a duplicate running attempt")
        for attempt in attempts:
            buckets[self._recover_one(attempt)].append(attempt.attempt_id)
        return RecoveryReport(
            resumed=tuple(buckets[RecoveryOutcome.RESUMED]),
            interrupted=tuple(buckets[RecoveryOutcome.INTERRUPTED]),
            outcome_unknown=tuple(buckets[RecoveryOutcome.OUTCOME_UNKNOWN]),
        )
