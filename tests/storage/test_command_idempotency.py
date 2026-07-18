from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from agentdeck.domain.events import DomainEvent
from agentdeck.storage.ownership import ProjectWriterLease
from agentdeck.storage.sqlite_store import (
    CommandConflict,
    CommandEnvelope,
    EntityChange,
    MutationDecision,
    MutationOutcome,
    MutationValidationError,
    RevisionConflict,
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


def _command(
    *,
    command_id: str = "cmd_1",
    expected_revision: int = 0,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id,
        kind="mission.noop",
        actor={"kind": "human", "id": "user_1"},
        payload={"mission_id": "mis_1", "items": [1, 2]},
        expected_revision=expected_revision,
        created_at="2026-07-18T00:00:00Z",
    )


def _decision(command: CommandEnvelope, *, event_id: str) -> MutationDecision:
    return MutationDecision(
        events=(
            DomainEvent.client_command(
                event_id=event_id,
                kind="mission_noop",
                command_id=command.command_id,
                expected_revision=command.expected_revision,
                actor=command.actor_dict(),
                payload={"accepted": True},
                created_at=command.created_at,
            ),
        ),
        result={"accepted": True},
    )


def test_exact_duplicate_replays_persisted_outcome_without_deciding(
    store: SQLiteMissionStore,
) -> None:
    first_command = _command()
    first = store.apply_command(
        first_command,
        lambda snapshot: _decision(first_command, event_id="evt_1"),
    )
    second_command = _command(command_id="cmd_2", expected_revision=1)
    store.apply_command(
        second_command,
        lambda snapshot: _decision(second_command, event_id="evt_2"),
    )

    def must_not_run(snapshot: object) -> MutationDecision:
        raise AssertionError("decision callback was called")

    replay = store.apply_command(first_command, must_not_run)

    assert replay == first
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (2,)
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (2,)
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone() == (2,)


@pytest.mark.parametrize(
    "changed",
    [
        {"kind": "mission.changed"},
        {"actor": {"kind": "agent", "id": "leader"}},
        {"payload": {"mission_id": "different"}},
        {"expected_revision": 1},
        {"created_at": "2026-07-18T00:00:01Z"},
    ],
)
def test_same_command_id_with_any_changed_bound_input_conflicts(
    store: SQLiteMissionStore,
    changed: dict[str, object],
) -> None:
    command = _command()
    store.apply_command(command, lambda snapshot: _decision(command, event_id="evt_1"))
    altered = replace(command, **changed)

    with pytest.raises(CommandConflict, match="^command input mismatch$") as raised:
        store.apply_command(altered, lambda snapshot: MutationDecision())

    assert raised.value.__cause__ is None
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (1,)
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (1,)


def test_stale_new_command_is_typed_conflict_and_writes_nothing(
    store: SQLiteMissionStore,
) -> None:
    command = _command()
    store.apply_command(command, lambda snapshot: _decision(command, event_id="evt_1"))
    stale = _command(command_id="cmd_stale", expected_revision=0)

    with pytest.raises(RevisionConflict, match="^stale project revision$") as raised:
        store.apply_command(stale, lambda snapshot: _decision(stale, event_id="evt_2"))

    assert raised.value.__cause__ is None
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (1,)
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (1,)
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone() == (1,)


def test_command_envelope_is_deeply_detached_frozen_and_canonical() -> None:
    actor = {"kind": "human", "claims": ["confirm"]}
    payload = {"mission_id": "mis_1", "nested": {"enabled": True}}
    command = CommandEnvelope(
        command_id="cmd_1",
        kind="mission.confirm",
        actor=actor,
        payload=payload,
        expected_revision=0,
        created_at="2026-07-18T00:00:00Z",
    )
    actor["kind"] = "changed"
    payload["mission_id"] = "changed"

    assert command.actor_dict() == {"claims": ["confirm"], "kind": "human"}
    assert command.payload_dict()["mission_id"] == "mis_1"
    assert command.input_hash.startswith("sha256:")
    assert len(command.input_hash) == 71
    with pytest.raises(FrozenInstanceError):
        command.kind = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        command.actor["kind"] = "changed"  # type: ignore[index]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"expected_revision": -1},
        {"expected_revision": 2**63},
        {"payload": {"ratio": 1.25}},
        {"payload": {1: "bad"}},
        {"actor": {}},
        {"created_at": ""},
    ],
)
def test_command_envelope_rejects_invalid_or_unbounded_input(
    kwargs: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "command_id": "cmd_1",
        "kind": "mission.noop",
        "actor": {"kind": "human"},
        "payload": {},
        "expected_revision": 0,
        "created_at": "2026-07-18T00:00:00Z",
    }
    values.update(kwargs)
    with pytest.raises(MutationValidationError, match="^command envelope invalid$"):
        CommandEnvelope(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "result",
    [
        {"ratio": 1.25},
        {"blob": b"bad"},
        {"tuple": (1, 2)},
        {"x": "a" * (65 * 1024)},
    ],
)
def test_mutation_decision_rejects_noncanonical_or_oversized_result(
    result: dict[str, object],
) -> None:
    with pytest.raises(MutationValidationError, match="^mutation decision invalid$"):
        MutationDecision(result=result)


def test_complete_outcome_is_canonical_size_bounded() -> None:
    with pytest.raises(MutationValidationError, match="^mutation outcome invalid$"):
        MutationOutcome(
            command_id="cmd_1",
            revision=1,
            event_ids=("evt_1",),
            result={"x": "a" * 65_480},
        )


def test_complete_entity_change_is_canonical_size_bounded() -> None:
    with pytest.raises(MutationValidationError, match="^entity change invalid$"):
        EntityChange.insert(
            "approvals",
            {
                "approval_id": "a" * 40_000,
                "subject_id": "b" * 40_000,
            },
        )


def test_entity_update_requires_exact_primary_key_and_cannot_rewrite_identity() -> None:
    with pytest.raises(MutationValidationError, match="^entity change invalid$"):
        EntityChange.update(
            "approvals",
            {"status": "approved"},
            where={"status": "pending"},
        )
    with pytest.raises(MutationValidationError, match="^entity change invalid$"):
        EntityChange.update(
            "approvals",
            {"approval_id": "apv_2"},
            where={"approval_id": "apv_1"},
        )

    change = EntityChange.update(
        "approvals",
        {"status": "approved", "decision_revision": 2},
        where={"approval_id": "apv_1"},
    )
    assert change.operation == "update"


@pytest.mark.parametrize("table", ["schema_migrations", "projects", "commands", "events"])
def test_entity_change_cannot_reach_authority_or_ledger_tables(table: str) -> None:
    with pytest.raises(MutationValidationError, match="^entity change invalid$"):
        EntityChange.insert(table, {"id": "forbidden"})


@pytest.mark.parametrize(
    ("table", "values", "where"),
    [
        (
            "mission_versions",
            {"specification_json": "{}"},
            {"mission_id": "mis_1", "version": 1},
        ),
        ("attempts", {"task_id": "tsk_2"}, {"attempt_id": "att_1"}),
        (
            "permissions",
            {"mission_id": "mis_2"},
            {"permission_id": "per_1"},
        ),
        ("evidence", {"task_id": "tsk_2"}, {"evidence_id": "evd_1"}),
        (
            "missions",
            {"created_revision": 2},
            {"mission_id": "mis_1"},
        ),
        (
            "artifacts",
            {"summary_json": "{}"},
            {"artifact_id": "art_1"},
        ),
        (
            "legacy_records",
            {"record_json": "{}"},
            {"record_id": "leg_1"},
        ),
    ],
)
def test_entity_update_rejects_lineage_spec_revision_and_insert_only_changes(
    table: str,
    values: dict[str, object],
    where: dict[str, object],
) -> None:
    with pytest.raises(MutationValidationError, match="^entity change invalid$") as raised:
        EntityChange.update(table, values, where=where)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("table", "values", "where"),
    [
        (
            "approvals",
            {"decision_revision": True},
            {"approval_id": "apv_1"},
        ),
        (
            "mission_versions",
            {"confirmed_revision": 2},
            {"mission_id": "mis_1", "version": True},
        ),
        (
            "sessions",
            {"last_sequence": True},
            {"session_id": "ses_1"},
        ),
        (
            "tasks",
            {"status": "running"},
            {"task_id": True},
        ),
    ],
)
def test_entity_change_rejects_bool_in_values_and_primary_key_where(
    table: str,
    values: dict[str, object],
    where: dict[str, object],
) -> None:
    with pytest.raises(MutationValidationError, match="^entity change invalid$") as raised:
        EntityChange.update(table, values, where=where)
    assert raised.value.__cause__ is None


def test_entity_insert_rejects_bool_even_for_integer_schema_column() -> None:
    with pytest.raises(MutationValidationError, match="^entity change invalid$") as raised:
        EntityChange.insert(
            "mission_versions",
            {
                "mission_id": "mis_1",
                "version": True,
                "specification_json": "{}",
                "proposal_provenance_json": "{}",
            },
        )
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "tamper",
    ["ghost_event", "event_revision", "command_revision"],
)
def test_exact_replay_fails_closed_when_persisted_ledger_does_not_match_outcome(
    store: SQLiteMissionStore,
    tamper: str,
) -> None:
    command = _command()
    store.apply_command(command, lambda snapshot: _decision(command, event_id="evt_1"))
    if tamper == "ghost_event":
        store._connection.execute(  # noqa: SLF001
            "INSERT INTO events("
            "event_id, project_id, project_revision, trigger_kind, kind, "
            "provenance_json, payload_json, command_id, adapter_event_id, "
            "internal_trigger_id, created_at"
            ") VALUES ("
            "'evt_ghost', 'prj_1', 1, 'client_command', 'ghost', '{}', '{}', "
            "'cmd_1', NULL, NULL, '2026-07-18T00:00:00Z'"
            ")"
        )
    elif tamper == "event_revision":
        store._connection.execute(  # noqa: SLF001
            "UPDATE events SET project_revision = 0 WHERE event_id = 'evt_1'"
        )
    else:
        store._connection.execute(  # noqa: SLF001
            "UPDATE commands SET completed_revision = 0 WHERE command_id = 'cmd_1'"
        )
    called = 0

    def must_not_decide(snapshot: object) -> MutationDecision:
        nonlocal called
        called += 1
        return MutationDecision()

    with pytest.raises(MutationValidationError, match="^command outcome invalid$"):
        store.apply_command(command, must_not_decide)

    assert called == 0
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (1,)
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (1,)
        expected_events = 2 if tamper == "ghost_event" else 1
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone() == (
            expected_events,
        )
