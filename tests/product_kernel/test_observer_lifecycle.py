"""Product Observer acknowledgement pump containment and safe shutdown."""

from __future__ import annotations

import asyncio

from agentdeck.product.observer_lifecycle import ProductObserverLifecycle


class _FlakyServer:
    """A drain that raises on its first call, then drains normally."""

    def __init__(self, fail_times: int = 1) -> None:
        self.closed = False
        self.drain_calls = 0
        self.successful_drains = 0
        self._fail_times = fail_times

    def start(self) -> None:
        pass

    def drain_acknowledgements(self, limit: int = 256) -> int:
        self.drain_calls += 1
        if self.drain_calls <= self._fail_times:
            raise RuntimeError("acknowledgement pump failure")
        self.successful_drains += 1
        return 0

    def close(self) -> None:
        self.closed = True

    @property
    def subscriber_count(self) -> int:
        return 0


class _Publisher:
    def publish(self, value) -> None:
        pass

    def acknowledge(self, value) -> None:
        pass

    def current_cursor(self, attempt_id):
        return None


def test_pump_survives_raising_drain_and_close_still_unlinks_server() -> None:
    async def scenario() -> None:
        server = _FlakyServer(fail_times=1)
        lifecycle = ProductObserverLifecycle()
        lifecycle.bind(server=server, publisher=_Publisher())
        await lifecycle.start()
        await asyncio.sleep(0.05)
        await lifecycle.close()
        # One raised drain must neither kill the pump nor block shutdown.
        assert server.successful_drains >= 1
        assert server.closed is True

    asyncio.run(scenario())


def test_close_unlinks_server_even_when_every_drain_raises() -> None:
    async def scenario() -> None:
        server = _FlakyServer(fail_times=10_000)
        lifecycle = ProductObserverLifecycle()
        lifecycle.bind(server=server, publisher=_Publisher())
        await lifecycle.start()
        await asyncio.sleep(0.03)
        await lifecycle.close()
        # Even a persistently failing drain must not leak the socket/threads.
        assert server.closed is True

    asyncio.run(scenario())
