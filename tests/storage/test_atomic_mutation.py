from __future__ import annotations

import json
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agentdeck.domain.events import DomainEvent
from agentdeck.storage.sqlite_store import (
    CommandEnvelope,
    EntityChange,
    MutationDecision,
    MutationOutcome,
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
