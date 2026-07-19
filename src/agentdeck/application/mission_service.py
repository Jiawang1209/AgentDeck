"""Application authority for validated Preview revision and exact confirmation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json

from agentdeck.application.leader_service import LeaderService
from agentdeck.application.session_validation import canonical_clock_time
from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.mission import (
    ConfirmedMissionVersion,
    MissionDraft,
    MissionPreview,
    PreviewDriftError,
)
from agentdeck.kernel.session import ProductSession, SessionState
from agentdeck.ports.clock import Clock
from agentdeck.ports.leader import LeaderFailure, LeaderProposal, LeaderRequest
from agentdeck.ports.store import CommandResult, Store, StoreTransaction


class MissionServiceError(RuntimeError):
    """Raised when durable Mission authority is unavailable or incoherent."""


@dataclass(frozen=True)
class MissionPreviewView:
    draft: MissionDraft
    preview: MissionPreview

    @property
    def objective(self) -> str:
        return self.draft.objective

    @property
    def preview_id(self) -> str:
        return self.preview.preview_id

    @property
    def version(self) -> int:
        return self.preview.version

    @property
    def content_hash(self) -> str:
        return self.preview.content_hash


@dataclass(frozen=True)
class MissionResult:
    mode: str
    preview: MissionPreviewView | None = None
    mission: ConfirmedMissionVersion | None = None
    diagnostic: Diagnostic | None = None

    def __post_init__(self) -> None:
        shapes = {
            "mission_preview": (self.preview is not None, self.mission is None, self.diagnostic is None),
            "mission_confirmed": (self.preview is None, self.mission is not None, self.diagnostic is None),
            "diagnostic": (self.preview is None, self.mission is None, self.diagnostic is not None),
        }
        if self.mode not in shapes or shapes[self.mode] != (True, True, True):
            raise ValueError("Mission result shape is invalid")


class MissionService:
    """Persist only validated Leader proposals and exact human confirmation."""

    def __init__(
        self,
        *,
        store: Store,
        clock: Clock,
        session_id: str,
        leader_service: LeaderService,
        request_template: LeaderRequest,
    ) -> None:
        for dependency, methods, label in (
            (store, ("execute_once", "load_aggregate", "lookup_command"), "store"),
            (clock, ("now",), "clock"),
            (leader_service, ("propose",), "leader_service"),
        ):
            if any(not callable(getattr(dependency, method, None)) for method in methods):
                raise TypeError(f"{label} does not satisfy MissionService")
        if type(session_id) is not str or not session_id.startswith("ses_"):
            raise ValueError("session_id must be a typed identity")
        if type(request_template) is not LeaderRequest:
            raise TypeError("request_template must be a LeaderRequest")
        self._store = store
        self._clock = clock
        self._session_id = session_id
        self._leader = leader_service
        self._request = request_template
        self._current: MissionPreviewView | None = None
        self._current_state: str | None = None
        self._restore_current()

    def current_preview(self) -> MissionPreviewView | None:
        return self._current

    def propose(self, goal: str) -> MissionResult:
        if self._current is not None:
            return self.revise(goal)
        session = self._session(SessionState.READY)
        request = self._request_for(goal)
        proposal = self._propose(request)
        if type(proposal) is MissionResult:
            return proposal
        preview = proposal.mission.preview(1)
        return self._persist_preview(proposal.mission, preview, session)

    def revise(self, user_delta: str) -> MissionResult:
        current = self._current
        if current is None or self._current_state != "awaiting_confirmation":
            raise MissionServiceError("no mutable Mission Preview is available")
        session = self._session(SessionState.AWAITING_CONFIRMATION)
        delta = _goal(user_delta)
        revision_goal = (
            "Revise this exact current Mission Preview:\n"
            f"{current.preview.canonical_content}\nHuman revision:\n{delta}"
        )
        try:
            request = self._request_for(revision_goal)
        except (TypeError, ValueError):
            return self._leader_diagnostic("oversize")
        proposal = self._propose(request)
        if type(proposal) is MissionResult:
            return proposal
        preview = proposal.mission.preview(current.version + 1)
        return self._persist_preview(proposal.mission, preview, session)

    def confirm(self, preview_id: str, content_hash: str) -> MissionResult:
        current = self._current
        if current is None:
            return self._drift_diagnostic()
        try:
            confirmed = current.preview.confirm(
                preview_id=preview_id, content_hash=content_hash
            )
        except (PreviewDriftError, TypeError, ValueError):
            return self._drift_diagnostic()
        command_id = f"mission:confirm:{current.preview_id}:{current.content_hash[:16]}"
        existing = self._store.lookup_command(command_id, "confirm_mission")
        if existing is not None:
            mission = _confirmed_from_result(existing, confirmed)
            self._current_state = "confirmed"
            return MissionResult("mission_confirmed", mission=mission)
        session = self._session(SessionState.AWAITING_CONFIRMATION)
        running = session.transition(SessionState.RUNNING)
        occurred_at = self._now()

        def persist(transaction: StoreTransaction) -> CommandResult:
            transaction.save_aggregate(
                "confirmed_missions", confirmed.mission_id,
                _mission_snapshot(
                    self._session_id, current.preview, "confirmed", occurred_at
                ),
            )
            transaction.save_session(_session_snapshot(
                running, self._request.permission_ceiling.value
            ))
            transaction.append_event(_event(
                command_id, "mission_confirmed", confirmed.mission_id,
                {"preview_id": current.preview_id, "version": current.version},
                occurred_at,
            ))
            return {
                "content_hash": confirmed.content_hash,
                "mission_id": confirmed.mission_id,
                "preview_id": current.preview_id,
                "version": confirmed.version,
            }

        result = self._store.execute_once(command_id, "confirm_mission", persist)
        mission = _confirmed_from_result(result, confirmed)
        self._current_state = "confirmed"
        return MissionResult("mission_confirmed", mission=mission)

    def _persist_preview(
        self, draft: MissionDraft, preview: MissionPreview, session: ProductSession
    ) -> MissionResult:
        candidate = MissionPreviewView(draft, preview)
        mission_id = f"msn_{preview.content_hash[:24]}"
        input_digest = sha256(preview.canonical_content.encode("utf-8")).hexdigest()[:16]
        command_id = f"mission:preview:{self._session_id}:{preview.version}:{input_digest}"
        occurred_at = self._now()
        drafting = session.transition(SessionState.DRAFTING)
        awaiting = drafting.transition(SessionState.AWAITING_CONFIRMATION)

        def persist(transaction: StoreTransaction) -> CommandResult:
            transaction.save_aggregate(
                "mission_previews", preview.preview_id,
                _mission_snapshot(self._session_id, preview, "awaiting_confirmation", None),
            )
            transaction.save_session(_session_snapshot(
                awaiting, self._request.permission_ceiling.value
            ))
            transaction.append_event(_event(
                command_id, "mission_preview_created", mission_id,
                {"preview_id": preview.preview_id, "version": preview.version},
                occurred_at,
            ))
            return {
                "content_hash": preview.content_hash,
                "mission_id": mission_id,
                "preview_id": preview.preview_id,
                "version": preview.version,
            }

        result = self._store.execute_once(command_id, "create_mission_preview", persist)
        _validate_preview_result(result, preview)
        self._current = candidate
        self._current_state = "awaiting_confirmation"
        return MissionResult("mission_preview", preview=candidate)

    def _propose(self, request: LeaderRequest) -> LeaderProposal | MissionResult:
        try:
            return self._leader.propose(request).proposal
        except LeaderFailure as error:
            code = error.code.value if hasattr(error, "code") else "transport"
            return self._leader_diagnostic(code)
        except Exception:
            return self._leader_diagnostic("transport")

    def _request_for(self, goal: str) -> LeaderRequest:
        return replace(self._request, user_goal=_goal(goal), schema_repair=None)

    def _restore_current(self) -> None:
        stored = self._store.load_aggregate("current_mission_preview", self._session_id)
        if stored is None:
            return
        try:
            preview = MissionPreview(
                stored["preview_id"], stored["version"], stored["content_hash"],
                stored["canonical_content"],
            )
            payload = json.loads(preview.canonical_content)
            payload.pop("version")
            draft = LeaderProposal.from_mapping(payload, request=self._request).mission
            state = stored["state"]
            if state not in {"awaiting_confirmation", "confirmed"}:
                raise ValueError
        except Exception:
            raise MissionServiceError("stored Mission Preview is malformed") from None
        self._current = MissionPreviewView(draft, preview)
        self._current_state = state

    def _session(self, expected: SessionState) -> ProductSession:
        stored = self._store.load_aggregate("product_sessions", self._session_id)
        try:
            if (
                stored is None
                or stored["state"] != expected.value
                or stored.get("permission_profile")
                != self._request.permission_ceiling.value
            ):
                raise ValueError
            pending = stored.get("pending_goal")
            if pending is not None and type(pending) is not str:
                raise ValueError
            return ProductSession(
                self._session_id, self._request.project_context.project_root,
                expected, pending,
            )
        except Exception:
            raise MissionServiceError("ProductSession Mission state is inconsistent") from None

    def _leader_diagnostic(self, code: str) -> MissionResult:
        return MissionResult("diagnostic", diagnostic=Diagnostic.create(
            code=f"leader_{code}", stage="mission_preview", severity=Severity.ERROR,
            actor="leader", summary="The Leader did not produce a valid Mission Preview.",
            cause="The selected Leader failed in a stable diagnostic category.",
            impact="No Mission Preview or execution authority was created.",
            protection="AgentDeck retained the current durable state without fallback.",
            recovery_actions=("Review Leader readiness and retry the goal.",),
            retryable=code in {"timeout", "transport", "nonzero"}, outcome_known=True,
            occurred_at=self._now(),
        ))

    def _drift_diagnostic(self) -> MissionResult:
        return MissionResult("diagnostic", diagnostic=Diagnostic.create(
            code="mission_preview_drift", stage="mission_confirmation",
            severity=Severity.ERROR, actor="agentdeck",
            summary="The confirmation does not match the current Mission Preview.",
            cause="The Preview identity or canonical content hash changed.",
            impact="The Mission was not confirmed or started.",
            protection="Only the exact current Preview can create execution authority.",
            recovery_actions=("Review and confirm the current Preview ID and hash.",),
            retryable=True, outcome_known=True, occurred_at=self._now(),
        ))

    def _now(self) -> str:
        return canonical_clock_time(self._clock.now())


def _goal(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("Mission goal must be nonempty text")
    value.encode("utf-8", "strict")
    return value


def _mission_snapshot(
    session_id: str, preview: MissionPreview, state: str, confirmed_at: str | None
) -> dict[str, object]:
    return {
        "mission_id": f"msn_{preview.content_hash[:24]}", "session_id": session_id,
        "state": state, "current_version": preview.version,
        "preview_id": preview.preview_id, "version": preview.version,
        "content_hash": preview.content_hash,
        "canonical_content": preview.canonical_content, "confirmed_at": confirmed_at,
    }


def _session_snapshot(
    session: ProductSession, permission: str
) -> dict[str, object]:
    return {
        "session_id": session.session_id, "state": session.state.value,
        "permission_profile": permission, "pending_goal": session.pending_goal,
    }


def _event(
    command_id: str, kind: str, mission_id: str,
    payload: dict[str, object], occurred_at: str,
) -> DomainEvent:
    digest = sha256(f"{command_id}:{kind}".encode("utf-8")).hexdigest()[:32]
    return replace(DomainEvent.create(
        kind=kind, aggregate_type="mission", aggregate_id=mission_id,
        payload=payload, occurred_at=occurred_at,
    ), event_id=f"evt_{digest}")


def _validate_preview_result(result: CommandResult, preview: MissionPreview) -> None:
    expected = {
        "content_hash": preview.content_hash,
        "mission_id": f"msn_{preview.content_hash[:24]}",
        "preview_id": preview.preview_id, "version": preview.version,
    }
    if result != expected:
        raise MissionServiceError("stored Mission Preview result is malformed")


def _confirmed_from_result(
    result: CommandResult, expected: ConfirmedMissionVersion
) -> ConfirmedMissionVersion:
    if result != {
        "content_hash": expected.content_hash, "mission_id": expected.mission_id,
        "preview_id": f"prv_{expected.content_hash[:24]}", "version": expected.version,
    }:
        raise MissionServiceError("stored Mission confirmation result is malformed")
    return expected


__all__ = [
    "MissionPreviewView", "MissionResult", "MissionService", "MissionServiceError",
]
