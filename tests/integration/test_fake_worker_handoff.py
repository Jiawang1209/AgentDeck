from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdeck.app.mission_service import (
    HandoffRequest,
    MissionProposal,
    MissionService,
    StartAttemptRequest,
    adapter_event_integrity_hash,
)
from agentdeck.domain.authorization import AuthorizationEnvelope, ExternalEffectPolicy
from agentdeck.domain.events import DomainEvent
from agentdeck.domain.mission import MissionVersion, TaskSpec
from agentdeck.storage.ownership import ProjectWriterLease
from agentdeck.storage.sqlite_store import CommandEnvelope, EventConflict, SQLiteMissionStore


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


def _task(task_id: str, *, dependencies: tuple[str, ...] = ()) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        objective=f"Complete {task_id}",
        role="worker",
        scope=("src",),
        acceptance_contribution=(f"{task_id} done",),
        acceptance_criteria=(f"{task_id} check",),
        dependencies=dependencies,
        retry_limit=1,
        budget_units=10,
    )


def _proposal() -> MissionProposal:
    mission = MissionVersion(
        mission_id="mis_1",
        version=1,
        goal="Run deterministic two-worker handoff",
        scope=("src",),
        exclusions=("network",),
        tasks=(_task("build"), _task("review", dependencies=("build",))),
        acceptance_criteria=("build and review complete",),
        constraints=("fake workers only",),
        max_parallel_tasks=1,
        budget_units=20,
        ordered_routes=("codex", "claude"),
        expires_at=None,
        provenance_source="test-leader",
        provenance_id="turn_1",
    )
    authorization = AuthorizationEnvelope(
        goal=mission.goal,
        semantic_scope=mission.scope,
        path_scope=("src",),
        exclusions=mission.exclusions,
        operations=("read", "write", "test"),
        allowed_agents=("codex", "claude"),
        allowed_roles=("worker",),
        external_effect_policy=ExternalEffectPolicy.DENY,
        max_attempts=4,
        max_retries=2,
        max_recoveries=1,
        budget_units=20,
        acceptance_criteria=mission.acceptance_criteria,
        ordered_routes=mission.ordered_routes,
        expires_at=None,
    )
    return MissionProposal(mission, authorization, {"provider": "fake"})


def _command(kind: str, payload: dict[str, object], command_id: str, revision: int):
    return CommandEnvelope(
        command_id=command_id,
        kind=kind,
        actor={"kind": "human", "id": "user_1"},
        payload=payload,
        expected_revision=revision,
        created_at="2026-07-18T10:00:00Z",
    )


def _confirmed(service: MissionService) -> MissionProposal:
    proposal = _proposal()
    service.propose(
        _command(
            "mission.propose",
            {
                "mission_id": "mis_1",
                "version": 1,
                "authorization_digest": proposal.authorization_digest,
                "leader_provenance_hash": proposal.leader_provenance_hash,
            },
            "cmd_propose",
            0,
        ),
        proposal,
    )
    service.confirm(
        _command(
            "mission.confirm",
            {
                "mission_id": "mis_1",
                "version": 1,
                "authorization_digest": proposal.authorization_digest,
            },
            "cmd_confirm",
            1,
        ),
        mission_id="mis_1",
        version=1,
        digest=proposal.authorization_digest,
    )
    return proposal


def _internal(event_id: str, kind: str, trigger_id: str, revision: int, payload: dict[str, object]):
    return DomainEvent.internal_trigger(
        event_id=event_id,
        kind=kind,
        internal_trigger_id=trigger_id,
        source_revision=revision,
        source_snapshot_id=f"snapshot_{revision}",
        payload=payload,
        created_at="2026-07-18T10:00:00Z",
    )


def _adapter(
    event_id: str,
    kind: str,
    adapter_id: str,
    *,
    task_id: str,
    attempt_id: str,
    session_id: str,
    sequence: int,
    payload: dict[str, object],
):
    integrity_hash = adapter_event_integrity_hash(
        event_id=event_id,
        kind=kind,
        adapter_event_id=adapter_id,
        mission_id="mis_1",
        mission_version="1",
        task_id=task_id,
        attempt_id=attempt_id,
        session_id=session_id,
        sequence=sequence,
        payload=payload,
        created_at="2026-07-18T10:00:00Z",
    )
    return DomainEvent.adapter_event(
        event_id=event_id,
        kind=kind,
        adapter_event_id=adapter_id,
        mission_id="mis_1",
        mission_version="1",
        task_id=task_id,
        attempt_id=attempt_id,
        session_id=session_id,
        sequence=sequence,
        integrity_hash=integrity_hash,
        payload=payload,
        created_at="2026-07-18T10:00:00Z",
    )


def _read_statuses(store: SQLiteMissionStore):
    with store.open_reader() as reader:
        mission = reader.execute("SELECT status FROM missions").fetchone()[0]
        tasks = dict(reader.execute("SELECT task_id, status FROM tasks"))
        attempts = reader.execute(
            "SELECT attempt_id, task_id, attempt_number, status "
            "FROM attempts ORDER BY started_revision"
        ).fetchall()
    return mission, tasks, attempts


def test_fake_two_worker_chain_requires_verification_and_durable_handoff(store) -> None:
    service = MissionService(store)
    _confirmed(service)

    service.release_ready_tasks(
        _internal("evt_release_1", "tasks.release", "int_release_1", 2, {"mission_id": "mis_1"})
    )
    build_start = StartAttemptRequest(
        mission_id="mis_1",
        mission_version=1,
        task_id="build",
        attempt_id="att_build_1",
        session_id="ses_build_1",
        agent_id="codex",
        model_id="gpt-fake",
        transport="fake",
        route_position=0,
        budget_units=5,
    )
    service.start_attempt(
        _internal("evt_start_1", "attempt.start", "int_start_1", 3, build_start.to_dict()),
        build_start,
    )

    message = _adapter(
        "evt_msg_1", "worker_message", "adp_msg_1",
        task_id="build", attempt_id="att_build_1", session_id="ses_build_1",
        sequence=1, payload={"text": "done"},
    )
    first_message = service.record_worker_event(message)
    assert service.record_worker_event(message) == first_message
    assert _read_statuses(store)[1]["build"] == "running"

    evidence = _adapter(
        "evt_evidence_1", "evidence", "adp_evidence_1",
        task_id="build", attempt_id="att_build_1", session_id="ses_build_1",
        sequence=2,
        payload={
            "evidence_id": "evd_build",
            "kind": "test_result",
            "criterion": "build check",
            "fact": "check_passed",
            "reason": "fake check passed",
        },
    )
    service.record_evidence(evidence)
    service.record_worker_event(
        _adapter(
            "evt_turn_1", "turn_completed", "adp_turn_1",
            task_id="build", attempt_id="att_build_1", session_id="ses_build_1",
            sequence=3, payload={},
        )
    )
    assert _read_statuses(store)[1]["build"] == "awaiting_verification"
    service.verify_task(
        _internal("evt_verify_1", "task.verify", "int_verify_1", 7, {"task_id": "build"}),
        "build",
    )
    mission, tasks, attempts = _read_statuses(store)
    assert mission == "running"
    assert tasks == {"build": "completed", "review": "pending"}
    assert attempts[0] == ("att_build_1", "build", 1, "completed")

    service.record_worker_event(
        _adapter(
            "evt_stale_msg", "worker_message", "adp_stale_msg",
            task_id="build", attempt_id="att_build_1", session_id="ses_build_1",
            sequence=4, payload={"text": "still done"},
        )
    )
    service.record_evidence(
        _adapter(
            "evt_stale_evidence", "evidence", "adp_stale_evidence",
            task_id="build", attempt_id="att_build_1", session_id="ses_build_1",
            sequence=5,
            payload={
                "evidence_id": "evd_stale",
                "kind": "test_result",
                "criterion": "build check",
                "fact": "check_passed",
                "reason": "late duplicate credit",
            },
        )
    )
    assert _read_statuses(store)[:2] == (
        "running",
        {"build": "completed", "review": "pending"},
    )
    with store.open_reader() as reader:
        assert reader.execute(
            "SELECT COUNT(*) FROM evidence WHERE evidence_id = 'evd_stale'"
        ).fetchone() == (0,)

    handoff = HandoffRequest(
        handoff_id="hnd_build_review",
        mission_id="mis_1",
        mission_version=1,
        source_task_id="build",
        source_attempt_id="att_build_1",
        destination_task_id="review",
        evidence_ids=("evd_build",),
        context={"summary": "build verified"},
    )
    service.record_handoff(
        _internal("evt_handoff", "handoff.record", "int_handoff", 10, handoff.to_dict()),
        handoff,
    )
    service.release_ready_tasks(
        _internal("evt_release_2", "tasks.release", "int_release_2", 11, {"mission_id": "mis_1"})
    )
    review_start = StartAttemptRequest(
        mission_id="mis_1",
        mission_version=1,
        task_id="review",
        attempt_id="att_review_1",
        session_id="ses_review_1",
        agent_id="claude",
        model_id="claude-fake",
        transport="fake",
        route_position=1,
        budget_units=5,
    )
    service.start_attempt(
        _internal("evt_start_2", "attempt.start", "int_start_2", 12, review_start.to_dict()),
        review_start,
    )
    service.record_evidence(
        _adapter(
            "evt_evidence_2", "evidence", "adp_evidence_2",
            task_id="review", attempt_id="att_review_1", session_id="ses_review_1",
            sequence=1,
            payload={
                "evidence_id": "evd_review",
                "kind": "test_result",
                "criterion": "review check",
                "fact": "check_passed",
                "reason": "fake review passed",
            },
        )
    )
    service.record_worker_event(
        _adapter(
            "evt_turn_2", "turn_completed", "adp_turn_2",
            task_id="review", attempt_id="att_review_1", session_id="ses_review_1",
            sequence=2, payload={},
        )
    )
    service.verify_task(
        _internal("evt_verify_2", "task.verify", "int_verify_2", 15, {"task_id": "review"}),
        "review",
    )

    mission, tasks, attempts = _read_statuses(store)
    assert mission == "completed"
    assert tasks == {"build": "completed", "review": "completed"}
    assert attempts[1] == ("att_review_1", "review", 1, "completed")
    with store.open_reader() as reader:
        assert reader.execute("SELECT status FROM handoffs").fetchone() == ("accepted",)
        assert reader.execute("SELECT COUNT(*) FROM commands").fetchone() == (2,)
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone() == (16,)
        assert reader.execute("SELECT revision FROM projects").fetchone() == (16,)

    terminal_late = _adapter(
        "evt_terminal_late", "worker_message", "adp_terminal_late",
        task_id="review", attempt_id="att_review_1", session_id="ses_review_1",
        sequence=3, payload={"text": "late terminal observation"},
    )
    terminal_outcome = service.record_worker_event(terminal_late)
    assert service.record_worker_event(terminal_late) == terminal_outcome
    service.record_evidence(
        _adapter(
            "evt_terminal_evidence", "evidence", "adp_terminal_evidence",
            task_id="review", attempt_id="att_review_1", session_id="ses_review_1",
            sequence=4,
            payload={
                "evidence_id": "evd_terminal_late",
                "kind": "test_result",
                "criterion": "review check",
                "fact": "check_passed",
                "reason": "late terminal evidence",
            },
        )
    )
    mission, tasks, attempts = _read_statuses(store)
    assert mission == "completed"
    assert tasks == {"build": "completed", "review": "completed"}
    assert [item[3] for item in attempts] == ["completed", "completed"]
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (18,)
        assert reader.execute(
            "SELECT last_sequence FROM sessions WHERE session_id = 'ses_review_1'"
        ).fetchone() == (4,)
        assert reader.execute(
            "SELECT COUNT(*) FROM evidence WHERE evidence_id = 'evd_terminal_late'"
        ).fetchone() == (0,)


def test_adapter_integrity_is_canonical_and_binds_every_field(store) -> None:
    fields = {
        "event_id": "evt_integrity",
        "kind": "worker_message",
        "adapter_event_id": "adp_integrity",
        "mission_id": "mis_1",
        "mission_version": "1",
        "task_id": "build",
        "attempt_id": "att_1",
        "session_id": "ses_1",
        "sequence": 1,
        "payload": {"b": 2, "a": 1},
        "created_at": "2026-07-18T10:00:00Z",
    }
    first = adapter_event_integrity_hash(**fields)
    assert first == adapter_event_integrity_hash(
        **{**fields, "payload": {"a": 1, "b": 2}}
    )
    for key, changed in {
        "event_id": "evt_other",
        "kind": "progress",
        "adapter_event_id": "adp_other",
        "mission_id": "mis_other",
        "mission_version": "2",
        "task_id": "other",
        "attempt_id": "att_other",
        "session_id": "ses_other",
        "sequence": 2,
        "payload": {"a": 2},
        "created_at": "2026-07-18T10:01:00Z",
    }.items():
        assert adapter_event_integrity_hash(**{**fields, key: changed}) != first


def test_safe_no_effect_failure_creates_distinct_retry_and_exhaustion_stops(store) -> None:
    service = MissionService(store)
    _confirmed(service)
    service.release_ready_tasks(
        _internal("evt_release", "tasks.release", "int_release", 2, {"mission_id": "mis_1"})
    )
    first_request = StartAttemptRequest(
        "mis_1", 1, "build", "att_1", "ses_1", "codex", "fake", "fake", 0, 4
    )
    service.start_attempt(
        _internal("evt_start", "attempt.start", "int_start", 3, first_request.to_dict()),
        first_request,
    )
    service.record_worker_event(
        _adapter(
            "evt_failed_1", "failed", "adp_failed_1",
            task_id="build", attempt_id="att_1", session_id="ses_1", sequence=1,
            payload={"reason": "transport ended", "effect_status": "proven_no_effect"},
        )
    )
    mission, tasks, attempts = _read_statuses(store)
    assert mission == "running"
    assert tasks["build"] == "ready"
    assert attempts == [("att_1", "build", 1, "failed")]

    oversized = StartAttemptRequest(
        "mis_1", 1, "build", "att_2_bad", "ses_2_bad", "codex", "fake", "fake", 0, 7
    )
    with pytest.raises(ValueError, match="^attempt budget exhausted$"):
        service.start_attempt(
            _internal("evt_bad_retry", "attempt.start", "int_bad_retry", 5, oversized.to_dict()),
            oversized,
        )

    second_request = StartAttemptRequest(
        "mis_1", 1, "build", "att_2", "ses_2", "codex", "fake", "fake", 0, 4
    )
    service.start_attempt(
        _internal("evt_retry", "attempt.start", "int_retry", 5, second_request.to_dict()),
        second_request,
    )
    service.record_worker_event(
        _adapter(
            "evt_failed_2", "failed", "adp_failed_2",
            task_id="build", attempt_id="att_2", session_id="ses_2", sequence=1,
            payload={"reason": "second transport end", "effect_status": "proven_no_effect"},
        )
    )
    mission, tasks, attempts = _read_statuses(store)
    assert mission == "failed"
    assert tasks["build"] == "failed"
    assert attempts == [
        ("att_1", "build", 1, "failed"),
        ("att_2", "build", 2, "failed"),
    ]


def test_takeover_scope_conflict_and_terminal_failure_are_persisted_in_order(store) -> None:
    service = MissionService(store)
    _confirmed(service)
    service.release_ready_tasks(
        _internal("evt_release", "tasks.release", "int_release", 2, {"mission_id": "mis_1"})
    )
    request = StartAttemptRequest(
        "mis_1", 1, "build", "att_1", "ses_1", "codex", "fake", "fake", 0, 4
    )
    service.start_attempt(
        _internal("evt_start", "attempt.start", "int_start", 3, request.to_dict()), request
    )

    service.record_worker_event(
        _adapter(
            "evt_takeover", "session_takeover", "adp_takeover",
            task_id="build", attempt_id="att_1", session_id="ses_1", sequence=1,
            payload={"reason": "human owns session"},
        )
    )
    assert _read_statuses(store)[:2] == (
        "running", {"build": "paused", "review": "pending"}
    )

    service.record_worker_event(
        _adapter(
            "evt_conflict", "permission_conflict", "adp_conflict",
            task_id="build", attempt_id="att_1", session_id="ses_1", sequence=2,
            payload={"reason": "scope conflict"},
        )
    )
    assert _read_statuses(store)[0] == "paused"

    malformed = _adapter(
        "evt_bad_failed", "failed", "adp_bad_failed",
        task_id="build", attempt_id="att_1", session_id="ses_1", sequence=3,
        payload={"reason": "missing effect proof"},
    )
    with pytest.raises(ValueError, match="^worker event payload invalid$"):
        service.record_worker_event(malformed)
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (6,)

    service.record_worker_event(
        _adapter(
            "evt_failed", "failed", "adp_failed",
            task_id="build", attempt_id="att_1", session_id="ses_1", sequence=3,
            payload={"reason": "known partial write", "effect_status": "known_effect"},
        )
    )
    mission, tasks, attempts = _read_statuses(store)
    assert mission == "failed"
    assert tasks["build"] == "failed"
    assert attempts == [("att_1", "build", 1, "failed")]
    with store.open_reader() as reader:
        assert reader.execute(
            "SELECT status, last_sequence FROM sessions WHERE session_id = 'ses_1'"
        ).fetchone() == ("failed", 3)


def test_tampered_adapter_integrity_is_rejected_before_any_write(store) -> None:
    service = MissionService(store)
    _confirmed(service)
    service.release_ready_tasks(
        _internal("evt_release", "tasks.release", "int_release", 2, {"mission_id": "mis_1"})
    )
    request = StartAttemptRequest(
        "mis_1", 1, "build", "att_1", "ses_1", "codex", "fake", "fake", 0, 4
    )
    service.start_attempt(
        _internal("evt_start", "attempt.start", "int_start", 3, request.to_dict()), request
    )
    original = _adapter(
        "evt_integrity", "worker_message", "adp_integrity",
        task_id="build", attempt_id="att_1", session_id="ses_1", sequence=1,
        payload={"text": "original"},
    )
    tampered = DomainEvent.adapter_event(
        event_id="evt_integrity",
        kind="worker_message",
        adapter_event_id="adp_integrity",
        mission_id="mis_1",
        mission_version="1",
        task_id="build",
        attempt_id="att_1",
        session_id="ses_1",
        sequence=1,
        integrity_hash=original.provenance.integrity_hash,
        payload={"text": "tampered"},
        created_at="2026-07-18T10:00:00Z",
    )
    with pytest.raises(ValueError, match="^mission event invalid$"):
        service.record_worker_event(tampered)
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (4,)
        assert reader.execute("SELECT COUNT(*) FROM events").fetchone() == (4,)


def test_sequence_gap_and_changed_duplicate_fail_without_revision_or_double_credit(store) -> None:
    service = MissionService(store)
    _confirmed(service)
    service.release_ready_tasks(
        _internal("evt_release", "tasks.release", "int_release", 2, {"mission_id": "mis_1"})
    )
    request = StartAttemptRequest(
        "mis_1", 1, "build", "att_1", "ses_1", "codex", "fake", "fake", 0, 5
    )
    service.start_attempt(
        _internal("evt_start", "attempt.start", "int_start", 3, request.to_dict()), request
    )
    gap = _adapter(
        "evt_gap", "worker_message", "adp_gap",
        task_id="build", attempt_id="att_1", session_id="ses_1", sequence=2, payload={},
    )
    with pytest.raises(ValueError, match="^adapter sequence conflict$"):
        service.record_worker_event(gap)

    first = _adapter(
        "evt_first", "evidence", "adp_evidence",
        task_id="build", attempt_id="att_1", session_id="ses_1", sequence=1,
        payload={
            "evidence_id": "evd_1", "kind": "test_result", "criterion": "build check",
            "fact": "check_passed", "reason": "passed",
        },
    )
    service.record_evidence(first)
    changed = _adapter(
        "evt_first", "evidence", "adp_evidence",
        task_id="build", attempt_id="att_1", session_id="ses_1", sequence=1,
        payload={
            "evidence_id": "evd_1", "kind": "test_result", "criterion": "build check",
            "fact": "check_failed", "reason": "changed",
        },
    )
    with pytest.raises(EventConflict, match="^adapter event input mismatch$"):
        service.record_evidence(changed)
    with store.open_reader() as reader:
        assert reader.execute("SELECT revision FROM projects").fetchone() == (5,)
        assert reader.execute("SELECT COUNT(*) FROM evidence").fetchone() == (1,)


def test_missing_evidence_is_unavailable_and_ambiguous_effect_pauses_mission(store) -> None:
    service = MissionService(store)
    _confirmed(service)
    service.release_ready_tasks(
        _internal("evt_release", "tasks.release", "int_release", 2, {"mission_id": "mis_1"})
    )
    request = StartAttemptRequest(
        "mis_1", 1, "build", "att_1", "ses_1", "codex", "fake", "fake", 0, 5
    )
    service.start_attempt(
        _internal("evt_start", "attempt.start", "int_start", 3, request.to_dict()), request
    )
    service.record_worker_event(
        _adapter(
            "evt_turn", "turn_completed", "adp_turn",
            task_id="build", attempt_id="att_1", session_id="ses_1", sequence=1, payload={},
        )
    )
    service.verify_task(
        _internal("evt_verify", "task.verify", "int_verify", 5, {"task_id": "build"}), "build"
    )
    assert _read_statuses(store)[1]["build"] == "paused"

    service.record_worker_event(
        _adapter(
            "evt_permission", "permission_conflict", "adp_permission",
            task_id="build", attempt_id="att_1", session_id="ses_1", sequence=2,
            payload={"reason": "operation outside frozen authority"},
        )
    )
    service.record_worker_event(
        _adapter(
            "evt_ambiguous", "ambiguous_effect", "adp_ambiguous",
            task_id="build", attempt_id="att_1", session_id="ses_1", sequence=3,
            payload={"reason": "unknown write outcome"},
        )
    )
    mission, tasks, attempts = _read_statuses(store)
    assert mission == "paused"
    assert tasks["build"] == "paused"
    assert attempts[0][3] == "paused"
    with store.open_reader() as reader:
        reconciliation = json.loads(
            reader.execute("SELECT reconciliation_json FROM sessions").fetchone()[0]
        )
    assert [item["kind"] for item in reconciliation["facts"]] == [
        "permission_conflict",
        "ambiguous_effect",
    ]
