from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

import pytest

from agentdeck.application.execution_runtime import ForegroundExecutionRuntime
from agentdeck.application.execution_service import ExecutionService
from agentdeck.application.project_lifecycle_service import ProjectLifecycleService
from agentdeck.kernel.mission import MissionDraft
from agentdeck.kernel.permissions import Effect, PermissionProfile, PermissionScope
from agentdeck.ports.worker import TaskRequest, WorkerEvent, WorkerHandle, WorkerResult
from product_kernel.fakes import FrozenClock, RecordingApprovalService

NOW = datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc)
class Transaction:
    def __init__(self, store: "MemoryStore") -> None:
        self._store = store
        self._pending: list[tuple[str, str, dict[str, object]]] = []
        self.duplicate_result = None

    def save_aggregate(self, kind, identity, snapshot) -> None:
        if kind == "handoffs" and self._store.fail_on_handoff_commit:
            raise RuntimeError("simulated handoff persistence failure")
        if (kind, identity) == self._store.fail_on_aggregate:
            raise RuntimeError("simulated aggregate persistence failure")
        existing_evidence = sum(key[0] == "evidence" for key in self._store.aggregates)
        pending_evidence = sum(item[0] == "evidence" for item in self._pending)
        if (
            kind == "evidence"
            and self._store.fail_on_evidence_number == existing_evidence + pending_evidence + 1
        ):
            raise RuntimeError("simulated evidence persistence failure")
        self._pending.append((kind, identity, dict(snapshot)))

    def append_event(self, event) -> None:
        return None
    def commit(self) -> None:
        for kind, identity, snapshot in self._pending:
            self._store.aggregates[(kind, identity)] = snapshot

class MemoryStore:
    def __init__(self) -> None:
        self.aggregates = {
            ("product_sessions", "ses_1"): {
                "session_id": "ses_1", "state": "running",
            },
        }
        self.commands: dict[tuple[str, str], dict[str, object]] = {}
        self.fail_on_handoff_commit = False
        self.fail_on_aggregate: tuple[str, str] | None = None
        self.fail_on_evidence_number: int | None = None

    def execute_once(self, command_id, command_kind, callback):
        key = (command_id, command_kind)
        if key in self.commands:
            return dict(self.commands[key])
        transaction = Transaction(self)
        result = callback(transaction)
        transaction.commit()
        self.commands[key] = dict(result)
        return dict(result)

    def lookup_command(self, command_id, command_kind=None):
        for (identity, kind), result in self.commands.items():
            if identity == command_id and (command_kind is None or kind == command_kind):
                return dict(result)
        return None

    def load_aggregate(self, kind, identity):
        snapshot = self.aggregates.get((kind, identity))
        return None if snapshot is None else dict(snapshot)
    def list_active_exit_attempts(self, session_id): return ()

class ScriptedWorker:
    def __init__(self, harness: "Harness", task_name: str) -> None:
        self._harness = harness
        self._task_name = task_name
        self._handle: WorkerHandle | None = None
        self._request: TaskRequest | None = None

    async def start_task(self, request: TaskRequest) -> WorkerHandle:
        assert ("attempts", request.attempt_id) in self._harness.store.aggregates
        self._harness.started_tasks.append(self._task_name)
        self._harness.requests.append(request)
        self._request = request
        self._handle = WorkerHandle(
            "ses_1", request.agent_id, request.task_id, request.attempt_id
        )
        return self._handle

    async def _events(self):
        assert self._handle is not None
        yield WorkerEvent(
            event_id=f"evt_{self._task_name}", session_id="ses_1",
            agent_id=self._handle.agent_id, task_id=self._handle.task_id,
            attempt_id=self._handle.attempt_id, transport="acp", sequence=1,
            kind="completed", timestamp=NOW.isoformat(), payload={"status": "done"},
        )

    def stream_events(self, handle):
        assert handle == self._handle
        return self._events()

    async def respond_permission(self, *args, **kwargs):
        raise AssertionError("script has no permission request")
    async def cancel_task(self, *args, **kwargs):
        raise AssertionError("script is not cancelled")

    async def collect_result(self, handle):
        assert handle == self._handle
        return WorkerResult(
            session_id="ses_1", agent_id=handle.agent_id, task_id=handle.task_id,
            attempt_id=handle.attempt_id, status="completed",
            payload=self._result_payload(),
        )

    def _result_payload(self):
        payload = self._harness.result_payload(self._task_name)
        if self._task_name == "revision":
            assert self._request is not None
            authority = json.loads(self._request.instruction)["authoritative_revision_task"]
            findings = authority["accepted_findings"]
            if payload["resolved_finding_ids"] is None:
                payload["resolved_finding_ids"] = [
                    finding["finding_id"] for finding in findings
                ]
            if payload["evidence_ids"] is None:
                payload["evidence_ids"] = [
                    finding["evidence_lineage"]["review_evidence_id"] for finding in findings
                ]
        return payload

class Harness:
    def __init__(self, *, store=None, objective="build the approved feature") -> None:
        self.store = MemoryStore() if store is None else store
        self.started_tasks: list[str] = []
        self.requests: list[TaskRequest] = []
        self.results = {
            "implementation": {
                "summary": "implementation complete", "artifact_reference": "workspace patch",
                "content_hash": "a" * 64,
            },
            "review": {"summary": "review complete", "findings": [{
                "finding_id": "rfn_1", "scope": "project", "severity": "warning",
                "summary": "verified finding", "criterion": "approved scope", "evidence_ids": None,
            }]},
            "revision": {
                "summary": "revision complete", "base": "base", "head": "head", "diff_hash": "b" * 64,
                "resolved_finding_ids": None, "evidence_ids": None,
            },
            "acceptance": {
                "summary": "accepted", "criteria": ["the objective is complete with evidence"],
                "evidence_by_criterion": {"the objective is complete with evidence": None},
                "accepted": True, "failure_reason": None,
            },
        }
        self.draft = MissionDraft.coding_default(
            "drf_1", objective, "/project", "codex-cli", "gpt-test", PermissionProfile.APPROVE_FOR_ME,
        )
        preview = self.draft.preview(1)
        self.confirmed = preview.confirm(preview_id=preview.preview_id, content_hash=preview.content_hash)
        clock = FrozenClock(NOW)
        self.approvals = RecordingApprovalService(store=self.store, clock=clock)
        self.runtime = ForegroundExecutionRuntime()
        self.lifecycle = ProjectLifecycleService(
            store=self.store, clock=clock, session_id="ses_1")
        self.service = ExecutionService(store=self.store, clock=clock, approval_service=self.approvals,
            worker_factory=lambda task: ScriptedWorker(self, task.name), runtime=self.runtime,
            lifecycle=self.lifecycle)
    def result_payload(self, name):
        payload = json.loads(json.dumps(self.results[name]))
        task_ids = {task.name: task.task_id for task in self.draft.tasks}
        attempts = {request.attempt_id for request in self.requests}
        prior = [identity for (kind, identity), facts in self.store.aggregates.items()
                 if kind == "evidence" and facts["task_id"] == task_ids.get(
                     {"review": "implementation", "revision": "review",
                      "acceptance": "revision"}.get(name))
                 and facts["attempt_id"] in attempts]
        if name == "review":
            for finding in payload["findings"]:
                if finding["evidence_ids"] is None:
                    finding["evidence_ids"] = prior
        elif name == "acceptance":
            payload["evidence_by_criterion"] = {criterion: prior if ids is None else ids
                                                for criterion, ids in payload["evidence_by_criterion"].items()}
        return payload
    async def run(self):
        return await self.service.run_confirmed_mission(
            session_id="ses_1", confirmed=self.confirmed, draft=self.draft,
            permission_scope=PermissionScope.for_profile(PermissionProfile.APPROVE_FOR_ME),
        )

def test_coordinator_runs_only_the_frozen_four_stage_graph() -> None:
    harness = Harness()
    result = asyncio.run(harness.run())

    assert harness.started_tasks == [
        "implementation", "review", "revision", "acceptance"
    ]
    assert [item.source_attempt_id for item in result.handoffs] == [
        attempt.attempt_id for attempt in result.attempts[:3]
    ]
    assert result.diagnostic is None

def test_worker_cannot_directly_dispatch_peer() -> None:
    harness = Harness()
    harness.results["review"]["next_agent_command"] = "dispatch codex"

    result = asyncio.run(harness.run())

    assert "next_agent_command" not in result.revision_task.canonical_payload()
    assert result.diagnostic.code == "worker_result_invalid"

def test_each_attempt_permission_scope_is_narrowed_to_the_frozen_task() -> None:
    harness = Harness()

    asyncio.run(harness.service.run_confirmed_mission(
        session_id="ses_1", confirmed=harness.confirmed, draft=harness.draft,
        permission_scope=PermissionScope(
            PermissionProfile.APPROVE_FOR_ME, frozenset({Effect.READ})
        ),
    ))

    assert [scope.effects for scope in harness.approvals.scopes] == [
        frozenset({Effect.READ}) for _ in harness.draft.tasks
    ]

def test_dependency_without_committed_handoff_never_starts() -> None:
    harness = Harness()
    harness.store.fail_on_handoff_commit = True

    result = asyncio.run(harness.run())

    assert result.diagnostic is not None
    assert result.diagnostic.code == "stage_bundle_persistence_failed"
    assert harness.started_tasks == ["implementation"]

def test_confirmed_permission_profile_cannot_be_replaced_by_caller() -> None:
    harness = Harness()
    harness.draft = MissionDraft.coding_default(
        "drf_1", "build", "/project", "codex-cli", "gpt-test",
        PermissionProfile.ASK_FOR_APPROVAL,
    )
    preview = harness.draft.preview(1)
    harness.confirmed = preview.confirm(
        preview_id=preview.preview_id, content_hash=preview.content_hash
    )

    with pytest.raises(ValueError, match="permission profile"):
        asyncio.run(harness.service.run_confirmed_mission(
            session_id="ses_1", confirmed=harness.confirmed, draft=harness.draft,
            permission_scope=PermissionScope.for_profile(PermissionProfile.FULL_ACCESS),
        ))

    assert harness.started_tasks == []

class DeniedButCompletedWorker(ScriptedWorker):
    async def _events(self):
        assert self._handle is not None
        yield WorkerEvent(
            event_id="evt_permission", session_id=self._handle.session_id,
            agent_id=self._handle.agent_id, task_id=self._handle.task_id,
            attempt_id=self._handle.attempt_id, transport="acp", sequence=1,
            kind="permission_requested", timestamp=NOW.isoformat(),
            payload={
                "permission_request_id": "perm_1", "effect": "network",
                "risk": "bounded risk",
            },
        )
        yield WorkerEvent(
            event_id="evt_hostile_complete", session_id=self._handle.session_id,
            agent_id=self._handle.agent_id, task_id=self._handle.task_id,
            attempt_id=self._handle.attempt_id, transport="acp", sequence=2,
            kind="completed", timestamp=NOW.isoformat(), payload={"status": "done"},
        )

    async def respond_permission(
        self, handle, *, permission_request_id, allowed, reason,
    ) -> None:
        assert allowed is False

    async def cancel_task(self, handle, *, reason) -> None:
        return None

def test_denied_permission_stops_even_if_worker_claims_completed() -> None:
    harness = Harness()
    harness.service._worker_factory = lambda task: DeniedButCompletedWorker(
        harness, task.name
    )

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "permission_denied"
    assert result.diagnostic.outcome_known is False
    assert all(
        "retry" not in action.lower()
        for action in result.diagnostic.recovery_actions
    )
    assert harness.started_tasks == ["implementation"]
    assert result.handoffs == ()
    attempt_id = result.attempts[0].attempt_id
    assert harness.store.aggregates[("attempts", attempt_id)]["state"] == "outcome_unknown"

class DriftHandleWorker(ScriptedWorker):
    async def start_task(self, request: TaskRequest) -> WorkerHandle:
        self._harness.started_tasks.append(self._task_name)
        self._harness.requests.append(request)
        self._request = request
        self._handle = WorkerHandle("ses_drift", "agt_drift", "tsk_drift", "att_drift")
        return self._handle

    async def _events(self):
        assert self._handle is not None
        yield WorkerEvent(
            event_id="evt_drift", session_id=self._handle.session_id,
            agent_id=self._handle.agent_id, task_id=self._handle.task_id,
            attempt_id=self._handle.attempt_id, transport="acp", sequence=1,
            kind="completed", timestamp=NOW.isoformat(), payload={"status": "done"},
        )

    async def collect_result(self, handle):
        return WorkerResult(
            session_id=handle.session_id, agent_id=handle.agent_id,
            task_id=handle.task_id, attempt_id=handle.attempt_id, status="completed",
            payload=self._result_payload(),
        )

def test_worker_handle_must_match_exact_request_lineage() -> None:
    harness = Harness()
    harness.service._worker_factory = lambda task: DriftHandleWorker(harness, task.name)

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "worker_handle_lineage_invalid"
    assert result.diagnostic.outcome_known is False
    assert harness.started_tasks == ["implementation"]
    attempt_id = result.attempts[0].attempt_id
    assert harness.store.aggregates[("attempts", attempt_id)]["state"] == "outcome_unknown"

class IndependentACPSessionWorker(ScriptedWorker):
    async def start_task(self, request: TaskRequest) -> WorkerHandle:
        assert ("attempts", request.attempt_id) in self._harness.store.aggregates
        self._harness.started_tasks.append(self._task_name)
        self._harness.requests.append(request)
        self._request = request
        self._handle = WorkerHandle(
            f"ses_acp_{self._task_name}", request.agent_id,
            request.task_id, request.attempt_id,
        )
        return self._handle

    async def _events(self):
        assert self._handle is not None
        yield WorkerEvent(
            event_id=f"evt_{self._task_name}", session_id=self._handle.session_id,
            agent_id=self._handle.agent_id, task_id=self._handle.task_id,
            attempt_id=self._handle.attempt_id, transport="acp", sequence=1,
            kind="completed", timestamp=NOW.isoformat(), payload={"status": "done"},
        )

    async def collect_result(self, handle):
        return WorkerResult(
            session_id=handle.session_id, agent_id=handle.agent_id,
            task_id=handle.task_id, attempt_id=handle.attempt_id, status="completed",
            payload=self._result_payload(),
        )

def test_acp_session_identity_remains_distinct_from_product_session() -> None:
    harness = Harness()
    harness.service._worker_factory = lambda task: IndependentACPSessionWorker(
        harness, task.name
    )

    result = asyncio.run(harness.run())

    assert result.diagnostic is None
    assert harness.started_tasks == [
        "implementation", "review", "revision", "acceptance"
    ]
    for attempt, request in zip(result.attempts, harness.requests, strict=True):
        assert harness.store.aggregates[("attempts", attempt.attempt_id)][
            "acp_session_id"
        ] == f"ses_acp_{json.loads(request.instruction)['task']['name']}"

class FailedWorker(ScriptedWorker):
    async def _events(self):
        assert self._handle is not None
        yield WorkerEvent(
            event_id="evt_failed", session_id=self._handle.session_id,
            agent_id=self._handle.agent_id, task_id=self._handle.task_id,
            attempt_id=self._handle.attempt_id, transport="acp", sequence=1,
            kind="failed", timestamp=NOW.isoformat(), payload={"reason": "failed"},
        )

    async def collect_result(self, handle):
        return WorkerResult(
            session_id=handle.session_id, agent_id=handle.agent_id,
            task_id=handle.task_id, attempt_id=handle.attempt_id, status="failed",
            payload={"summary": "failed"},
        )

def test_worker_failure_is_durable_before_return() -> None:
    harness = Harness()
    harness.service._worker_factory = lambda task: FailedWorker(harness, task.name)

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "worker_stage_failed"
    assert result.attempts[0].state.value == "failed"
    attempt_id = result.attempts[0].attempt_id
    assert harness.store.aggregates[("attempts", attempt_id)]["state"] == "failed"

def test_invalid_result_schema_is_durable_and_stops_downstream() -> None:
    harness = Harness()
    del harness.results["implementation"]["content_hash"]

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "worker_result_invalid"
    assert result.diagnostic.outcome_known is False
    assert harness.started_tasks == ["implementation"]
    attempt_id = result.attempts[0].attempt_id
    assert harness.store.aggregates[("attempts", attempt_id)]["state"] == "outcome_unknown"

def test_replaying_confirmed_mission_never_repeats_worker_io() -> None:
    harness = Harness()
    first = asyncio.run(harness.run())

    second = asyncio.run(harness.run())

    assert first.diagnostic is None
    assert second.diagnostic.code == "mission_execution_replayed"
    assert harness.started_tasks == [
        "implementation", "review", "revision", "acceptance"
    ]
    other = Harness(store=harness.store, objective="build another approved feature")
    other_result = asyncio.run(other.run())
    assert other_result.diagnostic is None
    assert {item.attempt_id for item in first.attempts}.isdisjoint(
        item.attempt_id for item in other_result.attempts
    )

def test_invalid_complete_task_request_never_persists_running_attempt() -> None:
    harness = Harness(objective="x" * 70_000)

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "task_request_invalid"
    assert result.diagnostic.outcome_known is True
    assert result.attempts == ()
    assert not any(kind == "attempts" for kind, _ in harness.store.aggregates)
    assert harness.started_tasks == []

def test_worker_start_failure_is_durable_and_stops_before_worker_io() -> None:
    harness = Harness()

    def fail_start(task):
        raise RuntimeError("raw start failure")

    harness.service._worker_factory = fail_start
    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "worker_start_failed"
    assert harness.started_tasks == []
    attempt_id = result.attempts[0].attempt_id
    assert harness.store.aggregates[("attempts", attempt_id)]["state"] == "failed"

class BridgeFailureWorker(ScriptedWorker):
    async def _events(self):
        raise RuntimeError("raw bridge failure")
        yield

def test_worker_bridge_failure_is_durable_and_stops_downstream() -> None:
    harness = Harness()
    harness.service._worker_factory = lambda task: BridgeFailureWorker(harness, task.name)

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "worker_bridge_failed"
    assert result.diagnostic.outcome_known is False
    assert harness.started_tasks == ["implementation"]
    attempt_id = result.attempts[0].attempt_id
    assert harness.store.aggregates[("attempts", attempt_id)]["state"] == "outcome_unknown"

def test_acceptance_bundle_failure_has_precise_non_handoff_diagnostic() -> None:
    harness = Harness()
    harness.store.fail_on_evidence_number = 4

    result = asyncio.run(harness.run())

    assert result.diagnostic.code == "stage_bundle_persistence_failed"
    assert harness.started_tasks == [
        "implementation", "review", "revision", "acceptance"
    ]
    attempt_id = result.attempts[-1].attempt_id
    assert harness.store.aggregates[("attempts", attempt_id)]["state"] == "running"
