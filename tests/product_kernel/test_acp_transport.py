from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import wraps
import json
import os
from pathlib import Path
import traceback

import pytest
from acp import PROTOCOL_VERSION
from acp.schema import AgentCapabilities, InitializeResponse, PromptCapabilities

from agentdeck.adapters.acp_transport import ACPStdioTransport
from agentdeck.ports.transport import (
    TransportFailure,
    TransportPermissionDecision,
    TransportPromptPart,
    TransportUpdateKind,
)

from .fixtures.fake_acp_stdio_agent import (
    OVERSIZE_MARKER,
    PROPOSAL_MIME,
    PROPOSAL_URI,
    fake_command,
)
from .test_leader_contract import valid_proposal


def _write_proposal(path: Path) -> None:
    path.write_text(json.dumps(valid_proposal()), encoding="utf-8")


def _calls(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _exception_text(error: BaseException) -> str:
    rendered = ["".join(traceback.format_exception(error))]
    pending = [error]
    while pending:
        current = pending.pop()
        rendered.append(str(current))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return "\n".join(rendered)


def _sync_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


@_sync_test
async def test_client_factory_is_injected_and_stays_lazy(tmp_path: Path) -> None:
    calls: list[tuple[object, ...]] = []

    class Connection:
        async def initialize(self, protocol_version: int) -> InitializeResponse:
            calls.append(("initialize", protocol_version))
            return InitializeResponse(
                protocol_version=PROTOCOL_VERSION,
                agent_capabilities=AgentCapabilities(
                    prompt_capabilities=PromptCapabilities(embedded_context=True)
                ),
            )

    @asynccontextmanager
    async def factory(callback, command, project_root, max_bytes, timeout_seconds):
        calls.append(("factory", callback, command, project_root, max_bytes, timeout_seconds))
        yield Connection()

    transport = ACPStdioTransport(
        ("never-started",), project_root=str(tmp_path), client_factory=factory
    )
    assert calls == []
    async with transport:
        assert (await transport.initialize()).embedded_context is True
    assert calls[0][0] == "factory"
    assert calls[0][2:4] == (("never-started",), str(tmp_path))
    assert calls[1] == ("initialize", PROTOCOL_VERSION)


@_sync_test
async def test_stdio_transport_is_lazy_and_uses_official_structured_updates(
    tmp_path: Path,
) -> None:
    log = tmp_path / "calls.jsonl"
    proposal = tmp_path / "proposal.json"
    _write_proposal(proposal)
    transport = ACPStdioTransport(
        fake_command(log_path=log, proposal_path=proposal),
        project_root=str(tmp_path),
    )
    assert _calls(log) == []
    async with transport:
        capabilities = await transport.initialize()
        session = await transport.new_session()
        prompt = asyncio.create_task(transport.prompt(session, (
            TransportPromptPart.text("Return a structured proposal."),
            TransportPromptPart.resource(
                uri="agentdeck://leader/request",
                mime_type="application/vnd.agentdeck.request+json",
                text="{}",
            ),
        )))
        updates = [item async for item in transport.stream_updates(session)]
        response = await prompt
    assert capabilities.embedded_context is True
    assert capabilities.resume_session is True
    assert response.stop_reason == "end_turn"
    assert [item.kind for item in updates] == [
        TransportUpdateKind.MESSAGE, TransportUpdateKind.ARTIFACT,
    ]
    assert updates[1].artifact is not None
    assert (
        updates[1].artifact.uri,
        updates[1].artifact.mime_type,
    ) == (PROPOSAL_URI, PROPOSAL_MIME)
    assert json.loads(updates[1].artifact.text)["objective"] == "Build an accessible page"
    assert [item["call"] for item in _calls(log)] == [
        "initialize", "session/new", "session/prompt",
    ]


@_sync_test
async def test_transport_carries_resume_cancel_and_permission_response(
    tmp_path: Path,
) -> None:
    log = tmp_path / "calls.jsonl"
    proposal = tmp_path / "proposal.json"
    _write_proposal(proposal)
    command = fake_command(log_path=log, proposal_path=proposal, mode="permission")
    async with ACPStdioTransport(command, project_root=str(tmp_path)) as transport:
        await transport.initialize()
        session = await transport.new_session()
        await transport.resume_session(session)
        prompt = asyncio.create_task(transport.prompt(
            session, (TransportPromptPart.text("permission transport check"),)
        ))
        stream = transport.stream_updates(session)
        update = await anext(stream)
        assert update.kind is TransportUpdateKind.PERMISSION
        assert update.permission is not None
        await transport.respond_permission(
            session,
            TransportPermissionDecision(
                request_id=update.permission.request_id,
                allowed=False,
                reason="Leader planning cannot perform tools",
            ),
        )
        assert (await prompt).stop_reason == "end_turn"
        assert [item async for item in stream] == []
        await transport.cancel(session)
    names = [item["call"] for item in _calls(log)]
    assert names == [
        "initialize", "session/new", "session/resume", "session/prompt",
        "permission/result", "session/cancel",
    ]


@_sync_test
async def test_stdio_response_bound_is_typed_content_free_and_closes_process(
    tmp_path: Path,
) -> None:
    log = tmp_path / "calls.jsonl"
    proposal = tmp_path / "proposal.json"
    _write_proposal(proposal)
    command = fake_command(log_path=log, proposal_path=proposal, mode="oversize")
    with pytest.raises(TransportFailure, match="response_oversize") as caught:
        async with ACPStdioTransport(
            command, project_root=str(tmp_path), max_bytes=4096
        ) as transport:
            await transport.initialize()
            session = await transport.new_session()
            prompt = asyncio.create_task(transport.prompt(
                session, (TransportPromptPart.text("bounded"),)
            ))
            async for _update in transport.stream_updates(session):
                pass
            await prompt
    rendered = _exception_text(caught.value)
    assert OVERSIZE_MARKER not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    pid = next(item["pid"] for item in _calls(log) if item["call"] == "initialize")
    await asyncio.sleep(0)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_command_is_argv_only_and_rejects_shell_or_hostile_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="argv"):
        ACPStdioTransport("python fake.py", project_root=str(tmp_path))
    with pytest.raises(ValueError, match="argv"):
        ACPStdioTransport(("python", ""), project_root=str(tmp_path))
    with pytest.raises(ValueError, match="project root"):
        ACPStdioTransport(("python",), project_root="")
    hostile = "\ud800"
    with pytest.raises(ValueError, match="argv") as caught:
        ACPStdioTransport(("python", hostile), project_root=str(tmp_path))
    assert hostile not in _exception_text(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
