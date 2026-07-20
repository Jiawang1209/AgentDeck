from __future__ import annotations

import asyncio
from dataclasses import replace
from functools import wraps

import pytest

from agentdeck.application.execution_runtime import (
    ActiveExecutionBinding,
    ExecutionBindingError,
    ForegroundExecutionRuntime,
)
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot
from agentdeck.ports.worker import WorkerHandle


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
