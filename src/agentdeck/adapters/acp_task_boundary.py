"""Content-free observation and bounded cleanup for ACP background tasks."""
from __future__ import annotations

import asyncio
from typing import Any, Final, TypeVar


_PROMPT_CLEANUP_TIMEOUT_SECONDS: Final = 1.0
_T = TypeVar("_T")


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except BaseException:
        pass


def observe_background_task(task: asyncio.Task[_T]) -> asyncio.Task[_T]:
    task.add_done_callback(_consume_task_result)
    return task


def caller_cancellation_pending() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


async def cancel_background_task(task: asyncio.Task[Any]) -> bool:
    if task.done():
        return False
    task.cancel()
    caller_cancelled: asyncio.CancelledError | None = None
    try:
        done, _ = await asyncio.wait(
            {task}, timeout=_PROMPT_CLEANUP_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError as error:
        if caller_cancellation_pending():
            caller_cancelled = error
        done = set()
    if task not in done and not task.done():
        task.cancel()
        try:
            await asyncio.wait(
                {task}, timeout=_PROMPT_CLEANUP_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError as error:
            if caller_cancellation_pending() and caller_cancelled is None:
                caller_cancelled = error
    if caller_cancelled is not None:
        raise caller_cancelled
    return not task.done()


__all__ = [
    "caller_cancellation_pending", "cancel_background_task",
    "observe_background_task",
]
