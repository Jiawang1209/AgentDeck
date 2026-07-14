from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
import time

import pytest

from agentdeck.daemon.scheduler import SchedulerDecision, SchedulerFacts
from agentdeck.daemon.client import (
    CLIENT_METHODS,
    DaemonUnavailable,
    admit_confirmed_mission,
    govern_mission,
)
from agentdeck.daemon.service import (
    DaemonWorkerCoordinator,
    ProjectDaemonService,
    ServiceError,
    resolve_previous_handoff,
    probe_tmux_worker_readiness,
    validate_confirmed_mission_admission,
)
from agentdeck.daemon.supervisor import SubmittedReceipt, TransportResult
from agentdeck.daemon.transports import AcpWorkerTransport, WorkerTransportError
from agentdeck.models import AgentSpec, RuntimeConfig


MISSION_ID = "mis_0123456789ab"


def test_tmux_readiness_probe_blocks_first_run_trust_without_sending_input() -> None:
    calls: list[str] = []
    backend = SimpleNamespace(
        pane_exists=lambda _config, _pane: True,
        capture_output=lambda _config, _pane, lines=200: (
            "Claude Code context 100%\nDo you trust the files in this folder?\n"
            "Yes, I trust this folder\n"
        ),
        send_input=lambda *_args: calls.append("send"),
    )
    ready, blocker = probe_tmux_worker_readiness(
        backend,
        runtime_config=RuntimeConfig(),
        agent=AgentSpec("reviewer", "review", "claude", "claude"),
        pane_id="%1",
    )
    assert ready is False
    assert blocker == "tmux Worker setup required: directory trust required"
    assert calls == []


def _compact_handoff(token: str, summary: str = "planner compact summary") -> dict[str, object]:
    return {
        "handoff_token": token,
        "status": "completed",
        "summary": summary,
        "verification": "planner deterministic verification",
        "risks": "none",
        "next_steps": "review the plan",
        "artifacts": [
            {"path": "reports/plan.md", "content_hash": "sha256:" + "a" * 64}
        ],
        "trace_ids": ["trace_planner"],
    }


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
    assert calls == [
        "server:start", "mutation:first", "effect:prepare_dispatch",
    ]
    assert not second.done()
    await service.tick()
    assert calls[-1] == "mutation:second"
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


def test_close_pumps_registered_worker_cleanup_before_server_close() -> None:
    calls: list[str] = []

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer(calls), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()

        async def worker() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                await service.submit_worker_cleanup(
                    lambda: calls.append("cleanup:persisted")
                )

        service.start_worker_io(worker(), on_completion=lambda _value: None)
        await asyncio.sleep(0)
        queued_external = service.submit_mutation(
            lambda: calls.append("external:must-not-run")
        )
        await service.close()

        assert calls == ["server:start", "cleanup:persisted", "server:close"]
        with pytest.raises(ServiceError, match="service closed"):
            await queued_external
        with pytest.raises(ServiceError, match="not accepting mutations"):
            service.submit_mutation(lambda: None)
        assert service.active_worker_task_count == 0

    asyncio.run(case())


def test_worker_cleanup_requires_registered_worker_even_while_open() -> None:
    calls: list[str] = []

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer(calls), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        with pytest.raises(ServiceError, match="cleanup is unavailable"):
            service.submit_worker_cleanup(
                lambda: calls.append("forged-cleanup")
            )

        completed = asyncio.Event()

        async def worker() -> str:
            await service.submit_worker_cleanup(
                lambda: calls.append("registered-cleanup")
            )
            completed.set()
            return "done"

        service.start_worker_io(worker(), on_completion=lambda _value: None)
        await asyncio.sleep(0)
        await service.tick()
        await asyncio.wait_for(completed.wait(), timeout=0.2)
        await service.close()
        assert "forged-cleanup" not in calls
        assert calls == ["server:start", "registered-cleanup", "server:close"]

    asyncio.run(case())


def test_concurrent_close_shares_cleanup_failure_and_still_closes_server() -> None:
    calls: list[str] = []

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer(calls), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()

        async def worker() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                await service.submit_worker_cleanup(
                    lambda: (_ for _ in ()).throw(OSError("save failed"))
                )

        service.start_worker_io(worker(), on_completion=lambda _value: None)
        await asyncio.sleep(0)
        results = await asyncio.gather(
            service.close(), service.close(), return_exceptions=True
        )

        assert [type(item) for item in results] == [ServiceError, ServiceError]
        assert [str(item) for item in results] == [
            "daemon shutdown cleanup failed", "daemon shutdown cleanup failed"
        ]
        assert calls == ["server:start", "server:close"]
        assert service.active_worker_task_count == 0

    asyncio.run(case())


def test_cancelled_first_close_waiter_does_not_cancel_shared_shutdown() -> None:
    calls: list[str] = []
    close_started: asyncio.Event
    release_close: asyncio.Event

    class BlockingServer(FakeServer):
        async def close(self) -> None:
            calls.append("server:closing")
            close_started.set()
            await release_close.wait()
            calls.append("server:closed")

    async def case() -> None:
        nonlocal close_started, release_close
        close_started = asyncio.Event()
        release_close = asyncio.Event()
        service = ProjectDaemonService(
            server=BlockingServer(calls), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        first = asyncio.create_task(service.close())
        await close_started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release_close.set()
        await service.close()
        assert calls == ["server:start", "server:closing", "server:closed"]

    asyncio.run(case())


def test_close_has_bounded_grace_for_worker_that_ignores_cancellation() -> None:
    calls: list[str] = []
    durable = {"attempt_state": "submitted", "session_state": "busy"}

    async def case() -> None:
        release_worker = asyncio.Event()
        worker_started = asyncio.Event()
        service = ProjectDaemonService(
            server=FakeServer(calls), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
            shutdown_grace_seconds=0.01,
        )
        await service.start()

        async def worker() -> str:
            worker_started.set()
            while not release_worker.is_set():
                try:
                    await release_worker.wait()
                except asyncio.CancelledError:
                    continue
            return "released"

        service.start_worker_io(worker(), on_completion=lambda _result: None)
        await worker_started.wait()
        started = time.monotonic()
        try:
            with pytest.raises(ServiceError, match="Worker shutdown grace exceeded"):
                await asyncio.wait_for(service.close(), timeout=0.2)
            assert time.monotonic() - started < 0.2
            assert calls == ["server:start", "server:close"]
            assert durable == {
                "attempt_state": "submitted", "session_state": "busy"
            }
            assert service.active_worker_task_count == 1
        finally:
            release_worker.set()
            for _ in range(20):
                await asyncio.sleep(0)
                if service.active_worker_task_count == 0:
                    break
            assert service.active_worker_task_count == 0

    asyncio.run(case())


def test_self_replenishing_callback_queue_cannot_starve_scheduler() -> None:
    calls: list[str] = []

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer(calls), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=pending_facts,
            apply_transition=lambda decision: calls.append(f"effect:{decision.kind}"),
        )
        await service.start()

        def mutation(number: int) -> None:
            calls.append(f"mutation:{number}")
            service.submit_mutation(lambda: mutation(number + 1))

        service.submit_mutation(lambda: mutation(1))
        await service.tick()
        await service.tick()
        assert calls == [
            "server:start", "mutation:1", "effect:prepare_dispatch",
        ]
        await service.close()

    asyncio.run(case())


def test_scheduler_due_without_facts_loads_scheduler_only_once_per_tick() -> None:
    loads = 0

    def no_facts():
        nonlocal loads
        loads += 1
        return None

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer([]), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=no_facts,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        service.submit_mutation(lambda: None)
        await service.tick()
        await service.tick()
        assert loads == 1
        await service.close()

    asyncio.run(case())


def test_worker_coordinator_persists_receipt_before_starting_completion() -> None:
    calls: list[str] = []
    attempt = {
        "attempt_id": "mat_0123456789ab", "mission_id": MISSION_ID,
        "step_id": "step_1", "agent_id": "planner", "configured_transport": "tmux",
        "dispatch_key": "dsp_" + "a" * 32, "state": "prepared",
    }

    class Store:
        def claim_mission_attempt_admission(self, **kwargs):
            calls.append("claim")
            return {**attempt, "state": "admitting", "admission_claim_id": "adm_0123456789ab"}
        def record_mission_attempt_submitted(self, **kwargs):
            calls.append("persist:submitted")
            return {**attempt, "state": "submitted", "admission_claim_id": "adm_0123456789ab"}
        def record_tmux_mission_attempt_completion(self, **kwargs):
            assert kwargs["canonical_handoff"]["summary"] == "done"
            calls.append("persist:tmux-combined")

    class Transport:
        async def admit(self, claimed):
            calls.append("io:admit")
            return SubmittedReceipt("receipt-1", attempt["dispatch_key"], "sent")
        async def complete(self, submitted, receipt):
            assert "persist:submitted" in calls
            calls.append("io:complete")
            return TransportResult("structured_reply", True, {
                "handoff_token": attempt["dispatch_key"], "status": "completed",
                "summary": "done", "verification": "ok", "risks": "none",
                "next_steps": "continue",
            })

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer(calls), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        coordinator = DaemonWorkerCoordinator(
            service=service, store=Store(), transport_for=lambda _attempt: Transport()
        )
        coordinator.launch(attempt)
        for _ in range(10):
            await asyncio.sleep(0)
            await service.tick()
            if "persist:tmux-combined" in calls:
                break
        assert calls.index("persist:submitted") < calls.index("io:complete")
        assert calls[-1] == "persist:tmux-combined"
        await service.close()

    asyncio.run(case())


def test_tmux_admission_timeout_persists_ambiguous_attempt() -> None:
    attempt = {
        "attempt_id": "mat_0123456789ab", "mission_id": MISSION_ID,
        "step_id": "step_1", "agent_id": "planner", "configured_transport": "tmux",
        "dispatch_key": "dsp_" + "a" * 32, "state": "prepared",
    }

    class Store:
        state = "prepared"

        def claim_mission_attempt_admission(self, **_kwargs):
            self.state = "admitting"
            return {**attempt, "state": "admitting", "admission_claim_id": "adm_0123456789ab"}

        def mark_mission_attempt_admission_ambiguous(self, **kwargs):
            assert kwargs["reason"] == "admission_outcome_unknown"
            self.state = "ambiguous"

    store = Store()

    class Transport:
        async def admit(self, _claimed):
            raise WorkerTransportError("tmux Worker admission failed")

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer([]), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        DaemonWorkerCoordinator(
            service=service, store=store, transport_for=lambda _attempt: Transport()
        ).launch(attempt)
        for _ in range(10):
            await asyncio.sleep(0)
            await service.tick()
            if store.state == "ambiguous":
                break
        assert store.state == "ambiguous"
        await service.close()

    asyncio.run(case())


def test_tmux_capture_timeout_persists_failed_completion() -> None:
    attempt = {
        "attempt_id": "mat_0123456789ab", "mission_id": MISSION_ID,
        "step_id": "step_1", "agent_id": "planner", "configured_transport": "tmux",
        "dispatch_key": "dsp_" + "a" * 32, "state": "prepared",
    }

    class Store:
        completion: dict[str, object] | None = None

        def claim_mission_attempt_admission(self, **_kwargs):
            return {**attempt, "state": "admitting", "admission_claim_id": "adm_0123456789ab"}

        def record_mission_attempt_submitted(self, **_kwargs):
            return {**attempt, "state": "submitted", "admission_claim_id": "adm_0123456789ab"}

        def record_tmux_mission_attempt_completion(self, **kwargs):
            self.completion = kwargs

    store = Store()

    class Transport:
        async def admit(self, claimed):
            return SubmittedReceipt("receipt-1", claimed["dispatch_key"], "sent")

        async def complete(self, _submitted, _receipt):
            raise WorkerTransportError("tmux Worker capture failed")

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer([]), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        DaemonWorkerCoordinator(
            service=service, store=store, transport_for=lambda _attempt: Transport()
        ).launch(attempt)
        for _ in range(20):
            await asyncio.sleep(0)
            await service.tick()
            if store.completion is not None:
                break
        assert store.completion is not None
        assert store.completion["succeeded"] is False
        assert store.completion["summary"] == "Worker transport failed"
        assert store.completion["canonical_handoff"] is None
        await service.close()

    asyncio.run(case())


def test_acp_worker_persists_submitted_receipt_before_prompt_starts() -> None:
    calls: list[str] = []
    prompt_release: asyncio.Event
    attempt = {
        "attempt_id": "mat_0123456789ab", "mission_id": MISSION_ID,
        "step_id": "step_1", "agent_id": "planner", "configured_transport": "acp",
        "dispatch_key": "dsp_" + "a" * 32, "state": "prepared",
    }

    class Store:
        durable_state = "prepared"

        def claim_mission_attempt_admission(self, **kwargs):
            del kwargs
            self.durable_state = "admitting"
            calls.append("persist:admitting")
            return {
                **attempt, "state": "admitting",
                "admission_claim_id": "adm_0123456789ab",
            }

        def record_acp_mission_attempt_completion(self, **kwargs):
            del kwargs
            assert self.durable_state == "submitted"
            self.durable_state = "succeeded"
            calls.append("persist:acp-completion")
            return {}

        def record_mission_attempt_submitted(self, **kwargs):
            assert kwargs["expected_claim_id"] == "adm_0123456789ab"
            assert self.durable_state == "admitting"
            self.durable_state = "submitted"
            calls.append("persist:submitted")
            return {
                **attempt,
                "state": "submitted",
                "admission_claim_id": "adm_0123456789ab",
            }

        def record_mission_attempt_result(self, **kwargs):
            raise AssertionError("ACP must use the combined durable mutation")

        def record_mission_reply_evidence(self, **kwargs):
            raise AssertionError("ACP must use the combined durable mutation")

    store = Store()

    class Transport:
        async def admit(self, claimed):
            del claimed
            calls.append("io:new-session")
            return SubmittedReceipt("receipt-1", attempt["dispatch_key"], "session-created")

        async def complete(self, claimed, receipt):
            del claimed, receipt
            calls.append("io:prompt-started")
            await prompt_release.wait()
            calls.append("io:prompt-result")
            return TransportResult("end_turn", True, {
                "handoff_token": attempt["dispatch_key"], "status": "completed",
                "summary": "done", "verification": "ok", "risks": "none",
                "next_steps": "continue",
            })

    async def case() -> None:
        nonlocal prompt_release
        prompt_release = asyncio.Event()
        service = ProjectDaemonService(
            server=FakeServer(calls), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        coordinator = DaemonWorkerCoordinator(
            service=service, store=store, transport_for=lambda _attempt: Transport()
        )
        coordinator.launch(attempt)
        for _ in range(10):
            await asyncio.sleep(0)
            await service.tick()
            if "io:prompt-started" in calls:
                break
        assert store.durable_state == "submitted"
        assert calls.index("persist:submitted") < calls.index("io:prompt-started")
        assert "persist:acp-completion" not in calls

        prompt_release.set()
        for _ in range(10):
            await asyncio.sleep(0)
            await service.tick()
            if "persist:acp-completion" in calls:
                break
        assert store.durable_state == "succeeded"
        assert calls[-1] == "persist:acp-completion"
        await service.close()

    asyncio.run(case())


def test_acp_completion_save_failure_persists_cleanup_ambiguity() -> None:
    attempt = {
        "attempt_id": "mat_0123456789ab", "mission_id": MISSION_ID,
        "step_id": "step_1", "agent_id": "planner", "configured_transport": "acp",
        "dispatch_key": "dsp_" + "a" * 32, "state": "prepared",
    }

    class Store:
        state = "prepared"
        ambiguity_stage: str | None = None

        def claim_mission_attempt_admission(self, **_kwargs):
            self.state = "admitting"
            return {**attempt, "state": "admitting",
                    "admission_claim_id": "adm_0123456789ab"}

        def record_mission_attempt_submitted(self, **_kwargs):
            self.state = "submitted"
            return {**attempt, "state": "submitted",
                    "admission_claim_id": "adm_0123456789ab"}

        def record_acp_mission_attempt_completion(self, **_kwargs):
            raise OSError("/private/state/result.json")

        def mark_acp_mission_attempt_completion_ambiguous(self, **kwargs):
            assert self.state == "submitted"
            self.ambiguity_stage = kwargs["completion_stage"]
            self.state = "ambiguous"

    store = Store()

    class Transport:
        async def admit(self, claimed):
            return SubmittedReceipt("receipt-1", claimed["dispatch_key"], "created")

        async def complete(self, _submitted, _receipt):
            return TransportResult("end_turn", True, _compact_handoff(attempt["dispatch_key"]))

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer([]), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        DaemonWorkerCoordinator(
            service=service, store=store, transport_for=lambda _attempt: Transport()
        ).launch(attempt)
        for _ in range(20):
            await asyncio.sleep(0)
            await service.tick()
            if store.state == "ambiguous":
                break
        assert store.state == "ambiguous"
        assert store.ambiguity_stage == "cleanup"
        await service.close()

    asyncio.run(case())


def test_acp_completion_and_ambiguity_save_failure_fail_stops_daemon() -> None:
    attempt = {
        "attempt_id": "mat_0123456789ab", "mission_id": MISSION_ID,
        "step_id": "step_1", "agent_id": "planner", "configured_transport": "acp",
        "dispatch_key": "dsp_" + "a" * 32, "state": "prepared",
    }

    class Store:
        state = "prepared"

        def claim_mission_attempt_admission(self, **_kwargs):
            self.state = "admitting"
            return {**attempt, "state": "admitting",
                    "admission_claim_id": "adm_0123456789ab"}

        def record_mission_attempt_submitted(self, **_kwargs):
            self.state = "submitted"
            return {**attempt, "state": "submitted",
                    "admission_claim_id": "adm_0123456789ab"}

        def record_acp_mission_attempt_completion(self, **_kwargs):
            raise OSError("first durable save failed")

        def mark_acp_mission_attempt_completion_ambiguous(self, **_kwargs):
            raise OSError("ambiguity save failed")

    store = Store()

    class Transport:
        async def admit(self, claimed):
            return SubmittedReceipt("receipt-1", claimed["dispatch_key"], "created")

        async def complete(self, _submitted, _receipt):
            return TransportResult("end_turn", True, _compact_handoff(attempt["dispatch_key"]))

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer([]), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        DaemonWorkerCoordinator(
            service=service, store=store, transport_for=lambda _attempt: Transport()
        ).launch(attempt)
        caught: ServiceError | None = None
        for _ in range(20):
            await asyncio.sleep(0)
            try:
                await service.tick()
            except ServiceError as exc:
                caught = exc
                break
        assert str(caught) == "daemon Worker completion persistence failed"
        assert store.state == "submitted"
        with pytest.raises(ServiceError, match="completion persistence failed"):
            await service.tick()
        with pytest.raises(ServiceError, match="not accepting mutations"):
            service.submit_mutation(lambda: None)
        await service.close()

    asyncio.run(case())


def test_acp_cleanup_failure_cannot_persist_succeeded_reply(tmp_path: Path) -> None:
    attempt = {
        "attempt_id": "mat_0123456789ab", "mission_id": MISSION_ID,
        "step_id": "step_1", "agent_id": "planner", "configured_transport": "acp",
        "dispatch_key": "dsp_" + "a" * 32, "state": "prepared",
    }

    class Store:
        attempt_state = "prepared"
        reply_count = 0

        def claim_mission_attempt_admission(self, **_kwargs):
            self.attempt_state = "admitting"
            return {**attempt, "state": "admitting",
                    "admission_claim_id": "adm_0123456789ab"}

        def mark_mission_attempt_admission_ambiguous(self, **_kwargs):
            raise AssertionError("completion failure is not admission ambiguity")

        def record_mission_attempt_submitted(self, **_kwargs):
            assert self.attempt_state == "admitting"
            self.attempt_state = "submitted"
            return {
                **attempt,
                "state": "submitted",
                "admission_claim_id": "adm_0123456789ab",
            }

        def mark_acp_mission_attempt_completion_ambiguous(self, **kwargs):
            assert self.attempt_state == "submitted"
            assert kwargs["completion_stage"] == "cleanup"
            self.attempt_state = "ambiguous"

        def record_acp_mission_attempt_completion(self, **_kwargs):
            self.reply_count += 1
            self.attempt_state = "succeeded"

    store = Store()

    class Sink:
        fragments = [
            "\n".join((
                f"handoff_token: {attempt['dispatch_key']}", "status: completed",
                "summary: done", "verification: ok", "risks: none",
                "next_steps: review",
            ))
        ]
        permission_seen = False
        session_state = "busy"

        async def append_update(self, *_args): return None
        async def append_permission(self, *_args): return None
        async def append_permission_decision(self, *_args): return None
        async def activate(self, *_args): return None
        async def finish(self, *_args): self.session_state = "ready"
        async def disconnect(self, _reason): raise OSError("save failed")

    sink = Sink()

    class Transport:
        async def initialize(self): return object()
        async def new_session(self):
            return SimpleNamespace(native_session_id="native-cleanup-failed")
        async def prompt(self, *_args):
            return SimpleNamespace(stop_reason="end_turn")
        async def close(self): return None

    worker_transport = AcpWorkerTransport(
        argv=("fake-agent-acp",), workspace=tmp_path, prompt="prompt", sink=sink,
        transport_factory=lambda *_args, **_kwargs: Transport(),
    )

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer([]), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        DaemonWorkerCoordinator(
            service=service, store=store,
            transport_for=lambda _attempt: worker_transport,
        ).launch(attempt)
        for _ in range(20):
            await asyncio.sleep(0)
            await service.tick()
            if store.attempt_state == "ambiguous":
                break
        assert sink.session_state == "ready"
        assert store.attempt_state == "ambiguous"
        assert store.reply_count == 0
        await service.close()

    asyncio.run(case())


@pytest.mark.parametrize(
    "reported_stage, expected_stage",
    [
        ("prompt", "prompt"),
        ("update", "update"),
        ("parse", "parse"),
        ("finish", "finish"),
        ("cleanup", "cleanup"),
        ("/private/invalid", "prompt"),
    ],
)
def test_acp_coordinator_records_submitted_completion_stage_as_ambiguity(
    reported_stage: str, expected_stage: str
) -> None:
    attempt = {
        "attempt_id": "mat_0123456789ab", "mission_id": MISSION_ID,
        "step_id": "step_1", "agent_id": "planner", "configured_transport": "acp",
        "dispatch_key": "dsp_" + "a" * 32, "state": "prepared",
    }

    class Store:
        state = "prepared"
        recorded_stage: str | None = None

        def claim_mission_attempt_admission(self, **_kwargs):
            self.state = "admitting"
            return {
                **attempt, "state": "admitting",
                "admission_claim_id": "adm_0123456789ab",
            }

        def record_mission_attempt_submitted(self, **_kwargs):
            assert self.state == "admitting"
            self.state = "submitted"
            return {
                **attempt, "state": "submitted",
                "admission_claim_id": "adm_0123456789ab",
            }

        def mark_mission_attempt_admission_ambiguous(self, **_kwargs):
            raise AssertionError("completion must not become admission ambiguity")

        def mark_acp_mission_attempt_completion_ambiguous(self, **kwargs):
            assert self.state == "submitted"
            self.recorded_stage = kwargs["completion_stage"]
            self.state = "ambiguous"

    store = Store()

    class CompletionFailure(RuntimeError):
        completion_stage = reported_stage

    class Transport:
        calls = 0

        async def admit(self, claimed):
            self.calls += 1
            return SubmittedReceipt("receipt-1", claimed["dispatch_key"], "session-created")

        async def complete(self, _submitted, _receipt):
            self.calls += 1
            raise CompletionFailure("command /private/project --token secret")

    transport = Transport()

    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer([]), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        DaemonWorkerCoordinator(
            service=service, store=store, transport_for=lambda _attempt: transport
        ).launch(attempt)
        for _ in range(20):
            await asyncio.sleep(0)
            await service.tick()
            if store.state == "ambiguous":
                break
        assert store.state == "ambiguous"
        assert store.recorded_stage == expected_stage
        assert transport.calls == 2
        await service.close()

    asyncio.run(case())


def test_acp_shutdown_while_prompt_is_in_flight_leaves_durable_admission_claim() -> None:
    prompt_started: asyncio.Event
    never_returns: asyncio.Event
    prompt_cancelled: asyncio.Event

    class Store:
        durable_state = "prepared"

        def claim_mission_attempt_admission(self, **kwargs):
            del kwargs
            self.durable_state = "admitting"
            return {
                "attempt_id": "mat_0123456789ab", "mission_id": MISSION_ID,
                "step_id": "step_1", "agent_id": "planner",
                "configured_transport": "acp", "dispatch_key": "dsp_" + "b" * 32,
                "state": "admitting", "admission_claim_id": "adm_0123456789ab",
            }

        def record_acp_mission_attempt_completion(self, **kwargs):
            del kwargs
            self.durable_state = "succeeded"

        def record_mission_attempt_submitted(self, **kwargs):
            del kwargs
            assert self.durable_state == "admitting"
            self.durable_state = "submitted"
            return {
                "attempt_id": "mat_0123456789ab", "mission_id": MISSION_ID,
                "step_id": "step_1", "agent_id": "planner",
                "configured_transport": "acp", "dispatch_key": "dsp_" + "b" * 32,
                "state": "submitted", "admission_claim_id": "adm_0123456789ab",
            }

    store = Store()

    class Transport:
        async def admit(self, claimed):
            return SubmittedReceipt("receipt-1", claimed["dispatch_key"], "session-created")

        async def complete(self, claimed, receipt):
            del claimed, receipt
            prompt_started.set()
            try:
                await never_returns.wait()
                raise AssertionError("unreachable")
            finally:
                prompt_cancelled.set()

    async def case() -> None:
        nonlocal prompt_started, never_returns, prompt_cancelled
        prompt_started = asyncio.Event()
        never_returns = asyncio.Event()
        prompt_cancelled = asyncio.Event()
        service = ProjectDaemonService(
            server=FakeServer([]), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        DaemonWorkerCoordinator(
            service=service, store=store, transport_for=lambda _attempt: Transport()
        ).launch({
            "attempt_id": "mat_0123456789ab", "mission_id": MISSION_ID,
            "step_id": "step_1", "agent_id": "planner", "configured_transport": "acp",
            "dispatch_key": "dsp_" + "b" * 32, "state": "prepared",
        })
        for _ in range(20):
            await asyncio.sleep(0)
            await service.tick()
            if prompt_started.is_set():
                break
        assert prompt_started.is_set()
        await service.close()
        assert store.durable_state == "submitted"
        assert prompt_cancelled.is_set()
        assert service.active_worker_task_count == 0

    asyncio.run(case())


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


def test_queued_mutation_wakes_service_without_waiting_for_poll_deadline() -> None:
    async def case() -> None:
        service = ProjectDaemonService(
            server=FakeServer([]), reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None, load_scheduler_facts=lambda: None,
            apply_transition=lambda _decision: None,
        )
        run = asyncio.create_task(service.run(poll_interval_seconds=5))
        while not service.started:
            await asyncio.sleep(0)

        def mutate() -> str:
            service.request_shutdown()
            return "applied"

        result = await asyncio.wait_for(service.submit_mutation(mutate), timeout=0.2)
        assert result == "applied"
        await asyncio.wait_for(run, timeout=0.2)

    asyncio.run(case())


class AdmissionStore:
    def __init__(self, mission: dict[str, object]) -> None:
        self.mission = mission

    def mission_by_id(self, mission_id: str) -> dict[str, object]:
        assert mission_id == self.mission["mission_id"]
        return dict(self.mission)

    def admit_mission_execution(
        self, mission_id: str, *, snapshot_hash: str
    ) -> dict[str, object]:
        assert mission_id == self.mission["mission_id"]
        assert snapshot_hash == self.mission["snapshot_hash"]
        return {"state": "admitted", "snapshot_hash": snapshot_hash}


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

    class DeferredStore:
        def record_mission_not_admitted(self, mission_id, *, snapshot_hash, blocker):
            return {
                "state": "confirmed_not_admitted",
                "snapshot_hash": snapshot_hash,
                "blocker": blocker,
                "recovery_command": f"agentdeck mission run --mission-id {mission_id} --confirm",
                "updated_at": "2026-07-13T00:00:00+00:00",
            }

    result = asyncio.run(
        admit_confirmed_mission(
            object(),
            object(),
            mission,
            connect_factory=unavailable,
            state_store=DeferredStore(),
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
        "durable_admission": {
            "state": "confirmed_not_admitted",
            "snapshot_hash": snapshot["execution_hash"],
            "blocker": "verified project daemon is unavailable",
            "recovery_command": f"agentdeck mission run --mission-id {MISSION_ID} --confirm",
            "updated_at": "2026-07-13T00:00:00+00:00",
        },
    }
    assert foreground_calls == []


def test_lost_admission_response_reports_existing_durable_admission() -> None:
    snapshot_hash = "sha256:" + "a" * 64
    mission = {
        "mission_id": MISSION_ID,
        "status": "preparing",
        "snapshot_hash": snapshot_hash,
        "execution_snapshot": {
            "mission": {"mission_id": MISSION_ID},
            "execution_hash": snapshot_hash,
        },
    }

    async def unavailable(*_args, **_kwargs):
        raise DaemonUnavailable("response lost")

    class Store:
        def record_mission_not_admitted(self, *_args, **_kwargs):
            return {
                "state": "admitted", "snapshot_hash": snapshot_hash,
                "blocker": None, "recovery_command": None, "updated_at": "now",
            }

    result = asyncio.run(
        admit_confirmed_mission(
            Path("."), object(), mission,
            connect_factory=unavailable, state_store=Store(),
        )
    )
    assert result["accepted"] is True
    assert result["state"] == "admitted"


def test_mission_governance_client_uses_controller_and_exact_preview() -> None:
    calls: list[tuple[str, dict[str, object], str | None, int | None]] = []

    class Client:
        async def request(
            self, method, params, *, lease_id=None, lease_generation=None
        ):
            calls.append((method, dict(params), lease_id, lease_generation))
            if method == "controller.acquire":
                return {"lease_id": "lse_" + "1" * 24, "generation": 7}
            if method == "mission.resume":
                return {
                    "accepted": True, "mission_id": MISSION_ID,
                    "state": "running", "preview_id": "gov_0123456789ab",
                }
            return {"released": True}

        async def close(self):
            calls.append(("close", {}, None, None))

    async def connect(*_args, **_kwargs):
        return Client()

    result = asyncio.run(
        govern_mission(
            Path("."), object(), mission_id=MISSION_ID, action="resume",
            preview_id="gov_0123456789ab", connect_factory=connect,
        )
    )
    assert result["state"] == "running"
    assert calls[1] == (
        "mission.resume",
        {"mission_id": MISSION_ID, "preview_id": "gov_0123456789ab"},
        "lse_" + "1" * 24,
        7,
    )
    assert calls[-2][0] == "controller.release"
    assert calls[-1][0] == "close"


def test_mission_governance_client_reuses_deterministic_logical_identity() -> None:
    acquired_client_ids: list[str] = []

    class Client:
        async def request(
            self, method, params, *, lease_id=None, lease_generation=None
        ):
            del lease_id, lease_generation
            if method == "controller.acquire":
                acquired_client_ids.append(params["client_id"])
                return {
                    "lease_id": "lse_" + str(len(acquired_client_ids)) * 24,
                    "generation": len(acquired_client_ids),
                }
            if method == "mission.resume":
                return {"state": "pending"}
            return {"released": True}

        async def close(self):
            return None

    async def connect(*_args, **_kwargs):
        return Client()

    async def run() -> None:
        for preview_id in (None, "gov_0123456789ab"):
            await govern_mission(
                Path("/tmp/project"), object(), mission_id=MISSION_ID,
                action="resume", preview_id=preview_id, connect_factory=connect,
            )
        await govern_mission(
            Path("/tmp/project"), object(), mission_id="mis_0123456789ac",
            action="resume", connect_factory=connect,
        )

    asyncio.run(run())
    assert acquired_client_ids[0] == acquired_client_ids[1]
    assert acquired_client_ids[2] != acquired_client_ids[0]
    assert acquired_client_ids[0].startswith("client_mission_governance_")


def test_previous_handoff_resolves_exact_frozen_predecessor() -> None:
    token = "dsp_" + "1" * 32
    compact = _compact_handoff(token)
    attempt = {
        "mission_id": MISSION_ID,
        "attempt_id": "mat_" + "2" * 12,
        "step_id": "step_2",
        "agent_id": "reviewer",
    }
    state = {
        "missions": [{
            "mission_id": MISSION_ID,
            "execution_snapshot": {"mission": {"steps": [
                {"step_id": "step_1", "agent_id": "planner", "position": 1},
                {"step_id": "step_2", "agent_id": "reviewer", "position": 2},
            ]}},
        }],
        "mission_attempts": [
            {
                "mission_id": MISSION_ID,
                "attempt_id": "mat_" + "1" * 12,
                "step_id": "step_1",
                "agent_id": "planner",
                "dispatch_key": token,
                "state": "succeeded",
            },
            attempt,
        ],
        "mission_worker_replies": [{
            "mission_id": MISSION_ID,
            "attempt_id": "mat_" + "1" * 12,
            "reply_id": "mrp_" + "1" * 12,
            "dispatch_key": token,
            "state": "validated",
            "canonical_handoff": compact,
        }],
        "mission_handoffs": [{
            "mission_id": MISSION_ID,
            "attempt_id": "mat_" + "1" * 12,
            "handoff_id": "hof_" + "1" * 12,
            "reply_id": "mrp_" + "1" * 12,
            "state": "recorded",
            "canonical_handoff": compact,
        }],
    }

    assert resolve_previous_handoff(state, attempt) == compact
    first = dict(attempt, attempt_id="mat_" + "1" * 12, step_id="step_1", agent_id="planner")
    assert resolve_previous_handoff(state, first) is None


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_handoff",
        "duplicate_attempt",
        "wrong_mission",
        "reply_not_validated",
        "content_drift",
    ],
)
def test_previous_handoff_fails_closed_on_ambiguous_or_invalid_lineage(
    mutation: str,
) -> None:
    token = "dsp_" + "1" * 32
    compact = _compact_handoff(token)
    attempt = {
        "mission_id": MISSION_ID,
        "attempt_id": "mat_" + "2" * 12,
        "step_id": "step_2",
        "agent_id": "reviewer",
    }
    previous = {
        "mission_id": MISSION_ID,
        "attempt_id": "mat_" + "1" * 12,
        "step_id": "step_1",
        "agent_id": "planner",
        "dispatch_key": token,
        "state": "succeeded",
    }
    reply = {
        "mission_id": MISSION_ID,
        "attempt_id": previous["attempt_id"],
        "reply_id": "mrp_" + "1" * 12,
        "dispatch_key": token,
        "state": "validated",
        "canonical_handoff": compact,
    }
    handoff = {
        "mission_id": MISSION_ID,
        "attempt_id": previous["attempt_id"],
        "handoff_id": "hof_" + "1" * 12,
        "reply_id": reply["reply_id"],
        "state": "recorded",
        "canonical_handoff": compact,
    }
    state = {
        "missions": [{
            "mission_id": MISSION_ID,
            "execution_snapshot": {"mission": {"steps": [
                {"step_id": "step_1", "agent_id": "planner", "position": 1},
                {"step_id": "step_2", "agent_id": "reviewer", "position": 2},
            ]}},
        }],
        "mission_attempts": [previous, attempt],
        "mission_worker_replies": [reply],
        "mission_handoffs": [handoff],
    }
    if mutation == "missing_handoff":
        state["mission_handoffs"] = []
    elif mutation == "duplicate_attempt":
        state["mission_attempts"].append(dict(previous, attempt_id="mat_" + "3" * 12))
    elif mutation == "wrong_mission":
        state["mission_handoffs"][0] = dict(handoff, mission_id="mis_" + "f" * 12)
    elif mutation == "reply_not_validated":
        state["mission_worker_replies"][0] = dict(reply, state="received")
    else:
        state["mission_handoffs"][0] = dict(
            handoff,
            canonical_handoff=_compact_handoff(token, "different compact content"),
        )

    with pytest.raises(ServiceError, match="previous Worker handoff"):
        resolve_previous_handoff(state, attempt)
