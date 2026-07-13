from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp import schema

from agentdeck.daemon.supervisor import SubmittedReceipt, TransportResult
from agentdeck.daemon.transports import (
    AcpWorkerTransport,
    TmuxWorkerTransport,
    WorkerTransportError,
    build_worker_prompt,
)
from agentdeck.models import AgentSpec, RuntimeConfig


DISPATCH_KEY = "dsp_" + "1" * 32


def _attempt(transport: str) -> dict[str, object]:
    return {
        "attempt_id": "mat_0123456789ab",
        "mission_id": "mis_0123456789ab",
        "step_id": "step_1",
        "agent_id": "worker",
        "configured_transport": transport,
        "dispatch_key": DISPATCH_KEY,
    }


def _agent(transport: str, command: tuple[str, ...] = ()) -> AgentSpec:
    return AgentSpec(
        agent_id="worker",
        role="implementation",
        provider="fake",
        command="fake-worker",
        role_prompt="Implement only the assigned task.",
        transport=transport,
        transport_command=command,
    )


def _reply(token: str = DISPATCH_KEY) -> str:
    return "\n".join(
        (
            f"handoff_token: {token}",
            "status: completed",
            "summary: implementation finished",
            "verification: focused tests passed",
            "risks: none",
            "next_steps: review",
        )
    )


class RecordingTmuxBackend:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.sent: list[tuple[RuntimeConfig, str, str]] = []
        self.captures: list[tuple[RuntimeConfig, str, int]] = []

    def send_input(self, config: RuntimeConfig, pane_id: str, text: str) -> None:
        self.sent.append((config, pane_id, text))

    def capture_output(
        self, config: RuntimeConfig, pane_id: str, lines: int = 200
    ) -> str:
        self.captures.append((config, pane_id, lines))
        return self.outputs.pop(0)


def test_build_worker_prompt_binds_exact_attempt_agent_and_handoff() -> None:
    prompt = build_worker_prompt(
        _attempt("tmux"),
        _agent("tmux"),
        task="Implement the daemon adapter",
        previous_handoff={"summary": "design approved"},
    )

    assert "Role: implementation" in prompt
    assert "Implement only the assigned task." in prompt
    assert "Task: Implement the daemon adapter" in prompt
    assert '"summary": "design approved"' in prompt
    assert f"Use this handoff token exactly: {DISPATCH_KEY}" in prompt


def test_tmux_transport_admits_once_and_completes_from_bounded_correlated_poll() -> None:
    backend = RecordingTmuxBackend(["working", _reply()])
    config = RuntimeConfig(session_name="demo", socket_name="demo-socket")
    transport = TmuxWorkerTransport(
        config=config,
        pane_id="%7",
        prompt="bounded worker prompt",
        backend=backend,
        max_polls=2,
        poll_interval_seconds=0,
        capture_lines=80,
    )

    async def run() -> tuple[SubmittedReceipt, TransportResult]:
        receipt = await transport.admit(_attempt("tmux"))
        result = await transport.complete(_attempt("tmux"), receipt)
        return receipt, result

    receipt, result = asyncio.run(run())

    assert receipt.dispatch_key == DISPATCH_KEY
    assert backend.sent == [(config, "%7", "bounded worker prompt")]
    assert backend.captures == [(config, "%7", 80), (config, "%7", 80)]
    assert result.stop_reason == "structured_reply"
    assert result.validated is True
    assert result.reply["handoff_token"] == DISPATCH_KEY
    assert result.artifacts == ()
    assert result.trace_ids == ()


def test_tmux_transport_times_out_without_fallback_or_extra_poll() -> None:
    backend = RecordingTmuxBackend(["still working", "still working"])
    transport = TmuxWorkerTransport(
        config=RuntimeConfig(),
        pane_id="%2",
        prompt="prompt",
        backend=backend,
        max_polls=2,
        poll_interval_seconds=0,
    )

    async def run() -> None:
        receipt = await transport.admit(_attempt("tmux"))
        with pytest.raises(WorkerTransportError, match="bounded poll exhausted"):
            await transport.complete(_attempt("tmux"), receipt)

    asyncio.run(run())
    assert len(backend.sent) == 1
    assert len(backend.captures) == 2


class FakeAcpTransport:
    def __init__(
        self,
        argv: tuple[str, ...],
        workspace: Path,
        client: object,
        *,
        request_timeout: float,
        permission: bool = False,
        expected_permission_outcome: str = "cancelled",
    ) -> None:
        self.argv = argv
        self.workspace = Path(workspace)
        self.client = client
        self.request_timeout = request_timeout
        self.permission = permission
        self.expected_permission_outcome = expected_permission_outcome
        self.calls: list[object] = []

    async def initialize(self) -> object:
        self.calls.append("initialize")
        return object()

    async def new_session(self) -> object:
        self.calls.append("new_session")
        return SimpleNamespace(native_session_id="native-worker-1")

    async def prompt(self, session_id: str, text: str) -> object:
        self.calls.append(("prompt", session_id, text))
        if self.permission:
            response = await self.client.request_permission(
                session_id,
                schema.ToolCallUpdate(toolCallId="call-1", title="Edit", kind="edit"),
                [
                    schema.PermissionOption(
                        optionId="allow", name="Allow once", kind="allow_once"
                    )
                ],
            )
            assert response.outcome.outcome == self.expected_permission_outcome
        await self.client._sink.append_update(  # type: ignore[attr-defined]
            session_id,
            "text",
            {"role": "agent", "content": {"text": _reply()}},
        )
        return SimpleNamespace(stop_reason="end_turn", outcome="completed")

    async def close(self) -> None:
        self.calls.append("close")


def test_acp_transport_uses_initialize_session_and_prompt_without_state_sink(
    tmp_path: Path,
) -> None:
    created: list[FakeAcpTransport] = []

    def factory(*args: Any, **kwargs: Any) -> FakeAcpTransport:
        transport = FakeAcpTransport(*args, **kwargs)
        created.append(transport)
        return transport

    transport = AcpWorkerTransport(
        argv=("fake-agent-acp",),
        workspace=tmp_path,
        prompt="ACP worker prompt",
        transport_factory=factory,
        request_timeout=4,
    )

    async def run() -> tuple[SubmittedReceipt, TransportResult]:
        receipt = await transport.admit(_attempt("acp"))
        result = await transport.complete(_attempt("acp"), receipt)
        return receipt, result

    receipt, result = asyncio.run(run())

    assert receipt.dispatch_key == DISPATCH_KEY
    assert created[0].argv == ("fake-agent-acp",)
    assert created[0].workspace == tmp_path
    assert created[0].request_timeout == 4
    assert created[0].calls == [
        "initialize",
        "new_session",
        ("prompt", "native-worker-1", "ACP worker prompt"),
        "close",
    ]
    assert result == TransportResult(
        stop_reason="end_turn",
        validated=True,
        reply={
            "handoff_token": DISPATCH_KEY,
            "status": "completed",
            "summary": "implementation finished",
            "verification": "focused tests passed",
            "risks": "none",
            "next_steps": "review",
        },
    )


def test_acp_permission_is_denied_and_completion_fails_closed(tmp_path: Path) -> None:
    created: list[FakeAcpTransport] = []

    def factory(*args: Any, **kwargs: Any) -> FakeAcpTransport:
        transport = FakeAcpTransport(*args, **kwargs, permission=True)
        created.append(transport)
        return transport

    transport = AcpWorkerTransport(
        argv=("fake-agent-acp",),
        workspace=tmp_path,
        prompt="prompt",
        transport_factory=factory,
    )

    async def run() -> None:
        receipt = await transport.admit(_attempt("acp"))
        with pytest.raises(WorkerTransportError, match="forbidden permission"):
            await transport.complete(_attempt("acp"), receipt)

    asyncio.run(run())
    assert created[0].calls[-1] == "close"


def test_acp_transport_can_delegate_permission_to_daemon_ledger(tmp_path: Path) -> None:
    created: list[FakeAcpTransport] = []

    class Ledger:
        fail_on_permission = False

        def __init__(self) -> None:
            self.fragments: list[str] = []
            self.permission_seen = False
            self.pending: list[dict[str, Any]] = []
            self.decisions: list[object] = []
            self.disconnects: list[str] = []

        async def activate(self, native_session_id: str, _initialized: object) -> None:
            assert native_session_id == "native-worker-1"

        async def append_update(
            self, _session_id: str, _kind: str, payload: dict[str, Any]
        ) -> object:
            self.fragments.append(payload["content"]["text"])
            return {}

        async def append_permission(
            self,
            _session_id: str,
            summary: dict[str, Any],
            _options: list[schema.PermissionOption],
        ) -> object:
            self.permission_seen = True
            pending = {**summary, "permission_id": "prm_000000000001"}
            self.pending.append(pending)
            return pending

        async def append_permission_decision(
            self,
            _session_id: str,
            _tool_call_id: str,
            decision: object,
        ) -> None:
            self.decisions.append(decision)

        async def disconnect(self, reason: str) -> None:
            self.disconnects.append(reason)

    ledger = Ledger()

    def factory(*args: Any, **kwargs: Any) -> FakeAcpTransport:
        transport = FakeAcpTransport(
            *args,
            **kwargs,
            permission=True,
            expected_permission_outcome="selected",
        )
        created.append(transport)
        return transport

    async def decide(
        pending: dict[str, Any], options: list[schema.PermissionOption]
    ) -> object:
        assert pending["permission_id"] == "prm_000000000001"
        return __import__(
            "agentdeck.runtime.acp_client", fromlist=["PermissionDecision"]
        ).PermissionDecision.select(options[0].option_id)

    transport = AcpWorkerTransport(
        argv=("fake-agent-acp",),
        workspace=tmp_path,
        prompt="prompt",
        transport_factory=factory,
        sink=ledger,
        decide=decide,
    )

    async def run() -> TransportResult:
        receipt = await transport.admit(_attempt("acp"))
        return await transport.complete(_attempt("acp"), receipt)

    result = asyncio.run(run())
    assert result.validated is True
    assert len(ledger.pending) == 1
    assert len(ledger.decisions) == 1
    assert ledger.disconnects == ["transport_closed"]
    assert created[0].calls[-1] == "close"


@pytest.mark.parametrize("cancel_stage", ["initialize", "new_session", "activate"])
def test_acp_admit_cancellation_closes_and_disconnects_existing_session(
    tmp_path: Path, cancel_stage: str,
) -> None:
    reached = asyncio.Event()
    transport_calls: list[str] = []
    disconnects: list[str] = []

    class BlockingTransport:
        async def initialize(self):
            transport_calls.append("initialize")
            if cancel_stage == "initialize":
                reached.set()
                await asyncio.Event().wait()
            return object()

        async def new_session(self):
            transport_calls.append("new_session")
            if cancel_stage == "new_session":
                reached.set()
                await asyncio.Event().wait()
            return SimpleNamespace(native_session_id="native-cancelled")

        async def prompt(self, *_args):
            raise AssertionError("prompt must not run")

        async def close(self):
            transport_calls.append("close")

    class Sink:
        fragments: list[str] = []

        async def append_update(self, *_args):
            return None

        async def append_permission(self, *_args):
            return None

        async def append_permission_decision(self, *_args):
            return None

        async def activate(self, *_args):
            if cancel_stage == "activate":
                reached.set()
                await asyncio.Event().wait()

        async def disconnect(self, reason: str):
            disconnects.append(reason)

    worker = AcpWorkerTransport(
        argv=("fake-agent-acp",), workspace=tmp_path, prompt="prompt",
        transport_factory=lambda *_args, **_kwargs: BlockingTransport(),
        sink=Sink(),
    )

    async def case() -> None:
        task = asyncio.create_task(worker.admit(_attempt("acp")))
        await reached.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(case())
    assert transport_calls[-1] == "close"
    assert disconnects == (
        ["admission_cancelled"] if cancel_stage == "activate" else []
    )
