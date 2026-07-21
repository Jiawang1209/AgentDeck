from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from typing import Iterator

import pytest

from agentdeck.adapters.sqlite import SQLiteStore, StoreSchemaError
from agentdeck.ports.store import Store, StoreTransaction


TABLES = {
    "schema_metadata", "projects", "product_sessions", "conversation_turns",
    "agent_instances", "missions", "mission_versions", "tasks", "attempts",
    "handoffs", "approvals", "evidence", "commands", "events", "observer_cursors", "takeover_ownership",
}


@contextmanager
def _raw(database: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        yield connection
    finally:
        connection.close()


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in connection.execute(
            """SELECT name FROM sqlite_schema
               WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"""
        )
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _foreign_keys(connection: sqlite3.Connection, table: str) -> set[tuple[str, str, str]]:
    return {
        (row[3], row[2], row[4])
        for row in connection.execute(f"PRAGMA foreign_key_list({table})")
    }


def _indexes(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA index_list({table})")}


def _live_schema(database: Path) -> list[tuple[object, ...]]:
    with _raw(database) as connection:
        return connection.execute(
            """SELECT type, name, tbl_name, sql FROM sqlite_schema
               WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
               ORDER BY type, name"""
        ).fetchall()


def _valid_database(project: Path) -> Path:
    project.mkdir()
    store = SQLiteStore.open(project)
    database = store.path
    store.close()
    return database


def test_store_port_requires_transactions_for_every_mutation() -> None:
    reads = {"command", "execute_once", "lookup_command", "load_aggregate",
             "list_running_attempts", "count", "close"}
    mutations = {"record_command", "save_aggregate", "save_session", "save_attempt",
                 "recover_attempt", "append_event"}
    assert reads <= Store.__dict__.keys()
    assert mutations.isdisjoint(Store.__dict__)
    assert mutations | {"lookup_command", "load_aggregate"} <= StoreTransaction.__dict__.keys()


def test_store_binds_only_the_supplied_resolved_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    store = SQLiteStore.open(project)
    try:
        assert store.path == project.resolve() / ".agentdeck" / "agentdeck.db"
        assert _tables(store.connection) == TABLES
        assert store.connection.execute(
            "SELECT singleton, schema_version, project_root FROM schema_metadata"
        ).fetchall() == [(1, 3, str(project.resolve()))]
        assert not (elsewhere / ".agentdeck").exists()
        assert not (tmp_path / ".agentdeck").exists()
    finally:
        store.close()


@pytest.mark.parametrize("kind", ["missing", "file", "symlink"])
def test_open_rejects_invalid_project_roots(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "project"
    if kind == "file":
        root.write_text("project file", encoding="utf-8")
    elif kind == "symlink":
        (tmp_path / "real").mkdir()
        root.symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises((FileNotFoundError, NotADirectoryError, ValueError)):
        SQLiteStore.open(root)
    assert not (root / ".agentdeck").exists()


def test_open_rejects_agentdeck_symlink_escape(tmp_path: Path) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".agentdeck").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        SQLiteStore.open(project)
    assert not (outside / "agentdeck.db").exists()


def test_open_rejects_database_symlink_and_preserves_target(tmp_path: Path) -> None:
    database = tmp_path / "project" / ".agentdeck" / "agentdeck.db"
    outside = tmp_path / "outside.db"
    database.parent.mkdir(parents=True)
    outside.write_bytes(b"outside authority")
    database.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        SQLiteStore.open(tmp_path / "project")
    assert outside.read_bytes() == b"outside authority"


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_open_rejects_non_regular_database_paths(tmp_path: Path, kind: str) -> None:
    database = tmp_path / kind / ".agentdeck" / "agentdeck.db"
    database.parent.mkdir(parents=True)
    database.mkdir() if kind == "directory" else os.mkfifo(database)
    with pytest.raises(ValueError, match="regular file"):
        SQLiteStore.open(tmp_path / kind)


def test_open_rejects_hard_linked_database_authority(tmp_path: Path) -> None:
    database = tmp_path / "project" / ".agentdeck" / "agentdeck.db"
    outside = tmp_path / "outside.db"
    database.parent.mkdir(parents=True)
    outside.write_bytes(b"shared authority")
    os.link(outside, database)
    with pytest.raises(ValueError, match="hard link"):
        SQLiteStore.open(tmp_path / "project")
    assert outside.read_bytes() == b"shared authority"


def test_existing_non_database_file_is_preserved(tmp_path: Path) -> None:
    database = tmp_path / ".agentdeck" / "agentdeck.db"
    database.parent.mkdir()
    database.write_bytes(b"not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        SQLiteStore.open(tmp_path)
    assert database.read_bytes() == b"not a sqlite database"


def test_writer_and_read_only_open_keep_one_version_and_root_row(tmp_path: Path) -> None:
    first = SQLiteStore.open(tmp_path)
    second = SQLiteStore.open_read_only(tmp_path)
    try:
        assert first.path == second.path
        assert first.connection.execute(
            "SELECT singleton, schema_version, project_root FROM schema_metadata"
        ).fetchall() == [(1, 3, str(tmp_path.resolve()))]
        assert second.connection.execute(
            "SELECT count(*) FROM schema_metadata"
        ).fetchone() == (1,)
    finally:
        first.close()
        second.close()


def test_inspection_connection_is_read_only_and_pragmas_are_safe(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path)
    connection = store.connection
    before = _live_schema(store.path)
    assert connection.execute("PRAGMA query_only").fetchone() == (1,)
    assert connection.execute("PRAGMA busy_timeout").fetchone() == (5000,)
    assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    assert connection.execute("PRAGMA synchronous").fetchone() == (2,)
    for sql in ("CREATE TABLE injected(value)", "PRAGMA user_version = 9"):
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(sql)
    assert _live_schema(store.path) == before
    assert not Path(f"{store.path}-wal").exists()
    assert not Path(f"{store.path}-shm").exists()
    store.close()
    store.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with pytest.raises(RuntimeError, match="closed"):
        _ = store.connection


def test_migration_failure_rolls_back_ddl_and_closes_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentdeck.adapters.sqlite as adapter
    class TrackingConnection(sqlite3.Connection):
        closed = False
        def close(self) -> None:
            self.closed = True
            super().close()
    tracked: list[TrackingConnection] = []
    def connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, factory=TrackingConnection)
        tracked.append(connection)
        return connection
    import agentdeck.adapters.sqlite_migrations as migrations
    original = migrations.V2_DDL
    monkeypatch.setattr(adapter, "_connect_database", connect)
    monkeypatch.setattr(migrations, "V2_DDL", (*original, "BAD SQL"))
    with pytest.raises(sqlite3.DatabaseError):
        SQLiteStore.open(tmp_path)
    assert len(tracked) == 1 and tracked[0].closed
    with _raw(tmp_path / ".agentdeck" / "agentdeck.db") as connection:
        assert _tables(connection) == set()
    monkeypatch.setattr(migrations, "V2_DDL", original)
    recovered = SQLiteStore.open(tmp_path)
    assert recovered.connection.execute("SELECT schema_version FROM schema_metadata").fetchone() == (3,)
    recovered.close()


def test_precreated_zero_byte_database_initializes_current_schema(tmp_path: Path) -> None:
    database = tmp_path / ".agentdeck" / "agentdeck.db"
    database.parent.mkdir()
    database.touch()
    store = SQLiteStore.open(tmp_path)
    assert _tables(store.connection) == TABLES
    store.close()


@pytest.mark.parametrize("ddl", [
    "CREATE VIEW injected AS SELECT 1 AS value",
    "CREATE TABLE base(value); CREATE TRIGGER injected AFTER INSERT ON base BEGIN SELECT 1; END;",
])
def test_unknown_schema_object_without_metadata_is_preserved_and_rejected(
    tmp_path: Path, ddl: str
) -> None:
    database = tmp_path / ".agentdeck" / "agentdeck.db"
    database.parent.mkdir()
    with _raw(database) as connection:
        connection.executescript(ddl)
    before = _live_schema(database)
    with pytest.raises(StoreSchemaError):
        SQLiteStore.open(tmp_path)
    assert _live_schema(database) == before


@pytest.mark.parametrize("damage", ["higher", "missing"])
def test_invalid_version_in_wal_fails_without_changing_database(
    tmp_path: Path, damage: str
) -> None:
    database = _valid_database(tmp_path / damage)
    with _raw(database) as connection:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        sql = ("UPDATE schema_metadata SET schema_version = 4" if damage == "higher"
               else "DELETE FROM schema_metadata")
        connection.execute(sql)
    before = database.read_bytes()
    with pytest.raises(StoreSchemaError):
        SQLiteStore.open(database.parents[1])
    assert database.read_bytes() == before
    with _raw(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)


@pytest.mark.parametrize("damage_sql", [
    "DROP TABLE conversation_turns",
    "CREATE TABLE shadow_authority(value)",
    "ALTER TABLE tasks ADD COLUMN injected TEXT",
    "DROP INDEX idx_events_aggregate",
])
def test_live_schema_drift_fails_closed_without_repair(
    tmp_path: Path, damage_sql: str
) -> None:
    database = _valid_database(tmp_path / "project")
    with _raw(database) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(damage_sql)
    damaged = _live_schema(database)
    with pytest.raises(StoreSchemaError, match="drift|damaged"):
        SQLiteStore.open(database.parents[1])
    assert _live_schema(database) == damaged
    with _raw(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_live_constraint_drift_fails_closed_without_repair(tmp_path: Path) -> None:
    database = _valid_database(tmp_path / "project")
    with _raw(database) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name = 'commands'"
        ).fetchone()[0]
        changed = sql.replace(" CHECK (state IN ('started','completed','failed'))", "")
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute("UPDATE sqlite_schema SET sql=? WHERE name='commands'", (changed,))
        version = connection.execute("PRAGMA schema_version").fetchone()[0]
        connection.execute(f"PRAGMA schema_version = {version + 1}")
    damaged = _live_schema(database)
    with pytest.raises(StoreSchemaError, match="drift"):
        SQLiteStore.open(database.parents[1])
    assert _live_schema(database) == damaged


def test_database_copied_from_another_project_fails_unchanged(tmp_path: Path) -> None:
    source = _valid_database(tmp_path / "source")
    with _raw(source) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
    target = tmp_path / "target"
    target_database = target / ".agentdeck" / "agentdeck.db"
    target_database.parent.mkdir(parents=True)
    target_database.write_bytes(source.read_bytes())
    before = target_database.read_bytes()
    with pytest.raises(StoreSchemaError, match="project root"):
        SQLiteStore.open(target)
    assert target_database.read_bytes() == before
    with _raw(target_database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_replacement_before_connect_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentdeck.adapters.sqlite as adapter
    project = tmp_path / "project"
    database = _valid_database(project)
    with _raw(database) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
    spare = database.with_name("spare.db")
    spare.write_bytes(database.read_bytes())
    replacement_bytes = spare.read_bytes()
    replacement_inode = spare.stat().st_ino
    original = adapter._connect_database

    def replace_then_connect(path: Path) -> sqlite3.Connection:
        os.replace(spare, path)
        return original(path)

    monkeypatch.setattr(adapter, "_connect_database", replace_then_connect)
    with pytest.raises(StoreSchemaError, match="identity"):
        SQLiteStore.open(project)
    assert database.stat().st_ino == replacement_inode
    assert database.read_bytes() == replacement_bytes


def test_replacement_after_migrate_before_return_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentdeck.adapters.sqlite as adapter

    project = tmp_path / "project"
    database = _valid_database(project)
    spare = database.with_name("spare.db")
    spare.write_bytes(database.read_bytes())
    replacement_inode = spare.stat().st_ino
    monkeypatch.setattr(adapter, "_after_migrate", lambda path: os.replace(spare, path), raising=False)
    with pytest.raises(StoreSchemaError, match="identity"):
        SQLiteStore.open(project)
    assert database.stat().st_ino == replacement_inode


@pytest.mark.parametrize(
    ("phase", "kind"),
    [("connect", "symlink"), ("connect", "other-project"), ("return", "other-project")],
)
def test_inspection_replacement_never_exposes_external_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, kind: str
) -> None:
    import agentdeck.adapters.sqlite as adapter

    project = tmp_path / "project"
    database = _valid_database(project)
    if kind == "other-project":
        external = _valid_database(tmp_path / "other")
    else:
        external = tmp_path / "external.db"
        with _raw(external) as connection:
            connection.execute("CREATE TABLE external_marker(secret TEXT)")
            connection.execute("INSERT INTO external_marker VALUES ('never expose')")
    class TrackingConnection(sqlite3.Connection):
        closed = False
        def close(self) -> None:
            self.closed = True
            super().close()
    tracked: list[TrackingConnection] = []
    def replace() -> None:
        if kind == "symlink":
            database.unlink()
            database.symlink_to(external)
        else:
            os.replace(external, database)
    def connect(path: Path) -> sqlite3.Connection:
        if phase == "connect":
            replace()
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro", uri=True, factory=TrackingConnection
        )
        tracked.append(connection)
        return connection
    monkeypatch.setattr(adapter, "_connect_inspection_database", connect, raising=False)
    if phase == "return":
        monkeypatch.setattr(adapter, "_after_inspection_validation", lambda path: replace(), raising=False)
    store = SQLiteStore.open(project)
    try:
        with pytest.raises((StoreSchemaError, ValueError), match="identity|symlink|project"):
            _ = store.connection
        assert store._inspection is None
    finally:
        store.close()
    assert len(tracked) == 1 and tracked[0].closed


def test_analyze_internal_tables_do_not_change_authority_schema(tmp_path: Path) -> None:
    database = _valid_database(tmp_path / "project")
    with _raw(database) as connection:
        connection.execute("ANALYZE")
        assert "sqlite_stat1" in {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
    reopened = SQLiteStore.open(database.parents[1])
    reopened.close()


def test_schema_has_stable_keys_indexes_and_current_state_fields(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path)
    connection = store.connection
    assert {row[1] for row in connection.execute("PRAGMA table_info(tasks)") if row[5]} == {"task_id"}
    assert _foreign_keys(connection, "product_sessions") >= {("project_id", "projects", "project_id")}
    assert _foreign_keys(connection, "mission_versions") >= {("mission_id", "missions", "mission_id")}
    assert _foreign_keys(connection, "tasks") >= {
        ("mission_id", "mission_versions", "mission_id"),
        ("mission_version", "mission_versions", "version"),
    }
    assert _foreign_keys(connection, "attempts") >= {
        ("task_id", "tasks", "task_id"),
        ("agent_instance_id", "agent_instances", "instance_id"),
    }
    assert _foreign_keys(connection, "handoffs") >= {
        ("source_attempt_id", "attempts", "attempt_id"),
        ("target_task_id", "tasks", "task_id"),
    }
    assert {"state", "updated_at"} <= _columns(connection, "product_sessions")
    assert {"state", "current_version", "updated_at"} <= _columns(connection, "missions")
    assert {"state", "ordinal", "retryable", "effect_observed"} <= _columns(connection, "attempts")
    assert any("ordinal" in name for name in _indexes(connection, "attempts"))
    assert any("aggregate" in name for name in _indexes(connection, "events"))
    store.close()


def test_foreign_keys_and_state_constraints_are_enforced(tmp_path: Path) -> None:
    database = _valid_database(tmp_path / "project")
    with _raw(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                """INSERT INTO attempts (
                   attempt_id,task_id,ordinal,state,retryable,effect_observed,created_at,updated_at
                   ) VALUES ('att_orphan','tsk_missing',1,'pending',0,0,'now','now')"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(
                """INSERT INTO commands (command_id,command_kind,state,created_at)
                   VALUES ('cmd_bad','confirm','unknown','now')"""
            )


def test_schema_names_keep_secrets_and_raw_frames_out(tmp_path: Path) -> None:
    forbidden = {
        "api_key", "password", "access_token", "credential_value",
        "full_environment", "hidden_reasoning", "raw_protocol",
        "protocol_frame", "raw_terminal", "terminal_output",
    }
    store = SQLiteStore.open(tmp_path)
    connection = store.connection
    schema = " ".join(
        row[0] or "" for row in connection.execute(
            "SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL"
        )
    ).lower()
    all_columns = {column for table in TABLES for column in _columns(connection, table)}
    assert not forbidden.intersection(all_columns)
    assert not any(term in schema for term in forbidden)
    assert {
        "sanitized_content", "canonical_mission_facts", "canonical_task_facts",
        "canonical_handoff_facts", "canonical_evidence_facts",
        "canonical_result_facts", "canonical_payload_facts",
    } <= all_columns
    store.close()
