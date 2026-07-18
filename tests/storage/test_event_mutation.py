from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agentdeck.domain.events import DomainEvent
from agentdeck.storage.ownership import ProjectWriterLease
from agentdeck.storage.sqlite_store import (
    EntityChange,
    EventConflict,
    EventMutationOutcome,
    MAX_ARCHIVED_LINEAGE_BYTES,
    MAX_ARCHIVED_LINEAGE_ROWS,
    MutationDecision,
    MutationValidationError,
    ProjectMutationSnapshot,
    SQLiteMissionStore,
)


@pytest.fixture
def store(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    lease = ProjectWriterLease.acquire(root)
    mission_store = SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
    try:
        yield mission_store
    finally:
        mission_store.close()
        lease.close()


def _internal(*, trigger_id: str = "int_1", revision: int = 0, payload=None):
    return DomainEvent.internal_trigger(
        event_id="evt_internal_1",
        kind="scheduler_tick",
        internal_trigger_id=trigger_id,
        source_revision=revision,
        source_snapshot_id=f"snapshot_{revision}",
        payload={} if payload is None else payload,
        created_at="2026-07-18T09:00:00Z",
    )


def _adapter(
    *,
    adapter_id: str = "adp_1",
    event_id: str = "evt_adapter_1",
    sequence: int = 1,
    payload=None,
):
    return DomainEvent.adapter_event(
        event_id=event_id,
        kind="worker_message",
        adapter_event_id=adapter_id,
        mission_id="mis_1",
        mission_version="1",
        task_id="tsk_1",
        attempt_id="att_1",
        session_id="ses_1",
        sequence=sequence,
        integrity_hash="sha256:" + "a" * 64,
        payload={} if payload is None else payload,
        created_at="2026-07-18T09:00:00Z",
    )


def _decision(store: SQLiteMissionStore, event: DomainEvent) -> MutationDecision:
    return MutationDecision(
        changes=(
            EntityChange.insert(
                "approvals",
                {
                    "approval_id": "apv_event",
                    "project_id": store.project_id,
                    "subject_kind": "project",
                    "subject_id": "prj_1",
                    "subject_digest": "sha256:" + "b" * 64,
                    "status": "approved",
                    "actor_json": json.dumps({"kind": "system"}),
                    "decision_revision": 1,
                },
            ),
        ),
        events=(event,),
        result={"status": "applied"},
    )


def test_internal_event_commits_changes_event_and_exactly_one_revision(store) -> None:
    event = _internal()

    outcome = store.apply_event(event, lambda snapshot: _decision(store, event))

    assert outcome == EventMutationOutcome(
        trigger_id="int_1", revision=1, event_ids=("evt_internal_1",)
    )
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (1,)
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (0,)
        assert reader.execute(
            "SELECT trigger_kind, internal_trigger_id, project_revision FROM events"
        ).fetchone() == ("internal_trigger", "int_1", 1)
        assert reader.execute("SELECT COUNT(*) FROM approvals").fetchone() == (1,)


def test_exact_adapter_duplicate_is_replayed_before_callback_and_changed_duplicate_conflicts(
    store,
) -> None:
    first = _adapter()
    calls = 0

    def decide(snapshot):
        nonlocal calls
        calls += 1
        return MutationDecision(events=(first,), result={})

    outcome = store.apply_event(first, decide)
    assert store.apply_event(first, lambda snapshot: pytest.fail("duplicate callback")) == outcome
    assert calls == 1

    changed = _adapter(payload={"changed": True})
    with pytest.raises(EventConflict, match="^adapter event input mismatch$"):
        store.apply_event(changed, lambda snapshot: MutationDecision(events=(changed,)))
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (1,)
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone() == (1,)


def test_internal_trigger_source_revision_is_cas_and_event_id_collision_is_typed(store) -> None:
    first = _internal()
    store.apply_event(first, lambda snapshot: MutationDecision(events=(first,)))
    changed_trigger = DomainEvent.internal_trigger(
        event_id="evt_internal_1",
        kind="changed",
        internal_trigger_id="int_1",
        source_revision=0,
        source_snapshot_id="snapshot_0",
        payload={"changed": True},
        created_at="2026-07-18T09:00:00Z",
    )
    with pytest.raises(EventConflict, match="^internal trigger input mismatch$"):
        store.apply_event(
            changed_trigger,
            lambda snapshot: pytest.fail("changed duplicate callback"),
        )
    stale = DomainEvent.internal_trigger(
        event_id="evt_internal_stale",
        kind="scheduler_tick",
        internal_trigger_id="int_stale",
        source_revision=0,
        source_snapshot_id="snapshot_0",
        payload={},
        created_at="2026-07-18T09:01:00Z",
    )
    with pytest.raises(MutationValidationError, match="^internal source revision conflict$"):
        store.apply_event(stale, lambda snapshot: MutationDecision(events=(stale,)))

    collision = DomainEvent.internal_trigger(
        event_id="evt_internal_1",
        kind="other",
        internal_trigger_id="int_2",
        source_revision=1,
        source_snapshot_id="snapshot_1",
        payload={},
        created_at="2026-07-18T09:02:00Z",
    )
    with pytest.raises(EventConflict, match="^event identity conflict$"):
        store.apply_event(collision, lambda snapshot: MutationDecision(events=(collision,)))


def test_client_command_events_are_rejected_and_callback_failure_rolls_back(store) -> None:
    client = DomainEvent.client_command(
        event_id="evt_client",
        kind="bad",
        command_id="cmd_1",
        expected_revision=0,
        actor={"kind": "human", "id": "user_1"},
        payload={},
        created_at="2026-07-18T09:00:00Z",
    )
    with pytest.raises(MutationValidationError, match="^event mutation invalid$"):
        store.apply_event(client, lambda snapshot: MutationDecision(events=(client,)))

    event = _adapter()
    with pytest.raises(RuntimeError, match="private failure"):
        store.apply_event(
            event,
            lambda snapshot: (_ for _ in ()).throw(RuntimeError("private failure")),
        )
    with store.open_reader() as reader:
        assert reader.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert reader.execute("SELECT revision FROM projects").fetchone() == (0,)
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone() == (0,)


def test_event_insert_failure_rolls_back_prior_change_and_store_is_reusable(store) -> None:
    event = _internal()
    store._connection.execute(  # noqa: SLF001 - deterministic failure injection
        "CREATE TEMP TRIGGER reject_event BEFORE INSERT ON events "
        "BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    try:
        with pytest.raises(sqlite3.IntegrityError):
            store.apply_event(event, lambda snapshot: _decision(store, event))
    finally:
        store._connection.execute("DROP TRIGGER temp.reject_event")  # noqa: SLF001

    with store.open_reader() as reader:
        assert reader.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert reader.execute("SELECT revision FROM projects").fetchone() == (0,)
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone() == (0,)
        assert reader.execute("SELECT COUNT(*) FROM approvals").fetchone() == (0,)
    assert store.apply_event(event, lambda snapshot: _decision(store, event)).revision == 1


def test_archived_lineage_is_independently_bounded_and_deeply_frozen() -> None:
    snapshot = ProjectMutationSnapshot(
        project_id="prj_1",
        revision=7,
        authority_state="sqlite_active",
        entities={},
        archived_lineage={
            "missions": [
                {
                    "mission_id": "mis_1",
                    "current_version": 1,
                    "status": "completed",
                }
            ],
            "mission_versions": [
                {
                    "mission_id": "mis_1",
                    "version": 1,
                    "confirmed_revision": 2,
                    "authorization_digest": "sha256:" + "a" * 64,
                }
            ],
            "tasks": [
                {
                    "task_id": "tsk_1",
                    "mission_id": "mis_1",
                    "mission_version": 1,
                    "status": "completed",
                }
            ],
            "attempts": [
                {"attempt_id": "att_1", "task_id": "tsk_1", "status": "completed"}
            ],
            "sessions": [
                {
                    "session_id": "ses_1",
                    "attempt_id": "att_1",
                    "last_sequence": 3,
                    "status": "completed",
                }
            ],
        },
    )
    assert snapshot.archived_lineage["sessions"][0]["last_sequence"] == 3
    with pytest.raises(TypeError):
        snapshot.archived_lineage["sessions"][0]["last_sequence"] = 4  # type: ignore[index]

    with pytest.raises(MutationValidationError, match="^mutation snapshot invalid$"):
        ProjectMutationSnapshot(
            project_id="prj_1",
            revision=0,
            authority_state="sqlite_active",
            entities={},
            archived_lineage={
                "missions": [
                    {
                        "mission_id": f"mis_{index}",
                        "current_version": 1,
                        "status": "completed",
                    }
                    for index in range(MAX_ARCHIVED_LINEAGE_ROWS + 1)
                ]
            },
        )


def test_adapter_snapshot_selects_only_exact_archived_lineage_over_large_history(
    store,
) -> None:
    mission_rows = [
        (f"mis_{index}", "prj_1", 1, "completed", 0, 0)
        for index in range(MAX_ARCHIVED_LINEAGE_ROWS + 9)
    ]
    version_rows = [
        (
            mission_id,
            1,
            "{}",
            "sha256:" + "a" * 64,
            "{}",
            0,
        )
        for mission_id, *_ in mission_rows
    ]
    store._connection.executemany(  # noqa: SLF001 - bounded history fixture
        "INSERT INTO missions(mission_id, project_id, current_version, status, "
        "created_revision, updated_revision) VALUES (?, ?, ?, ?, ?, ?)",
        mission_rows,
    )
    store._connection.executemany(  # noqa: SLF001 - bounded history fixture
        "INSERT INTO mission_versions(mission_id, version, specification_json, "
        "authorization_digest, proposal_provenance_json, confirmed_revision) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        version_rows,
    )
    store._connection.execute(  # noqa: SLF001 - bounded history fixture
        "INSERT INTO tasks(task_id, mission_id, mission_version, specification_json, "
        "status, created_revision, updated_revision) "
        "VALUES ('tsk_1', 'mis_1', 1, '{}', 'completed', 0, 0)"
    )
    store._connection.execute(  # noqa: SLF001 - bounded history fixture
        "INSERT INTO attempts(attempt_id, task_id, attempt_number, status, "
        "route_position, budget_json, started_revision, terminal_revision) "
        "VALUES ('att_1', 'tsk_1', 1, 'completed', 0, '{\"budget_units\":1}', 0, 0)"
    )
    store._connection.execute(  # noqa: SLF001 - bounded history fixture
        "INSERT INTO sessions(session_id, attempt_id, agent_id, model_id, transport, "
        "status, last_sequence, lease_json, reconciliation_json) "
        "VALUES ('ses_1', 'att_1', 'codex', NULL, 'fake', 'completed', 0, NULL, NULL)"
    )
    store._connection.commit()  # noqa: SLF001

    event = _adapter()
    observed: list[dict[str, tuple[dict[str, object], ...]]] = []

    def decide(snapshot):
        observed.append(
            {
                table: tuple(dict(row) for row in rows)
                for table, rows in snapshot.archived_lineage.items()
            }
        )
        assert sum(len(rows) for rows in snapshot.archived_lineage.values()) == 5
        return MutationDecision(
            changes=(
                EntityChange.update(
                    "sessions", {"last_sequence": 1}, where={"session_id": "ses_1"}
                ),
            ),
            events=(event,),
        )

    store.apply_event(event, decide)
    assert len(observed) == 1
    assert tuple(observed[0]) == (
        "missions",
        "mission_versions",
        "tasks",
        "attempts",
        "sessions",
    )
    assert observed[0]["missions"][0]["mission_id"] == "mis_1"

    internal = _internal(trigger_id="int_after_archive", revision=1)
    store.apply_event(
        internal,
        lambda snapshot: (
            pytest.fail("internal snapshot exposed archived lineage")
            if snapshot.archived_lineage
            else MutationDecision(events=(internal,))
        ),
    )

    payload = "x" * 60_000
    row_count = MAX_ARCHIVED_LINEAGE_BYTES // len(payload.encode("utf-8")) + 2
    with pytest.raises(MutationValidationError, match="^mutation snapshot invalid$"):
        ProjectMutationSnapshot(
            project_id="prj_1",
            revision=0,
            authority_state="sqlite_active",
            entities={},
            archived_lineage={
                "sessions": [
                    {
                        "session_id": f"ses_{index}",
                        "attempt_id": f"att_{index}",
                        "last_sequence": 0,
                        "status": payload,
                    }
                    for index in range(row_count)
                ]
            },
        )
