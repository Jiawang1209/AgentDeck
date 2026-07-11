from __future__ import annotations

from typing import Any

from acp import schema
from pydantic import BaseModel

from agentdeck.runtime.protocol import TransportCapabilities, UPDATE_KINDS


MAX_ACP_MESSAGE_BYTES = 64 * 1024
MAX_ACP_TURN_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_ACP_UPDATES_PER_TURN = 256
MAX_ACP_IDENTIFIER_BYTES = 256
MAX_ACP_SUMMARY_BYTES = 1024

STOP_REASON_TO_TURN_STATE: dict[str, tuple[str, str]] = {
    "end_turn": ("completed", "end_turn"),
    "max_tokens": ("blocked", "max_tokens"),
    "max_turn_requests": ("blocked", "max_turn_requests"),
    "refusal": ("blocked", "refusal"),
    "cancelled": ("failed", "cancelled"),
}

_MESSAGE_ROLES = {
    schema.AgentMessageChunk: "agent",
    schema.UserMessageChunk: "user",
    schema.AgentThoughtChunk: "thought",
}
_UPDATE_DISCRIMINATORS = {
    schema.AgentMessageChunk: "agent_message_chunk",
    schema.UserMessageChunk: "user_message_chunk",
    schema.AgentThoughtChunk: "agent_thought_chunk",
    schema.ToolCallStart: "tool_call",
    schema.ToolCallProgress: "tool_call_update",
    schema.AgentPlanUpdate: "plan",
    schema.AgentPlanContentUpdate: "plan_update",
    schema.AgentPlanRemovedUpdate: "plan_removed",
    schema.AvailableCommandsUpdate: "available_commands_update",
    schema.CurrentModeUpdate: "current_mode_update",
    schema.ConfigOptionUpdate: "config_option_update",
    schema.SessionInfoUpdate: "session_info_update",
    schema.UsageUpdate: "usage_update",
}
_TOOL_RESULT_STATUSES = {"completed", "failed"}
_HIGH_RISK_TOOL_KINDS = {"edit", "delete", "move", "execute", "switch_mode"}
_LOW_RISK_TOOL_KINDS = {"read", "search", "think", "fetch"}


def _bounded_text(name: str, value: object, *, limit: int = MAX_ACP_SUMMARY_BYTES) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    shortened = encoded[:limit]
    while shortened:
        try:
            return shortened.decode("utf-8")
        except UnicodeDecodeError:
            shortened = shortened[:-1]
    raise ValueError(f"{name} cannot be normalized")


def _bounded_string(name: str, value: object, *, limit: int = MAX_ACP_SUMMARY_BYTES) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    shortened = encoded[:limit]
    while shortened:
        try:
            return shortened.decode("utf-8")
        except UnicodeDecodeError:
            shortened = shortened[:-1]
    return ""


def _identifier(name: str, value: object) -> str:
    return _bounded_text(name, value, limit=MAX_ACP_IDENTIFIER_BYTES)


def _exact_count(name: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def ensure_message_within_bounds(message_bytes: object) -> int:
    size = _exact_count("message_bytes", message_bytes)
    if size > MAX_ACP_MESSAGE_BYTES:
        raise ValueError(f"ACP message exceeds {MAX_ACP_MESSAGE_BYTES} bytes")
    return size


def ensure_turn_within_bounds(payload_bytes: object, updates: object) -> tuple[int, int]:
    payload_size = _exact_count("payload_bytes", payload_bytes)
    update_count = _exact_count("updates", updates)
    if payload_size > MAX_ACP_TURN_PAYLOAD_BYTES:
        raise ValueError(f"ACP turn payload exceeds {MAX_ACP_TURN_PAYLOAD_BYTES} bytes")
    if update_count > MAX_ACP_UPDATES_PER_TURN:
        raise ValueError(f"ACP turn updates exceed {MAX_ACP_UPDATES_PER_TURN}")
    return payload_size, update_count


def map_agent_capabilities(response: schema.InitializeResponse) -> TransportCapabilities:
    if not isinstance(response, schema.InitializeResponse):
        raise TypeError("response must be an InitializeResponse")
    agent_caps = response.agent_capabilities
    session_caps = agent_caps.session_capabilities if agent_caps is not None else None
    resume_supported = session_caps is not None and session_caps.resume is not None
    return TransportCapabilities(
        structured_sessions=True,
        streaming_updates=True,
        structured_tools=True,
        permission_requests=True,
        resume_session=resume_supported,
        observable_terminal=False,
    )


def map_stop_reason(stop_reason: object) -> tuple[str, str]:
    if type(stop_reason) is not str or not stop_reason:
        raise ValueError("stop_reason must be a non-empty string")
    return STOP_REASON_TO_TURN_STATE.get(stop_reason, ("failed", "unknown_stop_reason"))


def _model_allowlist(model: BaseModel, fields: tuple[str, ...]) -> dict[str, Any]:
    source = model.model_dump(by_alias=True, exclude_none=True)
    return {field: source[field] for field in fields if field in source}


def _map_content_chunk(update: BaseModel, role: str) -> tuple[str, dict[str, Any]]:
    content = update.content
    if not isinstance(content, schema.TextContentBlock):
        raise ValueError("unsupported ACP content block")
    text = content.text
    if type(text) is not str:
        raise ValueError("content.text must be a string")
    payload: dict[str, Any] = {
        "role": role,
        "content": {"type": "text", "text": text},
    }
    if update.message_id is not None:
        payload["message_id"] = _identifier("message_id", update.message_id)
    return "text", payload


def _tool_base(update: schema.ToolCallStart | schema.ToolCallProgress) -> dict[str, Any]:
    payload: dict[str, Any] = {"tool_call_id": _identifier("tool_call_id", update.tool_call_id)}
    if update.title is not None:
        payload["title"] = _bounded_text("title", update.title)
    if update.kind is not None:
        payload["kind"] = update.kind
    if update.status is not None:
        payload["status"] = update.status
    return payload


def _artifact_summaries(update: schema.ToolCallStart | schema.ToolCallProgress) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in update.content or []:
        if isinstance(item, schema.FileEditToolCallContent):
            summaries.append({"type": "diff", "path": _bounded_text("artifact path", item.path)})
        elif isinstance(item, schema.ContentToolCallContent):
            content = item.content
            if isinstance(content, schema.ResourceContentBlock):
                summaries.append({"type": "resource", "uri": _bounded_text("artifact uri", content.uri)})
            elif isinstance(content, schema.EmbeddedResourceContentBlock):
                resource = content.resource
                summaries.append({"type": "resource", "uri": _bounded_text("artifact uri", resource.uri)})
    return summaries


def _map_tool_update(update: schema.ToolCallStart | schema.ToolCallProgress) -> tuple[str, dict[str, Any]]:
    payload = _tool_base(update)
    artifacts = _artifact_summaries(update)
    if artifacts:
        artifact_payload: dict[str, Any] = {
            "tool_call_id": payload["tool_call_id"],
            "artifacts": artifacts,
        }
        if update.status is not None:
            artifact_payload["status"] = update.status
        return "artifact", artifact_payload
    kind = "tool_result" if update.status in _TOOL_RESULT_STATUSES else "tool_call"
    return kind, payload


def _map_plan(update: schema.AgentPlanUpdate) -> tuple[str, dict[str, Any]]:
    entries = []
    for entry in update.entries:
        entries.append({
            "content": _bounded_text("plan content", entry.content),
            "priority": entry.priority,
            "status": entry.status,
        })
    return "progress", {"session_update": "plan", "entries": entries}


def _map_plan_content(update: schema.AgentPlanContentUpdate) -> tuple[str, dict[str, Any]]:
    plan = update.plan
    return "progress", {
        "session_update": "plan_update",
        "id": _identifier("plan id", plan.id),
        "plan_type": plan.type,
    }


def _map_progress(update: BaseModel, discriminator: str, fields: tuple[str, ...]) -> tuple[str, dict[str, Any]]:
    values = _model_allowlist(update, fields)
    payload: dict[str, Any] = {"session_update": discriminator}
    for key, value in values.items():
        normalized_key = {
            "currentModeId": "current_mode_id",
            "updatedAt": "updated_at",
        }.get(key, key)
        if type(value) is str:
            value = _bounded_text(normalized_key, value)
        payload[normalized_key] = value
    return "progress", payload


def _map_config(update: schema.ConfigOptionUpdate) -> tuple[str, dict[str, Any]]:
    options = []
    for option in update.config_options:
        if isinstance(option, schema.SessionConfigOptionSelect):
            if option.type != "select":
                raise ValueError("ACP config option discriminator does not match its typed model")
            current_value: str | bool = _bounded_string("current_value", option.current_value)
            option_type = "select"
        elif isinstance(option, schema.SessionConfigOptionBoolean):
            if option.type != "boolean":
                raise ValueError("ACP config option discriminator does not match its typed model")
            if type(option.current_value) is not bool:
                raise TypeError("current_value must be a bool")
            current_value = option.current_value
            option_type = "boolean"
        else:
            raise TypeError("unsupported ACP config option")
        item = {
            "id": _identifier("config option id", option.id),
            "name": _bounded_text("config option name", option.name),
            "type": option_type,
            "current_value": current_value,
        }
        options.append(item)
    return "progress", {"session_update": "config_option_update", "config_options": options}


def map_session_update(update: object) -> tuple[str, dict[str, Any]]:
    expected_discriminator = next(
        (value for cls, value in _UPDATE_DISCRIMINATORS.items() if isinstance(update, cls)),
        None,
    )
    if expected_discriminator is None:
        raise TypeError("unsupported ACP session update")
    if update.session_update != expected_discriminator:
        raise ValueError("ACP session update discriminator does not match its typed model")
    for cls, role in _MESSAGE_ROLES.items():
        if isinstance(update, cls):
            return _map_content_chunk(update, role)
    if isinstance(update, (schema.ToolCallStart, schema.ToolCallProgress)):
        mapped = _map_tool_update(update)
    elif isinstance(update, schema.AgentPlanUpdate):
        mapped = _map_plan(update)
    elif isinstance(update, schema.AgentPlanContentUpdate):
        mapped = _map_plan_content(update)
    elif isinstance(update, schema.CurrentModeUpdate):
        mapped = _map_progress(update, "current_mode_update", ("currentModeId",))
    elif isinstance(update, schema.SessionInfoUpdate):
        mapped = _map_progress(update, "session_info_update", ("title", "updatedAt"))
    elif isinstance(update, schema.UsageUpdate):
        mapped = _map_progress(update, "usage_update", ("used", "size"))
    elif isinstance(update, schema.ConfigOptionUpdate):
        mapped = _map_config(update)
    elif isinstance(update, schema.AvailableCommandsUpdate):
        mapped = "progress", {
            "session_update": "available_commands_update",
            "command_count": len(update.available_commands),
        }
    elif isinstance(update, schema.AgentPlanRemovedUpdate):
        mapped = "progress", {
            "session_update": "plan_removed",
            "id": _identifier("plan id", update.id),
        }
    else:
        raise TypeError("unsupported ACP session update")
    if mapped[0] not in UPDATE_KINDS:
        raise ValueError("mapped update kind is not supported by the protocol ledger")
    return mapped


def _permission_risk(kind: str | None) -> str:
    if kind in _HIGH_RISK_TOOL_KINDS:
        return "high"
    if kind in _LOW_RISK_TOOL_KINDS:
        return "low"
    return "unknown"


def summarize_permission(request: schema.RequestPermissionRequest) -> dict[str, Any]:
    if not isinstance(request, schema.RequestPermissionRequest):
        raise TypeError("request must be a RequestPermissionRequest")
    tool_call = request.tool_call
    summary: dict[str, Any] = {
        "tool_call_id": _identifier("tool_call_id", tool_call.tool_call_id),
        "risk": _permission_risk(tool_call.kind),
    }
    if tool_call.title is not None:
        summary["title"] = _bounded_text("title", tool_call.title)
    if tool_call.kind is not None:
        summary["kind"] = tool_call.kind
    if tool_call.locations:
        location = tool_call.locations[0]
        target = _bounded_text("target", location.path)
        if location.line is not None:
            target = _bounded_text("target", f"{target}:{location.line}")
        summary["target"] = target
    return summary
