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
