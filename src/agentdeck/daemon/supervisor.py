from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..conversation.transports import WorkerRoute
from ..runtime.acp_mapping import map_stop_reason
from ..state import validate_mission_attempt_record
from ..workflow import validate_compact_worker_outcome


class WorkerAttemptError(RuntimeError):
    """A Worker attempt cannot be safely admitted or normalized."""


@dataclass(frozen=True)
class ArtifactEvidence:
    path: str
    content_hash: str

    def __post_init__(self) -> None:
        if (
            type(self.path) is not str
            or not self.path
            or type(self.content_hash) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.content_hash) is None
        ):
            raise ValueError("invalid Worker artifact evidence")


@dataclass(frozen=True)
class SubmittedReceipt:
    receipt_id: str
    dispatch_key: str
    summary: str

    def __post_init__(self) -> None:
        if (
            type(self.receipt_id) is not str
            or not self.receipt_id
            or type(self.dispatch_key) is not str
            or re.fullmatch(r"dsp_[0-9a-f]{32}", self.dispatch_key) is None
            or type(self.summary) is not str
            or not self.summary
        ):
            raise ValueError("invalid Worker admission receipt")


@dataclass(frozen=True)
class TransportResult:
    stop_reason: str
    validated: bool
    reply: dict[str, Any]
    artifacts: tuple[ArtifactEvidence, ...] = ()
    trace_ids: tuple[str, ...] = ()


CompletionFactory = Callable[[], Awaitable[TransportResult]]


@dataclass(frozen=True)
class TransportExecution:
    admission: SubmittedReceipt
    completion_factory: CompletionFactory

    def __post_init__(self) -> None:
        if not isinstance(self.admission, SubmittedReceipt):
            raise TypeError("transport admission is invalid")
        if inspect.isawaitable(self.completion_factory) or not callable(
            self.completion_factory
        ):
            raise TypeError("transport completion must be a cold completion factory")


@dataclass(frozen=True)
class AttemptOutcome:
    status: str
    summary: str
    verification: str
    risks: str
    next_steps: str
    artifacts: tuple[ArtifactEvidence, ...]
    trace_ids: tuple[str, ...]

    def compact(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "verification": self.verification,
            "risks": self.risks,
            "next_steps": self.next_steps,
            "artifacts": [
                {"path": item.path, "content_hash": item.content_hash}
                for item in self.artifacts
            ],
            "trace_ids": list(self.trace_ids),
        }


@dataclass(frozen=True)
class SupervisorGateDecision:
    next_worker: str | None
    blocker: str | None


MissionAttempt = dict[str, Any]
AuthorizeAttempt = Callable[
    [MissionAttempt], Mapping[str, object] | Awaitable[Mapping[str, object]]
]
PersistSubmitted = Callable[
    [MissionAttempt, SubmittedReceipt], None | Awaitable[None]
]
ExecuteTransport = Callable[
    [MissionAttempt], TransportExecution | Awaitable[TransportExecution]
]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


_ATTEMPT_AUTHORITY_FIELDS = (
    "attempt_id",
    "mission_id",
    "step_id",
    "agent_id",
    "configured_transport",
    "dispatch_key",
    "snapshot_hash",
    "created_at",
)


def _same_attempt_authority(left: MissionAttempt, right: MissionAttempt) -> bool:
    return all(left[field] == right[field] for field in _ATTEMPT_AUTHORITY_FIELDS)


def _validated_attempt(value: object) -> MissionAttempt:
    try:
        return validate_mission_attempt_record(value)
    except (TypeError, ValueError):
        raise WorkerAttemptError("Worker attempt record is invalid") from None


def _validate_route(attempt: MissionAttempt, route: WorkerRoute) -> None:
    if not isinstance(route, WorkerRoute):
        raise TypeError("Worker route must be a WorkerRoute")
    if (
        type(route.agent_id) is not str
        or route.agent_id != attempt["agent_id"]
        or type(route.configured_transport) is not str
        or route.configured_transport != attempt["configured_transport"]
        or type(route.effective_transport) is not str
        or route.effective_transport != attempt["configured_transport"]
    ):
        raise WorkerAttemptError("Worker transport drift")
    if route.ownership != "agentdeck_owned":
        raise WorkerAttemptError("Worker ownership is not AgentDeck")
    if type(route.ready) is not bool or type(route.automation_allowed) is not bool:
        raise WorkerAttemptError("Worker route facts are invalid")
    if not route.ready:
        raise WorkerAttemptError("Worker transport is not ready")
    if not route.automation_allowed:
        raise WorkerAttemptError("Worker automation is blocked")


def _validate_result_shape(result: object) -> TransportResult:
    if (
        not isinstance(result, TransportResult)
        or type(result.stop_reason) is not str
        or not result.stop_reason
        or type(result.validated) is not bool
        or type(result.reply) is not dict
        or type(result.artifacts) is not tuple
        or type(result.trace_ids) is not tuple
        or any(not isinstance(item, ArtifactEvidence) for item in result.artifacts)
        or any(type(item) is not str or not item for item in result.trace_ids)
        or len(result.trace_ids) != len(set(result.trace_ids))
    ):
        raise WorkerAttemptError("Worker result is invalid")
    for item in result.artifacts:
        try:
            ArtifactEvidence(item.path, item.content_hash)
        except (TypeError, ValueError):
            raise WorkerAttemptError("Worker result is invalid") from None
    return result


class WorkerAttemptSupervisor:
    def __init__(
        self,
        *,
        authorize_attempt: AuthorizeAttempt,
        persist_submitted: PersistSubmitted,
        acp_execute: ExecuteTransport,
        tmux_execute: ExecuteTransport,
    ) -> None:
        for callback in (
            authorize_attempt,
            persist_submitted,
            acp_execute,
            tmux_execute,
        ):
            if not callable(callback):
                raise TypeError("Worker supervisor callback must be callable")
        self._authorize_attempt = authorize_attempt
        self._persist_submitted = persist_submitted
        self._acp_execute = acp_execute
        self._tmux_execute = tmux_execute
        self._claimed_dispatch_keys: set[str] = set()

    async def _current_attempt(self, candidate: MissionAttempt) -> MissionAttempt:
        try:
            current = await _maybe_await(self._authorize_attempt(candidate))
        except Exception:
            raise WorkerAttemptError("Worker attempt authority check failed") from None
        return _validated_attempt(current)

    async def execute(
        self, attempt_record: Mapping[str, object], route: WorkerRoute
    ) -> AttemptOutcome:
        attempt = _validated_attempt(attempt_record)
        if attempt["state"] != "prepared":
            raise WorkerAttemptError("Worker attempt must be prepared")
        _validate_route(attempt, route)
        current = await self._current_attempt(attempt)
        if not _same_attempt_authority(attempt, current):
            raise WorkerAttemptError("Worker attempt authority drift")
        if current["state"] in {"submitted", "running"}:
            raise WorkerAttemptError("Worker attempt is already submitted")
        if current["state"] != "prepared" or current != attempt:
            raise WorkerAttemptError("Worker attempt authority drift")
        dispatch_key = attempt["dispatch_key"]
        if dispatch_key in self._claimed_dispatch_keys:
            raise WorkerAttemptError("Worker attempt is already submitted")
        self._claimed_dispatch_keys.add(dispatch_key)

        executor = (
            self._acp_execute
            if attempt["configured_transport"] == "acp"
            else self._tmux_execute
        )
        label = "ACP" if attempt["configured_transport"] == "acp" else "tmux"
        try:
            execution = await _maybe_await(executor(attempt))
        except Exception:
            raise WorkerAttemptError(f"{label} Worker failed before admission") from None
        if not isinstance(execution, TransportExecution):
            raise WorkerAttemptError(f"{label} Worker returned an invalid admission")
        if execution.admission.dispatch_key != dispatch_key:
            raise WorkerAttemptError("Worker admission receipt lineage drift")
        try:
            await _maybe_await(self._persist_submitted(attempt, execution.admission))
            submitted = await self._current_attempt(attempt)
        except WorkerAttemptError:
            raise WorkerAttemptError(
                "Worker submitted receipt persistence failed"
            ) from None
        except Exception:
            raise WorkerAttemptError(
                "Worker submitted receipt persistence failed"
            ) from None
        if (
            not _same_attempt_authority(attempt, submitted)
            or submitted["state"] != "submitted"
            or submitted["receipt_summary"] != execution.admission.summary
        ):
            raise WorkerAttemptError(
                "Worker submitted receipt persistence failed"
            ) from None

        try:
            completion = execution.completion_factory()
            if not inspect.isawaitable(completion):
                raise TypeError("completion is not awaitable")
            result = await completion
        except Exception:
            raise WorkerAttemptError(f"{label} Worker failed") from None
        try:
            result = _validate_result_shape(result)
        except WorkerAttemptError:
            raise WorkerAttemptError(f"{label} Worker result is invalid") from None
        if attempt["configured_transport"] == "acp":
            try:
                turn_state, _ = map_stop_reason(result.stop_reason)
            except ValueError:
                raise WorkerAttemptError("ACP Worker stop reason is invalid") from None
            if turn_state != "completed":
                raise WorkerAttemptError("ACP Worker did not complete")
        elif result.stop_reason != "structured_reply" or not result.validated:
            raise WorkerAttemptError("tmux Worker reply is not validated")
        if not result.validated:
            raise WorkerAttemptError(f"{label} Worker result is not validated")
        try:
            compact = validate_compact_worker_outcome(
                reply=result.reply,
                artifacts=[
                    {"path": item.path, "content_hash": item.content_hash}
                    for item in result.artifacts
                ],
                trace_ids=list(result.trace_ids),
                expected_handoff_token=dispatch_key,
            )
        except (TypeError, ValueError):
            raise WorkerAttemptError(f"{label} Worker result is invalid") from None
        detached_artifacts = tuple(
            ArtifactEvidence(item["path"], item["content_hash"])
            for item in compact["artifacts"]
        )
        return AttemptOutcome(
            status=str(compact["status"]),
            summary=str(compact["summary"]),
            verification=str(compact["verification"]),
            risks=str(compact["risks"]),
            next_steps=str(compact["next_steps"]),
            artifacts=detached_artifacts,
            trace_ids=tuple(str(item) for item in compact["trace_ids"]),
        )


def supervisor_gate(ledger: Mapping[str, object]) -> SupervisorGateDecision:
    """Allow the next Worker only after AgentDeck-owned validation and handoff."""
    if not isinstance(ledger, Mapping):
        raise TypeError("supervisor ledger facts must be a mapping")
    reply_state = ledger.get("reply_state")
    handoff_state = ledger.get("handoff_state")
    next_worker = ledger.get("next_worker")
    if reply_state not in {"none", "received", "validated"}:
        return SupervisorGateDecision(None, "Worker reply state is invalid")
    if handoff_state not in {"none", "recorded"}:
        return SupervisorGateDecision(None, "Worker handoff state is invalid")
    if handoff_state == "recorded" and reply_state != "validated":
        return SupervisorGateDecision(None, "Worker handoff lacks validated reply")
    if reply_state != "validated" or handoff_state != "recorded":
        return SupervisorGateDecision(None, "Worker completion handoff is not ready")
    if type(next_worker) is not str or not next_worker:
        return SupervisorGateDecision(None, "Next Worker identity is invalid")
    return SupervisorGateDecision(next_worker, None)
