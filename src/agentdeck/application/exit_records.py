"""Read-only projection of durable ProductSession exit authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hmac import compare_digest
import json

from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot, ExitRequest
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import Store, _session_identity


EXIT_FIELDS = (
    "pending_exit_id", "pending_exit_attempt_id",
    "canonical_pending_exit_attempt_facts", "pending_exit_attempt_hash",
    "pending_exit_requested_at",
)
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


def snapshot_from_facts(value: object) -> ExitAttemptSnapshot:
    if type(value) is not dict or set(value) != {
        "attempt_id", "task_id", "agent_instance_id", "ordinal", "state",
        "acp_session_id", "effect_observed", "durable_fingerprint",
    }:
        raise ValueError("exit snapshot shape is invalid")
    if type(value["state"]) is not str:
        raise ValueError("exit snapshot state is invalid")
    return ExitAttemptSnapshot(
        attempt_id=value["attempt_id"], task_id=value["task_id"],
        agent_instance_id=value["agent_instance_id"], ordinal=value["ordinal"],
        state=AttemptState(value["state"]), acp_session_id=value["acp_session_id"],
        effect_observed=value["effect_observed"],
        durable_fingerprint=value["durable_fingerprint"],
    )


def request_from_session(session: Mapping[str, object]) -> ExitRequest | None:
    if not all(field in session for field in EXIT_FIELDS):
        raise ValueError("pending exit group is absent")
    values = tuple(session[field] for field in EXIT_FIELDS)
    if values == (None,) * len(EXIT_FIELDS):
        return None
    if any(value is None for value in values):
        raise ValueError("pending exit group is partial")
    request_id, attempt_id, canonical, attempt_hash, requested_at = values
    if type(canonical) is not str:
        raise ValueError("pending exit canonical facts are invalid")
    snapshot = snapshot_from_facts(json.loads(canonical))
    request = ExitRequest(request_id, snapshot, attempt_hash, requested_at)
    if attempt_id != snapshot.attempt_id or canonical != (
        snapshot.canonical_bytes().decode("utf-8")
    ):
        raise ValueError("pending exit lineage is invalid")
    return request


def restore_pending_exit(
    *, store: Store, clock: Clock, session_id: str,
) -> ExitResult | None:
    """Project persisted exit state without creating or superseding a request."""

    session_id = _session_identity(session_id)
    try:
        session = store.load_aggregate("product_sessions", session_id)
        if session is None:
            return exit_failure(clock, "exit_session_missing")
        pending = request_from_session(session)
    except (TypeError, ValueError, RuntimeError, json.JSONDecodeError):
        return exit_failure(clock, "exit_request_malformed")
    if pending is None:
        return None
    try:
        attempts = store.list_active_exit_attempts(session_id)
    except (TypeError, ValueError, RuntimeError):
        return exit_failure(clock, "exit_authority_invalid", request=pending)
    if type(attempts) is not tuple or any(
        type(attempt) is not ExitAttemptSnapshot for attempt in attempts
    ):
        return exit_failure(clock, "exit_authority_invalid", request=pending)
    if len(attempts) > 1:
        return exit_failure(clock, "exit_active_attempt_ambiguous", request=pending)
    if not attempts:
        try:
            durable = store.load_aggregate("attempts", pending.attempt.attempt_id)
        except (TypeError, ValueError, RuntimeError):
            return exit_failure(clock, "exit_authority_invalid", request=pending)
        code = "exit_attempt_missing" if durable is None else "exit_request_drift"
        return exit_failure(clock, code, request=pending)
    if not same_attempt(attempts[0], pending.attempt):
        return exit_failure(clock, "exit_request_drift", request=pending)
    return ExitResult("exit_confirmation_required", False, pending)


def exit_failure(
    clock: Clock,
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
        occurred_at=_now(clock),
        attempt_id=None if request is None else request.attempt.attempt_id,
    )
    return ExitResult("diagnostic", should_exit, request, diagnostic)


def same_attempt(left: ExitAttemptSnapshot, right: ExitAttemptSnapshot) -> bool:
    return left.attempt_id == right.attempt_id and compare_digest(
        left.content_hash, right.content_hash
    )


def _now(clock: Clock) -> str:
    value = clock.now()
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat()
