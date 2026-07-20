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
    "cancel_rejected", "cancel_timeout", "transport_disconnected",
    "exit_active_attempt_ambiguous", "exit_attempt_missing",
    "exit_authority_changed_after_cancel", "exit_authority_invalid",
    "exit_binding_drift", "exit_cancellation_unavailable",
    "exit_input_closed_with_active_work", "exit_request_drift",
    "exit_request_identity_mismatch", "exit_request_malformed",
    "exit_request_missing", "exit_session_missing", "project_dispatch_paused",
})
ACTIVE_EXIT_RESULT_FIELDS = frozenset({
    "attempt_hash", "attempt_id", "diagnostic_code", "mode",
    "outcome_known", "request_id", "should_exit",
})


@dataclass(frozen=True)
class ExitResult:
    mode: str
    should_exit: bool
    request: ExitRequest | None = None
    diagnostic: Diagnostic | None = None


def closed_exit_result(
    *, request: ExitRequest | None, mode: str,
    diagnostic_code: str | None, outcome_known: bool, should_exit: bool,
) -> dict[str, object]:
    if type(outcome_known) is not bool or type(should_exit) is not bool:
        raise TypeError("closed exit booleans must be exact")
    if request is not None and type(request) is not ExitRequest:
        raise TypeError("request must be an ExitRequest or None")
    if mode == "project_paused":
        if diagnostic_code is not None or not outcome_known or not should_exit:
            raise ValueError("project pause result is invalid")
    elif mode == "diagnostic":
        if (
            type(diagnostic_code) is not str
            or diagnostic_code not in _DIAGNOSTIC_CODES
            or should_exit
            or request is None
        ):
            raise ValueError("closed exit diagnostic is invalid")
    else:
        raise ValueError("closed exit mode is invalid")
    return {
        "attempt_hash": None if request is None else request.attempt_hash,
        "attempt_id": None if request is None else request.attempt.attempt_id,
        "diagnostic_code": diagnostic_code,
        "mode": mode,
        "outcome_known": outcome_known,
        "request_id": None if request is None else request.request_id,
        "should_exit": should_exit,
    }


def exit_result_from_command(result: object, *, clock: Clock) -> ExitResult:
    if type(result) is not dict or set(result) != ACTIVE_EXIT_RESULT_FIELDS:
        raise ValueError("stored exit result is malformed")
    mode = result["mode"]
    code = result["diagnostic_code"]
    known = result["outcome_known"]
    should_exit = result["should_exit"]
    attempt_id = result["attempt_id"]
    attempt_hash = result["attempt_hash"]
    request_id = result["request_id"]
    if type(known) is not bool or type(should_exit) is not bool:
        raise ValueError("stored exit result is malformed")
    empty = (attempt_id, attempt_hash, request_id) == (None, None, None)
    populated = (
        type(attempt_id) is str and attempt_id.startswith("att_")
        and bool(attempt_id.removeprefix("att_")) and len(attempt_id.encode("utf-8")) <= 255
        and type(attempt_hash) is str and len(attempt_hash) == 64
        and all(character in "0123456789abcdef" for character in attempt_hash)
        and type(request_id) is str and request_id.startswith("xrt_")
        and len(request_id) == 36
        and all(character in "0123456789abcdef" for character in request_id[4:])
    )
    if not (empty or populated):
        raise ValueError("stored exit result is malformed")
    if mode == "project_paused":
        valid_result = code is None and known and should_exit
    else:
        valid_result = (
            mode == "diagnostic" and type(code) is str
            and code in _DIAGNOSTIC_CODES and not should_exit and populated
        )
    if not valid_result:
        raise ValueError("stored exit result is malformed")
    if mode == "project_paused":
        return ExitResult(mode, True)
    return exit_failure(
        clock, code, outcome_known=known, attempt_id=attempt_id,
    )


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
    outcome_known: bool = True,
    attempt_id: str | None = None,
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
        outcome_known=outcome_known,
        occurred_at=_now(clock),
        attempt_id=(
            request.attempt.attempt_id if request is not None else attempt_id
        ),
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
