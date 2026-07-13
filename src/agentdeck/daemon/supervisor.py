from __future__ import annotations

import copy
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..conversation.transports import WorkerRoute
from ..runtime.acp_mapping import map_stop_reason
from ..state import validate_mission_attempt_record
from ..workflow import CanonicalArtifact, CanonicalHandoff, build_canonical_handoff
from .scheduler import is_successful_attempt_result


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
ClaimAdmission = Callable[
    [MissionAttempt], Mapping[str, object] | Awaitable[Mapping[str, object]]
]
ReleaseAdmission = Callable[
    [MissionAttempt], Mapping[str, object] | Awaitable[Mapping[str, object]]
]
MarkAttemptAmbiguous = Callable[
    [MissionAttempt, SubmittedReceipt, str], None | Awaitable[None]
]
AdmitTransport = Callable[
    [MissionAttempt], SubmittedReceipt | Awaitable[SubmittedReceipt]
]
CompleteTransport = Callable[
    [MissionAttempt, SubmittedReceipt], TransportResult | Awaitable[TransportResult]
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
_REPLY_ALLOWLIST = (
    "handoff_token",
    "status",
    "summary",
    "verification",
    "risks",
    "next_steps",
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


def _project_reply_allowlist(reply: object) -> dict[str, str] | None:
    if type(reply) is not dict:
        return None
    projected: dict[str, str] = {}
    for field in _REPLY_ALLOWLIST:
        value = reply.get(field)
        if type(value) is not str or not value:
            return None
        projected[field] = value
    return projected


def _project_result(
    result: object,
) -> tuple[TransportResult, dict[str, str], list[dict[str, str]], list[str]] | None:
    if (
        not isinstance(result, TransportResult)
        or type(result.stop_reason) is not str
        or not result.stop_reason
        or type(result.validated) is not bool
        or type(result.artifacts) is not tuple
        or type(result.trace_ids) is not tuple
        or any(type(item) is not str or not item for item in result.trace_ids)
        or len(result.trace_ids) != len(set(result.trace_ids))
    ):
        return None
    reply = _project_reply_allowlist(result.reply)
    if reply is None:
        return None
    artifacts: list[dict[str, str]] = []
    for item in result.artifacts:
        if (
            not isinstance(item, ArtifactEvidence)
            or type(item.path) is not str
            or not item.path
            or type(item.content_hash) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item.content_hash) is None
        ):
            return None
        artifacts.append({"path": item.path, "content_hash": item.content_hash})
    return result, reply, artifacts, list(result.trace_ids)


class WorkerAttemptSupervisor:
    def __init__(
        self,
        *,
        authorize_attempt: AuthorizeAttempt,
        claim_admission: ClaimAdmission,
        release_admission: ReleaseAdmission,
        persist_submitted: PersistSubmitted,
        mark_attempt_ambiguous: MarkAttemptAmbiguous,
        acp_admit: AdmitTransport,
        tmux_admit: AdmitTransport,
        acp_complete: CompleteTransport,
        tmux_complete: CompleteTransport,
    ) -> None:
        for callback in (
            authorize_attempt,
            claim_admission,
            release_admission,
            persist_submitted,
            mark_attempt_ambiguous,
            acp_admit,
            tmux_admit,
            acp_complete,
            tmux_complete,
        ):
            if not callable(callback):
                raise TypeError("Worker supervisor callback must be callable")
        self._authorize_attempt = authorize_attempt
        self._claim_admission = claim_admission
        self._release_admission = release_admission
        self._persist_submitted = persist_submitted
        self._mark_attempt_ambiguous = mark_attempt_ambiguous
        self._acp_admit = acp_admit
        self._tmux_admit = tmux_admit
        self._acp_complete = acp_complete
        self._tmux_complete = tmux_complete
        self._halted = False

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

    async def _durably_mark_unknown(
        self, baseline: MissionAttempt, receipt: SubmittedReceipt
    ) -> bool:
        marked, _ = await _capture_callback(
            self._mark_attempt_ambiguous,
            copy.deepcopy(baseline),
            copy.deepcopy(receipt),
            "receipt_persistence_unknown",
        )
        if not marked:
            return False
        authority_ok, ambiguous = await self._current_attempt(baseline)
        return bool(
            authority_ok
            and _same_attempt_authority(baseline, ambiguous)
            and ambiguous["state"] == "ambiguous"
            and ambiguous["receipt_summary"] == receipt.summary
            and ambiguous["blocker"] == "receipt_persistence_unknown"
            and ambiguous["terminal_reason"] == "receipt_persistence_unknown"
        )

    async def _durably_release_admission(
        self, baseline: MissionAttempt, claimed: MissionAttempt
    ) -> bool:
        released, _ = await _capture_callback(
            self._release_admission, copy.deepcopy(claimed)
        )
        if not released:
            return False
        authority_ok, prepared = await self._current_attempt(baseline)
        return bool(authority_ok and prepared == baseline)

    def _halt(self) -> None:
        self._halted = True

    async def execute(
        self, attempt_record: Mapping[str, object], route: WorkerRoute
    ) -> AttemptOutcome:
        if self._halted:
            raise WorkerAttemptError("Worker supervisor is halted")
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
        if current["state"] in {"admitting", "submitted", "running"}:
            raise WorkerAttemptError("Worker attempt is already submitted")
        if current["state"] != "prepared" or current != baseline:
            raise WorkerAttemptError("Worker attempt authority drift")

        claimed_ok, raw_claimed = await _capture_callback(
            self._claim_admission, copy.deepcopy(baseline)
        )
        claimed = _validated_attempt(raw_claimed) if claimed_ok else {}
        if not (
            claimed
            and _same_attempt_authority(baseline, claimed)
            and claimed["state"] == "admitting"
            and claimed["receipt_summary"] is None
            and claimed["blocker"] is None
            and claimed["terminal_reason"] is None
        ):
            raise WorkerAttemptError("Worker admission claim failed")
        claim_authority_ok, durable_claimed = await self._current_attempt(baseline)
        if not claim_authority_ok or durable_claimed != claimed:
            raise WorkerAttemptError("Worker admission claim failed")
        claimed = durable_claimed

        admit = self._acp_admit if transport == "acp" else self._tmux_admit
        admitted, receipt = await _capture_callback(admit, copy.deepcopy(baseline))
        if not admitted:
            if not await self._durably_release_admission(baseline, claimed):
                self._halt()
                raise WorkerAttemptError("Worker admission claim release failed")
            raise WorkerAttemptError(f"{label} Worker failed before admission")
        if not isinstance(receipt, SubmittedReceipt):
            self._halt()
            raise WorkerAttemptError(f"{label} Worker returned an invalid admission")
        if receipt.dispatch_key != dispatch_key:
            marked = await self._durably_mark_unknown(baseline, receipt)
            if not marked:
                self._halt()
                raise WorkerAttemptError("Worker ambiguity persistence failed")
            raise WorkerAttemptError("Worker admission receipt lineage drift")

        persisted, _ = await _capture_callback(
            self._persist_submitted,
            copy.deepcopy(claimed),
            copy.deepcopy(receipt),
        )
        submitted: MissionAttempt = {}
        if persisted:
            authority_ok, submitted = await self._current_attempt(baseline)
            persisted = bool(
                authority_ok
                and _same_attempt_authority(baseline, submitted)
                and submitted["state"] == "submitted"
                and submitted["receipt_summary"] == receipt.summary
            )
        if not persisted:
            marked = await self._durably_mark_unknown(baseline, receipt)
            if marked:
                raise WorkerAttemptError("Worker submitted receipt persistence failed")
            self._halt()
            raise WorkerAttemptError("Worker ambiguity persistence failed")

        complete = self._acp_complete if transport == "acp" else self._tmux_complete
        completed, raw_result = await _capture_callback(
            complete,
            copy.deepcopy(submitted),
            copy.deepcopy(receipt),
        )
        if not completed:
            raise WorkerAttemptError(f"{label} Worker failed")
        projected = _project_result(raw_result)
        if projected is None:
            raise WorkerAttemptError(f"{label} Worker result is invalid")
        result, reply, artifacts, trace_ids = projected
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
                reply=reply,
                artifacts=artifacts,
                trace_ids=trace_ids,
                expected_handoff_token=dispatch_key,
                require_artifact_hashes=True,
            )
        except (TypeError, ValueError):
            handoff = None
        if handoff is None:
            raise WorkerAttemptError(f"{label} Worker result is invalid")
        return handoff


def supervisor_gate(ledger: Mapping[str, object]) -> SupervisorGateDecision:
    """Allow the next Worker only after AgentDeck-owned validation and handoff."""
    expected_fields = {
        "attempt_state",
        "result_status",
        "reply_state",
        "handoff_state",
        "next_worker",
    }
    if type(ledger) is not dict or set(ledger) != expected_fields:
        return SupervisorGateDecision(None, "Worker ledger facts are invalid")
    attempt_state = ledger["attempt_state"]
    result_status = ledger["result_status"]
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
    if not is_successful_attempt_result(attempt_state, result_status):
        return SupervisorGateDecision(None, "Worker attempt result is not successful")
    if type(next_worker) is not str or not next_worker:
        return SupervisorGateDecision(None, "Next Worker identity is invalid")
    return SupervisorGateDecision(next_worker, None)
