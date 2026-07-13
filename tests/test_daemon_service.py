from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import MappingProxyType

import pytest

from agentdeck.daemon.scheduler import SchedulerDecision, SchedulerFacts
from agentdeck.daemon.client import (
    CLIENT_METHODS,
    DaemonUnavailable,
    admit_confirmed_mission,
)
from agentdeck.daemon.service import (
    ProjectDaemonService,
    ServiceError,
    validate_confirmed_mission_admission,
)


MISSION_ID = "mis_0123456789ab"


class FakeServer:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def start(self) -> None:
        self.calls.append("server:start")

    async def close(self) -> None:
        self.calls.append("server:close")


def pending_facts() -> SchedulerFacts:
    return SchedulerFacts(
        mission_id=MISSION_ID,
        mission_state="running",
        step_id="step_1",
        step_state="pending",
        attempt_id=None,
        attempt_state="none",
        reply_state="none",
        handoff_state="none",
        permission_state="none",
        worker_ready=True,
        next_step_eligible=False,
        all_steps_completed=False,
        snapshot_state="valid",
        lineage_state="valid",
        ownership_state="owned",
        active_attempt_count=0,
        blocker=None,
    )


async def _case_startup_reconciles_and_flushes_before_server_and_scheduler() -> None:
    calls: list[str] = []
    service = ProjectDaemonService(
        server=FakeServer(calls),
        reconcile_all=lambda: calls.append("reconcile"),
        flush_safe_outboxes=lambda: calls.append("flush"),
        load_scheduler_facts=lambda: pending_facts(),
        apply_transition=lambda decision: calls.append(f"effect:{decision.kind}"),
    )

    await service.start()
    await service.tick()
    await service.close()

    assert calls == [
        "reconcile",
        "server:start",
        "flush",
        "effect:prepare_dispatch",
        "server:close",
    ]


async def _case_failed_reconciliation_never_starts_server_or_applies_effect() -> None:
    calls: list[str] = []

    def fail_reconcile() -> None:
        calls.append("reconcile")
        raise RuntimeError("corrupt")

    service = ProjectDaemonService(
        server=FakeServer(calls),
        reconcile_all=fail_reconcile,
        flush_safe_outboxes=lambda: calls.append("flush"),
        load_scheduler_facts=lambda: pending_facts(),
        apply_transition=lambda decision: calls.append(f"effect:{decision.kind}"),
    )

    with pytest.raises(ServiceError, match="startup reconciliation failed"):
        await service.start()
    assert calls == ["reconcile"]


async def _case_service_applies_only_one_external_effect_per_iteration() -> None:
    calls: list[str] = []
    service = ProjectDaemonService(
        server=FakeServer(calls),
        reconcile_all=lambda: None,
        flush_safe_outboxes=lambda: None,
        load_scheduler_facts=lambda: pending_facts(),
        apply_transition=lambda decision: calls.append(decision.kind),
    )
    await service.start()

    await service.tick()

    assert calls == ["server:start", "prepare_dispatch"]
    await service.close()


async def _case_long_worker_io_only_enqueues_completion_for_later_tick() -> None:
    calls: list[str] = []
    worker_release = asyncio.Event()

    async def worker_io() -> str:
        calls.append("worker:started")
        await worker_release.wait()
        calls.append("worker:return")
        return "validated"

    service = ProjectDaemonService(
        server=FakeServer(calls),
        reconcile_all=lambda: None,
        flush_safe_outboxes=lambda: None,
        load_scheduler_facts=lambda: None,
        apply_transition=lambda decision: calls.append(f"effect:{decision.kind}"),
    )
    await service.start()
    service.start_worker_io(
        worker_io(),
        on_completion=lambda value: calls.append(f"completion:{value}"),
    )
    await asyncio.sleep(0)
    assert calls == ["server:start", "worker:started"]

    worker_release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert calls == ["server:start", "worker:started", "worker:return"]

    await service.tick()
    assert calls[-1] == "completion:validated"
    await service.close()


async def _case_mutations_and_worker_completions_share_one_service_owned_queue() -> None:
    calls: list[str] = []
    service = ProjectDaemonService(
        server=FakeServer(calls),
        reconcile_all=lambda: None,
        flush_safe_outboxes=lambda: None,
        load_scheduler_facts=lambda: pending_facts(),
        apply_transition=lambda decision: calls.append(f"effect:{decision.kind}"),
    )
    await service.start()
    first = service.submit_mutation(lambda: calls.append("mutation:first"))
    second = service.submit_mutation(lambda: calls.append("mutation:second"))

    await service.tick()
    assert calls == ["server:start", "mutation:first"]
    assert first.done() and not second.done()

    await service.tick()
    assert calls == ["server:start", "mutation:first", "mutation:second"]
    assert second.done()
    await service.close()


def test_startup_reconciles_and_flushes_before_server_and_scheduler() -> None:
    asyncio.run(_case_startup_reconciles_and_flushes_before_server_and_scheduler())


def test_failed_reconciliation_never_starts_server_or_applies_effect() -> None:
    asyncio.run(_case_failed_reconciliation_never_starts_server_or_applies_effect())


def test_service_applies_only_one_external_effect_per_iteration() -> None:
    asyncio.run(_case_service_applies_only_one_external_effect_per_iteration())


def test_long_worker_io_only_enqueues_completion_for_later_tick() -> None:
    asyncio.run(_case_long_worker_io_only_enqueues_completion_for_later_tick())


def test_mutations_and_worker_completions_share_one_service_owned_queue() -> None:
    asyncio.run(_case_mutations_and_worker_completions_share_one_service_owned_queue())


def test_run_owns_bounded_loop_and_shutdown() -> None:
    calls: list[str] = []
    service: ProjectDaemonService

    def apply(decision: SchedulerDecision) -> None:
        calls.append(decision.kind)
        service.request_shutdown()

    service = ProjectDaemonService(
        server=FakeServer(calls),
        reconcile_all=lambda: calls.append("reconcile"),
        flush_safe_outboxes=lambda: None,
        load_scheduler_facts=lambda: pending_facts(),
        apply_transition=apply,
    )
    asyncio.run(service.run(poll_interval_seconds=0.001))
    assert calls == ["reconcile", "server:start", "prepare_dispatch", "server:close"]


class AdmissionStore:
    def __init__(self, mission: dict[str, object]) -> None:
        self.mission = mission

    def mission_by_id(self, mission_id: str) -> dict[str, object]:
        assert mission_id == self.mission["mission_id"]
        return dict(self.mission)


def test_daemon_admission_revalidates_exact_frozen_snapshot_digest() -> None:
    snapshot = {
        "mission": {"mission_id": MISSION_ID},
        "execution_hash": "sha256:" + "a" * 64,
    }
    store = AdmissionStore(
        {
            "mission_id": MISSION_ID,
            "status": "preparing",
            "snapshot_hash": snapshot["execution_hash"],
            "execution_snapshot": snapshot,
        }
    )

    assert validate_confirmed_mission_admission(
        store,
        {
            "mission_id": MISSION_ID,
            "snapshot_hash": snapshot["execution_hash"],
            "execution_snapshot": snapshot,
        },
    ) == {
        "accepted": True,
        "mission_id": MISSION_ID,
        "snapshot_hash": snapshot["execution_hash"],
        "state": "admitted",
    }

    with pytest.raises(ServiceError, match="frozen Mission admission drift"):
        validate_confirmed_mission_admission(
            store,
            {
                "mission_id": MISSION_ID,
                "snapshot_hash": "sha256:" + "b" * 64,
                "execution_snapshot": snapshot,
            },
        )

    frozen_snapshot = MappingProxyType(
        {
            "mission": MappingProxyType({"mission_id": MISSION_ID}),
            "execution_hash": snapshot["execution_hash"],
        }
    )
    assert validate_confirmed_mission_admission(
        store,
        {
            "mission_id": MISSION_ID,
            "snapshot_hash": snapshot["execution_hash"],
            "execution_snapshot": frozen_snapshot,
        },
    )["accepted"] is True


def test_unavailable_daemon_leaves_confirmed_mission_visible_not_foreground() -> None:
    snapshot = {
        "mission": {"mission_id": MISSION_ID},
        "execution_hash": "sha256:" + "a" * 64,
    }
    mission = {
        "mission_id": MISSION_ID,
        "status": "preparing",
        "snapshot_hash": snapshot["execution_hash"],
        "execution_snapshot": snapshot,
    }
    foreground_calls: list[str] = []

    async def unavailable(*_args: object, **_kwargs: object) -> object:
        raise DaemonUnavailable("offline")

    result = asyncio.run(
        admit_confirmed_mission(
            object(),
            object(),
            mission,
            connect_factory=unavailable,
        )
    )

    assert "mission.admit" in CLIENT_METHODS
    assert result == {
        "accepted": False,
        "mission_id": MISSION_ID,
        "snapshot_hash": snapshot["execution_hash"],
        "state": "confirmed_not_admitted",
        "blocker": "verified project daemon is unavailable",
        "recovery_control": {
            "kind": "retry_admission",
            "command": f"agentdeck mission run --mission-id {MISSION_ID} --confirm",
            "safety": "explicit_user",
        },
    }
    assert foreground_calls == []
