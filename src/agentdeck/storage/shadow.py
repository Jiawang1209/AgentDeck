"""Shadow mirror of the JSON state into SQLite (quarantine phase).

Boundary contract (route spec phase 2/3): the mirror is only ever written
from inside `StateStore._protocol_mutation_lock` (single writer inherited
from the same flock; no second lock file), JSON stays the sole authority,
and any shadow failure is logged to `.agentdeck/logs/shadow-errors.jsonl`
without ever breaking the JSON path. `rm state.db` is a complete rollback.

The lexical secrets firewall (`storage.secrets`) is intentionally NOT applied
to the mirror: it reproduces what state.json already stores, and state keys
like `handoff_token` are workflow correlation ids the blunt lexical policy
would false-positive on. The firewall guards future normalized canonical
facts, not the quarantine mirror.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agentdeck.storage.schema import SCHEMA_VERSION

AUTHORITY_QUARANTINED = "sqlite_installed_quarantined"

# Collections stored as one row per item, with the item's natural id.
_ID_FIELDS = {
    "messages": "message_id",
    "attempts": "attempt_id",
    "mission_attempts": "attempt_id",
    "jobs": "job_id",
    "replies": "reply_id",
    "artifacts": "artifact_id",
    "missions": "mission_id",
    "plans": "plan_id",
    "approvals": "approval_id",
    "chat_turns": "turn_id",
    "leader_actions": "action_id",
    "skill_loads": "load_id",
    "skill_suggestions": "suggestion_id",
    "memory_suggestions": "suggestion_id",
    "agent_sessions": "session_id",
    "conversation_sessions": "session_id",
    "permission_requests": "request_id",
    "delegations": "delegation_id",
}

# Collections whose JSON value is a dict keyed by record id.
_DICT_COLLECTIONS = {"agents"}


def shadow_database_path(root: Path | str) -> Path:
    return Path(root) / ".agentdeck" / "state" / "state.db"


def _shadow_error_log_path(root: Path | str) -> Path:
    return Path(root) / ".agentdeck" / "logs" / "shadow-errors.jsonl"


def _record_id(collection: str, item: object, position: int) -> str:
    if isinstance(item, dict):
        field = _ID_FIELDS.get(collection)
        if field:
            value = item.get(field)
            if isinstance(value, str) and value:
                return value
    return f"idx:{position:08d}"


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def open_shadow_connection(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database, timeout=5.0, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    # DELETE journal:不产生 -wal/-shm 副产物，避免 .agentdeck 目录复制不一致。
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def mirror_state(connection: sqlite3.Connection, state: dict[str, object]) -> None:
    """Full-state mirror inside one IMMEDIATE transaction (idempotent)."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("DELETE FROM records")
        connection.execute("DELETE FROM singletons")
        for collection, value in state.items():
            if collection in _DICT_COLLECTIONS and isinstance(value, dict):
                for position, (key, item) in enumerate(sorted(value.items())):
                    connection.execute(
                        "INSERT OR REPLACE INTO records(collection, record_id, position, record_json)"
                        " VALUES (?, ?, ?, ?)",
                        (collection, str(key), position, _dump(item)),
                    )
            elif isinstance(value, list):
                for position, item in enumerate(value):
                    connection.execute(
                        "INSERT OR REPLACE INTO records(collection, record_id, position, record_json)"
                        " VALUES (?, ?, ?, ?)",
                        (collection, _record_id(collection, item, position), position, _dump(item)),
                    )
            else:
                connection.execute(
                    "INSERT OR REPLACE INTO singletons(key, value_json) VALUES (?, ?)",
                    (collection, _dump(value)),
                )
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('mirrored_at', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def _shadow_enabled(connection: sqlite3.Connection) -> bool:
    meta = dict(connection.execute("SELECT key, value FROM meta"))
    return (
        meta.get("authority_state") == AUTHORITY_QUARANTINED
        and meta.get("schema_version") == SCHEMA_VERSION
    )


def mirror_if_enabled(root: Path | str, state: dict[str, object]) -> None:
    """Best-effort shadow mirror; never raises into the JSON write path."""
    database = shadow_database_path(root)
    try:
        if not database.is_file():
            return
        connection = open_shadow_connection(database)
        try:
            if not _shadow_enabled(connection):
                return
            mirror_state(connection, state)
        finally:
            connection.close()
    except Exception as error:  # noqa: BLE001 - shadow must never break authority
        try:
            log_path = _shadow_error_log_path(root)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    _dump(
                        {
                            "at": datetime.now(timezone.utc).isoformat(),
                            "error": f"{type(error).__name__}: {error}",
                        }
                    )
                    + "\n"
                )
        except OSError:
            pass
