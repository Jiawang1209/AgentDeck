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


async def cancel_background_task(task: asyncio.Task[Any]) -> bool:
    if task.done():
        return False
    task.cancel()
    try:
        await asyncio.wait({task}, timeout=_PROMPT_CLEANUP_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return True
    return False


__all__ = ["cancel_background_task", "observe_background_task"]
