"""Caller-cancellation ownership at the approval-to-Worker boundary."""

from __future__ import annotations

import asyncio

from agentdeck.ports.worker import Worker, WorkerHandle


async def cancel_worker_for_caller(
    worker: Worker, handle: WorkerHandle,
) -> None:
    task = asyncio.current_task()
    if task is None or task.cancelling() == 0:
        return
    try:
        await worker.cancel_task(handle, reason="execution_caller_cancelled")
    except BaseException:
        pass


__all__ = ["cancel_worker_for_caller"]
