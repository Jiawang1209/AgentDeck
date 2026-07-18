from __future__ import annotations

import json
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agentdeck.domain.events import DomainEvent
from agentdeck.storage.sqlite_store import (
    MAX_IDENTITY_BYTES,
    MAX_IDENTITY_ROWS,
    MAX_SNAPSHOT_BYTES,
    MAX_SNAPSHOT_ROWS,
    CommandEnvelope,
    EntityChange,
    MutationDecision,
    MutationOutcome,
    MutationValidationError,
    ProjectMutationSnapshot,
    SQLiteMissionStore,
)
from agentdeck.storage.ownership import ProjectWriterLease


def _command(*, command_id: str = "cmd_1", expected_revision: int = 0) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id,
        kind="approval.record",
        actor={"kind": "human", "id": "user_1"},
        payload={"approval_id": "apv_1"},
        expected_revision=expected_revision,
        created_at="2026-07-18T00:00:00Z",
    )


def _event(command: CommandEnvelope, *, event_id: str = "evt_1") -> DomainEvent:
    return DomainEvent.client_command(
        event_id=event_id,
        kind="approval_recorded",
        command_id=command.command_id,
        expected_revision=command.expected_revision,
        actor=command.actor_dict(),
        payload={"approval_id": "apv_1"},
        created_at=command.created_at,
    )


def _approval_change(store: SQLiteMissionStore) -> EntityChange:
    return EntityChange.insert(
        "approvals",
        {
            "approval_id": "apv_1",
            "project_id": store.project_id,
            "subject_kind": "mission",
            "subject_id": "mis_1",
            "subject_digest": "sha256:" + "a" * 64,
            "status": "approved",
            "actor_json": json.dumps({"kind": "human"}),
            "decision_revision": 1,
        },
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


def _decision(store: SQLiteMissionStore, command: CommandEnvelope) -> MutationDecision:
    return MutationDecision(
        changes=(_approval_change(store),),
        events=(_event(command),),
        result={"approval_id": "apv_1", "status": "approved"},
    )


def _assert_empty_at_revision_zero(store: SQLiteMissionStore) -> None:
    with store.open_reader() as reader:
        assert reader.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert reader.execute("SELECT revision FROM projects").fetchone() == (0,)
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (0,)
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone() == (0,)
        assert reader.execute("SELECT COUNT(*) FROM approvals").fetchone() == (0,)


def test_apply_command_commits_entity_event_outcome_and_one_revision(
    store: SQLiteMissionStore,
) -> None:
    command = _command()

    outcome = store.apply_command(command, lambda snapshot: _decision(store, command))

    assert outcome == MutationOutcome(
        command_id="cmd_1",
        revision=1,
        event_ids=("evt_1",),
        result={"approval_id": "apv_1", "status": "approved"},
    )
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (1,)
        assert reader.execute(
            "SELECT status, accepted_revision, completed_revision FROM commands"
        ).fetchone() == ("completed", 1, 1)
        assert reader.execute(
            "SELECT event_id, project_revision, command_id FROM events"
        ).fetchone() == ("evt_1", 1, "cmd_1")
        assert reader.execute("SELECT approval_id FROM approvals").fetchone() == (
            "apv_1",
        )
        persisted = json.loads(
            reader.execute("SELECT outcome_json FROM commands").fetchone()[0]
        )
        assert persisted == outcome.to_dict()


def test_decision_snapshot_is_deeply_frozen_and_detached(
    store: SQLiteMissionStore,
) -> None:
    command = _command()
    seen: list[ProjectMutationSnapshot] = []

    def decide(snapshot: ProjectMutationSnapshot) -> MutationDecision:
        seen.append(snapshot)
        with pytest.raises(TypeError):
            snapshot.entities["approvals"] = ()  # type: ignore[index]
        return _decision(store, command)

    outcome = store.apply_command(command, decide)

    assert outcome.revision == 1
    assert seen[0].revision == 0
    with pytest.raises(FrozenInstanceError):
        seen[0].revision = 4  # type: ignore[misc]


def test_decision_exception_rolls_back_everything(store: SQLiteMissionStore) -> None:
    def explode(snapshot: ProjectMutationSnapshot) -> MutationDecision:
        raise RuntimeError("private callback detail")

    with pytest.raises(RuntimeError, match="private callback detail"):
        store.apply_command(_command(), explode)

    _assert_empty_at_revision_zero(store)


def test_duplicate_second_event_rolls_back_first_event_and_entity(
    store: SQLiteMissionStore,
) -> None:
    command = _command()
    first = _event(command)
    second = _event(command, event_id="evt_2")
    store._connection.execute(  # noqa: SLF001
        "CREATE TEMP TRIGGER inject_second_event_failure "
        "BEFORE INSERT ON events WHEN NEW.event_id = 'evt_2' "
        "BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )

    try:
        with pytest.raises(sqlite3.IntegrityError):
            store.apply_command(
                command,
                lambda snapshot: MutationDecision(
                    changes=(_approval_change(store),),
                    events=(first, second),
                    result={},
                ),
            )
    finally:
        store._connection.execute(  # noqa: SLF001
            "DROP TRIGGER temp.inject_second_event_failure"
        )

    _assert_empty_at_revision_zero(store)


def test_authorizer_failure_before_event_rolls_back_and_store_remains_usable(
    store: SQLiteMissionStore,
) -> None:
    command = _command()

    def deny_events(action: int, arg1: str | None, *_: object) -> int:
        if action == sqlite3.SQLITE_INSERT and arg1 == "events":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    store._connection.set_authorizer(deny_events)  # noqa: SLF001
    try:
        with pytest.raises(sqlite3.DatabaseError):
            store.apply_command(command, lambda snapshot: _decision(store, command))
    finally:
        store._connection.set_authorizer(None)  # noqa: SLF001

    _assert_empty_at_revision_zero(store)
    assert store.apply_command(command, lambda snapshot: _decision(store, command)).revision == 1


def test_authorizer_failure_before_revision_rolls_back_and_store_remains_usable(
    store: SQLiteMissionStore,
) -> None:
    command = _command()

    def deny_revision(action: int, arg1: str | None, arg2: str | None, *_: object) -> int:
        if action == sqlite3.SQLITE_UPDATE and arg1 == "projects" and arg2 == "revision":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    store._connection.set_authorizer(deny_revision)  # noqa: SLF001
    try:
        with pytest.raises(sqlite3.DatabaseError):
            store.apply_command(command, lambda snapshot: _decision(store, command))
    finally:
        store._connection.set_authorizer(None)  # noqa: SLF001

    _assert_empty_at_revision_zero(store)
    assert store.apply_command(command, lambda snapshot: _decision(store, command)).revision == 1


def test_invalid_client_event_provenance_rolls_back(store: SQLiteMissionStore) -> None:
    command = _command()
    wrong = DomainEvent.client_command(
        event_id="evt_wrong",
        kind="approval_recorded",
        command_id="cmd_other",
        expected_revision=0,
        actor=command.actor_dict(),
        payload={},
        created_at=command.created_at,
    )

    with pytest.raises(ValueError, match="mutation decision invalid"):
        store.apply_command(
            command,
            lambda snapshot: MutationDecision(events=(wrong,), result={}),
        )

    _assert_empty_at_revision_zero(store)


def test_outcome_serialization_failure_rolls_back_prior_entity_and_event(
    store: SQLiteMissionStore,
) -> None:
    command = _command()

    with pytest.raises(ValueError, match="mutation outcome invalid"):
        store.apply_command(
            command,
            lambda snapshot: MutationDecision(
                changes=(_approval_change(store),),
                events=(_event(command),),
                result={"x": "a" * 65_480},
            ),
        )

    _assert_empty_at_revision_zero(store)


def test_nested_apply_poisoning_fails_outer_without_partial_rows(
    store: SQLiteMissionStore,
) -> None:
    outer = _command(command_id="cmd_outer")
    inner = _command(command_id="cmd_inner")

    def decide(snapshot: ProjectMutationSnapshot) -> MutationDecision:
        with pytest.raises(MutationValidationError, match="^nested mutation rejected$"):
            store.apply_command(
                inner,
                lambda nested: _decision(store, inner),
            )
        return _decision(store, outer)

    with pytest.raises(MutationValidationError, match="^nested mutation rejected$"):
        store.apply_command(outer, decide)

    _assert_empty_at_revision_zero(store)
    assert store.apply_command(
        outer,
        lambda snapshot: _decision(store, outer),
    ).revision == 1


def test_callback_cannot_end_owned_transaction_and_fall_into_autocommit(
    store: SQLiteMissionStore,
) -> None:
    command = _command(command_id="cmd_transaction")

    def decide(snapshot: ProjectMutationSnapshot) -> MutationDecision:
        store._connection.rollback()  # noqa: SLF001
        return _decision(store, command)

    with pytest.raises(
        MutationValidationError,
        match="^mutation transaction invalid$",
    ):
        store.apply_command(command, decide)

    _assert_empty_at_revision_zero(store)
    assert store.apply_command(
        command,
        lambda snapshot: _decision(store, command),
    ).revision == 1


def test_post_change_snapshot_must_remain_readable_and_failure_is_atomic(
    store: SQLiteMissionStore,
) -> None:
    first = _command(command_id="cmd_first")
    first_decision = MutationDecision(
        changes=tuple(
            EntityChange.insert(
                "learning",
                {
                    "learning_id": f"lrn_{index}",
                    "project_id": store.project_id,
                    "source_evidence_id": None,
                    "review_json": "a" * 60_000,
                    "application_json": None,
                    "created_revision": 1,
                },
            )
            for index in range(8)
        ),
        events=(_event(first, event_id="evt_first"),),
        result={},
    )
    assert store.apply_command(first, lambda snapshot: first_decision).revision == 1

    oversized = _command(command_id="cmd_oversized", expected_revision=1)
    with pytest.raises(MutationValidationError, match="^mutation snapshot invalid$"):
        store.apply_command(
            oversized,
            lambda snapshot: MutationDecision(
                changes=(
                    EntityChange.update(
                        "learning",
                        {"application_json": "b" * 60_000},
                        where={"learning_id": "lrn_0"},
                    ),
                ),
                events=(_event(oversized, event_id="evt_oversized"),),
                result={},
            ),
        )

    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (1,)
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (1,)
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone() == (1,)
        assert reader.execute(
            "SELECT length(review_json), application_json FROM learning "
            "WHERE learning_id = 'lrn_0'"
        ).fetchone() == (60_000, None)
        assert reader.execute("SELECT COUNT(*) FROM learning").fetchone() == (8,)

    third = _command(command_id="cmd_third", expected_revision=1)
    assert store.apply_command(
        third,
        lambda snapshot: MutationDecision(
            changes=(
                EntityChange.update(
                    "learning",
                    {"application_json": "applied"},
                    where={"learning_id": "lrn_0"},
                ),
            ),
            events=(_event(third, event_id="evt_third"),),
            result={},
        ),
    ).revision == 2


def test_snapshot_rows_are_stably_ordered_by_declared_primary_key(
    store: SQLiteMissionStore,
) -> None:
    first = _command(command_id="cmd_insert")

    def approval(approval_id: str) -> EntityChange:
        return EntityChange.insert(
            "approvals",
            {
                "approval_id": approval_id,
                "project_id": store.project_id,
                "subject_kind": "mission",
                "subject_id": approval_id,
                "subject_digest": "sha256:" + "a" * 64,
                "status": "pending",
                "actor_json": None,
                "decision_revision": None,
            },
        )

    store.apply_command(
        first,
        lambda snapshot: MutationDecision(
            changes=(approval("apv_z"), approval("apv_a"), approval("apv_m")),
            events=(_event(first, event_id="evt_insert"),),
            result={},
        ),
    )
    second = _command(command_id="cmd_observe", expected_revision=1)
    observed: list[str] = []

    def observe(snapshot: ProjectMutationSnapshot) -> MutationDecision:
        observed.extend(
            str(row["approval_id"]) for row in snapshot.entities["approvals"]
        )
        assert [row["approval_id"] for row in snapshot.identities["approvals"]] == [
            "apv_a",
            "apv_m",
            "apv_z",
        ]
        with pytest.raises(TypeError):
            snapshot.identities["approvals"] = ()  # type: ignore[index]
        with pytest.raises(TypeError):
            snapshot.identities["approvals"][0]["approval_id"] = "changed"  # type: ignore[index]
        return MutationDecision(events=(_event(second, event_id="evt_observe"),))

    store.apply_command(second, observe)
    assert observed == ["apv_a", "apv_m", "apv_z"]


def test_identity_index_has_independent_row_and_byte_limits(monkeypatch) -> None:
    monkeypatch.setattr("agentdeck.storage.sqlite_store.MAX_IDENTITY_ROWS", 2)
    with pytest.raises(MutationValidationError, match="^mutation snapshot invalid$"):
        ProjectMutationSnapshot(
            project_id="prj_1",
            revision=0,
            authority_state="legacy",
            entities={},
            identities={
                "tasks": [
                    {"task_id": "tsk_a"},
                    {"task_id": "tsk_b"},
                    {"task_id": "tsk_c"},
                ]
            },
        )

    monkeypatch.setattr("agentdeck.storage.sqlite_store.MAX_IDENTITY_ROWS", MAX_IDENTITY_ROWS)
    monkeypatch.setattr("agentdeck.storage.sqlite_store.MAX_IDENTITY_BYTES", 8)
    with pytest.raises(MutationValidationError, match="^mutation snapshot invalid$"):
        ProjectMutationSnapshot(
            project_id="prj_1",
            revision=0,
            authority_state="legacy",
            entities={},
            identities={"tasks": [{"task_id": "tsk_too_large"}]},
        )
    assert MAX_IDENTITY_BYTES > 8


def test_mutation_change_count_budget_rejects_before_unbounded_commit(
    store: SQLiteMissionStore,
) -> None:
    command = _command(command_id="cmd_many")
    changes = tuple(
        EntityChange.insert(
            "approvals",
            {
                "approval_id": f"apv_{index:05d}",
                "project_id": store.project_id,
                "subject_kind": "mission",
                "subject_id": "mis_1",
                "subject_digest": "sha256:" + "a" * 64,
                "status": "pending",
                "actor_json": None,
                "decision_revision": None,
            },
        )
        for index in range(MAX_SNAPSHOT_ROWS + 1)
    )

    with pytest.raises(MutationValidationError, match="^mutation decision invalid$"):
        store.apply_command(
            command,
            lambda snapshot: MutationDecision(changes=changes, result={}),
        )

    _assert_empty_at_revision_zero(store)


def test_terminal_row_batch_is_bounded_before_any_sql_write(
    store: SQLiteMissionStore,
) -> None:
    command = _command(command_id="cmd_terminal_overflow")

    def decide(snapshot: ProjectMutationSnapshot) -> MutationDecision:
        changes: list[EntityChange] = []
        for index in range(9):
            mission_id = f"mis_terminal_{index}"
            changes.extend(
                (
                    EntityChange.insert(
                        "missions",
                        {
                            "mission_id": mission_id,
                            "project_id": store.project_id,
                            "current_version": 1,
                            "status": "completed",
                            "created_revision": 1,
                            "updated_revision": 1,
                        },
                    ),
                    EntityChange.insert(
                        "mission_versions",
                        {
                            "mission_id": mission_id,
                            "version": 1,
                            "specification_json": "x" * 60_000,
                            "authorization_digest": None,
                            "proposal_provenance_json": "{}",
                            "confirmed_revision": None,
                        },
                    ),
                )
            )
        return MutationDecision(changes=tuple(changes))

    with pytest.raises(MutationValidationError, match="^mutation decision invalid$") as raised:
        store.apply_command(command, decide)

    assert raised.value.__cause__ is None
    _assert_empty_at_revision_zero(store)


def test_terminal_row_batch_below_aggregate_limit_can_commit(
    store: SQLiteMissionStore,
) -> None:
    command = _command(command_id="cmd_terminal_bounded")

    def decide(snapshot: ProjectMutationSnapshot) -> MutationDecision:
        changes: list[EntityChange] = []
        for index in range(8):
            mission_id = f"mis_terminal_{index}"
            changes.extend(
                (
                    EntityChange.insert(
                        "missions",
                        {
                            "mission_id": mission_id,
                            "project_id": store.project_id,
                            "current_version": 1,
                            "status": "completed",
                            "created_revision": 1,
                            "updated_revision": 1,
                        },
                    ),
                    EntityChange.insert(
                        "mission_versions",
                        {
                            "mission_id": mission_id,
                            "version": 1,
                            "specification_json": "x" * 60_000,
                            "authorization_digest": None,
                            "proposal_provenance_json": "{}",
                            "confirmed_revision": None,
                        },
                    ),
                )
            )
        return MutationDecision(changes=tuple(changes))

    outcome = store.apply_command(command, decide)

    assert outcome.revision == 1
    assert MAX_SNAPSHOT_BYTES == 512 * 1024
    with store.open_reader() as reader:
        assert reader.execute("SELECT COUNT(*) FROM missions").fetchone() == (8,)
        assert reader.execute("SELECT COUNT(*) FROM mission_versions").fetchone() == (
            8,
        )
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (1,)
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone() == (0,)


def test_invalid_insert_primary_key_inside_decision_writes_nothing(
    store: SQLiteMissionStore,
) -> None:
    command = _command(command_id="cmd_invalid_pk")

    with pytest.raises(MutationValidationError, match="^entity change invalid$"):
        store.apply_command(
            command,
            lambda snapshot: MutationDecision(
                changes=(EntityChange.insert("missions", {"status": "proposed"}),),
            ),
        )

    _assert_empty_at_revision_zero(store)


def test_client_event_timestamp_must_match_command_before_first_commit(
    store: SQLiteMissionStore,
) -> None:
    command = _command(command_id="cmd_time")
    mismatched = DomainEvent.client_command(
        event_id="evt_time",
        kind="approval_recorded",
        command_id=command.command_id,
        expected_revision=command.expected_revision,
        actor=command.actor_dict(),
        payload={},
        created_at="2026-07-18T00:00:01Z",
    )

    with pytest.raises(MutationValidationError, match="^mutation decision invalid$"):
        store.apply_command(
            command,
            lambda snapshot: MutationDecision(events=(mismatched,)),
        )
    _assert_empty_at_revision_zero(store)

    aligned = _event(command, event_id="evt_time")
    first = store.apply_command(
        command,
        lambda snapshot: MutationDecision(events=(aligned,)),
    )

    def must_not_decide(snapshot: object) -> MutationDecision:
        raise AssertionError("exact replay called decision")

    assert store.apply_command(command, must_not_decide) == first
