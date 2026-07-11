from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import math
import re
from typing import Any

from agentdeck.models import new_id, utc_now


AGENT_SESSION_STATES = ("created", "connecting", "ready", "busy", "reconnecting", "disconnected", "stopped", "failed")
TURN_STATES = ("created", "submitted", "streaming", "waiting_permission", "completed", "blocked", "failed", "ambiguous")
TURN_KINDS = ("prompt", "load_replay")
UPDATE_KINDS = ("progress", "text", "tool_call", "tool_result", "permission_request", "artifact", "completion", "error")
PERMISSION_STATES = ("pending", "approved", "denied", "expired")
TRANSPORT_KINDS = ("acp", "acp-adapter", "tmux", "api")
PROTOCOL_ENTITY_TYPES = ("session", "turn", "permission")
PROTOCOL_TRANSITION_DETAILS_MAX_BYTES = 4096
PROTOCOL_TRANSITION_REASON_MAX_LENGTH = 128

SESSION_TRANSITION_EDGES = {
    ("created", "connecting"), ("created", "ready"), ("created", "failed"),
    ("connecting", "ready"), ("connecting", "disconnected"), ("connecting", "failed"),
    ("ready", "busy"), ("ready", "disconnected"), ("ready", "stopped"), ("ready", "failed"),
    ("busy", "ready"), ("busy", "disconnected"), ("busy", "stopped"), ("busy", "failed"),
    ("disconnected", "reconnecting"), ("disconnected", "stopped"),
    ("reconnecting", "ready"), ("reconnecting", "disconnected"), ("reconnecting", "failed"),
}
TURN_TRANSITION_EDGES = {
    ("created", "submitted"), ("created", "streaming"), ("created", "completed"),
    ("created", "ambiguous"),
    ("submitted", "streaming"), ("submitted", "waiting_permission"),
    ("submitted", "completed"), ("submitted", "blocked"), ("submitted", "failed"),
    ("submitted", "ambiguous"),
    ("streaming", "waiting_permission"), ("streaming", "completed"),
    ("streaming", "blocked"), ("streaming", "failed"), ("streaming", "ambiguous"),
    ("waiting_permission", "streaming"), ("waiting_permission", "blocked"),
    ("waiting_permission", "failed"), ("waiting_permission", "ambiguous"),
}
PERMISSION_TRANSITION_EDGES = {
    ("pending", "approved"), ("pending", "denied"), ("pending", "expired"),
}
PROTOCOL_TRANSITION_EDGES = {
    "session": SESSION_TRANSITION_EDGES,
    "turn": TURN_TRANSITION_EDGES,
    "permission": PERMISSION_TRANSITION_EDGES,
}


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
    if type(transport) is not str or transport not in TRANSPORT_KINDS:
        raise ValueError("transport must be one of TRANSPORT_KINDS")
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


def build_turn(session_id: str, message_id: str, kind: str = "prompt") -> dict[str, Any]:
    session_id = _record_id("session_id", session_id, "ags_")
    message_id = _required_string("message_id", message_id)
    if type(kind) is not str or kind not in TURN_KINDS:
        raise ValueError("kind must be one of TURN_KINDS")
    now = utc_now()
    return {
        "turn_id": new_id("trn"),
        "session_id": session_id,
        "message_id": message_id,
        "kind": kind,
        "state": "created",
        "created_at": now,
        "updated_at": now,
    }


def validate_transition_edge(entity_type: str, from_state: str, to_state: str) -> None:
    if type(entity_type) is not str or entity_type not in PROTOCOL_ENTITY_TYPES:
        raise ValueError("entity_type must be one of PROTOCOL_ENTITY_TYPES")
    if type(from_state) is not str or type(to_state) is not str:
        raise ValueError("invalid protocol state transition")
    if (from_state, to_state) not in PROTOCOL_TRANSITION_EDGES[entity_type]:
        raise ValueError(f"invalid protocol state transition: {from_state} -> {to_state}")


def build_protocol_transition(
    entity_type: str,
    entity_id: str,
    from_state: str,
    to_state: str,
    reason: str | None,
    details: dict[str, Any],
) -> dict[str, Any]:
    validate_transition_edge(entity_type, from_state, to_state)
    prefixes = {"session": "ags_", "turn": "trn_", "permission": "prm_"}
    entity_id = _record_id("entity_id", entity_id, prefixes[entity_type])
    if reason is not None:
        if type(reason) is not str or not reason.strip():
            raise ValueError("reason must be null or a non-empty string")
        if len(reason) > PROTOCOL_TRANSITION_REASON_MAX_LENGTH:
            raise ValueError(
                f"reason must be at most {PROTOCOL_TRANSITION_REASON_MAX_LENGTH} characters"
            )
    if type(details) is not dict:
        raise TypeError("details must be a dict")
    cloned_details = _clone_json_value(details, set())
    encoded_details = json.dumps(
        cloned_details, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded_details) > PROTOCOL_TRANSITION_DETAILS_MAX_BYTES:
        raise ValueError(
            f"details must be at most {PROTOCOL_TRANSITION_DETAILS_MAX_BYTES} bytes"
        )
    return {
        "transition_id": new_id("pst"),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "from_state": from_state,
        "to_state": to_state,
        "reason": reason,
        "details": cloned_details,
        "created_at": utc_now(),
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
