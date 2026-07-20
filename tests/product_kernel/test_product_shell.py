from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import wraps
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentdeck.adapters.discovery import ReadinessState, ToolDiscovery
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.recovery_service import RecoveryService
from agentdeck.application.session_service import SessionService
from agentdeck.product.bootstrap import build_product_shell
from agentdeck.product.shell import AsyncTerminalReader
from agentdeck.product.slash_commands import CommandKind

from .fakes import FrozenClock
from .test_sqlite_execution_resume import _seed_base, seed_closed_stage


NOW = datetime(2026, 7, 20, 8, 9, 10, tzinfo=timezone.utc)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def _discovery():
    return {"codex": ToolDiscovery(
        name="codex", command="codex", resolved_path="/tools/codex",
        version="codex 1.0", authenticated=True, acp_available=True,
        readiness=ReadinessState.READY, capabilities=("leader", "worker", "acp"),
    )}


def _config(**_layers):
    return SimpleNamespace(
        resolve=lambda _key: SimpleNamespace(value="approve-for-me")
    )


class FakeExecution:
    def __init__(self, *, block: bool = False) -> None:
        self.calls = []
        self.loop_ids: set[int] = set()
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.block = block

    async def run_confirmed_mission(self, **facts):
        self.calls.append(facts)
        self.loop_ids.add(id(asyncio.get_running_loop()))
        self.started.set()
        if self.block:
            await self.finish.wait()
        return None


class AsyncLines:
    def __init__(self, *lines: str) -> None:
        self.lines = iter(lines)
        self.calls = 0

    async def __call__(self, prompt: str) -> str:
        assert prompt == "agentdeck> "
        self.calls += 1
        await asyncio.sleep(0)
        try:
            return next(self.lines)
        except StopIteration:
            raise EOFError from None


def _seed_resume(root: Path, *, closed_implementation: bool = False) -> None:
    store = SQLiteStore.open(root, clock=FrozenClock(NOW))
    _seed_base(store)
    store._require_writer().commit()
    store._require_writer().execute("PRAGMA foreign_keys=OFF")
    store._require_writer().execute(
        "UPDATE projects SET project_id=? WHERE project_id='prj_resume'",
        (store._project_id,),
    )
    store._require_writer().execute(
        "UPDATE product_sessions SET project_id=? WHERE session_id='ses_1'",
        (store._project_id,),
    )
    store._require_writer().commit()
    store._require_writer().execute("PRAGMA foreign_keys=ON")
    store.execute_once(
        "session:configure:ses_1", "configure_product_session", lambda _: {
            "accepted": True, "goal": None, "leader_backend": "codex-cli",
            "mode": "ready", "model": "native-default",
            "permission": "approve_for_me", "session_id": "ses_1",
        },
    )
    if closed_implementation:
        seed_closed_stage(store, "implementation")
    store._require_writer().commit()
    store.close()


def _build(
    root: Path, reader, output: list[str], *, execution: FakeExecution | None = None,
    recovery_factory=RecoveryService,
):
    kwargs = {}
    if execution is not None:
        kwargs.update(
            adapter_readiness={},
            adapter_composition_factory=lambda **_: SimpleNamespace(
                worker=lambda _backend: None
            ),
            approval_service_factory=lambda **_: object(),
            execution_service_factory=lambda **_: execution,
        )
    return build_product_shell(
        project_root=str(root), read_line=reader, write_line=output.append,
        clock_factory=lambda: FrozenClock(NOW), discovery_factory=_discovery,
        config_factory=_config, recovery_factory=recovery_factory, **kwargs,
    )


@async_test
async def test_first_run_shell_retains_goal_and_resumes_after_setup(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    shell = _build(tmp_path, AsyncLines(
        "Build an accessible page", "/leader codex-cli", "/model native-default",
        "/permissions approve-for-me", "/setup confirm", "/exit",
    ), output)

    assert await shell.run_async() == 0
    transcript = "\n".join(output)
    assert "I saved your goal while setup completes." in transcript
    assert "Goal ready: Build an accessible page" in transcript
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        view = SessionService.open_latest(
            store=store, clock=FrozenClock(NOW), project_root=str(tmp_path),
            available_leaders={"codex-cli": ("native-default",)},
            session_id_factory=lambda: "ses_unused",
        ).current()
        assert (view.leader_backend, view.model, view.permission) == (
            "codex-cli", "native-default", "approve_for_me",
        )
    finally:
        store.close()


@async_test
async def test_help_status_setup_and_declared_controls_work_without_llm(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    shell = _build(tmp_path, AsyncLines(
        "/help", "/status", "/setup", "/agents", "/mission", "/pause",
        "/takeover att_1", "/diagnose", "/unknown", "/exit",
    ), output)

    await shell.run_async()

    transcript = "\n".join(output)
    assert "Select Leader" in transcript
    assert "AgentDeck is setup." in transcript
    assert "No Agent Instances are active." in transcript
    assert "Command not recognized. Use /help." in transcript
    assert all(f"/{kind.value}" in transcript for kind in CommandKind)
    assert "{" not in transcript


@async_test
async def test_recovery_finishes_before_first_input_read(tmp_path: Path) -> None:
    calls: list[str] = []

    class RecordingRecovery:
        def __init__(self, **facts):
            self.real = RecoveryService(**facts)

        async def reconcile(self):
            calls.append("recovery.reconcile")
            return await self.real.reconcile()

    class Reader(AsyncLines):
        async def __call__(self, prompt: str) -> str:
            calls.append("read_line")
            return await super().__call__(prompt)

    shell = _build(
        tmp_path, Reader("/exit"), [], recovery_factory=RecordingRecovery
    )

    await shell.run_async()

    assert calls[:2] == ["recovery.reconcile", "read_line"]


@async_test
async def test_paused_reentry_starts_nothing_before_explicit_resume(
    tmp_path: Path,
) -> None:
    _seed_resume(tmp_path)
    execution = FakeExecution()
    output: list[str] = []
    shell = _build(
        tmp_path, AsyncLines("/status", "/exit"), output, execution=execution
    )

    await shell.run_async()

    assert execution.calls == []
    assert "AgentDeck is paused." in "\n".join(output)


@async_test
async def test_resume_without_execution_adapter_stays_paused(
    tmp_path: Path,
) -> None:
    _seed_resume(tmp_path)
    output: list[str] = []
    shell = _build(tmp_path, AsyncLines("/resume", "/exit"), output)

    await shell.run_async()

    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        assert store.load_aggregate("product_sessions", "ses_1")["state"] == "paused"
        assert store.connection.execute(
            "SELECT count(*) FROM events WHERE kind='project_resumed'"
        ).fetchone() == (0,)
        assert store.count("attempts") == 0
    finally:
        store.close()
    assert "execution_adapter_unavailable" in "\n".join(output)


@async_test
async def test_explicit_resume_starts_one_same_loop_mission_child(
    tmp_path: Path,
) -> None:
    _seed_resume(tmp_path)
    execution = FakeExecution()
    shell = _build(
        tmp_path, AsyncLines("/resume", "/exit"), [], execution=execution
    )

    await shell.run_async()

    assert len(execution.calls) == 1
    assert execution.calls[0]["resume_plan"].first_attempt_ordinal == 1
    assert execution.loop_ids == {id(asyncio.get_running_loop())}


@async_test
async def test_second_resume_observes_existing_child_without_duplicate(
    tmp_path: Path,
) -> None:
    _seed_resume(tmp_path)
    execution = FakeExecution(block=True)

    class Reader(AsyncLines):
        async def __call__(self, prompt: str) -> str:
            value = await super().__call__(prompt)
            if value == "/exit":
                execution.finish.set()
            return value

    output: list[str] = []
    shell = _build(
        tmp_path, Reader("/resume", "/resume", "/exit"), output,
        execution=execution,
    )

    await shell.run_async()

    assert len(execution.calls) == 1
    assert "already running" in "\n".join(output).lower()


@async_test
async def test_waiting_for_terminal_input_does_not_block_mission_child(
    tmp_path: Path,
) -> None:
    _seed_resume(tmp_path)
    execution = FakeExecution(block=True)
    release_input = asyncio.Event()

    class BlockingReader:
        calls = 0

        async def __call__(self, prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return "/resume"
            await release_input.wait()
            return "/exit"

    shell = _build(tmp_path, BlockingReader(), [], execution=execution)
    running = asyncio.create_task(shell.run_async())

    await execution.started.wait()
    assert running.done() is False
    execution.finish.set()
    release_input.set()
    await running


@async_test
async def test_startup_transcript_uses_one_exact_resume_snapshot(
    tmp_path: Path,
) -> None:
    _seed_resume(tmp_path, closed_implementation=True)
    output: list[str] = []
    shell = _build(tmp_path, AsyncLines("/exit"), output)

    await shell.run_async()

    transcript = "\n".join(output)
    assert "tsk_review" in transcript
    assert "next Attempt 1" in transcript
    assert "hnd_implementation_1" in transcript
    assert "canonical" not in transcript.lower()


def test_bootstrap_rejects_a_nonempty_fresh_runtime(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="empty runtime"):
        build_product_shell(
            project_root=str(tmp_path), read_line=AsyncLines("/exit"),
            write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
            discovery_factory=_discovery, config_factory=_config,
            runtime_factory=lambda: SimpleNamespace(is_empty=lambda: False),
        )


def test_bootstrap_preserves_an_injected_falsey_async_reader(
    tmp_path: Path,
) -> None:
    class FalseyReader(AsyncLines):
        def __bool__(self) -> bool:
            return False

    reader = FalseyReader("/exit")
    shell = build_product_shell(
        project_root=str(tmp_path), read_line=reader,
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=_discovery, config_factory=_config,
    )
    try:
        assert shell._read_line is reader
    finally:
        shell._close_once()


@async_test
async def test_async_terminal_reader_does_not_block_the_foreground_loop() -> None:
    read_descriptor, write_descriptor = os.pipe()
    stream = os.fdopen(read_descriptor, "r", encoding="utf-8")
    prompt = io.StringIO()
    reader = AsyncTerminalReader(stream, prompt)
    task = asyncio.create_task(reader("agentdeck> "))
    try:
        await asyncio.sleep(0)
        assert task.done() is False
        assert prompt.getvalue() == "agentdeck> "
        os.write(write_descriptor, b"/status\n")
        assert await asyncio.wait_for(task, timeout=1) == "/status"
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        os.close(write_descriptor)
        stream.close()


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
