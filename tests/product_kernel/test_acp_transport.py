from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, redirect_stderr, suppress
from functools import wraps
import gc
import io
import json
import os
from pathlib import Path
import traceback
from types import SimpleNamespace
import warnings

import pytest
from acp import PROTOCOL_VERSION
from acp.schema import (
    AgentCapabilities,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptCapabilities,
    PromptResponse,
    RequestPermissionResponse,
    ToolCallUpdate,
)

from agentdeck.adapters.acp_transport import ACPStdioTransport, _session_provenance
from agentdeck.ports.transport import (
    TransportFailure,
    TransportFailureCode,
    TransportPermissionDecision,
    TransportPromptPart,
    TransportSession,
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

def test_session_provenance_is_bounded_explicit_and_concrete() -> None:
    actual = {"agentdeck": {"resolved_model": "gpt-5.5", "server_version": "0.131.0"}}
    assert _session_provenance(None) == (None, None)
    assert _session_provenance(actual) == ("gpt-5.5", "0.131.0")
    for invalid in ({"agentdeck": {}}, {"agentdeck": {"resolved_model": "native-default", "server_version": "0.131.0"}}):
        with pytest.raises(ValueError, match="provenance"):
            _session_provenance(invalid)


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
    async def factory(callback, command, project_root, max_bytes, timeout_seconds,
                      environment):
        calls.append(("factory", callback, command, project_root, max_bytes,
                      timeout_seconds, environment))
        yield Connection()
    transport = ACPStdioTransport(
        ("never-started",), project_root=str(tmp_path), client_factory=factory,
        environment={"CLAUDE_CODE_EXECUTABLE": "/verified/claude"},
    )
    assert calls == []
    async with transport:
        assert (await transport.initialize()).embedded_context is True
    assert calls[0][0] == "factory"
    assert calls[0][2:4] == (("never-started",), str(tmp_path))
    assert calls[0][6]["CLAUDE_CODE_EXECUTABLE"] == "/verified/claude"
    assert calls[0][6]["PATH"] == os.environ["PATH"]
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


@_sync_test
async def test_factory_and_manager_enter_failures_are_typed_and_content_free(
    tmp_path: Path,
) -> None:
    marker = "secret-synchronous-factory-failure"

    def broken_factory(*_args):
        raise RuntimeError(marker)

    async with ACPStdioTransport(
        ("unused",), project_root=str(tmp_path), client_factory=broken_factory
    ) as transport:
        with pytest.raises(TransportFailure, match="initialization_failed") as caught:
            await transport.initialize()
    assert marker not in _exception_text(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None

    @asynccontextmanager
    async def hanging_factory(*_args):
        await asyncio.sleep(1)
        yield object()

    with pytest.raises(TransportFailure, match="timeout") as timed_out:
        async with ACPStdioTransport(
            ("unused",), project_root=str(tmp_path), timeout_seconds=0.03,
            client_factory=hanging_factory,
        ) as transport:
            await transport.initialize()
    assert timed_out.value.transport_code is TransportFailureCode.TIMEOUT
    assert timed_out.value.__cause__ is None and timed_out.value.__context__ is None


@_sync_test
async def test_connection_session_and_close_share_one_total_deadline(
    tmp_path: Path,
) -> None:
    class Connection:
        async def initialize(self, _version: int) -> InitializeResponse:
            await asyncio.sleep(0.08)
            return InitializeResponse(
                protocol_version=PROTOCOL_VERSION,
                agent_capabilities=AgentCapabilities(
                    prompt_capabilities=PromptCapabilities(embedded_context=True)
                ),
            )

        async def new_session(self, *, cwd: str) -> NewSessionResponse:
            await asyncio.sleep(0.08)
            return NewSessionResponse(session_id=f"persisted:{cwd}")

    @asynccontextmanager
    async def staged_factory(*_args):
        await asyncio.sleep(0.08)
        yield Connection()

    with pytest.raises(TransportFailure, match="timeout") as caught:
        async with ACPStdioTransport(
            ("unused",), project_root=str(tmp_path), timeout_seconds=0.18,
            client_factory=staged_factory,
        ) as transport:
            await transport.initialize()
            await transport.new_session()
    assert caught.value.transport_code is TransportFailureCode.TIMEOUT

    @asynccontextmanager
    async def hanging_close_factory(*_args):
        try:
            yield Connection()
        finally:
            await asyncio.sleep(1)

    with pytest.raises(TransportFailure, match="timeout") as close_failure:
        async with ACPStdioTransport(
            ("unused",), project_root=str(tmp_path), timeout_seconds=0.03,
            client_factory=hanging_close_factory,
        ) as transport:
            await transport.initialize()
    assert close_failure.value.__cause__ is None
    assert close_failure.value.__context__ is None


@_sync_test
async def test_update_drain_timeout_is_typed_and_uses_remaining_budget(
    tmp_path: Path,
) -> None:
    callback_slot: list[object] = []

    class Connection:
        async def initialize(self, _version: int) -> InitializeResponse:
            return InitializeResponse(
                protocol_version=PROTOCOL_VERSION,
                agent_capabilities=AgentCapabilities(
                    prompt_capabilities=PromptCapabilities(embedded_context=True)
                ),
            )

        async def new_session(self, *, cwd: str) -> NewSessionResponse:
            return NewSessionResponse(session_id=f"drain:{cwd}")

        async def prompt(self, _session_id: str, _parts: list[object]) -> PromptResponse:
            callback = callback_slot[0]
            callback.observe(SimpleNamespace(
                direction=SimpleNamespace(value="incoming"),
                message={"method": "session/update"},
            ))
            return PromptResponse(stop_reason="end_turn")

    @asynccontextmanager
    async def factory(callback, *_args):
        callback_slot.append(callback)
        yield Connection()

    with pytest.raises(TransportFailure, match="timeout") as caught:
        async with ACPStdioTransport(
            ("unused",), project_root=str(tmp_path), timeout_seconds=0.03,
            client_factory=factory,
        ) as transport:
            await transport.initialize()
            session = await transport.new_session()
            await transport.prompt(session, (TransportPromptPart.text("drain"),))
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@_sync_test
async def test_fresh_transport_can_resume_a_persisted_session(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    proposal = tmp_path / "proposal.json"
    _write_proposal(proposal)
    persisted = TransportSession("persisted-session")
    async with ACPStdioTransport(
        fake_command(log_path=log, proposal_path=proposal),
        project_root=str(tmp_path),
    ) as transport:
        await transport.initialize()
        assert await transport.resume_session(persisted) == persisted
    assert [item["call"] for item in _calls(log)] == [
        "initialize", "session/resume",
    ]


@pytest.mark.parametrize("cancel_mode", ["success", "async_failure", "sync_failure"])
@_sync_test
async def test_cancel_always_clears_pending_permission_waiter(
    tmp_path: Path, cancel_mode: str,
) -> None:
    callback_slot: list[object] = []

    class Connection:
        async def initialize(self, _version: int) -> InitializeResponse:
            return InitializeResponse(
                protocol_version=PROTOCOL_VERSION,
                agent_capabilities=AgentCapabilities(
                    prompt_capabilities=PromptCapabilities(embedded_context=True)
                ),
            )

        async def new_session(self, *, cwd: str) -> NewSessionResponse:
            return NewSessionResponse(session_id=f"permission:{cwd}")

        async def prompt(self, session_id: str, _parts: list[object]) -> PromptResponse:
            response = await callback_slot[0].request_permission(
                session_id,
                ToolCallUpdate(tool_call_id="tool-1", kind="read", status="pending"),
                [PermissionOption(
                    option_id="reject-once", name="Reject", kind="reject_once"
                )],
            )
            assert type(response) is RequestPermissionResponse
            return PromptResponse(stop_reason="end_turn")

        def cancel(self, _session_id: str):
            if cancel_mode == "sync_failure":
                raise RuntimeError("secret-cancel-failure")

            async def result() -> None:
                if cancel_mode == "async_failure":
                    raise RuntimeError("secret-cancel-failure")

            return result()

    @asynccontextmanager
    async def factory(callback, *_args):
        callback_slot.append(callback)
        yield Connection()

    async with ACPStdioTransport(
        ("unused",), project_root=str(tmp_path), client_factory=factory
    ) as transport:
        await transport.initialize()
        session = await transport.new_session()
        prompt = asyncio.create_task(transport.prompt(
            session, (TransportPromptPart.text("permission"),)
        ))
        update = await anext(transport.stream_updates(session))
        assert update.permission is not None
        if cancel_mode != "success":
            with pytest.raises(TransportFailure, match="cancellation_failed") as caught:
                await transport.cancel(session)
            assert "secret-cancel-failure" not in _exception_text(caught.value)
        else:
            await transport.cancel(session)
        with pytest.raises(TransportFailure, match="permission_invalid"):
            await transport.respond_permission(
                session,
                TransportPermissionDecision(
                    request_id=update.permission.request_id,
                    allowed=False,
                    reason="cancelled",
                ),
            )
        with suppress(asyncio.CancelledError, TransportFailure):
            await asyncio.wait_for(prompt, timeout=0.1)


async def _rejected_coroutine_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ensure_future,
) -> tuple[TransportFailure, bool, str]:
    async def hostile_none_coroutine_qualname_marker() -> None:
        return None

    coroutine = hostile_none_coroutine_qualname_marker()
    stderr = io.StringIO()
    with warnings.catch_warnings(record=True) as caught_warnings, redirect_stderr(stderr):
        warnings.simplefilter("always")
        async with ACPStdioTransport(("unused",), project_root=str(tmp_path)) as transport:
            with monkeypatch.context() as patch:
                patch.setattr(asyncio, "ensure_future", ensure_future)
                with pytest.raises(TransportFailure, match="session_failed") as caught:
                    await transport._invoke(
                        coroutine, TransportFailureCode.SESSION_FAILED)
        closed = coroutine.cr_frame is None
        del coroutine
        gc.collect()
    rendered = stderr.getvalue() + "\n".join(
        str(item.message) for item in caught_warnings)
    return caught.value, closed, rendered


@_sync_test
async def test_ensure_future_cancelled_error_closes_coroutine_content_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "secret-ensure-future-cancelled"

    def cancelled(_awaitable):
        raise asyncio.CancelledError(marker)

    error, closed, rendered = await _rejected_coroutine_result(
        tmp_path, monkeypatch, cancelled)
    assert closed and marker not in _exception_text(error) + rendered
    assert "never awaited" not in rendered


@_sync_test
async def test_ensure_future_none_closes_hostile_named_coroutine_without_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    error, closed, rendered = await _rejected_coroutine_result(
        tmp_path, monkeypatch, lambda _awaitable: None)
    assert closed and "hostile_none_coroutine_qualname_marker" not in rendered
    assert "never awaited" not in rendered
    assert error.__cause__ is None and error.__context__ is None
