from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import wraps
import json
import os
from pathlib import Path

import pytest
from acp import PROTOCOL_VERSION
from acp.schema import (
    AgentCapabilities, InitializeResponse, NewSessionResponse, PromptResponse,
)

from agentdeck.adapters.acp import ACPWorker, ACPWorkerError
from agentdeck.adapters.acp_worker_connection import ACPWorkerConnection
from agentdeck.ports.worker import WorkerCancellationError

from .fakes import FrozenClock
from .fixtures.fake_acp_stdio_agent import fake_command
from .worker_contract import task_request


NOW = datetime(2026, 7, 20, 8, 0, 0, tzinfo=timezone.utc)


def _sync_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def _calls(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


@_sync_test
async def test_official_stdio_connection_is_lazy_forwards_and_reaps_process(
    tmp_path: Path,
) -> None:
    log = tmp_path / "calls.jsonl"
    proposal = tmp_path / "proposal.json"
    proposal.write_text('{"unused":true}', encoding="utf-8")
    owner = ACPWorkerConnection(
        fake_command(log_path=log, proposal_path=proposal),
        project_root=str(tmp_path), environment={},
    )
    worker = ACPWorker(
        agent=owner, project_root=str(tmp_path), clock=FrozenClock(NOW),
        project_boundary_enforced=True,
    )
    assert _calls(log) == [] and owner.closed is False

    handle = await worker.start_task(task_request())
    events = [event async for event in worker.stream_events(handle)]
    assert (await worker.collect_result(handle)).status == "completed"
    assert {event.kind for event in events} >= {"started", "message", "completed"}
    assert owner.closed is True
    pid = next(item["pid"] for item in _calls(log) if item["call"] == "initialize")
    await asyncio.sleep(0)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


class _Connection:
    def __init__(self, failure: str | None = None) -> None:
        self.failure = failure
        self.cancelled = False

    async def initialize(self, protocol_version: int) -> InitializeResponse:
        if self.failure == "initialize":
            raise RuntimeError("private initialization body")
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(self, *, cwd: str) -> NewSessionResponse:
        if self.failure == "session":
            raise RuntimeError("private session body")
        return NewSessionResponse(session_id=f"session:{cwd}")

    async def prompt(self, session_id: str, prompt: object) -> PromptResponse:
        if self.failure == "prompt":
            raise RuntimeError("private prompt body")
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str) -> None:
        self.cancelled = True


class _CallbackWorker:
    async def session_update(self, *args, **kwargs) -> None:
        return None

    async def request_permission(self, *args, **kwargs) -> object:
        return object()


class _CancellationConnection:
    def __init__(self, owner: "_CancellationOwner") -> None:
        self.owner = owner

    async def initialize(self, protocol_version: int) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(self, *, cwd: str) -> NewSessionResponse:
        return NewSessionResponse(session_id="raw_session")

    async def cancel(self, session_id: str) -> None:
        self.owner.calls.append(("cancel", session_id))
        self.owner.cancel_count += 1
        self.owner.cancel_entered.set()
        if self.owner.cancel_blocks:
            await asyncio.Event().wait()
        if self.owner.cancel_failure is not None:
            raise self.owner.cancel_failure


class _CancellationOwner:
    def __init__(self, tmp_path: Path) -> None:
        self.project_root = str(tmp_path)
        self.calls: list[tuple[object, ...]] = []
        self.cancel_count = 0
        self.cancel_entered = asyncio.Event()
        self.cancel_blocks = False
        self.cancel_failure: BaseException | None = None
        self.reap_blocks = False
        self.reap_failure: BaseException | None = None

    def connection(self, *, timeout_seconds: float = 30.0) -> ACPWorkerConnection:
        @asynccontextmanager
        async def spawn(client, command, *args, **kwargs):
            try:
                yield _CancellationConnection(self), object()
            finally:
                if self.reap_blocks:
                    await asyncio.Event().wait()
                if self.reap_failure is not None:
                    raise self.reap_failure
                self.calls.append(("owner_reaped",))

        connection = ACPWorkerConnection(
            ("/verified/adapter",), project_root=self.project_root,
            spawn_factory=spawn, timeout_seconds=timeout_seconds,
        )
        connection.on_connect(_CallbackWorker())
        return connection


def _assert_cancellation_error(
    error: WorkerCancellationError, code: str, outcome_known: bool,
    *forbidden: str,
) -> None:
    assert type(error) is WorkerCancellationError
    assert error.code == code
    assert error.outcome_known is outcome_known
    assert error.args == (code, outcome_known)
    assert error.__dict__ == {"code": code, "outcome_known": outcome_known}
    assert str(error) == repr((code, outcome_known))
    assert repr(error) == f"WorkerCancellationError({code!r}, {outcome_known!r})"
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = repr(error) + str(error) + repr(error.args) + repr(error.__dict__)
    assert all(value not in rendered for value in forbidden)


def _injected_owner(
    tmp_path: Path, failure: str | None,
) -> tuple[ACPWorkerConnection, list[tuple[str, object]]]:
    lifecycle: list[tuple[str, object]] = []

    @asynccontextmanager
    async def spawn(client, command, *args, **kwargs):
        lifecycle.append(("spawn", (client, command, args, kwargs)))
        try:
            yield _Connection(failure), object()
        finally:
            lifecycle.append(("closed", failure))

    owner = ACPWorkerConnection(
        ("/verified/adapter",), project_root=str(tmp_path),
        environment={"VERIFIED_EXECUTABLE": "/verified/cli"},
        spawn_factory=spawn,
    )
    return owner, lifecycle


@pytest.mark.parametrize(
    ("failure", "diagnostic"),
    (("initialize", "acp_initialization_failed"),
     ("session", "acp_session_failed")),
)
@_sync_test
async def test_connection_closes_on_initialize_or_session_failure(
    tmp_path: Path, failure: str, diagnostic: str,
) -> None:
    owner, lifecycle = _injected_owner(tmp_path, failure)
    worker = ACPWorker(
        agent=owner, project_root=str(tmp_path), clock=FrozenClock(NOW),
        project_boundary_enforced=True,
    )
    with pytest.raises(ACPWorkerError) as caught:
        await worker.start_task(task_request())
    assert caught.value.diagnostic.code == diagnostic
    assert owner.closed is True
    assert [item[0] for item in lifecycle] == ["spawn", "closed"]


@_sync_test
async def test_connection_closes_after_prompt_failure(tmp_path: Path) -> None:
    owner, lifecycle = _injected_owner(tmp_path, "prompt")
    worker = ACPWorker(
        agent=owner, project_root=str(tmp_path), clock=FrozenClock(NOW),
        project_boundary_enforced=True,
    )
    handle = await worker.start_task(task_request())
    events = [event async for event in worker.stream_events(handle)]
    with pytest.raises(ACPWorkerError):
        await worker.collect_result(handle)
    assert events[-1].kind == "failed"
    assert owner.closed is True
    assert [item[0] for item in lifecycle] == ["spawn", "closed"]


@_sync_test
async def test_spawn_receives_exact_absolute_argv_root_and_merged_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/changed/after-readiness")
    owner, lifecycle = _injected_owner(tmp_path, None)
    worker = ACPWorker(
        agent=owner, project_root=str(tmp_path), clock=FrozenClock(NOW),
        project_boundary_enforced=True,
    )
    handle = await worker.start_task(task_request())
    _ = [event async for event in worker.stream_events(handle)]
    spawn = lifecycle[0][1]
    assert spawn[1:3] == ("/verified/adapter", ())
    kwargs = spawn[3]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"]["VERIFIED_EXECUTABLE"] == "/verified/cli"
    assert kwargs["env"]["PATH"] == "/changed/after-readiness"
    assert kwargs["receive_timeout"] == 30.0
    assert owner.closed is True


def test_connection_rejects_relative_command_root_or_hostile_environment(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="absolute"):
        ACPWorkerConnection(("adapter",), project_root=str(tmp_path))
    with pytest.raises(ValueError, match="canonical absolute"):
        ACPWorkerConnection(("/adapter",), project_root="relative")
    with pytest.raises(ValueError, match="environment"):
        ACPWorkerConnection(
            ("/adapter",), project_root=str(tmp_path),
            environment={"BAD\nKEY": "value"},
        )


@_sync_test
async def test_cancel_succeeds_only_after_notification_and_owner_reap(
    tmp_path: Path,
) -> None:
    owner = _CancellationOwner(tmp_path)
    connection = owner.connection()
    await connection.initialize(PROTOCOL_VERSION)
    await connection.new_session(cwd=owner.project_root)

    await connection.cancel("raw_session")

    assert owner.calls[-2:] == [
        ("cancel", "raw_session"),
        ("owner_reaped",),
    ]
    assert connection.closed is True


@_sync_test
async def test_cancel_notification_timeout_is_closed_and_content_free(
    tmp_path: Path,
) -> None:
    owner = _CancellationOwner(tmp_path)
    owner.cancel_blocks = True
    connection = owner.connection(timeout_seconds=0.01)
    await connection.initialize(PROTOCOL_VERSION)
    await connection.new_session(cwd=owner.project_root)

    with pytest.raises(WorkerCancellationError) as captured:
        await connection.cancel("raw_session")

    _assert_cancellation_error(
        captured.value, "cancel_timeout", False, "raw_session",
    )
    assert connection.closed is True


@_sync_test
async def test_owner_reap_timeout_never_reports_cancel_success(
    tmp_path: Path,
) -> None:
    owner = _CancellationOwner(tmp_path)
    owner.reap_blocks = True
    connection = owner.connection(timeout_seconds=0.01)
    await connection.initialize(PROTOCOL_VERSION)
    await connection.new_session(cwd=owner.project_root)

    with pytest.raises(WorkerCancellationError) as captured:
        await connection.cancel("raw_session")

    _assert_cancellation_error(captured.value, "cancel_timeout", False)
    assert owner.cancel_count == 1
    assert connection.closed is True


@_sync_test
async def test_owner_reap_failure_is_transport_disconnected(
    tmp_path: Path,
) -> None:
    owner = _CancellationOwner(tmp_path)
    owner.reap_failure = RuntimeError("private owner shutdown body")
    connection = owner.connection()
    await connection.initialize(PROTOCOL_VERSION)
    await connection.new_session(cwd=owner.project_root)

    with pytest.raises(WorkerCancellationError) as captured:
        await connection.cancel("raw_session")

    _assert_cancellation_error(
        captured.value, "transport_disconnected", False,
        "private owner shutdown body", "raw_session",
    )
    assert owner.cancel_count == 1 and connection.closed is True


@pytest.mark.parametrize(
    "raw_failure",
    [
        BrokenPipeError("private broken pipe"),
        ConnectionError("private connection body"),
        EOFError("private eof body"),
        RuntimeError("private unexpected SDK body"),
    ],
)
@_sync_test
async def test_cancel_transport_failures_are_closed_and_content_free(
    tmp_path: Path, raw_failure: BaseException,
) -> None:
    owner = _CancellationOwner(tmp_path)
    owner.cancel_failure = raw_failure
    connection = owner.connection()
    await connection.initialize(PROTOCOL_VERSION)
    await connection.new_session(cwd=owner.project_root)

    with pytest.raises(WorkerCancellationError) as captured:
        await connection.cancel("raw_session")

    _assert_cancellation_error(
        captured.value, "transport_disconnected", False,
        "raw_session", str(raw_failure),
    )
    assert connection.closed is True


@_sync_test
async def test_cancel_caller_cancellation_propagates_after_owner_reap(
    tmp_path: Path,
) -> None:
    owner = _CancellationOwner(tmp_path)
    owner.cancel_blocks = True
    connection = owner.connection()
    await connection.initialize(PROTOCOL_VERSION)
    await connection.new_session(cwd=owner.project_root)
    task = asyncio.create_task(connection.cancel("raw_session"))
    await owner.cancel_entered.wait()

    task.cancel("private caller cancellation")
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
    assert owner.calls[-1] == ("owner_reaped",)
    assert connection.closed is True


@_sync_test
async def test_cancel_hostile_raw_text_never_crosses_the_port(
    tmp_path: Path,
) -> None:
    hostile = "ghp_" + ("A" * 36)
    owner = _CancellationOwner(tmp_path)
    owner.cancel_failure = RuntimeError(hostile)
    connection = owner.connection()
    await connection.initialize(PROTOCOL_VERSION)
    await connection.new_session(cwd=owner.project_root)

    with pytest.raises(WorkerCancellationError) as captured:
        await connection.cancel("raw_session")

    _assert_cancellation_error(
        captured.value, "transport_disconnected", False,
        hostile, "raw_session",
    )
    assert connection.closed is True


@pytest.mark.parametrize("lifecycle", ["never_connected", "already_closed"])
@_sync_test
async def test_cancel_without_live_connection_is_known_rejection(
    tmp_path: Path, lifecycle: str,
) -> None:
    owner = _CancellationOwner(tmp_path)
    connection = owner.connection()
    if lifecycle == "already_closed":
        await connection.aclose()

    with pytest.raises(WorkerCancellationError) as captured:
        await connection.cancel("raw_session")

    _assert_cancellation_error(
        captured.value, "cancel_rejected", True, "raw_session",
    )
    assert owner.cancel_count == 0
    assert connection.closed is True


@_sync_test
async def test_close_before_start_is_terminal_and_never_spawns(
    tmp_path: Path,
) -> None:
    starts: list[object] = []

    def forbidden_spawn(*args, **kwargs):
        starts.append((args, kwargs))
        raise AssertionError("spawn must remain unreachable")

    owner = ACPWorkerConnection(
        ("/verified/adapter",), project_root=str(tmp_path),
        spawn_factory=forbidden_spawn,
    )
    ACPWorker(
        agent=owner, project_root=str(tmp_path), clock=FrozenClock(NOW),
        project_boundary_enforced=True,
    )
    await owner.aclose()

    assert owner.closed is True
    with pytest.raises(ValueError, match="lifecycle"):
        await owner.initialize(PROTOCOL_VERSION)
    assert starts == []


@_sync_test
async def test_synchronous_spawn_failure_is_terminal_and_never_retried(
    tmp_path: Path,
) -> None:
    starts: list[object] = []

    def broken_spawn(*args, **kwargs):
        starts.append((args, kwargs))
        raise RuntimeError("private synchronous spawn failure")

    owner = ACPWorkerConnection(
        ("/verified/adapter",), project_root=str(tmp_path),
        spawn_factory=broken_spawn,
    )
    ACPWorker(
        agent=owner, project_root=str(tmp_path), clock=FrozenClock(NOW),
        project_boundary_enforced=True,
    )
    with pytest.raises(RuntimeError, match="synchronous spawn failure"):
        await owner.initialize(PROTOCOL_VERSION)

    assert owner.closed is True
    with pytest.raises(ValueError, match="lifecycle"):
        await owner.initialize(PROTOCOL_VERSION)
    assert len(starts) == 1
