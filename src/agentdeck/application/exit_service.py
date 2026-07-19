"""Exact ProductSession exit authority without Worker cancellation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest, compare_digest as _compare_internal
import json

from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot, ExitRequest
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import (
    CommandResult,
    Store,
    StoreTransaction,
    _session_identity,
)


_EXIT_FIELDS = (
    "pending_exit_id", "pending_exit_attempt_id",
    "canonical_pending_exit_attempt_facts", "pending_exit_attempt_hash",
    "pending_exit_requested_at",
)
_LOWER_HEX = frozenset("0123456789abcdef")
_DIAGNOSTIC_CODES = frozenset({
    "exit_active_attempt_ambiguous", "exit_attempt_missing",
    "exit_authority_invalid", "exit_cancellation_unavailable",
    "exit_input_closed_with_active_work", "exit_request_drift",
    "exit_request_identity_mismatch", "exit_request_malformed",
    "exit_request_missing", "exit_session_missing",
})


@dataclass(frozen=True)
class ExitResult:
    mode: str
    should_exit: bool
    request: ExitRequest | None = None
    diagnostic: Diagnostic | None = None


class _ExitAbort(RuntimeError):
    def __init__(self, code: str, request: ExitRequest | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.request = request

def _require_dependency(
    dependency: object, methods: tuple[str, ...], label: str,
) -> None:
    if any(not callable(getattr(dependency, method, None)) for method in methods):
        raise TypeError(f"{label} does not satisfy the exit dependency")

def _valid_lower_hex(value: object, length: int) -> bool:
    return (
        type(value) is str
        and len(value) == length
        and all(character in _LOWER_HEX for character in value)
    )

def _valid_request_id(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 36
        and value.startswith("xrt_")
        and _valid_lower_hex(value[4:], 32)
    )

def _snapshot_from_facts(value: object) -> ExitAttemptSnapshot:
    if type(value) is not dict or set(value) != {
        "attempt_id", "task_id", "agent_instance_id", "ordinal", "state",
        "acp_session_id", "effect_observed", "durable_fingerprint",
    }:
        raise ValueError("exit snapshot shape is invalid")
    if type(value["state"]) is not str:
        raise ValueError("exit snapshot state is invalid")
    return ExitAttemptSnapshot(
        attempt_id=value["attempt_id"],
        task_id=value["task_id"],
        agent_instance_id=value["agent_instance_id"],
        ordinal=value["ordinal"],
        state=AttemptState(value["state"]),
        acp_session_id=value["acp_session_id"],
        effect_observed=value["effect_observed"],
        durable_fingerprint=value["durable_fingerprint"],
    )

def _request_from_session(session: Mapping[str, object]) -> ExitRequest | None:
    if not all(field in session for field in _EXIT_FIELDS):
        raise ValueError("pending exit group is absent")
    values = tuple(session[field] for field in _EXIT_FIELDS)
    if values == (None,) * len(_EXIT_FIELDS):
        return None
    if any(value is None for value in values):
        raise ValueError("pending exit group is partial")
    request_id, attempt_id, canonical, attempt_hash, requested_at = values
    if type(canonical) is not str:
        raise ValueError("pending exit canonical facts are invalid")
    snapshot = _snapshot_from_facts(json.loads(canonical))
    request = ExitRequest(request_id, snapshot, attempt_hash, requested_at)
    if (
        attempt_id != snapshot.attempt_id
        or canonical != snapshot.canonical_bytes().decode("utf-8")
    ):
        raise ValueError("pending exit lineage is invalid")
    return request

def _request_result(request: ExitRequest) -> CommandResult:
    return {
        "attempt_hash": request.attempt_hash,
        "canonical_attempt_facts": request.attempt.canonical_bytes().decode("utf-8"),
        "mode": "exit_confirmation_required",
        "request_id": request.request_id,
        "requested_at": request.requested_at,
    }

def _request_from_result(result: CommandResult) -> ExitRequest:
    if set(result) != {
        "attempt_hash", "canonical_attempt_facts", "mode", "request_id",
        "requested_at",
    } or result["mode"] != "exit_confirmation_required":
        raise ValueError("stored exit request result is malformed")
    canonical = result["canonical_attempt_facts"]
    if type(canonical) is not str:
        raise ValueError("stored exit request result is malformed")
    snapshot = _snapshot_from_facts(json.loads(canonical))
    request = ExitRequest(
        result["request_id"], snapshot, result["attempt_hash"], result["requested_at"]
    )
    if canonical != snapshot.canonical_bytes().decode("utf-8"):
        raise ValueError("stored exit request result is malformed")
    return request

def _event(
    command_id: str, kind: str, session_id: str, request: ExitRequest,
    occurred_at: str,
) -> DomainEvent:
    digest = sha256(f"{kind}\0{command_id}".encode("utf-8", "strict")).hexdigest()[:32]
    return DomainEvent(
        event_id=f"evt_{digest}",
        kind=kind,
        aggregate_type="product_session",
        aggregate_id=session_id,
        payload=(
            ("attempt_hash", request.attempt_hash),
            ("attempt_id", request.attempt.attempt_id),
            ("request_id", request.request_id),
        ),
        occurred_at=occurred_at,
    )

def _decline_facts(result: CommandResult) -> tuple[str, str]:
    if set(result) != {"attempt_hash", "mode", "request_id"} or (
        result["mode"] != "exit_declined"
        or not _valid_request_id(result["request_id"])
        or not _valid_lower_hex(result["attempt_hash"], 64)
    ):
        raise ValueError("stored exit decline result is malformed")
    return result["request_id"], result["attempt_hash"]

def _session_write(
    session: Mapping[str, object], request: ExitRequest | None,
) -> dict[str, object]:
    snapshot = {
        "session_id": session["session_id"],
        "state": session["state"],
        "permission_profile": session["permission_profile"],
        "pending_goal": session["pending_goal"],
    }
    if request is None:
        snapshot.update(dict.fromkeys(_EXIT_FIELDS))
    else:
        snapshot.update({
            "pending_exit_id": request.request_id,
            "pending_exit_attempt_id": request.attempt.attempt_id,
            "canonical_pending_exit_attempt_facts": (
                request.attempt.canonical_bytes().decode("utf-8")
            ),
            "pending_exit_attempt_hash": request.attempt_hash,
            "pending_exit_requested_at": request.requested_at,
        })
    return snapshot


class ExitService:
    def __init__(
        self,
        *,
        store: Store,
        clock: Clock,
        session_id: str,
        request_id_factory: Callable[[], str],
    ) -> None:
        _require_dependency(
            store,
            (
                "execute_once", "list_active_exit_attempts", "load_aggregate",
                "lookup_command",
            ),
            "store",
        )
        _require_dependency(clock, ("now",), "clock")
        if not callable(request_id_factory):
            raise TypeError("request_id_factory must be callable")
        self._store = store
        self._clock = clock
        self._session_id = _session_identity(session_id)
        self._request_id_factory = request_id_factory

    def request_exit(self) -> ExitResult:
        attempts = self._active_attempts()
        if attempts is None:
            return self._failure("exit_authority_invalid")
        if not attempts:
            return ExitResult("exit_ready", True)
        if len(attempts) > 1:
            return self._failure("exit_active_attempt_ambiguous")
        session, pending = self._load_pending()
        if session is None:
            return self._failure("exit_request_malformed")
        current = attempts[0]
        if pending is not None and _same_attempt(pending.attempt, current):
            return ExitResult("exit_confirmation_required", False, pending)
        try:
            request_id = self._request_id_factory()
            candidate = ExitRequest(
                request_id, current, current.content_hash, self._now()
            )
        except StopIteration:
            raise ValueError("request_id_factory did not return an identity") from None
        if pending is not None and candidate.request_id == pending.request_id:
            raise ValueError("request_id_factory repeated durable exit identity")
        if self._store.lookup_command(
            f"exit:request:{candidate.request_id}", "request_product_exit"
        ) is not None:
            raise ValueError("request_id_factory returned a durable exit identity")
        command_id = f"exit:request:{candidate.request_id}"

        def persist(transaction: StoreTransaction) -> CommandResult:
            live_session, live_pending = self._transaction_pending(transaction)
            live_attempts = transaction.list_active_exit_attempts()
            if len(live_attempts) != 1:
                raise _ExitAbort(
                    "exit_attempt_missing" if not live_attempts
                    else "exit_active_attempt_ambiguous",
                    live_pending,
                )
            if not _same_optional_request(live_pending, pending):
                raise _ExitAbort("exit_request_drift", live_pending)
            if not _same_attempt(live_attempts[0], candidate.attempt):
                raise _ExitAbort("exit_request_drift", live_pending)
            transaction.save_session(_session_write(live_session, candidate))
            transaction.append_event(_event(
                command_id, "exit_requested", self._session_id, candidate,
                candidate.requested_at,
            ))
            return _request_result(candidate)

        try:
            result = self._store.execute_once(
                command_id, "request_product_exit", persist
            )
        except _ExitAbort as error:
            return self._failure(error.code, request=error.request)
        request = _request_from_result(result)
        return ExitResult("exit_confirmation_required", False, request)

    def decline(self, request_id: str, attempt_hash: str) -> ExitResult:
        if not _valid_request_id(request_id) or not _valid_lower_hex(attempt_hash, 64):
            return self._failure("exit_request_identity_mismatch")
        replay = self._decline_replay(request_id, attempt_hash)
        if replay is not None:
            return replay
        checked = self._decision_authority(request_id, attempt_hash)
        if isinstance(checked, ExitResult):
            return checked
        pending = checked
        command_id = f"exit:decline:{pending.request_id}"
        decision_at = self._now()

        def persist(transaction: StoreTransaction) -> CommandResult:
            session, live = self._transaction_pending(transaction)
            if live is None or not _same_request(live, pending):
                raise _ExitAbort("exit_request_drift", live)
            self._require_live_attempt(transaction, live)
            transaction.save_session(_session_write(session, None))
            transaction.append_event(_event(
                command_id, "exit_declined", self._session_id, live, decision_at
            ))
            return {
                "attempt_hash": live.attempt_hash,
                "mode": "exit_declined",
                "request_id": live.request_id,
            }

        try:
            result = self._store.execute_once(
                command_id, "decline_product_exit", persist
            )
        except _ExitAbort as error:
            return self._failure(error.code, request=error.request)
        try:
            stored_id, stored_hash = _decline_facts(result)
            if stored_id != pending.request_id:
                raise ValueError
            ExitRequest(stored_id, pending.attempt, stored_hash, pending.requested_at)
        except (TypeError, ValueError):
            raise ValueError("stored exit decline result is malformed") from None
        return ExitResult("exit_declined", False)

    def confirm(self, request_id: str, attempt_hash: str) -> ExitResult:
        if not _valid_request_id(request_id) or not _valid_lower_hex(attempt_hash, 64):
            return self._failure("exit_request_identity_mismatch")
        checked = self._decision_authority(request_id, attempt_hash)
        if isinstance(checked, ExitResult):
            return checked
        return self._failure(
            "exit_cancellation_unavailable", request=checked,
        )

    def input_closed(self) -> ExitResult:
        attempts = self._active_attempts()
        if attempts is None:
            return self._failure("exit_authority_invalid", should_exit=True)
        if attempts:
            return self._failure(
                "exit_input_closed_with_active_work", should_exit=True,
            )
        return ExitResult("exit_ready", True)

    def _decision_authority(
        self, request_id: str, attempt_hash: str,
    ) -> ExitRequest | ExitResult:
        session, pending = self._load_pending()
        if session is None:
            return self._failure("exit_request_malformed")
        if pending is None:
            return self._failure("exit_request_missing")
        request_matches = request_id == pending.request_id
        hash_matches = compare_digest(attempt_hash, pending.attempt_hash)
        if not request_matches or not hash_matches:
            code = (
                "exit_request_drift"
                if not request_matches and not hash_matches
                else "exit_request_identity_mismatch"
            )
            return self._failure(code, request=pending)
        attempts = self._active_attempts()
        if attempts is None:
            return self._failure("exit_authority_invalid", request=pending)
        matching = tuple(
            attempt for attempt in attempts
            if attempt.attempt_id == pending.attempt.attempt_id
        )
        if not matching:
            durable = self._store.load_aggregate(
                "attempts", pending.attempt.attempt_id
            )
            code = "exit_attempt_missing" if durable is None else "exit_request_drift"
            return self._failure(code, request=pending)
        if len(attempts) > 1:
            return self._failure("exit_active_attempt_ambiguous", request=pending)
        if not _same_attempt(matching[0], pending.attempt):
            return self._failure("exit_request_drift", request=pending)
        return pending

    def _decline_replay(
        self, request_id: str, attempt_hash: str,
    ) -> ExitResult | None:
        try:
            result = self._store.lookup_command(
                f"exit:decline:{request_id}", "decline_product_exit"
            )
        except (TypeError, ValueError, RuntimeError):
            return self._failure("exit_authority_invalid")
        if result is None:
            return None
        try:
            stored_id, stored_hash = _decline_facts(result)
        except (TypeError, ValueError):
            return self._failure("exit_authority_invalid")
        hash_matches = compare_digest(attempt_hash, stored_hash)
        if stored_id != request_id:
            return self._failure("exit_authority_invalid")
        if not hash_matches:
            return self._failure("exit_request_identity_mismatch")
        return ExitResult("exit_declined", False)

    def _require_live_attempt(
        self, transaction: StoreTransaction, pending: ExitRequest,
    ) -> None:
        attempts = transaction.list_active_exit_attempts()
        matching = tuple(
            attempt for attempt in attempts
            if attempt.attempt_id == pending.attempt.attempt_id
        )
        if not matching:
            durable = transaction.load_aggregate("attempts", pending.attempt.attempt_id)
            raise _ExitAbort(
                "exit_attempt_missing" if durable is None else "exit_request_drift",
                pending,
            )
        if len(attempts) > 1:
            raise _ExitAbort("exit_active_attempt_ambiguous", pending)
        if not _same_attempt(matching[0], pending.attempt):
            raise _ExitAbort("exit_request_drift", pending)

    def _active_attempts(self) -> tuple[ExitAttemptSnapshot, ...] | None:
        try:
            attempts = self._store.list_active_exit_attempts()
        except (TypeError, ValueError, RuntimeError):
            return None
        if type(attempts) is not tuple or any(
            type(attempt) is not ExitAttemptSnapshot for attempt in attempts
        ):
            return None
        return attempts

    def _load_pending(
        self,
    ) -> tuple[Mapping[str, object] | None, ExitRequest | None]:
        try:
            session = self._store.load_aggregate(
                "product_sessions", self._session_id
            )
            if session is None:
                return None, None
            return session, _request_from_session(session)
        except (TypeError, ValueError, RuntimeError):
            return None, None

    def _transaction_pending(
        self, transaction: StoreTransaction,
    ) -> tuple[Mapping[str, object], ExitRequest | None]:
        session = transaction.load_aggregate("product_sessions", self._session_id)
        if session is None:
            raise _ExitAbort("exit_session_missing")
        try:
            return session, _request_from_session(session)
        except (TypeError, ValueError, RuntimeError):
            raise _ExitAbort("exit_request_malformed") from None

    def _failure(
        self,
        code: str,
        *,
        request: ExitRequest | None = None,
        should_exit: bool = False,
    ) -> ExitResult:
        if code not in _DIAGNOSTIC_CODES:
            raise ValueError("exit diagnostic code is not allowlisted")
        diagnostic = Diagnostic.create(
            code=code,
            stage="exit",
            severity=Severity.WARNING,
            actor="agentdeck",
            summary="The requested exit action could not be completed.",
            cause="The durable exit authority did not match the required state.",
            impact="No active Attempt was changed by this decision.",
            protection="AgentDeck kept the authoritative work and decision state intact.",
            recovery_actions=("Review the current exit status and retry explicitly.",),
            retryable=True,
            outcome_known=True,
            occurred_at=self._now(),
            attempt_id=None if request is None else request.attempt.attempt_id,
        )
        return ExitResult("diagnostic", should_exit, request, diagnostic)

    def _now(self) -> str:
        value = self._clock.now()
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).isoformat()


def _same_attempt(left: ExitAttemptSnapshot, right: ExitAttemptSnapshot) -> bool:
    return left.attempt_id == right.attempt_id and _compare_internal(
        left.content_hash, right.content_hash
    )


def _same_request(left: ExitRequest, right: ExitRequest) -> bool:
    return left.request_id == right.request_id and _compare_internal(
        left.attempt_hash, right.attempt_hash
    ) and left.requested_at == right.requested_at and _same_attempt(
        left.attempt, right.attempt
    )


def _same_optional_request(left: ExitRequest | None, right: ExitRequest | None) -> bool:
    if left is None or right is None:
        return left is right
    return _same_request(left, right)
