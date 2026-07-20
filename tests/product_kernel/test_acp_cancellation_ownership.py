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


class _Owner:
    def __init__(self, tmp_path: Path) -> None:
        self.project_root = str(tmp_path)
        self.notification_calls = 0
        self.notification_entered = asyncio.Event()
        self.notification_release = asyncio.Event()
        self.notification_blocks = False
        self.reap_calls = 0
        self.reap_entered = asyncio.Event()
        self.reap_release = asyncio.Event()
        self.reap_blocks = False
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
async def test_aclose_releases_lock_before_owner_reap(tmp_path: Path) -> None:
    owner = _Owner(tmp_path)
    owner.reap_blocks = True
    connection = await owner.ready()
    closing = asyncio.create_task(connection.aclose())
    await owner.reap_entered.wait()
    try:
        await asyncio.wait_for(connection._close_lock.acquire(), timeout=0.01)
        connection._close_lock.release()
        with pytest.raises(WorkerCancellationError) as captured:
            await connection.cancel("raw_session")
        _assert_error(captured.value, "cancel_rejected", True)
    finally:
        owner.reap_release.set()
        await closing
    assert owner.notification_calls == 0
    assert owner.reap_calls == 1
