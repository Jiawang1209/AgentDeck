"""Exact historical schema-v1 databases for migration tests."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from agentdeck.adapters.sqlite_migrations import (
    _live_schema_fingerprint,
    _live_schema_objects,
)
from agentdeck.adapters.sqlite_schema import V1_DDL


NOW = "2026-07-19T00:00:00+00:00"


def create_v1_database(
    project: Path,
    *,
    state: str = "ready",
    permission: str | None = "approve_for_me",
    goal: str | None = "Migrate safely",
    leader_backend: str = "codex-cli",
    model: str = "native-default",
    include_configure: bool = True,
) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    root = project.resolve()
    state_directory = root / ".agentdeck"
    state_directory.mkdir()
    database = state_directory / "agentdeck.db"
    project_id = "prj_" + sha256(
        str(root).encode("utf-8", "strict")
    ).hexdigest()[:24]
    session_id = "ses_v1"
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        for statement in V1_DDL:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?)", (project_id, str(root), NOW)
        )
        connection.execute(
            """INSERT INTO product_sessions (
                   session_id,project_id,state,permission_profile,pending_goal,
                   created_at,updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, project_id, state, permission, goal, NOW, NOW),
        )
        if include_configure:
            result = {
                "accepted": True,
                "goal": goal,
                "leader_backend": leader_backend,
                "mode": "goal_ready" if goal is not None else "ready",
                "model": model,
                "permission": permission,
                "session_id": session_id,
            }
            connection.execute(
                "INSERT INTO commands VALUES (?, ?, 'completed', ?, ?, ?)",
                (
                    f"session:configure:{session_id}",
                    "configure_product_session",
                    json.dumps(
                        result, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                    NOW,
                    NOW,
                ),
            )
        connection.execute(
            "INSERT INTO schema_metadata VALUES (1, 1, ?, ?)",
            (_live_schema_fingerprint(connection), str(root)),
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return database


def authority_snapshot(database: Path) -> tuple[object, ...]:
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        return (
            tuple(_live_schema_objects(connection)),
            tuple(connection.execute(
                "SELECT * FROM schema_metadata ORDER BY singleton"
            )),
            tuple(connection.execute(
                "SELECT * FROM product_sessions ORDER BY session_id"
            )),
            tuple(connection.execute("SELECT * FROM commands ORDER BY command_id")),
        )
    finally:
        connection.close()


def create_damaged_v1_database(project: Path, damage: str) -> Path:
    if damage == "missing_configure":
        return create_v1_database(project, include_configure=False)
    if damage == "setup_with_configure":
        return create_v1_database(
            project, state="setup", permission=None, goal=None,
            include_configure=True,
        )
    database = create_v1_database(project)
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        if damage == "unknown_version":
            connection.execute(
                "UPDATE schema_metadata SET schema_version=99 WHERE singleton=1"
            )
        elif damage == "self_consistent_non_v1":
            connection.execute(
                "ALTER TABLE product_sessions ADD COLUMN future_identity TEXT"
            )
            connection.execute(
                """UPDATE schema_metadata SET schema_version=3,schema_digest=?
                   WHERE singleton=1""",
                (_live_schema_fingerprint(connection),),
            )
        elif damage == "partial_v2":
            connection.execute(
                "ALTER TABLE product_sessions ADD COLUMN leader_backend TEXT"
            )
            connection.execute(
                "UPDATE schema_metadata SET schema_digest=? WHERE singleton=1",
                (_live_schema_fingerprint(connection),),
            )
        elif damage == "wrong_command_id":
            connection.execute(
                """UPDATE commands SET command_id='session:configure:ses_wrong'
                   WHERE command_id='session:configure:ses_v1'"""
            )
        elif damage == "wrong_command_kind":
            connection.execute(
                """UPDATE commands SET command_kind='other_kind'
                   WHERE command_id='session:configure:ses_v1'"""
            )
        elif damage == "started_command":
            connection.execute(
                """UPDATE commands SET state='started',canonical_result_facts=NULL,
                   completed_at=NULL WHERE command_id='session:configure:ses_v1'"""
            )
        elif damage == "malformed_configure":
            connection.execute(
                """UPDATE commands SET canonical_result_facts='{"accepted":true}'
                   WHERE command_id='session:configure:ses_v1'"""
            )
        elif damage in {
            "conflicting_permission", "session_lineage", "goal_lineage",
            "mode_lineage", "leader_oversize", "model_oversize",
        }:
            raw = connection.execute(
                """SELECT canonical_result_facts FROM commands
                   WHERE command_id='session:configure:ses_v1'"""
            ).fetchone()[0]
            result = json.loads(raw)
            replacements = {
                "conflicting_permission": ("permission", "full_access"),
                "session_lineage": ("session_id", "ses_other"),
                "goal_lineage": ("goal", "Different goal"),
                "mode_lineage": ("mode", "ready"),
                "leader_oversize": ("leader_backend", "l" * 4097),
                "model_oversize": ("model", "m" * 4097),
            }
            key, value = replacements[damage]
            result[key] = value
            connection.execute(
                """UPDATE commands SET canonical_result_facts=?
                   WHERE command_id='session:configure:ses_v1'""",
                (json.dumps(
                    result, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False,
                ),),
            )
        else:
            raise ValueError(f"unknown v1 damage: {damage}")
    finally:
        connection.close()
    return database
