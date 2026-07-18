"""Schema v1 for the Durable Mission Kernel."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from functools import lru_cache


SCHEMA_VERSION = 1
AUTHORITY_STATES = frozenset(
    {
        "legacy_active",
        "sqlite_installed_quarantined",
        "sqlite_active",
    }
)

SCHEMA_TABLES = (
    "schema_migrations",
    "projects",
    "commands",
    "events",
    "missions",
    "mission_versions",
    "tasks",
    "attempts",
    "sessions",
    "permissions",
    "handoffs",
    "evidence",
    "approvals",
    "artifacts",
    "learning",
    "suggestions",
    "legacy_records",
)


_SCHEMA = """
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL
);

CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    authority_state TEXT NOT NULL CHECK (
        authority_state IN (
            'legacy_active',
            'sqlite_installed_quarantined',
            'sqlite_active'
        )
    ),
    authority_generation INTEGER NOT NULL DEFAULT 0 CHECK (authority_generation >= 0),
    configuration_identity TEXT,
    cutover_watermark INTEGER CHECK (cutover_watermark IS NULL OR cutover_watermark >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE commands (
    command_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    input_hash TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    status TEXT NOT NULL CHECK (status IN ('accepted', 'completed', 'rejected')),
    outcome_json TEXT,
    accepted_revision INTEGER CHECK (accepted_revision IS NULL OR accepted_revision >= 0),
    completed_revision INTEGER CHECK (completed_revision IS NULL OR completed_revision >= 0),
    actor_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE events (
    event_cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    project_revision INTEGER NOT NULL CHECK (project_revision >= 0),
    trigger_kind TEXT NOT NULL CHECK (
        trigger_kind IN ('client_command', 'adapter_event', 'internal_trigger')
    ),
    kind TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    command_id TEXT REFERENCES commands(command_id) ON DELETE RESTRICT,
    adapter_event_id TEXT UNIQUE,
    internal_trigger_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    CHECK (
        (
            trigger_kind = 'client_command'
            AND command_id IS NOT NULL
            AND adapter_event_id IS NULL
            AND internal_trigger_id IS NULL
        ) OR (
            trigger_kind = 'adapter_event'
            AND command_id IS NULL
            AND adapter_event_id IS NOT NULL
            AND internal_trigger_id IS NULL
        ) OR (
            trigger_kind = 'internal_trigger'
            AND command_id IS NULL
            AND adapter_event_id IS NULL
            AND internal_trigger_id IS NOT NULL
        )
    )
);

CREATE TABLE missions (
    mission_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    current_version INTEGER,
    status TEXT NOT NULL,
    created_revision INTEGER NOT NULL CHECK (created_revision >= 0),
    updated_revision INTEGER NOT NULL CHECK (updated_revision >= 0)
);

CREATE TABLE mission_versions (
    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version > 0),
    specification_json TEXT NOT NULL,
    authorization_digest TEXT,
    proposal_provenance_json TEXT NOT NULL,
    confirmed_revision INTEGER CHECK (confirmed_revision IS NULL OR confirmed_revision >= 0),
    PRIMARY KEY (mission_id, version)
);

CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    mission_version INTEGER NOT NULL,
    specification_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_revision INTEGER NOT NULL CHECK (created_revision >= 0),
    updated_revision INTEGER NOT NULL CHECK (updated_revision >= 0),
    FOREIGN KEY (mission_id, mission_version)
        REFERENCES mission_versions(mission_id, version) ON DELETE RESTRICT
);

CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    status TEXT NOT NULL,
    route_position INTEGER NOT NULL CHECK (route_position >= 0),
    budget_json TEXT NOT NULL,
    started_revision INTEGER NOT NULL CHECK (started_revision >= 0),
    terminal_revision INTEGER CHECK (terminal_revision IS NULL OR terminal_revision >= 0),
    UNIQUE (task_id, attempt_number)
);

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    agent_id TEXT NOT NULL,
    model_id TEXT,
    transport TEXT NOT NULL,
    status TEXT NOT NULL,
    last_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    lease_json TEXT,
    reconciliation_json TEXT
);

CREATE TABLE permissions (
    permission_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE RESTRICT,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_json TEXT,
    created_revision INTEGER NOT NULL CHECK (created_revision >= 0),
    decided_revision INTEGER CHECK (decided_revision IS NULL OR decided_revision >= 0)
);

CREATE TABLE handoffs (
    handoff_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE RESTRICT,
    source_task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    destination_task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    context_json TEXT NOT NULL,
    created_revision INTEGER NOT NULL CHECK (created_revision >= 0),
    accepted_revision INTEGER CHECK (accepted_revision IS NULL OR accepted_revision >= 0)
);

CREATE TABLE evidence (
    evidence_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    integrity_hash TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_revision INTEGER NOT NULL CHECK (created_revision >= 0)
);

CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    subject_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    actor_json TEXT,
    decision_revision INTEGER CHECK (decision_revision IS NULL OR decision_revision >= 0)
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    task_id TEXT REFERENCES tasks(task_id) ON DELETE RESTRICT,
    relative_path TEXT,
    content_hash TEXT NOT NULL,
    media_type TEXT,
    summary_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_revision INTEGER NOT NULL CHECK (created_revision >= 0)
);

CREATE TABLE learning (
    learning_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    source_evidence_id TEXT REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    review_json TEXT NOT NULL,
    application_json TEXT,
    created_revision INTEGER NOT NULL CHECK (created_revision >= 0)
);

CREATE TABLE suggestions (
    suggestion_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('memory', 'skill', 'improvement_mission')),
    status TEXT NOT NULL,
    proposed_hash TEXT NOT NULL,
    proposed_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_revision INTEGER NOT NULL CHECK (created_revision >= 0),
    applied_revision INTEGER CHECK (applied_revision IS NULL OR applied_revision >= 0)
);

CREATE TABLE legacy_records (
    record_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    collection TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    record_json TEXT NOT NULL,
    imported_revision INTEGER NOT NULL CHECK (imported_revision >= 0),
    UNIQUE (collection, source_identity, record_id)
);

CREATE INDEX commands_project_revision_idx
    ON commands(project_id, expected_revision, command_id);
CREATE INDEX events_project_cursor_idx
    ON events(project_id, event_cursor);
CREATE INDEX events_project_revision_idx
    ON events(project_id, project_revision, event_cursor);
CREATE INDEX missions_project_status_idx
    ON missions(project_id, status);
CREATE INDEX tasks_mission_status_idx
    ON tasks(mission_id, mission_version, status);
CREATE INDEX attempts_task_status_idx
    ON attempts(task_id, status, attempt_number);
CREATE INDEX sessions_attempt_status_idx
    ON sessions(attempt_id, status);
CREATE INDEX permissions_attempt_status_idx
    ON permissions(attempt_id, status);
CREATE INDEX handoffs_destination_status_idx
    ON handoffs(destination_task_id, status);
CREATE INDEX evidence_task_attempt_idx
    ON evidence(task_id, attempt_id);
"""


def apply_schema_v1(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    authority_state: str,
) -> None:
    """Install schema v1 into a new off-path database transaction."""

    connection.executescript(_SCHEMA)
    connection.execute(
        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
        (SCHEMA_VERSION, "durable_mission_v1", ""),
    )
    connection.execute(
        "INSERT INTO projects("
        "project_id, revision, authority_state, authority_generation, created_at"
        ") VALUES (?, 0, ?, 0, ?)",
        (project_id, authority_state, ""),
    )


def _quoted_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _pragma_rows(
    connection: sqlite3.Connection,
    pragma: str,
    name: str,
) -> list[list[object]]:
    quoted = _quoted_identifier(name)
    return [list(row) for row in connection.execute(f"PRAGMA {pragma}({quoted})")]


def schema_fingerprint(connection: sqlite3.Connection) -> str:
    """Hash the executable schema shape rather than database self-reporting."""

    objects = [
        {
            "type": row[0],
            "name": row[1],
            "table_name": row[2],
            "sql": row[3],
        }
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index') "
            "AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY type, name"
        )
    ]
    table_names = sorted(
        item["name"] for item in objects if item["type"] == "table"
    )
    tables: list[dict[str, object]] = []
    for table_name in table_names:
        index_list = _pragma_rows(connection, "index_list", table_name)
        index_details = [
            {
                "name": row[1],
                "columns": _pragma_rows(connection, "index_xinfo", row[1]),
            }
            for row in index_list
        ]
        index_details.sort(key=lambda item: str(item["name"]))
        tables.append(
            {
                "name": table_name,
                "columns": _pragma_rows(connection, "table_xinfo", table_name),
                "foreign_keys": _pragma_rows(
                    connection,
                    "foreign_key_list",
                    table_name,
                ),
                "indexes": index_list,
                "index_details": index_details,
            }
        )
    canonical = json.dumps(
        {"objects": objects, "tables": tables},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


@lru_cache(maxsize=1)
def expected_schema_fingerprint() -> str:
    """Build the trusted v1 reference independently from the on-disk store."""

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        apply_schema_v1(
            connection,
            project_id="reference-project",
            authority_state="sqlite_active",
        )
        return schema_fingerprint(connection)
    finally:
        connection.close()
