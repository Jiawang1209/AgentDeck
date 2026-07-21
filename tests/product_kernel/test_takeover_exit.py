from __future__ import annotations

import asyncio
from contextlib import suppress

from agentdeck.application.takeover_control import TakeoverControl
from agentdeck.kernel.execution import Attempt
from agentdeck.kernel.mission import AgentRole, TaskDefinition
from agentdeck.kernel.permissions import Effect, PermissionProfile, PermissionScope
from agentdeck.ports.observer import ObserverCursor
from product_kernel.fakes import FrozenClock
from product_kernel.test_product_exit_acp_integration import ExitHarness
from product_kernel.test_takeover import Harness, NOW, project_evidence


def test_automatic_cancellation_waits_during_human_ownership() -> None:
    async def scenario() -> None:
        harness = Harness()
        task = None
        try:
            attempt_id = await harness.start()
            calls = []

            async def cancel(handle, **request):
                calls.append((handle, request))

            harness.worker.cancel_task = cancel
            controlled = harness.service.takeover_control.controlled_worker(
                harness.worker, harness.worker.handle,
            )
            assert (await harness.service.takeover(attempt_id)).accepted is True
            task = asyncio.create_task(controlled.cancel_task(
                harness.worker.handle, reason="permission_denied",
            ))
            await asyncio.sleep(0)
            assert calls == [] and not task.done()
            assert (await harness.service.return_control(attempt_id)).accepted is True
            await task
            assert len(calls) == 1
        finally:
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await harness.close()

    asyncio.run(scenario())


def _exit_task() -> TaskDefinition:
    return TaskDefinition(
        "tsk_1", "implementation", AgentRole.IMPLEMENTER, "codex-cli", "agt_1",
        "acp://route", (), frozenset({Effect.READ}), ("result",), ("accepted",),
    )


def test_confirmed_exit_releases_exact_takeover_waiter_and_interrupts_owner(
    tmp_path,
) -> None:
    async def scenario() -> None:
        harness = ExitHarness(tmp_path)
        try:
            harness.bind()
            task = _exit_task()
            attempt = Attempt.pending("att_1", "tsk_1", 1).start()
            permission = PermissionScope(
                PermissionProfile.APPROVE_FOR_ME, frozenset({Effect.READ}),
            )
            source = project_evidence()
            control = TakeoverControl(
                store=harness.store, clock=FrozenClock(NOW), runtime=harness.runtime,
                project_evidence=lambda: source,
                permission_snapshot=lambda: permission,
                observer_cursor=lambda: ObserverCursor(
                    "prj_1", "ses_acp_1", "agt_1", "tsk_1", "att_1", "acp",
                    1, "evt_started", "b" * 64,
                ),
            )
            from product_kernel.test_takeover import Harness as MissionHarness
            mission = MissionHarness()
            control.arm(
                product_session_id="ses_1", confirmed=mission.confirmed, task=task,
                attempt=attempt, permission=permission, acp_session_id="ses_acp_1",
            )
            assert (await control.takeover("att_1")).accepted is True
            await harness.coordinator.decline(
                harness.request.request_id, harness.request.attempt_hash,
            )
            decision = harness.exit_service.request_exit()
            request = decision.request
            assert request is not None
            released = asyncio.create_task(
                harness.runtime.wait_released("att_1", harness.handle)
            )
            result = await harness.coordinator.confirm(
                request.request_id, request.attempt_hash,
            )
            await released
            control.interrupt_from_exit("att_1")
            assert result.should_exit is True
            assert harness.worker.cancel_calls == [
                (harness.handle, "product_exit_confirmed"),
            ]
            assert harness.attempt_state() == "interrupted"
            assert harness.store.load_aggregate("takeover_ownership", "att_1")["state"] \
                == "interrupted"
            returned = await control.return_control("att_1")
            assert returned.accepted is False
        finally:
            harness.close()

    asyncio.run(scenario())

