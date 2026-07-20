"""Async terminal and task ownership helpers for the Product Shell."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TextIO


_CHILD_SETTLE_TIMEOUT_SECONDS = 1.0


class AsyncTerminalReader:
    def __init__(self, stream: TextIO, prompt_stream: TextIO) -> None:
        self._stream, self._prompt_stream = stream, prompt_stream

    async def __call__(self, prompt: str) -> str:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        descriptor = self._stream.fileno()
        self._prompt_stream.write(prompt)
        self._prompt_stream.flush()

        def ready() -> None:
            if future.done():
                return
            try:
                line = self._stream.readline()
                if line == "":
                    future.set_exception(EOFError())
                else:
                    future.set_result(line.rstrip("\r\n"))
            except BaseException as error:
                future.set_exception(error)

        loop.add_reader(descriptor, ready)
        try:
            return await future
        finally:
            loop.remove_reader(descriptor)


async def _shield_cleanup(
    cleanup: Coroutine[Any, Any, None],
    caller_cancelled: asyncio.CancelledError | None,
) -> None:
    cleanup_task = asyncio.create_task(cleanup)
    first_cancelled = caller_cancelled
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as error:
            if first_cancelled is None:
                first_cancelled = error
    await asyncio.gather(cleanup_task, return_exceptions=True)
    if first_cancelled is not None:
        raise first_cancelled


async def _cancel_and_collect(*tasks: asyncio.Task) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def collect_iteration_tasks(
    *tasks: asyncio.Task,
    caller_cancelled: asyncio.CancelledError | None = None,
) -> None:
    await _shield_cleanup(
        _cancel_and_collect(*tasks), caller_cancelled,
    )


async def cancel_and_settle_owned(task: asyncio.Task | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    done, _ = await asyncio.wait(
        (task,), timeout=_CHILD_SETTLE_TIMEOUT_SECONDS,
    )
    if task not in done:
        task.cancel()
        await asyncio.sleep(0)
    if task.done():
        await asyncio.gather(task, return_exceptions=True)


async def settle_owned_after_caller_cancel(
    task: asyncio.Task | None,
    caller_cancelled: asyncio.CancelledError,
) -> None:
    await _shield_cleanup(
        cancel_and_settle_owned(task), caller_cancelled,
    )


__all__ = [
    "AsyncTerminalReader",
    "cancel_and_settle_owned",
    "collect_iteration_tasks",
    "settle_owned_after_caller_cancel",
]
