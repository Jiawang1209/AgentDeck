"""Product-owned Observer endpoint, acknowledgement pump, and proof sources."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from agentdeck.adapters.observer_ipc import ObserverIPCError, UnixObserverServer
from agentdeck.application.observer_broker import ObserverBroker
from agentdeck.application.takeover_control import TakeoverControl
from agentdeck.kernel.permissions import PermissionScope
from agentdeck.ports.clock import Clock
from agentdeck.ports.observer import ObserverCursor
from agentdeck.ports.store import Store


class ActivePermissionSnapshotSource:
    """Read the exact effective scope owned by the armed takeover control."""

    def __init__(self) -> None:
        self._control: TakeoverControl | None = None

    def bind(self, control: TakeoverControl) -> None:
        if type(control) is not TakeoverControl or self._control is not None:
            raise ValueError("permission proof source binding is invalid")
        self._control = control

    def __call__(self) -> PermissionScope:
        active = None if self._control is None else self._control._active
        if active is None or type(active.permission) is not PermissionScope:
            raise RuntimeError("permission snapshot is unavailable")
        return active.permission


class RuntimeObserverCursorSource:
    """Resolve only the acknowledged cursor for the current runtime Attempt."""

    def __init__(self, *, runtime: object, lifecycle: "ProductObserverLifecycle") -> None:
        if not callable(getattr(runtime, "status", None)):
            raise TypeError("Observer cursor source requires runtime status")
        self._runtime, self._lifecycle = runtime, lifecycle

    def __call__(self) -> ObserverCursor | None:
        status = self._runtime.status()
        attempt_id = status.attempt_id if status.state == "active" else None
        return None if attempt_id is None else self._lifecycle.current_cursor(attempt_id)


class ProductObserverLifecycle:
    """Own one project endpoint and drain acknowledgements on the Product loop."""

    def __init__(
        self, *, project_root: Path, project_id: str, store: Store, clock: Clock,
    ) -> None:
        self._broker: ObserverBroker | None = None
        self._server = UnixObserverServer(
            project_root=project_root, project_id=project_id,
            acknowledge=self._acknowledge, cursor_reader=self._read_cursor,
        )
        self._broker = ObserverBroker(
            project_id=project_id, store=store, clock=clock, channel=self._server,
        )
        self._pump: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._available = False

    @property
    def publisher(self) -> ObserverBroker:
        assert self._broker is not None
        return self._broker

    @property
    def subscriber_count(self) -> int:
        return self._server.subscriber_count if self._available else 0

    def current_cursor(self, attempt_id: str) -> ObserverCursor | None:
        return self.publisher.current_cursor(attempt_id)

    async def start(self) -> None:
        if self._pump is not None:
            raise RuntimeError("Observer lifecycle is already started")
        try:
            self._server.start()
        except ObserverIPCError as error:
            if error.code != "observer_endpoint_unavailable":
                raise
            return
        self._available = True
        self._pump = asyncio.create_task(self._drain())

    async def close(self) -> None:
        self._stopping.set()
        if self._pump is not None:
            await self._pump
            self._pump = None
        if self._available:
            self._server.drain_acknowledgements()
            self._server.close()
            self._available = False

    async def _drain(self) -> None:
        while not self._stopping.is_set():
            self._server.drain_acknowledgements()
            await asyncio.sleep(0.01)
        self._server.drain_acknowledgements()

    def _acknowledge(self, value) -> None:
        self.publisher.acknowledge(value)

    def _read_cursor(self, binding) -> ObserverCursor | None:
        return self.publisher.current_cursor(binding.attempt_id)


class ProductObserverRunner:
    """Wrap the shell so Observer closes before the Store on every exit path."""

    def __init__(self, lifecycle: object | None, close_store: Callable[[], object]) -> None:
        if lifecycle is not None and any(
            not callable(getattr(lifecycle, name, None)) for name in ("start", "close")
        ):
            raise TypeError("observer_lifecycle does not satisfy the Product Shell")
        self.lifecycle, self._close_store = lifecycle, close_store
        self._closed = False

    async def run(self, operation: Callable[[], Awaitable[int]]) -> int:
        try:
            if self.lifecycle is not None:
                await self.lifecycle.start()
            return await operation()
        finally:
            try:
                if self.lifecycle is not None:
                    await self.lifecycle.close()
            finally:
                self.close_store()

    def close_store(self) -> None:
        if not self._closed:
            self._closed = True
            self._close_store()


__all__ = [
    "ActivePermissionSnapshotSource", "ProductObserverLifecycle",
    "ProductObserverRunner", "RuntimeObserverCursorSource",
]
