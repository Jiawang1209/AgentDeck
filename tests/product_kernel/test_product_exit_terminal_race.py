from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.async_exit_coordinator import AsyncExitCoordinator
from agentdeck.application.execution_runtime import (
    ActiveExecutionBinding, ExecutionBindingError, ForegroundExecutionRuntime,
)
from agentdeck.application.execution_service import ExecutionService
from agentdeck.application.exit_service import ExitService
from agentdeck.application.project_lifecycle_service import (
    ProjectDispatchBlocked, ProjectLifecycleService,
)
from agentdeck.kernel.execution import Attempt, Evidence, EvidenceKind, Handoff
from agentdeck.ports.worker import WorkerHandle

from .fakes import FrozenClock, RecordingApprovalService
from .test_sqlite_execution import NOW, _attempt_snapshot, _seed_lineage
from .test_product_exit_acp_integration import ExitHarness


class TerminalWinWorker:
    def __init__(self) -> None:
        self.cancel_count = 0
        self.on_cancel = None

    async def start_task(self, request): raise AssertionError("unexpected start")
    def stream_events(self, handle): raise AssertionError("unexpected stream")
    async def respond_permission(self, handle, **kwargs): raise AssertionError("unexpected permission")
    async def collect_result(self, handle): raise AssertionError("unexpected collect")

    async def cancel_task(self, handle, *, reason):
        self.cancel_count += 1
        assert reason == "product_exit_confirmed"
        assert self.on_cancel is not None
        self.on_cancel()


def test_terminal_win_uses_production_bundle_and_release_without_artifact_loss(
    tmp_path,
):
    async def race() -> None:
        store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
        try:
            _seed_lineage(store)
            connection = store._require_writer()
            connection.execute(
                "UPDATE agent_instances SET acp_session_id='ses_acp_1' "
                "WHERE instance_id='agt_implementation'"
            )
            started = Attempt.pending(
                "att_impl_1", "tsk_implementation", 1
            ).start()
            started_facts = _attempt_snapshot(started)
            started_facts["acp_session_id"] = "ses_acp_1"
            store.execute_once(
                "cmd_started", "execution_attempt_started",
                lambda transaction: transaction.save_aggregate(
                    "attempts", started.attempt_id, started_facts
                ) or {"attempt_id": started.attempt_id},
            )
            worker = TerminalWinWorker()
            handle = WorkerHandle(
                "ses_acp_1", "agt_implementation",
                "tsk_implementation", "att_impl_1",
            )
            runtime = ForegroundExecutionRuntime()
            runtime.bind(ActiveExecutionBinding(
                "att_impl_1", "tsk_implementation", "agt_implementation",
                "ses_acp_1", handle, worker,
            ))
            lifecycle = ProjectLifecycleService(
                store=store, clock=FrozenClock(NOW), session_id="ses_1"
            )
            execution = ExecutionService(
                store=store, clock=FrozenClock(NOW),
                approval_service=RecordingApprovalService(
                    store=store, clock=FrozenClock(NOW)
                ),
                worker_factory=lambda task: worker,
                runtime=runtime, lifecycle=lifecycle,
            )
            service = ExitService(
                store=store, clock=FrozenClock(NOW), session_id="ses_1",
                request_id_factory=lambda: "xrt_" + "9" * 32,
            )
            request = service.request_exit().request
            assert request is not None
            coordinator = AsyncExitCoordinator(
                exit_service=service, store=store, clock=FrozenClock(NOW),
                runtime=runtime, lifecycle=lifecycle, session_id="ses_1",
            )
            terminal = started.complete("implementation complete")
            evidence = Evidence.create(
                "ev_terminal_win", EvidenceKind.ARTIFACT_HASH,
                {"artifact_reference": "workspace patch", "content_hash": "b" * 64},
            )
            handoff = Handoff.create(
                "hnd_terminal_win", terminal.attempt_id, "tsk_review",
                terminal.result_summary, (evidence.evidence_id,),
                artifact_references=("workspace patch",),
            )
            confirmed = SimpleNamespace(mission_id="msn_1", version=1)
            task = SimpleNamespace(
                task_id="tsk_implementation",
                agent_instance_id="agt_implementation",
            )
            committed = {}

            def terminal_win() -> None:
                committed["bundle"] = execution._persist_terminal(
                    terminal, task, (evidence,), handoff, confirmed, "ses_acp_1"
                )
                runtime.release(terminal.attempt_id, handle)

            worker.on_cancel = terminal_win
            result = await coordinator.confirm(
                request.request_id, request.attempt_hash
            )
            assert result.diagnostic.code == "exit_authority_changed_after_cancel"
            with pytest.raises(ProjectDispatchBlocked):
                lifecycle.require_dispatchable()
            bundle = committed["bundle"]
            preserved = (
                store.load_aggregate("evidence", evidence.evidence_id),
                store.load_aggregate("handoffs", handoff.handoff_id),
            )
            assert bundle.attempt == terminal and bundle.evidence == (evidence,)
            assert bundle.handoff == handoff
            paused = await coordinator.request_exit()
            assert paused.mode == "project_paused" and paused.should_exit is True
            assert preserved == (
                store.load_aggregate("evidence", evidence.evidence_id),
                store.load_aggregate("handoffs", handoff.handoff_id),
            )
            assert worker.cancel_count == 1
        finally:
            store.close()

    asyncio.run(race())


def test_durable_success_replay_retries_only_runtime_settlement(
    tmp_path, monkeypatch,
):
    async def replay() -> None:
        harness = ExitHarness(tmp_path)
        try:
            harness.bind()
            original = harness.runtime.settle_exit_cancellation
            monkeypatch.setattr(
                harness.runtime, "settle_exit_cancellation",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    ExecutionBindingError("synthetic settlement drift")
                ),
            )
            first = await harness.coordinator.confirm(
                harness.request.request_id, harness.request.attempt_hash
            )
            assert first.diagnostic.code == "exit_runtime_convergence_failed"
            monkeypatch.setattr(
                harness.runtime, "settle_exit_cancellation", original
            )
            second = await harness.coordinator.confirm(
                harness.request.request_id, harness.request.attempt_hash
            )
            assert second.mode == "project_paused" and second.should_exit is True
            assert harness.worker.cancel_count == 1
        finally:
            harness.close()

    asyncio.run(replay())
