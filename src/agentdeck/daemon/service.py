"""Authoritative bounded project-daemon service composition.

The service serializes every durable mutation and external-effect completion
through one queue.  Worker I/O may run concurrently, but it can only enqueue a
completion callback; it never receives the service's mutation authority.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import inspect
import math
import re
from typing import Any, Protocol

from .scheduler import SchedulerDecision, SchedulerFacts, schedule_gate


class ServiceError(RuntimeError):
    """The daemon service could not preserve its authority boundary."""


class ServiceServer(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...


Callback = Callable[[], object | Awaitable[object]]
TransitionCallback = Callable[[SchedulerDecision], object | Awaitable[object]]


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def validate_confirmed_mission_admission(
    store: object, params: object
) -> dict[str, object]:
    """Revalidate client-submitted authority against the daemon's durable copy."""
    if type(params) is not dict or set(params) != {
        "mission_id",
        "snapshot_hash",
        "execution_snapshot",
    }:
        raise ServiceError("frozen Mission admission is invalid")
    mission_id = params["mission_id"]
    snapshot_hash = params["snapshot_hash"]
    snapshot = params["execution_snapshot"]
    if (
        type(mission_id) is not str
        or re.fullmatch(r"mis_[0-9a-f]{12}", mission_id) is None
        or type(snapshot_hash) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot_hash) is None
        or not isinstance(snapshot, Mapping)
        or not callable(getattr(store, "mission_by_id", None))
    ):
        raise ServiceError("frozen Mission admission is invalid")
    try:
        persisted = store.mission_by_id(mission_id)
    except Exception:
        raise ServiceError("frozen Mission admission is invalid") from None
    submitted_snapshot = _thaw_json(snapshot)
    if (
        type(persisted) is not dict
        or persisted.get("status") != "preparing"
        or persisted.get("snapshot_hash") != snapshot_hash
        or persisted.get("execution_snapshot") != submitted_snapshot
        or submitted_snapshot.get("execution_hash") != snapshot_hash
        or not isinstance(submitted_snapshot.get("mission"), dict)
        or submitted_snapshot["mission"].get("mission_id") != mission_id
    ):
        raise ServiceError("frozen Mission admission drift")
    return {
        "accepted": True,
        "mission_id": mission_id,
        "snapshot_hash": snapshot_hash,
        "state": "admitted",
    }


async def _call(callback: Callable[..., object], *args: object) -> object:
    value = callback(*args)
    if inspect.isawaitable(value):
        return await value
    return value


class ProjectDaemonService:
    """Compose startup recovery, RPC serving, and one bounded transition tick."""

    def __init__(
        self,
        *,
        server: ServiceServer,
        reconcile_all: Callback,
        flush_safe_outboxes: Callback,
        load_scheduler_facts: Callable[
            [], SchedulerFacts | None | Awaitable[SchedulerFacts | None]
        ],
        apply_transition: TransitionCallback,
    ) -> None:
        if not all(
            callable(value)
            for value in (
                reconcile_all,
                flush_safe_outboxes,
                load_scheduler_facts,
                apply_transition,
            )
        ):
            raise TypeError("daemon service callbacks must be callable")
        if not callable(getattr(server, "start", None)) or not callable(
            getattr(server, "close", None)
        ):
            raise TypeError("daemon service server is invalid")
        self.server = server
        self._reconcile_all = reconcile_all
        self._flush_safe_outboxes = flush_safe_outboxes
        self._load_scheduler_facts = load_scheduler_facts
        self._apply_transition = apply_transition
        self._queue: asyncio.Queue[
            tuple[Callback, asyncio.Future[object] | None]
        ] = asyncio.Queue()
        self._worker_tasks: set[asyncio.Task[None]] = set()
        self._started = False
        self._closed = False
        self._shutdown = asyncio.Event()

    @property
    def started(self) -> bool:
        return self._started and not self._closed

    async def start(self) -> None:
        if self._closed:
            raise ServiceError("daemon service is closed")
        if self._started:
            return
        try:
            await _call(self._reconcile_all)
        except Exception:
            raise ServiceError("startup reconciliation failed") from None
        try:
            await self.server.start()
        except Exception:
            raise ServiceError("daemon server startup failed") from None
        self._started = True

    def submit_mutation(self, callback: Callback) -> asyncio.Future[object]:
        if not self.started or not callable(callback):
            raise ServiceError("daemon service is not accepting mutations")
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((callback, future))
        return future

    def start_worker_io(
        self,
        operation: Awaitable[Any],
        *,
        on_completion: Callable[[Any], object | Awaitable[object]],
    ) -> None:
        if not self.started or not inspect.isawaitable(operation) or not callable(
            on_completion
        ):
            if inspect.iscoroutine(operation):
                operation.close()
            raise ServiceError("Worker I/O submission is invalid")

        async def run() -> None:
            try:
                result = await operation
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                callback: Callback = lambda: (_ for _ in ()).throw(exc)
            else:
                callback = lambda: on_completion(result)
            self._queue.put_nowait((callback, None))

        task = asyncio.create_task(run())
        self._worker_tasks.add(task)
        task.add_done_callback(self._worker_tasks.discard)

    async def tick(self) -> SchedulerDecision | None:
        if not self.started:
            raise ServiceError("daemon service is not started")
        await _call(self._flush_safe_outboxes)
        try:
            callback, future = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            facts = await _call(self._load_scheduler_facts)
            if facts is None:
                return None
            if not isinstance(facts, SchedulerFacts):
                raise ServiceError("scheduler facts are invalid")
            decision = schedule_gate(facts)
            await _call(self._apply_transition, decision)
            return decision
        try:
            result = await _call(callback)
        except Exception as exc:
            if future is not None and not future.done():
                future.set_exception(exc)
            return None
        if future is not None and not future.done():
            future.set_result(result)
        return None

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def run(self, *, poll_interval_seconds: float = 0.1) -> None:
        if (
            type(poll_interval_seconds) not in {int, float}
            or not math.isfinite(float(poll_interval_seconds))
            or poll_interval_seconds <= 0
        ):
            raise ServiceError("daemon service poll interval is invalid")
        await self.start()
        try:
            while not self._shutdown.is_set():
                await self.tick()
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(), timeout=float(poll_interval_seconds)
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in tuple(self._worker_tasks):
            task.cancel()
        if self._worker_tasks:
            await asyncio.gather(*tuple(self._worker_tasks), return_exceptions=True)
        while not self._queue.empty():
            _callback, future = self._queue.get_nowait()
            if future is not None and not future.done():
                future.set_exception(ServiceError("daemon service closed"))
        if self._started:
            await self.server.close()
        self._started = False
