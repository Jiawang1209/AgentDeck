from pathlib import Path
import sqlite3

import pytest

from agentdeck.adapters.sqlite import SQLiteStore, StoreSchemaError
from agentdeck.adapters.sqlite_migrations import migrate_schema
from product_kernel.sqlite_v1_fixture import create_v1_database


V3_TABLES = {"observer_cursors", "takeover_ownership"}


def _metadata(path: Path) -> tuple[int, str]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            "SELECT schema_version,schema_digest FROM schema_metadata"
        ).fetchone()
    finally:
        connection.close()


def test_fresh_database_is_exact_schema_v3(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path)
    try:
        assert store.connection.execute(
            "SELECT schema_version FROM schema_metadata"
        ).fetchone() == (3,)
        tables = {
            row[0] for row in store.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
        assert V3_TABLES <= tables
    finally:
        store.close()


@pytest.mark.parametrize("source_version", (1, 2))
def test_v1_and_v2_converge_to_fresh_v3(
    tmp_path: Path, source_version: int,
) -> None:
    migrated_root = tmp_path / "migrated"
    database = create_v1_database(migrated_root)
    if source_version == 2:
        connection = sqlite3.connect(database, isolation_level=None)
        try:
            migrate_schema(connection, migrated_root.resolve())
        finally:
            connection.close()
    migrated = SQLiteStore.open(migrated_root)
    fresh_root = tmp_path / "fresh"
    fresh_root.mkdir()
    fresh = SQLiteStore.open(fresh_root)
    try:
        assert _metadata(migrated.path)[0] == 3
        assert _metadata(migrated.path)[1] == _metadata(fresh.path)[1]
    finally:
        migrated.close()
        fresh.close()


def test_partial_v3_rolls_back_without_acceptance(tmp_path: Path) -> None:
    database = create_v1_database(tmp_path)
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        migrate_schema(connection, tmp_path.resolve())
        connection.execute(
            "CREATE TABLE observer_cursors(cursor_id TEXT PRIMARY KEY)"
        )
        before = tuple(connection.execute(
            "SELECT type,name,sql FROM sqlite_schema ORDER BY type,name"
        ))
    finally:
        connection.close()

    with pytest.raises(StoreSchemaError):
        SQLiteStore.open(tmp_path)

    connection = sqlite3.connect(database)
    try:
        assert tuple(connection.execute(
            "SELECT type,name,sql FROM sqlite_schema ORDER BY type,name"
        )) == before
    finally:
        connection.close()
