from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from acp.schema import (
    AgentCapabilities, InitializeResponse, NewSessionResponse,
)

import agentdeck.adapters.acp_task_boundary as task_boundary
from agentdeck.adapters.acp import ACPWorker
from agentdeck.adapters.acp_worker_connection import ACPWorkerConnection
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.async_exit_coordinator import AsyncExitCoordinator
from agentdeck.application.execution_runtime import (
    ActiveExecutionBinding, ForegroundExecutionRuntime,
)
from agentdeck.application.exit_service import ExitService
from agentdeck.application.project_lifecycle_service import (
    ProjectDispatchBlocked, ProjectLifecycleService,
)
from agentdeck.ports.worker import WorkerCancellationError

from .fakes import FrozenClock
from .test_sqlite_exit_authority import NOW, REQUEST_ID, _seed_lineage
from .worker_contract import task_request


class BlockingACPConnection:
    def __init__(self, owner: "RealACPExitHarness") -> None:
        self.owner = owner

    async def initialize(self, protocol_version: int) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(self, *, cwd: str) -> NewSessionResponse:
        return NewSessionResponse(session_id="raw_session")

    async def prompt(self, *args, **kwargs) -> object:
        self.owner.prompt_entered.set()
        while True:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.owner.prompt_cancel_count += 1
                if (
                    self.owner.prompt_cancel_count
                    <= self.owner.resisted_prompt_cancels
                ):
                    self.owner.prompt_cleanup_entered.set()
                    continue
                raise

    async def cancel(self, session_id: str) -> None:
        self.owner.cancel_calls += 1
        self.owner.cancel_entered.set()
        if self.owner.block_cancel:
            await asyncio.Event().wait()


class RealACPExitHarness:
    def __init__(
        self, root: Path, *, block_cancel: bool = False,
        resisted_prompt_cancels: int = 0,
    ) -> None:
        self.block_cancel = block_cancel
        self.resisted_prompt_cancels = resisted_prompt_cancels
        self.prompt_cancel_count = 0
        self.cancel_calls = 0
        self.cancel_entered = asyncio.Event()
        self.prompt_entered = asyncio.Event()
        self.prompt_cleanup_entered = asyncio.Event()
        self.owner_reaped = asyncio.Event()
        self.store = SQLiteStore.open(root, clock=FrozenClock(NOW))
        _seed_lineage(self.store, ("running",))

        @asynccontextmanager
        async def spawn(*args, **kwargs):
            try:
                yield BlockingACPConnection(self), object()
            finally:
                self.owner_reaped.set()

        self.connection = ACPWorkerConnection(
            ("/verified/adapter",), project_root=str(root),
            spawn_factory=spawn, timeout_seconds=0.05,
        )
        self.worker = ACPWorker(
            agent=self.connection, project_root=str(root), clock=FrozenClock(NOW),
            project_boundary_enforced=True,
        )
        self.runtime = ForegroundExecutionRuntime()
        self.request = None
        self.handle = None
        self.coordinator = None
        self.lifecycle = None

    async def start(self) -> None:
        handle = await self.worker.start_task(task_request())
        self.handle = handle
        await self.prompt_entered.wait()
        connection = self.store._require_writer()
        connection.execute(
            "UPDATE agent_instances SET acp_session_id=? WHERE instance_id='agt_1'",
            (handle.session_id,),
        )
        connection.execute(
            "UPDATE attempts SET acp_session_id=? WHERE attempt_id='att_1'",
            (handle.session_id,),
        )
        exit_service = ExitService(
            store=self.store, clock=FrozenClock(NOW), session_id="ses_1",
            request_id_factory=lambda: REQUEST_ID,
        )
        self.request = exit_service.request_exit().request
        assert self.request is not None
        self.lifecycle = ProjectLifecycleService(
            store=self.store, clock=FrozenClock(NOW), session_id="ses_1",
        )
        self.runtime.bind(ActiveExecutionBinding(
            "att_1", "tsk_1", "agt_1", handle.session_id, handle, self.worker,
        ))
        self.coordinator = AsyncExitCoordinator(
            exit_service=exit_service, store=self.store, clock=FrozenClock(NOW),
            runtime=self.runtime, lifecycle=self.lifecycle, session_id="ses_1",
        )

    def database_facts(self) -> tuple[tuple[object, ...], ...]:
        connection = self.store._require_writer()
        return tuple(
            row for table in ("product_sessions", "attempts", "commands", "events")
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")
        )

    async def close(self) -> None:
        prompt = None if self.worker._run is None else self.worker._run.prompt_task
        if prompt is not None and not prompt.done():
            prompt.cancel()
            await asyncio.gather(prompt, return_exceptions=True)
        await self.connection.aclose()
        self.store.close()


def test_real_acp_confirm_preserves_caller_cancellation_and_closes_fence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = RealACPExitHarness(tmp_path, block_cancel=True)
        await harness.start()
        assert harness.coordinator is not None and harness.request is not None
        before = harness.database_facts()
        confirmation = asyncio.create_task(harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash,
        ))
        await harness.cancel_entered.wait()
        confirmation.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await confirmation
            assert confirmation.cancelled()
            await asyncio.sleep(0)
            assert harness.database_facts() == before
            assert harness.cancel_calls == 1
            assert harness.runtime.status().state == "fenced_pending"
            assert harness.runtime.matching_exit_cancellation(
                harness.request.request_id, harness.request.attempt_hash,
            ).outcome.code == "transport_disconnected"
            with pytest.raises(ProjectDispatchBlocked):
                harness.lifecycle.require_dispatchable()
            prompt = harness.worker._run.prompt_task
            assert prompt is not None and prompt.done()
            assert harness.owner_reaped.is_set()
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_real_acp_resistant_prompt_is_recancelled_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(task_boundary, "_PROMPT_CLEANUP_TIMEOUT_SECONDS", 0.01)
        harness = RealACPExitHarness(tmp_path, resisted_prompt_cancels=1)
        await harness.start()
        assert harness.handle is not None
        try:
            await asyncio.wait_for(
                harness.worker.cancel_task(
                    harness.handle, reason="product_exit_confirmed",
                ),
                timeout=0.2,
            )
            prompt = harness.worker._run.prompt_task
            assert harness.prompt_cleanup_entered.is_set()
            assert prompt is not None and prompt.done()
            assert harness.owner_reaped.is_set()
            assert (await harness.worker.collect_result(harness.handle)).status == (
                "cancelled"
            )
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_real_acp_cleanup_timeout_never_reports_worker_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(task_boundary, "_PROMPT_CLEANUP_TIMEOUT_SECONDS", 0.01)
        harness = RealACPExitHarness(tmp_path, resisted_prompt_cancels=2)
        await harness.start()
        assert harness.handle is not None
        try:
            with pytest.raises(WorkerCancellationError) as captured:
                await asyncio.wait_for(
                    harness.worker.cancel_task(
                        harness.handle, reason="product_exit_confirmed",
                    ),
                    timeout=0.2,
                )
            assert (captured.value.code, captured.value.outcome_known) == (
                "cancel_timeout", False,
            )
            assert harness.prompt_cancel_count == 2
            assert harness.worker._run.prompt_task is not None
            assert not harness.worker._run.prompt_task.done()
            with pytest.raises(WorkerCancellationError) as collected:
                await harness.worker.collect_result(harness.handle)
            assert collected.value is captured.value
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_prompt_cleanup_reports_timeout_when_task_resists_bounded_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(task_boundary, "_PROMPT_CLEANUP_TIMEOUT_SECONDS", 0.01)
        release = asyncio.Event()
        cancellations = 0

        async def resistant_prompt() -> None:
            nonlocal cancellations
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancellations += 1

        prompt = asyncio.create_task(resistant_prompt())
        await asyncio.sleep(0)
        cleanup_pending = await task_boundary.cancel_background_task(prompt)
        assert cleanup_pending is True
        assert cancellations == 2 and not prompt.done()
        release.set()
        await asyncio.wait_for(prompt, timeout=0.1)
        assert prompt.done()

    asyncio.run(scenario())


def test_caller_cancellation_during_stalled_owner_claim_is_not_masked(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = RealACPExitHarness(tmp_path)
        await harness.start()
        await harness.connection._close_lock.acquire()
        cancellation = asyncio.create_task(
            harness.connection.cancel("raw_session")
        )
        await asyncio.sleep(0)
        cancellation.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(cancellation), timeout=0.2)
            assert cancellation.cancelled()
        finally:
            harness.connection._close_lock.release()
            await asyncio.wait_for(harness.close(), timeout=0.2)

    asyncio.run(scenario())
