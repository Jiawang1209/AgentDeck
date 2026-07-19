"""Pure ProductSession snapshots, identities, events, and result projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

from agentdeck.application.session_validation import SessionServiceError, bounded_text
from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.session import ProductSession, SessionState
from agentdeck.ports.store import CommandResult, SessionSelection, Store


_OMITTED = object()


def session_from_snapshot(
    snapshot: Mapping[str, object], project_root: str,
) -> ProductSession:
    try:
        session_id = snapshot["session_id"]
        state = snapshot["state"]
        pending_goal = snapshot.get("pending_goal")
        validated_session_identity(session_id)
        if type(state) is not str:
            raise TypeError
        if pending_goal is not None and type(pending_goal) is not str:
            raise TypeError
        return ProductSession(
            session_id=session_id,
            project_root=project_root,
            state=SessionState(state),
            pending_goal=pending_goal,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SessionServiceError("stored ProductSession is malformed") from error


def validated_session_identity(value: object) -> str:
    try:
        selection = SessionSelection(value, 1)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SessionServiceError("ProductSession identity is invalid") from None
    assert selection.session_id is not None
    return selection.session_id


def project_id_for_root(project_root: str) -> str:
    digest = sha256(project_root.encode("utf-8", "strict")).hexdigest()[:24]
    return f"prj_{digest}"


def load_session_command(
    store: Store, command_id: str, command_kind: str,
) -> CommandResult | None:
    try:
        return store.lookup_command(command_id, command_kind)
    except (TypeError, ValueError, RuntimeError):
        raise SessionServiceError(
            "stored ProductSession command authority is invalid"
        ) from None


def validated_configuration_facts(
    result: CommandResult,
) -> tuple[str, str, str]:
    if set(result) != {
        "accepted", "goal", "leader_backend", "mode", "model", "permission",
        "session_id",
    }:
        raise SessionServiceError("stored setup result is malformed")
    leader = result.get("leader_backend")
    model = result.get("model")
    permission = result.get("permission")
    if result.get("accepted") is not True or type(permission) is not str:
        raise SessionServiceError("stored setup result is malformed")
    try:
        checked_leader = bounded_text(leader, "stored leader", 4_096)
        checked_model = bounded_text(model, "stored model", 4_096)
    except (TypeError, ValueError):
        raise SessionServiceError("stored setup result is malformed") from None
    if permission not in {"ask_for_approval", "approve_for_me", "full_access"}:
        raise SessionServiceError("stored setup permission is invalid")
    return checked_leader, checked_model, permission


def session_snapshot(
    session: ProductSession,
    permission: str | None,
    *,
    leader_backend: object = _OMITTED,
    leader_model: object = _OMITTED,
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "session_id": session.session_id,
        "state": session.state.value,
        "permission_profile": permission,
        "pending_goal": session.pending_goal,
    }
    if leader_backend is not _OMITTED:
        snapshot["leader_backend"] = leader_backend
    if leader_model is not _OMITTED:
        snapshot["leader_model"] = leader_model
    return snapshot


def session_event(
    command_id: str,
    kind: str,
    session_id: str,
    payload: Mapping[str, object],
    occurred_at: str,
) -> DomainEvent:
    digest = sha256(f"{command_id}:{kind}".encode("utf-8", "strict")).hexdigest()[:32]
    return replace(DomainEvent.create(
        kind=kind,
        aggregate_type="product_session",
        aggregate_id=session_id,
        payload=payload,
        occurred_at=occurred_at,
    ), event_id=f"evt_{digest}")


def new_turn_command_id(session_id: str) -> str:
    session_digest = sha256(session_id.encode("utf-8", "strict")).hexdigest()[:16]
    return f"session:text:{session_digest}:{uuid4().hex}"


def turn_id(session_id: str, command_id: str) -> str:
    digest = sha256(
        f"{session_id}:{command_id}".encode("utf-8", "strict")
    ).hexdigest()[:32]
    return f"trn_{digest}"


def result_from_command(
    result: CommandResult, expected_session_id: str,
) -> tuple[str, bool, str | None]:
    mode = result.get("mode")
    accepted = result.get("accepted")
    goal = result.get("goal")
    if result.get("session_id") != expected_session_id:
        raise SessionServiceError("stored session command lineage is invalid")
    if (
        type(mode) is not str
        or type(accepted) is not bool
        or (goal is not None and type(goal) is not str)
    ):
        raise SessionServiceError("stored session command result is malformed")
    return mode, accepted, goal
