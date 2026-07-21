"""Strict, content-free JSON Lines protocol for the human Observer channel."""

from __future__ import annotations

import json
from types import MappingProxyType

from agentdeck.ports.observer import (
    ObserverAcknowledgement, ObserverBinding, ObserverCursor,
    ObserverPublication, ObserverSubscription, cursor_for_event,
)
from agentdeck.ports.worker import WorkerEvent


VERSION = "observer-ipc/v1"
MAX_FRAME_BYTES = 256 * 1024
_MAX_ITEMS = 512
_MAX_DEPTH = 10
_FRAME_FIELDS = {
    "handshake": {"version", "type", "project_id", "session_id", "agent_id", "mode"},
    "binding": {
        "version", "type", "project_id", "session_id", "agent_id", "task_id",
        "attempt_id", "transport", "cursor",
    },
    "event": {
        "version", "type", "project_id", "session_id", "agent_id", "task_id",
        "attempt_id", "transport", "sequence", "event_id", "timestamp", "kind",
        "payload", "fingerprint",
    },
    "ack": {
        "version", "type", "project_id", "session_id", "agent_id", "task_id",
        "attempt_id", "transport", "sequence", "event_id", "fingerprint",
    },
    "degraded": {"version", "type", "code", "stage"},
    "close": {"version", "type", "reason"},
}
_DEGRADED_CODES = frozenset({
    "observer_endpoint_unavailable", "observer_endpoint_replaced",
    "observer_subscription_mismatch", "observer_protocol_invalid",
})
_STAGES = frozenset({"connect", "handshake", "subscribe", "delivery", "shutdown"})
_CLOSE_REASONS = frozenset({"server_closed", "client_closed"})


class ObserverProtocolError(RuntimeError):
    ALLOWED = frozenset({
        "observer_frame_invalid", "observer_frame_oversize",
        "observer_frame_utf8_invalid", "observer_protocol_version_invalid",
        "observer_protocol_identity_mismatch",
    })

    def __init__(self, code: str) -> None:
        if code not in self.ALLOWED:
            raise ValueError("unknown Observer protocol error")
        self.code = code
        super().__init__(code)


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise ObserverProtocolError("observer_frame_invalid")
        result[key] = value
    return result


def _closed_json(value: object) -> None:
    budget = [_MAX_ITEMS]

    def visit(item: object, depth: int) -> None:
        budget[0] -= 1
        if depth > _MAX_DEPTH or budget[0] < 0:
            raise ObserverProtocolError("observer_frame_invalid")
        if item is None or type(item) in {bool, int, float}:
            if type(item) is float and (
                item != item or item in {float("inf"), float("-inf")}
            ):
                raise ObserverProtocolError("observer_frame_invalid")
            return
        if type(item) is str:
            try:
                encoded = item.encode("utf-8", "strict")
            except UnicodeError:
                raise ObserverProtocolError("observer_frame_utf8_invalid") from None
            if not encoded or len(encoded) > 64 * 1024:
                raise ObserverProtocolError("observer_frame_invalid")
            return
        if type(item) is list:
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ObserverProtocolError("observer_frame_invalid")
                visit(key, depth + 1)
                visit(child, depth + 1)
            return
        raise ObserverProtocolError("observer_frame_invalid")

    visit(value, 0)


def decode_frame(
    wire: bytes, *, expected: ObserverSubscription | ObserverBinding | None = None,
) -> dict[str, object]:
    if type(wire) is not bytes or not wire.endswith(b"\n") or wire.count(b"\n") != 1:
        raise ObserverProtocolError("observer_frame_invalid")
    if len(wire) > MAX_FRAME_BYTES:
        raise ObserverProtocolError("observer_frame_oversize")
    try:
        text = wire[:-1].decode("utf-8", "strict")
    except UnicodeError:
        raise ObserverProtocolError("observer_frame_utf8_invalid") from None
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
    except ObserverProtocolError:
        raise
    except Exception:
        raise ObserverProtocolError("observer_frame_invalid") from None
    if type(value) is not dict:
        raise ObserverProtocolError("observer_frame_invalid")
    _closed_json(value)
    kind = value.get("type")
    if kind not in _FRAME_FIELDS or set(value) != _FRAME_FIELDS[kind]:
        raise ObserverProtocolError("observer_frame_invalid")
    if value.get("version") != VERSION:
        raise ObserverProtocolError("observer_protocol_version_invalid")
    try:
        _validate_shape(kind, value)
        _validate_expected(value, expected)
    except ObserverProtocolError:
        raise
    except Exception:
        raise ObserverProtocolError("observer_frame_invalid") from None
    return value


def encode_frame(value: dict[str, object]) -> bytes:
    try:
        wire = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", "strict") + b"\n"
    except Exception:
        raise ObserverProtocolError("observer_frame_invalid") from None
    decode_frame(wire)
    return wire


def _validate_shape(kind: str, value: dict[str, object]) -> None:
    if kind == "handshake":
        ObserverSubscription(value["project_id"], value["session_id"], value["agent_id"])
        if value["mode"] != "read_only":
            raise ValueError
    elif kind == "binding":
        binding_from_frame(value)
        if value["cursor"] is not None:
            cursor_from_dict(value["cursor"], binding=value)
    elif kind == "event":
        event = event_from_frame(value)
        cursor = cursor_from_dict(value, binding=value)
        if cursor != cursor_for_event(value["project_id"], event):
            raise ValueError
    elif kind == "ack":
        cursor_from_dict(value, binding=value)
    elif kind == "degraded":
        if value["code"] not in _DEGRADED_CODES or value["stage"] not in _STAGES:
            raise ValueError
    elif value["reason"] not in _CLOSE_REASONS:
        raise ValueError


def _validate_expected(
    value: dict[str, object], expected: ObserverSubscription | ObserverBinding | None,
) -> None:
    if expected is None or value["type"] in {"degraded", "close"}:
        return
    fields = ("project_id", "session_id", "agent_id")
    if any(value[name] != getattr(expected, name) for name in fields):
        raise ObserverProtocolError("observer_protocol_identity_mismatch")
    if type(expected) is ObserverBinding and value["type"] != "handshake":
        extra = ("task_id", "attempt_id", "transport")
        if any(value[name] != getattr(expected, name) for name in extra):
            raise ObserverProtocolError("observer_protocol_identity_mismatch")


def _thaw(value: object) -> object:
    if type(value) is MappingProxyType:
        return {key: _thaw(value[key]) for key in value}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


def _cursor_dict(cursor: ObserverCursor) -> dict[str, object]:
    return dict(cursor.__dict__)


def handshake_frame(subscription: ObserverSubscription) -> dict[str, object]:
    return {
        "version": VERSION, "type": "handshake", "project_id": subscription.project_id,
        "session_id": subscription.session_id, "agent_id": subscription.agent_id,
        "mode": "read_only",
    }


def binding_frame(
    binding: ObserverBinding, cursor: ObserverCursor | None,
) -> dict[str, object]:
    return {
        "version": VERSION, "type": "binding", **binding.__dict__,
        "cursor": None if cursor is None else _cursor_dict(cursor),
    }


def event_frame(publication: ObserverPublication) -> dict[str, object]:
    event = publication.event
    return {
        "version": VERSION, "type": "event", "project_id": publication.binding.project_id,
        "session_id": event.session_id, "agent_id": event.agent_id,
        "task_id": event.task_id, "attempt_id": event.attempt_id,
        "transport": event.transport, "sequence": event.sequence,
        "event_id": event.event_id, "timestamp": event.timestamp, "kind": event.kind,
        "payload": _thaw(event.payload), "fingerprint": publication.cursor.fingerprint,
    }


def acknowledgement_frame(cursor: ObserverCursor) -> dict[str, object]:
    return {"version": VERSION, "type": "ack", **_cursor_dict(cursor)}


def binding_from_frame(value: dict[str, object]) -> ObserverBinding:
    return ObserverBinding(*(value[name] for name in (
        "project_id", "session_id", "agent_id", "task_id", "attempt_id", "transport",
    )))


def cursor_from_dict(
    value: object, *, binding: dict[str, object] | None = None,
) -> ObserverCursor:
    if type(value) is not dict:
        raise ValueError("cursor shape invalid")
    cursor = ObserverCursor(*(value[name] for name in (
        "project_id", "session_id", "agent_id", "task_id", "attempt_id", "transport",
        "sequence", "event_id", "fingerprint",
    )))
    if binding is not None and any(
        getattr(cursor, name) != binding[name] for name in (
            "project_id", "session_id", "agent_id", "task_id", "attempt_id", "transport",
        )
    ):
        raise ValueError("cursor binding invalid")
    return cursor


def event_from_frame(value: dict[str, object]) -> WorkerEvent:
    return WorkerEvent(*(value[name] for name in (
        "event_id", "session_id", "agent_id", "task_id", "attempt_id", "transport",
        "sequence", "kind", "timestamp", "payload",
    )))


__all__ = [
    "MAX_FRAME_BYTES", "ObserverProtocolError", "VERSION", "acknowledgement_frame",
    "binding_frame", "binding_from_frame", "cursor_from_dict", "decode_frame",
    "encode_frame", "event_frame", "event_from_frame", "handshake_frame",
]
