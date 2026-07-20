from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.exit_service import ExitService
from agentdeck.application.project_lifecycle_service import ProjectLifecycleService
from agentdeck.application.recovery_service import RecoveryService
from agentdeck.kernel.execution import Attempt
from agentdeck.kernel.mission import MissionDraft
from agentdeck.product.bootstrap import build_product_shell
from agentdeck.ports.worker import WorkerEvent, WorkerHandle, WorkerResult

from .fakes import FrozenClock
from .test_product_shell import FakeExecution, _config, _discovery, _seed_resume
from .test_sqlite_execution_resume import (
    _insert_attempt,
    _task,
    seed_closed_stage,
    seed_outcome_unknown_attempt,
)


NOW = datetime(2026, 7, 20, 8, 9, 10, tzinfo=timezone.utc)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


class AsyncLines:
    def __init__(self, *values: str) -> None:
        self.values = iter(values)

    async def __call__(self, _prompt: str) -> str:
        await asyncio.sleep(0)
        try:
            return next(self.values)
        except StopIteration:
            raise EOFError from None


def _build(
    root: Path, reader, output: list[str], execution: FakeExecution,
    *, store_factory=SQLiteStore.open,
):
    return build_product_shell(
        project_root=str(root), read_line=reader, write_line=output.append,
        clock_factory=lambda: FrozenClock(NOW), discovery_factory=_discovery,
        config_factory=_config, store_factory=store_factory,
        adapter_readiness={},
        adapter_composition_factory=lambda **_: SimpleNamespace(
            worker=lambda _backend: None
        ),
        approval_service_factory=lambda **_: object(),
        execution_service_factory=lambda **_: execution,
    )


def _attach_seed_authority(store: SQLiteStore) -> None:
    draft = MissionDraft.coding_default(
        "drf_resume", "Resume the exact mission", str(store._project_root),
        "codex-cli", "native-default", "approve_for_me",
        leader_version="1.2.3",
    )
    preview = draft.preview(1)
    store._resume_draft = draft
    store._resume_confirmed = preview.confirm(
        preview_id=preview.preview_id, content_hash=preview.content_hash
    )


def _request_pending_exit(store: SQLiteStore) -> None:
    _attach_seed_authority(store)
    task = _task(store, "implementation")
    _insert_attempt(
        store,
        Attempt.pending("att_pending", task.task_id, 1).start(),
    )
    result = ExitService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        request_id_factory=lambda: "xrt_" + "1" * 32,
    ).request_exit()
    assert result.mode == "exit_confirmation_required"


@async_test
@pytest.mark.parametrize("case", ["outcome_unknown", "pending_exit", "complete"])
async def test_invalid_resume_authority_starts_zero_workers(
    tmp_path: Path, case: str,
) -> None:
    _seed_resume(tmp_path)
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _attach_seed_authority(store)
    if case == "outcome_unknown":
        seed_outcome_unknown_attempt(store, "implementation", 1)
    elif case == "complete":
        for name in ("implementation", "review", "revision", "acceptance"):
            seed_closed_stage(store, name)
    store._require_writer().commit()
    execution = FakeExecution()
    output: list[str] = []

    class Reader(AsyncLines):
        mutated = False

        async def __call__(self, prompt: str) -> str:
            if case == "pending_exit" and not self.mutated:
                _request_pending_exit(store)
                self.mutated = True
            return await super().__call__(prompt)

    shell = _build(
        tmp_path, Reader("/resume", "/exit"), output, execution,
        store_factory=lambda *args, **kwargs: store,
    )
    await shell.run_async()

    assert execution.calls == []
    assert "Resume blocked:" in "\n".join(output)


@async_test
@pytest.mark.parametrize("drift", ["session", "projection_hash"])
async def test_resume_revalidates_startup_snapshot_before_worker(
    tmp_path: Path, drift: str,
) -> None:
    _seed_resume(tmp_path)
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    execution = FakeExecution()

    class DriftReader:
        calls = 0

        async def __call__(self, _prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                if drift == "session":
                    store._require_writer().execute(
                        "UPDATE product_sessions SET state='running' WHERE session_id='ses_1'"
                    )
                else:
                    task_id = store._require_writer().execute(
                        "SELECT task_id FROM tasks WHERE name='implementation'"
                    ).fetchone()[0]
                    store._require_writer().execute(
                        "UPDATE tasks SET state='ready' WHERE task_id=?",
                        (task_id,),
                    )
                store._require_writer().commit()
                return "/resume"
            return "/exit"

    shell = _build(
        tmp_path, DriftReader(), [], execution,
        store_factory=lambda *args, **kwargs: store,
    )
    await shell.run_async()

    assert execution.calls == []


@async_test
async def test_eof_during_active_child_does_not_close_store_under_task(
    tmp_path: Path,
) -> None:
    _seed_resume(tmp_path)
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    execution = FakeExecution(block=True)
    close_calls: list[str] = []
    real_close = store.close

    def close() -> None:
        close_calls.append("close")
        real_close()

    store.close = close  # type: ignore[method-assign]

    class EOFReader:
        calls = 0

        async def __call__(self, _prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return "/resume"
            await execution.started.wait()
            raise EOFError

    shell = _build(
        tmp_path, EOFReader(), [], execution,
        store_factory=lambda *args, **kwargs: store,
    )
    running = asyncio.create_task(shell.run_async())
    await execution.started.wait()
    await asyncio.sleep(0)
    assert close_calls == []
    assert running.done() is False
    execution.finish.set()
    await running
    assert close_calls == ["close"]


@async_test
async def test_sigint_callback_uses_same_awaited_project_exit_path(
    tmp_path: Path,
) -> None:
    waiting = asyncio.Event()

    class BlockingReader:
        async def __call__(self, _prompt: str) -> str:
            waiting.set()
            await asyncio.Event().wait()
            raise AssertionError

    shell = build_product_shell(
        project_root=str(tmp_path), read_line=BlockingReader(),
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=_discovery, config_factory=_config,
    )
    calls = 0
    request_exit = shell._exit.request_exit

    async def counted_request_exit():
        nonlocal calls
        calls += 1
        return await request_exit()

    shell._exit.request_exit = counted_request_exit
    running = asyncio.create_task(shell.run_async())
    await waiting.wait()
    signal.raise_signal(signal.SIGINT)
    await asyncio.wait_for(running, timeout=1)

    assert calls == 1


def test_reentry_production_recovery_is_never_a_noop_default(tmp_path: Path) -> None:
    shell = build_product_shell(
        project_root=str(tmp_path), read_line=AsyncLines("/exit"),
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=_discovery, config_factory=_config,
    )
    assert type(shell._recovery) is RecoveryService


@async_test
async def test_recovered_resume_reaches_real_execution_gate_once(
    tmp_path: Path,
) -> None:
    _seed_resume(tmp_path)
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    snapshot = store.load_execution_resume("ses_1")
    first = await ProjectLifecycleService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
    ).resume(snapshot)
    assert first.should_start is True
    await RecoveryService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        recovery_run_id="restart_real_execution_gate",
    ).reconcile()
    store.close()

    class FailedWorker:
        def __init__(self) -> None:
            self.starts = 0
            self.started = asyncio.Event()
            self.handle = None

        async def start_task(self, request):
            self.starts += 1
            self.handle = WorkerHandle(
                "ses_reentry", request.agent_id, request.task_id,
                request.attempt_id,
            )
            self.started.set()
            return self.handle

        async def _events(self):
            assert self.handle is not None
            yield WorkerEvent(
                event_id="evt_reentry_failed", session_id=self.handle.session_id,
                agent_id=self.handle.agent_id, task_id=self.handle.task_id,
                attempt_id=self.handle.attempt_id, transport="acp", sequence=1,
                kind="failed", timestamp=NOW.isoformat(),
                payload={"reason": "stopped"},
            )

        def stream_events(self, handle):
            assert handle == self.handle
            return self._events()

        async def respond_permission(self, *args, **kwargs):
            raise AssertionError("no permission request expected")

        async def cancel_task(self, *args, **kwargs):
            raise AssertionError("no cancellation expected")

        async def collect_result(self, handle):
            assert handle == self.handle
            return WorkerResult(
                session_id=handle.session_id, agent_id=handle.agent_id,
                task_id=handle.task_id, attempt_id=handle.attempt_id,
                status="failed", payload={"reason": "stopped"},
            )

    worker = FailedWorker()

    class Reader(AsyncLines):
        async def __call__(self, prompt: str) -> str:
            value = await super().__call__(prompt)
            if value == "/exit":
                await asyncio.wait_for(worker.started.wait(), timeout=1)
            return value

    shell = build_product_shell(
        project_root=str(tmp_path), read_line=Reader("/resume", "/exit"),
        write_line=lambda _: None, clock_factory=lambda: FrozenClock(NOW),
        discovery_factory=_discovery, config_factory=_config,
        adapter_readiness={},
        adapter_composition_factory=lambda **_: SimpleNamespace(
            worker=lambda _backend: worker
        ),
    )
    await shell.run_async()

    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        assert worker.starts == 1
        assert reopened._require_writer().execute(
            "SELECT count(*) FROM events WHERE kind='project_resumed'"
        ).fetchone() == (2,)
    finally:
        reopened.close()
