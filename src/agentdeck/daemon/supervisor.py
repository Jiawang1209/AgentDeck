from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..conversation.transports import WorkerRoute
from ..runtime.acp_mapping import map_stop_reason
from ..workflow import validate_compact_worker_outcome


class WorkerAttemptError(RuntimeError):
    """A Worker attempt cannot be safely admitted or normalized."""


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    agent_id: str
    configured_transport: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"mat_[0-9a-f]{12}", self.attempt_id):
            raise ValueError("invalid Worker attempt identity")
        if not self.agent_id:
            raise ValueError("invalid Worker identity")
        if self.configured_transport not in {"acp", "tmux"}:
            raise ValueError("invalid Worker transport")


@dataclass(frozen=True)
class ArtifactEvidence:
    path: str
    content_hash: str


@dataclass(frozen=True)
class SubmittedReceipt:
    receipt_id: str
    summary: str

    def __post_init__(self) -> None:
        if not self.receipt_id or not self.summary:
            raise ValueError("invalid Worker admission receipt")


@dataclass(frozen=True)
class TransportResult:
    stop_reason: str
    validated: bool
    reply: dict[str, Any]
    artifacts: tuple[ArtifactEvidence, ...] = ()
    trace_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransportExecution:
    admission: SubmittedReceipt
    completion: Awaitable[TransportResult]


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


PersistSubmitted = Callable[
    [AttemptRecord, SubmittedReceipt], None | Awaitable[None]
]
ExecuteTransport = Callable[
    [AttemptRecord], TransportExecution | Awaitable[TransportExecution]
]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _close_unawaited(value: object) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


class WorkerAttemptSupervisor:
    def __init__(
        self,
        *,
        persist_submitted: PersistSubmitted,
        acp_execute: ExecuteTransport,
        tmux_execute: ExecuteTransport,
    ) -> None:
        self._persist_submitted = persist_submitted
        self._acp_execute = acp_execute
        self._tmux_execute = tmux_execute

    async def execute(
        self, attempt: AttemptRecord, route: WorkerRoute
    ) -> AttemptOutcome:
        if not isinstance(attempt, AttemptRecord) or not isinstance(route, WorkerRoute):
            raise TypeError("Worker attempt supervision requires exact domain records")
        if (
            route.agent_id != attempt.agent_id
            or route.configured_transport != attempt.configured_transport
            or route.effective_transport != attempt.configured_transport
        ):
            raise WorkerAttemptError("Worker transport drift")
        if not route.ready:
            raise WorkerAttemptError(route.blocker or "Worker transport is not ready")
        if not route.automation_allowed:
            raise WorkerAttemptError(route.prompt_blocker or "Worker automation is blocked")

        executor = (
            self._acp_execute
            if attempt.configured_transport == "acp"
            else self._tmux_execute
        )
        label = "ACP" if attempt.configured_transport == "acp" else "tmux"
        try:
            execution = await _maybe_await(executor(attempt))
        except Exception as error:
            raise WorkerAttemptError(f"{label} Worker failed before admission") from error
        if not isinstance(execution, TransportExecution):
            raise WorkerAttemptError(f"{label} Worker returned an invalid admission")
        try:
            await _maybe_await(
                self._persist_submitted(attempt, execution.admission)
            )
        except Exception:
            _close_unawaited(execution.completion)
            raise
        try:
            result = await execution.completion
        except Exception as error:
            raise WorkerAttemptError(f"{label} Worker failed") from error
        if not isinstance(result, TransportResult):
            raise WorkerAttemptError(f"{label} Worker returned an invalid result")
        if attempt.configured_transport == "acp":
            try:
                turn_state, _ = map_stop_reason(result.stop_reason)
            except ValueError as error:
                raise WorkerAttemptError("ACP Worker stop reason is invalid") from error
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
            )
        except (TypeError, ValueError) as error:
            raise WorkerAttemptError(f"{label} Worker result is invalid") from error
        return AttemptOutcome(
            status=compact["status"],
            summary=compact["summary"],
            verification=compact["verification"],
            risks=compact["risks"],
            next_steps=compact["next_steps"],
            artifacts=result.artifacts,
            trace_ids=result.trace_ids,
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
