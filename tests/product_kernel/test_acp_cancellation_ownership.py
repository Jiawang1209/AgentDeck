from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path

import pytest
from acp import PROTOCOL_VERSION
from acp.schema import AgentCapabilities, InitializeResponse, NewSessionResponse

from agentdeck.adapters.acp_worker_connection import ACPWorkerConnection
from agentdeck.ports.worker import WorkerCancellationError


def _sync_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


class _CallbackWorker:
    async def session_update(self, *args, **kwargs) -> None:
        return None

    async def request_permission(self, *args, **kwargs) -> object:
        return object()


class _Connection:
    def __init__(self, owner: "_Owner") -> None:
        self.owner = owner

    async def initialize(self, protocol_version: int) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(self, *, cwd: str) -> NewSessionResponse:
        return NewSessionResponse(session_id="raw_session")

    async def prompt(self, *args, **kwargs) -> object:
        return object()

    async def cancel(self, session_id: str) -> None:
        connection = self.owner.connection_owner
        self.owner.detach_snapshots.append((
            connection.closed,
            connection._connection is None,
            connection._manager is None,
        ))
        self.owner.notification_calls += 1
        self.owner.notification_entered.set()
        if self.owner.notification_blocks:
            await self.owner.notification_release.wait()
        if self.owner.notification_failure is not None:
            raise self.owner.notification_failure


class _Owner:
    def __init__(self, tmp_path: Path) -> None:
        self.project_root = str(tmp_path)
        self.notification_calls = 0
        self.notification_entered = asyncio.Event()
        self.notification_release = asyncio.Event()
        self.notification_blocks = False
        self.notification_failure: BaseException | None = None
        self.reap_calls = 0
        self.reap_completed = 0
        self.reap_entered = asyncio.Event()
        self.reap_release = asyncio.Event()
        self.reap_blocks = False
        self.reap_failure: BaseException | None = None
        self.detach_snapshots: list[tuple[bool, bool, bool]] = []
        self.connection_owner: ACPWorkerConnection

    def build(self, *, timeout_seconds: float = 30.0) -> ACPWorkerConnection:
        @asynccontextmanager
        async def spawn(client, command, *args, **kwargs):
            try:
                yield _Connection(self), object()
            finally:
                self.reap_calls += 1
                self.reap_entered.set()
                if self.reap_blocks:
                    await self.reap_release.wait()
                self.reap_completed += 1
                if self.reap_failure is not None:
                    raise self.reap_failure

        connection = ACPWorkerConnection(
            ("/verified/adapter",), project_root=self.project_root,
            spawn_factory=spawn, timeout_seconds=timeout_seconds,
        )
        connection.on_connect(_CallbackWorker())
        self.connection_owner = connection
        return connection

    async def ready(self, *, timeout_seconds: float = 30.0) -> ACPWorkerConnection:
        connection = self.build(timeout_seconds=timeout_seconds)
        await connection.initialize(PROTOCOL_VERSION)
        await connection.new_session(cwd=self.project_root)
        return connection


def _assert_error(
    error: WorkerCancellationError, code: str, outcome_known: bool,
) -> None:
    assert type(error) is WorkerCancellationError
    assert (error.code, error.outcome_known) == (code, outcome_known)
    assert error.args == (code, outcome_known)
    assert error.__cause__ is None
    assert error.__context__ is None


def _timeout_after_claim_error(
    monkeypatch: pytest.MonkeyPatch, connection: ACPWorkerConnection,
    error: BaseException,
) -> None:
    claim_finished = asyncio.Event()
    original_wait_for = asyncio.wait_for
    wait_calls = 0

    async def failing_claim():
        claim_finished.set()
        raise error

    async def controlled_wait_for(awaitable, timeout):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            if isinstance(awaitable, asyncio.Future):
                awaitable.add_done_callback(
                    lambda future: None if future.cancelled() else future.exception()
                )
            await claim_finished.wait()
            raise TimeoutError
        return await original_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(connection, "_detach_cancel_owner", failing_claim)
    monkeypatch.setattr(asyncio, "wait_for", controlled_wait_for)


@pytest.mark.parametrize(
    ("missing", "code", "known", "notification_calls", "reap_calls"),
    [
        ("manager", "transport_disconnected", False, 0, 0),
        ("connection", "cancel_rejected", True, 0, 1),
    ],
)
@_sync_test
async def test_cancel_inconsistent_owner_pair_fails_closed(
    tmp_path: Path, missing: str, code: str, known: bool,
    notification_calls: int, reap_calls: int,
) -> None:
    owner = _Owner(tmp_path)
    connection = await owner.ready()
    manager = connection._manager
    setattr(connection, f"_{missing}", None)
    try:
        with pytest.raises(WorkerCancellationError) as captured:
            await connection.cancel("raw_session")
        _assert_error(captured.value, code, known)
        assert owner.notification_calls == notification_calls
        assert owner.reap_calls == reap_calls
        assert connection.closed is True
    finally:
        if missing == "manager" and manager is not None:
            await manager.__aexit__(None, None, None)


@_sync_test
async def test_concurrent_and_repeated_cancel_claim_owner_once(
    tmp_path: Path,
) -> None:
    owner = _Owner(tmp_path)
    owner.notification_blocks = True
    connection = await owner.ready()
    first = asyncio.create_task(connection.cancel("raw_session"))
    await owner.notification_entered.wait()
    second = asyncio.create_task(connection.cancel("raw_session"))
    for _ in range(10):
        if second.done() or owner.notification_calls == 2:
            break
        await asyncio.sleep(0)
    owner.notification_release.set()
    first_result, second_result = await asyncio.gather(
        first, second, return_exceptions=True,
    )
    assert first_result is None
    assert type(second_result) is WorkerCancellationError
    _assert_error(second_result, "cancel_rejected", True)
    with pytest.raises(WorkerCancellationError) as repeated:
        await connection.cancel("raw_session")
    _assert_error(repeated.value, "cancel_rejected", True)
    assert owner.notification_calls == 1
    assert owner.reap_calls == 1
    assert owner.detach_snapshots == [(True, True, True)]


@_sync_test
async def test_caller_cancellation_finishes_claim_and_owner_cleanup(
    tmp_path: Path,
) -> None:
    owner = _Owner(tmp_path)
    connection = await owner.ready(timeout_seconds=0.1)
    await connection._close_lock.acquire()
    task = asyncio.create_task(connection.cancel("raw_session"))
    await asyncio.sleep(0)
    assert task.cancel("private lock cancellation") is True
    await asyncio.sleep(0)
    assert task.cancelling() == 1
    connection._close_lock.release()
    try:
        with pytest.raises(WorkerCancellationError) as captured:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.3)
    finally:
        if connection._close_lock.locked():
            connection._close_lock.release()
    _assert_error(captured.value, "cancel_timeout", False)
    assert owner.notification_calls == 0
    assert connection.closed is True
    assert connection._connection is None and connection._manager is None
    assert owner.reap_calls == 1


@_sync_test
async def test_claim_timeout_drains_task_and_preserves_explicit_close(
    tmp_path: Path,
) -> None:
    owner = _Owner(tmp_path)
    connection = await owner.ready(timeout_seconds=0.01)
    await connection._close_lock.acquire()
    try:
        with pytest.raises(WorkerCancellationError) as captured:
            await connection.cancel("raw_session")
    finally:
        connection._close_lock.release()
    _assert_error(captured.value, "cancel_timeout", False)
    assert connection.closed is False
    assert connection._connection is not None and connection._manager is not None
    assert owner.notification_calls == 0 and owner.reap_calls == 0
    current = asyncio.current_task()
    assert [
        task for task in asyncio.all_tasks()
        if task is not current and not task.done()
    ] == []
    await connection.aclose()
    await connection.aclose()
    assert owner.reap_calls == 1


@_sync_test
async def test_claim_timeout_settles_completed_owner_pair_before_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _Owner(tmp_path)
    connection = await owner.ready(timeout_seconds=0.01)
    await connection._close_lock.acquire()
    detached = asyncio.Event()
    original_detach = connection._detach_cancel_owner
    original_wait_for = asyncio.wait_for
    wait_calls = 0

    async def controlled_detach():
        pair = await original_detach()
        detached.set()
        return pair

    async def timeout_after_detach(awaitable, timeout):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            connection._close_lock.release()
            await detached.wait()
            raise TimeoutError
        return await original_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(connection, "_detach_cancel_owner", controlled_detach)
    monkeypatch.setattr(asyncio, "wait_for", timeout_after_detach)
    with pytest.raises(WorkerCancellationError) as captured:
        await connection.cancel("raw_session")
    _assert_error(captured.value, "cancel_timeout", False)
    assert owner.notification_calls == 0
    assert connection.closed is True
    assert connection._connection is None and connection._manager is None
    assert owner.reap_calls == 1
    current = asyncio.current_task()
    assert [
        task for task in asyncio.all_tasks()
        if task is not current and not task.done()
    ] == []
    await connection.aclose()
    await connection.aclose()
    with pytest.raises(WorkerCancellationError) as repeated:
        await connection.cancel("raw_session")
    _assert_error(repeated.value, "cancel_rejected", True)
    assert owner.reap_calls == 1


@_sync_test
async def test_claim_timeout_authority_survives_settle_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _Owner(tmp_path)
    connection = await owner.ready(timeout_seconds=0.01)
    _timeout_after_claim_error(
        monkeypatch, connection, RuntimeError("private settle body"),
    )
    with pytest.raises(WorkerCancellationError) as captured:
        await connection.cancel("raw_session")
    _assert_error(captured.value, "cancel_timeout", False)
    assert owner.notification_calls == 0 and owner.reap_calls == 0
    current = asyncio.current_task()
    assert [
        task for task in asyncio.all_tasks()
        if task is not current and not task.done()
    ] == []
    await connection.aclose()
    assert owner.reap_calls == 1


@_sync_test
async def test_claim_settle_memory_error_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _Owner(tmp_path)
    connection = await owner.ready(timeout_seconds=0.01)
    _timeout_after_claim_error(monkeypatch, connection, MemoryError())
    with pytest.raises(MemoryError):
        await connection.cancel("raw_session")
    current = asyncio.current_task()
    assert [
        task for task in asyncio.all_tasks()
        if task is not current and not task.done()
    ] == []
    await connection.aclose()
    assert owner.reap_calls == 1


@_sync_test
async def test_aclose_releases_lock_before_owner_reap(tmp_path: Path) -> None:
    owner = _Owner(tmp_path)
    owner.reap_blocks = True
    connection = await owner.ready(timeout_seconds=0.01)
    closing = asyncio.create_task(connection.aclose())
    await owner.reap_entered.wait()
    try:
        await asyncio.wait_for(connection._close_lock.acquire(), timeout=0.01)
        connection._close_lock.release()
        with pytest.raises(WorkerCancellationError) as captured:
            await connection.cancel("raw_session")
        _assert_error(captured.value, "cancel_timeout", False)
    finally:
        owner.reap_release.set()
        await closing
    assert owner.notification_calls == 0
    assert owner.reap_calls == 1


@_sync_test
async def test_prompt_cancellation_cannot_cancel_shared_owner_reap(
    tmp_path: Path,
) -> None:
    owner = _Owner(tmp_path)
    owner.reap_blocks = True
    connection = await owner.ready(timeout_seconds=0.01)
    prompt = asyncio.create_task(connection.prompt("raw_session", []))
    await owner.reap_entered.wait()

    prompt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await prompt
    with pytest.raises(WorkerCancellationError) as captured:
        await asyncio.wait_for(connection.cancel("raw_session"), timeout=0.2)
    _assert_error(captured.value, "cancel_timeout", False)
    assert owner.reap_calls == 1 and owner.reap_completed == 0

    owner.reap_release.set()
    await asyncio.wait_for(connection.aclose(), timeout=0.2)
    assert owner.reap_calls == owner.reap_completed == 1
    with pytest.raises(WorkerCancellationError) as repeated:
        await connection.cancel("raw_session")
    _assert_error(repeated.value, "cancel_rejected", True)
    assert owner.reap_calls == 1


@pytest.mark.parametrize("fatal", [MemoryError(), SystemExit(17)])
@_sync_test
async def test_notification_fatal_reaps_owner_and_propagates_exactly(
    tmp_path: Path, fatal: BaseException,
) -> None:
    owner = _Owner(tmp_path)
    owner.notification_failure = fatal
    connection = await owner.ready(timeout_seconds=0.01)

    with pytest.raises(type(fatal)) as captured:
        await connection.cancel("raw_session")

    assert captured.value is fatal
    assert owner.notification_calls == 1
    assert owner.reap_calls == owner.reap_completed == 1
    assert connection.closed is True
    assert connection._connection is None and connection._manager is None
    await connection.aclose()
    assert owner.reap_calls == 1


@_sync_test
async def test_late_shutdown_failure_is_consumed_and_remains_classifiable(
    tmp_path: Path,
) -> None:
    hostile = "credential=late-private-shutdown"
    owner = _Owner(tmp_path)
    owner.reap_blocks = True
    owner.reap_failure = RuntimeError(hostile)
    connection = await owner.ready(timeout_seconds=0.01)
    loop_contexts: list[dict[str, object]] = []
    asyncio.get_running_loop().set_exception_handler(
        lambda _loop, context: loop_contexts.append(context)
    )

    with pytest.raises(WorkerCancellationError) as timed_out:
        await connection.cancel("raw_session")
    _assert_error(timed_out.value, "cancel_timeout", False)
    owner.reap_release.set()
    shutdown = connection._shutdown_task
    assert shutdown is not None
    await asyncio.wait({shutdown})

    assert shutdown.done() and shutdown._log_traceback is False
    assert loop_contexts == []
    with pytest.raises(WorkerCancellationError) as later:
        await connection.cancel("raw_session")
    _assert_error(later.value, "transport_disconnected", False)
    assert hostile not in repr(later.value)
    assert loop_contexts == []
