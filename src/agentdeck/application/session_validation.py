"""Content-free validation helpers for ProductSession application inputs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import re
from types import MappingProxyType
from typing import Final

from agentdeck.kernel.events import normalize_occurred_at
from agentdeck.ports.store import CommandResult, STORE_COMMAND_ID_MAX_BYTES


_MAX_GOAL_BYTES: Final = 65_536
_MAX_SELECTION_BYTES: Final = 4_096
_MAX_LEADERS: Final = 256
_MAPPING_PROXY_TYPE: Final = type(MappingProxyType({}))
_ASSIGNMENT_KEY: Final = re.compile(
    r"(?<![A-Za-z0-9_])[\"']?(?P<key>[A-Za-z][A-Za-z0-9_-]*"
    r"(?:[ \t]+[A-Za-z][A-Za-z0-9_-]*)?)[\"']?\s*[:=]\s*(?=\S)"
)
_SENSITIVE_KEY_SUFFIXES: Final = (
    "apikey", "authorization", "cookie", "credential", "credentials",
    "password", "passphrase", "privatekey", "secret", "sshkey", "token",
)
_BEARER_VALUE: Final = re.compile(r"(?i)\bbearer\s+\S+")
_PRIVATE_KEY: Final = re.compile(r"(?i)-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")
_SSH_PUBLIC_KEY: Final = re.compile(
    r"(?<![A-Za-z0-9_-])ssh-ed25519[ \t]+"
    r"[A-Za-z0-9+/]{32,}={0,3}(?![A-Za-z0-9+/=])"
)
_BARE_CREDENTIAL: Final = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-(?:proj|ant)-[A-Za-z0-9_-]{16,}"
    r"|sk-[A-Za-z0-9]{48}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|AIza[A-Za-z0-9_-]{20,}"
    r")(?![A-Za-z0-9])"
)


class SessionServiceError(RuntimeError):
    """Raised when ProductSession application facts fail closed."""


def bounded_text(value: object, label: str, maximum: int) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    if not value.strip():
        raise ValueError(f"{label} must not be empty")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError(f"{label} must be bounded UTF-8") from None
    if len(encoded) > maximum:
        raise ValueError(f"{label} must be bounded UTF-8")
    return value


def validate_goal(value: object) -> str:
    goal = bounded_text(value, "goal", _MAX_GOAL_BYTES)
    assignments = (
        re.sub(r"[^A-Za-z0-9]", "", match.group("key")).lower()
        for match in _ASSIGNMENT_KEY.finditer(goal)
    )
    if (
        any(key.endswith(_SENSITIVE_KEY_SUFFIXES) for key in assignments)
        or _BEARER_VALUE.search(goal)
        or _PRIVATE_KEY.search(goal)
        or _SSH_PUBLIC_KEY.search(goal)
        or _BARE_CREDENTIAL.search(goal)
    ):
        raise ValueError("goal contains prohibited credential material")
    return goal


def validate_command_id(value: object) -> str:
    return bounded_text(value, "command_id", STORE_COMMAND_ID_MAX_BYTES)


def canonical_project_root(value: object) -> str:
    raw = bounded_text(value, "project_root", _MAX_GOAL_BYTES)
    try:
        resolved = Path(raw).resolve(strict=True)
        if not resolved.is_dir():
            raise OSError
    except (OSError, RuntimeError, ValueError):
        raise SessionServiceError("project root is unavailable") from None
    return bounded_text(str(resolved), "project_root", _MAX_GOAL_BYTES)


def project_root_digest(project_root: str) -> str:
    return sha256(project_root.encode("utf-8", "strict")).hexdigest()


def canonical_clock_time(value: object) -> str:
    canonical: str | None = None
    if type(value) is datetime:
        try:
            if value.tzinfo is not None and value.utcoffset() is not None:
                canonical = normalize_occurred_at(value.isoformat())
        except BaseException:
            pass
    if canonical is None:
        raise SessionServiceError("clock must return a timezone-aware datetime")
    return canonical


def snapshot_available_leaders(
    available: Mapping[str, tuple[str, ...]],
) -> Mapping[str, tuple[str, ...]]:
    if type(available) not in {dict, _MAPPING_PROXY_TYPE}:
        raise TypeError("available_leaders must be a safe snapshot")
    items: list[tuple[object, object]] = []
    try:
        iterator = iter(available.items())
        for _ in range(_MAX_LEADERS + 1):
            try:
                items.append(next(iterator))
            except StopIteration:
                break
    except BaseException:
        raise TypeError("available_leaders must be a safe snapshot") from None
    if len(items) > _MAX_LEADERS:
        raise ValueError("available_leaders is too large")
    copied: dict[str, tuple[str, ...]] = {}
    for leader, models in items:
        checked_leader = bounded_text(leader, "leader", _MAX_SELECTION_BYTES)
        if type(models) is not tuple or not models or len(models) > _MAX_LEADERS:
            raise TypeError("Leader models must be a bounded nonempty tuple")
        if checked_leader in copied:
            raise ValueError("available_leaders contains duplicate names")
        copied[checked_leader] = tuple(
            bounded_text(model, "model", _MAX_SELECTION_BYTES) for model in models
        )
    return MappingProxyType(copied)


def validate_creation_result(
    result: CommandResult | None, session_id: str, root_digest: str
) -> None:
    if result is None or set(result) != {
        "project_root_digest", "session_id", "state",
    }:
        raise SessionServiceError("durable ProductSession creation result is malformed")
    if result["session_id"] != session_id or result["state"] != "setup":
        raise SessionServiceError("durable ProductSession creation lineage is invalid")
    if result["project_root_digest"] != root_digest:
        raise SessionServiceError("ProductSession project root does not match")


def validate_accept_result(
    result: CommandResult, *, session_id: str, goal: str, turn_id: str
) -> tuple[int, str]:
    if set(result) != {
        "accepted", "goal", "mode", "session_id", "turn_id", "turn_occurred_at",
        "turn_ordinal",
    }:
        raise SessionServiceError("stored accept result is malformed")
    if result["session_id"] != session_id:
        raise SessionServiceError("stored accept result lineage is invalid")
    if (
        result["accepted"] is not True
        or result["mode"] != "setup_required"
        or result["goal"] != goal
        or result["turn_id"] != turn_id
        or type(result["turn_ordinal"]) is not int
        or result["turn_ordinal"] <= 0
        or type(result["turn_occurred_at"]) is not str
    ):
        raise SessionServiceError("stored accept result is malformed")
    try:
        occurred_at = normalize_occurred_at(result["turn_occurred_at"])
    except (TypeError, ValueError):
        raise SessionServiceError("stored accept result is malformed") from None
    if occurred_at != result["turn_occurred_at"]:
        raise SessionServiceError("stored accept result is malformed")
    return result["turn_ordinal"], occurred_at


def validate_durable_turn(
    turn: CommandResult | None, *, session_id: str, goal: str, turn_id: str,
    ordinal: int, occurred_at: str,
) -> None:
    if turn is None or set(turn) != {
        "actor_role", "occurred_at", "ordinal", "sanitized_content", "session_id",
        "turn_id",
    }:
        raise SessionServiceError("durable turn is missing or malformed")
    stored_time = turn["occurred_at"]
    if type(stored_time) is not str:
        raise SessionServiceError("durable turn is missing or malformed")
    try:
        canonical_time = normalize_occurred_at(stored_time)
    except (TypeError, ValueError):
        raise SessionServiceError("durable turn is missing or malformed") from None
    if (
        turn["turn_id"] != turn_id
        or turn["session_id"] != session_id
        or turn["actor_role"] != "human"
        or turn["sanitized_content"] != goal
        or turn["ordinal"] != ordinal
        or canonical_time != occurred_at
        or canonical_time != stored_time
    ):
        raise SessionServiceError("durable turn is missing or malformed")
