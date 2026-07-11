from __future__ import annotations

import math

import pytest
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AgentPlanContentUpdate,
    AgentPlanUpdate,
    AgentThoughtChunk,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    FileEditToolCallContent,
    InitializeResponse,
    PlanEntry,
    RequestPermissionRequest,
    SessionCapabilities,
    SessionConfigOptionBoolean,
    SessionInfoUpdate,
    SessionResumeCapabilities,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
    UserMessageChunk,
)
from pydantic import BaseModel, ConfigDict

from agentdeck.runtime.acp_mapping import (
    MAX_ACP_MESSAGE_BYTES,
    MAX_ACP_TURN_PAYLOAD_BYTES,
    MAX_ACP_UPDATES_PER_TURN,
    ensure_message_within_bounds,
    ensure_turn_within_bounds,
    map_agent_capabilities,
    map_session_update,
    map_stop_reason,
    summarize_permission,
)
from agentdeck.runtime.protocol import UPDATE_KINDS


def _text_update(cls: type[BaseModel], text: str, message_id: str | None = "m1") -> BaseModel:
    discriminators = {
        AgentMessageChunk: "agent_message_chunk",
        UserMessageChunk: "user_message_chunk",
        AgentThoughtChunk: "agent_thought_chunk",
    }
    return cls(
        sessionUpdate=discriminators[cls],
        content=TextContentBlock(type="text", text=text),
        messageId=message_id,
    )


@pytest.mark.parametrize(
    ("cls", "role"),
    [
        (AgentMessageChunk, "agent"),
        (UserMessageChunk, "user"),
        (AgentThoughtChunk, "thought"),
    ],
)
def test_text_chunks_map_to_allowlisted_text(cls: type[BaseModel], role: str) -> None:
    update = _text_update(cls, "hello")
    update.field_meta = {"secret": "not persisted"}

    mapped = map_session_update(update)

    assert mapped == (
        "text",
        {"role": role, "message_id": "m1", "content": {"type": "text", "text": "hello"}},
    )
    assert mapped[0] in UPDATE_KINDS


def test_capabilities_are_negotiated_and_omitted_resume_is_unsupported() -> None:
    response = InitializeResponse(
        protocolVersion=1,
        agentCapabilities=AgentCapabilities(loadSession=True, sessionCapabilities=SessionCapabilities()),
    )
    caps = map_agent_capabilities(response)
    assert caps.summary() == {
        "structured_sessions": True,
        "streaming_updates": True,
        "structured_tools": True,
        "permission_requests": True,
        "resume_session": False,
        "observable_terminal": False,
    }

    response.agent_capabilities.session_capabilities = SessionCapabilities(
        resume=SessionResumeCapabilities()
    )
    assert map_agent_capabilities(response).resume_session is True


@pytest.mark.parametrize("load_session", [False, None])
def test_omitted_or_false_load_is_not_treated_as_supported(load_session: bool | None) -> None:
    response = InitializeResponse(
        protocolVersion=1,
        agentCapabilities=AgentCapabilities(loadSession=load_session, sessionCapabilities=None),
    )
    assert map_agent_capabilities(response).structured_sessions is True
    assert map_agent_capabilities(response).resume_session is False


def test_tool_start_and_updates_redact_raw_values_and_choose_result_by_status() -> None:
    start = ToolCallStart(
        sessionUpdate="tool_call",
        toolCallId="call-1", title="Edit file", kind="edit", status="in_progress",
        rawInput={"env": {"TOKEN": "secret"}}, _meta={"secret": True},
    )
    assert map_session_update(start) == (
        "tool_call",
        {"tool_call_id": "call-1", "title": "Edit file", "kind": "edit", "status": "in_progress"},
    )

    progress = ToolCallProgress(
        sessionUpdate="tool_call_update",
        toolCallId="call-1", title="Done", kind="edit", status="completed",
        rawOutput={"secret": "value"},
    )
    assert map_session_update(progress) == (
        "tool_result",
        {"tool_call_id": "call-1", "title": "Done", "kind": "edit", "status": "completed"},
    )


def test_file_diff_content_maps_only_to_artifact_summary() -> None:
    update = ToolCallProgress(
        sessionUpdate="tool_call_update",
        toolCallId="call-2", status="completed",
        content=[FileEditToolCallContent(type="diff", path="src/a.py", oldText="secret old", newText="secret new")],
    )
    kind, payload = map_session_update(update)
    assert kind == "artifact"
    assert payload == {
        "tool_call_id": "call-2",
        "artifacts": [{"type": "diff", "path": "src/a.py"}],
        "status": "completed",
    }
    assert "secret" not in repr(payload)


def test_plan_progress_and_information_updates_are_bounded_allowlists() -> None:
    plan = AgentPlanUpdate(
        sessionUpdate="plan",
        entries=[PlanEntry(content="Implement", priority="high", status="in_progress")],
        _meta={"secret": "drop"},
    )
    assert map_session_update(plan) == (
        "progress",
        {"session_update": "plan", "entries": [{"content": "Implement", "priority": "high", "status": "in_progress"}]},
    )
    content_plan = AgentPlanContentUpdate(
        sessionUpdate="plan_update",
        plan={"type": "markdown", "id": "plan-1", "content": "private plan body"},
    )
    assert map_session_update(content_plan) == (
        "progress", {"session_update": "plan_update", "id": "plan-1", "plan_type": "markdown"}
    )
    assert map_session_update(CurrentModeUpdate(sessionUpdate="current_mode_update", currentModeId="code")) == (
        "progress", {"session_update": "current_mode_update", "current_mode_id": "code"}
    )
    assert map_session_update(SessionInfoUpdate(sessionUpdate="session_info_update", title="Title", updatedAt="2026-07-12T00:00:00Z")) == (
        "progress", {"session_update": "session_info_update", "title": "Title", "updated_at": "2026-07-12T00:00:00Z"}
    )
    assert map_session_update(UsageUpdate(sessionUpdate="usage_update", used=5, size=100)) == (
        "progress", {"session_update": "usage_update", "used": 5, "size": 100}
    )
    config = ConfigOptionUpdate(
        sessionUpdate="config_option_update",
        configOptions=[SessionConfigOptionBoolean(id="safe", name="Safe", currentValue=True, type="boolean")]
    )
    assert map_session_update(config) == (
        "progress",
        {"session_update": "config_option_update", "config_options": [{"id": "safe", "name": "Safe", "type": "boolean", "current_value": True}]},
    )


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("end_turn", ("completed", "end_turn")),
        ("max_tokens", ("blocked", "max_tokens")),
        ("max_turn_requests", ("blocked", "max_turn_requests")),
        ("refusal", ("blocked", "refusal")),
        ("cancelled", ("failed", "cancelled")),
        ("future_reason", ("failed", "unknown_stop_reason")),
    ],
)
def test_stop_reason_table_never_synthesizes_unknown_success(reason: str, expected: tuple[str, str]) -> None:
    assert map_stop_reason(reason) == expected


def test_permission_summary_is_compact_conservative_and_redacted() -> None:
    request = RequestPermissionRequest(
        sessionId="native-1",
        toolCall={
            "toolCallId": "call-3", "title": "Run command", "kind": "execute",
            "locations": [{"path": "/tmp/work/file.txt", "line": 9}],
            "rawInput": {"env": {"TOKEN": "secret"}}, "_meta": {"anything": "secret"},
        },
        options=[{"optionId": "yes", "name": "Allow", "kind": "allow_once"}],
        _meta={"env": "secret"},
    )
    assert summarize_permission(request) == {
        "tool_call_id": "call-3", "title": "Run command", "kind": "execute",
        "target": "/tmp/work/file.txt:9", "risk": "high",
    }

    request.tool_call.kind = "other"
    request.tool_call.locations = None
    assert summarize_permission(request)["risk"] == "unknown"


class FutureUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")
    session_update: str = "future_update"


@pytest.mark.parametrize("update", [FutureUpdate(secret="value"), object(), {"sessionUpdate": "agent_message_chunk"}])
def test_unknown_update_types_fail_instead_of_faking_success(update: object) -> None:
    with pytest.raises((TypeError, ValueError), match="unsupported ACP session update"):
        map_session_update(update)


def test_mutated_unknown_discriminator_on_known_model_fails_closed() -> None:
    update = _text_update(AgentMessageChunk, "hello")
    update.session_update = "future_update"
    with pytest.raises(ValueError, match="discriminator"):
        map_session_update(update)


def test_invalid_ids_and_unsupported_content_fail_closed() -> None:
    with pytest.raises(ValueError, match="message_id"):
        map_session_update(_text_update(AgentMessageChunk, "hello", ""))
    with pytest.raises(ValueError, match="tool_call_id"):
        map_session_update(ToolCallStart(sessionUpdate="tool_call", toolCallId=" ", title="x"))
    update = AgentMessageChunk.model_validate({
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "image", "data": "AAAA", "mimeType": "image/png"},
    })
    with pytest.raises(ValueError, match="unsupported ACP content"):
        map_session_update(update)


@pytest.mark.parametrize("value", [True, False, -1, 1.5, math.inf, math.nan])
def test_message_bound_rejects_bool_impostors_and_invalid_sizes(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ensure_message_within_bounds(value)


def test_message_bound_accepts_exact_edge_and_rejects_one_over() -> None:
    assert ensure_message_within_bounds(MAX_ACP_MESSAGE_BYTES) == MAX_ACP_MESSAGE_BYTES
    with pytest.raises(ValueError, match="message"):
        ensure_message_within_bounds(MAX_ACP_MESSAGE_BYTES + 1)


def test_turn_bounds_accept_exact_edges_and_reject_one_over() -> None:
    assert ensure_turn_within_bounds(MAX_ACP_TURN_PAYLOAD_BYTES, MAX_ACP_UPDATES_PER_TURN) == (
        MAX_ACP_TURN_PAYLOAD_BYTES, MAX_ACP_UPDATES_PER_TURN
    )
    with pytest.raises(ValueError, match="payload"):
        ensure_turn_within_bounds(MAX_ACP_TURN_PAYLOAD_BYTES + 1, 1)
    with pytest.raises(ValueError, match="updates"):
        ensure_turn_within_bounds(1, MAX_ACP_UPDATES_PER_TURN + 1)


@pytest.mark.parametrize("payload_bytes,updates", [(True, 1), (1, False), (-1, 1), (1, -1), (1.0, 1)])
def test_turn_bounds_reject_malformed_counts(payload_bytes: object, updates: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ensure_turn_within_bounds(payload_bytes, updates)
