from __future__ import annotations

import asyncio
from hashlib import sha256
import json

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.async_exit_coordinator import AsyncExitCoordinator
from agentdeck.application.execution_runtime import ForegroundExecutionRuntime
from agentdeck.application.execution_service import ExecutionService
from agentdeck.application.exit_service import ExitService
from agentdeck.application.project_lifecycle_service import ProjectLifecycleService
from agentdeck.kernel.permissions import PermissionProfile, PermissionScope
from agentdeck.ports.worker import WorkerEvent, WorkerHandle, WorkerResult

from .fakes import FrozenClock, RecordingApprovalService
from .test_sqlite_execution_resume import NOW, _seed_base


class ObservedRuntime(ForegroundExecutionRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.release_observed = asyncio.Event()

    def release(self, attempt_id, worker_handle) -> None:
        super().release(attempt_id, worker_handle)
        self.release_observed.set()


class BlockingTerminalWorker:
    def __init__(self, runtime: ObservedRuntime) -> None:
        self.runtime = runtime
        self.handle = None
        self.collect_entered = asyncio.Event()
        self.result_allowed = asyncio.Event()
        self.cancel_count = 0
        self.started_count = 0

    async def start_task(self, request):
        self.started_count += 1
        self.handle = WorkerHandle(
            "ses_implementation", request.agent_id,
            request.task_id, request.attempt_id,
        )
        return self.handle

    async def _events(self):
        assert self.handle is not None
        yield WorkerEvent(
            "evt_terminal", self.handle.session_id, self.handle.agent_id,
            self.handle.task_id, self.handle.attempt_id, "acp", 1,
            "completed", NOW.isoformat(), {"summary": "done"},
        )

    def stream_events(self, handle):
        assert handle == self.handle
        return self._events()

    async def respond_permission(self, handle, **kwargs):
        raise AssertionError("terminal race has no permission request")

    async def collect_result(self, handle):
        assert handle == self.handle
        self.collect_entered.set()
        await self.result_allowed.wait()
        return WorkerResult(
            handle.session_id, handle.agent_id, handle.task_id,
            handle.attempt_id, "completed", {
                "summary": "implementation complete",
                "artifact_reference": "workspace patch",
                "content_hash": "b" * 64,
            },
        )

    async def cancel_task(self, handle, *, reason):
        assert handle == self.handle
        assert reason == "product_exit_confirmed"
        self.cancel_count += 1
        self.result_allowed.set()
        await self.runtime.release_observed.wait()


def _prepare_execution(store: SQLiteStore):
    _seed_base(store)
    store._require_writer().execute(
        "UPDATE product_sessions SET state='running' WHERE session_id='ses_1'"
    )
    store._require_writer().execute(
        "UPDATE tasks SET state='running' WHERE task_id='tsk_implementation'"
    )
    store._require_writer().execute(
        "UPDATE agent_instances SET acp_session_id='ses_implementation' "
        "WHERE instance_id='agt_implementation'"
    )
    return store._resume_draft, store._resume_confirmed


def test_terminal_win_uses_public_execution_and_preserves_real_bundle(tmp_path):
    async def race() -> None:
        clock = FrozenClock(NOW)
        store = SQLiteStore.open(tmp_path, clock=clock)
        try:
            draft, confirmed = _prepare_execution(store)
            runtime = ObservedRuntime()
            worker = BlockingTerminalWorker(runtime)
            lifecycle = ProjectLifecycleService(
                store=store, clock=clock, session_id="ses_1"
            )
            execution = ExecutionService(
                store=store, clock=clock,
                approval_service=RecordingApprovalService(
                    store=store, clock=clock
                ),
                worker_factory=lambda task: worker,
                runtime=runtime, lifecycle=lifecycle,
            )
            exit_service = ExitService(
                store=store, clock=clock, session_id="ses_1",
                request_id_factory=lambda: "xrt_" + "9" * 32,
            )
            coordinator = AsyncExitCoordinator(
                exit_service=exit_service, store=store, clock=clock,
                runtime=runtime, lifecycle=lifecycle, session_id="ses_1",
            )
            mission = asyncio.create_task(execution.run_confirmed_mission(
                session_id="ses_1", confirmed=confirmed, draft=draft,
                permission_scope=PermissionScope.for_profile(
                    PermissionProfile.APPROVE_FOR_ME
                ),
            ))
            await worker.collect_entered.wait()
            pending = await coordinator.request_exit()
            assert pending.mode == "exit_confirmation_required"
            request = pending.request
            result = await coordinator.confirm(
                request.request_id, request.attempt_hash
            )
            completed = await mission
            assert result.diagnostic.code == "exit_authority_changed_after_cancel"
            assert completed.diagnostic.code == "project_dispatch_paused"
            assert worker.started_count == worker.cancel_count == 1
            assert runtime.release_observed.is_set()

            attempt = completed.attempts[0]
            evidence = completed.evidence[0]
            handoff = completed.handoffs[0]
            snapshots = (
                store.load_aggregate("attempts", attempt.attempt_id),
                store.load_aggregate("evidence", evidence.evidence_id),
                store.load_aggregate("handoffs", handoff.handoff_id),
            )
            evidence_row = store._require_writer().execute(
                "SELECT canonical_evidence_facts,content_hash FROM evidence "
                "WHERE evidence_id=?", (evidence.evidence_id,),
            ).fetchone()
            assert snapshots[0]["state"] == "completed"
            assert json.loads(evidence_row[0])["content_hash"] == "b" * 64
            assert evidence_row[1] == sha256(evidence_row[0].encode()).hexdigest()
            assert snapshots[2]["content_hash"] == handoff.content_hash

            paused = await coordinator.request_exit()
            assert paused.mode == "project_paused" and paused.should_exit is True
            assert worker.cancel_count == 1
            assert snapshots == (
                store.load_aggregate("attempts", attempt.attempt_id),
                store.load_aggregate("evidence", evidence.evidence_id),
                store.load_aggregate("handoffs", handoff.handoff_id),
            )
        finally:
            store.close()

    asyncio.run(race())
