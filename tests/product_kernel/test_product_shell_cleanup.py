from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.recovery_service import RecoveryService
from agentdeck.product.bootstrap import build_product_shell

from .fakes import FrozenClock
from .test_product_shell import (
    NOW, AsyncLines, _config, _discovery, _seed_resume, async_test,
)


def _cleanup_probe_shell(tmp_path: Path, *, recovery_factory=RecoveryService):
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    close_calls: list[str] = []
    real_close = store.close

    def close() -> None:
        close_calls.append("close")
        real_close()

    store.close = close  # type: ignore[method-assign]
    shell = build_product_shell(
        project_root=str(tmp_path), read_line=AsyncLines("/exit"),
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=_discovery, config_factory=_config,
        store_factory=lambda *args, **kwargs: store,
        recovery_factory=recovery_factory,
    )
    return shell, close_calls


@async_test
async def test_recovery_failure_closes_store_once(tmp_path: Path) -> None:
    class FailingRecovery:
        def __init__(self, **_facts):
            pass

        async def reconcile(self):
            raise RuntimeError("recovery failed")

    shell, close_calls = _cleanup_probe_shell(
        tmp_path, recovery_factory=FailingRecovery,
    )
    with pytest.raises(RuntimeError, match="recovery failed"):
        await shell.run_async()
    assert close_calls == ["close"]


@async_test
async def test_initial_render_failure_closes_store_once(tmp_path: Path) -> None:
    shell, close_calls = _cleanup_probe_shell(tmp_path)
    shell._render = lambda _value: (_ for _ in ()).throw(
        RuntimeError("render failed")
    )
    with pytest.raises(RuntimeError, match="render failed"):
        await shell.run_async()
    assert close_calls == ["close"]


@async_test
async def test_resume_projection_failure_closes_store_once(tmp_path: Path) -> None:
    _seed_resume(tmp_path)
    shell, close_calls = _cleanup_probe_shell(tmp_path)

    class FailingPlanner:
        def materialize(self, _snapshot):
            raise AssertionError("projection failed")

    shell._resume_planner = FailingPlanner()
    with pytest.raises(AssertionError, match="projection failed"):
        await shell.run_async()
    assert close_calls == ["close"]


@async_test
async def test_signal_handler_install_failure_closes_store_once(
    tmp_path: Path, monkeypatch,
) -> None:
    shell, close_calls = _cleanup_probe_shell(tmp_path)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("signal failed")),
    )
    with pytest.raises(RuntimeError, match="signal failed"):
        await shell.run_async()
    assert close_calls == ["close"]


@async_test
async def test_cancellation_during_recovery_closes_store_once(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()

    class BlockingRecovery:
        def __init__(self, **_facts):
            pass

        async def reconcile(self):
            started.set()
            await asyncio.Event().wait()

    shell, close_calls = _cleanup_probe_shell(
        tmp_path, recovery_factory=BlockingRecovery,
    )
    running = asyncio.create_task(shell.run_async())
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert close_calls == ["close"]


@async_test
async def test_cancelled_owned_child_still_closes_store_once(
    tmp_path: Path,
) -> None:
    _seed_resume(tmp_path)
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    close_calls: list[str] = []
    real_close = store.close

    def close() -> None:
        close_calls.append("close")
        real_close()

    class CancelledExecution:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def run_confirmed_mission(self, **_facts):
            self.started.set()
            raise asyncio.CancelledError

    execution = CancelledExecution()

    class Reader(AsyncLines):
        async def __call__(self, prompt: str) -> str:
            value = await super().__call__(prompt)
            if value == "/exit":
                await execution.started.wait()
            return value

    store.close = close  # type: ignore[method-assign]
    shell = build_product_shell(
        project_root=str(tmp_path), read_line=Reader("/resume", "/exit"),
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=_discovery, config_factory=_config,
        store_factory=lambda *args, **kwargs: store, adapter_readiness={},
        adapter_composition_factory=lambda **_: SimpleNamespace(
            worker=lambda _backend: None
        ),
        approval_service_factory=lambda **_: object(),
        execution_service_factory=lambda **_: execution,
    )

    with pytest.raises(asyncio.CancelledError):
        await shell.run_async()

    assert close_calls == ["close"]
