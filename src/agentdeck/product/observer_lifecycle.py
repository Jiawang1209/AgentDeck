"""Product-owned Observer endpoint, acknowledgement pump, and proof sources."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class ProductObserverLifecycle:
    """Own one project endpoint and drain acknowledgements on the Product loop."""

    def __init__(self) -> None:
        self._server: object | None = None
        self._publisher: object | None = None
        self._pump: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._available = False

    def bind(self, *, server: object, publisher: object) -> None:
        if self._server is not None or any(
            not callable(getattr(server, name, None))
            for name in ("start", "close", "drain_acknowledgements")
        ) or any(
            not callable(getattr(publisher, name, None))
            for name in ("publish", "acknowledge", "current_cursor")
        ):
            raise TypeError("Observer lifecycle composition is invalid")
        self._server, self._publisher = server, publisher

    @property
    def publisher(self):
        if self._publisher is None:
            raise RuntimeError("Observer lifecycle is not composed")
        return self._publisher

    @property
    def subscriber_count(self) -> int:
        return self._server.subscriber_count if self._available else 0

    def current_cursor(self, attempt_id: str):
        return self.publisher.current_cursor(attempt_id)

    def acknowledge(self, value) -> None:
        self.publisher.acknowledge(value)

    def read_cursor(self, binding):
        return self.publisher.current_cursor(binding.attempt_id)

    async def start(self) -> None:
        if self._pump is not None:
            raise RuntimeError("Observer lifecycle is already started")
        if self._server is None:
            raise RuntimeError("Observer lifecycle is not composed")
        try:
            self._server.start()
        except Exception as error:
            if getattr(error, "code", None) != "observer_endpoint_unavailable":
                raise
            return
        self._available = True
        self._pump = asyncio.create_task(self._drain())

    async def close(self) -> None:
        self._stopping.set()
        if self._pump is not None:
            try:
                await self._pump
            except Exception:
                pass
            self._pump = None
        if self._available:
            self._safe_drain()
            self._server.close()
            self._available = False

    async def _drain(self) -> None:
        while not self._stopping.is_set():
            self._safe_drain()
            await asyncio.sleep(0.01)
        self._safe_drain()

    def _safe_drain(self) -> None:
        try:
            self._server.drain_acknowledgements()
        except Exception:
            # A single acknowledgement failure is observation degradation. It
            # must never kill the pump or block the deterministic shutdown that
            # unlinks the socket and joins the listener threads.
            pass

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
    "ProductObserverLifecycle", "ProductObserverRunner",
]
