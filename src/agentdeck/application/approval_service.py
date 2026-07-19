"""Sequential, durable ACP permission handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.permissions import Effect, PermissionScope
from agentdeck.ports.approval import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalReviewer,
    ReviewerVerdict,
)
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import Store
from agentdeck.ports.worker import Worker, WorkerEvent, WorkerHandle, WorkerResult


_MAX_PERMISSION_REQUESTS = 64


@dataclass(frozen=True)
class ApprovalContext:
    mission_id: str
    mission_version: int
    permission_scope: PermissionScope
    scope_hash: str

    def __post_init__(self) -> None:
        if type(self.mission_id) is not str or not self.mission_id.startswith("msn_"):
            raise ValueError("mission_id must be a typed identity")
        if type(self.mission_version) is not int or self.mission_version < 1:
            raise ValueError("mission_version must be positive")
        if type(self.permission_scope) is not PermissionScope:
            raise TypeError("permission_scope must be a PermissionScope")
        if (
            type(self.scope_hash) is not str
            or len(self.scope_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.scope_hash)
        ):
            raise ValueError("scope_hash must be 64 lowercase hex")


@dataclass(frozen=True)
class PermissionBridgeResult:
    approvals: tuple[ApprovalRecord, ...]
    worker_result: WorkerResult
    terminal_result_validated: bool
    handoff_committed: bool = False
    next_task_allowed: bool = False
    diagnostic: Diagnostic | None = None

    def __post_init__(self) -> None:
        if type(self.approvals) is not tuple or any(
            type(item) is not ApprovalRecord for item in self.approvals
        ):
            raise TypeError("approvals must contain ApprovalRecord values")
        if type(self.worker_result) is not WorkerResult:
            raise TypeError("worker_result must be a WorkerResult")
        if self.terminal_result_validated is not True:
            raise ValueError("bridge result requires a validated terminal result")
        if self.handoff_committed or self.next_task_allowed:
            raise ValueError("ApprovalService cannot authorize the next Task")
        if self.diagnostic is not None and type(self.diagnostic) is not Diagnostic:
            raise TypeError("diagnostic must be a Diagnostic or None")


class ApprovalService:
    def __init__(
        self,
        *,
        store: Store,
        clock: Clock,
        human_reviewer: ApprovalReviewer | None = None,
        independent_reviewer: ApprovalReviewer | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._human_reviewer = human_reviewer
        self._independent_reviewer = independent_reviewer

    async def handle_permission(
        self,
        worker: Worker,
        handle: WorkerHandle,
        event: WorkerEvent,
        context: ApprovalContext,
    ) -> ApprovalRecord:
        self._require_lineage(handle, event)
        if event.kind != "permission_requested":
            raise ValueError("event is not a permission request")
        permission_id, effect, risk = _permission_facts(event)
        requested_at = self._now()
        approval_id = _approval_id(handle.attempt_id, permission_id)
        request = ApprovalRequest(
            approval_id=approval_id,
            mission_id=context.mission_id,
            mission_version=context.mission_version,
            task_id=handle.task_id,
            attempt_id=handle.attempt_id,
            agent_id=handle.agent_id,
            permission_request_id=permission_id,
            effect=effect,
            risk=risk,
            scope_hash=context.scope_hash,
            requested_at=requested_at,
        )
        request = self._persist_request(request)
        replay = self._store.lookup_command(
            _decision_command(approval_id), "approval_decision"
        )
        if replay is None:
            decision, diagnostic = await self._decide(request, context.permission_scope)
            record = ApprovalRecord(
                request=request,
                state="approved" if decision.allowed else "denied",
                decision=decision,
                diagnostic_code=diagnostic,
            )
            self._persist_decision(record)
        else:
            record = _record_from_result(request, replay)
        response_failed = False
        try:
            await worker.respond_permission(
                handle,
                permission_request_id=request.permission_request_id,
                allowed=record.decision.allowed,
                reason=record.decision.reason,
            )
        except Exception:
            response_failed = True
        if response_failed:
            raise RuntimeError("approval_response_failed")
        return record

    async def bridge_attempt(
        self, worker: Worker, handle: WorkerHandle, context: ApprovalContext
    ) -> PermissionBridgeResult:
        approvals: list[ApprovalRecord] = []
        terminal_kind: str | None = None
        async for event in worker.stream_events(handle):
            self._require_lineage(handle, event)
            if event.kind == "permission_requested":
                if len(approvals) >= _MAX_PERMISSION_REQUESTS:
                    raise ValueError("permission request budget exceeded")
                record = await self.handle_permission(worker, handle, event, context)
                approvals.append(record)
                if not record.decision.allowed:
                    await worker.cancel_task(handle, reason=record.decision.reason)
            if event.kind in {"completed", "failed", "cancelled"}:
                terminal_kind = event.kind
        if terminal_kind is None:
            raise ValueError("worker stream ended without a terminal event")
        result = await worker.collect_result(handle)
        if result.status != terminal_kind:
            raise ValueError("worker terminal event and result disagree")
        diagnostic_code = next(
            (item.diagnostic_code for item in approvals if item.diagnostic_code), None
        )
        return PermissionBridgeResult(
            approvals=tuple(approvals),
            worker_result=result,
            terminal_result_validated=True,
            diagnostic=(
                None
                if diagnostic_code is None
                else self._diagnostic(diagnostic_code, handle, context)
            ),
        )

    async def _decide(
        self, request: ApprovalRequest, scope: PermissionScope
    ) -> tuple[ApprovalDecision, str | None]:
        if request.risk == "unclassified_tool_effect":
            code = "permission_effect_unclassified"
            return self._decision("agentdeck", False, code), code
        policy = scope.decide(request.effect, actor=request.agent_id)
        if policy.allowed:
            return self._decision("agentdeck", True, policy.reason), None
        if policy.requires_human:
            reviewer_id, error = _reviewer_identity(self._human_reviewer)
            if error is not None or (
                reviewer_id is not None and reviewer_id != "human"
            ):
                code = "approval_reviewer_invalid"
                return self._decision("agentdeck", False, code), code
            return await self._review(
                request, self._human_reviewer, "human_approval_unavailable",
                reviewer_id,
            )
        if policy.requires_independent_reviewer:
            reviewer = self._independent_reviewer
            reviewer_id, error = _reviewer_identity(reviewer)
            if error is not None or (
                reviewer_id is not None and not reviewer_id.startswith("agt_")
            ):
                code = "approval_reviewer_invalid"
                return self._decision("agentdeck", False, code), code
            if reviewer_id == request.agent_id:
                code = "approval_reviewer_not_independent"
                return self._decision("agentdeck", False, code), code
            return await self._review(
                request, reviewer, "independent_approval_unavailable", reviewer_id,
            )
        return self._decision("agentdeck", False, policy.reason), policy.reason

    async def _review(
        self,
        request: ApprovalRequest,
        reviewer: ApprovalReviewer | None,
        unavailable_code: str,
        reviewer_id: str | None,
    ) -> tuple[ApprovalDecision, str | None]:
        if reviewer is None or reviewer_id is None:
            return self._decision("agentdeck", False, unavailable_code), unavailable_code
        try:
            verdict = await reviewer.review(request)
            if type(verdict) is not ReviewerVerdict:
                raise TypeError
            decision = self._decision(reviewer_id, verdict.allowed, verdict.reason)
        except Exception:
            code = "approval_reviewer_failed"
            return self._decision("agentdeck", False, code), code
        return decision, None if decision.allowed else decision.reason

    def _decision(self, reviewer: str, allowed: bool, reason: str) -> ApprovalDecision:
        return ApprovalDecision(reviewer, allowed, reason, self._now())

    def _diagnostic(
        self, code: str, handle: WorkerHandle, context: ApprovalContext
    ) -> Diagnostic:
        return Diagnostic.create(
            code=code,
            stage="approval",
            severity=Severity.ERROR,
            actor=handle.agent_id,
            summary="The ACP permission request was denied.",
            cause="The confirmed permission policy could not authorize the request.",
            impact="The current Worker Attempt cannot continue.",
            protection="AgentDeck retained the confirmed scope and stopped the Attempt.",
            recovery_actions=("Review the permission decision and confirmed scope.",),
            retryable=False,
            outcome_known=True,
            occurred_at=self._now(),
            mission_id=context.mission_id,
            task_id=handle.task_id,
            attempt_id=handle.attempt_id,
        )

    def _persist_request(self, request: ApprovalRequest) -> ApprovalRequest:
        snapshot = _snapshot(request, "pending", None)

        def save(transaction):
            transaction.save_aggregate("approvals", request.approval_id, snapshot)
            transaction.append_event(_event(request, "approval_requested", self._now()))
            return _request_result(request)

        result = self._store.execute_once(
            _request_command(request.approval_id), "approval_request", save
        )
        durable = _request_from_result(result)
        if _request_identity(durable) != _request_identity(request):
            raise ValueError("permission request replay drifted")
        return durable

    def _persist_decision(self, record: ApprovalRecord) -> None:
        snapshot = _snapshot(record.request, record.state, record.decision)

        def save(transaction):
            transaction.save_aggregate("approvals", record.request.approval_id, snapshot)
            transaction.append_event(
                _event(record.request, "approval_decided", record.decision.decided_at)
            )
            return _record_result(record)

        self._store.execute_once(
            _decision_command(record.request.approval_id), "approval_decision", save
        )

    def _now(self) -> str:
        value = self._clock.now()
        if type(value) is not datetime:
            raise ValueError("clock must return a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.isoformat()

    @staticmethod
    def _require_lineage(handle: WorkerHandle, event: WorkerEvent) -> None:
        if type(handle) is not WorkerHandle or type(event) is not WorkerEvent:
            raise ValueError("worker handle or event lineage is invalid")
        expected = (handle.session_id, handle.agent_id, handle.task_id, handle.attempt_id)
        actual = (event.session_id, event.agent_id, event.task_id, event.attempt_id)
        if actual != expected or event.transport != handle.transport:
            raise ValueError("worker handle or event lineage is invalid")


def _approval_id(attempt_id: str, permission_id: str) -> str:
    digest = sha256(f"{attempt_id}\0{permission_id}".encode()).hexdigest()[:24]
    return f"apv_{digest}"


def _reviewer_identity(
    reviewer: ApprovalReviewer | None,
) -> tuple[str | None, str | None]:
    if reviewer is None:
        return None, None
    failed = False
    try:
        reviewer_id = reviewer.reviewer_id
    except Exception:
        failed = True
        reviewer_id = None
    if (
        failed or type(reviewer_id) is not str or not reviewer_id.strip()
        or any(character.isspace() for character in reviewer_id)
    ):
        return None, "approval_reviewer_invalid"
    return reviewer_id, None


def _request_command(approval_id: str) -> str:
    return f"cmd_{approval_id}_request"


def _decision_command(approval_id: str) -> str:
    return f"cmd_{approval_id}_decision"


def _request_facts(request: ApprovalRequest) -> dict[str, object]:
    return {
        "mission_id": request.mission_id,
        "mission_version": request.mission_version,
        "task_id": request.task_id,
        "attempt_id": request.attempt_id,
        "agent_id": request.agent_id,
        "permission_request_id": request.permission_request_id,
        "effect": request.effect.value,
        "risk": request.risk,
    }


def _decision_facts(decision: ApprovalDecision) -> dict[str, object]:
    return {
        "reviewer_id": decision.reviewer_id,
        "allowed": decision.allowed,
        "reason": decision.reason,
    }


def _snapshot(
    request: ApprovalRequest, state: str, decision: ApprovalDecision | None
) -> dict[str, object]:
    return {
        "approval_id": request.approval_id,
        "mission_id": request.mission_id,
        "mission_version": request.mission_version,
        "attempt_id": request.attempt_id,
        "effect": request.effect.value,
        "state": state,
        "scope_hash": request.scope_hash,
        "request": _request_facts(request),
        "decision": None if decision is None else _decision_facts(decision),
        "requested_at": request.requested_at,
        "decided_at": None if decision is None else decision.decided_at,
    }


def _event(request: ApprovalRequest, kind: str, occurred_at: str) -> dict[str, object]:
    return {
        "event_id": f"evt_{request.approval_id}_{kind}",
        "kind": kind,
        "aggregate_type": "approval",
        "aggregate_id": request.approval_id,
        "payload": {
            "mission_id": request.mission_id,
            "task_id": request.task_id,
            "attempt_id": request.attempt_id,
            "agent_id": request.agent_id,
            "permission_request_id": request.permission_request_id,
            "effect": request.effect.value,
        },
        "occurred_at": occurred_at,
    }


def _record_result(record: ApprovalRecord) -> dict[str, object]:
    return {
        "approval_id": record.request.approval_id,
        "state": record.state,
        "reviewer_id": record.decision.reviewer_id,
        "allowed": record.decision.allowed,
        "reason": record.decision.reason,
        "decided_at": record.decision.decided_at,
        "diagnostic_code": record.diagnostic_code,
    }


def _request_result(request: ApprovalRequest) -> dict[str, object]:
    return {
        "approval_id": request.approval_id,
        "mission_id": request.mission_id,
        "mission_version": request.mission_version,
        "task_id": request.task_id,
        "attempt_id": request.attempt_id,
        "agent_id": request.agent_id,
        "permission_request_id": request.permission_request_id,
        "effect": request.effect.value,
        "risk": request.risk,
        "scope_hash": request.scope_hash,
        "requested_at": request.requested_at,
    }


def _permission_facts(event: WorkerEvent) -> tuple[object, Effect, object]:
    invalid = False
    try:
        permission_id = event.payload["permission_request_id"]
        effect = Effect(event.payload["effect"])
        risk = event.payload["risk"]
    except (KeyError, TypeError, ValueError):
        invalid = True
        permission_id, effect, risk = None, Effect.DESTRUCTIVE, None
    if invalid:
        raise ValueError("permission event facts are invalid")
    return permission_id, effect, risk


def _request_from_result(result: dict[str, object]) -> ApprovalRequest:
    invalid = False
    try:
        request = ApprovalRequest(
            approval_id=result["approval_id"],
            mission_id=result["mission_id"],
            mission_version=result["mission_version"],
            task_id=result["task_id"],
            attempt_id=result["attempt_id"],
            agent_id=result["agent_id"],
            permission_request_id=result["permission_request_id"],
            effect=Effect(result["effect"]),
            risk=result["risk"],
            scope_hash=result["scope_hash"],
            requested_at=result["requested_at"],
        )
    except (KeyError, TypeError, ValueError):
        invalid = True
        request = None
    if invalid or request is None:
        raise ValueError("stored approval request is invalid")
    return request


def _request_identity(request: ApprovalRequest) -> tuple[object, ...]:
    return (
        request.approval_id, request.mission_id, request.mission_version,
        request.task_id, request.attempt_id, request.agent_id,
        request.permission_request_id, request.effect, request.risk,
        request.scope_hash,
    )


def _record_from_result(
    request: ApprovalRequest, result: dict[str, object]
) -> ApprovalRecord:
    invalid = False
    try:
        decision = ApprovalDecision(
            reviewer_id=result["reviewer_id"],
            allowed=result["allowed"],
            reason=result["reason"],
            decided_at=result["decided_at"],
        )
        record = ApprovalRecord(
            request=request,
            state=result["state"],
            decision=decision,
            diagnostic_code=result.get("diagnostic_code"),
        )
    except (KeyError, TypeError, ValueError):
        invalid = True
        record = None
    if invalid or record is None:
        raise ValueError("stored approval decision is invalid")
    return record


__all__ = ["ApprovalContext", "ApprovalService", "PermissionBridgeResult"]
