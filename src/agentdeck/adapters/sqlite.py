"""Project-local SQLite authority and version-one schema."""

from __future__ import annotations

from hashlib import sha256
from hmac import compare_digest
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Final, TypeAlias


_SCHEMA_VERSION: Final = 1
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
_FileIdentity: TypeAlias = tuple[int, int, bool, bool]


class StoreSchemaError(RuntimeError):
    """Raised when an existing database cannot be trusted as this schema."""


def _migration_statements() -> tuple[str, ...]:
    return (
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


def _live_schema_objects(connection: sqlite3.Connection) -> list[tuple[str, ...]]:
    return connection.execute(
        """SELECT type, name, tbl_name, sql FROM sqlite_schema
           WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
           ORDER BY type, name"""
    ).fetchall()


def _live_schema_fingerprint(connection: sqlite3.Connection) -> str:
    serialized = json.dumps(
        _live_schema_objects(connection), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8", "strict")
    return sha256(serialized).hexdigest()


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


def _file_identity(path: Path) -> _FileIdentity:
    info = path.lstat()
    return (info.st_dev, info.st_ino, stat.S_ISREG(info.st_mode), info.st_nlink == 1)


def _prepare_database_path(root: Path) -> tuple[Path, _FileIdentity]:
    state_directory = root / _STATE_DIRECTORY
    if _path_exists(state_directory):
        if state_directory.is_symlink():
            raise ValueError(".agentdeck must not be a symlink")
        if not state_directory.is_dir():
            raise NotADirectoryError(".agentdeck must be a directory")
    else:
        state_directory.mkdir(mode=0o700)
    if state_directory.resolve(strict=True).parent != root:
        raise ValueError(".agentdeck escapes the resolved project root")

    database = state_directory / _DATABASE_NAME
    if _path_exists(database):
        identity = _file_identity(database)
        if database.is_symlink():
            raise ValueError("database path must not be a symlink")
        if not identity[2]:
            raise ValueError("database path must be a regular file")
        if not identity[3]:
            raise ValueError("database path must not be a hard link")
    else:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(database, flags, 0o600)
        os.close(descriptor)
        identity = _file_identity(database)
    return database, identity


def _connect_database(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=5.0, isolation_level=None)


def _configure_local(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")


def _configure_durability(connection: sqlite3.Connection) -> None:
    mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()
    if mode != ("delete",):
        raise StoreSchemaError("SQLite rollback journal mode is unavailable")
    connection.execute("PRAGMA synchronous = FULL")


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            """SELECT name FROM sqlite_schema
               WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"""
        )
    }


def _validate_existing_schema(connection: sqlite3.Connection, root: Path) -> None:
    columns = tuple(
        (row[1], row[2].upper(), row[3], row[5])
        for row in connection.execute("PRAGMA table_info(schema_metadata)")
    )
    if columns != _METADATA_COLUMNS:
        raise StoreSchemaError("schema metadata is damaged")
    rows = connection.execute(
        "SELECT singleton, schema_version, schema_digest, project_root FROM schema_metadata"
    ).fetchall()
    if (
        len(rows) != 1
        or rows[0][:2] != (1, _SCHEMA_VERSION)
        or type(rows[0][2]) is not str
        or len(rows[0][2]) != 64
        or type(rows[0][3]) is not str
    ):
        raise StoreSchemaError("schema version is unknown or damaged")
    try:
        rows[0][3].encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise StoreSchemaError("stored project root is not strict UTF-8") from error
    if rows[0][3] != str(root):
        raise StoreSchemaError("database belongs to a different project root")
    if _table_names(connection) != _REQUIRED_TABLES:
        raise StoreSchemaError("versioned schema is damaged")
    if not compare_digest(rows[0][2], _live_schema_fingerprint(connection)):
        raise StoreSchemaError("live schema drifted from its version authority")


def _validate_before_durability(connection: sqlite3.Connection, root: Path) -> None:
    if not _live_schema_objects(connection):
        return
    tables = _table_names(connection)
    if "schema_metadata" not in tables:
        raise StoreSchemaError("database has no recognized schema authority")
    _validate_existing_schema(connection, root)


def _migrate(connection: sqlite3.Connection, root: Path) -> None:
    statements = _migration_statements()
    connection.execute("BEGIN IMMEDIATE")
    try:
        objects = _live_schema_objects(connection)
        if not objects:
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_metadata VALUES (?, ?, ?, ?)",
                (1, _SCHEMA_VERSION, _live_schema_fingerprint(connection), str(root)),
            )
        elif "schema_metadata" in _table_names(connection):
            _validate_existing_schema(connection, root)
        else:
            raise StoreSchemaError("database has no recognized schema authority")
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _require_database_identity(path: Path, expected: _FileIdentity) -> None:
    try:
        current = _file_identity(path)
    except OSError as error:
        raise StoreSchemaError("database identity is unavailable") from error
    if current != expected or not current[2] or not current[3]:
        raise StoreSchemaError("database identity changed during open")


def _after_migrate(path: Path) -> None:
    """Private deterministic race-test seam."""


def _connect_inspection_database(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(
        f"{path.as_uri()}?mode=ro", uri=True, timeout=5.0, isolation_level=None
    )


def _after_inspection_validation(path: Path) -> None:
    """Private deterministic race-test seam."""


def _open_inspection_connection(
    path: Path, expected: _FileIdentity, root: Path
) -> sqlite3.Connection:
    connection = _connect_inspection_database(path)
    try:
        _require_database_identity(path, expected)
        _configure_local(connection)
        connection.execute("PRAGMA query_only = ON")
        _validate_existing_schema(connection, root)
        _after_inspection_validation(path)
        _require_database_identity(path, expected)
        return connection
    except BaseException:
        connection.close()
        raise


class SQLiteStore:
    """Own the resolved project's SQLite connection; mutations arrive in Task 10."""

    def __init__(
        self, path: Path, connection: sqlite3.Connection,
        identity: _FileIdentity, project_root: Path,
    ) -> None:
        self.path = path
        self._writer: sqlite3.Connection | None = connection
        self._inspection: sqlite3.Connection | None = None
        self._database_identity = identity
        self._project_root = project_root

    @classmethod
    def open(cls, project_root: str | os.PathLike[str]) -> "SQLiteStore":
        root = _resolved_project_root(project_root)
        path, identity = _prepare_database_path(root)
        connection: sqlite3.Connection | None = None
        try:
            connection = _connect_database(path)
            _require_database_identity(path, identity)
            _configure_local(connection)
            _validate_before_durability(connection, root)
            _configure_durability(connection)
            _migrate(connection, root)
            _require_database_identity(path, identity)
            _after_migrate(path)
            _require_database_identity(path, identity)
            return cls(path, connection, identity, root)
        except BaseException:
            if connection is not None:
                connection.close()
            raise

    @property
    def connection(self) -> sqlite3.Connection:
        """Return a cached query-only inspection connection."""
        if self._writer is None:
            raise RuntimeError("SQLiteStore is closed")
        _require_database_identity(self.path, self._database_identity)
        if self._inspection is None:
            self._inspection = _open_inspection_connection(
                self.path, self._database_identity, self._project_root
            )
        return self._inspection

    def close(self) -> None:
        inspection, self._inspection = self._inspection, None
        writer, self._writer = self._writer, None
        if inspection is not None:
            inspection.close()
        if writer is not None:
            writer.close()
