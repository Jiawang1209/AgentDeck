from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

import agentdeck.adapters.sqlite_migrations as migrations
from agentdeck.adapters.sqlite import SQLiteStore, StoreSchemaError
from agentdeck.adapters.sqlite_migrations import (
    V1_SCHEMA_FINGERPRINT,
    V2_SCHEMA_FINGERPRINT,
    migrate_schema,
)
from agentdeck.adapters.sqlite_schema import V1_DDL, V3_SCHEMA_FINGERPRINT

from .sqlite_v1_fixture import (
    authority_snapshot,
    create_damaged_v1_database,
    create_v1_database,
)


V2_COLUMNS = (
    "leader_backend",
    "leader_model",
    "pending_exit_id",
    "pending_exit_attempt_id",
    "canonical_pending_exit_attempt_facts",
    "pending_exit_attempt_hash",
    "pending_exit_requested_at",
)
V2_TRIGGERS = (
    "trg_product_sessions_v2_closed_insert",
    "trg_product_sessions_v2_closed_update",
)


def test_known_schema_fingerprints_are_pinned_against_ddl_drift() -> None:
    assert V1_SCHEMA_FINGERPRINT == (
        "c2252ae6c5d83ac3c8f17cd856ffc491d259b8ad6378e449e0fef19f48c3d733"
    )
    assert V2_SCHEMA_FINGERPRINT == (
        "8a95c6f1f53d40e162f27ddbd9c2c102b912d174b9826ffdde02ac1ed49db009"
    )
    with pytest.raises(StoreSchemaError, match="fingerprint pin"):
        migrations.known_schema_fingerprint(
            (*V1_DDL, "CREATE TABLE accidental_drift(value TEXT)"),
            V1_SCHEMA_FINGERPRINT,
        )


def test_fresh_database_is_exact_current_schema(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path)
    try:
        metadata = store.connection.execute(
            "SELECT schema_version,schema_digest FROM schema_metadata"
        ).fetchone()
        assert metadata is not None and metadata[0] == 3
        assert metadata == (3, V3_SCHEMA_FINGERPRINT)
        columns = tuple(
            row[1] for row in store.connection.execute(
                "PRAGMA table_info(product_sessions)"
            )
        )
        assert columns[-7:] == V2_COLUMNS
        triggers = tuple(
            row[0] for row in store.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='trigger' ORDER BY name"
            )
        )
        assert triggers == V2_TRIGGERS
    finally:
        store.close()


DAMAGED_V1_CASES = (
    "unknown_version",
    "self_consistent_non_v1",
    "partial_v2",
    "missing_configure",
    "setup_with_configure",
    "wrong_command_id",
    "wrong_command_kind",
    "started_command",
    "malformed_configure",
    "conflicting_permission",
    "session_lineage",
    "goal_lineage",
    "mode_lineage",
    "leader_oversize",
    "model_oversize",
)


@pytest.mark.parametrize("damage", DAMAGED_V1_CASES)
def test_damaged_v1_is_rejected_without_authority_changes(
    tmp_path: Path, damage: str,
) -> None:
    database = create_damaged_v1_database(tmp_path, damage)
    before = authority_snapshot(database)
    with pytest.raises(StoreSchemaError):
        SQLiteStore.open(tmp_path)
    assert authority_snapshot(database) == before


def test_configure_command_with_invalid_completed_timestamp_rolls_back(
    tmp_path: Path,
) -> None:
    database = create_v1_database(tmp_path)
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute(
            """UPDATE commands SET created_at='not-a-time',completed_at=' '
               WHERE command_id='session:configure:ses_v1'"""
        )
    finally:
        connection.close()
    before = authority_snapshot(database)
    with pytest.raises(StoreSchemaError):
        SQLiteStore.open(tmp_path)
    assert authority_snapshot(database) == before


def test_setup_v1_rejects_unattributable_malformed_configure_candidate(
    tmp_path: Path,
) -> None:
    database = create_v1_database(
        tmp_path, state="setup", permission=None, goal=None,
        include_configure=False,
    )
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute(
            """INSERT INTO commands VALUES (
               'cmd_orphan','configure_product_session','completed','{','now','now')"""
        )
    finally:
        connection.close()
    before = authority_snapshot(database)
    with pytest.raises(StoreSchemaError):
        SQLiteStore.open(tmp_path)
    assert authority_snapshot(database) == before


def test_configured_v1_rejects_extra_malformed_configure_candidate(
    tmp_path: Path,
) -> None:
    database = create_v1_database(tmp_path)
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute(
            """INSERT INTO commands VALUES (
               'session:configure:orphan','configure_product_session',
               'completed','{','now','now')"""
        )
    finally:
        connection.close()
    before = authority_snapshot(database)
    with pytest.raises(StoreSchemaError):
        SQLiteStore.open(tmp_path)
    assert authority_snapshot(database) == before


@pytest.mark.parametrize(
    ("command_id", "command_kind"),
    [
        (sqlite3.Binary(b"session:configure:orphan"), "other_kind"),
        ("cmd_blob_kind", sqlite3.Binary(b"configure_product_session")),
    ],
)
def test_v1_rejects_blob_configure_markers_without_authority_changes(
    tmp_path: Path, command_id: object, command_kind: object,
) -> None:
    database = create_v1_database(tmp_path)
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute(
            "INSERT INTO commands VALUES (?,?,'completed','{}','now','now')",
            (command_id, command_kind),
        )
    finally:
        connection.close()
    before = authority_snapshot(database)
    with pytest.raises(StoreSchemaError):
        SQLiteStore.open(tmp_path)
    assert authority_snapshot(database) == before


def test_unrelated_blob_command_is_not_coerced_into_configure_candidate(
    tmp_path: Path,
) -> None:
    database = create_v1_database(tmp_path)
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute(
            "INSERT INTO commands VALUES (?,?,'completed','{}','now','now')",
            (sqlite3.Binary(b"arbitrary-id"), sqlite3.Binary(b"other-kind")),
        )
    finally:
        connection.close()
    store = SQLiteStore.open(tmp_path)
    try:
        assert store.connection.execute(
            """SELECT typeof(command_id),typeof(command_kind) FROM commands
               WHERE typeof(command_id)='blob'"""
        ).fetchone() == ("blob", "blob")
    finally:
        store.close()


def test_configure_candidate_classification_streams_command_ledger() -> None:
    class IterationOnlyCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def __iter__(self):
            return iter(self.cursor)

        def fetchall(self):
            raise AssertionError("command ledger classification must not fetchall")

    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, parameters=()):
            cursor = self.connection.execute(sql, parameters)
            if "FROM commands ORDER BY command_id" in " ".join(sql.split()):
                return IterationOnlyCursor(cursor)
            return cursor

    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.execute(
            """CREATE TABLE commands (
               command_id,command_kind,state,canonical_result_facts,
               created_at,completed_at)"""
        )
        connection.executemany(
            "INSERT INTO commands VALUES (?,?,?,?,?,?)",
            [
                ("cmd_unrelated", "other_kind", "completed", "x" * 4096,
                 "now", "now"),
                ("session:configure:ses_1", "configure_product_session",
                 "completed", "{}", "now", "now"),
                ("cmd_unrelated_2", "other_kind", "completed", "y" * 4096,
                 "now", "now"),
            ],
        )
        rows = migrations._configure_rows(ConnectionProxy(connection))
        assert [row[0] for row in rows] == ["session:configure:ses_1"]
    finally:
        connection.close()


@pytest.mark.parametrize(
    "damage",
    ["non_ascii_digest", "permission_array", "mode_array", "extreme_offset"],
)
def test_stored_v1_validation_has_stable_schema_error_boundary(
    tmp_path: Path, damage: str,
) -> None:
    database = create_v1_database(tmp_path)
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        if damage == "non_ascii_digest":
            connection.execute(
                "UPDATE schema_metadata SET schema_digest=? WHERE singleton=1",
                ("é" * 64,),
            )
        elif damage in {"permission_array", "mode_array"}:
            raw = connection.execute(
                """SELECT canonical_result_facts FROM commands
                   WHERE command_id='session:configure:ses_v1'"""
            ).fetchone()[0]
            result = json.loads(raw)
            result["permission" if damage == "permission_array" else "mode"] = []
            connection.execute(
                """UPDATE commands SET canonical_result_facts=?
                   WHERE command_id='session:configure:ses_v1'""",
                (json.dumps(
                    result, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False,
                ),),
            )
        else:
            connection.execute(
                """UPDATE commands SET created_at=?,completed_at=?
                   WHERE command_id='session:configure:ses_v1'""",
                ("0001-01-01T00:00:00+14:00",) * 2,
            )
    finally:
        connection.close()
    before = authority_snapshot(database)
    with pytest.raises(StoreSchemaError):
        SQLiteStore.open(tmp_path)
    assert authority_snapshot(database) == before


class _FailCommitOnce(sqlite3.Connection):
    fail_commit = True

    def execute(self, sql, parameters=()):
        if sql == "COMMIT" and self.fail_commit:
            self.fail_commit = False
            raise sqlite3.OperationalError("injected real COMMIT failure")
        return super().execute(sql, parameters)


def test_real_commit_failure_rolls_back_all_migration_authority(
    tmp_path: Path,
) -> None:
    database = create_v1_database(tmp_path)
    before = authority_snapshot(database)
    connection = sqlite3.connect(
        database, isolation_level=None, factory=_FailCommitOnce
    )
    try:
        with pytest.raises(sqlite3.OperationalError, match="COMMIT failure"):
            migrate_schema(connection, tmp_path.resolve())
        assert not connection.in_transaction
    finally:
        connection.close()
    assert authority_snapshot(database) == before


def test_valid_current_schema_reopen_has_exact_digest_and_zero_migration_writes(
    tmp_path: Path,
) -> None:
    created = SQLiteStore.open(tmp_path)
    created.close()
    reopened = SQLiteStore.open(tmp_path)
    try:
        assert reopened._writer.total_changes == 0
        assert reopened.connection.execute(
            "SELECT schema_version,schema_digest FROM schema_metadata"
        ).fetchone() == (3, V3_SCHEMA_FINGERPRINT)
    finally:
        reopened.close()


def test_exact_configured_v1_migrates_and_backfills_identity(
    tmp_path: Path,
) -> None:
    database = create_v1_database(tmp_path)
    assert authority_snapshot(database)[1] == (
        (1, 1, V1_SCHEMA_FINGERPRINT, str(tmp_path.resolve())),
    )
    store = SQLiteStore.open(tmp_path)
    try:
        assert store.connection.execute(
            "SELECT schema_version FROM schema_metadata"
        ).fetchone() == (3,)
        assert store.connection.execute(
            "SELECT leader_backend,leader_model FROM product_sessions"
        ).fetchone() == ("codex-cli", "native-default")
    finally:
        store.close()


def test_fresh_and_migrated_databases_have_identical_current_authority(
    tmp_path: Path,
) -> None:
    fresh_root = tmp_path / "fresh"
    fresh_root.mkdir()
    fresh = SQLiteStore.open(fresh_root)
    fresh_database = fresh.path
    fresh.close()
    migrated_root = tmp_path / "migrated"
    migrated_database = create_v1_database(migrated_root)
    migrated = SQLiteStore.open(migrated_root)
    migrated.close()
    fresh_snapshot = authority_snapshot(fresh_database)
    migrated_snapshot = authority_snapshot(migrated_database)
    assert fresh_snapshot[0] == migrated_snapshot[0]
    assert fresh_snapshot[1][0][1:3] == migrated_snapshot[1][0][1:3] == (
        3, V3_SCHEMA_FINGERPRINT,
    )


@pytest.mark.parametrize(
    ("state", "leader", "model", "pending_exit_id"),
    [
        ("ready", None, None, None),
        ("setup", "codex-cli", "native-default", None),
        ("ready", "codex-cli", None, None),
        ("ready", "codex-cli", "native-default", "exit_partial"),
    ],
)
def test_v2_triggers_reject_every_partial_closed_shape(
    tmp_path: Path,
    state: str,
    leader: str | None,
    model: str | None,
    pending_exit_id: str | None,
) -> None:
    store = SQLiteStore.open(tmp_path)
    connection = store._writer
    try:
        connection.execute(
            "INSERT INTO projects VALUES ('prj_trigger', ?, 'now')",
            (str(tmp_path.resolve()),),
        )
        with pytest.raises(sqlite3.IntegrityError, match="closed shape"):
            connection.execute(
                """INSERT INTO product_sessions (
                       session_id,project_id,state,permission_profile,pending_goal,
                       created_at,updated_at,leader_backend,leader_model,
                       pending_exit_id
                   ) VALUES ('ses_trigger','prj_trigger',?,NULL,NULL,'now','now',?,?,?)""",
                (state, leader, model, pending_exit_id),
            )
    finally:
        store.close()


def test_exact_setup_v1_migrates_without_inventing_configuration(
    tmp_path: Path,
) -> None:
    create_v1_database(
        tmp_path, state="setup", permission=None, goal=None,
        include_configure=False,
    )
    store = SQLiteStore.open(tmp_path)
    try:
        assert store.connection.execute(
            "SELECT schema_version FROM schema_metadata"
        ).fetchone() == (3,)
        assert store.connection.execute(
            "SELECT leader_backend,leader_model FROM product_sessions"
        ).fetchone() == (None, None)
    finally:
        store.close()
