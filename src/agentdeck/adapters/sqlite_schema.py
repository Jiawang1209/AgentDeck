"""Immutable SQLite DDL authorities and filesystem safety helpers."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
from typing import Final

_SCHEMA_VERSION: Final = 2
_STATE_DIRECTORY: Final = ".agentdeck"
_DATABASE_NAME: Final = "agentdeck.db"
_REQUIRED_TABLES: Final = frozenset(
    {
        "schema_metadata", "projects", "product_sessions", "conversation_turns",
        "agent_instances", "missions", "mission_versions", "tasks", "attempts",
        "handoffs", "approvals", "evidence", "commands", "events",
    }
)
_METADATA_COLUMNS: Final = (
    ("singleton", "INTEGER", 0, 1),
    ("schema_version", "INTEGER", 1, 0),
    ("schema_digest", "TEXT", 1, 0),
    ("project_root", "TEXT", 1, 0),
)
FileIdentity = tuple[int, int, bool, bool]
class StoreSchemaError(RuntimeError):
    """Raised when an existing database cannot be trusted as this schema."""


class StoreCommandStateError(RuntimeError):
    """Raised when mutation authority or a persisted command is invalid."""


class StoreSerializationError(ValueError):
    """Raised when a command fact cannot be safely bounded and canonicalized."""


V1_DDL: Final = (
        """CREATE TABLE schema_metadata (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
            schema_digest TEXT NOT NULL CHECK (length(schema_digest) = 64),
            project_root TEXT NOT NULL CHECK (length(project_root) > 0)
        )""",
        """CREATE TABLE projects (
            project_id TEXT PRIMARY KEY,
            resolved_root TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0)
        )""",
        """CREATE TABLE product_sessions (
            session_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
            state TEXT NOT NULL CHECK (state IN (
                'setup','ready','drafting','awaiting_confirmation','running',
                'awaiting_approval','paused','needs_attention','completed','failed','cancelled'
            )),
            permission_profile TEXT CHECK (permission_profile IS NULL OR permission_profile IN (
                'ask_for_approval','approve_for_me','full_access'
            )),
            pending_goal TEXT,
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0)
        )""",
        """CREATE TABLE conversation_turns (
            turn_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES product_sessions(session_id) ON DELETE RESTRICT,
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            actor_role TEXT NOT NULL CHECK (actor_role IN ('human','leader','system')),
            sanitized_content TEXT NOT NULL CHECK (length(trim(sanitized_content)) > 0),
            occurred_at TEXT NOT NULL CHECK (length(trim(occurred_at)) > 0),
            UNIQUE (session_id, ordinal)
        )""",
        """CREATE TABLE agent_instances (
            instance_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES product_sessions(session_id) ON DELETE RESTRICT,
            backend_id TEXT NOT NULL,
            transport TEXT NOT NULL,
            backend_version TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN (
                'leader','implementer','reviewer','reviser','acceptance_reviewer'
            )),
            acp_session_id TEXT UNIQUE,
            state TEXT NOT NULL CHECK (state IN ('planned','ready','active','stopped','lost')),
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0)
        )""",
        """CREATE TABLE missions (
            mission_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES product_sessions(session_id) ON DELETE RESTRICT,
            state TEXT NOT NULL CHECK (state IN (
                'draft','awaiting_confirmation','confirmed','running','completed','failed','cancelled'
            )),
            current_version INTEGER NOT NULL CHECK (current_version > 0),
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0)
        )""",
        """CREATE TABLE mission_versions (
            mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE RESTRICT,
            version INTEGER NOT NULL CHECK (version > 0),
            preview_id TEXT NOT NULL UNIQUE,
            content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
            canonical_mission_facts TEXT NOT NULL,
            confirmed_at TEXT,
            PRIMARY KEY (mission_id, version),
            UNIQUE (mission_id, content_hash)
        )""",
        """CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            mission_version INTEGER NOT NULL CHECK (mission_version > 0),
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN (
                'implementer','reviewer','reviser','acceptance_reviewer'
            )),
            planned_backend TEXT NOT NULL,
            planned_agent_instance_id TEXT NOT NULL,
            acp_route TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN (
                'pending','ready','running','awaiting_approval','completed','failed','cancelled'
            )),
            canonical_task_facts TEXT NOT NULL,
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
            FOREIGN KEY (mission_id, mission_version)
                REFERENCES mission_versions(mission_id, version) ON DELETE RESTRICT,
            UNIQUE (mission_id, mission_version, ordinal)
        )""",
        """CREATE TABLE attempts (
            attempt_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
            agent_instance_id TEXT REFERENCES agent_instances(instance_id) ON DELETE RESTRICT,
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            state TEXT NOT NULL CHECK (state IN (
                'pending','running','awaiting_approval','human_controlled','completed',
                'failed','cancelled','interrupted','outcome_unknown'
            )),
            reason TEXT,
            result_summary TEXT,
            retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
            acp_session_id TEXT,
            effect_observed INTEGER NOT NULL DEFAULT 0 CHECK (effect_observed IN (0, 1)),
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
            UNIQUE (task_id, ordinal)
        )""",
        """CREATE TABLE handoffs (
            handoff_id TEXT PRIMARY KEY,
            source_attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
            target_task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
            result_summary TEXT NOT NULL,
            canonical_handoff_facts TEXT NOT NULL,
            content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            UNIQUE (source_attempt_id, target_task_id, content_hash)
        )""",
        """CREATE TABLE approvals (
            approval_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            mission_version INTEGER NOT NULL CHECK (mission_version > 0),
            attempt_id TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
            effect TEXT NOT NULL CHECK (effect IN (
                'read','write_project','command_project','network','write_external',
                'credential','destructive','publish'
            )),
            state TEXT NOT NULL CHECK (state IN ('pending','approved','denied','cancelled','expired')),
            scope_hash TEXT NOT NULL CHECK (length(scope_hash) = 64),
            canonical_request_facts TEXT NOT NULL,
            canonical_decision_facts TEXT,
            requested_at TEXT NOT NULL CHECK (length(trim(requested_at)) > 0),
            decided_at TEXT,
            FOREIGN KEY (mission_id, mission_version)
                REFERENCES mission_versions(mission_id, version) ON DELETE RESTRICT
        )""",
        """CREATE TABLE evidence (
            evidence_id TEXT PRIMARY KEY,
            task_id TEXT REFERENCES tasks(task_id) ON DELETE RESTRICT,
            attempt_id TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
            kind TEXT NOT NULL CHECK (kind IN (
                'test_exit_status','diff_identity','artifact_hash','review_finding',
                'acceptance_result','human_decision'
            )),
            canonical_evidence_facts TEXT NOT NULL,
            content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0)
        )""",
        """CREATE TABLE commands (
            command_id TEXT PRIMARY KEY,
            command_kind TEXT NOT NULL CHECK (length(trim(command_kind)) > 0),
            state TEXT NOT NULL CHECK (state IN ('started','completed','failed')),
            canonical_result_facts TEXT,
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            completed_at TEXT
        )""",
        """CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            command_id TEXT REFERENCES commands(command_id) ON DELETE RESTRICT,
            kind TEXT NOT NULL CHECK (length(trim(kind)) > 0),
            aggregate_type TEXT NOT NULL CHECK (length(trim(aggregate_type)) > 0),
            aggregate_id TEXT NOT NULL CHECK (length(trim(aggregate_id)) > 0),
            canonical_payload_facts TEXT NOT NULL,
            occurred_at TEXT NOT NULL CHECK (length(trim(occurred_at)) > 0)
        )""",
        "CREATE INDEX idx_turns_session_ordinal ON conversation_turns(session_id, ordinal)",
        "CREATE INDEX idx_agents_session_state ON agent_instances(session_id, state)",
        "CREATE INDEX idx_missions_session_state ON missions(session_id, state)",
        "CREATE INDEX idx_tasks_mission_state ON tasks(mission_id, mission_version, state)",
        "CREATE INDEX idx_attempts_task_ordinal ON attempts(task_id, ordinal)",
        "CREATE INDEX idx_attempts_state ON attempts(state)",
        "CREATE INDEX idx_approvals_state ON approvals(state)",
        "CREATE INDEX idx_evidence_attempt ON evidence(attempt_id)",
        "CREATE INDEX idx_commands_state ON commands(state)",
        "CREATE INDEX idx_events_aggregate ON events(aggregate_type, aggregate_id, occurred_at)",
    )

V2_DDL: Final = (
    "ALTER TABLE product_sessions ADD COLUMN leader_backend TEXT",
    "ALTER TABLE product_sessions ADD COLUMN leader_model TEXT",
    "ALTER TABLE product_sessions ADD COLUMN pending_exit_id TEXT",
    "ALTER TABLE product_sessions ADD COLUMN pending_exit_attempt_id TEXT",
    """ALTER TABLE product_sessions
       ADD COLUMN canonical_pending_exit_attempt_facts TEXT""",
    "ALTER TABLE product_sessions ADD COLUMN pending_exit_attempt_hash TEXT",
    "ALTER TABLE product_sessions ADD COLUMN pending_exit_requested_at TEXT",
    """CREATE TRIGGER trg_product_sessions_v2_closed_insert
       BEFORE INSERT ON product_sessions
       WHEN ((NEW.leader_backend IS NULL) != (NEW.leader_model IS NULL))
         OR (NEW.state = 'setup' AND NEW.leader_backend IS NOT NULL)
         OR (NEW.state != 'setup' AND NEW.leader_backend IS NULL)
         OR ((NEW.pending_exit_id IS NULL)
           + (NEW.pending_exit_attempt_id IS NULL)
           + (NEW.canonical_pending_exit_attempt_facts IS NULL)
           + (NEW.pending_exit_attempt_hash IS NULL)
           + (NEW.pending_exit_requested_at IS NULL)) NOT IN (0, 5)
       BEGIN
         SELECT RAISE(ABORT, 'invalid product_sessions v2 closed shape');
       END""",
    """CREATE TRIGGER trg_product_sessions_v2_closed_update
       BEFORE UPDATE ON product_sessions
       WHEN ((NEW.leader_backend IS NULL) != (NEW.leader_model IS NULL))
         OR (NEW.state = 'setup' AND NEW.leader_backend IS NOT NULL)
         OR (NEW.state != 'setup' AND NEW.leader_backend IS NULL)
         OR ((NEW.pending_exit_id IS NULL)
           + (NEW.pending_exit_attempt_id IS NULL)
           + (NEW.canonical_pending_exit_attempt_facts IS NULL)
           + (NEW.pending_exit_attempt_hash IS NULL)
           + (NEW.pending_exit_requested_at IS NULL)) NOT IN (0, 5)
       BEGIN
         SELECT RAISE(ABORT, 'invalid product_sessions v2 closed shape');
       END""",
)


def _migration_statements() -> tuple[str, ...]:
    """Return the immutable historical v1 DDL for compatibility tests."""
    return V1_DDL


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            """SELECT name FROM sqlite_schema
               WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"""
        )
    }


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _resolved_project_root(project_root: str | os.PathLike[str]) -> Path:
    supplied = Path(project_root).expanduser()
    if supplied.is_symlink():
        raise ValueError("project root must not be a symlink")
    if not supplied.exists():
        raise FileNotFoundError(f"project root does not exist: {supplied}")
    if not supplied.is_dir():
        raise NotADirectoryError(f"project root is not a directory: {supplied}")
    resolved = supplied.resolve(strict=True)
    try:
        str(resolved).encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise ValueError("project root must be strict UTF-8") from error
    return resolved


def _state_directory(root: Path, *, create: bool) -> Path:
    path = root / _STATE_DIRECTORY
    if _path_exists(path):
        if path.is_symlink():
            raise ValueError(".agentdeck must not be a symlink")
        if not path.is_dir():
            raise NotADirectoryError(".agentdeck must be a directory")
    elif create:
        path.mkdir(mode=0o700)
    else:
        raise FileNotFoundError("project has no .agentdeck authority")
    if path.resolve(strict=True).parent != root:
        raise ValueError(".agentdeck escapes the resolved project root")
    return path


def _file_identity(path: Path) -> FileIdentity:
    info = path.lstat()
    return (info.st_dev, info.st_ino, stat.S_ISREG(info.st_mode), info.st_nlink == 1)


def _require_safe_file(path: Path, label: str) -> FileIdentity:
    identity = _file_identity(path)
    if path.is_symlink():
        raise ValueError(f"{label} path must not be a symlink")
    if not identity[2]:
        raise ValueError(f"{label} path must be a regular file")
    if not identity[3]:
        raise ValueError(f"{label} path must not be a hard link")
    return identity


def _database_path(state: Path, *, create: bool) -> tuple[Path, FileIdentity]:
    database = state / _DATABASE_NAME
    if _path_exists(database):
        return database, _require_safe_file(database, "database")
    if not create:
        raise FileNotFoundError("project has no AgentDeck database")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(database, flags, 0o600)
    os.close(descriptor)
    return database, _require_safe_file(database, "database")


def _require_database_identity(path: Path, expected: FileIdentity) -> None:
    try:
        current = _file_identity(path)
    except OSError as error:
        raise StoreSchemaError("database identity is unavailable") from error
    if current != expected or not current[2] or not current[3]:
        raise StoreSchemaError("database identity changed during open or transaction")


def _connect_database(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=5.0, isolation_level=None)


def _connect_inspection_database(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(
        f"{path.as_uri()}?mode=ro", uri=True, timeout=5.0, isolation_level=None
    )


def _configure_local(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")


def _configure_durability(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA journal_mode = DELETE").fetchone() != ("delete",):
        raise StoreSchemaError("SQLite rollback journal mode is unavailable")
    connection.execute("PRAGMA synchronous = FULL")


def _no_op_path_hook(path: Path) -> None:
    pass
