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
        # 集合清单入 meta：空 list/dict 集合没有 records 行，重建时必须
        # 靠清单恢复出空容器，否则空集合的 key 会整个丢失。
        manifest = {
            "lists": sorted(
                key
                for key, value in state.items()
                if isinstance(value, list)
            ),
            "dicts": sorted(
                key
                for key, value in state.items()
                if key in _DICT_COLLECTIONS and isinstance(value, dict)
            ),
        }
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('collections', ?)",
            (_dump(manifest),),
        )
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


def reconstruct_state(connection: sqlite3.Connection) -> dict[str, object]:
    """Inverse of `mirror_state`: rebuild the state dict from the mirror.

    List collections come back in position order; dict collections
    (`_DICT_COLLECTIONS`) come back keyed by record_id; singletons verbatim.
    """
    state: dict[str, object] = {}
    manifest_row = connection.execute(
        "SELECT value FROM meta WHERE key = 'collections'"
    ).fetchone()
    if manifest_row is not None:
        manifest = json.loads(manifest_row[0])
        for key in manifest.get("lists", []):
            state[key] = []
        for key in manifest.get("dicts", []):
            state[key] = {}
    for collection, record_id, record_json in connection.execute(
        "SELECT collection, record_id, record_json FROM records"
        " ORDER BY collection, position"
    ):
        item = json.loads(record_json)
        if collection in _DICT_COLLECTIONS:
            bucket = state.setdefault(collection, {})
            assert isinstance(bucket, dict)
            bucket[record_id] = item
        else:
            bucket = state.setdefault(collection, [])
            assert isinstance(bucket, list)
            bucket.append(item)
    for key, value_json in connection.execute("SELECT key, value_json FROM singletons"):
        state[key] = json.loads(value_json)
    return state


def _shadow_enabled(connection: sqlite3.Connection) -> bool:
    meta = dict(connection.execute("SELECT key, value FROM meta"))
    return (
        meta.get("authority_state") == AUTHORITY_QUARANTINED
        and meta.get("schema_version") == SCHEMA_VERSION
    )


def _log_shadow_error(root: Path | str, error: Exception) -> None:
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


EVENTS_AUTHORITY_JOURNAL = "journal"
EVENTS_AUTHORITY_SQLITE = "sqlite"


def log_shadow_error(root: Path | str, error: Exception) -> None:
    """Public alias for export-path failures once the table is authoritative."""
    _log_shadow_error(root, error)


def events_authority(root: Path | str) -> str:
    """Current events authority: 'sqlite' only after an explicit cutover.

    Fail-safe: any read problem degrades to 'journal' — the synchronous
    export keeps events.jsonl complete, so no data is lost by degrading.
    """
    database = shadow_database_path(root)
    try:
        if not database.is_file():
            return EVENTS_AUTHORITY_JOURNAL
        connection = open_shadow_connection(database)
        try:
            if not _shadow_enabled(connection):
                return EVENTS_AUTHORITY_JOURNAL
            meta = dict(connection.execute("SELECT key, value FROM meta"))
            if meta.get("events_authority") == EVENTS_AUTHORITY_SQLITE:
                return EVENTS_AUTHORITY_SQLITE
            return EVENTS_AUTHORITY_JOURNAL
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 - degrade to the journal, never crash reads
        return EVENTS_AUTHORITY_JOURNAL


def set_events_authority(connection: sqlite3.Connection, value: str) -> None:
    if value not in {EVENTS_AUTHORITY_JOURNAL, EVENTS_AUTHORITY_SQLITE}:
        raise ValueError("unknown events authority")
    connection.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('events_authority', ?)",
        (value,),
    )


EVENTS_EXPORT_SYNC = "sync"
EVENTS_EXPORT_ON_DEMAND = "on_demand"


def events_export_mode(root: Path | str) -> str:
    """SQLite 阶段 5d：events.jsonl 导出模式（仅 authority=sqlite 时有意义）。

    Fail-safe: any read problem degrades to 'sync' — keep exporting rather
    than ever silently stopping the journal mirror.
    """
    database = shadow_database_path(root)
    try:
        if not database.is_file():
            return EVENTS_EXPORT_SYNC
        connection = open_shadow_connection(database)
        try:
            if not _shadow_enabled(connection):
                return EVENTS_EXPORT_SYNC
            meta = dict(connection.execute("SELECT key, value FROM meta"))
            if meta.get("events_export_mode") == EVENTS_EXPORT_ON_DEMAND:
                return EVENTS_EXPORT_ON_DEMAND
            return EVENTS_EXPORT_SYNC
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 - degrade to sync, never crash the write path
        return EVENTS_EXPORT_SYNC


def set_events_export_mode(connection: sqlite3.Connection, value: str) -> None:
    if value not in {EVENTS_EXPORT_SYNC, EVENTS_EXPORT_ON_DEMAND}:
        raise ValueError("unknown events export mode")
    connection.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('events_export_mode', ?)",
        (value,),
    )


def insert_event_lines(connection: sqlite3.Connection, encoded: bytes) -> int:
    """Parse encoded journal lines and INSERT OR IGNORE them. Returns inserted count."""
    inserted = 0
    for line in encoded.decode("utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        event = json.loads(stripped)
        cursor = connection.execute(
            "INSERT OR IGNORE INTO events"
            " (event_id, event_type, payload_json, created_at)"
            " VALUES (?, ?, ?, ?)",
            (
                str(event.get("event_id")),
                str(event.get("event_type")),
                _dump(event.get("payload")),
                str(event.get("created_at")),
            ),
        )
        inserted += cursor.rowcount if cursor.rowcount > 0 else 0
    return inserted


def append_events_authoritative(root: Path | str, encoded: bytes) -> None:
    """Post-cutover authoritative append: any failure raises (never swallowed)."""
    database = shadow_database_path(root)
    connection = open_shadow_connection(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            insert_event_lines(connection, encoded)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()


def journal_bytes_from_table(root: Path | str) -> bytes:
    """Rebuild the exact journal byte stream from the events table.

    Rows are re-serialized with the same options `append_event` uses
    (ensure_ascii=False, sort_keys=True), so a verified cutover keeps this
    byte-identical to events.jsonl. Any failure raises.
    """
    database = shadow_database_path(root)
    connection = open_shadow_connection(database)
    try:
        lines: list[str] = []
        for event_id, event_type, payload_json, created_at in connection.execute(
            "SELECT event_id, event_type, payload_json, created_at"
            " FROM events ORDER BY event_cursor"
        ):
            lines.append(
                _dump(
                    {
                        "created_at": created_at,
                        "event_id": event_id,
                        "event_type": event_type,
                        "payload": json.loads(payload_json),
                    }
                )
                + "\n"
            )
        return "".join(lines).encode("utf-8")
    finally:
        connection.close()


def append_events_if_enabled(root: Path | str, encoded: bytes) -> None:
    """SQLite 阶段 5a：journal 权威写成功后的影子双写。

    encoded 可含多行（daemon outbox 批量）；每行一个事件 JSON。失败只落
    shadow-errors.jsonl，绝不进入 journal 写路径。调用方必须已持有
    `_protocol_mutation_lock`（阶段 2 硬规则：连接只在该锁内打开）。
    """
    database = shadow_database_path(root)
    try:
        if not database.is_file():
            return
        connection = open_shadow_connection(database)
        try:
            if not _shadow_enabled(connection):
                return
            connection.execute("BEGIN IMMEDIATE")
            try:
                insert_event_lines(connection, encoded)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()
    except Exception as error:  # noqa: BLE001 - shadow must never break authority
        _log_shadow_error(root, error)


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
        _log_shadow_error(root, error)
