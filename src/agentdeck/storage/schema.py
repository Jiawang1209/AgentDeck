"""Shadow-mirror schema v1 for the SQLite migration quarantine phase.

Generic P1-style shape (json payload columns, no per-collection DDL): the
mirror only has to reproduce the JSON state faithfully for shadow-read
comparison; normalized per-collection tables arrive with later phases. The
pinned fingerprint is verified at enable time and by `storage shadow-status`
(see docs/superpowers/specs/2026-07-26-sqlite-migration-route.md).
"""

from __future__ import annotations

import sqlite3

from agentdeck.storage.fingerprint import schema_fingerprint

SCHEMA_V1 = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE records (
    collection TEXT NOT NULL,
    record_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (collection, record_id)
);
CREATE INDEX idx_records_collection_position
    ON records(collection, position);
CREATE TABLE singletons (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
CREATE TABLE events (
    event_cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

SCHEMA_VERSION = "shadow-mirror/v1"

# Pinned at authoring time; recomputed in tests via schema_fingerprint() so a
# drifted DDL fails loudly instead of silently changing shape.
EXPECTED_SCHEMA_V1_FINGERPRINT = (
    "sha256:745af5fb4f984239f25c7b60598f33723e4b13a3b2d9ab28739d1e04dd4f917f"
)


def apply_schema_v1(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_V1)
    connection.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    connection.commit()


def verify_schema_v1(connection: sqlite3.Connection) -> bool:
    return schema_fingerprint(connection) == EXPECTED_SCHEMA_V1_FINGERPRINT
