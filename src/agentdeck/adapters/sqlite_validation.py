"""Bounded canonical persistence validation for the SQLite adapter."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import errno
import fcntl
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import sqlite3
from typing import Final

from agentdeck.adapters.sqlite_schema import (
    StoreCommandStateError,
    StoreSchemaError,
    StoreSerializationError,
    _require_safe_file,
)
from agentdeck.adapters.sqlite_secrets import _validate_fact_key_value
from agentdeck.kernel.events import (
    DomainEvent,
    FactArray,
    FactObject,
    normalize_occurred_at,
)
from agentdeck.kernel.execution import Attempt, AttemptState
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import CommandResult


_MAX_CANONICAL_BYTES: Final = 262_144
_MAX_STRING_BYTES: Final = 65_536
_MAX_DEPTH: Final = 32
_MAX_NODES: Final = 10_000
_SQLITE_MIN_INTEGER: Final = -(2**63)
_SQLITE_MAX_INTEGER: Final = 2**63 - 1
EVENT_ID_MAX_BYTES: Final = 255
EVENT_KIND_MAX_BYTES: Final = 128
AGGREGATE_TYPE_MAX_BYTES: Final = 128
AGGREGATE_ID_MAX_BYTES: Final = 255

_LOCK_NAME: Final = "writer.lock"
StateIdentity = tuple[int, int]


class StoreWriterBusyError(RuntimeError):
    """Raised when another foreground process owns the project writer."""


def _state_identity(state: Path) -> StateIdentity:
    try:
        info = state.lstat()
    except OSError as error:
        raise StoreSchemaError("state directory identity is unavailable") from error
    if not state.is_dir() or state.is_symlink():
        raise StoreSchemaError("state directory identity is invalid")
    return info.st_dev, info.st_ino


def _require_state_identity(state: Path, expected: StateIdentity) -> None:
    if _state_identity(state) != expected:
        raise StoreSchemaError("state directory identity changed during open or transaction")


def _ensure_lock_marker(state: Path) -> Path:
    path = state / _LOCK_NAME
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise ValueError("writer lock path must be a regular non-symlink file") from error
    try:
        info = os.fstat(descriptor)
        identity = _require_safe_file(path, "writer lock")
        if (info.st_dev, info.st_ino) != identity[:2] or info.st_mode & 0o077:
            raise ValueError("writer lock must be a protected regular sole-link file")
        return path
    finally:
        os.close(descriptor)


def _acquire_writer_lock(state: Path) -> tuple[int, Path, StateIdentity]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(state, flags)
    try:
        info = os.fstat(descriptor)
        identity = _state_identity(state)
        if (info.st_dev, info.st_ino) != identity:
            raise StoreSchemaError("state directory identity changed during lock acquisition")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise StoreWriterBusyError(
                    "another AgentDeck foreground writer owns this project"
                ) from error
            raise
        path = _ensure_lock_marker(state)
        _require_state_identity(state, identity)
        return descriptor, path, identity
    except BaseException:
        os.close(descriptor)
        raise


def _release_writer_lock(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _copy_json(value: object, *, depth: int, budget: list[int]) -> object:
    if depth > _MAX_DEPTH:
        raise StoreSerializationError("canonical facts exceed maximum depth")
    budget[0] += 1
    if budget[0] > _MAX_NODES:
        raise StoreSerializationError("canonical facts exceed maximum item count")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not _SQLITE_MIN_INTEGER <= value <= _SQLITE_MAX_INTEGER:
            raise StoreSerializationError("integer exceeds SQLite range")
        return value
    if type(value) is float:
        if not isfinite(value):
            raise StoreSerializationError("floats must be finite")
        return value
    if type(value) is str:
        _bounded_text(value, "canonical string", _MAX_STRING_BYTES, allow_blank=True)
        return value
    if type(value) in {list, tuple}:
        return [_copy_json(item, depth=depth + 1, budget=budget) for item in value]
    if isinstance(value, Mapping):
        copied: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise StoreSerializationError("canonical object keys must be strings")
            _bounded_text(key, "canonical object key", _MAX_STRING_BYTES, allow_blank=True)
            _validate_fact_key_value(key, item)
            copied[key] = _copy_json(item, depth=depth + 1, budget=budget)
        return copied
    raise StoreSerializationError("unsupported canonical fact type")


def _canonical(value: object, *, require_object: bool = True) -> tuple[str, CommandResult]:
    if require_object and not isinstance(value, Mapping):
        raise StoreSerializationError("command result must be an object")
    copied = _copy_json(value, depth=0, budget=[0])
    text = json.dumps(copied, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(text.encode("utf-8", "strict")) > _MAX_CANONICAL_BYTES:
        raise StoreSerializationError("canonical facts are too large")
    return text, json.loads(text)


def _decode_canonical(text: object) -> CommandResult:
    if type(text) is not str:
        raise StoreCommandStateError("completed command has no canonical result")
    try:
        value = json.loads(text)
        canonical, copied = _canonical(value)
    except (json.JSONDecodeError, StoreSerializationError) as error:
        raise StoreCommandStateError("completed command result is malformed") from error
    if canonical != text:
        raise StoreCommandStateError("completed command result is not canonical")
    return copied


def _bounded_text(
    value: object, label: str, maximum: int, *, allow_blank: bool = False
) -> str:
    if type(value) is not str:
        raise StoreSerializationError(f"{label} must be a string")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise StoreSerializationError(f"{label} must be strict UTF-8") from error
    if not allow_blank and not value.strip():
        raise StoreSerializationError(f"{label} must be nonempty")
    if len(encoded) > maximum:
        raise StoreSerializationError(f"{label} is too large")
    return value


def _timestamp(clock: Clock) -> str:
    value = clock.now()
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise StoreCommandStateError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat()


def _stored_timestamp(value: object, label: str) -> str:
    try:
        text = _bounded_text(value, label, 64)
        normalized = normalize_occurred_at(text)
    except (TypeError, ValueError) as error:
        raise StoreCommandStateError(f"{label} is not a valid timestamp") from error
    if normalized != text:
        raise StoreCommandStateError(f"{label} is not canonical UTC")
    return text


def _validate_command_row(row: tuple[object, ...]) -> CommandResult:
    kind, state, result, created_at, completed_at = row
    try:
        _bounded_text(kind, "stored command kind", 128)
        created = _stored_timestamp(created_at, "command created_at")
    except (StoreSerializationError, StoreCommandStateError) as error:
        raise StoreCommandStateError("stored command identity or timestamp is invalid") from error
    if state == "completed":
        if completed_at is None:
            raise StoreCommandStateError("completed command has no completed timestamp")
        completed = _stored_timestamp(completed_at, "command completed_at")
        if datetime.fromisoformat(completed) < datetime.fromisoformat(created):
            raise StoreCommandStateError("command completed before it was created")
        return _decode_canonical(result)
    if state == "started":
        if result is not None or completed_at is not None:
            raise StoreCommandStateError("started command row is incoherent")
        raise StoreCommandStateError("stored command is incomplete")
    raise StoreCommandStateError("stored command state is invalid")


def _thaw_fact(value: object) -> object:
    if type(value) is FactObject:
        return {key: _thaw_fact(item) for key, item in value.items}
    if type(value) is FactArray:
        return [_thaw_fact(item) for item in value.items]
    return value


def _event_record(
    event: DomainEvent | Mapping[str, object], command_id: str
) -> tuple[str, str, str, str, str, str]:
    if type(event) is DomainEvent:
        values: Mapping[str, object] = {
            "event_id": event.event_id, "kind": event.kind,
            "aggregate_type": event.aggregate_type, "aggregate_id": event.aggregate_id,
            "payload": {key: _thaw_fact(value) for key, value in event.payload},
            "occurred_at": event.occurred_at,
        }
    elif isinstance(event, Mapping):
        values = event
    else:
        raise StoreSerializationError("event must be a DomainEvent or object")
    allowed = {
        "event_id", "kind", "aggregate_type", "aggregate_id", "payload", "occurred_at",
    }
    if not set(values) <= allowed:
        raise StoreSerializationError("event contains unsupported fields")
    event_id = _bounded_text(values.get("event_id"), "event_id", EVENT_ID_MAX_BYTES)
    kind = _bounded_text(values.get("kind"), "event kind", EVENT_KIND_MAX_BYTES)
    aggregate_type = _bounded_text(
        values.get("aggregate_type", "command"), "aggregate_type", AGGREGATE_TYPE_MAX_BYTES
    )
    aggregate_id = _bounded_text(
        values.get("aggregate_id", command_id), "aggregate_id", AGGREGATE_ID_MAX_BYTES
    )
    payload_text, _ = _canonical(values.get("payload", {}))
    try:
        occurred_at = normalize_occurred_at(
            _bounded_text(values.get("occurred_at"), "event occurred_at", 64)
        )
    except (TypeError, ValueError) as error:
        raise StoreSerializationError("event occurred_at must be a valid timestamp") from error
    return event_id, kind, aggregate_type, aggregate_id, payload_text, occurred_at


_ATTEMPT_COLUMNS = (
    "attempt_id", "task_id", "agent_instance_id", "ordinal", "state", "reason",
    "result_summary", "retryable", "acp_session_id", "effect_observed",
    "created_at", "updated_at",
)

def _save_conversation_turn(
    connection: sqlite3.Connection, snapshot: Mapping[str, object], now: str
) -> None:
    _, facts = _canonical(snapshot)
    allowed = {
        "turn_id", "session_id", "actor_role", "sanitized_content", "occurred_at",
    }
    if set(facts) != allowed:
        raise StoreSerializationError("conversation turn fields are invalid")
    turn_id = _bounded_text(facts["turn_id"], "turn_id", 255)
    session_id = _bounded_text(facts["session_id"], "session_id", 255)
    actor_role = _bounded_text(facts["actor_role"], "actor_role", 64)
    if actor_role not in {"human", "leader", "system"}:
        raise StoreSerializationError("actor_role is unsupported")
    content = _bounded_text(
        facts["sanitized_content"], "sanitized_content", _MAX_STRING_BYTES
    )
    occurred_at = _stored_timestamp(facts.get("occurred_at", now), "turn occurred_at")
    ordinal = connection.execute(
        "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM conversation_turns WHERE session_id=?",
        (session_id,),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO conversation_turns VALUES (?, ?, ?, ?, ?, ?)",
        (turn_id, session_id, ordinal, actor_role, content, occurred_at),
    )


def _attempt_from_row(row: tuple[object, ...]) -> tuple[Attempt, dict[str, object]]:
    values = dict(zip(_ATTEMPT_COLUMNS, row, strict=True))
    if any(
        type(values[field]) is not int or values[field] not in {0, 1}
        for field in ("retryable", "effect_observed")
    ):
        raise StoreCommandStateError("stored attempt booleans are invalid")
    try:
        attempt = Attempt(
            values["attempt_id"], values["task_id"], values["ordinal"],
            AttemptState(values["state"]), reason=values["reason"],
            result_summary=values["result_summary"], retryable=bool(values["retryable"]),
        )
        _bounded_text(attempt.attempt_id, "attempt_id", 255)
        _bounded_text(attempt.task_id, "task_id", 255)
        agent = values["agent_instance_id"]
        if agent is not None:
            _bounded_text(agent, "agent_instance_id", 255)
            if not agent.startswith("agt_") or not agent[4:]:
                raise StoreSerializationError("agent_instance_id must be typed")
        if values["acp_session_id"] is not None:
            _bounded_text(values["acp_session_id"], "acp_session_id", 255)
        created = _stored_timestamp(values["created_at"], "attempt created_at")
        updated = _stored_timestamp(values["updated_at"], "attempt updated_at")
        if datetime.fromisoformat(updated) < datetime.fromisoformat(created):
            raise StoreCommandStateError("attempt timestamps are incoherent")
    except (TypeError, ValueError) as error:
        raise StoreCommandStateError("stored attempt is malformed") from error
    return attempt, values


def _attempt_fingerprint(values: Mapping[str, object]) -> str:
    serialized = json.dumps(
        [values[column] for column in _ATTEMPT_COLUMNS],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8", "strict")
    return sha256(serialized).hexdigest()


def _attempt_record(
    snapshot: Mapping[str, object], existing: tuple[object, ...] | None, now: str
) -> tuple[object, ...]:
    _, facts = _canonical(snapshot)
    allowed = set(_ATTEMPT_COLUMNS)
    if not set(facts) <= allowed:
        raise StoreSerializationError("attempt snapshot contains unsupported fields")
    if not {"attempt_id", "task_id", "ordinal", "state"} <= set(facts):
        raise StoreSerializationError("attempt snapshot is incomplete")
    old_attempt = None
    old: dict[str, object] = {}
    if existing is not None:
        old_attempt, old = _attempt_from_row(existing)
    retryable = facts.get(
        "retryable", bool(old["retryable"]) if old else False
    )
    effect_observed = facts.get(
        "effect_observed", bool(old["effect_observed"]) if old else False
    )
    if type(retryable) is not bool:
        raise TypeError("retryable must be a bool")
    if type(effect_observed) is not bool:
        raise TypeError("effect_observed must be a bool")
    try:
        state = AttemptState(facts["state"])
    except (TypeError, ValueError) as error:
        raise ValueError("state must be a supported AttemptState") from error
    attempt = Attempt(
        facts["attempt_id"], facts["task_id"], facts["ordinal"], state,
        reason=facts.get("reason"), result_summary=facts.get("result_summary"),
        retryable=retryable,
    )
    _bounded_text(attempt.attempt_id, "attempt_id", 255)
    _bounded_text(attempt.task_id, "task_id", 255)
    for value, label in ((attempt.reason, "reason"), (attempt.result_summary, "result_summary")):
        if value is not None:
            _bounded_text(value, label, _MAX_STRING_BYTES)
    agent = facts.get("agent_instance_id", old.get("agent_instance_id"))
    acp = facts.get("acp_session_id", old.get("acp_session_id"))
    for value, label in ((agent, "agent_instance_id"), (acp, "acp_session_id")):
        if value is not None:
            _bounded_text(value, label, 255)
    created_at = facts.get("created_at", old.get("created_at", now))
    updated_at = facts.get("updated_at", now)
    _stored_timestamp(created_at, "attempt created_at")
    _stored_timestamp(updated_at, "attempt updated_at")
    if old_attempt is not None:
        immutable = {
            "task_id": attempt.task_id, "agent_instance_id": agent,
            "ordinal": attempt.ordinal, "created_at": created_at,
        }
        if any(immutable[key] != old[key] for key in immutable):
            raise ValueError("attempt immutable lineage cannot drift")
        old_acp = old["acp_session_id"]
        if old_acp is None and acp is not None:
            if (
                old_attempt.state is not AttemptState.RUNNING
                or attempt.state is not AttemptState.RUNNING
            ):
                raise ValueError("ACP session may bind only while Attempt is running")
        elif old_acp != acp:
            raise ValueError("immutable ACP session lineage cannot be removed or drifted")
        if bool(old["effect_observed"]) and not effect_observed:
            raise ValueError("effect_observed is monotonic")
        if datetime.fromisoformat(updated_at) < datetime.fromisoformat(old["updated_at"]):
            raise ValueError("attempt updated_at is monotonic")
        if attempt.state == old_attempt.state:
            if attempt != old_attempt:
                raise ValueError("attempt state facts cannot be rewritten")
        else:
            transitioned = old_attempt.transition(
                attempt.state, reason=attempt.reason,
                result_summary=attempt.result_summary, retryable=attempt.retryable,
            )
            if transitioned != attempt:
                raise ValueError("attempt transition facts are inconsistent")
    if datetime.fromisoformat(updated_at) < datetime.fromisoformat(created_at):
        raise ValueError("attempt updated_at precedes created_at")
    return (
        attempt.attempt_id, attempt.task_id, agent, attempt.ordinal, attempt.state.value,
        attempt.reason, attempt.result_summary, int(attempt.retryable), acp,
        int(effect_observed), created_at, updated_at,
    )
