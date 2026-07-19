from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agentdeck.application.approval_service import ApprovalService
from agentdeck.application.execution_service import ExecutionService
from agentdeck.kernel.mission import MissionDraft
from agentdeck.kernel.permissions import PermissionProfile, PermissionScope
from agentdeck.ports.worker import TaskRequest, WorkerEvent, WorkerHandle, WorkerResult
from product_kernel.fakes import FrozenClock


NOW = datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc)


class Transaction:
    def __init__(self, store: "MemoryStore") -> None:
        self._store = store
        self._pending: list[tuple[str, str, dict[str, object]]] = []
        self.duplicate_result = None

    def save_aggregate(self, kind, identity, snapshot) -> None:
        if kind == "handoffs" and self._store.fail_on_handoff_commit:
            raise RuntimeError("simulated handoff persistence failure")
        self._pending.append((kind, identity, dict(snapshot)))

    def append_event(self, event) -> None:
        return None

    def commit(self) -> None:
        for kind, identity, snapshot in self._pending:
            self._store.aggregates[(kind, identity)] = snapshot


class MemoryStore:
    def __init__(self) -> None:
        self.aggregates: dict[tuple[str, str], dict[str, object]] = {}
        self.commands: dict[tuple[str, str], dict[str, object]] = {}
        self.fail_on_handoff_commit = False

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


class RecordingApprovalService(ApprovalService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.scopes = []

    async def bridge_attempt(self, worker, handle, context):
        self.scopes.append(context.permission_scope)
        return await super().bridge_attempt(worker, handle, context)


class ScriptedWorker:
    def __init__(self, harness: "Harness", task_name: str) -> None:
        self._harness = harness
        self._task_name = task_name
        self._handle: WorkerHandle | None = None

    async def start_task(self, request: TaskRequest) -> WorkerHandle:
        assert ("attempts", request.attempt_id) in self._harness.store.aggregates
        self._harness.started_tasks.append(self._task_name)
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
            payload=self._harness.results[self._task_name],
        )


class Harness:
    def __init__(self) -> None:
        self.store = MemoryStore()
        self.started_tasks: list[str] = []
        self.results = {
            "implementation": {
                "summary": "implementation complete",
                "artifact_reference": "workspace patch",
                "content_hash": "a" * 64,
            },
            "review": {
                "summary": "review complete", "finding_id": "rfn_1",
                "scope": "project", "severity": "warning",
                "criterion": "approved scope",
                "evidence_ids": ["ev_implementation_1"],
            },
            "revision": {
                "summary": "revision complete", "base": "base",
                "head": "head", "diff_hash": "b" * 64,
            },
            "acceptance": {
                "summary": "accepted",
                "criteria": ["the objective is complete with evidence"],
                "evidence_by_criterion": {
                    "the objective is complete with evidence": ["ev_revision_1"]
                },
                "accepted": True, "failure_reason": None,
            },
        }
        self.draft = MissionDraft.coding_default(
            "drf_1", "build the approved feature", "/project", "codex-cli",
            "gpt-test", PermissionProfile.APPROVE_FOR_ME,
        )
        preview = self.draft.preview(1)
        self.confirmed = preview.confirm(
            preview_id=preview.preview_id, content_hash=preview.content_hash
        )
        clock = FrozenClock(NOW)
        self.approvals = RecordingApprovalService(store=self.store, clock=clock)
        self.service = ExecutionService(
            store=self.store, clock=clock,
            approval_service=self.approvals,
            worker_factory=lambda task: ScriptedWorker(self, task.name),
        )

    async def run(self):
        return await self.service.run_confirmed_mission(
            session_id="ses_1", confirmed=self.confirmed, draft=self.draft,
            permission_scope=PermissionScope.for_profile(
                PermissionProfile.APPROVE_FOR_ME
            ),
        )


def test_coordinator_runs_only_the_frozen_four_stage_graph() -> None:
    harness = Harness()
    result = asyncio.run(harness.run())

    assert harness.started_tasks == [
        "implementation", "review", "revision", "acceptance"
    ]
    assert [item.source_attempt_id for item in result.handoffs] == [
        "att_impl_1", "att_review_1", "att_revision_1"
    ]
    assert result.diagnostic is None


def test_worker_cannot_directly_dispatch_peer() -> None:
    harness = Harness()
    harness.results["review"]["next_agent_command"] = "dispatch codex"

    result = asyncio.run(harness.run())

    assert "next_agent_command" not in result.revision_task.canonical_payload()
    assert result.revision_task.created_by == "agentdeck"


def test_each_attempt_permission_scope_is_narrowed_to_the_frozen_task() -> None:
    harness = Harness()

    asyncio.run(harness.run())

    assert [scope.effects for scope in harness.approvals.scopes] == [
        task.allowed_effects for task in harness.draft.tasks
    ]


def test_dependency_without_committed_handoff_never_starts() -> None:
    harness = Harness()
    harness.store.fail_on_handoff_commit = True

    result = asyncio.run(harness.run())

    assert result.diagnostic is not None
    assert result.diagnostic.code == "handoff_persistence_failed"
    assert harness.started_tasks == ["implementation"]
