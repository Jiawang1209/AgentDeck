from __future__ import annotations

import asyncio
import functools
import inspect
import os
from pathlib import Path
import sys
from typing import Any

import pytest
from acp import schema
from acp.exceptions import RequestError

import agentdeck.runtime.acp_client as acp_client_module
from agentdeck.runtime.acp_client import AgentDeckAcpClient, PermissionDecision
from agentdeck.runtime.acp_mapping import MAX_ACP_TURN_PAYLOAD_BYTES, MAX_ACP_UPDATES_PER_TURN


FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"


def async_test(function: Any) -> Any:
    @functools.wraps(function)
    def run(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(function(*args, **kwargs))
    return run


def _message(text: str) -> schema.AgentMessageChunk:
    return schema.AgentMessageChunk(
        sessionUpdate="agent_message_chunk",
        content=schema.TextContentBlock(type="text", text=text),
    )


def _tool() -> schema.ToolCallUpdate:
    return schema.ToolCallUpdate(toolCallId="call-1", title="Edit", kind="edit")


def _options() -> list[schema.PermissionOption]:
    return [
        schema.PermissionOption(optionId="allow", name="Allow once", kind="allow_once"),
        schema.PermissionOption(optionId="reject", name="Reject once", kind="reject_once"),
        schema.PermissionOption(optionId="always", name="Always", kind="allow_always"),
    ]


class FakeLedgerSink:
    def __init__(self) -> None:
        self.session_id = "native-1"
        self.completed = False
        self.updates: list[dict[str, Any]] = []
        self.permissions: list[dict[str, Any]] = []
        self.payload_bytes = 0

    async def append_update(self, session_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._active(session_id)
        encoded = len(str(payload).encode())
        if len(self.updates) >= MAX_ACP_UPDATES_PER_TURN:
            raise ValueError("ACP turn updates exceed bound")
        if self.payload_bytes + encoded > MAX_ACP_TURN_PAYLOAD_BYTES:
            raise ValueError("ACP turn payload exceeds bound")
        item = {"sequence": len(self.updates), "kind": kind, "payload": payload}
        self.updates.append(item)
        self.payload_bytes += encoded
        return item

    async def append_permission(
        self, session_id: str, summary: dict[str, Any], options: list[schema.PermissionOption]
    ) -> dict[str, Any]:
        self._active(session_id)
        item = {"permission_id": f"prm-{len(self.permissions)}", "summary": summary,
                "option_ids": [option.option_id for option in options], "decision": None}
        self.permissions.append(item)
        return item

    async def append_permission_decision(
        self, session_id: str, tool_call_id: str, decision: PermissionDecision
    ) -> None:
        pending = next(item for item in reversed(self.permissions)
                       if item["summary"]["tool_call_id"] == tool_call_id)
        if pending["decision"] is not None:
            raise ValueError("permission already settled")
        pending["decision"] = decision.ledger_status
        pending["reason"] = decision.reason

    def _active(self, session_id: str) -> None:
        if session_id != self.session_id:
            raise ValueError("ACP session correlation mismatch")
        if self.completed:
            raise ValueError("ACP turn is already complete")


@async_test
async def test_session_update_is_mapped_and_sequenced_exactly_once() -> None:
    sink = FakeLedgerSink()
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.cancelled("unused"))
    await client.session_update("native-1", _message("a"))
    await client.session_update("native-1", _message("b"))
    assert [item["sequence"] for item in sink.updates] == [0, 1]
    assert [item["payload"]["content"]["text"] for item in sink.updates] == ["a", "b"]


@async_test
async def test_session_update_propagates_wrong_session_completion_and_bounds() -> None:
    sink = FakeLedgerSink()
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.cancelled("unused"))
    with pytest.raises(ValueError, match="correlation"):
        await client.session_update("other", _message("x"))
    sink.completed = True
    with pytest.raises(ValueError, match="complete"):
        await client.session_update("native-1", _message("x"))
    sink.completed = False
    sink.updates = [{}] * MAX_ACP_UPDATES_PER_TURN
    with pytest.raises(ValueError, match="updates"):
        await client.session_update("native-1", _message("x"))
    sink.updates = []
    sink.payload_bytes = MAX_ACP_TURN_PAYLOAD_BYTES
    with pytest.raises(ValueError, match="payload"):
        await client.session_update("native-1", _message("x"))


@pytest.mark.parametrize(
    ("option_id", "ledger_status"), [("allow", "approved"), ("reject", "denied")]
)
@async_test
async def test_permission_records_pending_before_decision_and_returns_current_once_option(
    option_id: str, ledger_status: str
) -> None:
    sink = FakeLedgerSink()

    async def decide(pending: dict[str, Any], options: list[schema.PermissionOption]) -> PermissionDecision:
        assert pending["decision"] is None
        assert sink.permissions == [pending]
        return PermissionDecision.select(option_id)

    result = await AgentDeckAcpClient(sink=sink, decide=decide).request_permission(
        "native-1", _tool(), _options()
    )
    assert result.outcome.outcome == "selected"
    assert result.outcome.option_id == option_id
    assert sink.permissions[-1]["decision"] == ledger_status


@pytest.mark.parametrize("reason", ["non_tty", "ctrl_c", "timeout", "eof"])
@async_test
async def test_cancel_semantics_are_decider_results_and_never_auto_approve(reason: str) -> None:
    sink = FakeLedgerSink()

    async def decide(*_: Any) -> PermissionDecision:
        return PermissionDecision.cancelled(reason)

    result = await AgentDeckAcpClient(sink=sink, decide=decide).request_permission(
        "native-1", _tool(), _options()
    )
    assert result.outcome.outcome == "cancelled"
    assert sink.permissions[-1]["decision"] == "denied"
    assert sink.permissions[-1]["reason"] == reason


@pytest.mark.parametrize(
    ("error", "reason"), [(TimeoutError(), "timeout"), (EOFError(), "eof"),
                           (KeyboardInterrupt(), "ctrl_c")]
)
@async_test
async def test_interrupted_decider_is_settled_as_cancelled(error: BaseException, reason: str) -> None:
    sink = FakeLedgerSink()

    async def decide(*_: Any) -> PermissionDecision:
        raise error

    result = await AgentDeckAcpClient(sink=sink, decide=decide).request_permission(
        "native-1", _tool(), _options()
    )
    assert result.outcome.outcome == "cancelled"
    assert sink.permissions[-1]["decision"] == "denied"
    assert sink.permissions[-1]["reason"] == reason


@pytest.mark.parametrize("option_id", ["always", "missing"])
@async_test
async def test_disabled_always_and_unknown_options_fail_closed(option_id: str) -> None:
    sink = FakeLedgerSink()
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.select(option_id))
    result = await client.request_permission("native-1", _tool(), _options())
    assert result.outcome.outcome == "cancelled"
    assert sink.permissions[-1]["decision"] == "denied"
    assert sink.permissions[-1]["reason"] == "invalid_option"


@async_test
async def test_empty_options_wrong_session_and_completed_turn_reject_before_decider() -> None:
    sink = FakeLedgerSink()
    called = False

    async def decide(*_: Any) -> PermissionDecision:
        nonlocal called
        called = True
        return PermissionDecision.select("allow")

    client = AgentDeckAcpClient(sink=sink, decide=decide)
    with pytest.raises(ValueError, match="options"):
        await client.request_permission("native-1", _tool(), [])
    with pytest.raises(ValueError, match="correlation"):
        await client.request_permission("wrong", _tool(), _options())
    sink.completed = True
    with pytest.raises(ValueError, match="complete"):
        await client.request_permission("native-1", _tool(), _options())
    assert called is False


@async_test
async def test_concurrent_permission_request_fails_without_second_pending_record() -> None:
    sink = FakeLedgerSink()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def decide(*_: Any) -> PermissionDecision:
        entered.set()
        await release.wait()
        return PermissionDecision.cancelled("cancelled")

    client = AgentDeckAcpClient(sink=sink, decide=decide)
    first = asyncio.create_task(client.request_permission("native-1", _tool(), _options()))
    await entered.wait()
    with pytest.raises(RuntimeError, match="already pending"):
        await client.request_permission("native-1", _tool(), _options())
    assert len(sink.permissions) == 1
    release.set()
    await first


class CancellingLedgerSink(FakeLedgerSink):
    def __init__(self, block_at: str, *, fail_cancel_settlement: bool = False) -> None:
        super().__init__()
        self.block_at = block_at
        self.fail_cancel_settlement = fail_cancel_settlement
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self.final_decisions: list[PermissionDecision] = []

    async def append_permission(
        self, session_id: str, summary: dict[str, Any], options: list[schema.PermissionOption]
    ) -> dict[str, Any]:
        pending = await super().append_permission(session_id, summary, options)
        if self.block_at == "pending":
            self.blocked.set()
            await self.release.wait()
        return pending

    async def append_permission_decision(
        self, session_id: str, tool_call_id: str, decision: PermissionDecision
    ) -> None:
        pending = next(item for item in reversed(self.permissions)
                       if item["summary"]["tool_call_id"] == tool_call_id)
        if decision.reason == "cancelled" and self.fail_cancel_settlement:
            raise RuntimeError("cancel settlement failed")
        if self.block_at == "decision" and not self.blocked.is_set():
            pending["decision"] = decision.ledger_status
            pending["reason"] = decision.reason
            self.blocked.set()
            await self.release.wait()
        if decision.reason == "cancelled":
            pending["decision"] = "denied"
            pending["reason"] = "cancelled"
        elif pending["decision"] is None:
            await super().append_permission_decision(session_id, tool_call_id, decision)
        if not self.final_decisions or self.final_decisions[-1] != decision:
            self.final_decisions[:] = [decision]


@pytest.mark.parametrize("block_at", ["pending", "decision"])
@async_test
async def test_task_cancel_settles_possibly_written_permission_once_and_cleans_guard(
    block_at: str,
) -> None:
    sink = CancellingLedgerSink(block_at)
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.select("allow"))
    task = asyncio.create_task(client.request_permission("native-1", _tool(), _options()))
    await sink.blocked.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sink.permissions[0]["decision"] == "denied"
    assert sink.permissions[0]["reason"] == "cancelled"
    assert len(sink.final_decisions) == 1

    sink.block_at = "none"
    result = await client.request_permission("native-1", _tool(), _options())
    assert result.outcome.outcome == "selected"
    assert len(sink.permissions) == 2
    assert sink.permissions[0]["decision"] == "denied"
    assert sink.permissions[1]["decision"] == "approved"


@async_test
async def test_cancel_settlement_failure_raises_and_never_records_approval() -> None:
    sink = CancellingLedgerSink("pending", fail_cancel_settlement=True)
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.select("allow"))
    task = asyncio.create_task(client.request_permission("native-1", _tool(), _options()))
    await sink.blocked.wait()
    task.cancel()
    with pytest.raises(RuntimeError, match="cancel settlement failed"):
        await task
    assert sink.permissions[0]["decision"] is None
    assert all(decision.ledger_status != "approved" for decision in sink.final_decisions)
    sink.fail_cancel_settlement = False
    sink.block_at = "none"
    result = await client.request_permission("native-1", _tool(), _options())
    assert result.outcome.outcome == "selected"


class CancellationResistantSink(CancellingLedgerSink):
    def __init__(self) -> None:
        super().__init__("pending")
        self.settlement_started = asyncio.Event()
        self.settlement_cancelled = asyncio.Event()
        self.settlement_release = asyncio.Event()
        self.settlement_finished = asyncio.Event()

    async def append_permission_decision(
        self, session_id: str, tool_call_id: str, decision: PermissionDecision
    ) -> None:
        if decision.reason != "cancelled":
            await super().append_permission_decision(session_id, tool_call_id, decision)
            return
        self.settlement_started.set()
        while not self.settlement_release.is_set():
            try:
                await self.settlement_release.wait()
            except asyncio.CancelledError:
                self.settlement_cancelled.set()
        await super().append_permission_decision(session_id, tool_call_id, decision)
        self.settlement_finished.set()


@async_test
async def test_cancellation_resistant_settlement_has_a_hard_cleanup_bound(monkeypatch: Any) -> None:
    monkeypatch.setattr(acp_client_module, "_CANCEL_SETTLEMENT_TIMEOUT_SECONDS", 0.01)
    sink = CancellationResistantSink()
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.select("allow"))
    request = asyncio.create_task(client.request_permission("native-1", _tool(), _options()))
    await sink.blocked.wait()
    started = asyncio.get_running_loop().time()
    request.cancel()
    done, _ = await asyncio.wait({request}, timeout=0.25)
    bounded_elapsed = asyncio.get_running_loop().time() - started
    assert sink.settlement_started.is_set()
    assert sink.settlement_cancelled.is_set()

    sink.settlement_release.set()
    await asyncio.wait_for(sink.settlement_finished.wait(), timeout=0.25)
    result_or_error = await asyncio.wait_for(
        asyncio.gather(request, return_exceptions=True), timeout=0.25
    )
    assert request in done
    assert bounded_elapsed < 0.25
    assert isinstance(result_or_error[0], RuntimeError)
    assert sink.permissions[0]["decision"] == "denied"
    assert sink.permissions[0]["reason"] == "cancelled"
    assert all(decision.ledger_status != "approved" for decision in sink.final_decisions)

    sink.block_at = "none"
    result = await client.request_permission("native-1", _tool(), _options())
    assert result.outcome.outcome == "selected"


@async_test
async def test_all_unadvertised_callbacks_return_unsupported_without_side_effects(tmp_path: Path) -> None:
    client = AgentDeckAcpClient(
        sink=FakeLedgerSink(), decide=lambda *_: PermissionDecision.cancelled("unused")
    )
    target = tmp_path / "must-not-exist"
    calls = [
        client.read_text_file("native-1", str(target)),
        client.write_text_file("native-1", str(target), "secret"),
        client.create_terminal("native-1", "touch", [str(target)]),
        client.terminal_output("native-1", "term-1"),
        client.wait_for_terminal_exit("native-1", "term-1"),
        client.release_terminal("native-1", "term-1"),
        client.kill_terminal("native-1", "term-1"),
        client.create_elicitation("question", object()),
        client.complete_elicitation("eli-1"),
        client.ext_method("x/private", {"path": str(target)}),
        client.ext_notification("x/private", {"path": str(target)}),
    ]
    for call in calls:
        with pytest.raises(RequestError, match="not advertised|unsupported"):
            await call
    assert not target.exists()
    assert client.on_connect(object()) is None


def _transport_client(session_id: str = "fake-session-1") -> tuple[AgentDeckAcpClient, FakeLedgerSink]:
    sink = FakeLedgerSink()
    sink.session_id = session_id
    return AgentDeckAcpClient(
        sink=sink, decide=lambda *_: PermissionDecision.cancelled("non_tty")
    ), sink


@async_test
async def test_transport_initializes_streams_and_completes_prompt(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    client, sink = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "stream_end_turn"), tmp_path, client
    )
    initialized = await transport.initialize()
    session = await transport.new_session()
    result = await transport.prompt(session.native_session_id, "hello")
    await transport.close()

    assert initialized.protocol_version == 1
    assert initialized.client_capabilities == {"fs": None, "terminal": False}
    assert session.native_session_id == "fake-session-1"
    assert result.stop_reason == "end_turn"
    assert result.outcome == "completed"
    assert result.disconnect_reason == "clean_exit"
    assert [item["payload"]["content"]["text"] for item in sink.updates] == ["hello"]


@async_test
async def test_transport_preserves_exact_argv_and_canonical_cwd(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    marker = tmp_path / "argv.json"
    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "record_argv", str(marker), "two words", "$(false)"),
        tmp_path / ".",
        client,
    )
    await transport.initialize()
    await transport.close()
    import json
    recorded = json.loads(marker.read_text())
    assert recorded["argv"] == ["two words", "$(false)"]
    assert recorded["cwd"] == str(tmp_path.resolve())
    assert "create_subprocess_shell" not in inspect.getsource(AcpTransport)


@pytest.mark.parametrize("argv", [(), ("",), (sys.executable, 1)])
def test_transport_rejects_non_exact_nonempty_argv(argv: tuple[object, ...], tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    client, _ = _transport_client()
    with pytest.raises((TypeError, ValueError), match="argv"):
        AcpTransport(argv, tmp_path, client)  # type: ignore[arg-type]


@async_test
async def test_never_started_close_is_closed_and_idempotent(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    client, _ = _transport_client()
    transport = AcpTransport((sys.executable, "unused"), tmp_path, client)
    assert transport.lifecycle_state == "new"
    await asyncio.gather(transport.close(), transport.close())
    await transport.close()
    assert transport.lifecycle_state == "closed"
    with pytest.raises(RuntimeError, match="cannot start"):
        await transport.initialize()


@async_test
async def test_transport_blocks_exact_protocol_version_mismatch(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpProtocolVersionError, AcpTransport

    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "version_mismatch"), tmp_path, client,
        close_grace=0.05, terminate_grace=0.05, kill_grace=0.1,
    )
    with pytest.raises(AcpProtocolVersionError, match="protocol version"):
        await transport.initialize()
    assert transport._process.returncode is not None
    assert transport._stderr_task.done()
    assert transport.lifecycle_state == "closed"


@async_test
async def test_initialize_eof_race_is_ambiguous_and_self_cleans(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpAmbiguousOutcome, AcpTransport

    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "eof_initialize"), tmp_path, client,
        request_timeout=0.5, close_grace=0.05, terminate_grace=0.05, kill_grace=0.1,
    )
    with pytest.raises(AcpAmbiguousOutcome):
        await transport.initialize()
    assert transport._process.returncode is not None
    assert transport._stderr_task.done()
    assert transport.lifecycle_state == "closed"


@async_test
async def test_transport_bounds_timeout_and_eof_as_ambiguous(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpAmbiguousOutcome, AcpRequestTimeout, AcpTransport

    timeout_marker = tmp_path / "timeout-cancelled"
    for scenario, error, args in [
        ("timeout", AcpRequestTimeout, (str(timeout_marker),)),
        ("eof_before_response", AcpAmbiguousOutcome, ()),
    ]:
        client, _ = _transport_client()
        transport = AcpTransport(
            (sys.executable, str(FAKE_AGENT), scenario, *args), tmp_path, client,
            request_timeout=0.5,
        )
        await transport.initialize()
        session = await transport.new_session()
        with pytest.raises(error) as caught:
            await transport.prompt(session.native_session_id, "hello")
        if scenario == "timeout":
            await asyncio.sleep(0.05)
            assert timeout_marker.read_text() == "cancelled"
            assert caught.value.cancel_diagnostic.status == "sent"
            assert caught.value.cancel_diagnostic.session_id == "fake-session-1"
        await transport.close()


@pytest.mark.parametrize("cancel_behavior", ["hang", "error"])
@async_test
async def test_prompt_timeout_bounds_cancel_and_preserves_primary_error(
    tmp_path: Path, cancel_behavior: str
) -> None:
    from agentdeck.runtime.acp import AcpRequestTimeout, AcpTransport

    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "timeout"), tmp_path, client,
        request_timeout=0.5, cancel_timeout=0.03,
    )
    await transport.initialize()
    session = await transport.new_session()
    transport._request_timeout = 0.15

    async def bad_cancel(**_: Any) -> None:
        if cancel_behavior == "hang":
            await asyncio.sleep(60)
        raise RuntimeError("cancel failed with sensitive detail")

    transport._connection.cancel = bad_cancel
    started = asyncio.get_running_loop().time()
    with pytest.raises(AcpRequestTimeout) as caught:
        await transport.prompt(session.native_session_id, "hello")
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 0.4
    assert caught.value.cancel_diagnostic.status == (
        "timed_out" if cancel_behavior == "hang" else "failed"
    )
    assert "sensitive detail" not in repr(caught.value.cancel_diagnostic)
    await transport.close()


@async_test
async def test_prompt_cumulative_bound_failure_sends_bounded_cancel(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport, AcpTransportError

    marker = tmp_path / "bound-cancelled"
    client, sink = _transport_client()
    sink.payload_bytes = MAX_ACP_TURN_PAYLOAD_BYTES
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "stream_end_turn", str(marker)),
        tmp_path,
        client,
        request_timeout=0.5,
    )
    await transport.initialize()
    session = await transport.new_session()
    with pytest.raises(AcpTransportError) as caught:
        await transport.prompt(session.native_session_id, "hello")
    await asyncio.sleep(0.05)
    assert marker.read_text() == "cancelled"
    assert caught.value.cancel_diagnostic.status == "sent"
    await transport.close()


@async_test
async def test_transport_rejects_malformed_or_oversize_frames(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport, AcpTransportError

    for scenario in ("malformed_frame", "oversize_frame"):
        client, _ = _transport_client()
        transport = AcpTransport(
            (sys.executable, str(FAKE_AGENT), scenario), tmp_path, client,
            request_timeout=0.5, close_grace=0.05,
            terminate_grace=0.05, kill_grace=0.1,
        )
        with pytest.raises(AcpTransportError):
            await transport.initialize()
        assert transport._process.returncode is not None
        assert transport._stderr_task.done()


@async_test
async def test_transport_uses_reviewed_environment_plus_explicit_test_injection(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from agentdeck.runtime.acp import AcpTransport

    marker = tmp_path / "env.json"
    monkeypatch.setenv("ACP_UNREVIEWED_SECRET", "must-not-reach-child")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "reviewed-provider-value")
    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "record_env", str(marker)), tmp_path, client,
        test_env={"ACP_TEST_SENTINEL": "harmless-sentinel"},
    )
    await transport.initialize()
    await transport.close()
    import json
    recorded = json.loads(marker.read_text())
    assert recorded == {
        "sentinel": "harmless-sentinel", "unreviewed": None,
        "anthropic": "reviewed-provider-value",
    }
    assert "reviewed-provider-value" not in repr(transport)
    assert "harmless-sentinel" not in transport.stderr_diagnostic
    assert "[REDACTED]" in transport.stderr_diagnostic


@async_test
async def test_close_is_shielded_concurrent_retryable_and_bounds_stderr_descendant(
    tmp_path: Path
) -> None:
    from agentdeck.runtime.acp import AcpTransport

    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "descendant_stderr"), tmp_path, client,
        cancel_timeout=0.05, close_grace=0.05, terminate_grace=0.05, kill_grace=0.1,
    )
    await transport.initialize()
    child_pid = transport.child_pid

    original_close = transport._connection.close
    entered = asyncio.Event()
    release = asyncio.Event()
    close_calls = 0

    async def resistant_close() -> None:
        nonlocal close_calls
        close_calls += 1
        if close_calls > 1:
            await original_close()
            return
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        await original_close()

    transport._connection.close = resistant_close
    first = asyncio.create_task(transport.close())
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert transport.lifecycle_state == "cleaning"
    await asyncio.shield(transport._cleanup_task)
    assert transport.lifecycle_state == "closed"
    await asyncio.gather(transport.close(), transport.close())
    assert transport.lifecycle_state == "closed"
    assert transport._cleanup_task.done()
    assert transport._stderr_task.done()
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    release.set()
    await asyncio.sleep(0)


@async_test
async def test_close_caller_cancellation_during_process_wait_does_not_stop_cleanup(
    tmp_path: Path
) -> None:
    from agentdeck.runtime.acp import AcpTransport

    marker = tmp_path / "later-cancel"
    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "cancel_or_ignore_terminate", str(marker)),
        tmp_path, client, close_grace=0.05, terminate_grace=0.05, kill_grace=0.1,
    )
    await transport.initialize()
    child_pid = transport.child_pid
    entered_wait = asyncio.Event()
    original_wait = transport._process.wait

    async def observed_wait() -> int:
        entered_wait.set()
        return await original_wait()

    transport._process.wait = observed_wait
    closing = asyncio.create_task(transport.close())
    await entered_wait.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await transport.close()
    assert transport.lifecycle_state == "closed"
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@async_test
async def test_transport_bounds_and_redacts_stderr(tmp_path: Path, monkeypatch: Any) -> None:
    from agentdeck.runtime.acp import MAX_ACP_STDERR_BYTES, AcpTransport

    secret = "transport-secret-value"
    monkeypatch.setenv("ACP_TEST_SECRET", secret)
    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "stderr_noise"), tmp_path, client
    )
    await transport.initialize()
    await transport.close()
    diagnostic = transport.stderr_diagnostic
    assert len(diagnostic.encode()) <= MAX_ACP_STDERR_BYTES
    assert secret not in diagnostic
    assert "[REDACTED]" in diagnostic
    assert "ACP_TEST_SECRET" not in repr(transport)


@async_test
async def test_transport_cancellation_sends_cancel_and_kills_only_child(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    marker = tmp_path / "cancelled"
    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "cancel_or_ignore_terminate", str(marker)),
        tmp_path,
        client,
        request_timeout=5,
        terminate_grace=0.05,
        kill_grace=0.2,
    )
    await transport.initialize()
    session = await transport.new_session()
    prompt = asyncio.create_task(transport.prompt(session.native_session_id, "wait"))
    await asyncio.sleep(0.05)
    prompt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await prompt
    child_pid = transport.child_pid
    await transport.close()
    assert marker.read_text() == "cancelled"
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
