from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from acp import spawn_agent_process

from agentdeck.adapters.acp import ACPWorker, _native_request_id, _permission_effect
from agentdeck.adapters.codex_acp_server import CodexACPServer
from agentdeck.kernel.permissions import Effect, PermissionProfile, PermissionScope
from product_kernel.fakes import FrozenClock
from product_kernel.fixtures.fake_acp_agent import FakeACPAgent
from product_kernel.fixtures.fake_codex_app_server import fake_command
from product_kernel.worker_contract import assert_worker_contract, task_request


NOW = datetime(2026, 7, 19, 9, 0, 0, tzinfo=timezone.utc)


class _WorkerCallback:
    worker: ACPWorker | None = None

    async def session_update(self, *args, **kwargs):
        assert self.worker is not None
        return await self.worker.session_update(*args, **kwargs)

    async def request_permission(self, *args, **kwargs):
        assert self.worker is not None
        return await self.worker.request_permission(*args, **kwargs)


def worker_factory(scenario: str = "success"):
    def create() -> ACPWorker:
        kwargs = {"max_total_bytes": 64 * 1024} if scenario == "total_oversize" else {}
        return ACPWorker(
            agent=FakeACPAgent(scenario), project_root="/tmp/project",
            clock=FrozenClock(NOW), project_boundary_enforced=True, **kwargs,
        )

    return create


def test_acp_worker_passes_shared_contract() -> None:
    asyncio.run(assert_worker_contract(worker_factory()))


def test_permission_effect_mapping_is_conservative_and_bounded() -> None:
    expected = {
        "read": ("read", "read_only"), "search": ("read", "read_only"),
        "think": ("read", "read_only"),
        "edit": ("write_project", "project_mutation"),
        "move": ("write_project", "project_mutation"),
        "execute": ("command_project", "project_command"),
        "fetch": ("network", "network_access"),
        "delete": ("destructive", "project_deletion"),
    }
    assert {
        kind: _permission_effect(kind, project_boundary_enforced=True)
        for kind in expected
    } == expected
    unproven = {
        kind: _permission_effect(kind) for kind in ("edit", "move", "execute")
    }
    assert unproven == {
        "edit": ("write_external", "project_boundary_unproven"),
        "move": ("write_external", "project_boundary_unproven"),
        "execute": ("destructive", "project_boundary_unproven"),
    }
    default_scope = PermissionScope.for_profile(PermissionProfile.APPROVE_FOR_ME)
    assert all(
        not default_scope.decide(Effect(effect), actor="agt_executor").allowed
        for effect, _ in unproven.values()
    )
    for unknown in ("other", "switch_mode", "future_kind", None, []):
        assert _permission_effect(unknown) == (
            "destructive", "unclassified_tool_effect",
        )


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
        permission_events = [
            event for event in events if event.kind == "permission_requested"
        ]
        assert [event.payload["effect"] for event in permission_events] == [
            "write_project", "write_project",
        ]
        assert all(
            event.payload["risk"] == "project_mutation"
            for event in permission_events
        )
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


def test_real_codex_bridge_permission_preserves_native_request_lineage(tmp_path) -> None:
    async def scenario() -> None:
        bridge = CodexACPServer(app_server_command=fake_command(tmp_path / "calls.jsonl"))
        callback = _WorkerCallback()
        async with spawn_agent_process(
            callback, bridge.command[0], *bridge.command[1:], cwd=str(tmp_path),
            transport_kwargs={"shutdown_timeout": 2}, receive_timeout=5,
        ) as (connection, _process):
            worker = ACPWorker(
                agent=connection, project_root=str(tmp_path), clock=FrozenClock(NOW),
                project_boundary_enforced=True,
            )
            callback.worker = worker
            handle = await worker.start_task(task_request())
            permissions = []
            async for event in worker.stream_events(handle):
                if event.kind == "permission_requested":
                    permissions.append(event.payload)
                    await worker.respond_permission(
                        handle,
                        permission_request_id=event.payload["permission_request_id"],
                        allowed=True, reason="approved exact native lineage",
                    )
            assert permissions == [{
                "permission_request_id": "perm_1",
                "native_request_id": "perm_42",
                "tool_call_id": "item_42", "option_count": 2,
                "effect": "command_project", "risk": "project_command",
            }]
            assert (await worker.collect_result(handle)).status == "completed"

    asyncio.run(scenario())


def test_native_permission_identity_is_bounded_and_content_free() -> None:
    assert _native_request_id({"native_request_id": "native_42"}) == "native_42"
    assert _native_request_id({}) is None
    for invalid in (42, "", "x" * 257, "native id", "native\x00id"):
        with pytest.raises(ValueError, match="native request identity"):
            _native_request_id({"native_request_id": invalid})
