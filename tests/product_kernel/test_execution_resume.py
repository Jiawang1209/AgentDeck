from __future__ import annotations

import copy
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from agentdeck.application.execution_resume import ExecutionResumePlanner
from agentdeck.application.execution_records import (
    AuthoritativeRevisionTask,
    command_id,
)
from agentdeck.kernel.execution import Evidence, EvidenceKind
from agentdeck.kernel.session import SessionState
from agentdeck.ports.execution_resume import ExecutionResumeProjectionError
from product_kernel.test_sqlite_execution_resume import (
    NOW,
    _table_snapshot,
    seed_closed_stage,
    seed_interrupted_attempt,
    store,
)


@pytest.fixture
def snapshot(store):
    seed_closed_stage(store, "implementation")
    return store.load_execution_resume("ses_1")


def _rehash_forged(snapshot):
    encoded = json.dumps(
        snapshot.canonical_facts(), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8", "strict")
    object.__setattr__(snapshot, "content_hash", sha256(encoded).hexdigest())
    return snapshot


def test_planner_materializes_closed_context_and_remaining_suffix(snapshot):
    plan = ExecutionResumePlanner().materialize(snapshot)

    assert tuple(task.name for task in plan.remaining_tasks) == (
        "review", "revision", "acceptance"
    )
    assert plan.first_attempt_ordinal == 1
    assert tuple(item.attempt_id for item in plan.prior_attempts) == (
        "att_implementation_1",
    )
    assert tuple(item.handoff_id for item in plan.prior_handoffs) == (
        "hnd_implementation_1",
    )
    assert plan.snapshot_hash == snapshot.content_hash


def test_planner_uses_snapshot_next_ordinal_without_store_rescan(snapshot):
    forged = copy.copy(snapshot)
    object.__setattr__(forged, "max_prior_attempt_ordinal", 2)
    object.__setattr__(forged, "next_attempt_ordinal", 3)
    _rehash_forged(forged)

    with pytest.raises(
        ExecutionResumeProjectionError, match="resume_projection_malformed"
    ):
        ExecutionResumePlanner().materialize(forged)


def test_planner_preserves_interrupted_history_and_next_ordinal(store):
    seed_closed_stage(store, "implementation")
    seed_interrupted_attempt(store, "review", 1)
    seed_interrupted_attempt(store, "review", 2)

    plan = ExecutionResumePlanner().materialize(
        store.load_execution_resume("ses_1")
    )

    assert tuple(item.attempt_id for item in plan.prior_attempts) == (
        "att_implementation_1", "att_review_1", "att_review_2"
    )
    assert plan.first_attempt_ordinal == 3


def test_planner_rebuilds_authoritative_revision_task_from_review_evidence(store):
    seed_closed_stage(store, "implementation")
    seed_closed_stage(store, "review")

    plan = ExecutionResumePlanner().materialize(
        store.load_execution_resume("ses_1")
    )

    assert type(plan.revision_task) is AuthoritativeRevisionTask
    assert plan.revision_task.task_id == "tsk_revision"
    assert plan.revision_task.accepted_finding_ids == ("rfn_resume",)
    assert plan.revision_task.review_evidence_ids == ("ev_review_1",)


def test_planner_rejects_completed_acceptance(store):
    for name in ("implementation", "review", "revision", "acceptance"):
        seed_closed_stage(store, name)

    with pytest.raises(ExecutionResumeProjectionError, match="resume_mission_complete"):
        ExecutionResumePlanner().materialize(store.load_execution_resume("ses_1"))


def test_planner_rejects_snapshot_hash_drift_without_worker_dependency(snapshot):
    forged = copy.copy(snapshot)
    object.__setattr__(forged, "content_hash", "0" * 64)

    with pytest.raises(
        ExecutionResumeProjectionError, match="resume_projection_malformed"
    ):
        ExecutionResumePlanner().materialize(forged)


def test_planner_rejects_rehashed_terminal_command_identity_drift(snapshot):
    first = replace(
        snapshot.facts.stages[0], terminal_command_id="cmd_forged"
    )
    facts = replace(
        snapshot.facts, stages=(first, *snapshot.facts.stages[1:])
    )
    forged = type(snapshot).create(facts)

    with pytest.raises(
        ExecutionResumeProjectionError, match="resume_projection_malformed"
    ):
        ExecutionResumePlanner().materialize(forged)


def test_planner_rejects_rehashed_attempt_ordinal_gap(store):
    seed_closed_stage(store, "implementation")
    seed_interrupted_attempt(store, "review", 1)
    seed_interrupted_attempt(store, "review", 2)
    snapshot = store.load_execution_resume("ses_1")
    review = snapshot.facts.stages[1]
    attempts = (review.attempts[0], replace(review.attempts[1], ordinal=3))
    changed = replace(review, attempts=attempts)
    facts = replace(
        snapshot.facts,
        stages=(snapshot.facts.stages[0], changed, *snapshot.facts.stages[2:]),
    )
    forged = type(snapshot).create(facts)

    with pytest.raises(
        ExecutionResumeProjectionError, match="resume_projection_malformed"
    ):
        ExecutionResumePlanner().materialize(forged)


def test_planner_rejects_rehashed_review_evidence_lineage_drift(store):
    seed_closed_stage(store, "implementation")
    seed_closed_stage(store, "review")
    snapshot = store.load_execution_resume("ses_1")
    review = snapshot.facts.stages[1]
    evidence = review.evidence[0]
    payload = json.loads(evidence.canonical_evidence_facts)
    payload["evidence_ids"] = ["ev_missing"]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    changed_evidence = replace(
        evidence,
        canonical_evidence_facts=canonical,
        content_hash=sha256(canonical.encode()).hexdigest(),
    )
    changed_review = replace(review, evidence=(changed_evidence,))
    facts = replace(
        snapshot.facts,
        stages=(snapshot.facts.stages[0], changed_review, *snapshot.facts.stages[2:]),
    )
    forged = type(snapshot).create(facts)

    with pytest.raises(
        ExecutionResumeProjectionError, match="resume_projection_malformed"
    ):
        ExecutionResumePlanner().materialize(forged)


def test_planner_requires_exact_snapshot_type():
    with pytest.raises(TypeError, match="ExecutionResumeSnapshot"):
        ExecutionResumePlanner().materialize(object())


def _add_outside_review_evidence(store):
    evidence = Evidence.create("ev_review_outside", EvidenceKind.REVIEW_FINDING, {
        "finding_id": "rfn_outside", "scope": "outside",
        "severity": "error", "summary": "Outside confirmed scope",
        "criterion": store._resume_draft.acceptance_criteria[0],
        "evidence_ids": ["ev_implementation_1"],
    })
    connection = store._require_writer()
    connection.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?)", (
        evidence.evidence_id, "tsk_review", "att_review_1", evidence.kind.value,
        evidence.canonical_content,
        sha256(evidence.canonical_content.encode()).hexdigest(), NOW.isoformat(),
    ))
    identity = command_id(
        "terminal", store._resume_confirmed,
        next(task for task in store._resume_draft.tasks if task.name == "review"), 1,
    )
    result = json.loads(connection.execute(
        "SELECT canonical_result_facts FROM commands WHERE command_id=?", (identity,)
    ).fetchone()[0])
    result["evidence_ids"].append(evidence.evidence_id)
    connection.execute(
        "UPDATE commands SET canonical_result_facts=? WHERE command_id=?",
        (json.dumps(result, sort_keys=True, separators=(",", ":")), identity),
    )


def test_review_resume_keeps_all_evidence_but_only_in_scope_revision_authority(store):
    seed_closed_stage(store, "implementation")
    seed_closed_stage(store, "review")
    _add_outside_review_evidence(store)

    plan = ExecutionResumePlanner().materialize(
        store.load_execution_resume("ses_1")
    )

    assert tuple(item.evidence_id for item in plan.prior_evidence) == (
        "ev_implementation_1", "ev_review_1", "ev_review_outside"
    )
    assert plan.revision_task.accepted_finding_ids == ("rfn_resume",)
    assert plan.revision_task.review_evidence_ids == ("ev_review_1",)


def test_acceptance_evidence_must_reference_direct_revision_predecessor(store):
    for name in ("implementation", "review", "revision", "acceptance"):
        seed_closed_stage(store, name)
    connection = store._require_writer()
    canonical = connection.execute(
        "SELECT canonical_evidence_facts FROM evidence "
        "WHERE evidence_id='ev_acceptance_1'"
    ).fetchone()[0]
    payload = json.loads(canonical)
    criterion = store._resume_draft.acceptance_criteria[0]
    payload["evidence_by_criterion"][criterion] = ["ev_implementation_1"]
    changed = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    connection.execute(
        "UPDATE evidence SET canonical_evidence_facts=?,content_hash=? "
        "WHERE evidence_id='ev_acceptance_1'",
        (changed, sha256(changed.encode()).hexdigest()),
    )
    before = _table_snapshot(store)

    with pytest.raises(ExecutionResumeProjectionError, match="resume_projection_malformed"):
        store.load_execution_resume("ses_1")

    assert _table_snapshot(store) == before


def test_planner_validates_completed_history_before_mission_complete(store):
    for name in ("implementation", "review", "revision", "acceptance"):
        seed_closed_stage(store, name)
    snapshot = store.load_execution_resume("ses_1")
    with pytest.raises(ExecutionResumeProjectionError, match="resume_mission_complete"):
        ExecutionResumePlanner().materialize(snapshot)
    first = replace(snapshot.facts.stages[0], terminal_command_id="cmd_forged")
    forged = type(snapshot).create(replace(
        snapshot.facts, stages=(first, *snapshot.facts.stages[1:])
    ))

    with pytest.raises(
        ExecutionResumeProjectionError, match="resume_projection_malformed"
    ):
        ExecutionResumePlanner().materialize(forged)


def test_projection_rejects_orphan_later_terminal_command_read_only(store):
    seed_closed_stage(store, "implementation")
    task = next(
        item for item in store._resume_draft.tasks if item.name == "revision"
    )
    result = {
        "mission_id": store._resume_confirmed.mission_id,
        "mission_version": store._resume_confirmed.version,
        "task_id": task.task_id,
        "attempt_id": "att_revision_1",
        "evidence_ids": ["ev_orphan"],
        "handoff_id": "hnd_orphan",
    }
    store._require_writer().execute(
        "INSERT INTO commands VALUES (?,?,'completed',?,?,?)",
        (
            command_id("terminal", store._resume_confirmed, task, 1),
            "execution_stage_committed",
            json.dumps(result, sort_keys=True, separators=(",", ":")),
            NOW.isoformat(), NOW.isoformat(),
        ),
    )
    before = _table_snapshot(store)

    with pytest.raises(ExecutionResumeProjectionError, match="resume_projection_malformed"):
        store.load_execution_resume("ses_1")

    assert _table_snapshot(store) == before


def test_resume_facts_reject_str_enum_session_state(store):
    facts = store.load_execution_resume("ses_1").facts

    with pytest.raises(ExecutionResumeProjectionError, match="resume_projection_malformed"):
        replace(facts, session_state=SessionState.PAUSED)
