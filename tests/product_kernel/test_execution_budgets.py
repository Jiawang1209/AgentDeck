from __future__ import annotations

import asyncio

import pytest

from agentdeck.application.leader_service import LeaderService
from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.execution import AttemptState, ResultError
from agentdeck.kernel.execution_semantics import RetryPolicy
from agentdeck.ports.leader import LeaderFailure, LeaderFailureCode
from agentdeck.ports.worker import TaskRequest, WorkerEvent, WorkerHandle, WorkerResult
from product_kernel.test_execution_coordinator import Harness, NOW, ScriptedWorker
from product_kernel.test_leader_contract import request, valid_proposal


@pytest.mark.parametrize(("condition", "retry"), [
    ("transport_before_effect", True),
    ("worker_schema_invalid", True),
    ("known_test_failure", False),
    ("permission_denied", False),
    ("outcome_unknown", False),
    ("project_drift", False),
    ("scope_insufficiency", False),
    ("login_loss", False),
])
def test_retry_policy_is_bounded_and_semantic(condition: str, retry: bool) -> None:
    assert RetryPolicy.default().decision(condition, ordinal=1).retry is retry
    assert RetryPolicy.default().decision(condition, ordinal=2).retry is False


@pytest.mark.parametrize("ordinal", [True, 0, 2**63])
def test_retry_policy_rejects_non_sqlite_safe_ordinals(ordinal: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        RetryPolicy.default().decision("worker_schema_invalid", ordinal=ordinal)


def test_retry_policy_rejects_an_unknown_condition() -> None:
    with pytest.raises(ResultError, match="retry condition is not declared"):
        RetryPolicy.default().decision("mystery", ordinal=1)


class FakeLeader:
    def __init__(self) -> None:
        invalid = valid_proposal()
        invalid.pop("tasks")
        self.results = [invalid, invalid, valid_proposal()]
        self.calls = 0

    def propose_mission(self, leader_request):
        result = self.results[self.calls]
        self.calls += 1
        return result


def test_leader_schema_repair_remains_capped_at_one() -> None:
    leader = FakeLeader()

    with pytest.raises(LeaderFailure) as raised:
        LeaderService(leader).propose(request())

    assert raised.value.code is LeaderFailureCode.SCHEMA
    assert leader.calls == 2


class SafeWorkerFailure(RuntimeError):
    def __init__(
        self, code: str, *, outcome_known: bool, retryable: bool,
        task_id: str = "tsk_test", attempt_id: str = "att_test",
    ) -> None:
        self.diagnostic = Diagnostic.create(
            code=code, stage="worker", severity=Severity.ERROR,
            actor="agentdeck", summary="worker stopped", cause="typed failure",
            impact="the attempt stopped", protection="authority stayed closed",
            recovery_actions=("inspect the durable attempt",),
            retryable=retryable, outcome_known=outcome_known,
            occurred_at=NOW.isoformat(), mission_id="msn_test",
            task_id=task_id, attempt_id=attempt_id,
        )
        super().__init__(code)


class SchemaWorker(ScriptedWorker):
    async def start_task(self, request: TaskRequest) -> WorkerHandle:
        if self._task_name != "acceptance":
            count = self._harness.schema_calls.get(self._task_name, 0) + 1
            self._harness.schema_calls[self._task_name] = count
            if count <= self._harness.invalid_counts.get(self._task_name, 0):
                self._harness.started_tasks.append(self._task_name)
                raise SafeWorkerFailure(
                    "worker_schema_invalid", outcome_known=True, retryable=True,
                    task_id=request.task_id, attempt_id=request.attempt_id,
                )
        return await super().start_task(request)

    async def collect_result(self, handle: WorkerHandle) -> WorkerResult:
        payload = dict(self._harness.results[self._task_name])
        if self._task_name == "acceptance":
            count = self._harness.schema_calls.get(self._task_name, 0) + 1
            self._harness.schema_calls[self._task_name] = count
            if count <= self._harness.invalid_counts.get(self._task_name, 0):
                payload["evidence_by_criterion"] = {}
        return WorkerResult(
            session_id=handle.session_id, agent_id=handle.agent_id,
            task_id=handle.task_id, attempt_id=handle.attempt_id,
            status="completed", payload=payload,
        )


def schema_harness(**invalid_counts: int) -> Harness:
    harness = Harness()
    harness.schema_calls = {}
    harness.invalid_counts = invalid_counts
    harness.service._worker_factory = lambda task: SchemaWorker(harness, task.name)
    return harness


def test_worker_schema_invalid_retries_once_with_a_new_attempt() -> None:
    harness = schema_harness(implementation=1)

    result = asyncio.run(harness.run())

    assert result.diagnostic is None
    assert harness.started_tasks[:3] == ["implementation", "implementation", "review"]
    first, second = result.attempts[:2]
    assert (first.state, first.reason, first.retryable) == (
        AttemptState.FAILED, "worker_schema_invalid", True,
    )
    assert second.state is AttemptState.COMPLETED
    assert (first.ordinal, second.ordinal) == (1, 2)
    assert first.attempt_id != second.attempt_id


def test_two_worker_schema_failures_exhaust_the_attempt_budget() -> None:
    harness = schema_harness(implementation=3)

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "worker_schema_invalid"
    assert harness.started_tasks == ["implementation", "implementation"]
    assert [attempt.ordinal for attempt in result.attempts] == [1, 2]
    assert all(attempt.state is AttemptState.FAILED for attempt in result.attempts)


def test_acceptance_has_exactly_one_attempt_even_when_schema_is_invalid() -> None:
    harness = schema_harness(acceptance=2)

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "acceptance_evidence_missing"
    assert harness.started_tasks.count("acceptance") == 1
    assert result.attempts[-1].ordinal == 1
    assert result.attempts[-1].retryable is False


def test_failed_acceptance_persists_typed_evidence_and_never_retries() -> None:
    harness = Harness()
    harness.results["acceptance"].update(
        accepted=False, failure_reason="criterion failed",
    )

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "acceptance_failed"
    assert harness.started_tasks.count("acceptance") == 1
    assert result.attempts[-1].state is AttemptState.FAILED
    assert result.evidence[-1].kind.value == "acceptance_result"


class TransportWorker(ScriptedWorker):
    async def start_task(self, task_request: TaskRequest) -> WorkerHandle:
        self._harness.transport_calls += 1
        if self._harness.transport_calls <= self._harness.transport_failures:
            self._harness.started_tasks.append(self._task_name)
            raise SafeWorkerFailure(
                "acp_disconnected_before_effect", outcome_known=True, retryable=True,
                task_id=task_request.task_id, attempt_id=task_request.attempt_id,
            )
        return await super().start_task(task_request)


class ForgedSchemaDiagnosticWorker(ScriptedWorker):
    async def start_task(self, task_request: TaskRequest) -> WorkerHandle:
        self._harness.started_tasks.append(self._task_name)
        raise SafeWorkerFailure(
            "worker_schema_invalid", outcome_known=True, retryable=True,
        )


class StartUnknownOutcomeWorker(ScriptedWorker):
    async def start_task(self, task_request: TaskRequest) -> WorkerHandle:
        self._harness.started_tasks.append(self._task_name)
        raise SafeWorkerFailure(
            "worker_outcome_unknown", outcome_known=False, retryable=False,
            task_id=task_request.task_id, attempt_id=task_request.attempt_id,
        )


def transport_harness(failures: int) -> Harness:
    harness = Harness()
    harness.transport_calls = 0
    harness.transport_failures = failures
    harness.service._worker_factory = lambda task: TransportWorker(harness, task.name)
    return harness


def test_pre_effect_transport_loss_uses_one_reconnect_budget() -> None:
    harness = transport_harness(1)

    result = asyncio.run(harness.run())

    assert result.diagnostic is None
    assert harness.started_tasks[:2] == ["implementation", "implementation"]
    assert [attempt.ordinal for attempt in result.attempts[:2]] == [1, 2]


def test_second_pre_effect_transport_loss_does_not_reconnect_again() -> None:
    harness = transport_harness(3)

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "transport_before_effect"
    assert harness.transport_calls == 2
    assert harness.started_tasks == ["implementation", "implementation"]
    assert [attempt.ordinal for attempt in result.attempts] == [1, 2]


def test_forged_failure_lineage_cannot_authorize_retry() -> None:
    harness = Harness()
    harness.service._worker_factory = lambda task: ForgedSchemaDiagnosticWorker(
        harness, task.name
    )

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "worker_start_failed"
    assert harness.started_tasks == ["implementation"]
    assert len(result.attempts) == 1


def test_start_unknown_outcome_is_never_downgraded_to_known_failure() -> None:
    harness = Harness()
    harness.service._worker_factory = lambda task: StartUnknownOutcomeWorker(
        harness, task.name
    )

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "outcome_unknown"
    assert result.diagnostic.outcome_known is False
    assert result.attempts[0].state is AttemptState.OUTCOME_UNKNOWN
    assert harness.started_tasks == ["implementation"]


class SemanticFailureWorker(ScriptedWorker):
    async def _events(self):
        assert self._handle is not None
        yield WorkerEvent(
            event_id="evt_failed", session_id=self._handle.session_id,
            agent_id=self._handle.agent_id, task_id=self._handle.task_id,
            attempt_id=self._handle.attempt_id, transport="acp", sequence=1,
            kind="failed", timestamp=NOW.isoformat(), payload={"reason": "stopped"},
        )

    async def collect_result(self, handle: WorkerHandle) -> WorkerResult:
        return WorkerResult(
            session_id=handle.session_id, agent_id=handle.agent_id,
            task_id=handle.task_id, attempt_id=handle.attempt_id, status="failed",
            payload={"summary": "stage failed", "stop_reason": self._harness.stop_reason},
        )


@pytest.mark.parametrize("condition", [
    "known_test_failure", "scope_insufficiency", "login_loss", "project_drift",
])
def test_semantic_attention_conditions_never_blind_retry(condition: str) -> None:
    harness = Harness()
    harness.stop_reason = condition
    harness.service._worker_factory = lambda task: SemanticFailureWorker(
        harness, task.name
    )

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == condition
    assert harness.started_tasks == ["implementation"]
    assert len(result.attempts) == 1
    assert result.attempts[0].retryable is False


class UnknownOutcomeWorker(ScriptedWorker):
    async def _events(self):
        assert self._handle is not None
        raise SafeWorkerFailure(
            "worker_outcome_unknown", outcome_known=False, retryable=False,
            task_id=self._handle.task_id, attempt_id=self._handle.attempt_id,
        )
        yield


def test_unknown_outcome_never_retries() -> None:
    harness = Harness()
    harness.service._worker_factory = lambda task: UnknownOutcomeWorker(
        harness, task.name
    )

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "outcome_unknown"
    assert harness.started_tasks == ["implementation"]
    assert len(result.attempts) == 1
    assert result.attempts[0].state is AttemptState.OUTCOME_UNKNOWN


def test_revision_cycle_is_hard_capped_at_one() -> None:
    harness = Harness()
    budgets = dict(harness.draft.budgets)
    budgets["max_revision_cycles"] = 3
    harness.draft = harness.draft.revise(budgets=budgets)
    preview = harness.draft.preview(1)
    harness.confirmed = preview.confirm(
        preview_id=preview.preview_id, content_hash=preview.content_hash,
    )

    result = asyncio.run(harness.run())

    assert result.diagnostic is None
    assert harness.started_tasks.count("revision") == 1
