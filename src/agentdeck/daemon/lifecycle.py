from __future__ import annotations

from datetime import datetime
import hashlib
import re
from typing import Mapping


DAEMON_STATES = frozenset(
    {
        "starting",
        "ready",
        "busy",
        "idle_grace",
        "stopping",
        "stopped",
        "blocked",
    }
)

_DAEMON_RECORD_FIELDS = {
    "instance_id",
    "project_root_hash",
    "start_nonce_hash",
    "state",
    "created_at",
    "updated_at",
}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _required_string(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"daemon {field} must be a non-empty string")
    return value


def _aware_timestamp(value: object, field: str) -> datetime:
    timestamp = _required_string(value, field)
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError(f"daemon {field} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"daemon {field} must be a timezone-aware timestamp")
    return parsed


def validate_daemon_record(record: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(record, Mapping):
        raise TypeError("daemon record must be a mapping")
    if set(record) != _DAEMON_RECORD_FIELDS:
        raise ValueError("daemon record fields are invalid")

    _required_string(record["instance_id"], "instance_id")
    _required_string(record["project_root_hash"], "project_root_hash")
    nonce_hash = _required_string(record["start_nonce_hash"], "start_nonce_hash")
    if _SHA256_PATTERN.fullmatch(nonce_hash) is None:
        raise ValueError("daemon start_nonce_hash must be a lowercase sha256 hash")
    state = record["state"]
    if type(state) is not str or state not in DAEMON_STATES:
        raise ValueError("daemon state is invalid")
    created_at = _aware_timestamp(record["created_at"], "created_at")
    updated_at = _aware_timestamp(record["updated_at"], "updated_at")
    if updated_at < created_at:
        raise ValueError("daemon updated_at must not be earlier than created_at")
    return dict(record)


def build_daemon_record(
    *,
    instance_id: str,
    project_root_hash: str,
    start_nonce: str,
    state: str,
    created_at: str,
) -> dict[str, object]:
    nonce = _required_string(start_nonce, "start_nonce")
    record: dict[str, object] = {
        "instance_id": instance_id,
        "project_root_hash": project_root_hash,
        "start_nonce_hash": hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
        "state": state,
        "created_at": created_at,
        "updated_at": created_at,
    }
    return validate_daemon_record(record)
