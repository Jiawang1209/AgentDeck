from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agentdeck.adapters.acp import ACPWorker
from product_kernel.fakes import FrozenClock
from product_kernel.fixtures.fake_acp_agent import FakeACPAgent
from product_kernel.worker_contract import assert_worker_contract, task_request


NOW = datetime(2026, 7, 19, 9, 0, 0, tzinfo=timezone.utc)


def worker_factory(scenario: str = "success"):
    def create() -> ACPWorker:
        kwargs = {"max_total_bytes": 64 * 1024} if scenario == "total_oversize" else {}
        return ACPWorker(
            agent=FakeACPAgent(scenario), project_root="/tmp/project",
            clock=FrozenClock(NOW), **kwargs,
        )

    return create


def test_acp_worker_passes_shared_contract() -> None:
    asyncio.run(assert_worker_contract(worker_factory()))


def test_two_permissions_are_sequential_and_lineage_bound() -> None:
    async def scenario() -> None:
        worker = worker_factory("two_permissions")()
        handle = await worker.start_task(task_request())
        permission_ids = []
        events = []
        async for event in worker.stream_events(handle):
            events.append(event)
            if event.kind == "permission_requested":
                permission_id = event.payload["permission_request_id"]
                permission_ids.append(permission_id)
                await worker.respond_permission(
                    handle, permission_request_id=permission_id, allowed=True,
                    reason="approved for contract test",
                )

        assert permission_ids == ["perm_1", "perm_2"]
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert all(event.session_id == handle.session_id for event in events)
        assert {"progress", "tool_started", "tool_completed", "artifact_changed"} <= {
            event.kind for event in events
        }
        assert (await worker.collect_result(handle)).status == "completed"

    asyncio.run(scenario())


def test_raw_acp_frames_never_enter_stable_worker_payloads() -> None:
    async def scenario() -> None:
        worker = worker_factory()()
        handle = await worker.start_task(task_request())
        events = []
        async for event in worker.stream_events(handle):
            events.append(event)
            if event.kind == "permission_requested":
                await worker.respond_permission(
                    handle,
                    permission_request_id=event.payload["permission_request_id"],
                    allowed=True,
                    reason="approved",
                )
        rendered = repr([(event.kind, dict(event.payload)) for event in events])
        assert "rawInput" not in rendered and "rawOutput" not in rendered
        assert "raw-acp-session" not in rendered

    asyncio.run(scenario())
