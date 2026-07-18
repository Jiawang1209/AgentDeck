from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agentdeck.app.mission_service import MissionProposal, MissionService
from agentdeck.domain.authorization import AuthorizationEnvelope, ExternalEffectPolicy
from agentdeck.domain.mission import MissionVersion, TaskSpec
from agentdeck.storage.ownership import ProjectWriterLease
from agentdeck.storage.sqlite_store import (
    CommandEnvelope,
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


def _mission(*, mission_id: str = "mis_1") -> MissionVersion:
    return MissionVersion(
        mission_id=mission_id,
        version=1,
        goal="Implement and review the durable kernel",
        scope=("src/agentdeck",),
        exclusions=("global configuration",),
        tasks=(
            TaskSpec(
                task_id=f"tsk_{mission_id}_implementation",
                objective="Implement the bounded change",
                role="codex-worker",
                scope=("src/agentdeck",),
                acceptance_contribution=("implementation exists",),
                acceptance_criteria=("focused tests pass",),
                concurrency_keys=("repository",),
                retry_limit=1,
                budget_units=10,
            ),
            TaskSpec(
                task_id=f"tsk_{mission_id}_review",
                objective="Review the bounded change",
                role="claude-reviewer",
                scope=("src/agentdeck",),
                acceptance_contribution=("review completed",),
                acceptance_criteria=("no blocking finding",),
                dependencies=(f"tsk_{mission_id}_implementation",),
                concurrency_keys=("repository",),
                retry_limit=1,
                budget_units=5,
            ),
        ),
        acceptance_criteria=("implementation and review both complete",),
        constraints=("local only",),
        max_parallel_tasks=1,
        budget_units=20,
        ordered_routes=("codex", "claude"),
        expires_at=None,
        provenance_source="leader",
        provenance_id=f"turn_{mission_id}",
        metadata={"schema": "mission/v1"},
    )


def _authorization(mission: MissionVersion) -> AuthorizationEnvelope:
    return AuthorizationEnvelope(
        goal=mission.goal,
        semantic_scope=mission.scope,
        path_scope=("src/agentdeck",),
        exclusions=mission.exclusions,
        operations=("read", "write", "test"),
        allowed_agents=("codex", "claude"),
        allowed_roles=("codex-worker", "claude-reviewer"),
        external_effect_policy=ExternalEffectPolicy.DENY,
        max_attempts=4,
        max_retries=2,
        max_recoveries=1,
        budget_units=100,
        acceptance_criteria=mission.acceptance_criteria,
        ordered_routes=mission.ordered_routes,
        expires_at=None,
        metadata={"authority": "human-confirmation-required"},
    )


def _proposal(*, mission_id: str = "mis_1") -> MissionProposal:
    mission = _mission(mission_id=mission_id)
    return MissionProposal(
        mission_version=mission,
        authorization_envelope=_authorization(mission),
        leader_provenance={
            "provider": "codex-cli",
            "model": "gpt-5.5",
            "turn_id": f"turn_{mission_id}",
        },
    )


def _command(
    kind: str,
    payload: dict[str, object],
    *,
    command_id: str,
    expected_revision: int,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id,
        kind=kind,
        actor={"kind": "human", "id": "user_1"},
        payload=payload,
        expected_revision=expected_revision,
        created_at="2026-07-18T08:00:00Z",
    )


def _propose(service: MissionService, proposal: MissionProposal, *, revision: int = 0):
    command = _command(
        "mission.propose",
        {
            "mission_id": proposal.mission_version.mission_id,
            "version": proposal.mission_version.version,
            "authorization_digest": proposal.authorization_digest,
        },
        command_id="cmd_propose",
        expected_revision=revision,
    )
    return command, service.propose(command, proposal)


def test_propose_persists_exact_version_and_separate_leader_provenance(store) -> None:
    service = MissionService(store)
    proposal = _proposal()

    command, outcome = _propose(service, proposal)

    assert outcome.to_dict() == {
        "command_id": "cmd_propose",
        "revision": 1,
        "event_ids": [outcome.event_ids[0]],
        "result": {
            "authorization_digest": proposal.authorization_digest,
            "mission_id": "mis_1",
            "status": "proposed",
            "version": 1,
        },
    }
    with store.open_reader() as reader:
        mission = reader.execute(
            "SELECT current_version, status, created_revision, updated_revision "
            "FROM missions WHERE mission_id = 'mis_1'"
        ).fetchone()
        version = reader.execute(
            "SELECT specification_json, authorization_digest, "
            "proposal_provenance_json, confirmed_revision FROM mission_versions"
        ).fetchone()
        event = reader.execute(
            "SELECT command_id, kind, created_at FROM events"
        ).fetchone()
        task_count = reader.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert mission == (1, "proposed", 1, 1)
    specification = json.loads(version[0])
    provenance = json.loads(version[2])
    assert specification == {
        "authorization_digest": proposal.authorization_digest,
        "authorization_envelope": proposal.authorization_envelope.to_dict(),
        "mission_version": proposal.mission_version.to_dict(),
    }
    assert version[1:] == (proposal.authorization_digest, version[2], None)
    assert provenance == proposal.leader_provenance_dict()
    assert "leader_provenance" not in specification
    assert event == (command.command_id, "mission_proposed", command.created_at)
    assert task_count == 0


def test_confirm_exact_digest_creates_frozen_tasks_in_same_revision(store) -> None:
    service = MissionService(store)
    proposal = _proposal()
    _propose(service, proposal)
    command = _command(
        "mission.confirm",
        {
            "mission_id": "mis_1",
            "version": 1,
            "authorization_digest": proposal.authorization_digest,
        },
        command_id="cmd_confirm",
        expected_revision=1,
    )

    outcome = service.confirm(
        command,
        mission_id="mis_1",
        version=1,
        digest=proposal.authorization_digest,
    )

    assert outcome.result["status"] == "confirmed"
    assert outcome.revision == 2
    with store.open_reader() as reader:
        mission = reader.execute(
            "SELECT current_version, status, updated_revision FROM missions"
        ).fetchone()
        confirmed = reader.execute(
            "SELECT confirmed_revision FROM mission_versions"
        ).fetchone()
        tasks = reader.execute(
            "SELECT task_id, specification_json, status, created_revision, updated_revision "
            "FROM tasks ORDER BY rowid"
        ).fetchall()
    assert mission == (1, "confirmed", 2)
    assert confirmed == (2,)
    assert [row[0] for row in tasks] == [task.task_id for task in proposal.mission_version.tasks]
    assert [json.loads(row[1]) for row in tasks] == [
        task.to_dict() for task in proposal.mission_version.tasks
    ]
    assert all(row[2:] == ("pending", 2, 2) for row in tasks)


def test_wrong_digest_and_corrupted_stored_spec_fail_closed_without_write(store) -> None:
    service = MissionService(store)
    proposal = _proposal()
    _propose(service, proposal)
    wrong = "sha256:" + "f" * 64
    command = _command(
        "mission.confirm",
        {"mission_id": "mis_1", "version": 1, "authorization_digest": wrong},
        command_id="cmd_wrong",
        expected_revision=1,
    )
    with pytest.raises(ValueError, match="^authorization digest mismatch$"):
        service.confirm(command, mission_id="mis_1", version=1, digest=wrong)

    store._connection.execute(  # noqa: SLF001 - deterministic corruption injection
        "UPDATE mission_versions SET specification_json = ?",
        ('{"mission_version":{"version":1.0}}',),
    )
    store._connection.commit()  # noqa: SLF001
    exact = _command(
        "mission.confirm",
        {
            "mission_id": "mis_1",
            "version": 1,
            "authorization_digest": proposal.authorization_digest,
        },
        command_id="cmd_corrupt",
        expected_revision=1,
    )
    with pytest.raises(ValueError, match="^stored mission specification invalid$"):
        service.confirm(
            exact,
            mission_id="mis_1",
            version=1,
            digest=proposal.authorization_digest,
        )
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (1,)
        assert reader.execute("SELECT COUNT(*) FROM tasks").fetchone() == (0,)
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (1,)


def test_stale_revision_and_second_active_mission_are_rejected(store) -> None:
    service = MissionService(store)
    _propose(service, _proposal())
    stale_proposal = _proposal(mission_id="mis_2")
    stale = _command(
        "mission.propose",
        {
            "mission_id": "mis_2",
            "version": 1,
            "authorization_digest": stale_proposal.authorization_digest,
        },
        command_id="cmd_stale",
        expected_revision=0,
    )
    with pytest.raises(RevisionConflict, match="^stale project revision$"):
        service.propose(stale, stale_proposal)

    current = _command(
        "mission.propose",
        {
            "mission_id": "mis_2",
            "version": 1,
            "authorization_digest": stale_proposal.authorization_digest,
        },
        command_id="cmd_second",
        expected_revision=1,
    )
    with pytest.raises(ValueError, match="^mutating mission conflict$"):
        service.propose(current, stale_proposal)


def test_exact_duplicate_replays_and_different_second_confirmation_fails(store) -> None:
    service = MissionService(store)
    proposal = _proposal()
    propose_command, first = _propose(service, proposal)
    assert service.propose(propose_command, proposal) == first
    command = _command(
        "mission.confirm",
        {
            "mission_id": "mis_1",
            "version": 1,
            "authorization_digest": proposal.authorization_digest,
        },
        command_id="cmd_confirm",
        expected_revision=1,
    )
    first_confirmation = service.confirm(
        command, mission_id="mis_1", version=1, digest=proposal.authorization_digest
    )
    assert service.confirm(
        command, mission_id="mis_1", version=1, digest=proposal.authorization_digest
    ) == first_confirmation
    second = _command(
        "mission.confirm",
        {
            "mission_id": "mis_1",
            "version": 1,
            "authorization_digest": proposal.authorization_digest,
        },
        command_id="cmd_confirm_again",
        expected_revision=2,
    )
    with pytest.raises(ValueError, match="^mission confirmation invalid$"):
        service.confirm(
            second,
            mission_id="mis_1",
            version=1,
            digest=proposal.authorization_digest,
        )


def test_cancel_is_durable_terminal_and_does_not_delete_version_or_tasks(store) -> None:
    service = MissionService(store)
    proposal = _proposal()
    _propose(service, proposal)
    confirm = _command(
        "mission.confirm",
        {
            "mission_id": "mis_1",
            "version": 1,
            "authorization_digest": proposal.authorization_digest,
        },
        command_id="cmd_confirm",
        expected_revision=1,
    )
    service.confirm(
        confirm,
        mission_id="mis_1",
        version=1,
        digest=proposal.authorization_digest,
    )
    command = _command(
        "mission.cancel",
        {"mission_id": "mis_1"},
        command_id="cmd_cancel",
        expected_revision=2,
    )

    outcome = service.cancel(command, mission_id="mis_1")

    assert outcome.result == {"mission_id": "mis_1", "status": "cancelled"}
    with store.open_reader() as reader:
        assert reader.execute("SELECT status, updated_revision FROM missions").fetchone() == (
            "cancelled",
            3,
        )
        assert reader.execute("SELECT COUNT(*) FROM mission_versions").fetchone() == (1,)
        assert reader.execute("SELECT COUNT(*) FROM tasks").fetchone() == (2,)

    terminal = _command(
        "mission.cancel",
        {"mission_id": "mis_1"},
        command_id="cmd_cancel_again",
        expected_revision=3,
    )
    with pytest.raises(ValueError, match="^mission terminal$"):
        service.cancel(terminal, mission_id="mis_1")
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (3,)
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (3,)


def test_proposal_and_commands_are_closed_deeply_immutable_values(store) -> None:
    provenance = {"provider": "codex-cli", "nested": {"route": ["codex"]}}
    mission = _mission()
    proposal = MissionProposal(mission, _authorization(mission), provenance)
    provenance["nested"]["route"].append("claude")  # type: ignore[index,union-attr]
    assert proposal.leader_provenance_dict()["nested"] == {"route": ["codex"]}
    with pytest.raises(FrozenInstanceError):
        proposal.mission_version = _mission(mission_id="mis_2")  # type: ignore[misc]

    service = MissionService(store)
    bad_kind = _command(
        "mission.confirm",
        {
            "mission_id": "mis_1",
            "version": 1,
            "authorization_digest": proposal.authorization_digest,
        },
        command_id="cmd_bad_kind",
        expected_revision=0,
    )
    with pytest.raises(MutationValidationError, match="^mission command invalid$"):
        service.propose(bad_kind, proposal)


def test_leader_provider_and_model_are_not_authorization_authority() -> None:
    mission = _mission()
    envelope = _authorization(mission)
    codex = MissionProposal(
        mission,
        envelope,
        {"provider": "codex-cli", "model": "gpt-5.5"},
    )
    claude = MissionProposal(
        mission,
        envelope,
        {"provider": "claude-agent-acp", "model": "opus"},
    )

    assert codex.authorization_digest == claude.authorization_digest
    assert codex.leader_provenance_dict() != claude.leader_provenance_dict()


def test_bool_version_in_bound_command_payload_cannot_alias_integer(store) -> None:
    service = MissionService(store)
    proposal = _proposal()
    command = _command(
        "mission.propose",
        {
            "mission_id": "mis_1",
            "version": True,
            "authorization_digest": proposal.authorization_digest,
        },
        command_id="cmd_bool_version",
        expected_revision=0,
    )

    with pytest.raises(MutationValidationError, match="^mission command invalid$"):
        service.propose(command, proposal)


def test_leader_command_cannot_turn_its_own_proposal_into_authority(store) -> None:
    service = MissionService(store)
    proposal = _proposal()
    command = CommandEnvelope(
        command_id="cmd_leader_self_confirm",
        kind="mission.propose",
        actor={"kind": "leader", "id": "codex"},
        payload={
            "mission_id": "mis_1",
            "version": 1,
            "authorization_digest": proposal.authorization_digest,
        },
        expected_revision=0,
        created_at="2026-07-18T08:00:00Z",
    )

    with pytest.raises(MutationValidationError, match="^mission command invalid$"):
        service.propose(command, proposal)
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (0,)
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (0,)


@pytest.mark.parametrize(
    "provenance",
    [{}, {"score": 1.5}, {"opaque": b"secret"}, {"huge": "x" * 9000}],
)
def test_leader_provenance_is_required_canonical_and_bounded(provenance) -> None:
    mission = _mission()
    with pytest.raises(ValueError, match="^mission proposal invalid$"):
        MissionProposal(mission, _authorization(mission), provenance)
