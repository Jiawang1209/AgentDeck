from __future__ import annotations

import asyncio
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest
from acp import PROTOCOL_VERSION
from acp.schema import AgentCapabilities, InitializeResponse, NewSessionResponse

from agentdeck.adapters.acp import ACPWorker
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.product.bootstrap import build_product_shell

from .fakes import FrozenClock
from .test_product_shell import NOW, _config, _discovery, _seed_resume, async_test


class BlockingReader:
    def __init__(self, *initial: str) -> None:
        self._initial = iter(initial)
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def __call__(self, _prompt: str) -> str:
        try:
            return next(self._initial)
        except StopIteration:
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.stopped.set()
            raise AssertionError("blocked input resumed without cancellation")


class ResistantReader:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.release = asyncio.Event()
        self.stopped = asyncio.Event()
        self.cancel_count = 0

    async def __call__(self, _prompt: str) -> str:
        self.started.set()
        try:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_count += 1
                    self.cleanup_started.set()
        finally:
            self.stopped.set()
        raise EOFError


class BlockingACPAgent:
    def __init__(self) -> None:
        self.prompt_started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.cancel_calls = 0

    async def initialize(self, protocol_version: int) -> InitializeResponse:
        assert protocol_version == PROTOCOL_VERSION
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(self, *, cwd: str) -> NewSessionResponse:
        assert cwd
        return NewSessionResponse(session_id="raw_caller_cancel")

    async def prompt(self, *_args, **_kwargs):
        self.prompt_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped.set()

    async def cancel(self, session_id: str) -> None:
        assert session_id == "raw_caller_cancel"
        self.cancel_calls += 1


def _store_probe(root: Path):
    store = SQLiteStore.open(root, clock=FrozenClock(NOW))
    close_calls: list[str] = []
    real_close = store.close

    def close() -> None:
        close_calls.append("close")
        real_close()

    store.close = close  # type: ignore[method-assign]
    return store, close_calls


async def _cancel_tasks(tasks: set[asyncio.Task]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@async_test
async def test_caller_cancel_collects_iteration_tasks_and_closes_once(
    tmp_path: Path,
) -> None:
    reader = BlockingReader()
    store, close_calls = _store_probe(tmp_path)
    shell = build_product_shell(
        project_root=str(tmp_path), read_line=reader,
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=_discovery, config_factory=_config,
        store_factory=lambda *args, **kwargs: store,
    )
    baseline = set(asyncio.all_tasks())
    running = asyncio.create_task(shell.run_async())
    await reader.started.wait()
    owned = set(asyncio.all_tasks()) - baseline

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    leaked = {task for task in owned if not task.done()}
    try:
        assert reader.stopped.is_set()
        assert leaked == set()
        assert close_calls == ["close"]
    finally:
        await _cancel_tasks(leaked)


@async_test
async def test_caller_cancel_bounds_real_mission_and_retains_running_attempt(
    tmp_path: Path,
) -> None:
    _seed_resume(tmp_path)
    reader = BlockingReader("/resume")
    agent = BlockingACPAgent()
    worker = ACPWorker(
        agent=agent, project_root=str(tmp_path), clock=FrozenClock(NOW),
        project_boundary_enforced=True,
    )
    store, close_calls = _store_probe(tmp_path)
    shell = build_product_shell(
        project_root=str(tmp_path), read_line=reader,
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=_discovery, config_factory=_config,
        store_factory=lambda *args, **kwargs: store, adapter_readiness={},
        adapter_composition_factory=lambda **_: SimpleNamespace(
            worker=lambda _backend: worker
        ),
    )
    baseline = set(asyncio.all_tasks())
    running = asyncio.create_task(shell.run_async())
    await agent.prompt_started.wait()
    await reader.started.wait()
    owned = set(asyncio.all_tasks()) - baseline

    running.cancel()
    done, _ = await asyncio.wait({running}, timeout=0.2)
    completed_in_bound = running in done
    original_cancelled = False
    if completed_in_bound:
        try:
            await running
        except asyncio.CancelledError:
            original_cancelled = True
    leaked = {task for task in owned if not task.done()}
    close_before_manual_cleanup = tuple(close_calls)
    reopened = SQLiteStore.open_read_only(tmp_path, clock=FrozenClock(NOW))
    try:
        attempts = reopened.connection.execute(
            "SELECT state FROM attempts ORDER BY rowid"
        ).fetchall()
        session_state = reopened.load_aggregate(
            "product_sessions", "ses_1"
        )["state"]
        paused_events = reopened.connection.execute(
            "SELECT count(*) FROM events WHERE kind='project_paused'"
        ).fetchone()[0]
        terminal_rows = tuple(
            reopened.connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]
            for table in ("evidence", "handoffs")
        )
        assert attempts == [("running",)]
        assert session_state == "running"
        assert paused_events == 0
        assert terminal_rows == (0, 0)
    finally:
        reopened.close()
        await _cancel_tasks(leaked)

    assert completed_in_bound is True
    assert original_cancelled is True
    assert reader.stopped.is_set() and agent.stopped.is_set()
    assert agent.cancel_calls == 1
    assert leaked == set()
    assert close_before_manual_cleanup == ("close",)


@async_test
async def test_repeated_caller_cancel_preserves_first_error_and_finishes_cleanup(
    tmp_path: Path,
) -> None:
    _seed_resume(tmp_path)
    reader = BlockingReader("/resume")
    store, close_calls = _store_probe(tmp_path)

    class SlowCancellationExecution:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cleanup_started = asyncio.Event()
            self.cleanup_release = asyncio.Event()

        async def run_confirmed_mission(self, **_facts):
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cleanup_started.set()
                await self.cleanup_release.wait()
                raise

    execution = SlowCancellationExecution()
    shell = build_product_shell(
        project_root=str(tmp_path), read_line=reader,
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=_discovery, config_factory=_config,
        store_factory=lambda *args, **kwargs: store, adapter_readiness={},
        adapter_composition_factory=lambda **_: SimpleNamespace(
            worker=lambda _backend: None
        ),
        approval_service_factory=lambda **_: object(),
        execution_service_factory=lambda **_: execution,
    )
    baseline = set(asyncio.all_tasks())
    running = asyncio.create_task(shell.run_async())
    await execution.started.wait()
    await reader.started.wait()
    owned = set(asyncio.all_tasks()) - baseline

    running.cancel("first")
    await execution.cleanup_started.wait()
    running.cancel("second")
    await asyncio.sleep(0)
    still_cleaning = not running.done()
    execution.cleanup_release.set()
    raised: asyncio.CancelledError | None = None
    try:
        await running
    except asyncio.CancelledError as error:
        raised = error
    leaked = {task for task in owned if not task.done()}
    try:
        assert still_cleaning is True
        assert raised is not None and raised.args == ("first",)
        assert close_calls == ["close"]
        assert leaked == set()
    finally:
        execution.cleanup_release.set()
        await _cancel_tasks(leaked)


@async_test
async def test_repeated_cancel_during_reader_cleanup_preserves_first_error(
    tmp_path: Path,
) -> None:
    store, close_calls = _store_probe(tmp_path)
    reader = ResistantReader()
    shell = build_product_shell(
        project_root=str(tmp_path), read_line=reader,
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=_discovery, config_factory=_config,
        store_factory=lambda *args, **kwargs: store,
    )
    baseline = set(asyncio.all_tasks())
    running = asyncio.create_task(shell.run_async())
    await reader.started.wait()
    owned = set(asyncio.all_tasks()) - baseline

    running.cancel("first")
    await reader.cleanup_started.wait()
    running.cancel("second")
    await asyncio.sleep(0)
    still_cleaning = not running.done()
    reader.release.set()
    raised: asyncio.CancelledError | None = None
    try:
        await running
    except asyncio.CancelledError as error:
        raised = error
    leaked = {task for task in owned if not task.done()}
    try:
        assert still_cleaning is True
        assert raised is not None and raised.args == ("first",)
        assert reader.stopped.is_set()
        assert close_calls == ["close"]
        assert leaked == set()
    finally:
        reader.release.set()
        await _cancel_tasks(leaked)


@async_test
async def test_first_caller_cancel_during_sigint_reader_cleanup_is_preserved(
    tmp_path: Path,
) -> None:
    store, close_calls = _store_probe(tmp_path)
    reader = ResistantReader()
    shell = build_product_shell(
        project_root=str(tmp_path), read_line=reader,
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=_discovery, config_factory=_config,
        store_factory=lambda *args, **kwargs: store,
    )
    baseline = set(asyncio.all_tasks())
    running = asyncio.create_task(shell.run_async())
    await reader.started.wait()
    owned = set(asyncio.all_tasks()) - baseline

    signal.raise_signal(signal.SIGINT)
    await reader.cleanup_started.wait()
    running.cancel("caller-first")
    await asyncio.sleep(0)
    reader.release.set()
    raised: asyncio.CancelledError | None = None
    result: int | None = None
    try:
        result = await running
    except asyncio.CancelledError as error:
        raised = error
    leaked = {task for task in owned if not task.done()}
    try:
        assert result is None
        assert raised is not None and raised.args == ("caller-first",)
        assert reader.stopped.is_set()
        assert close_calls == ["close"]
        assert leaked == set()
    finally:
        reader.release.set()
        await _cancel_tasks(leaked)
