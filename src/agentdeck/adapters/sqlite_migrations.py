"""Exact SQLite schema fingerprints and transactional v1-to-v2 migration."""

from __future__ import annotations

from hashlib import sha256
from hmac import compare_digest
import json
from pathlib import Path
import sqlite3
from typing import Final

from agentdeck.adapters.sqlite_schema import (
    _METADATA_COLUMNS,
    _REQUIRED_TABLES,
    StoreCommandStateError,
    StoreSchemaError,
    V1_DDL,
    V2_DDL,
    _table_names,
)
from agentdeck.adapters.sqlite_validation import _validate_command_row


_CONFIGURE_KIND: Final = "configure_product_session"
_CONFIGURE_ID_PREFIX: Final = "session:configure:"
_PERMISSIONS: Final = frozenset(
    {"ask_for_approval", "approve_for_me", "full_access"}
)


def _live_schema_objects(connection: sqlite3.Connection) -> list[tuple[str, ...]]:
    return connection.execute(
        """SELECT type, name, tbl_name, sql FROM sqlite_schema
           WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
           ORDER BY type, name"""
    ).fetchall()


def _schema_fingerprint(objects: object) -> str:
    serialized = json.dumps(
        objects, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8", "strict")
    return sha256(serialized).hexdigest()


def _live_schema_fingerprint(connection: sqlite3.Connection) -> str:
    return _schema_fingerprint(_live_schema_objects(connection))


def _known_authority(
    statements: tuple[str, ...],
) -> tuple[tuple[tuple[str, ...], ...], str]:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        for statement in statements:
            connection.execute(statement)
        objects = tuple(_live_schema_objects(connection))
        return objects, _schema_fingerprint(objects)
    finally:
        connection.close()


def known_schema_fingerprint(
    statements: tuple[str, ...], expected_pin: str,
) -> str:
    """Compute an exact authority fingerprint and require its immutable pin."""
    _, computed = _known_authority(statements)
    if not compare_digest(computed, expected_pin):
        raise StoreSchemaError("SQLite schema fingerprint pin does not match DDL")
    return computed


V1_SCHEMA_FINGERPRINT: Final = (
    "c2252ae6c5d83ac3c8f17cd856ffc491d259b8ad6378e449e0fef19f48c3d733"
)
V2_SCHEMA_FINGERPRINT: Final = (
    "8a95c6f1f53d40e162f27ddbd9c2c102b912d174b9826ffdde02ac1ed49db009"
)
V1_SCHEMA_OBJECTS, _ = _known_authority(V1_DDL)
V2_SCHEMA_OBJECTS, _ = _known_authority(V1_DDL + V2_DDL)
known_schema_fingerprint(V1_DDL, V1_SCHEMA_FINGERPRINT)
known_schema_fingerprint(V1_DDL + V2_DDL, V2_SCHEMA_FINGERPRINT)


def _metadata_version(connection: sqlite3.Connection, root: Path) -> int:
    columns = tuple(
        (row[1], row[2].upper(), row[3], row[5])
        for row in connection.execute("PRAGMA table_info(schema_metadata)")
    )
    if columns != _METADATA_COLUMNS:
        raise StoreSchemaError("schema metadata is damaged")
    rows = connection.execute(
        "SELECT singleton,schema_version,schema_digest,project_root FROM schema_metadata"
    ).fetchall()
    if (
        len(rows) != 1
        or rows[0][0] != 1
        or type(rows[0][1]) is not int
        or rows[0][1] not in {1, 2}
        or type(rows[0][2]) is not str
        or len(rows[0][2]) != 64
        or any(character not in "0123456789abcdef" for character in rows[0][2])
        or type(rows[0][3]) is not str
    ):
        raise StoreSchemaError("schema version is unknown or damaged")
    try:
        rows[0][3].encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise StoreSchemaError("stored project root is not strict UTF-8") from error
    if rows[0][3] != str(root):
        raise StoreSchemaError("database belongs to a different project root")
    version = rows[0][1]
    objects = tuple(_live_schema_objects(connection))
    fingerprint = _schema_fingerprint(objects)
    expected_objects, expected_fingerprint = (
        (V1_SCHEMA_OBJECTS, V1_SCHEMA_FINGERPRINT)
        if version == 1
        else (V2_SCHEMA_OBJECTS, V2_SCHEMA_FINGERPRINT)
    )
    if _table_names(connection) != _REQUIRED_TABLES:
        raise StoreSchemaError("versioned schema is damaged")
    if (
        objects != expected_objects
        or not compare_digest(rows[0][2], fingerprint)
        or not compare_digest(fingerprint, expected_fingerprint)
    ):
        raise StoreSchemaError("live schema drifted from its version authority")
    return version


def _strict_identity(value: object, label: str) -> str:
    if type(value) is not str:
        raise StoreSchemaError(f"stored {label} must be text")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise StoreSchemaError(f"stored {label} is not strict UTF-8") from error
    if not value.strip():
        raise StoreSchemaError(f"stored {label} is blank")
    if len(encoded) > 4096:
        raise StoreSchemaError(f"stored {label} is too large")
    return value


def _has_configure_marker(value: object, marker: str, *, prefix: bool) -> bool:
    if type(value) is str:
        return value.startswith(marker) if prefix else value == marker
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw, expected = bytes(value), marker.encode("ascii")
        return raw.startswith(expected) if prefix else raw == expected
    return False


def _configure_rows(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    cursor = connection.execute(
        """SELECT command_id,command_kind,state,canonical_result_facts,
                  created_at,completed_at
           FROM commands ORDER BY command_id""",
    )
    candidates = []
    for row in cursor:
        if (
            _has_configure_marker(row[0], _CONFIGURE_ID_PREFIX, prefix=True)
            or _has_configure_marker(row[1], _CONFIGURE_KIND, prefix=False)
        ):
            candidates.append(row)
    return candidates


def _v1_backfill_pairs(
    connection: sqlite3.Connection,
) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    sessions = connection.execute(
        """SELECT session_id,state,permission_profile,pending_goal
           FROM product_sessions ORDER BY session_id"""
    ).fetchall()
    candidates: dict[object, list[dict[str, object]]] = {
        row[0]: [] for row in sessions
    }
    for row in _configure_rows(connection):
        command_id, kind, command_state, raw_result, created_at, completed_at = row
        if type(command_id) is not str or type(kind) is not str:
            raise StoreSchemaError("configure command identity must be text")
        if kind != _CONFIGURE_KIND:
            raise StoreSchemaError("configure command kind is invalid")
        try:
            result = _validate_command_row(
                (kind, command_state, raw_result, created_at, completed_at)
            )
        except (StoreCommandStateError, TypeError, ValueError, OverflowError) as error:
            raise StoreSchemaError("configure command is not exactly completed") from error
        if set(result) != {
            "accepted", "goal", "leader_backend", "mode", "model",
            "permission", "session_id",
        }:
            raise StoreSchemaError("configure command result fields are invalid")
        session_id = result.get("session_id")
        if type(session_id) is not str or session_id not in candidates:
            raise StoreSchemaError("configure command has no exact session lineage")
        if command_id != f"session:configure:{session_id}":
            raise StoreSchemaError("configure command id is invalid")
        result_permission = result.get("permission")
        result_mode = result.get("mode")
        if (
            result.get("accepted") is not True
            or type(result_permission) is not str
            or result_permission not in _PERMISSIONS
            or type(result_mode) is not str
            or result_mode not in {"ready", "goal_ready"}
        ):
            raise StoreSchemaError("configure command result is invalid")
        _strict_identity(result.get("leader_backend"), "leader backend")
        _strict_identity(result.get("model"), "leader model")
        candidates[session_id].append(result)
    for session_id, state, permission, goal in sessions:
        assigned = candidates[session_id]
        if state == "setup":
            if assigned:
                raise StoreSchemaError("setup session has a configure command")
            continue
        if len(assigned) != 1:
            raise StoreSchemaError("configured session needs exactly one configure command")
        result = assigned[0]
        expected_mode = "goal_ready" if goal is not None else "ready"
        if (
            permission not in _PERMISSIONS
            or result.get("permission") != permission
            or result.get("goal") != goal
            or result.get("mode") != expected_mode
        ):
            raise StoreSchemaError("configure command lineage is inconsistent")
        leader = _strict_identity(result.get("leader_backend"), "leader backend")
        model = _strict_identity(result.get("model"), "leader model")
        pairs.append((leader, model, session_id))
    return pairs


def _validate_v2_rows(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """SELECT session_id,state,leader_backend,leader_model,
                  pending_exit_id,pending_exit_attempt_id,
                  canonical_pending_exit_attempt_facts,pending_exit_attempt_hash,
                  pending_exit_requested_at
           FROM product_sessions ORDER BY session_id"""
    ).fetchall()
    for row in rows:
        session_id, state, leader, model, *pending = row
        if state == "setup":
            if leader is not None or model is not None:
                raise StoreSchemaError(
                    f"setup session {session_id!r} has configured identity"
                )
        else:
            _strict_identity(leader, "leader backend")
            _strict_identity(model, "leader model")
        nulls = sum(value is None for value in pending)
        if nulls not in {0, 5}:
            raise StoreSchemaError(
                f"session {session_id!r} has a partial pending-exit group"
            )


def _validate_existing_schema(connection: sqlite3.Connection, root: Path) -> None:
    version = _metadata_version(connection, root)
    if version == 1:
        _v1_backfill_pairs(connection)
    else:
        _validate_v2_rows(connection)


def _validate_before_durability(connection: sqlite3.Connection, root: Path) -> None:
    if not _live_schema_objects(connection):
        return
    if "schema_metadata" not in _table_names(connection):
        raise StoreSchemaError("database has no recognized schema authority")
    _validate_existing_schema(connection, root)


def _execute_all(connection: sqlite3.Connection, statements: tuple[str, ...]) -> None:
    for statement in statements:
        connection.execute(statement)


def _require_v2_authority(connection: sqlite3.Connection) -> None:
    objects = tuple(_live_schema_objects(connection))
    if objects != V2_SCHEMA_OBJECTS or not compare_digest(
        _schema_fingerprint(objects), V2_SCHEMA_FINGERPRINT
    ):
        raise StoreSchemaError("migration did not produce exact schema v2")


def migrate_schema(connection: sqlite3.Connection, root: Path) -> None:
    """Create v2 or migrate an exact v1 authority in one immediate transaction."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        objects = _live_schema_objects(connection)
        created = not objects
        migrated = False
        if created:
            _execute_all(connection, V1_DDL)
            _execute_all(connection, V2_DDL)
        elif "schema_metadata" not in _table_names(connection):
            raise StoreSchemaError("database has no recognized schema authority")
        else:
            version = _metadata_version(connection, root)
            if version == 1:
                backfill = _v1_backfill_pairs(connection)
                _execute_all(connection, V2_DDL)
                connection.executemany(
                    """UPDATE product_sessions
                       SET leader_backend=?,leader_model=? WHERE session_id=?""",
                    backfill,
                )
                migrated = True
            else:
                _validate_v2_rows(connection)
        _validate_v2_rows(connection)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise StoreSchemaError("schema contains foreign-key violations")
        _require_v2_authority(connection)
        if created:
            connection.execute(
                "INSERT INTO schema_metadata VALUES (1, 2, ?, ?)",
                (V2_SCHEMA_FINGERPRINT, str(root)),
            )
        elif migrated:
            connection.execute(
                """UPDATE schema_metadata
                   SET schema_version=2,schema_digest=? WHERE singleton=1""",
                (V2_SCHEMA_FINGERPRINT,),
            )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
