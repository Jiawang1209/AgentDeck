"""Stable ACP-only Worker Port and redacted event values."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Final, Protocol, TypeAlias, cast

from agentdeck.kernel.events import normalize_occurred_at


WORKER_EVENT_KINDS: Final = frozenset(
    "started progress tool_started tool_completed permission_requested "
    "artifact_changed message completed failed cancelled".split()
)
WORKER_RESULT_STATUSES: Final = frozenset({"completed", "failed", "cancelled"})
_SENSITIVE_WORDS = frozenset(
    "authorization credential credentials environment password secret".split()
)
_SENSITIVE_KEY_FAMILIES = (
    "apikey", "apitoken", "authtoken", "accesstoken", "sessiontoken",
    "refreshtoken", "idtoken", "bearertoken", "rawframe", "privatekey",
)
_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)
_ASSIGNMENT = re.compile(
    r'''(?<![A-Za-z0-9])(["']?)([A-Za-z][A-Za-z0-9_.-]{0,127})\1[ \t]*[:=][ \t]*\S'''
)
_BEARER = re.compile(r"(?<![A-Za-z0-9])bearer[ \t]+\S", re.IGNORECASE)
_MAX_TEXT_BYTES = 64 * 1024
_MAX_PAYLOAD_ITEMS = 256
_MAX_PAYLOAD_DEPTH = 8
_MAX_SEQUENCE = 2**63 - 1
_MIN_SQLITE_INTEGER = -(2**63)

PayloadScalar: TypeAlias = str | int | float | bool | None
PayloadValue: TypeAlias = PayloadScalar | tuple["PayloadValue", ...] | Mapping[str, "PayloadValue"]
RedactedPayload: TypeAlias = Mapping[str, PayloadValue]


def _text(value: object, field: str, *, prefix: str | None = None) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError(f"{field} must be valid UTF-8") from None
    if not value.strip() or len(encoded) > _MAX_TEXT_BYTES:
        raise ValueError(f"{field} must be nonempty and bounded")
    if prefix is not None and (
        not value.startswith(prefix)
        or not value.removeprefix(prefix)
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field} must be a typed {prefix} identity")
    return value


def _key_shape(value: str) -> tuple[tuple[str, ...], str]:
    words = tuple(re.findall(r"[a-z0-9]+", _CAMEL_BOUNDARY.sub("_", value).lower()))
    return words, "".join(words)


def _is_sensitive_key(value: str) -> bool:
    words, compact = _key_shape(value)
    return bool(
        _SENSITIVE_WORDS.intersection(words)
        or compact == "token"
        or any(family in compact for family in _SENSITIVE_KEY_FAMILIES)
    )


def _contains_sensitive_value(value: str) -> bool:
    return (
        any(_is_sensitive_key(match.group(2)) for match in _ASSIGNMENT.finditer(value))
        or _BEARER.search(value) is not None
        or ("-----BEGIN " in value.upper() and "PRIVATE KEY-----" in value.upper())
    )


def validate_worker_reason(value: object) -> str:
    text = _text(value, "reason")
    if _contains_sensitive_value(text):
        raise ValueError("reason contains sensitive content")
    return text


def _is_observability_metric(key: str, value: object) -> bool:
    words, _ = _key_shape(key)
    return type(value) in {bool, int, float} and (
        words[-1:] in {("count",), ("bytes",), ("total",)}
        or words[-2:] in {("latency", "ms"), ("duration", "ms")}
    )


def _freeze_payload(value: object) -> RedactedPayload:
    budget = [_MAX_PAYLOAD_ITEMS]

    def snapshot(item: object) -> tuple[tuple[object, object], ...]:
        if type(item) is dict:
            if len(item) > budget[0]:
                raise ValueError("payload exceeds maximum items")
            return tuple(dict.items(item))
        if type(item) is MappingProxyType:
            entries = []
            try:
                iterator = iter(item)
                for _ in range(budget[0] + 1):
                    try:
                        key = next(iterator)
                    except StopIteration:
                        return tuple(entries)
                    entries.append((key, item[key]))
            except Exception:
                raise ValueError("payload mapping snapshot invalid") from None
            raise ValueError("payload exceeds maximum items")
        raise ValueError("payload must use built-in JSON containers")

    def freeze(item: object, depth: int) -> PayloadValue:
        if depth > _MAX_PAYLOAD_DEPTH:
            raise ValueError("payload exceeds maximum depth")
        budget[0] -= 1
        if budget[0] < 0:
            raise ValueError("payload exceeds maximum items")
        if item is None or type(item) is bool:
            return cast(PayloadScalar, item)
        if type(item) is int:
            if not _MIN_SQLITE_INTEGER <= item <= _MAX_SEQUENCE:
                raise ValueError("payload integer is outside SQLite range")
            return item
        if type(item) is float:
            if item != item or item in {float("inf"), float("-inf")}:
                raise ValueError("payload floats must be finite")
            return item
        if type(item) is str:
            text = _text(item, "payload text")
            if _contains_sensitive_value(text):
                raise ValueError("payload contains sensitive content")
            return text
        if type(item) in {list, tuple}:
            return tuple(freeze(child, depth + 1) for child in item)
        if type(item) in {dict, MappingProxyType}:
            copied = snapshot(item)
            result: dict[str, PayloadValue] = {}
            for key, child in copied:
                key = _text(key, "payload key")
                if key in result:
                    raise ValueError("payload contains duplicate key")
                if _is_sensitive_key(key) and not _is_observability_metric(key, child):
                    raise ValueError("payload contains sensitive content")
                result[key] = freeze(child, depth + 1)
            return MappingProxyType(result)
        if isinstance(item, Mapping):
            raise ValueError("payload must use built-in JSON containers")
        raise TypeError("payload contains unsupported value")

    if type(value) not in {dict, MappingProxyType}:
        raise ValueError("payload must use built-in JSON containers")
    frozen = freeze(value, 0)
    return cast(RedactedPayload, frozen)


@dataclass(frozen=True)
class TaskRequest:
    agent_id: str
    task_id: str
    attempt_id: str
    instruction: str

    def __post_init__(self) -> None:
        _text(self.agent_id, "agent_id", prefix="agt_")
        _text(self.task_id, "task_id", prefix="tsk_")
        _text(self.attempt_id, "attempt_id", prefix="att_")
        _text(self.instruction, "instruction")


@dataclass(frozen=True)
class WorkerHandle:
    session_id: str
    agent_id: str
    task_id: str
    attempt_id: str
    transport: str = "acp"

    def __post_init__(self) -> None:
        _text(self.session_id, "session_id", prefix="ses_")
        _text(self.agent_id, "agent_id", prefix="agt_")
        _text(self.task_id, "task_id", prefix="tsk_")
        _text(self.attempt_id, "attempt_id", prefix="att_")
        if self.transport != "acp":
            raise ValueError("transport must be acp")


@dataclass(frozen=True)
class WorkerEvent:
    event_id: str
    session_id: str
    agent_id: str
    task_id: str
    attempt_id: str
    transport: str
    sequence: int
    kind: str
    timestamp: str
    payload: RedactedPayload

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id", prefix="evt_")
        _text(self.session_id, "session_id", prefix="ses_")
        _text(self.agent_id, "agent_id", prefix="agt_")
        _text(self.task_id, "task_id", prefix="tsk_")
        _text(self.attempt_id, "attempt_id", prefix="att_")
        if self.transport != "acp":
            raise ValueError("transport must be acp")
        if type(self.sequence) is not int or not 1 <= self.sequence <= _MAX_SEQUENCE:
            raise ValueError("sequence must be a positive SQLite-safe integer")
        if type(self.kind) is not str or self.kind not in WORKER_EVENT_KINDS:
            raise ValueError("kind must be a stable Worker Event kind")
        try:
            normalized_timestamp = normalize_occurred_at(self.timestamp)
        except (TypeError, ValueError):
            raise ValueError("timestamp must be a timezone-aware ISO datetime") from None
        object.__setattr__(self, "timestamp", normalized_timestamp)
        object.__setattr__(self, "payload", _freeze_payload(self.payload))


@dataclass(frozen=True)
class WorkerResult:
    session_id: str
    agent_id: str
    task_id: str
    attempt_id: str
    status: str
    payload: RedactedPayload

    def __post_init__(self) -> None:
        _text(self.session_id, "session_id", prefix="ses_")
        _text(self.agent_id, "agent_id", prefix="agt_")
        _text(self.task_id, "task_id", prefix="tsk_")
        _text(self.attempt_id, "attempt_id", prefix="att_")
        if type(self.status) is not str or self.status not in WORKER_RESULT_STATUSES:
            raise ValueError("status must be a terminal Worker result")
        object.__setattr__(self, "payload", _freeze_payload(self.payload))


class Worker(Protocol):
    async def start_task(self, request: TaskRequest) -> WorkerHandle: ...
    def stream_events(self, handle: WorkerHandle) -> AsyncIterator[WorkerEvent]: ...
    async def respond_permission(
        self,
        handle: WorkerHandle,
        *,
        permission_request_id: str,
        allowed: bool,
        reason: str,
    ) -> None: ...
    async def cancel_task(self, handle: WorkerHandle, *, reason: str) -> None: ...
    async def collect_result(self, handle: WorkerHandle) -> WorkerResult: ...


__all__ = [
    "RedactedPayload", "TaskRequest", "Worker", "WorkerEvent", "WorkerHandle",
    "WorkerResult", "WORKER_EVENT_KINDS", "WORKER_RESULT_STATUSES",
    "validate_worker_reason",
]
