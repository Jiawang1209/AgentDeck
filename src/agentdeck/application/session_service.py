"""Application sequencing for ProductSession setup and retained goals."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Final
from uuid import uuid4

from agentdeck.application.session_validation import (
    SessionServiceError,
    bounded_text,
    canonical_clock_time,
    canonical_project_root,
    project_root_digest,
    snapshot_available_leaders,
    validate_accept_result,
    validate_command_id,
    validate_creation_result,
    validate_durable_turn,
    validate_goal,
)
from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.permissions import PermissionProfile
from agentdeck.kernel.session import ProductSession, SessionState
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import CommandResult, Store, StoreTransaction


_MAX_SELECTION_BYTES: Final = 4_096


@dataclass(frozen=True)
class SessionView:
    session_id: str
    project_root: str
    state: SessionState
    pending_goal: str | None
    leader_backend: str | None
    model: str | None
    permission: str | None


@dataclass(frozen=True)
class SessionResult:
    mode: str
    accepted: bool
    goal: str | None = None
    diagnostic: Diagnostic | None = None


class SessionService:
    """Own one Store-backed ProductSession without selecting a fallback Leader."""

    def __init__(
        self,
        *,
        store: Store,
        clock: Clock,
        session_id: str,
        project_root: str,
        available_leaders: Mapping[str, tuple[str, ...]],
    ) -> None:
        _require_dependency(store, ("execute_once", "load_aggregate", "lookup_command"), "store")
        _require_dependency(clock, ("now",), "clock")
        self._store = store
        self._clock = clock
        self._available_leaders = snapshot_available_leaders(available_leaders)
        root = canonical_project_root(project_root)
        self._session = self._create_or_load(session_id, root)
        self._leader_backend: str | None = None
        self._model: str | None = None
        self._permission = self._load_permission()
        self._restore_configuration()

    def _create_or_load(self, session_id: str, project_root: str) -> ProductSession:
        candidate = ProductSession.new(session_id, project_root)
        root_digest = project_root_digest(project_root)
        command_id = f"session:create:{session_id}"
        loaded = self._store.load_aggregate("product_sessions", session_id)
        if loaded is not None:
            result = self._store.lookup_command(command_id, "create_product_session")
            validate_creation_result(result, session_id, root_digest)
            return _session_from_snapshot(loaded, project_root)

        def create(transaction: StoreTransaction) -> CommandResult:
            transaction.save_session(_session_snapshot(candidate, None))
            transaction.append_event(_event(
                command_id,
                "session_created",
                session_id,
                {"state": SessionState.SETUP.value},
                self._now(),
            ))
            return {
                "project_root_digest": root_digest,
                "session_id": session_id,
                "state": SessionState.SETUP.value,
            }

        result = self._store.execute_once(command_id, "create_product_session", create)
        validate_creation_result(result, session_id, root_digest)
        loaded = self._store.load_aggregate("product_sessions", session_id)
        if loaded is None:
            raise SessionServiceError("created ProductSession is unavailable")
        return _session_from_snapshot(loaded, project_root)

    def accept_text(
        self, text: str, *, command_id: str | None = None
    ) -> SessionResult:
        goal = validate_goal(text)
        if self._session.state is not SessionState.SETUP:
            raise SessionServiceError("goal retention requires setup state")
        stable_id = (
            _new_turn_command_id(self._session.session_id)
            if command_id is None
            else validate_command_id(command_id)
        )
        turn_id = _turn_id(self._session.session_id, stable_id)
        updated = self._session.retain_goal(goal)

        def retain(transaction: StoreTransaction) -> CommandResult:
            occurred_at = self._now()
            transaction.save_session(_session_snapshot(updated, self._permission))
            transaction.save_aggregate("conversation_turns", turn_id, {
                "actor_role": "human",
                "occurred_at": occurred_at,
                "sanitized_content": goal,
                "session_id": updated.session_id,
                "turn_id": turn_id,
            })
            durable_turn = transaction.load_aggregate("conversation_turns", turn_id)
            if durable_turn is None:
                raise SessionServiceError("durable turn is missing or malformed")
            transaction.append_event(_event(
                stable_id,
                "conversation_turn_recorded",
                updated.session_id,
                {"actor_role": "human", "sanitized_content": goal},
                occurred_at,
            ))
            return {
                "accepted": True,
                "goal": goal,
                "mode": "setup_required",
                "session_id": updated.session_id,
                "turn_id": turn_id,
                "turn_occurred_at": durable_turn["occurred_at"],
                "turn_ordinal": durable_turn["ordinal"],
            }

        result = self._store.execute_once(stable_id, "accept_session_text", retain)
        self._reload_session()
        if result.get("session_id") != self._session.session_id:
            raise SessionServiceError("stored accept result lineage is invalid")
        durable_goal = self._session.pending_goal
        if durable_goal is None:
            raise SessionServiceError("stored accept result is malformed")
        turn_ordinal, turn_occurred_at = validate_accept_result(
            result, session_id=self._session.session_id, goal=durable_goal, turn_id=turn_id
        )
        turn = self._store.load_aggregate("conversation_turns", turn_id)
        validate_durable_turn(
            turn, session_id=self._session.session_id, goal=durable_goal, turn_id=turn_id,
            ordinal=turn_ordinal, occurred_at=turn_occurred_at,
        )
        return SessionResult(mode="setup_required", accepted=True, goal=durable_goal)

    def configure(
        self,
        *,
        leader: str,
        model: str,
        permission: str = PermissionProfile.APPROVE_FOR_ME.value,
    ) -> SessionResult:
        leader = bounded_text(leader, "leader", _MAX_SELECTION_BYTES)
        model = bounded_text(model, "model", _MAX_SELECTION_BYTES)
        diagnostic_code = self._selection_error(leader, model, permission)
        if diagnostic_code is not None:
            return SessionResult(
                mode="setup_required",
                accepted=False,
                goal=self._session.pending_goal,
                diagnostic=self._diagnostic(diagnostic_code),
            )
        profile = PermissionProfile(permission)
        command_id = f"session:configure:{self._session.session_id}"
        if self._session.state is not SessionState.SETUP:
            existing = self._store.lookup_command(command_id, "configure_product_session")
            if existing is None:
                raise SessionServiceError("session setup state is inconsistent")
            parsed = _result_from_command(existing, self._session.session_id)
            self._apply_configuration_result(existing)
            return parsed
        updated = self._session.transition(SessionState.READY)

        def persist(transaction: StoreTransaction) -> CommandResult:
            transaction.save_session(_session_snapshot(updated, profile.value))
            transaction.append_event(_event(
                command_id,
                "session_configured",
                updated.session_id,
                {
                    "leader_backend": leader,
                    "model": model,
                    "permission_profile": profile.value,
                },
                self._now(),
            ))
            return {
                "accepted": True,
                "goal": updated.pending_goal,
                "leader_backend": leader,
                "mode": "goal_ready" if updated.pending_goal is not None else "ready",
                "model": model,
                "permission": profile.value,
                "session_id": updated.session_id,
            }

        result = self._store.execute_once(
            command_id, "configure_product_session", persist
        )
        self._reload_session()
        parsed = _result_from_command(result, self._session.session_id)
        self._apply_configuration_result(result)
        return parsed

    def resume(self) -> SessionResult:
        self._reload_session()
        if self._session.state is SessionState.SETUP:
            mode = "setup_required"
        elif self._session.pending_goal is not None:
            mode = "goal_ready"
        else:
            mode = self._session.state.value
        return SessionResult(
            mode=mode,
            accepted=True,
            goal=self._session.pending_goal,
        )

    def current(self) -> SessionView:
        return SessionView(
            session_id=self._session.session_id,
            project_root=self._session.project_root,
            state=self._session.state,
            pending_goal=self._session.pending_goal,
            leader_backend=self._leader_backend,
            model=self._model,
            permission=self._permission,
        )

    def _reload_session(self) -> None:
        loaded = self._store.load_aggregate(
            "product_sessions", self._session.session_id
        )
        if loaded is None:
            raise SessionServiceError("ProductSession disappeared from Store")
        self._session = _session_from_snapshot(loaded, self._session.project_root)
        self._permission = self._load_permission()

    def _load_permission(self) -> str | None:
        loaded = self._store.load_aggregate(
            "product_sessions", self._session.session_id
        )
        if loaded is None:
            raise SessionServiceError("ProductSession is unavailable")
        permission = loaded.get("permission_profile")
        if permission is not None and permission not in {
            profile.value for profile in PermissionProfile
        }:
            raise SessionServiceError("stored permission profile is invalid")
        return permission

    def _apply_configuration_result(self, result: CommandResult) -> None:
        if set(result) != {
            "accepted", "goal", "leader_backend", "mode", "model", "permission",
            "session_id",
        }:
            raise SessionServiceError("stored setup result is malformed")
        leader = result.get("leader_backend")
        model = result.get("model")
        permission = result.get("permission")
        if (
            result.get("accepted") is not True
            or type(leader) is not str
            or type(model) is not str
            or type(permission) is not str
        ):
            raise SessionServiceError("stored setup result is malformed")
        checked_leader = bounded_text(leader, "stored leader", _MAX_SELECTION_BYTES)
        checked_model = bounded_text(model, "stored model", _MAX_SELECTION_BYTES)
        if permission not in {profile.value for profile in PermissionProfile}:
            raise SessionServiceError("stored setup permission is invalid")
        expected_mode = "goal_ready" if self._session.pending_goal is not None else "ready"
        if (
            permission != self._permission
            or self._session.state is SessionState.SETUP
            or result.get("goal") != self._session.pending_goal
            or result.get("mode") != expected_mode
        ):
            raise SessionServiceError("stored setup result conflicts with ProductSession")
        self._leader_backend = checked_leader
        self._model = checked_model
        self._permission = permission

    def _restore_configuration(self) -> None:
        command_id = f"session:configure:{self._session.session_id}"
        result = self._store.lookup_command(command_id, "configure_product_session")
        if self._session.state is SessionState.SETUP:
            if result is not None:
                raise SessionServiceError("setup command conflicts with ProductSession")
            return
        if result is None:
            raise SessionServiceError("configured ProductSession has no durable setup result")
        _result_from_command(result, self._session.session_id)
        self._apply_configuration_result(result)

    def _selection_error(
        self, leader: str, model: str, permission: str
    ) -> str | None:
        if type(permission) is not str or permission not in {
            profile.value for profile in PermissionProfile
        }:
            return "permission_profile_invalid"
        if leader not in self._available_leaders:
            return (
                "leader_credential_unavailable"
                if leader.startswith("api:")
                else "leader_unavailable"
            )
        if model not in self._available_leaders[leader]:
            return "leader_model_unavailable"
        return None

    def _diagnostic(self, code: str) -> Diagnostic:
        causes = {
            "leader_credential_unavailable": "The API Leader has no validated credential source.",
            "leader_unavailable": "The selected Leader was not discovered as ready.",
            "leader_model_unavailable": "The selected model was not validated for this Leader.",
            "permission_profile_invalid": "The permission profile is not supported.",
        }
        return Diagnostic.create(
            code=code,
            stage="setup",
            severity=Severity.ERROR,
            actor="agentdeck",
            summary="The requested setup selection was not applied.",
            cause=causes[code],
            impact="The ProductSession remains in setup.",
            protection="AgentDeck did not select a fallback or change session authority.",
            recovery_actions=("Choose an available Leader, model, and permission profile.",),
            retryable=True,
            outcome_known=True,
            occurred_at=self._now(),
        )

    def _now(self) -> str:
        return canonical_clock_time(self._clock.now())


def _require_dependency(value: object, methods: tuple[str, ...], label: str) -> None:
    if any(not callable(getattr(value, method, None)) for method in methods):
        raise TypeError(f"{label} does not satisfy the session dependency")


def _session_from_snapshot(
    snapshot: Mapping[str, object], project_root: str
) -> ProductSession:
    try:
        session_id = snapshot["session_id"]
        state = snapshot["state"]
        pending_goal = snapshot.get("pending_goal")
        if type(session_id) is not str or type(state) is not str:
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


def _session_snapshot(
    session: ProductSession, permission: str | None
) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "state": session.state.value,
        "permission_profile": permission,
        "pending_goal": session.pending_goal,
    }


def _event(
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


def _new_turn_command_id(session_id: str) -> str:
    session_digest = sha256(session_id.encode("utf-8", "strict")).hexdigest()[:16]
    return f"session:text:{session_digest}:{uuid4().hex}"


def _turn_id(session_id: str, command_id: str) -> str:
    digest = sha256(
        f"{session_id}:{command_id}".encode("utf-8", "strict")
    ).hexdigest()[:32]
    return f"trn_{digest}"


def _result_from_command(
    result: CommandResult, expected_session_id: str
) -> SessionResult:
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
    return SessionResult(mode=mode, accepted=accepted, goal=goal)
