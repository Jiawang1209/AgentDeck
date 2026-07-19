"""Deterministic four-stage ACP execution coordinator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import json
from types import MappingProxyType

from agentdeck.application.approval_service import ApprovalContext, ApprovalService
from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.execution import (
    AcceptanceResult, Attempt, Evidence, EvidenceKind, FindingSeverity, Handoff,
    ReviewFinding,
)
from agentdeck.kernel.mission import ConfirmedMissionVersion, MissionDraft, TaskDefinition
from agentdeck.kernel.permissions import PermissionScope
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import Store
from agentdeck.ports.worker import TaskRequest, Worker, WorkerHandle, WorkerResult

@dataclass(frozen=True)
class AuthoritativeRevisionTask:
    task_id: str
    created_by: str
    confirmed_scope: str
    accepted_finding_ids: tuple[str, ...]
    def canonical_payload(self) -> dict[str, object]:
        return {
            "task_id": self.task_id, "created_by": self.created_by,
            "confirmed_scope": self.confirmed_scope,
            "accepted_finding_ids": list(self.accepted_finding_ids),
        }

@dataclass(frozen=True)
class ExecutionResult:
    attempts: tuple[Attempt, ...]
    evidence: tuple[Evidence, ...]
    handoffs: tuple[Handoff, ...]
    revision_task: AuthoritativeRevisionTask
    diagnostic: Diagnostic | None = None

class ExecutionService:
    def __init__(
        self, *, store: Store, clock: Clock, approval_service: ApprovalService,
        worker_factory: Callable[[TaskDefinition], Worker],
    ) -> None:
        self._store = store
        self._clock = clock
        self._approval_service = approval_service
        self._worker_factory = worker_factory
    async def run_confirmed_mission(
        self, *, session_id: str, confirmed: ConfirmedMissionVersion,
        draft: MissionDraft, permission_scope: PermissionScope,
    ) -> ExecutionResult:
        self._validate_authority(session_id, confirmed, draft, permission_scope)
        attempts: list[Attempt] = []
        evidence: list[Evidence] = []
        handoffs: list[Handoff] = []
        revision_task = AuthoritativeRevisionTask(
            "tsk_revision", "agentdeck", draft.scope, ()
        )
        for index, task in enumerate(draft.tasks):
            if task.dependencies and (
                not handoffs or handoffs[-1].target_task_id != task.task_id
            ):
                raise RuntimeError("task dependency has no committed handoff")
            attempt = Attempt.pending(
                _stage_id("att_", confirmed, task, "1"), task.task_id, 1,
            ).start()
            try:
                request = TaskRequest(
                    task.agent_instance_id, task.task_id, attempt.attempt_id,
                    _task_instruction(
                        draft, confirmed, task, attempt,
                        handoffs[-1] if handoffs else None,
                        revision_task if task.name == "revision" else None,
                    ),
                )
                effective_scope = permission_scope.narrow(
                    permission_scope.effects.intersection(task.allowed_effects)
                )
            except Exception:
                invalid = attempt.fail("task_request_invalid", retryable=False)
                return ExecutionResult(
                    tuple(attempts), tuple(evidence), tuple(handoffs), revision_task,
                    self._diagnostic(
                        "task_request_invalid", confirmed, task, invalid,
                        "the complete bounded Task request was invalid",
                    ),
                )
            if not self._persist_started(attempt, task, confirmed):
                return ExecutionResult(
                    tuple(attempts), tuple(evidence), tuple(handoffs), revision_task,
                    self._diagnostic(
                        "mission_execution_replayed", confirmed, task, attempt,
                        "the confirmed execution command already exists",
                    ),
                )
            attempts.append(attempt)
            try:
                worker = self._worker_factory(task)
                handle = await worker.start_task(request)
            except Exception:
                return self._stop_attempt(
                    confirmed, attempts, evidence, handoffs, revision_task, task,
                    attempt.fail("worker_start_failed", retryable=False),
                    "worker_start_failed", "ACP Worker task start failed",
                )
            if not _handle_matches_request(handle, request):
                return self._stop_attempt(
                    confirmed, attempts, evidence, handoffs, revision_task, task,
                    attempt.unknown_outcome("worker_handle_lineage_invalid"),
                    "worker_handle_lineage_invalid",
                    "ACP Worker handle did not match the exact Task request",
                )
            try:
                self._bind_acp_session(attempt, task, confirmed, handle.session_id)
            except Exception:
                return self._stop_attempt(
                    confirmed, attempts, evidence, handoffs, revision_task, task,
                    attempt.unknown_outcome("acp_session_binding_failed"),
                    "acp_session_binding_failed",
                    "the validated ACP session did not bind durably",
                )
            try:
                bridge = await self._approval_service.bridge_attempt(
                    worker, handle,
                    ApprovalContext(
                        confirmed.mission_id, confirmed.version,
                        effective_scope,
                        confirmed.content_hash,
                    ),
                )
            except Exception:
                return self._stop_attempt(
                    confirmed, attempts, evidence, handoffs, revision_task, task,
                    attempt.unknown_outcome("worker_bridge_failed"),
                    "worker_bridge_failed", "ACP Worker event bridge failed",
                    acp_session_id=handle.session_id,
                )
            worker_result = bridge.worker_result
            if bridge.diagnostic is not None or any(
                approval.state != "approved" for approval in bridge.approvals
            ):
                return self._stop_attempt(
                    confirmed, attempts, evidence, handoffs, revision_task, task,
                    (
                        attempt.cancel("permission_denied")
                        if worker_result.status == "cancelled"
                        else attempt.unknown_outcome("permission_denied")
                    ),
                    "permission_denied",
                    "an ACP permission request was denied",
                    acp_session_id=handle.session_id,
                )
            if worker_result.status != "completed":
                return self._worker_failure(
                    confirmed, attempts, evidence, handoffs, revision_task, task,
                    attempt, worker_result, handle.session_id,
                )
            try:
                terminal = attempt.complete(_payload_text(worker_result, "summary"))
                typed_evidence, artifacts = _typed_evidence(
                    _stage_id("ev_", confirmed, task, "1"),
                    task, worker_result, draft,
                )
                candidate_revision = revision_task
                if task.name == "review":
                    finding = _review_finding(worker_result)
                    candidate_revision = AuthoritativeRevisionTask(
                        draft.tasks[2].task_id, "agentdeck", draft.scope,
                        (finding.finding_id,),
                    )
            except Exception:
                return self._stop_attempt(
                    confirmed, attempts, evidence, handoffs, revision_task, task,
                    attempt.unknown_outcome("worker_result_invalid"),
                    "worker_result_invalid", "ACP Worker result schema was invalid",
                    acp_session_id=handle.session_id,
                )
            handoff = None
            if index < len(draft.tasks) - 1:
                handoff = Handoff.create(
                    _stage_id(
                        "hnd_", confirmed, task,
                        draft.tasks[index + 1].task_id, "1",
                    ),
                    terminal.attempt_id,
                    draft.tasks[index + 1].task_id, terminal.result_summary,
                    (typed_evidence.evidence_id,), artifact_references=artifacts,
                )
            try:
                self._persist_terminal(
                    terminal, task, typed_evidence, handoff, confirmed,
                    handle.session_id,
                )
            except Exception:
                return ExecutionResult(
                    tuple(attempts), tuple(evidence), tuple(handoffs), revision_task,
                    self._diagnostic(
                        "stage_bundle_persistence_failed", confirmed, task, terminal,
                        "terminal execution bundle did not commit",
                    ),
                )
            attempts[-1] = terminal
            evidence.append(typed_evidence)
            if handoff is not None:
                handoffs.append(handoff)
            revision_task = candidate_revision
        return ExecutionResult(
            tuple(attempts), tuple(evidence), tuple(handoffs), revision_task
        )
    def _validate_authority(
        self, session_id: str, confirmed: ConfirmedMissionVersion,
        draft: MissionDraft, permission_scope: PermissionScope,
    ) -> None:
        if type(session_id) is not str or not session_id.startswith("ses_"):
            raise ValueError("session_id must be a typed identity")
        if type(confirmed) is not ConfirmedMissionVersion or type(draft) is not MissionDraft:
            raise TypeError("execution requires a confirmed Mission and its draft")
        if type(permission_scope) is not PermissionScope:
            raise TypeError("permission_scope must be a PermissionScope")
        preview = draft.preview(confirmed.version)
        if preview.content_hash != confirmed.content_hash or (
            preview.canonical_content != confirmed.canonical_content
        ):
            raise ValueError("execution draft does not match confirmed Mission")
        if permission_scope.profile is not draft.permission_profile:
            raise ValueError("permission profile does not match confirmed Mission")
    def _persist_started(
        self, attempt: Attempt, task: TaskDefinition,
        confirmed: ConfirmedMissionVersion,
    ) -> bool:
        created = False

        def commit(transaction):
            nonlocal created
            transaction.save_aggregate(
                "attempts", attempt.attempt_id, _attempt_snapshot(attempt, task)
            )
            created = True
            return {"attempt_id": attempt.attempt_id, "state": attempt.state.value}

        self._store.execute_once(
            _command_id("start", confirmed, task, attempt.ordinal),
            "execution_attempt_started", commit,
        )
        return created
    def _bind_acp_session(
        self, attempt: Attempt, task: TaskDefinition,
        confirmed: ConfirmedMissionVersion, acp_session_id: str,
    ) -> None:
        def commit(transaction):
            transaction.save_aggregate(
                "attempts", attempt.attempt_id,
                _attempt_snapshot(attempt, task, acp_session_id),
            )
            return {"attempt_id": attempt.attempt_id, "acp_session_id": acp_session_id}

        self._store.execute_once(
            _command_id("bind_acp", confirmed, task, attempt.ordinal),
            "execution_acp_session_bound", commit,
        )
    def _persist_terminal(
        self, attempt: Attempt, task: TaskDefinition, evidence: Evidence,
        handoff: Handoff | None, confirmed: ConfirmedMissionVersion,
        acp_session_id: str,
    ) -> None:
        def commit(transaction):
            transaction.save_aggregate(
                "attempts", attempt.attempt_id,
                _attempt_snapshot(attempt, task, acp_session_id),
            )
            transaction.save_aggregate(
                "evidence", evidence.evidence_id,
                {
                    "evidence_id": evidence.evidence_id, "task_id": attempt.task_id,
                    "attempt_id": attempt.attempt_id, "kind": evidence.kind.value,
                    "canonical_evidence_facts": evidence.canonical_content,
                },
            )
            if handoff is not None:
                transaction.save_aggregate(
                    "handoffs", handoff.handoff_id,
                    {
                        "handoff_id": handoff.handoff_id,
                        "source_attempt_id": handoff.source_attempt_id,
                        "target_task_id": handoff.target_task_id,
                        "result_summary": handoff.result_summary,
                        "canonical_handoff_facts": handoff.canonical_content,
                        "content_hash": handoff.content_hash,
                    },
                )
            return {
                "attempt_id": attempt.attempt_id, "state": attempt.state.value,
                "evidence_id": evidence.evidence_id,
                "handoff_id": None if handoff is None else handoff.handoff_id,
            }

        self._store.execute_once(
            _command_id("terminal", confirmed, task, attempt.ordinal),
            "execution_stage_committed", commit,
        )
    def _worker_failure(
        self, confirmed, attempts, evidence, handoffs, revision_task,
        task, attempt, result, acp_session_id,
    ) -> ExecutionResult:
        terminal = (
            attempt.cancel("worker_cancelled")
            if result.status == "cancelled"
            else attempt.fail("worker_failed", retryable=False)
        )
        return self._stop_attempt(
            confirmed, attempts, evidence, handoffs, revision_task, task, terminal,
            "worker_stage_failed",
            "ACP Worker returned a non-completed terminal result",
            acp_session_id=acp_session_id,
        )
    def _stop_attempt(
        self, confirmed, attempts, evidence, handoffs, revision_task, task,
        terminal, code, cause, *, acp_session_id=None,
    ) -> ExecutionResult:
        try:
            self._persist_terminal_attempt(
                terminal, task, confirmed, acp_session_id
            )
        except Exception:
            return ExecutionResult(
                tuple(attempts), tuple(evidence), tuple(handoffs), revision_task,
                self._diagnostic(
                    "terminal_attempt_persistence_failed", confirmed, task,
                    attempts[-1], "the safe terminal Attempt did not commit",
                ),
            )
        attempts[-1] = terminal
        return ExecutionResult(
            tuple(attempts), tuple(evidence), tuple(handoffs), revision_task,
            self._diagnostic(code, confirmed, task, terminal, cause),
        )
    def _persist_terminal_attempt(
        self, attempt: Attempt, task: TaskDefinition,
        confirmed: ConfirmedMissionVersion, acp_session_id: str | None,
    ) -> None:
        def commit(transaction):
            transaction.save_aggregate(
                "attempts", attempt.attempt_id,
                _attempt_snapshot(attempt, task, acp_session_id),
            )
            return {"attempt_id": attempt.attempt_id, "state": attempt.state.value}

        self._store.execute_once(
            _command_id("stop", confirmed, task, attempt.ordinal),
            "execution_attempt_stopped", commit,
        )
    def _diagnostic(self, code, confirmed, task, attempt, cause) -> Diagnostic:
        outcome_known = attempt.state.value in {
            "completed", "failed", "cancelled", "interrupted",
        }
        return Diagnostic.create(
            code=code, stage="execution", severity=Severity.ERROR,
            actor="agentdeck", summary="automatic execution stopped", cause=cause,
            impact="the dependent Task was not started",
            protection="durable dependency and handoff authority remained closed",
            recovery_actions=(
                ("inspect and reconcile the durable Attempt before any new action",)
                if not outcome_known
                else ("inspect the durable Attempt before an explicit new action",)
            ),
            retryable=False,
            outcome_known=outcome_known,
            occurred_at=self._clock.now().isoformat(), mission_id=confirmed.mission_id,
            task_id=None if task is None else task.task_id,
            attempt_id=attempt.attempt_id,
        )


def _attempt_snapshot(
    attempt: Attempt, task: TaskDefinition, acp_session_id: str | None = None,
) -> dict[str, object]:
    return {
        "attempt_id": attempt.attempt_id, "task_id": attempt.task_id,
        "agent_instance_id": task.agent_instance_id, "ordinal": attempt.ordinal,
        "state": attempt.state.value, "reason": attempt.reason,
        "result_summary": attempt.result_summary, "retryable": attempt.retryable,
        "acp_session_id": acp_session_id, "effect_observed": False,
    }
def _handle_matches_request(handle: object, request: TaskRequest) -> bool:
    return type(handle) is WorkerHandle and (
        handle.agent_id, handle.task_id, handle.attempt_id, handle.transport
    ) == (request.agent_id, request.task_id, request.attempt_id, "acp")
def _derived_id(prefix: str, *parts: str) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return prefix + sha256(canonical.encode("utf-8")).hexdigest()[:24]
def _stage_id(
    prefix: str, confirmed: ConfirmedMissionVersion,
    task: TaskDefinition, *parts: str,
) -> str:
    return _derived_id(
        prefix, confirmed.mission_id, str(confirmed.version), task.task_id, *parts
    )
def _command_id(
    phase: str, confirmed: ConfirmedMissionVersion,
    task: TaskDefinition, ordinal: int,
) -> str:
    return _stage_id("cmd_", confirmed, task, phase, str(ordinal))
def _task_instruction(
    draft: MissionDraft,
    confirmed: ConfirmedMissionVersion,
    task: TaskDefinition,
    attempt: Attempt,
    incoming_handoff: Handoff | None,
    revision_task: AuthoritativeRevisionTask | None,
) -> str:
    payload = {
        "mission": {
            "mission_id": confirmed.mission_id,
            "version": confirmed.version,
            "content_hash": confirmed.content_hash,
            "objective": draft.objective,
            "scope": draft.scope,
        },
        "task": task.canonical_projection(),
        "attempt": {
            "attempt_id": attempt.attempt_id, "ordinal": attempt.ordinal,
        },
        "incoming_handoff": (
            None if incoming_handoff is None else {
                "canonical_content": incoming_handoff.canonical_content,
                "content_hash": incoming_handoff.content_hash,
            }
        ),
        "authoritative_revision_task": (
            None if revision_task is None else revision_task.canonical_payload()
        ),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _payload_value(result: WorkerResult, field: str) -> object:
    if field not in result.payload:
        raise ValueError(f"worker result missing {field}")
    return result.payload[field]


def _payload_text(result: WorkerResult, field: str) -> str:
    value = _payload_value(result, field)
    if type(value) is not str or not value.strip():
        raise ValueError(f"worker result {field} must be text")
    return value


def _review_finding(result: WorkerResult) -> ReviewFinding:
    evidence_ids = _payload_value(result, "evidence_ids")
    if type(evidence_ids) is not tuple:
        raise ValueError("review evidence_ids must be a sequence")
    return ReviewFinding(
        _payload_text(result, "finding_id"), _payload_text(result, "scope"),
        FindingSeverity(_payload_text(result, "severity")),
        _payload_text(result, "summary"), _payload_text(result, "criterion"),
        evidence_ids,
    )


def _typed_evidence(
    identity: str, task: TaskDefinition, result: WorkerResult, draft: MissionDraft
) -> tuple[Evidence, tuple[str, ...]]:
    if task.name == "implementation":
        artifact = _payload_text(result, "artifact_reference")
        return Evidence.create(identity, EvidenceKind.ARTIFACT_HASH, {
            "artifact_reference": artifact,
            "content_hash": _payload_text(result, "content_hash"),
        }), (artifact,)
    if task.name == "review":
        finding = _review_finding(result)
        return Evidence.create(
            identity, EvidenceKind.REVIEW_FINDING, finding.canonical_projection()
        ), ()
    if task.name == "revision":
        return Evidence.create(identity, EvidenceKind.DIFF_IDENTITY, {
            "base": _payload_text(result, "base"),
            "head": _payload_text(result, "head"),
            "diff_hash": _payload_text(result, "diff_hash"),
        }), ()
    mapping = _payload_value(result, "evidence_by_criterion")
    if not isinstance(mapping, MappingProxyType):
        raise ValueError("acceptance evidence mapping must be an object")
    acceptance = AcceptanceResult.create(
        draft.acceptance_criteria, dict(mapping),
        accepted=_payload_value(result, "accepted"),
        failure_reason=_payload_value(result, "failure_reason"),
    )
    return Evidence.acceptance(
        identity, result=acceptance, source_kind=EvidenceKind.ACCEPTANCE_RESULT
    ), ()
