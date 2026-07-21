"""Exact race between automatic ownership gates and runtime release."""

from __future__ import annotations

import asyncio

from agentdeck.application import execution_authority as _authority
from agentdeck.application.execution_resume import ExecutionResult
from agentdeck.ports.worker import Worker, WorkerHandle


class AutomaticAuthorityReleased(RuntimeError):
    """The exact Worker binding was explicitly released before automation resumed."""


async def _cancel(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def wait_for_automatic_or_release(
    gate_wait, runtime, attempt_id: str, handle: WorkerHandle,
) -> None:
    gate_task = asyncio.create_task(gate_wait(attempt_id))
    release_task = asyncio.create_task(runtime.wait_released(attempt_id, handle))
    done, _ = await asyncio.wait(
        (gate_task, release_task), return_when=asyncio.FIRST_COMPLETED,
    )
    if release_task in done:
        await _cancel(gate_task)
        release_task.result()
        raise AutomaticAuthorityReleased("automatic_authority_released")
    await _cancel(release_task)
    gate_task.result()


async def await_operation_or_release(
    operation, runtime, attempt_id: str, handle: WorkerHandle,
):
    operation_task = asyncio.create_task(operation)
    release_task = asyncio.create_task(runtime.wait_released(attempt_id, handle))
    done, _ = await asyncio.wait(
        (operation_task, release_task), return_when=asyncio.FIRST_COMPLETED,
    )
    if release_task in done:
        await _cancel(operation_task)
        release_task.result()
        raise AutomaticAuthorityReleased("automatic_authority_released")
    await _cancel(release_task)
    return operation_task.result()


def released_execution_result(
    service, confirmed, attempts, evidence, handoffs, revision_task, task, handle,
) -> ExecutionResult:
    active = attempts[-1]
    terminal = active.interrupt("product_exit_confirmed")
    durable = service._store.load_aggregate("attempts", active.attempt_id)
    expected = _authority.attempt_snapshot(terminal, task, handle.session_id)
    if durable != expected:
        terminal = active.unknown_outcome("exit_release_authority_drifted")
        code = "exit_release_authority_drifted"
    else:
        service.takeover_control.interrupt_from_exit(active.attempt_id)
        code = "product_exit_confirmed"
    attempts[-1] = terminal
    return ExecutionResult(
        tuple(attempts), tuple(evidence), tuple(handoffs), revision_task,
        service._diagnostic(
            code, confirmed, task, terminal, "the confirmed exit released the Worker",
        ),
    )


class ControlledWorker:
    """Worker facade that gates automatic responses, cancellation, and collection."""

    def __init__(self, control, runtime, worker: Worker, handle: WorkerHandle) -> None:
        self._control, self._runtime = control, runtime
        self._worker, self._handle = worker, handle

    async def start_task(self, _request):
        raise RuntimeError("controlled Worker cannot start another task")

    def stream_events(self, handle):
        return self._worker.stream_events(handle)

    async def _wait(self) -> None:
        await wait_for_automatic_or_release(
            self._control.wait_for_automatic_input, self._runtime,
            self._handle.attempt_id, self._handle,
        )

    async def respond_permission(self, handle, **response) -> None:
        await self._wait()
        await self._worker.respond_permission(handle, **response)

    async def cancel_task(self, handle, **request) -> None:
        await self._wait()
        await self._worker.cancel_task(handle, **request)

    async def collect_result(self, handle):
        await self._wait()
        return await self._worker.collect_result(handle)


__all__ = [
    "AutomaticAuthorityReleased", "ControlledWorker",
    "await_operation_or_release", "released_execution_result",
    "wait_for_automatic_or_release",
]
