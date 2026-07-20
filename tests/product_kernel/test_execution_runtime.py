from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import wraps

import pytest

from agentdeck.application.execution_runtime import (
    ActiveExecutionBinding,
    ExecutionBindingError,
    ExecutionReservation,
    ExecutionRuntimeStatus,
    ForegroundExecutionRuntime,
)
from agentdeck.application.project_lifecycle_service import (
    ProjectDispatchBlocked,
    ProjectLifecycleService,
)
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot
from agentdeck.ports.worker import WorkerHandle
from product_kernel.fakes import FrozenClock
from product_kernel.test_execution_coordinator import Harness, ScriptedWorker
from product_kernel.test_sqlite_execution_resume import NOW


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


class RecordingWorker:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def start_task(self, request):
        self.calls.append(("start", request))

    def stream_events(self, handle):
        self.calls.append(("stream", handle))

    async def respond_permission(self, handle, **kwargs):
        self.calls.append(("permission", handle, kwargs))

    async def cancel_task(self, handle, *, reason):
        self.calls.append(("cancel", handle, reason))

    async def collect_result(self, handle):
        self.calls.append(("collect", handle))


def binding_for(
    attempt_id: str = "att_1",
    task_id: str = "tsk_1",
    agent_id: str = "agt_1",
    session_id: str = "ses_acp_1",
    *,
    worker: RecordingWorker | None = None,
    handle: WorkerHandle | None = None,
) -> ActiveExecutionBinding:
    worker = RecordingWorker() if worker is None else worker
    handle = WorkerHandle(
        session_id, agent_id, task_id, attempt_id
    ) if handle is None else handle
    return ActiveExecutionBinding(
        attempt_id, task_id, agent_id, session_id, handle, worker
    )


def exit_snapshot(binding: ActiveExecutionBinding) -> ExitAttemptSnapshot:
    return ExitAttemptSnapshot(
        binding.attempt_id,
        binding.task_id,
        binding.agent_instance_id,
        1,
        AttemptState.RUNNING,
        binding.acp_session_id,
        False,
        None,
    )


@async_test
async def test_exact_runtime_binding_resolves_every_shared_lineage_field():
    runtime = ForegroundExecutionRuntime()
    binding = binding_for()

    runtime.bind(binding)

    assert runtime.resolve_exact(exit_snapshot(binding)) is binding
    runtime.release(binding.attempt_id, binding.worker_handle)


@async_test
@pytest.mark.parametrize(
    "field",
    [
        "attempt_id",
        "task_id",
        "agent_instance_id",
        "acp_session_id",
        "worker_handle",
        "worker",
    ],
)
async def test_runtime_rejects_binding_drift_without_worker_io(field):
    runtime = ForegroundExecutionRuntime()
    active = binding_for()
    runtime.bind(active)
    other = binding_for("att_2", "tsk_2", "agt_2", "ses_acp_2")
    values = {
        "attempt_id": other.attempt_id,
        "task_id": other.task_id,
        "agent_instance_id": other.agent_instance_id,
        "acp_session_id": other.acp_session_id,
        "worker_handle": other.worker_handle,
        "worker": other.worker,
    }
    drifted = replace(active, **{field: values[field]})

    with pytest.raises(ExecutionBindingError):
        runtime.bind(drifted)

    assert active.worker.calls == []
    assert other.worker.calls == []


@async_test
async def test_runtime_rejects_duplicate_attempt_and_reused_worker_or_handle():
    runtime = ForegroundExecutionRuntime()
    first = binding_for()
    runtime.bind(first)
    runtime.release(first.attempt_id, first.worker_handle)

    with pytest.raises(ExecutionBindingError):
        runtime.bind(binding_for("att_1", "tsk_2", "agt_2", "ses_acp_2"))
    with pytest.raises(ExecutionBindingError):
        runtime.bind(binding_for(
            "att_2", "tsk_2", "agt_2", "ses_acp_2", worker=first.worker
        ))
    with pytest.raises(ExecutionBindingError):
        runtime.bind(binding_for(
            "att_2", "tsk_2", "agt_2", "ses_acp_2",
            handle=first.worker_handle,
        ))


@async_test
async def test_release_replay_is_idempotent_only_for_the_exact_released_pair():
    runtime = ForegroundExecutionRuntime()
    first = binding_for()
    runtime.bind(first)
    runtime.release(first.attempt_id, first.worker_handle)

    runtime.release(first.attempt_id, first.worker_handle)
    with pytest.raises(ExecutionBindingError):
        runtime.release("att_other", first.worker_handle)

    second = binding_for("att_2", "tsk_2", "agt_2", "ses_acp_2")
    runtime.bind(second)
    with pytest.raises(ExecutionBindingError):
        runtime.release(first.attempt_id, first.worker_handle)
    assert runtime.resolve_exact(exit_snapshot(second)) is second


def test_runtime_rejects_cross_loop_resolve_and_release():
    runtime = ForegroundExecutionRuntime()
    binding = binding_for()

    async def bind() -> None:
        runtime.bind(binding)

    asyncio.run(bind())

    async def reject() -> None:
        with pytest.raises(ExecutionBindingError):
            runtime.resolve_exact(exit_snapshot(binding))
        with pytest.raises(ExecutionBindingError):
            runtime.release(binding.attempt_id, binding.worker_handle)

    asyncio.run(reject())


@async_test
async def test_runtime_rejects_an_equal_full_handle_used_by_an_earlier_attempt():
    runtime = ForegroundExecutionRuntime()
    first = binding_for()
    runtime.bind(first)
    runtime.release(first.attempt_id, first.worker_handle)

    with pytest.raises(ExecutionBindingError):
        runtime.bind(replace(first, worker=RecordingWorker()))


@async_test
async def test_empty_runtime_reports_only_pristine_mission_state():
    runtime = ForegroundExecutionRuntime()
    assert runtime.is_empty() is True
    binding = binding_for()
    runtime.bind(binding)
    runtime.release(binding.attempt_id, binding.worker_handle)
    assert runtime.is_empty() is False


def reservation_for(
    attempt_id="att_1", task_id="tsk_1", agent_id="agt_1", *, worker=None,
):
    return ExecutionReservation(
        attempt_id, task_id, agent_id,
        RecordingWorker() if worker is None else worker,
    )


@async_test
async def test_reservation_is_invisible_until_exact_atomic_activation():
    runtime = ForegroundExecutionRuntime()
    reservation = reservation_for()
    handle = WorkerHandle("ses_acp_1", "agt_1", "tsk_1", "att_1")
    snapshot = ExitAttemptSnapshot(
        "att_1", "tsk_1", "agt_1", 1, AttemptState.RUNNING,
        "ses_acp_1", False, None,
    )

    runtime.reserve(reservation)
    assert runtime.status() == ExecutionRuntimeStatus(
        "reserved", "att_1", "tsk_1", "agt_1", False
    )
    with pytest.raises(ExecutionBindingError):
        runtime.resolve_exact(snapshot)
    binding = runtime.claim_handle(reservation, handle)
    assert runtime.status().has_handle is True
    with pytest.raises(ExecutionBindingError):
        runtime.resolve_exact(snapshot)

    runtime.activate(reservation, binding)

    assert runtime.resolve_exact(snapshot) is binding


@async_test
async def test_quarantine_owner_is_bounded_invisible_and_cannot_be_cleared():
    runtime = ForegroundExecutionRuntime()
    reservation = reservation_for()
    handle = WorkerHandle("ses_acp_1", "agt_1", "tsk_1", "att_1")
    runtime.reserve(reservation)
    runtime.claim_handle(reservation, handle)
    runtime.quarantine(reservation)
    status = runtime.status()
    snapshot = ExitAttemptSnapshot(
        "att_1", "tsk_1", "agt_1", 1, AttemptState.RUNNING,
        "ses_acp_1", False, None,
    )

    with pytest.raises(ExecutionBindingError):
        runtime.resolve_exact(snapshot)
    with pytest.raises(ExecutionBindingError):
        runtime.reserve(reservation_for("att_2", "tsk_2", "agt_2"))
    with pytest.raises(ExecutionBindingError):
        runtime.bind(binding_for("att_2", "tsk_2", "agt_2", "ses_acp_2"))
    with pytest.raises(ExecutionBindingError):
        runtime.release("att_1", handle)
    with pytest.raises(ExecutionBindingError):
        runtime.rollback(reservation)

    assert runtime.status() == status
    assert status == ExecutionRuntimeStatus(
        "quarantined", "att_1", "tsk_1", "agt_1", True
    )
    assert runtime.is_empty() is False


@pytest.mark.parametrize("status", [
    ("quarantined", "raw-attempt", "tsk_1", "agt_1", False),
    ("idle", None, None, None, True),
])
def test_runtime_status_rejects_invalid_bounded_state(status):
    with pytest.raises(ValueError):
        ExecutionRuntimeStatus(*status)


class ReturnedHandleWorker(ScriptedWorker):
    def __init__(self, harness, task_name, returned_handle, *, cancel_fails=False):
        super().__init__(harness, task_name)
        self.returned_handle = returned_handle
        self.cancel_fails = cancel_fails
        self.cancel_calls = []

    async def start_task(self, request):
        self._harness.started_tasks.append(self._task_name)
        self._harness.requests.append(request)
        self._request = request
        self._handle = self.returned_handle
        return self.returned_handle

    async def cancel_task(self, handle, *, reason):
        self.cancel_calls.append((handle, reason))
        if self.cancel_fails:
            raise RuntimeError("/private/cancel/raw prompt")


@async_test
@pytest.mark.parametrize(
    ("cancel_fails", "terminal_fails", "expected_status"),
    [(False, False, "idle"), (True, False, "quarantined"),
     (False, True, "quarantined")],
)
async def test_equal_old_handle_cancels_only_new_worker_or_quarantines(
    cancel_fails, terminal_fails, expected_status,
):
    harness = Harness()
    first = ScriptedWorker(harness, "implementation")
    second = None
    lease_states = []

    class CheckingLifecycle(ProjectLifecycleService):
        @asynccontextmanager
        async def dispatch_lease(self):
            async with super().dispatch_lease():
                yield
                lease_states.append(harness.runtime.status().state)

    harness.lifecycle = harness.service._lifecycle = CheckingLifecycle(
        store=harness.store, clock=FrozenClock(NOW), session_id="ses_1"
    )

    def factory(task):
        nonlocal second
        if task.name == "implementation":
            return first
        second = ReturnedHandleWorker(
            harness, task.name, first._handle, cancel_fails=cancel_fails
        )
        return second

    harness.service._worker_factory = factory
    if terminal_fails:
        original = harness.service._persist_terminal_attempt

        def persist(attempt, *args, **kwargs):
            if attempt.reason == "worker_handle_lineage_invalid":
                raise RuntimeError("/private/terminal/raw prompt")
            return original(attempt, *args, **kwargs)

        harness.service._persist_terminal_attempt = persist

    result = await harness.run()

    assert second is not None
    assert second.cancel_calls == [
        (first._handle, "execution_binding_rejected")
    ]
    if terminal_fails:
        assert result.diagnostic.code == "terminal_attempt_persistence_failed"
        assert "/private/terminal" not in result.diagnostic.cause
    else:
        assert result.attempts[-1].state is AttemptState.OUTCOME_UNKNOWN
        assert result.diagnostic.code == "worker_handle_lineage_invalid"
    assert harness.runtime.status().state == expected_status
    assert lease_states[-1] == expected_status
    assert harness.started_tasks == ["implementation", "review"]


@async_test
async def test_opaque_handle_is_never_cancelled_and_quarantines_bounded_owner():
    harness = Harness()
    first = ScriptedWorker(harness, "implementation")
    opaque = object()
    second = None

    def factory(task):
        nonlocal second
        if task.name == "implementation":
            return first
        second = ReturnedHandleWorker(harness, task.name, opaque)
        return second

    harness.service._worker_factory = factory

    result = await harness.run()

    assert second.cancel_calls == []
    assert result.attempts[-1].state is AttemptState.OUTCOME_UNKNOWN
    assert harness.runtime.status() == ExecutionRuntimeStatus(
        "quarantined", result.attempts[-1].attempt_id,
        harness.draft.tasks[1].task_id,
        harness.draft.tasks[1].agent_instance_id, False,
    )
    assert harness.runtime.is_empty() is False


@async_test
async def test_second_gate_terminal_failure_is_fixed_and_content_free():
    harness = Harness()

    class BlockSecondCheck(ProjectLifecycleService):
        calls = 0

        def require_dispatchable(self):
            self.calls += 1
            if self.calls == 2:
                raise ProjectDispatchBlocked("/private/hostile/prompt")
            return super().require_dispatchable()

    harness.lifecycle = harness.service._lifecycle = BlockSecondCheck(
        store=harness.store, clock=FrozenClock(NOW), session_id="ses_1"
    )

    def fail_terminal(*args, **kwargs):
        raise RuntimeError("/private/terminal/path raw prompt")

    harness.service._persist_terminal_attempt = fail_terminal

    result = await harness.run()

    assert result.diagnostic.code == "terminal_attempt_persistence_failed"
    assert "/private/terminal/path" not in result.diagnostic.cause
    assert harness.started_tasks == [] and harness.requests == []
