"""Application sequencing for ProductSession setup and retained goals."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from agentdeck.application.session_records import (
    load_session_command,
    new_turn_command_id,
    project_id_for_root,
    result_from_command,
    session_event,
    session_from_snapshot,
    session_snapshot,
    turn_id,
    validated_configuration_facts,
    validated_session_identity,
)

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
from agentdeck.kernel.permissions import PermissionProfile
from agentdeck.kernel.session import ProductSession, SessionState
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import (
    CommandResult,
    SessionSelection,
    Store,
    StoreTransaction,
)


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
    reentry_diagnostic: Diagnostic | None = None


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
        self._nonterminal_count = 1
        self._available_leaders = snapshot_available_leaders(available_leaders)
        root = canonical_project_root(project_root)
        self._session = self._create_or_load(session_id, root)
        self._leader_backend: str | None = None
        self._model: str | None = None
        self._permission = self._load_permission()
        self._restore_configuration()

    @classmethod
    def open_latest(
        cls,
        *,
        store: Store,
        clock: Clock,
        project_root: str,
        available_leaders: Mapping[str, tuple[str, ...]],
        session_id_factory: Callable[[], str],
    ) -> "SessionService":
        _require_dependency(
            store,
            (
                "execute_once", "load_aggregate", "lookup_command",
                "select_latest_nonterminal_session",
            ),
            "store",
        )
        if not callable(session_id_factory):
            raise TypeError("session_id_factory must be callable")
        try:
            selection = store.select_latest_nonterminal_session()
        except (TypeError, ValueError, RuntimeError):
            raise SessionServiceError(
                "ProductSession selection authority is invalid"
            ) from None
        if type(selection) is not SessionSelection:
            raise SessionServiceError("ProductSession selection authority is invalid")
        session_id = selection.session_id
        if session_id is None:
            session_id = validated_session_identity(session_id_factory())
            if store.load_aggregate("product_sessions", session_id) is not None:
                raise SessionServiceError(
                    "ProductSession factory identity is already durable")
        service = cls(
            store=store,
            clock=clock,
            session_id=session_id,
            project_root=project_root,
            available_leaders=available_leaders,
        )
        service._nonterminal_count = selection.nonterminal_count
        return service

    def _create_or_load(self, session_id: str, project_root: str) -> ProductSession:
        session_id = validated_session_identity(session_id)
        candidate = ProductSession.new(session_id, project_root)
        root_digest = project_root_digest(project_root)
        command_id = f"session:create:{session_id}"
        loaded = self._store.load_aggregate("product_sessions", session_id)
        if loaded is not None:
            if loaded.get("project_id") != project_id_for_root(project_root):
                raise SessionServiceError("ProductSession project root does not match")
            result = load_session_command(
                self._store, command_id, "create_product_session")
            if result is not None:
                validate_creation_result(result, session_id, root_digest)
            restored = session_from_snapshot(loaded, project_root)
            if restored.session_id != session_id:
                raise SessionServiceError("stored ProductSession identity is invalid")
            return restored

        def create(transaction: StoreTransaction) -> CommandResult:
            transaction.save_session(session_snapshot(candidate, None))
            transaction.append_event(session_event(
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
        return session_from_snapshot(loaded, project_root)

    def accept_text(
        self, text: str, *, command_id: str | None = None
    ) -> SessionResult:
        goal = validate_goal(text)
        stable_id = (
            new_turn_command_id(self._session.session_id)
            if command_id is None
            else validate_command_id(command_id)
        )
        turn_id_value = turn_id(self._session.session_id, stable_id)
        if command_id is not None:
            existing = self._store.lookup_command(stable_id, "accept_session_text")
            if existing is not None:
                return self._validated_accept_result(existing, goal, turn_id_value)
        if self._session.state is not SessionState.SETUP:
            raise SessionServiceError("goal retention requires setup state")
        updated = self._session.retain_goal(goal)

        def retain(transaction: StoreTransaction) -> CommandResult:
            occurred_at = self._now()
            transaction.save_session(session_snapshot(updated, self._permission))
            transaction.save_aggregate("conversation_turns", turn_id_value, {
                "actor_role": "human",
                "occurred_at": occurred_at,
                "sanitized_content": goal,
                "session_id": updated.session_id,
                "turn_id": turn_id_value,
            })
            durable_turn = transaction.load_aggregate(
                "conversation_turns", turn_id_value
            )
            if durable_turn is None:
                raise SessionServiceError("durable turn is missing or malformed")
            transaction.append_event(session_event(
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
                "turn_id": turn_id_value,
                "turn_occurred_at": durable_turn["occurred_at"],
                "turn_ordinal": durable_turn["ordinal"],
            }

        result = self._store.execute_once(stable_id, "accept_session_text", retain)
        self._reload_session()
        if self._session.pending_goal != goal:
            raise SessionServiceError("stored accept result is malformed")
        return self._validated_accept_result(result, goal, turn_id_value)

    def _validated_accept_result(
        self, result: CommandResult, goal: str, turn_id: str
    ) -> SessionResult:
        if result.get("goal") != goal:
            raise SessionServiceError(
                "stored accept result does not match accept command input lineage"
            )
        turn_ordinal, turn_occurred_at = validate_accept_result(
            result, session_id=self._session.session_id, goal=goal, turn_id=turn_id
        )
        turn = self._store.load_aggregate("conversation_turns", turn_id)
        validate_durable_turn(
            turn, session_id=self._session.session_id, goal=goal, turn_id=turn_id,
            ordinal=turn_ordinal, occurred_at=turn_occurred_at,
        )
        return _result_from_command(result, self._session.session_id)

    def configure(
        self,
        *,
        leader: str,
        model: str,
        permission: str = PermissionProfile.APPROVE_FOR_ME.value,
    ) -> SessionResult:
        leader = bounded_text(leader, "leader", _MAX_SELECTION_BYTES)
        model = bounded_text(model, "model", _MAX_SELECTION_BYTES)
        command_id = f"session:configure:{self._session.session_id}"
        existing = self._store.lookup_command(command_id, "configure_product_session")
        if existing is not None:
            parsed = _result_from_command(existing, self._session.session_id)
            self._apply_configuration_result(existing)
            if (
                existing.get("leader_backend"), existing.get("model"),
                existing.get("permission"),
            ) != (leader, model, permission):
                raise SessionServiceError(
                    "stored setup command selection lineage is invalid"
                )
            return parsed
        diagnostic_code = self._selection_error(leader, model, permission)
        if diagnostic_code is not None:
            return SessionResult(
                mode="setup_required",
                accepted=False,
                goal=self._session.pending_goal,
                diagnostic=self._diagnostic(diagnostic_code),
            )
        profile = PermissionProfile(permission)
        if self._session.state is not SessionState.SETUP:
            raise SessionServiceError("session setup state is inconsistent")
        updated = self._session.transition(SessionState.READY)

        def persist(transaction: StoreTransaction) -> CommandResult:
            transaction.save_session(session_snapshot(
                updated,
                profile.value,
                leader_backend=leader,
                leader_model=model,
            ))
            transaction.append_event(session_event(
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
            reentry_diagnostic=self._reentry_warning(),
        )

    def _reload_session(self) -> None:
        loaded = self._store.load_aggregate(
            "product_sessions", self._session.session_id
        )
        if loaded is None:
            raise SessionServiceError("ProductSession disappeared from Store")
        self._session = session_from_snapshot(loaded, self._session.project_root)
        self._permission = self._load_permission()
        self._restore_configuration()

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
        checked_leader, checked_model, permission = validated_configuration_facts(
            result)
        expected_mode = "goal_ready" if self._session.pending_goal is not None else "ready"
        stored = self._store.load_aggregate(
            "product_sessions", self._session.session_id
        )
        if (
            stored is None
            or stored.get("leader_backend") != checked_leader
            or stored.get("leader_model") != checked_model
            or permission != self._permission
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
        result = load_session_command(
            self._store, command_id, "configure_product_session")
        if self._session.state is SessionState.SETUP:
            stored = self._store.load_aggregate(
                "product_sessions", self._session.session_id
            )
            if (
                result is not None
                or stored is None
                or stored.get("leader_backend") is not None
                or stored.get("leader_model") is not None
            ):
                raise SessionServiceError("setup command conflicts with ProductSession")
            return
        if result is None:
            raise SessionServiceError("configured ProductSession has no durable setup result")
        _result_from_command(result, self._session.session_id)
        self._apply_configuration_result(result)

    def _reentry_warning(self) -> Diagnostic | None:
        if self._nonterminal_count <= 1:
            return None
        return Diagnostic.create(
            code="multiple_nonterminal_sessions",
            stage="reentry",
            severity=Severity.WARNING,
            actor="agentdeck",
            summary="Multiple nonterminal ProductSessions remain in this project.",
            cause=(
                f"The project has {self._nonterminal_count} nonterminal "
                "ProductSessions."
            ),
            impact="Only the latest stable ProductSession was restored.",
            protection="AgentDeck did not merge or mutate session history.",
            recovery_actions=("Review session history before continuing.",),
            retryable=False,
            outcome_known=True,
            occurred_at=self._now(),
        )

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


def _result_from_command(
    result: CommandResult, expected_session_id: str
) -> SessionResult:
    mode, accepted, goal = result_from_command(result, expected_session_id)
    return SessionResult(mode=mode, accepted=accepted, goal=goal)
