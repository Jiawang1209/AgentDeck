"""Cursor-safe, redacted rendering of decoded Worker Event-shaped values."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Protocol


_EVENT_FIELDS = (
    "event_id", "session_id", "agent_id", "task_id", "attempt_id",
    "transport", "sequence", "kind", "timestamp", "payload",
)
_EVENT_KINDS = frozenset(
    "started progress tool_started tool_completed permission_requested "
    "artifact_changed message completed failed cancelled".split()
)
_ERROR_MESSAGES = {
    "observer_cursor_load_failed": "cursor loading failed",
    "observer_cursor_write_failed": "cursor acknowledgement failed",
    "observer_cursor_invalid": "invalid observer cursor",
    "observer_cursor_conflict": "event and cursor conflict",
    "observer_identity_mismatch": "identity mismatch",
    "observer_malformed_event": "malformed decoded event",
    "observer_sequence_conflict": "sequence conflict",
    "observer_sequence_gap": "sequence gap",
    "observer_sequence_rollback": "sequence rollback",
    "observer_sink_failed": "observation sink failed",
    "observer_subscription_failed": "event subscription failed",
}
_FORBIDDEN_FIELD_SHAPES = (
    "hiddenreasoning", "chainofthought", "rawacp", "rawprotocol", "rawframe",
    "fullprompt", "stderr",
)
_SENSITIVE_KEY_SHAPES = (
    "token", "credential", "authorization", "password", "secret",
    "privatekey", "sshkey", "cookie",
)
_SECRET_RELATION = re.compile(
    r"(?i)\b(?:token|credentials?|authorization|password|secret|"
    r"private[- ]?key(?: material)?)\s*"
    r"(?:is|are|was|were|values?\s*(?:is|are|:|=)|:|=)\s*\S+"
)
_CREDENTIAL_SHAPE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?:bearer\s+\S+|sk-[A-Za-z0-9_-]+|"
    r"ghp_[A-Za-z0-9_-]+|github_pat_[A-Za-z0-9_-]+|"
    r"AKIA[A-Z0-9]+|AIza[A-Za-z0-9_-]+)(?![A-Za-z0-9_-])"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
    r"-----END [^-\r\n]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_FORBIDDEN_CONTENT = re.compile(
    r"\b(?:raw\s+ACP\s+(?:frame|frames|log|logs)|"
    r"raw\s+protocol\s+(?:frame|frames|log|logs|transcript)|"
    r"hidden\s+reasoning|full\s+prompt|stderr)\b",
    re.IGNORECASE,
)
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_MAX_TEXT_BYTES = 64 * 1024
_MAX_ITEMS = 256
_MAX_DEPTH = 8
_MAX_SEQUENCE = 2**63 - 1


class ObserverError(RuntimeError):
    """Stable, content-free observation degradation."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERROR_MESSAGES:
            raise ValueError("unknown Observer error code")
        self.code = code
        super().__init__(f"{code}: {_ERROR_MESSAGES[code]}")


@dataclass(frozen=True)
class ObserverCursor:
    """Last acknowledged immutable event identity and exact fingerprint."""

    session_id: str
    agent_id: str
    task_id: str
    attempt_id: str
    transport: str
    sequence: int
    event_id: str
    fingerprint: str

    def __post_init__(self) -> None:
        _typed_identity(self.session_id, "ses_")
        _typed_identity(self.agent_id, "agt_")
        _typed_identity(self.task_id, "tsk_")
        _typed_identity(self.attempt_id, "att_")
        _typed_identity(self.event_id, "evt_")
        if self.transport != "acp":
            raise ValueError("cursor transport must be acp")
        if type(self.sequence) is not int or not 1 <= self.sequence <= _MAX_SEQUENCE:
            raise ValueError("cursor sequence is invalid")
        if type(self.fingerprint) is not str or not _HEX_64.fullmatch(self.fingerprint):
            raise ValueError("cursor fingerprint is invalid")

    @property
    def lineage(self) -> tuple[str, str, str, str, str]:
        return (
            self.session_id, self.agent_id, self.task_id, self.attempt_id,
            self.transport,
        )


class CursorStore(Protocol):
    """Injected foreground-Application cursor reader/writer boundary."""

    def load(self) -> ObserverCursor | None: ...
    def acknowledge(self, cursor: ObserverCursor) -> None: ...


class ObservationSink(Protocol):
    """Injected terminal observation boundary."""

    def emit(self, record: str) -> None: ...


@dataclass(frozen=True)
class _EventSnapshot:
    event_id: str
    session_id: str
    agent_id: str
    task_id: str
    attempt_id: str
    transport: str
    sequence: int
    kind: str
    timestamp: str
    payload: dict[str, object]
    fingerprint: str

    @property
    def lineage(self) -> tuple[str, str, str, str, str]:
        return (
            self.session_id, self.agent_id, self.task_id, self.attempt_id,
            self.transport,
        )

    def cursor(self) -> ObserverCursor:
        return ObserverCursor(
            session_id=self.session_id, agent_id=self.agent_id,
            task_id=self.task_id, attempt_id=self.attempt_id,
            transport=self.transport, sequence=self.sequence,
            event_id=self.event_id, fingerprint=self.fingerprint,
        )


def _typed_identity(value: object, prefix: str) -> str:
    if type(value) is not str:
        raise ValueError("invalid typed identity")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError("invalid typed identity") from None
    if (
        not value.startswith(prefix) or not value.removeprefix(prefix)
        or len(encoded) > 512 or any(character.isspace() for character in value)
    ):
        raise ValueError("invalid typed identity")
    return value


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("invalid text")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError("invalid text") from None
    if not value or len(encoded) > _MAX_TEXT_BYTES:
        raise ValueError("invalid text")
    return value


def _json_payload(value: object) -> dict[str, object]:
    budget = [_MAX_ITEMS]

    def visit(item: object, depth: int) -> object:
        if depth > _MAX_DEPTH:
            raise ValueError("payload depth")
        budget[0] -= 1
        if budget[0] < 0:
            raise ValueError("payload size")
        if item is None or type(item) in {bool, int, float}:
            if type(item) is int and not -(2**63) <= item <= _MAX_SEQUENCE:
                raise ValueError("payload integer")
            if type(item) is float and (
                item != item or item in {float("inf"), float("-inf")}
            ):
                raise ValueError("payload float")
            return item
        if type(item) is str:
            return _text(item)
        if type(item) in {tuple, list}:
            return [visit(child, depth + 1) for child in item]
        if isinstance(item, Mapping):
            entries = tuple(item.items())
            if len(entries) > budget[0]:
                raise ValueError("payload size")
            result: dict[str, object] = {}
            for key, child in entries:
                key = _text(key)
                if key in result:
                    raise ValueError("duplicate payload key")
                result[key] = visit(child, depth + 1)
            return result
        raise ValueError("unsupported payload value")

    normalized = visit(value, 0)
    if type(normalized) is not dict:
        raise ValueError("payload root")
    return normalized


def _snapshot_event(value: object) -> _EventSnapshot:
    try:
        fields = {field: getattr(value, field) for field in _EVENT_FIELDS}
        event_id = _typed_identity(fields["event_id"], "evt_")
        session_id = _typed_identity(fields["session_id"], "ses_")
        agent_id = _typed_identity(fields["agent_id"], "agt_")
        task_id = _typed_identity(fields["task_id"], "tsk_")
        attempt_id = _typed_identity(fields["attempt_id"], "att_")
        transport = _text(fields["transport"])
        sequence = fields["sequence"]
        if type(sequence) is not int or not 1 <= sequence <= _MAX_SEQUENCE:
            raise ValueError("invalid sequence")
        kind = fields["kind"]
        if type(kind) is not str or kind not in _EVENT_KINDS:
            raise ValueError("invalid kind")
        timestamp = _text(fields["timestamp"])
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("invalid timestamp")
        payload = _json_payload(fields["payload"])
        record = {
            "agent_id": agent_id, "attempt_id": attempt_id,
            "event_id": event_id, "kind": kind, "payload": payload,
            "sequence": sequence, "session_id": session_id,
            "task_id": task_id, "timestamp": timestamp, "transport": transport,
        }
        canonical = json.dumps(
            record, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return _EventSnapshot(
            event_id=event_id, session_id=session_id, agent_id=agent_id,
            task_id=task_id, attempt_id=attempt_id, transport=transport,
            sequence=sequence, kind=kind, timestamp=timestamp, payload=payload,
            fingerprint=fingerprint,
        )
    except ObserverError:
        raise
    except Exception:
        raise ObserverError("observer_malformed_event") from None


def _key_shape(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _redact_text(value: str) -> str:
    if _FORBIDDEN_CONTENT.search(value):
        return "[REDACTED]"
    redacted = _PRIVATE_KEY_BLOCK.sub("[REDACTED]", value)
    redacted = _SECRET_RELATION.sub("[REDACTED]", redacted)
    redacted = _CREDENTIAL_SHAPE.sub("[REDACTED]", redacted)
    return redacted


def _redact(value: object) -> object:
    if type(value) is str:
        return _redact_text(value)
    if type(value) is list:
        return [_redact(item) for item in value]
    if type(value) is dict:
        result: dict[str, object] = {}
        for key, child in value.items():
            shape = _key_shape(key)
            if any(marker in shape for marker in _FORBIDDEN_FIELD_SHAPES):
                continue
            if any(marker in shape for marker in _SENSITIVE_KEY_SHAPES):
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact(child)
        return result
    return value


def _render_snapshot(event: _EventSnapshot) -> str:
    record = {
        "agent_id": event.agent_id, "attempt_id": event.attempt_id,
        "event_id": event.event_id, "kind": event.kind,
        "payload": _redact(event.payload), "sequence": event.sequence,
        "session_id": event.session_id, "task_id": event.task_id,
        "timestamp": event.timestamp, "transport": event.transport,
    }
    body = json.dumps(
        record, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    )
    if event.kind == "message":
        return f"[Agent {event.agent_id}] {body}"
    return (
        f"[AgentDeck] observed {event.kind} from [Agent {event.agent_id}] {body}"
    )


def render_event(value: object) -> str:
    """Render one decoded event without granting it lifecycle authority."""

    event = _snapshot_event(value)
    if event.transport != "acp":
        raise ObserverError("observer_malformed_event")
    return _render_snapshot(event)


def render_system(message: str) -> str:
    """Label one bounded AgentDeck-generated observation status."""

    try:
        safe = _redact_text(_text(message)).replace("\r", "\\r").replace("\n", "\\n")
    except Exception:
        raise ObserverError("observer_malformed_event") from None
    return f"[AgentDeck] {safe}"


class ObserverStream:
    """Render one immutable event lineage and acknowledge accepted cursors."""

    def __init__(
        self, *, cursor_store: CursorStore, sink: ObservationSink | None = None,
    ) -> None:
        load = getattr(cursor_store, "load", None)
        acknowledge = getattr(cursor_store, "acknowledge", None)
        if not callable(load) or not callable(acknowledge):
            raise TypeError("cursor_store must provide load and acknowledge")
        if sink is not None and not callable(getattr(sink, "emit", None)):
            raise TypeError("sink must provide emit")
        self._cursor_store = cursor_store
        self._sink = sink
        try:
            cursor = load()
        except Exception:
            raise ObserverError("observer_cursor_load_failed") from None
        if cursor is not None and type(cursor) is not ObserverCursor:
            raise ObserverError("observer_cursor_invalid")
        self._cursor = cursor

    def render(self, events: Iterable[object]) -> tuple[str, ...]:
        try:
            iterator = iter(events)
        except Exception:
            raise ObserverError("observer_subscription_failed") from None
        output: list[str] = []
        while True:
            try:
                value = next(iterator)
            except StopIteration:
                return tuple(output)
            except Exception:
                raise ObserverError("observer_subscription_failed") from None
            event = _snapshot_event(value)
            if self._is_exact_duplicate(event):
                continue
            self._validate_next(event)
            record = _render_snapshot(event)
            if self._sink is not None:
                try:
                    self._sink.emit(record)
                except Exception:
                    raise ObserverError("observer_sink_failed") from None
            cursor = event.cursor()
            try:
                self._cursor_store.acknowledge(cursor)
            except Exception:
                raise ObserverError("observer_cursor_write_failed") from None
            self._cursor = cursor
            output.append(record)

    def _is_exact_duplicate(self, event: _EventSnapshot) -> bool:
        cursor = self._cursor
        return bool(
            cursor is not None
            and event.lineage == cursor.lineage
            and event.sequence == cursor.sequence
            and event.event_id == cursor.event_id
            and event.fingerprint == cursor.fingerprint
        )

    def _validate_next(self, event: _EventSnapshot) -> None:
        cursor = self._cursor
        if cursor is None:
            if event.transport != "acp":
                raise ObserverError("observer_malformed_event")
            if event.sequence != 1:
                raise ObserverError("observer_sequence_gap")
            return
        if event.lineage != cursor.lineage:
            raise ObserverError("observer_identity_mismatch")
        if event.event_id == cursor.event_id and event.sequence != cursor.sequence:
            raise ObserverError("observer_cursor_conflict")
        if event.sequence < cursor.sequence:
            raise ObserverError("observer_sequence_rollback")
        if event.sequence == cursor.sequence:
            raise ObserverError("observer_sequence_conflict")
        if event.sequence != cursor.sequence + 1:
            raise ObserverError("observer_sequence_gap")


__all__ = [
    "CursorStore", "ObservationSink", "ObserverCursor", "ObserverError",
    "ObserverStream", "render_event", "render_system",
]
