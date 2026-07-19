"""Deterministic four-stage ACP execution coordinator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agentdeck.application.approval_service import ApprovalContext, ApprovalService
from agentdeck.application.execution_records import (
    attempt_snapshot as _attempt_snapshot,
    command_id as _command_id,
    exception_condition as _exception_condition,
    handle_matches_request as _handle_matches_request,
    payload_text as _payload_text,
    review_finding as _review_finding,
    stage_id as _stage_id,
    task_instruction as _task_instruction,
    typed_evidence as _typed_evidence,
    worker_failure_condition as _worker_failure_condition,
)
from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.execution import Attempt, Evidence, Handoff
from agentdeck.kernel.execution_semantics import RetryPolicy, materialize_revision
from agentdeck.kernel.mission import ConfirmedMissionVersion, MissionDraft, TaskDefinition
from agentdeck.kernel.permissions import PermissionScope
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import Store
from agentdeck.ports.worker import TaskRequest, Worker

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
        retry_policy = RetryPolicy.default()
        for index, task in enumerate(draft.tasks):
            if task.dependencies and (
                not handoffs or handoffs[-1].target_task_id != task.task_id
            ):
                raise RuntimeError("task dependency has no committed handoff")
            max_attempts = 1 if task.name == "acceptance" else min(draft.max_attempts, 2)
            reconnects = 0
            for ordinal in range(1, max_attempts + 1):
                attempt = Attempt.pending(
                    _stage_id("att_", confirmed, task, str(ordinal)),
                    task.task_id, ordinal,
                ).start()
                try:
                    request = TaskRequest(
                        task.agent_instance_id, task.task_id, attempt.attempt_id,
                        _task_instruction(
                            draft, confirmed, task, attempt,
                            handoffs[-1] if handoffs else None,
                            (
                                revision_task.canonical_payload()
                                if task.name == "revision" else None
                            ),
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
                except Exception as error:
                    condition = _exception_condition(
                        error, task_id=task.task_id, attempt_id=attempt.attempt_id
                    )
                    can_reconnect = (
                        condition == "transport_before_effect"
                        and reconnects < min(draft.max_acp_reconnects, 1)
                    )
                    if (can_reconnect or condition == "worker_schema_invalid") and self._retry_attempt(
                        retry_policy, condition, attempt, task, confirmed,
                        attempts, max_attempts, acp_session_id=None,
                    ):
                        reconnects += int(condition == "transport_before_effect")
                        continue
                    code = condition or "worker_start_failed"
                    reason = (
                        "recoverable_transport_interruption"
                        if condition == "transport_before_effect"
                        else condition or "worker_start_failed"
                    )
                    terminal = (
                        attempt.unknown_outcome("outcome_unknown")
                        if condition == "outcome_unknown"
                        else attempt.fail(reason, retryable=False)
                    )
                    return self._stop_attempt(
                        confirmed, attempts, evidence, handoffs, revision_task, task,
                        terminal, code,
                        "ACP Worker task start failed",
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
                            effective_scope, confirmed.content_hash,
                        ),
                    )
                except Exception as error:
                    condition = _exception_condition(
                        error, task_id=task.task_id, attempt_id=attempt.attempt_id
                    )
                    can_reconnect = (
                        condition == "transport_before_effect"
                        and reconnects < min(draft.max_acp_reconnects, 1)
                    )
                    if (can_reconnect or condition == "worker_schema_invalid") and self._retry_attempt(
                        retry_policy, condition, attempt, task, confirmed,
                        attempts, max_attempts, acp_session_id=handle.session_id,
                    ):
                        reconnects += int(condition == "transport_before_effect")
                        continue
                    if condition == "outcome_unknown":
                        terminal = attempt.unknown_outcome("outcome_unknown")
                    elif condition == "transport_before_effect":
                        terminal = attempt.fail(
                            "recoverable_transport_interruption", retryable=False
                        )
                    else:
                        terminal = attempt.unknown_outcome("worker_bridge_failed")
                    return self._stop_attempt(
                        confirmed, attempts, evidence, handoffs, revision_task, task,
                        terminal, condition or "worker_bridge_failed",
                        "ACP Worker event bridge failed",
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
                        "permission_denied", "an ACP permission request was denied",
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
                        _stage_id("ev_", confirmed, task, str(ordinal)),
                        task, worker_result, draft,
                    )
                    candidate_revision = revision_task
                    if task.name == "review":
                        finding = _review_finding(worker_result)
                        materialized = materialize_revision(
                            findings=(finding,), confirmed_scope=(draft.scope,)
                        )
                        if materialized.rejected:
                            return self._stop_attempt(
                                confirmed, attempts, evidence, handoffs,
                                revision_task, task,
                                attempt.fail("scope_insufficiency", retryable=False),
                                "scope_insufficiency",
                                "review findings exceeded the confirmed scope",
                                acp_session_id=handle.session_id,
                            )
                        candidate_revision = AuthoritativeRevisionTask(
                            draft.tasks[2].task_id, "agentdeck", draft.scope,
                            tuple(item.finding_id for item in materialized.findings),
                        )
                    if (
                        task.name == "acceptance"
                        and worker_result.payload["accepted"] is False
                    ):
                        terminal = attempt.fail("acceptance_failed", retryable=False)
                except Exception:
                    code = (
                        "acceptance_evidence_missing"
                        if task.name == "acceptance" else "worker_result_invalid"
                    )
                    return self._stop_attempt(
                        confirmed, attempts, evidence, handoffs, revision_task, task,
                        attempt.unknown_outcome(code), code,
                        "ACP Worker result schema was invalid",
                        acp_session_id=handle.session_id,
                    )
                handoff = None
                if index < len(draft.tasks) - 1:
                    handoff = Handoff.create(
                        _stage_id(
                            "hnd_", confirmed, task,
                            draft.tasks[index + 1].task_id, str(ordinal),
                        ),
                        terminal.attempt_id, draft.tasks[index + 1].task_id,
                        terminal.result_summary, (typed_evidence.evidence_id,),
                        artifact_references=artifacts,
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
                            "stage_bundle_persistence_failed", confirmed, task,
                            terminal, "terminal execution bundle did not commit",
                        ),
                    )
                attempts[-1] = terminal
                evidence.append(typed_evidence)
                if handoff is not None:
                    handoffs.append(handoff)
                revision_task = candidate_revision
                if terminal.reason == "acceptance_failed":
                    return ExecutionResult(
                        tuple(attempts), tuple(evidence), tuple(handoffs), revision_task,
                        self._diagnostic(
                            "acceptance_failed", confirmed, task, terminal,
                            "the typed acceptance result did not accept every criterion",
                        ),
                    )
                break
        return ExecutionResult(
            tuple(attempts), tuple(evidence), tuple(handoffs), revision_task
        )
    def _retry_attempt(
        self, policy, condition, attempt, task, confirmed, attempts,
        max_attempts, *, acp_session_id,
    ) -> bool:
        decision = policy.decision(condition, ordinal=attempt.ordinal)
        if not decision.retry or attempt.ordinal >= max_attempts:
            return False
        reason = (
            "recoverable_transport_interruption"
            if condition == "transport_before_effect" else "worker_schema_invalid"
        )
        terminal = attempt.fail(reason, retryable=True)
        try:
            self._persist_terminal_attempt(
                terminal, task, confirmed, acp_session_id
            )
        except Exception:
            return False
        attempts[-1] = terminal
        return True
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
        condition = _worker_failure_condition(result)
        terminal = (
            attempt.cancel("worker_cancelled")
            if result.status == "cancelled"
            else attempt.fail(condition or "worker_failed", retryable=False)
        )
        return self._stop_attempt(
            confirmed, attempts, evidence, handoffs, revision_task, task, terminal,
            condition or "worker_stage_failed",
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
