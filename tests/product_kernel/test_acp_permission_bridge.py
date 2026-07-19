from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agentdeck.application.approval_service import ApprovalContext, ApprovalService
from agentdeck.kernel.permissions import PermissionProfile, PermissionScope
from agentdeck.ports.approval import ReviewerVerdict
from agentdeck.ports.worker import TaskRequest, WorkerEvent, WorkerHandle, WorkerResult
from product_kernel.fakes import FrozenClock
from product_kernel.test_approval_service import FakeReviewer, FakeStore


NOW = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)
EFFECTS = ("read", "write_project", "command_project")


class SequentialPermissionWorker:
    def __init__(self, effects=EFFECTS) -> None:
        self.effects = tuple(effects)
        self.handle = None
        self.responses = []
        self.started_tasks = []
        self._cursor = 0
        self._pending = None
        self._terminal = False
        self._cancel_requested = False
        self.event_agent_id = "agt_executor"

    async def start_task(self, request: TaskRequest) -> WorkerHandle:
        self.started_tasks.append(request.task_id)
        self.handle = WorkerHandle("ses_1", request.agent_id, request.task_id, request.attempt_id)
        return self.handle

    def stream_events(self, handle):
        assert handle == self.handle
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._pending is not None:
            raise AssertionError("bridge advanced before exact permission response")
        if self._cancel_requested:
            if self._terminal:
                raise StopAsyncIteration
            self._terminal = True
            self._cursor += 1
            return self._event("cancelled", {"reason": "permission denied"})
        if self._cursor == 0:
            self._cursor += 1
            return self._event("started", {})
        if self._cursor <= len(self.effects):
            index = self._cursor
            self._cursor += 1
            self._pending = f"perm_{index}"
            return self._event("permission_requested", {
                "permission_request_id": self._pending,
                "tool_call_id": f"call_{index}", "option_count": 2,
                "effect": self.effects[index - 1], "risk": "bounded risk",
            })
        if not self._terminal:
            self._terminal = True
            self._cursor += 1
            return self._event("completed", {"summary": "done"})
        raise StopAsyncIteration

    async def respond_permission(
        self, handle, *, permission_request_id, allowed, reason,
    ) -> None:
        assert handle == self.handle
        assert permission_request_id == self._pending
        self.responses.append((permission_request_id, allowed, reason))
        self._pending = None

    async def cancel_task(self, handle, *, reason):
        assert handle == self.handle
        self._pending = None
        self._cancel_requested = True

    async def collect_result(self, handle):
        assert handle == self.handle and self._terminal
        status = "cancelled" if self._cancel_requested else "completed"
        return WorkerResult(
            session_id=handle.session_id, agent_id=handle.agent_id,
            task_id=handle.task_id, attempt_id=handle.attempt_id,
            status=status, payload={"summary": "done"},
        )

    def _event(self, kind, payload):
        return WorkerEvent(
            event_id=f"evt_{self._cursor}", session_id="ses_1",
            agent_id=self.event_agent_id, task_id="tsk_implementation",
            attempt_id="att_1", transport="acp", sequence=self._cursor,
            kind=kind, timestamp=NOW.isoformat(), payload=payload,
        )


def test_one_attempt_handles_multiple_permissions_strictly_in_order() -> None:
    async def scenario() -> None:
        worker = SequentialPermissionWorker()
        handle = await worker.start_task(TaskRequest(
            agent_id="agt_executor", task_id="tsk_implementation",
            attempt_id="att_1", instruction="Implement the confirmed task.",
        ))
        service = ApprovalService(store=FakeStore(), clock=FrozenClock(NOW))
        context = ApprovalContext(
            mission_id="msn_1", mission_version=1,
            permission_scope=PermissionScope.for_profile(
                PermissionProfile.APPROVE_FOR_ME
            ), scope_hash="a" * 64,
        )

        result = await service.bridge_attempt(worker, handle, context)

        assert [item.request.effect.value for item in result.approvals] == list(EFFECTS)
        assert [item.request.permission_request_id for item in result.approvals] == [
            "perm_1", "perm_2", "perm_3",
        ]
        assert all(item.request.attempt_id == "att_1" for item in result.approvals)
        assert worker.responses == [
            ("perm_1", True, "routine_project_effect"),
            ("perm_2", True, "routine_project_effect"),
            ("perm_3", True, "routine_project_effect"),
        ]
        assert result.worker_result.status == "completed"
        assert result.terminal_result_validated is True
        assert result.handoff_committed is False
        assert result.next_task_allowed is False
        assert worker.started_tasks == ["tsk_implementation"]

    asyncio.run(scenario())


def test_permission_bridge_rejects_wrong_event_lineage_without_response() -> None:
    async def scenario() -> None:
        worker = SequentialPermissionWorker()
        handle = await worker.start_task(TaskRequest(
            agent_id="agt_executor", task_id="tsk_implementation",
            attempt_id="att_1", instruction="Implement the confirmed task.",
        ))
        worker.event_agent_id = "agt_other"
        service = ApprovalService(store=FakeStore(), clock=FrozenClock(NOW))
        context = ApprovalContext(
            mission_id="msn_1", mission_version=1,
            permission_scope=PermissionScope.for_profile(), scope_hash="a" * 64,
        )

        try:
            await service.bridge_attempt(worker, handle, context)
        except ValueError as error:
            assert str(error) == "worker handle or event lineage is invalid"
        else:
            raise AssertionError("wrong lineage was accepted")
        assert worker.responses == []

    asyncio.run(scenario())


def test_executor_cannot_review_itself_and_attempt_stops() -> None:
    async def scenario() -> None:
        worker = SequentialPermissionWorker(("network",))
        handle = await worker.start_task(TaskRequest(
            agent_id="agt_executor", task_id="tsk_implementation",
            attempt_id="att_1", instruction="Implement the confirmed task.",
        ))
        reviewer = FakeReviewer("agt_executor", allowed=True)
        service = ApprovalService(
            store=FakeStore(), clock=FrozenClock(NOW),
            independent_reviewer=reviewer,
        )
        context = ApprovalContext(
            mission_id="msn_1", mission_version=1,
            permission_scope=PermissionScope.for_profile(), scope_hash="a" * 64,
        )

        result = await service.bridge_attempt(worker, handle, context)

        assert reviewer.calls == []
        assert result.worker_result.status == "cancelled"
        assert result.diagnostic.code == "approval_reviewer_not_independent"
        assert result.next_task_allowed is False
        assert worker.started_tasks == ["tsk_implementation"]

    asyncio.run(scenario())


def test_concurrent_permission_replay_uses_one_durable_reviewer_outcome() -> None:
    class ConcurrentReviewer:
        reviewer_id = "human"

        def __init__(self) -> None:
            self.calls = 0

        async def review(self, request):
            self.calls += 1
            ordinal = self.calls
            await asyncio.sleep(0)
            return ReviewerVerdict(ordinal == 1, f"decision-{ordinal}")

    async def scenario() -> None:
        store = FakeStore()
        reviewer = ConcurrentReviewer()
        service = ApprovalService(
            store=store, clock=FrozenClock(NOW), human_reviewer=reviewer
        )
        workers = [SequentialPermissionWorker(), SequentialPermissionWorker()]
        replay_handle = WorkerHandle(
            "ses_1", "agt_executor", "tsk_implementation", "att_1"
        )
        for worker in workers:
            worker.handle = replay_handle
            worker._pending = "perm_1"
        workers[0]._cursor = 1
        permission_event = workers[0]._event("permission_requested", {
            "permission_request_id": "perm_1", "tool_call_id": "call_1",
            "option_count": 2, "effect": "write_project", "risk": "bounded risk",
        })
        records = await asyncio.gather(*(
            service.handle_permission(
                worker,
                replay_handle,
                permission_event,
                ApprovalContext(
                    "msn_1", 1,
                    PermissionScope.for_profile(PermissionProfile.ASK_FOR_APPROVAL),
                    "a" * 64,
                ),
            )
            for worker in workers
        ))

        assert reviewer.calls == 1
        assert records[0] == records[1]
        assert workers[0].responses == workers[1].responses

    asyncio.run(scenario())


def test_services_sharing_one_store_share_the_reviewer_decision_lock() -> None:
    class SharedReviewer:
        reviewer_id = "human"

        def __init__(self) -> None:
            self.calls = 0

        async def review(self, request):
            self.calls += 1
            await asyncio.sleep(0)
            return ReviewerVerdict(True, "shared durable decision")

    async def scenario() -> None:
        store = FakeStore()
        reviewer = SharedReviewer()
        services = [
            ApprovalService(
                store=store, clock=FrozenClock(NOW), human_reviewer=reviewer
            )
            for _ in range(2)
        ]
        handle = WorkerHandle("ses_1", "agt_executor", "tsk_implementation", "att_1")
        workers = [SequentialPermissionWorker(), SequentialPermissionWorker()]
        for worker in workers:
            worker.handle = handle
            worker._pending = "perm_1"
        permission_event = WorkerEvent(
            event_id="evt_permission", session_id="ses_1", agent_id="agt_executor",
            task_id="tsk_implementation", attempt_id="att_1", transport="acp",
            sequence=1, kind="permission_requested", timestamp=NOW.isoformat(),
            payload={
                "permission_request_id": "perm_1", "tool_call_id": "call_1",
                "option_count": 2, "effect": "write_project", "risk": "bounded risk",
            },
        )
        approval_context = ApprovalContext(
            "msn_1", 1,
            PermissionScope.for_profile(PermissionProfile.ASK_FOR_APPROVAL),
            "a" * 64,
        )

        records = await asyncio.gather(*(
            service.handle_permission(worker, handle, permission_event, approval_context)
            for service, worker in zip(services, workers, strict=True)
        ))

        assert reviewer.calls == 1
        assert records[0] == records[1]
        assert workers[0].responses == workers[1].responses

    asyncio.run(scenario())


def test_terminal_result_requires_exact_handle_lineage() -> None:
    class WrongResultWorker(SequentialPermissionWorker):
        async def collect_result(self, handle):
            result = await super().collect_result(handle)
            return WorkerResult(
                session_id=result.session_id, agent_id="agt_other",
                task_id=result.task_id, attempt_id=result.attempt_id,
                status=result.status, payload=result.payload,
            )

    async def scenario() -> None:
        worker = WrongResultWorker(())
        handle = await worker.start_task(TaskRequest(
            agent_id="agt_executor", task_id="tsk_implementation",
            attempt_id="att_1", instruction="Implement the confirmed task.",
        ))
        service = ApprovalService(store=FakeStore(), clock=FrozenClock(NOW))
        with pytest.raises(
            ValueError, match="worker terminal event and result disagree"
        ):
            await service.bridge_attempt(
                worker, handle,
                ApprovalContext(
                    "msn_1", 1, PermissionScope.for_profile(), "a" * 64
                ),
            )

    asyncio.run(scenario())


def test_reviewer_verdict_rejects_terminal_control_text() -> None:
    with pytest.raises(ValueError, match="reason must be bounded text"):
        ReviewerVerdict(True, "approved\x1b[31m")
