from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
from acp import schema

from agentdeck.daemon.supervisor import SubmittedReceipt, TransportResult
from agentdeck.daemon.transports import (
    AcpWorkerTransport,
    TmuxWorkerTransport,
    WorkerTransportError,
    _AcpUpdateError,
    _MemoryAcpSink,
    build_worker_prompt,
)
from agentdeck.runtime.acp_mapping import MAX_ACP_UPDATES_PER_TURN
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


def test_tmux_subprocess_timeout_maps_to_worker_transport_error() -> None:
    timeout = subprocess.TimeoutExpired(["tmux", "send-keys"], 5.0)

    class TimeoutBackend(RecordingTmuxBackend):
        def send_input(self, *_args) -> None:
            raise timeout

    transport = TmuxWorkerTransport(
        config=RuntimeConfig(), pane_id="%2", prompt="prompt",
        backend=TimeoutBackend([]),
    )

    async def run() -> None:
        with pytest.raises(
            WorkerTransportError, match="tmux Worker admission failed"
        ) as raised:
            await transport.admit(_attempt("tmux"))
        assert raised.value.__cause__ is timeout

    asyncio.run(run())


def test_tmux_capture_timeout_maps_to_worker_transport_error() -> None:
    timeout = subprocess.TimeoutExpired(["tmux", "capture-pane"], 5.0)

    class TimeoutBackend(RecordingTmuxBackend):
        def capture_output(self, *_args) -> str:
            raise timeout

    transport = TmuxWorkerTransport(
        config=RuntimeConfig(), pane_id="%2", prompt="prompt",
        backend=TimeoutBackend([]), max_polls=1,
    )

    async def run() -> None:
        receipt = await transport.admit(_attempt("tmux"))
        with pytest.raises(
            WorkerTransportError, match="tmux Worker capture failed"
        ) as raised:
            await transport.complete(_attempt("tmux"), receipt)
        assert raised.value.__cause__ is timeout

    asyncio.run(run())


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
            {"role": "agent", "content": {"type": "text", "text": _reply()}},
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


def test_default_acp_sink_accepts_structured_updates_and_collects_only_agent_text(
    tmp_path: Path,
) -> None:
    class StructuredUpdateTransport(FakeAcpTransport):
        async def prompt(self, session_id: str, text: str) -> object:
            self.calls.append(("prompt", session_id, text))
            sink = self.client._sink  # type: ignore[attr-defined]
            await sink.append_update(
                session_id,
                "progress",
                {"session_update": "plan", "entries": []},
            )
            await sink.append_update(
                session_id,
                "tool_call",
                {"tool_call_id": "call-1", "kind": "read"},
            )
            await sink.append_update(
                session_id,
                "artifact",
                {
                    "tool_call_id": "call-1",
                    "artifacts": [{"type": "resource", "uri": "file:///tmp/result"}],
                },
            )
            await sink.append_update(
                session_id,
                "tool_result",
                {"tool_call_id": "call-1", "status": "completed"},
            )
            await sink.append_update(
                session_id,
                "text",
                {"role": "thought", "content": {"type": "text", "text": "private"}},
            )
            await sink.append_update(
                session_id,
                "text",
                {"role": "agent", "content": {"type": "text", "text": _reply()}},
            )
            return SimpleNamespace(stop_reason="end_turn", outcome="completed")

    transport = AcpWorkerTransport(
        argv=("fake-agent-acp",),
        workspace=tmp_path,
        prompt="ACP worker prompt",
        transport_factory=lambda *args, **kwargs: StructuredUpdateTransport(
            *args, **kwargs
        ),
    )

    async def run() -> TransportResult:
        receipt = await transport.admit(_attempt("acp"))
        return await transport.complete(_attempt("acp"), receipt)

    result = asyncio.run(run())

    assert result.validated is True
    assert result.reply["summary"] == "implementation finished"


def test_default_acp_sink_counts_non_text_updates_and_rejects_malformed_input() -> None:
    async def run() -> None:
        sink = _MemoryAcpSink()
        for index in range(MAX_ACP_UPDATES_PER_TURN):
            await sink.append_update(
                "native-worker-1",
                "progress",
                {"session_update": "usage_update", "used": index, "size": 256},
            )
        with pytest.raises(ValueError, match="turn updates exceed"):
            await sink.append_update(
                "native-worker-1",
                "tool_result",
                {"tool_call_id": "call-1", "status": "completed"},
            )
        with pytest.raises(WorkerTransportError, match="unsupported update"):
            await _MemoryAcpSink().append_update(
                "native-worker-1", "unknown", {"value": "ignored"}
            )
        with pytest.raises(WorkerTransportError, match="unsupported update"):
            await _MemoryAcpSink().append_update(
                "native-worker-1",
                "text",
                {"role": "agent", "content": {"text": "missing discriminator"}},
            )

    asyncio.run(run())


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
        with pytest.raises(WorkerTransportError, match="update failed") as raised:
            await transport.complete(_attempt("acp"), receipt)
        assert getattr(raised.value, "completion_stage", None) == "update"

    asyncio.run(run())
    assert created[0].calls[-1] == "close"


@pytest.mark.parametrize(
    "completion_stage", ["prompt", "update", "parse", "finish", "cleanup"]
)
def test_acp_completion_errors_expose_only_a_sanitized_stage(
    tmp_path: Path, completion_stage: str
) -> None:
    class Sink:
        fragments = [] if completion_stage == "parse" else [_reply()]
        permission_seen = False

        async def append_update(self, *_args):
            raise _AcpUpdateError("private update payload")

        async def append_permission(self, *_args): return None
        async def append_permission_decision(self, *_args): return None
        async def activate(self, *_args): return None

        async def finish(self, *_args):
            if completion_stage == "finish":
                raise OSError("/private/project/secret")

        async def disconnect(self, *_args): return None

    sink = Sink()

    class Transport:
        async def initialize(self): return object()

        async def new_session(self):
            return SimpleNamespace(native_session_id="native-staged-failure")

        async def prompt(self, session_id: str, _text: str):
            if completion_stage == "prompt":
                raise WorkerTransportError("command --token private")
            if completion_stage == "update":
                await sink.append_update(session_id, "progress", {})
            return SimpleNamespace(stop_reason="end_turn")

        async def close(self):
            if completion_stage == "cleanup":
                raise OSError("/private/adapter/path")

    worker = AcpWorkerTransport(
        argv=("fake-agent-acp",),
        workspace=tmp_path,
        prompt="prompt",
        transport_factory=lambda *_args, **_kwargs: Transport(),
        sink=sink,
    )

    async def run() -> None:
        receipt = await worker.admit(_attempt("acp"))
        with pytest.raises(WorkerTransportError) as raised:
            await worker.complete(_attempt("acp"), receipt)
        assert getattr(raised.value, "completion_stage", None) == completion_stage
        rendered = str(raised.value)
        assert "private" not in rendered
        assert "token" not in rendered
        assert "/" not in rendered

    asyncio.run(run())


def test_acp_completion_error_chain_cycle_is_bounded_and_classified_as_prompt(
    tmp_path: Path,
) -> None:
    error = WorkerTransportError("private prompt failure")
    error.__cause__ = error

    class Sink:
        fragments: list[str] = []
        permission_seen = False

        async def append_update(self, *_args): return None
        async def append_permission(self, *_args): return None
        async def append_permission_decision(self, *_args): return None
        async def activate(self, *_args): return None
        async def finish(self, *_args): return None
        async def disconnect(self, *_args): return None

    class Transport:
        async def initialize(self): return object()
        async def new_session(self):
            return SimpleNamespace(native_session_id="native-cycle")
        async def prompt(self, *_args): raise error
        async def close(self): return None

    worker = AcpWorkerTransport(
        argv=("fake-agent-acp",), workspace=tmp_path, prompt="prompt",
        transport_factory=lambda *_args, **_kwargs: Transport(), sink=Sink(),
    )

    async def run() -> None:
        receipt = await worker.admit(_attempt("acp"))
        with pytest.raises(WorkerTransportError) as raised:
            await asyncio.wait_for(
                worker.complete(_attempt("acp"), receipt), timeout=0.2
            )
        assert getattr(raised.value, "completion_stage", None) == "prompt"

    asyncio.run(run())


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


def test_acp_admit_cancel_does_not_wait_for_close_that_swallows_cancellation(
    tmp_path: Path,
) -> None:
    activate_started: asyncio.Event
    release_close: asyncio.Event
    close_started: asyncio.Event
    disconnects: list[str] = []

    class Sink:
        fragments: list[str] = []

        async def append_update(self, *_args): return None
        async def append_permission(self, *_args): return None
        async def append_permission_decision(self, *_args): return None

        async def activate(self, *_args):
            activate_started.set()
            await asyncio.Event().wait()

        async def disconnect(self, reason: str):
            disconnects.append(reason)

    class UncooperativeClose:
        async def initialize(self): return object()
        async def new_session(self):
            return SimpleNamespace(native_session_id="native-uncooperative")
        async def prompt(self, *_args): raise AssertionError

        async def close(self):
            close_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release_close.wait()

    worker = AcpWorkerTransport(
        argv=("fake-agent-acp",), workspace=tmp_path, prompt="prompt",
        request_timeout=0.01,
        transport_factory=lambda *_args, **_kwargs: UncooperativeClose(),
        sink=Sink(),
    )

    async def case() -> None:
        nonlocal activate_started, release_close, close_started
        activate_started = asyncio.Event()
        release_close = asyncio.Event()
        close_started = asyncio.Event()
        task = asyncio.create_task(worker.admit(_attempt("acp")))
        await activate_started.wait()
        task.cancel()
        await close_started.wait()
        done, _pending = await asyncio.wait({task}, timeout=0.1)
        try:
            assert task in done
            with pytest.raises(asyncio.CancelledError):
                await task
            assert disconnects == ["admission_cancelled"]
        finally:
            release_close.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        assert worker._cleanup_tasks == set()

    asyncio.run(case())


def test_acp_admit_cancel_disconnects_when_close_raises_cancelled_error(
    tmp_path: Path,
) -> None:
    activate_started: asyncio.Event
    disconnects: list[str] = []

    class Sink:
        fragments: list[str] = []
        async def append_update(self, *_args): return None
        async def append_permission(self, *_args): return None
        async def append_permission_decision(self, *_args): return None
        async def activate(self, *_args):
            activate_started.set()
            await asyncio.Event().wait()
        async def disconnect(self, reason: str): disconnects.append(reason)

    class CancelledClose:
        async def initialize(self): return object()
        async def new_session(self):
            return SimpleNamespace(native_session_id="native-cancelled-close")
        async def prompt(self, *_args): raise AssertionError
        async def close(self): raise asyncio.CancelledError

    worker = AcpWorkerTransport(
        argv=("fake-agent-acp",), workspace=tmp_path, prompt="prompt",
        transport_factory=lambda *_args, **_kwargs: CancelledClose(), sink=Sink(),
    )

    async def case() -> None:
        nonlocal activate_started
        activate_started = asyncio.Event()
        task = asyncio.create_task(worker.admit(_attempt("acp")))
        await activate_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(case())
    assert disconnects == ["admission_cancelled"]


def test_bounded_cleanup_propagates_outer_cancellation(tmp_path: Path) -> None:
    worker = AcpWorkerTransport(
        argv=("fake-agent-acp",), workspace=tmp_path, prompt="prompt",
        transport_factory=lambda *_args, **_kwargs: object(),
        request_timeout=1,
    )

    async def case() -> None:
        operation_started = asyncio.Event()

        async def operation() -> None:
            operation_started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(worker._bounded_cleanup(operation()))
        await operation_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert worker._cleanup_tasks == set()

    asyncio.run(case())


def test_uncooperative_cleanup_blocks_new_admission_and_remains_bounded(
    tmp_path: Path,
) -> None:
    release_cleanup: asyncio.Event
    activation_started: asyncio.Event
    factory_calls = 0

    class Sink:
        fragments: list[str] = []
        async def append_update(self, *_args): return None
        async def append_permission(self, *_args): return None
        async def append_permission_decision(self, *_args): return None
        async def activate(self, *_args):
            activation_started.set()
            await asyncio.Event().wait()
        async def disconnect(self, *_args): return None

    class Transport:
        async def initialize(self): return object()
        async def new_session(self):
            return SimpleNamespace(native_session_id="native-stubborn-cleanup")
        async def prompt(self, *_args): raise AssertionError
        async def close(self):
            while not release_cleanup.is_set():
                try:
                    await release_cleanup.wait()
                except asyncio.CancelledError:
                    continue

    def factory(*_args, **_kwargs):
        nonlocal factory_calls
        factory_calls += 1
        return Transport()

    worker = AcpWorkerTransport(
        argv=("fake-agent-acp",), workspace=tmp_path, prompt="prompt",
        request_timeout=0.01, transport_factory=factory, sink=Sink(),
    )

    async def case() -> None:
        nonlocal release_cleanup, activation_started
        release_cleanup = asyncio.Event()
        activation_started = asyncio.Event()
        first = asyncio.create_task(worker.admit(_attempt("acp")))
        await activation_started.wait()
        first.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await first
            assert len(worker._cleanup_tasks) == 1
            with pytest.raises(WorkerTransportError, match="cleanup is pending"):
                await asyncio.wait_for(
                    worker.admit(_attempt("acp")), timeout=0.05
                )
            assert factory_calls == 1
            assert len(worker._cleanup_tasks) == 1
        finally:
            release_cleanup.set()
            for _ in range(20):
                await asyncio.sleep(0)
                if not worker._cleanup_tasks:
                    break
            assert worker._cleanup_tasks == set()

    asyncio.run(case())


def test_invalid_factory_does_not_poison_admission_reservation(
    tmp_path: Path,
) -> None:
    calls = 0

    class ValidTransport:
        async def initialize(self): return object()
        async def new_session(self):
            return SimpleNamespace(native_session_id="native-valid-retry")
        async def prompt(self, *_args): raise AssertionError
        async def close(self): return None

    def factory(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return object() if calls == 1 else ValidTransport()

    worker = AcpWorkerTransport(
        argv=("fake-agent-acp",), workspace=tmp_path, prompt="prompt",
        transport_factory=factory,
    )

    async def case() -> None:
        with pytest.raises(WorkerTransportError, match="factory is invalid"):
            await worker.admit(_attempt("acp"))
        receipt = await worker.admit(_attempt("acp"))
        assert receipt.summary == "ACP session admitted"

    asyncio.run(case())


def test_acp_prompt_cancel_uses_bounded_close_then_disconnect(
    tmp_path: Path,
) -> None:
    prompt_started: asyncio.Event
    calls: list[str] = []

    class Sink:
        fragments: list[str] = []
        async def append_update(self, *_args): return None
        async def append_permission(self, *_args): return None
        async def append_permission_decision(self, *_args): return None
        async def activate(self, *_args): calls.append("activate")
        async def disconnect(self, reason: str): calls.append(f"disconnect:{reason}")

    class PromptTransport:
        async def initialize(self): return object()
        async def new_session(self):
            return SimpleNamespace(native_session_id="native-prompt-cancel")
        async def prompt(self, *_args):
            calls.append("prompt")
            prompt_started.set()
            await asyncio.Event().wait()
        async def close(self):
            calls.append("close")
            raise asyncio.CancelledError

    worker = AcpWorkerTransport(
        argv=("fake-agent-acp",), workspace=tmp_path, prompt="prompt",
        transport_factory=lambda *_args, **_kwargs: PromptTransport(), sink=Sink(),
    )

    async def case() -> None:
        nonlocal prompt_started
        prompt_started = asyncio.Event()
        receipt = await worker.admit(_attempt("acp"))
        task = asyncio.create_task(worker.complete(_attempt("acp"), receipt))
        await prompt_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(WorkerTransportError, match="not admitted"):
            await worker.complete(_attempt("acp"), receipt)

    asyncio.run(case())
    assert calls == [
        "activate", "prompt", "close", "disconnect:transport_close_failed"
    ]


def test_acp_success_is_not_reported_when_disconnect_persistence_fails(
    tmp_path: Path,
) -> None:
    class Sink:
        fragments = [_reply()]
        permission_seen = False

        async def append_update(self, *_args): return None
        async def append_permission(self, *_args): return None
        async def append_permission_decision(self, *_args): return None
        async def activate(self, *_args): return None
        async def finish(self, *_args): return None
        async def disconnect(self, _reason: str):
            raise OSError("disconnect save failed")

    class Transport:
        async def initialize(self): return object()
        async def new_session(self):
            return SimpleNamespace(native_session_id="native-cleanup-failure")
        async def prompt(self, *_args):
            return SimpleNamespace(stop_reason="end_turn")
        async def close(self): return None

    worker = AcpWorkerTransport(
        argv=("fake-agent-acp",), workspace=tmp_path, prompt="prompt",
        transport_factory=lambda *_args, **_kwargs: Transport(), sink=Sink(),
    )

    async def case() -> None:
        receipt = await worker.admit(_attempt("acp"))
        with pytest.raises(WorkerTransportError, match="cleanup failed"):
            await worker.complete(_attempt("acp"), receipt)

    asyncio.run(case())
