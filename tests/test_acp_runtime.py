from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from typing import Any

import pytest
from acp import schema
from acp.exceptions import RequestError

from agentdeck.runtime.acp_client import AgentDeckAcpClient, PermissionDecision
from agentdeck.runtime.acp_mapping import MAX_ACP_TURN_PAYLOAD_BYTES, MAX_ACP_UPDATES_PER_TURN


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
        self, pending: dict[str, Any], decision: PermissionDecision
    ) -> None:
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
                           (KeyboardInterrupt(), "ctrl_c"), (asyncio.CancelledError(), "cancelled")]
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
