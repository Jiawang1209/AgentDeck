from __future__ import annotations

import asyncio
from dataclasses import replace
from functools import wraps
import json
from pathlib import Path
import threading
import traceback

import pytest
from acp import PROTOCOL_VERSION
from acp.schema import (
    AgentCapabilities, InitializeResponse, LoadSessionResponse,
    NewSessionResponse, PromptCapabilities, PromptResponse,
    ResumeSessionResponse, SessionCapabilities, SessionResumeCapabilities,
)

from agentdeck.adapters.acp_leader import ACPLeader
from agentdeck.adapters.acp_transport import ACPStdioTransport
from agentdeck.ports.leader import (
    LeaderFailure,
    LeaderFailureCode,
    ProjectContext,
    ResolvedLeaderModel,
)
from agentdeck.ports.transport import (
    TransportFailure, TransportFailureCode, TransportPromptPart, TransportSession,
)

from .fixtures.fake_acp_stdio_agent import (
    DECOY_MARKER,
    OVERSIZE_MARKER,
    fake_command,
)
from .test_leader_contract import request, valid_proposal


def _fixture_files(
    tmp_path: Path, mode: str = "success", backend_id: str = "codex-cli"
) -> tuple[tuple[str, ...], Path]:
    log = tmp_path / f"{mode}.jsonl"
    proposal = tmp_path / "proposal.json"
    payload = valid_proposal()
    payload["project_root"] = str(tmp_path)
    payload["leader_backend"] = backend_id
    proposal.write_text(json.dumps(payload), encoding="utf-8")
    return fake_command(log_path=log, proposal_path=proposal, mode=mode), log


def _request(tmp_path: Path, backend_id: str = "codex-cli"):
    return replace(
        request(),
        project_context=ProjectContext(
            project_root=str(tmp_path),
            summary="isolated ACP Leader contract project",
        ),
        resolved_model=ResolvedLeaderModel(
            backend_id=backend_id,
            adapter_id="acp",
            model_id="native-default",
            version="1.2.3",
        ),
    )


def _calls(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _chain(error: BaseException) -> str:
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
        import asyncio
        return asyncio.run(function(*args, **kwargs))
    return run


def _leader(command: tuple[str, ...], **overrides: object) -> ACPLeader:
    options: dict[str, object] = {
        "backend_id": "codex-cli",
        "model": "native-default",
        "version": "1.2.3",
    }
    options.update(overrides)
    return ACPLeader(command, **options)


@pytest.mark.parametrize("backend_id", ["codex-cli", "claude-cli"])
def test_acp_leader_initializes_session_and_uses_only_structured_artifact(
    tmp_path: Path, backend_id: str,
) -> None:
    command, log = _fixture_files(tmp_path, backend_id=backend_id)
    result = _leader(command, backend_id=backend_id).propose_mission(
        _request(tmp_path, backend_id)
    )
    assert result.objective == "Build an accessible page"
    calls = _calls(log)
    assert [item["call"] for item in calls] == [
        "initialize", "session/new", "session/prompt",
    ]
    prompt = calls[-1]
    assert prompt["prompt_types"] == ["text", "resource"]
    assert DECOY_MARKER not in result.mission.preview(1).canonical_content


def test_each_proposal_owns_a_new_process_and_session(tmp_path: Path) -> None:
    command, log = _fixture_files(tmp_path)
    leader = _leader(command)
    leader.propose_mission(_request(tmp_path))
    leader.propose_mission(_request(tmp_path))
    created = [item for item in _calls(log) if item["call"] == "session/new"]
    assert len({item["pid"] for item in created}) == 2
    assert len({item["session_id"] for item in created}) == 2


@_sync_test
async def test_sync_leader_owns_async_sdk_even_inside_running_loop(tmp_path: Path) -> None:
    command, _log = _fixture_files(tmp_path)
    result = _leader(command).propose_mission(_request(tmp_path))
    assert result.objective == "Build an accessible page"


def test_capability_absence_fails_closed_before_session(tmp_path: Path) -> None:
    command, log = _fixture_files(tmp_path, "capability_missing")
    with pytest.raises(TransportFailure, match="capability_missing") as caught:
        _leader(command).propose_mission(_request(tmp_path))
    assert [item["call"] for item in _calls(log)] == ["initialize"]
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_leader_denies_permission_and_never_treats_tools_as_planning(
    tmp_path: Path,
) -> None:
    command, log = _fixture_files(tmp_path, "permission")
    with pytest.raises(TransportFailure, match="unexpected_side_effect") as caught:
        _leader(command).propose_mission(_request(tmp_path))
    outcome = next(
        item["outcome"] for item in _calls(log) if item["call"] == "permission/result"
    )
    assert outcome == {"outcome": "selected", "optionId": "reject-once"}
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_plain_message_is_never_scraped_as_a_proposal(tmp_path: Path) -> None:
    command, _log = _fixture_files(tmp_path, "text_only")
    with pytest.raises(LeaderFailure) as caught:
        _leader(command).propose_mission(_request(tmp_path))
    assert caught.value.code is LeaderFailureCode.SCHEMA
    assert DECOY_MARKER not in _chain(caught.value)


def test_invalid_structured_artifact_is_content_free_schema_failure(
    tmp_path: Path,
) -> None:
    command, _log = _fixture_files(tmp_path, "invalid_resource")
    with pytest.raises(LeaderFailure) as caught:
        _leader(command).propose_mission(_request(tmp_path))
    assert caught.value.code is LeaderFailureCode.SCHEMA
    assert "not-json" not in _chain(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_acp_leader_rejects_unbounded_output_without_content(
    tmp_path: Path,
) -> None:
    command, _log = _fixture_files(tmp_path, "oversize")
    with pytest.raises(TransportFailure, match="response_oversize") as caught:
        _leader(command, max_bytes=4096).propose_mission(_request(tmp_path))
    assert caught.value.code is LeaderFailureCode.OVERSIZE
    assert OVERSIZE_MARKER not in _chain(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize(
    "changes",
    [
        {"backend_id": "claude-cli"},
        {"model": "other-model"},
        {"version": "9.9.9"},
    ],
)
def test_frozen_leader_identity_drift_fails_before_process(
    tmp_path: Path, changes: dict[str, str]
) -> None:
    command, log = _fixture_files(tmp_path)
    with pytest.raises(ValueError, match="resolved Leader identity"):
        _leader(command, **changes).propose_mission(_request(tmp_path))
    assert _calls(log) == []


def test_constructor_rejects_fallback_or_unbounded_configuration(tmp_path: Path) -> None:
    command, _log = _fixture_files(tmp_path)
    with pytest.raises(ValueError, match="model"):
        ACPLeader(command, backend_id="codex-cli", model="", version="1.2.3")
    with pytest.raises(ValueError, match="backend"):
        ACPLeader(command, backend_id="", model="native-default", version="1.2.3")
    with pytest.raises(ValueError, match="response bound"):
        _leader(command, max_bytes=0)


def test_synchronous_transport_factory_failure_is_typed_and_content_free(
    tmp_path: Path,
) -> None:
    command, _log = _fixture_files(tmp_path)
    marker = "secret-transport-factory-failure"

    def broken_factory(*_args, **_kwargs):
        raise RuntimeError(marker)

    with pytest.raises(TransportFailure, match="initialization_failed") as caught:
        _leader(command, transport_factory=broken_factory).propose_mission(
            _request(tmp_path)
        )
    assert marker not in _chain(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@_sync_test
async def test_sync_bridge_join_is_bounded_inside_running_loop(tmp_path: Path) -> None:
    command, _log = _fixture_files(tmp_path)

    class SlowTransport:
        async def __aenter__(self):
            await asyncio.sleep(0.3)
            return self

        async def __aexit__(self, *_args):
            return None

    def factory(*_args, **_kwargs):
        return SlowTransport()

    with pytest.raises(TransportFailure, match="timeout") as caught:
        _leader(
            command, timeout_seconds=0.03, transport_factory=factory
        ).propose_mission(_request(tmp_path))
    assert caught.value.transport_code is TransportFailureCode.TIMEOUT
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("mode", ["permission_hang", "tool_hang"])
def test_planning_side_effect_is_cancelled_before_waiting_for_prompt(
    tmp_path: Path, mode: str,
) -> None:
    command, log = _fixture_files(tmp_path, mode)
    with pytest.raises(TransportFailure, match="unexpected_side_effect") as caught:
        _leader(command, timeout_seconds=0.5).propose_mission(_request(tmp_path))
    names = [item["call"] for item in _calls(log)]
    assert "session/cancel" in names
    assert names.index("session/cancel") > names.index("session/prompt")
    if mode == "permission_hang":
        assert names.index("session/cancel") > names.index("permission/result")
    assert caught.value.__cause__ is None and caught.value.__context__ is None


class _CancellationSwallowingTransport:
    async def __aenter__(self):
        while True:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                continue

    async def __aexit__(self, *_args):
        return None


def _swallowing_leader(command: tuple[str, ...]) -> ACPLeader:
    return _leader(
        command, timeout_seconds=0.03,
        transport_factory=lambda *_args, **_kwargs: _CancellationSwallowingTransport(),
    )


def test_sync_bridge_without_running_loop_does_not_wait_for_cancel_swallowing_task(
    tmp_path: Path,
) -> None:
    command, _log = _fixture_files(tmp_path)
    failures: list[BaseException] = []

    def invoke() -> None:
        try:
            _swallowing_leader(command).propose_mission(_request(tmp_path))
        except BaseException as error:
            failures.append(error)

    probe = threading.Thread(target=invoke, daemon=True)
    probe.start()
    probe.join(0.2)
    assert not probe.is_alive()
    assert len(failures) == 1 and type(failures[0]) is TransportFailure
    assert failures[0].transport_code is TransportFailureCode.TIMEOUT
    assert failures[0].__cause__ is None and failures[0].__context__ is None


@_sync_test
async def test_sync_bridge_inside_running_loop_leaves_no_worker_thread(
    tmp_path: Path,
) -> None:
    command, _log = _fixture_files(tmp_path)
    before = {thread.ident for thread in threading.enumerate()}
    with pytest.raises(TransportFailure, match="timeout"):
        _swallowing_leader(command).propose_mission(_request(tmp_path))
    await asyncio.sleep(0.02)
    leaked = [
        thread for thread in threading.enumerate()
        if thread.ident not in before and thread.is_alive()
    ]
    assert leaked == []


@pytest.mark.parametrize(("operation", "code"), [
    ("initialize", "initialization_failed"), ("nonawaitable", "initialization_failed"),
    ("new", "session_failed"), ("load", "session_failed"),
    ("resume", "session_failed"), ("prompt", "prompt_failed"),
    ("cancel", "cancellation_failed"), ("close", "disconnected"),
])
@_sync_test
async def test_sync_connection_call_failures_are_typed_and_content_free(
    tmp_path: Path, operation: str, code: str,
) -> None:
    marker = f"secret-sync-{operation}-failure"

    async def ready(value):
        return value

    class Connection:
        def initialize(self, _version: int):
            if operation == "initialize":
                raise RuntimeError(marker)
            if operation == "nonawaitable":
                return object()
            return ready(InitializeResponse(
                protocol_version=PROTOCOL_VERSION,
                agent_capabilities=AgentCapabilities(
                    load_session=operation != "resume",
                    prompt_capabilities=PromptCapabilities(embedded_context=True),
                    session_capabilities=SessionCapabilities(
                        resume=SessionResumeCapabilities()),
                ),
            ))

        def new_session(self, **_kwargs):
            if operation == "new":
                raise RuntimeError(marker)
            return ready(NewSessionResponse(session_id="sync-session"))

        def load_session(self, **_kwargs):
            if operation == "load":
                raise RuntimeError(marker)
            return ready(LoadSessionResponse())

        def resume_session(self, *_args, **_kwargs):
            if operation == "resume":
                raise RuntimeError(marker)
            return ready(ResumeSessionResponse())

        def prompt(self, *_args):
            if operation == "prompt":
                raise RuntimeError(marker)
            return ready(PromptResponse(stop_reason="end_turn"))

        def cancel(self, *_args):
            if operation == "cancel":
                raise RuntimeError(marker)
            return ready(None)

    class Manager:
        def __aenter__(self):
            return ready(Connection())

        def __aexit__(self, *_args):
            if operation == "close":
                raise RuntimeError(marker)
            return ready(None)

    transport = ACPStdioTransport(
        ("unused",), project_root=str(tmp_path),
        client_factory=lambda *_args: Manager(),
    )
    with pytest.raises(TransportFailure, match=code) as caught:
        async with transport:
            await transport.initialize()
            if operation == "new":
                await transport.new_session()
            elif operation in {"load", "resume"}:
                await transport.resume_session(TransportSession("persisted"))
            elif operation in {"prompt", "cancel"}:
                session = await transport.new_session()
                if operation == "prompt":
                    await transport.prompt(
                        session, (TransportPromptPart.text("sync prompt"),))
                else:
                    await transport.cancel(session)
    assert marker not in _chain(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
