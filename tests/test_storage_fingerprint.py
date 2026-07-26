from __future__ import annotations

import sqlite3

from agentdeck.storage.fingerprint import schema_fingerprint

DDL = (
    "CREATE TABLE items ("
    " item_id TEXT PRIMARY KEY,"
    " payload_json TEXT NOT NULL,"
    " created_revision INTEGER NOT NULL);"
    "CREATE INDEX idx_items_revision ON items(created_revision);"
)


def _connect(script: str) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(script)
    return connection


def test_identical_schema_yields_identical_fingerprint() -> None:
    a, b = _connect(DDL), _connect(DDL)
    try:
        fa, fb = schema_fingerprint(a), schema_fingerprint(b)
        assert fa == fb
        assert fa.startswith("sha256:")
        assert len(fa) == len("sha256:") + 64
    finally:
        a.close()
        b.close()


def test_added_column_changes_fingerprint() -> None:
    a = _connect(DDL)
    b = _connect(DDL)
    try:
        b.execute("ALTER TABLE items ADD COLUMN note TEXT")
        assert schema_fingerprint(a) != schema_fingerprint(b)
    finally:
        a.close()
        b.close()


def test_added_index_changes_fingerprint() -> None:
    a = _connect(DDL)
    b = _connect(DDL)
    try:
        b.execute("CREATE INDEX idx_items_payload ON items(payload_json)")
        assert schema_fingerprint(a) != schema_fingerprint(b)
    finally:
        a.close()
        b.close()


def test_data_rows_do_not_change_fingerprint() -> None:
    a = _connect(DDL)
    b = _connect(DDL)
    try:
        b.execute("INSERT INTO items VALUES ('i1', '{}', 1)")
        assert schema_fingerprint(a) == schema_fingerprint(b)
    finally:
        a.close()
        b.close()
