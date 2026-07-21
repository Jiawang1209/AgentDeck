"""Deterministic four-stage ACP execution coordinator."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
from agentdeck.application.approval_service import ApprovalContext, ApprovalService
from agentdeck.application import execution_authority as _authority, execution_records as _records
from agentdeck.application.takeover_control import TakeoverExecutionMixin
from agentdeck.application.takeover_wait import (AutomaticAuthorityReleased,
    await_operation_or_release, released_execution_result)
from agentdeck.application.execution_resume import (
    ExecutionResult, ExecutionResumePlan, initial_execution_state, validate_execution_authority,
)
from agentdeck.application.execution_records import AuthoritativeRevisionTask
from agentdeck.application.execution_runtime import (
    ExecutionReservation, ForegroundExecutionRuntime, execution_diagnostic,
    reject_reserved_worker, retry_execution_attempt,
    start_reserved_worker, stop_execution_attempt, worker_failure_result,
)
from agentdeck.application.project_lifecycle_service import ProjectDispatchBlocked, ProjectLifecycleService
from agentdeck.kernel.execution import Attempt, Evidence, Handoff
from agentdeck.kernel.execution_semantics import RetryPolicy, materialize_revision
from agentdeck.kernel.mission import ConfirmedMissionVersion, MissionDraft, TaskDefinition
from agentdeck.kernel.permissions import PermissionScope
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import Store
from agentdeck.ports.worker import TaskRequest, Worker
class ExecutionService(TakeoverExecutionMixin):
    def __init__(
        self, *, store: Store, clock: Clock, approval_service: ApprovalService,
        worker_factory: Callable[[TaskDefinition], Worker],
        runtime: ForegroundExecutionRuntime, lifecycle: ProjectLifecycleService,
    ) -> None:
        self._store, self._clock = store, clock
        self._approval_service, self._worker_factory = approval_service, worker_factory
        self._runtime, self._lifecycle = runtime, lifecycle
    async def run_confirmed_mission(
        self, *, session_id: str, confirmed: ConfirmedMissionVersion,
        draft: MissionDraft, permission_scope: PermissionScope,
        resume_plan: ExecutionResumePlan | None = None,
    ) -> ExecutionResult:
        validate_execution_authority(session_id, confirmed, draft, permission_scope, resume_plan)
        state = initial_execution_state(resume_plan, draft)
        attempts, evidence = list(state.attempts), list(state.evidence)
        committed_evidence, handoffs = list(state.committed_evidence), list(state.handoffs)
        revision_task = state.revision_task
        offset = len(draft.tasks) - len(state.stages)
        for index, (task, first_ordinal, attempt_budget) in enumerate(state.stages, offset):
            if index > offset:
                await asyncio.sleep(0)
            if task.dependencies and (
                not handoffs or handoffs[-1].target_task_id != task.task_id
            ):
                raise RuntimeError("task dependency has no committed handoff")
            reconnects = 0
            for attempt_number, ordinal in enumerate(range(
                first_ordinal, first_ordinal + attempt_budget)):
                reservation = None
                attempt = Attempt.pending(
                    _records.stage_id("att_", confirmed, task, str(ordinal)),
                    task.task_id, ordinal,
                ).start()
                try:
                    request = TaskRequest(
                        task.agent_instance_id, task.task_id, attempt.attempt_id,
                        _records.task_instruction(
                            draft, confirmed, task, attempt,
                            handoffs[-1] if handoffs else None,
                            revision_task.canonical_payload()
                            if task.name == "revision" else None,
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
                try:
                    async with self._lifecycle.dispatch_lease():
                        self._lifecycle.require_dispatchable()
                        if not self._persist_started(attempt, task, confirmed):
                            diagnostic = self._diagnostic(
                                "mission_execution_replayed", confirmed, task, attempt,
                                "the confirmed execution command already exists")
                            return ExecutionResult(tuple(attempts), tuple(evidence),
                                tuple(handoffs), revision_task, diagnostic)
                        attempts.append(attempt)
                        self._lifecycle.require_dispatchable()
                        worker = self._worker_factory(task)
                        candidate = ExecutionReservation(
                            attempt.attempt_id, task.task_id,
                            task.agent_instance_id, worker,
                        )
                        try:
                            self._runtime.reserve(candidate)
                        except Exception:
                            return self._stop_attempt(
                                confirmed, attempts, evidence, handoffs,
                                revision_task, task, attempt.fail(
                                    "execution_binding_rejected", retryable=False),
                                "execution_binding_rejected",
                                "the Worker identity was not available for a new Attempt",
                            )
                        reservation = candidate
                        binding = await start_reserved_worker(
                            self._runtime, reservation, request)
                        if binding is None:
                            return self._stop_attempt(
                                confirmed, attempts, evidence, handoffs,
                                revision_task, task, attempt.unknown_outcome(
                                    "worker_handle_lineage_invalid"),
                                "worker_handle_lineage_invalid",
                                "ACP Worker handle did not match the exact Task request",
                                reservation=reservation,
                            )
                        handle = binding.worker_handle
                        try:
                            attempt = self._bind_acp_session(
                                attempt, task, confirmed, handle.session_id
                            )
                        except Exception:
                            await reject_reserved_worker(
                                self._runtime, reservation, handle)
                            self._runtime.quarantine(reservation)
                            failed = attempt.unknown_outcome(
                                "acp_session_binding_failed")
                            diagnostic = self._diagnostic(
                                "acp_session_binding_failed", confirmed, task,
                                failed,
                                "the validated ACP session did not bind durably",
                            )
                            return ExecutionResult(
                                tuple(attempts), tuple(evidence),
                                tuple(handoffs), revision_task, diagnostic,
                            )
                        attempts[-1] = attempt
                        try:
                            self._takeover_activate(reservation, binding, (session_id, confirmed, task, attempt, effective_scope))
                        except Exception:
                            await reject_reserved_worker(
                                self._runtime, reservation, handle)
                            return self._stop_attempt(
                                confirmed, attempts, evidence, handoffs, revision_task,
                                task, attempt.unknown_outcome("acp_session_binding_failed"),
                                "acp_session_binding_failed",
                                "the validated ACP session did not bind durably",
                                acp_session_id=handle.session_id,
                                reservation=reservation,
                            )
                except ProjectDispatchBlocked:
                    if attempts and attempts[-1] == attempt:
                        return self._stop_attempt(
                            confirmed, attempts, evidence, handoffs,
                            revision_task, task,
                            attempt.interrupt("project_dispatch_paused"),
                            "project_dispatch_paused",
                            "the durable project stop intent closed dispatch",
                        )
                    stopped = attempt.interrupt("project_dispatch_paused")
                    diagnostic = self._diagnostic(
                        "project_dispatch_paused", confirmed, task, stopped,
                        "the durable project stop intent closed dispatch")
                    return ExecutionResult(tuple(attempts), tuple(evidence),
                        tuple(handoffs), revision_task, diagnostic)
                except Exception as error:
                    if not attempts or attempts[-1] != attempt:
                        stopped = attempt.interrupt("execution_start_failed")
                        diagnostic = self._diagnostic(
                            "execution_start_failed", confirmed, task, stopped,
                            "the durable execution start boundary did not close",
                        )
                        return ExecutionResult(
                            tuple(attempts), tuple(evidence), tuple(handoffs),
                            revision_task, diagnostic,
                        )
                    condition = _records.exception_condition(
                        error, task_id=task.task_id, attempt_id=attempt.attempt_id
                    )
                    if reservation is not None and condition == "outcome_unknown":
                        self._runtime.quarantine(reservation)
                    can_reconnect = (
                        condition == "transport_before_effect"
                        and reconnects < min(draft.max_acp_reconnects, 1)
                    )
                    if (can_reconnect or condition == "worker_schema_invalid") and retry_execution_attempt(
                        runtime=self._runtime, persist=self._persist_terminal_attempt,
                        policy=RetryPolicy.default(), condition=condition,
                        attempt=attempt, task=task, confirmed=confirmed,
                        attempts=attempts, retry_ordinal=attempt_number + 1,
                        can_retry=attempt_number + 1 < attempt_budget,
                        acp_session_id=None, reservation=reservation,
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
                        reservation=reservation,
                    )
                try:
                    bridge = await await_operation_or_release(
                        self._approval_service.bridge_attempt(
                            self._takeover_worker(worker, handle), handle,
                            ApprovalContext(confirmed.mission_id, confirmed.version, effective_scope, confirmed.content_hash)),
                        self._runtime, attempt.attempt_id, handle,
                    )
                except AutomaticAuthorityReleased:
                    return released_execution_result(self, confirmed, attempts,
                        evidence, handoffs, revision_task, task, handle)
                except Exception as error:
                    condition = _records.exception_condition(error,
                        task_id=task.task_id, attempt_id=attempt.attempt_id)
                    can_reconnect = (condition == "transport_before_effect" and
                        reconnects < min(draft.max_acp_reconnects, 1))
                    if (can_reconnect or condition == "worker_schema_invalid") and retry_execution_attempt(
                        runtime=self._runtime, persist=self._persist_terminal_attempt,
                        policy=RetryPolicy.default(), condition=condition,
                        attempt=attempt, task=task, confirmed=confirmed,
                        attempts=attempts, retry_ordinal=attempt_number + 1,
                        can_retry=attempt_number + 1 < attempt_budget,
                        acp_session_id=handle.session_id, worker_handle=handle,
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
                        terminal, condition or "worker_bridge_failed", "ACP Worker event bridge failed",
                        acp_session_id=handle.session_id, worker_handle=handle,
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
                        acp_session_id=handle.session_id, worker_handle=handle,
                    )
                if worker_result.status != "completed":
                    return worker_failure_result(
                        self._stop_attempt, confirmed, attempts, evidence,
                        handoffs, revision_task, task, attempt, worker_result,
                        handle,
                    )
                evidence_authority = (
                    None if not index else _records.EvidenceAuthority(
                        tuple(committed_evidence), confirmed.mission_id,
                        draft.tasks[index - 1].task_id, tuple(attempts),
                    )
                )
                try:
                    validated = _records.validated_stage_result(
                        _records.stage_id("ev_", confirmed, task, str(ordinal)),
                        task, worker_result, draft,
                        accepted_finding_ids=revision_task.accepted_finding_ids,
                        expected_revision_evidence_ids=revision_task.review_evidence_ids,
                        evidence_authority=evidence_authority,
                    )
                    terminal = attempt.complete(validated.summary)
                except _records.EvidenceLineageError:
                    return self._stop_attempt(
                        confirmed, attempts, evidence, handoffs, revision_task, task,
                        attempt.unknown_outcome("evidence_lineage_invalid"),
                        "evidence_lineage_invalid",
                        "typed evidence did not bind to the committed prior Task",
                        acp_session_id=handle.session_id, worker_handle=handle,
                    )
                except Exception:
                    code = (
                        "acceptance_evidence_missing"
                        if task.name == "acceptance" else "worker_result_invalid"
                    )
                    return self._stop_attempt(
                        confirmed, attempts, evidence, handoffs, revision_task, task,
                        attempt.unknown_outcome(code), code,
                        "ACP Worker result schema was invalid",
                        acp_session_id=handle.session_id, worker_handle=handle,
                    )
                try:
                    candidate_revision = revision_task
                    if task.name == "review":
                        materialized = materialize_revision(
                            findings=validated.review_findings,
                            confirmed_scope=(draft.scope,),
                        )
                        if not materialized.findings:
                            return self._stop_attempt(
                                confirmed, attempts, evidence, handoffs,
                                revision_task, task,
                                attempt.fail("scope_insufficiency", retryable=False),
                                "scope_insufficiency",
                                "review findings exceeded the confirmed scope",
                                acp_session_id=handle.session_id, worker_handle=handle,
                            )
                        candidate_revision = AuthoritativeRevisionTask.from_review(
                            draft.tasks[2].task_id, draft.scope,
                            materialized.findings, validated.evidence,
                        )
                    if task.name == "acceptance" and not validated.accepted:
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
                        acp_session_id=handle.session_id, worker_handle=handle,
                    )
                handoff = None
                if index < len(draft.tasks) - 1:
                    handoff = Handoff.create(
                        _records.stage_id(
                            "hnd_", confirmed, task,
                            draft.tasks[index + 1].task_id, str(ordinal),
                        ),
                        terminal.attempt_id, draft.tasks[index + 1].task_id,
                        terminal.result_summary,
                        (candidate_revision.review_evidence_ids
                         if task.name == "review" else tuple(
                             item.evidence_id for item in validated.evidence)),
                        artifact_references=validated.artifact_references,
                    )
                try:
                    committed = self._persist_terminal(
                        terminal, task, validated.evidence, handoff, confirmed,
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
                self._takeover_release(attempt.attempt_id, handle)
                terminal = committed.attempt
                handoff = committed.handoff
                if task.name == "review":
                    candidate_revision = AuthoritativeRevisionTask.from_review(
                        draft.tasks[2].task_id, draft.scope, materialized.findings,
                        committed.evidence,
                    )
                attempts[-1] = terminal
                evidence.extend(committed.evidence)
                committed_evidence.extend(
                    _records.CommittedEvidence(
                        item, confirmed.mission_id, task.task_id, terminal.attempt_id,
                    ) for item in committed.evidence
                )
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
        return ExecutionResult(tuple(attempts), tuple(evidence), tuple(handoffs), revision_task)
    def _persist_started(
        self, attempt: Attempt, task: TaskDefinition,
        confirmed: ConfirmedMissionVersion,
    ) -> bool:
        created = False
        def commit(transaction):
            nonlocal created
            transaction.save_aggregate(
                "attempts", attempt.attempt_id, _authority.attempt_snapshot(attempt, task)
            )
            created = True
            return {"attempt_id": attempt.attempt_id, "state": attempt.state.value}
        self._store.execute_once(
            _records.command_id("start", confirmed, task, attempt.ordinal),
            "execution_attempt_started", commit,
        )
        return created
    def _bind_acp_session(
        self, attempt: Attempt, task: TaskDefinition,
        confirmed: ConfirmedMissionVersion, acp_session_id: str,
    ) -> Attempt:
        expected = _authority.bound_command_result(
            confirmed, task, attempt, acp_session_id
        )
        def commit(transaction):
            transaction.save_aggregate(
                "attempts", attempt.attempt_id,
                _authority.attempt_snapshot(attempt, task, acp_session_id),
            )
            return expected
        result = self._store.execute_once(
            _records.command_id("bind_acp", confirmed, task, attempt.ordinal),
            "execution_acp_session_bound", commit,
        )
        identity = _authority.bound_attempt_reference(result)
        facts = self._store.load_aggregate("attempts", identity)
        return _authority.validated_bound_attempt(
            result, confirmed, task, attempt, acp_session_id, facts
        )
    def _persist_terminal(
        self, attempt: Attempt, task: TaskDefinition,
        evidence: tuple[Evidence, ...], handoff: Handoff | None,
        confirmed: ConfirmedMissionVersion, acp_session_id: str,
    ):
        expected_result = _records.terminal_command_result(
            confirmed, task, attempt, evidence, handoff
        )
        def commit(transaction):
            transaction.save_aggregate("attempts", attempt.attempt_id,
                _authority.attempt_snapshot(attempt, task, acp_session_id))
            for item in evidence:
                transaction.save_aggregate("evidence", item.evidence_id,
                                           _records.evidence_snapshot(item, attempt))
            if handoff is not None:
                transaction.save_aggregate("handoffs", handoff.handoff_id,
                                           _records.handoff_snapshot(handoff))
            return expected_result
        result = self._store.execute_once(
            _records.command_id("terminal", confirmed, task, attempt.ordinal),
            "execution_stage_committed", commit,
        )
        attempt_id, evidence_ids, handoff_id = _records.terminal_references(result)
        attempt_facts = self._store.load_aggregate("attempts", attempt_id)
        evidence_facts = tuple(self._store.load_aggregate("evidence", identity)
                               for identity in evidence_ids)
        handoff_facts = (None if handoff_id is None else self._store.load_aggregate("handoffs", handoff_id))
        return _records.validated_terminal_bundle(
            result, confirmed, task, attempt, evidence, handoff,
            acp_session_id, attempt_facts, evidence_facts, handoff_facts)
    def _stop_attempt(
        self, confirmed, attempts, evidence, handoffs, revision_task, task,
        terminal, code, cause, *, acp_session_id=None, worker_handle=None,
        reservation=None,
    ) -> ExecutionResult:
        return stop_execution_attempt(
            runtime=self._runtime, persist=self._persist_terminal_attempt,
            diagnostic=self._diagnostic, confirmed=confirmed,
            attempts=attempts, evidence=evidence, handoffs=handoffs,
            revision_task=revision_task, task=task, terminal=terminal,
            code=code, cause=cause, acp_session_id=acp_session_id,
            worker_handle=worker_handle, reservation=reservation,
        )
    def _persist_terminal_attempt(
        self, attempt: Attempt, task: TaskDefinition,
        confirmed: ConfirmedMissionVersion, acp_session_id: str | None,
    ) -> Attempt:
        expected = _authority.stopped_command_result(
            confirmed, task, attempt, acp_session_id
        )
        def commit(transaction):
            transaction.save_aggregate("attempts", attempt.attempt_id,
                _authority.attempt_snapshot(attempt, task, acp_session_id))
            return expected
        result = self._store.execute_once(_records.command_id(
            "stop", confirmed, task, attempt.ordinal),
            "execution_attempt_stopped", commit)
        identity = _authority.stopped_attempt_reference(result)
        facts = self._store.load_aggregate("attempts", identity)
        return _authority.validated_stopped_attempt(
            result, confirmed, task, attempt, acp_session_id, facts
        )
    def _diagnostic(self, code, confirmed, task, attempt, cause):
        return execution_diagnostic(self._clock, code, confirmed, task, attempt, cause)
