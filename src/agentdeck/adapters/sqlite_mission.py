"""Canonical command-bound SQLite persistence for Mission authority."""

from __future__ import annotations

from collections.abc import Mapping
import json
import sqlite3

from agentdeck.kernel.events import normalize_occurred_at
from agentdeck.kernel.mission import ConfirmedMissionVersion, MissionPreview


MISSION_AGGREGATE_TYPES = frozenset(
    {"mission_previews", "current_mission_preview", "confirmed_missions"}
)
_FIELDS = frozenset(
    {
        "mission_id", "session_id", "state", "current_version", "preview_id",
        "version", "content_hash", "canonical_content", "confirmed_at",
    }
)


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError(f"{field} must be strict UTF-8") from None
    return value


def _snapshot(value: object, *, confirmed: bool) -> dict[str, object]:
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError("Mission snapshot must have exact fields")
    copied = dict(value)
    mission_id = _text(copied["mission_id"], "mission_id")
    session_id = _text(copied["session_id"], "session_id")
    preview_id = _text(copied["preview_id"], "preview_id")
    content_hash = _text(copied["content_hash"], "content_hash")
    canonical = _text(copied["canonical_content"], "canonical_content")
    version = copied["version"]
    if type(version) is not int or version < 1 or copied["current_version"] != version:
        raise ValueError("Mission version must be one positive exact value")
    preview = MissionPreview(preview_id, version, content_hash, canonical)
    expected_id = f"msn_{preview.content_hash[:24]}"
    if mission_id != expected_id or not session_id.startswith("ses_"):
        raise ValueError("Mission snapshot identity is invalid")
    confirmed_at = copied["confirmed_at"]
    if confirmed:
        if copied["state"] != "confirmed" or type(confirmed_at) is not str:
            raise ValueError("confirmed Mission requires an exact confirmation time")
        copied["confirmed_at"] = normalize_occurred_at(confirmed_at)
        ConfirmedMissionVersion(mission_id, version, content_hash, canonical)
    elif copied["state"] != "awaiting_confirmation" or confirmed_at is not None:
        raise ValueError("Mission Preview cannot be confirmed")
    return copied


def save_mission_aggregate(
    connection: sqlite3.Connection,
    aggregate_type: str,
    aggregate_id: str,
    snapshot: Mapping[str, object],
    now: str,
) -> None:
    """Save one exact Preview or confirmation inside its active command."""

    if aggregate_type not in {"mission_previews", "confirmed_missions"}:
        raise ValueError("Mission aggregate is read-only or unsupported")
    confirmed = aggregate_type == "confirmed_missions"
    facts = _snapshot(dict(snapshot), confirmed=confirmed)
    expected_id = facts["mission_id"] if confirmed else facts["preview_id"]
    if aggregate_id != expected_id:
        raise ValueError("aggregate identity does not match Mission snapshot")
    if confirmed:
        _confirm(connection, facts)
    else:
        _save_preview(connection, facts, normalize_occurred_at(now))


def _save_preview(
    connection: sqlite3.Connection, facts: dict[str, object], now: str
) -> None:
    if connection.execute(
        "SELECT 1 FROM product_sessions WHERE session_id=?", (facts["session_id"],)
    ).fetchone() is None:
        raise ValueError("Mission Preview requires a durable ProductSession")
    connection.execute(
        "INSERT INTO missions VALUES (?,?,?,?,?,?)",
        (
            facts["mission_id"], facts["session_id"], facts["state"],
            facts["version"], now, now,
        ),
    )
    connection.execute(
        "INSERT INTO mission_versions VALUES (?,?,?,?,?,?)",
        (
            facts["mission_id"], facts["version"], facts["preview_id"],
            facts["content_hash"], facts["canonical_content"], None,
        ),
    )


def _confirm(connection: sqlite3.Connection, facts: dict[str, object]) -> None:
    row = connection.execute(
        """SELECT m.session_id,m.state,m.current_version,v.preview_id,v.content_hash,
                  v.canonical_mission_facts,v.confirmed_at
             FROM missions m JOIN mission_versions v ON v.mission_id=m.mission_id
              AND v.version=m.current_version WHERE m.mission_id=?""",
        (facts["mission_id"],),
    ).fetchone()
    expected = (
        facts["session_id"], "awaiting_confirmation", facts["version"],
        facts["preview_id"], facts["content_hash"], facts["canonical_content"], None,
    )
    if row != expected:
        raise ValueError("confirmed Mission does not match its durable Preview")
    connection.execute(
        "UPDATE missions SET state='confirmed',updated_at=? WHERE mission_id=?",
        (facts["confirmed_at"], facts["mission_id"]),
    )
    connection.execute(
        "UPDATE mission_versions SET confirmed_at=? WHERE mission_id=? AND version=?",
        (facts["confirmed_at"], facts["mission_id"], facts["version"]),
    )
    payload = json.loads(str(facts["canonical_content"]))
    for ordinal, task in enumerate(payload["tasks"], 1):
        canonical_task = json.dumps(
            task, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task["task_id"], facts["mission_id"], facts["version"], ordinal,
                task["name"], task["role"], task["backend"],
                task["agent_instance_id"], task["acp_route"], "pending",
                canonical_task, facts["confirmed_at"], facts["confirmed_at"],
            ),
        )


def load_mission_aggregate(
    connection: sqlite3.Connection, aggregate_type: str, aggregate_id: str
) -> dict[str, object] | None:
    """Load one validated exact Mission projection without hidden mutation."""

    _text(aggregate_id, "aggregate_id")
    if aggregate_type == "mission_previews":
        where, parameters = "v.preview_id=?", (aggregate_id,)
    elif aggregate_type == "current_mission_preview":
        where, parameters = "m.session_id=?", (aggregate_id,)
    elif aggregate_type == "confirmed_missions":
        where, parameters = "m.mission_id=? AND m.state='confirmed'", (aggregate_id,)
    else:
        raise ValueError("unsupported Mission aggregate type")
    row = connection.execute(
        f"""SELECT m.mission_id,m.session_id,m.state,m.current_version,
                   v.preview_id,v.version,v.content_hash,v.canonical_mission_facts,
                   v.confirmed_at
              FROM missions m JOIN mission_versions v ON v.mission_id=m.mission_id
               AND v.version=m.current_version WHERE {where}
              ORDER BY v.version DESC LIMIT 1""",
        parameters,
    ).fetchone()
    if row is None:
        return None
    facts = dict(zip(_FIELDS_IN_ROW_ORDER, row, strict=True))
    return _snapshot(facts, confirmed=facts["state"] == "confirmed")


_FIELDS_IN_ROW_ORDER = (
    "mission_id", "session_id", "state", "current_version", "preview_id",
    "version", "content_hash", "canonical_content", "confirmed_at",
)


__all__ = ["MISSION_AGGREGATE_TYPES", "load_mission_aggregate", "save_mission_aggregate"]
