from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math
import re
from typing import Any

from agentdeck.models import new_id, utc_now


AGENT_SESSION_STATES = ("created", "connecting", "ready", "busy", "reconnecting", "stopped", "failed")
TURN_STATES = ("created", "submitted", "streaming", "waiting_permission", "completed", "blocked", "failed", "ambiguous")
UPDATE_KINDS = ("progress", "text", "tool_call", "tool_result", "permission_request", "artifact", "completion", "error")
PERMISSION_STATES = ("pending", "approved", "denied", "expired")


@dataclass(frozen=True)
class TransportCapabilities:
    structured_sessions: bool
    streaming_updates: bool
    structured_tools: bool
    permission_requests: bool
    resume_session: bool
    observable_terminal: bool

    def __post_init__(self) -> None:
        for item in fields(self):
            if type(getattr(self, item.name)) is not bool:
                raise TypeError(f"{item.name} must be a bool")

    @classmethod
    def tmux_fallback(cls) -> "TransportCapabilities":
        return cls(False, False, False, False, False, True)

    def summary(self) -> dict[str, bool]:
        return asdict(self)


def _required_string(name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _record_id(name: str, value: object, prefix: str) -> str:
    result = _required_string(name, value)
    if re.fullmatch(rf"{re.escape(prefix)}[a-z0-9]+", result) is None:
        raise ValueError(f"{name} must match {prefix}<lowercase alphanumeric token>")
    return result


def _clone_json_value(value: object, active_containers: set[int]) -> Any:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("payload numbers must be finite")
        return value
    if type(value) not in (list, dict):
        raise TypeError("payload must contain only JSON-safe values")

    identity = id(value)
    if identity in active_containers:
        raise TypeError("payload must contain only JSON-safe values")
    active_containers.add(identity)
    try:
        if type(value) is list:
            return [_clone_json_value(item, active_containers) for item in value]
        clone: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("payload keys must be strings")
            clone[key] = _clone_json_value(item, active_containers)
        return clone
    finally:
        active_containers.remove(identity)


def build_agent_session(
    agent_id: str,
    provider: str,
    transport: str,
    native_session_id: str | None,
    workspace: str,
    capabilities: TransportCapabilities,
) -> dict[str, Any]:
    agent_id = _required_string("agent_id", agent_id)
    provider = _required_string("provider", provider)
    transport = _required_string("transport", transport)
    workspace = _required_string("workspace", workspace)
    if native_session_id is not None and (type(native_session_id) is not str or not native_session_id.strip()):
        raise ValueError("native_session_id must be None or a non-empty string")
    if type(capabilities) is not TransportCapabilities:
        raise TypeError("capabilities must be a TransportCapabilities instance")
    now = utc_now()
    return {
        "session_id": new_id("ags"),
        "agent_id": agent_id,
        "provider": provider,
        "transport": transport,
        "native_session_id": native_session_id,
        "workspace": workspace,
        "capabilities": capabilities.summary(),
        "state": "created",
        "created_at": now,
        "updated_at": now,
        "observation_bindings": [],
    }


def build_turn(session_id: str, message_id: str) -> dict[str, Any]:
    session_id = _record_id("session_id", session_id, "ags_")
    message_id = _required_string("message_id", message_id)
    now = utc_now()
    return {
        "turn_id": new_id("trn"),
        "session_id": session_id,
        "message_id": message_id,
        "state": "created",
        "created_at": now,
        "updated_at": now,
    }


def build_transport_update(
    session_id: str,
    turn_id: str,
    sequence: int,
    kind: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    session_id = _record_id("session_id", session_id, "ags_")
    turn_id = _record_id("turn_id", turn_id, "trn_")
    if type(sequence) is not int or sequence < 0:
        raise ValueError("sequence must be a non-negative integer")
    if type(kind) is not str or kind not in UPDATE_KINDS:
        raise ValueError("kind must be one of UPDATE_KINDS")
    if type(payload) is not dict:
        raise TypeError("payload must be a dict")
    return {
        "update_id": new_id("upd"),
        "session_id": session_id,
        "turn_id": turn_id,
        "sequence": sequence,
        "kind": kind,
        "payload": _clone_json_value(payload, set()),
        "created_at": utc_now(),
    }


def build_permission_request(
    session_id: str,
    turn_id: str,
    tool_name: str,
    target: str,
    risk: str,
) -> dict[str, Any]:
    session_id = _record_id("session_id", session_id, "ags_")
    turn_id = _record_id("turn_id", turn_id, "trn_")
    tool_name = _required_string("tool_name", tool_name)
    target = _required_string("target", target)
    risk = _required_string("risk", risk)
    now = utc_now()
    return {
        "permission_id": new_id("prm"),
        "session_id": session_id,
        "turn_id": turn_id,
        "tool_name": tool_name,
        "target": target,
        "risk": risk,
        "status": "pending",
        "decision": None,
        "created_at": now,
        "updated_at": now,
    }
