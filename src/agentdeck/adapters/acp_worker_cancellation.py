"""Caller-cancellation-aware ACP Worker cleanup facts."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agentdeck.adapters.acp_task_boundary import caller_cancellation_pending
from agentdeck.ports.worker import WorkerCancellationError


@dataclass(frozen=True)
class WorkerCancellationFacts:
    failure: tuple[str, bool] | None
    fatal: BaseException | None
    caller_cancelled: asyncio.CancelledError | None


async def resolve_worker_cancellation(
    notify: Awaitable[None], cleanup: Callable[[], Awaitable[bool]],
) -> WorkerCancellationFacts:
    failure: tuple[str, bool] | None = None
    fatal: BaseException | None = None
    caller_cancelled: asyncio.CancelledError | None = None
    try:
        await notify
    except WorkerCancellationError as error:
        failure = (error.code, error.outcome_known)
    except asyncio.CancelledError as error:
        if caller_cancellation_pending():
            caller_cancelled = error
        else:
            failure = ("cancel_timeout", False)
    except BaseException as error:
        if isinstance(error, Exception) and not isinstance(error, MemoryError):
            failure = ("transport_disconnected", False)
        else:
            fatal = error
    try:
        cleanup_pending = await cleanup()
    except asyncio.CancelledError as error:
        if caller_cancellation_pending():
            caller_cancelled = caller_cancelled or error
        cleanup_pending = True
    if cleanup_pending:
        failure = ("cancel_timeout", False)
    return WorkerCancellationFacts(failure, fatal, caller_cancelled)


__all__ = ["WorkerCancellationFacts", "resolve_worker_cancellation"]
