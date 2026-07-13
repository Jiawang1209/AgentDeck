from __future__ import annotations

import asyncio
import copy
import inspect
import re
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import Any

from ..conversation.transports import WorkerRoute
from ..runtime.acp_mapping import map_stop_reason
from ..state import validate_mission_attempt_record
from ..workflow import (
    CanonicalArtifact,
    CanonicalHandoff,
    build_canonical_handoff,
)


class WorkerAttemptError(RuntimeError):
    """A Worker attempt cannot be safely admitted or normalized."""


ArtifactEvidence = CanonicalArtifact
AttemptOutcome = CanonicalHandoff


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


CompletionFactory = Callable[
    [], Coroutine[Any, Any, TransportResult]
]


def _closure_futures(callback: object) -> tuple[asyncio.Future[Any], ...]:
    captured: list[object] = [callback]
    closure = getattr(callback, "__closure__", None)
    if type(closure) is tuple:
        for cell in closure:
            try:
                captured.append(cell.cell_contents)
            except ValueError:
                continue
    defaults = getattr(callback, "__defaults__", None)
    if type(defaults) is tuple:
        captured.extend(defaults)
    kwdefaults = getattr(callback, "__kwdefaults__", None)
    if type(kwdefaults) is dict:
        captured.extend(kwdefaults.values())
    found: list[asyncio.Future[Any]] = []
    for value in captured:
        if isinstance(value, asyncio.Future):
            found.append(value)
    return tuple(dict.fromkeys(found))


@dataclass(frozen=True)
class TransportExecution:
    admission: SubmittedReceipt
    completion_factory: CompletionFactory

    def __post_init__(self) -> None:
        if not isinstance(self.admission, SubmittedReceipt):
            raise TypeError("transport admission is invalid")
        hidden_futures = _closure_futures(self.completion_factory)
        valid_factory = (
            inspect.iscoroutinefunction(self.completion_factory)
            and not inspect.isawaitable(self.completion_factory)
            and not hidden_futures
        )
        if not valid_factory:
            for future in hidden_futures:
                future.cancel()
            raise TypeError(
                "transport completion must be a cold completion factory returning a native coroutine"
            )


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


async def _capture_callback(
    callback: Callable[..., Any], *args: object
) -> tuple[bool, Any]:
    try:
        value = callback(*args)
        if inspect.isawaitable(value):
            value = await value
    except Exception:
        return False, None
    return True, value


async def _capture_coroutine(coroutine: Coroutine[Any, Any, Any]) -> tuple[bool, Any]:
    try:
        value = await coroutine
    except Exception:
        return False, None
    return True, value


def _capture_factory(callback: CompletionFactory) -> tuple[bool, object]:
    try:
        value = callback()
    except Exception:
        return False, None
    return True, value


async def _dispose_invalid_completion(value: object) -> None:
    if inspect.iscoroutine(value):
        value.close()
        return
    if isinstance(value, asyncio.Future):
        value.cancel()
        await asyncio.gather(value, return_exceptions=True)


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
        return {}


def _validate_route(attempt: MissionAttempt, route: WorkerRoute) -> str | None:
    if not isinstance(route, WorkerRoute):
        return "Worker route must be a WorkerRoute"
    if (
        type(route.agent_id) is not str
        or route.agent_id != attempt["agent_id"]
        or type(route.configured_transport) is not str
        or route.configured_transport != attempt["configured_transport"]
        or type(route.effective_transport) is not str
        or route.effective_transport != attempt["configured_transport"]
    ):
        return "Worker transport drift"
    if route.ownership != "agentdeck_owned":
        return "Worker ownership is not AgentDeck"
    if type(route.ready) is not bool or type(route.automation_allowed) is not bool:
        return "Worker route facts are invalid"
    if not route.ready:
        return "Worker transport is not ready"
    if not route.automation_allowed:
        return "Worker automation is blocked"
    return None


def _result_shape_is_valid(result: object) -> bool:
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
        return False
    for item in result.artifacts:
        try:
            ArtifactEvidence(item.path, item.content_hash)
        except (TypeError, ValueError):
            return False
    return True


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
        self._recovery_dispatch_keys: set[str] = set()

    async def _current_attempt(
        self, baseline: MissionAttempt
    ) -> tuple[bool, MissionAttempt]:
        ok, raw = await _capture_callback(
            self._authorize_attempt, copy.deepcopy(baseline)
        )
        if not ok:
            return False, {}
        current = _validated_attempt(raw)
        return bool(current), current

    async def execute(
        self, attempt_record: Mapping[str, object], route: WorkerRoute
    ) -> AttemptOutcome:
        baseline = _validated_attempt(attempt_record)
        if not baseline:
            raise WorkerAttemptError("Worker attempt record is invalid")
        if baseline["state"] != "prepared":
            raise WorkerAttemptError("Worker attempt must be prepared")
        route_error = _validate_route(baseline, route)
        if route_error is not None:
            raise WorkerAttemptError(route_error)
        transport = baseline["configured_transport"]
        dispatch_key = baseline["dispatch_key"]
        label = "ACP" if transport == "acp" else "tmux"

        authority_ok, current = await self._current_attempt(baseline)
        if not authority_ok:
            raise WorkerAttemptError("Worker attempt authority check failed")
        if not _same_attempt_authority(baseline, current):
            raise WorkerAttemptError("Worker attempt authority drift")
        if dispatch_key in self._recovery_dispatch_keys:
            if current["state"] == "prepared":
                raise WorkerAttemptError("Worker attempt requires recovery")
            self._recovery_dispatch_keys.discard(dispatch_key)
        if current["state"] in {"submitted", "running"}:
            raise WorkerAttemptError("Worker attempt is already submitted")
        if current["state"] != "prepared" or current != baseline:
            raise WorkerAttemptError("Worker attempt authority drift")
        if dispatch_key in self._claimed_dispatch_keys:
            raise WorkerAttemptError("Worker attempt is already submitted")
        self._claimed_dispatch_keys.add(dispatch_key)

        executor = self._acp_execute if transport == "acp" else self._tmux_execute
        admitted, execution = await _capture_callback(
            executor, copy.deepcopy(baseline)
        )
        if not admitted:
            self._claimed_dispatch_keys.discard(dispatch_key)
            raise WorkerAttemptError(f"{label} Worker failed before admission")
        if not isinstance(execution, TransportExecution):
            self._claimed_dispatch_keys.discard(dispatch_key)
            raise WorkerAttemptError(f"{label} Worker returned an invalid admission")
        if execution.admission.dispatch_key != dispatch_key:
            self._claimed_dispatch_keys.discard(dispatch_key)
            self._recovery_dispatch_keys.add(dispatch_key)
            raise WorkerAttemptError("Worker admission receipt lineage drift")

        persisted, _ = await _capture_callback(
            self._persist_submitted,
            copy.deepcopy(baseline),
            copy.deepcopy(execution.admission),
        )
        if not persisted:
            self._claimed_dispatch_keys.discard(dispatch_key)
            self._recovery_dispatch_keys.add(dispatch_key)
            raise WorkerAttemptError("Worker submitted receipt persistence failed")
        authority_ok, submitted = await self._current_attempt(baseline)
        if not authority_ok:
            self._claimed_dispatch_keys.discard(dispatch_key)
            self._recovery_dispatch_keys.add(dispatch_key)
            raise WorkerAttemptError("Worker submitted receipt persistence failed")
        if (
            not _same_attempt_authority(baseline, submitted)
            or submitted["state"] != "submitted"
            or submitted["receipt_summary"] != execution.admission.summary
        ):
            self._claimed_dispatch_keys.discard(dispatch_key)
            self._recovery_dispatch_keys.add(dispatch_key)
            raise WorkerAttemptError("Worker submitted receipt persistence failed")
        self._claimed_dispatch_keys.discard(dispatch_key)
        self._recovery_dispatch_keys.discard(dispatch_key)

        factory_ok, completion = _capture_factory(execution.completion_factory)
        if not factory_ok:
            raise WorkerAttemptError(f"{label} Worker failed")
        if not inspect.iscoroutine(completion):
            await _dispose_invalid_completion(completion)
            raise WorkerAttemptError(f"{label} Worker failed")
        completed, result = await _capture_coroutine(completion)
        if not completed:
            raise WorkerAttemptError(f"{label} Worker failed")
        if not _result_shape_is_valid(result):
            raise WorkerAttemptError(f"{label} Worker result is invalid")
        if transport == "acp":
            try:
                turn_state, _ = map_stop_reason(result.stop_reason)
            except ValueError:
                turn_state = "invalid"
            if turn_state == "invalid":
                raise WorkerAttemptError("ACP Worker stop reason is invalid")
            if turn_state != "completed":
                raise WorkerAttemptError("ACP Worker did not complete")
        elif result.stop_reason != "structured_reply" or not result.validated:
            raise WorkerAttemptError("tmux Worker reply is not validated")
        if not result.validated:
            raise WorkerAttemptError(f"{label} Worker result is not validated")
        try:
            handoff = build_canonical_handoff(
                reply=copy.deepcopy(result.reply),
                artifacts=[
                    {"path": item.path, "content_hash": item.content_hash}
                    for item in result.artifacts
                ],
                trace_ids=list(result.trace_ids),
                expected_handoff_token=dispatch_key,
            )
        except (TypeError, ValueError):
            handoff = None
        if handoff is None:
            raise WorkerAttemptError(f"{label} Worker result is invalid")
        return handoff


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
