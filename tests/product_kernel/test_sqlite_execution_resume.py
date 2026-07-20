from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.execution_records import command_id, terminal_command_result
from agentdeck.kernel.execution import (
    AcceptanceResult,
    Attempt,
    Evidence,
    EvidenceKind,
    Handoff,
)
from agentdeck.kernel.mission import MissionDraft
from agentdeck.ports.execution_resume import ExecutionResumeProjectionError
from product_kernel.fakes import FrozenClock


NOW = datetime(2026, 7, 20, 1, 2, 3, tzinfo=timezone.utc)
MUTATIONS = (
    "task_only_completed",
    "orphan_completed_attempt",
    "terminal_command_wrong_kind",
    "terminal_command_wrong_attempt",
    "terminal_command_extra_field",
    "missing_evidence",
    "unreferenced_extra_evidence",
    "evidence_hash_drift",
    "handoff_hash_drift",
    "handoff_wrong_target",
    "handoff_evidence_drift",
    "acceptance_has_handoff",
    "non_acceptance_missing_handoff",
    "attempt_ordinal_gap",
    "completed_stage_after_open_stage",
    "second_executing_mission",
    "pending_exit_present",
    "session_not_paused",
)


@pytest.fixture
def store(tmp_path: Path):
    value = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _seed_base(value)
    try:
        yield value
    finally:
        value.close()


def _seed_base(store: SQLiteStore) -> None:
    connection = store._require_writer()
    now = NOW.isoformat()
    draft = MissionDraft.coding_default(
        "drf_resume", "Resume the exact mission", str(store._project_root),
        "codex-cli", "native-default", "approve_for_me", leader_version="1.2.3",
    )
    preview = draft.preview(1)
    confirmed = preview.confirm(
        preview_id=preview.preview_id, content_hash=preview.content_hash
    )
    store._resume_draft = draft
    store._resume_confirmed = confirmed
    connection.execute(
        "INSERT INTO projects VALUES ('prj_resume',?,?)",
        (str(store._project_root), now),
    )
    connection.execute(
        """INSERT INTO product_sessions (
               session_id,project_id,state,permission_profile,pending_goal,
               created_at,updated_at,leader_backend,leader_model)
           VALUES ('ses_1','prj_resume','paused','approve_for_me',NULL,?,?,?,?)""",
        (now, now, "codex-cli", "native-default"),
    )
    for task in draft.tasks:
        connection.execute(
            "INSERT INTO agent_instances VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                task.agent_instance_id, "ses_1", task.backend, "acp", "1.2.3",
                task.role.value, f"acp_{task.name}", "active", now, now,
            ),
        )
    connection.execute(
        "INSERT INTO missions VALUES (?,?,?,?,?,?)",
        (confirmed.mission_id, "ses_1", "running", 1, now, now),
    )
    connection.execute(
        "INSERT INTO mission_versions VALUES (?,?,?,?,?,?)",
        (
            confirmed.mission_id, 1, preview.preview_id, confirmed.content_hash,
            confirmed.canonical_content, now,
        ),
    )
    for ordinal, task in enumerate(draft.tasks, 1):
        canonical = json.dumps(
            task.canonical_projection(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task.task_id, confirmed.mission_id, 1, ordinal, task.name,
                task.role.value, task.backend, task.agent_instance_id,
                task.acp_route, "pending", canonical, now, now,
            ),
        )


def _task(store: SQLiteStore, name: str):
    return next(item for item in store._resume_draft.tasks if item.name == name)


def _insert_attempt(store: SQLiteStore, attempt: Attempt) -> None:
    task = next(item for item in store._resume_draft.tasks if item.task_id == attempt.task_id)
    now = NOW.isoformat()
    store._require_writer().execute(
        "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            attempt.attempt_id, attempt.task_id, task.agent_instance_id,
            attempt.ordinal, attempt.state.value, attempt.reason,
            attempt.result_summary, int(attempt.retryable), f"acp_{task.name}", 0,
            now, now,
        ),
    )


def seed_interrupted_attempt(store: SQLiteStore, name: str, ordinal: int) -> None:
    task = _task(store, name)
    attempt = Attempt.pending(
        f"att_{name}_{ordinal}", task.task_id, ordinal
    ).start().interrupt("product_exit_confirmed")
    _insert_attempt(store, attempt)


def seed_outcome_unknown_attempt(store: SQLiteStore, name: str, ordinal: int) -> None:
    task = _task(store, name)
    attempt = Attempt.pending(
        f"att_{name}_{ordinal}", task.task_id, ordinal
    ).start().unknown_outcome("recovery_effect_observed")
    _insert_attempt(store, attempt)


def _evidence(store: SQLiteStore, name: str, ordinal: int) -> Evidence:
    identity = f"ev_{name}_{ordinal}"
    if name == "review":
        return Evidence.create(identity, EvidenceKind.REVIEW_FINDING, {
            "finding_id": "rfn_resume", "scope": store._resume_draft.scope,
            "severity": "error", "summary": "Fix the landmark",
            "criterion": store._resume_draft.acceptance_criteria[0],
            "evidence_ids": ("ev_implementation_1",),
        })
    if name == "acceptance":
        result = AcceptanceResult.create(
            store._resume_draft.acceptance_criteria,
            {store._resume_draft.acceptance_criteria[0]: ("ev_revision_1",)},
            accepted=True,
        )
        return Evidence.acceptance(
            identity, result=result, source_kind=EvidenceKind.ACCEPTANCE_RESULT
        )
    return Evidence.create(identity, EvidenceKind.ARTIFACT_HASH, {
        "artifact_reference": f"{name} patch",
        "content_hash": sha256(name.encode()).hexdigest(),
    })


def seed_closed_stage(store: SQLiteStore, name: str, ordinal: int = 1) -> None:
    task = _task(store, name)
    attempt = Attempt.pending(
        f"att_{name}_{ordinal}", task.task_id, ordinal
    ).start().complete(f"{name} complete")
    evidence = _evidence(store, name, ordinal)
    index = store._resume_draft.tasks.index(task)
    handoff = None
    if name != "acceptance":
        handoff = Handoff.create(
            f"hnd_{name}_{ordinal}", attempt.attempt_id,
            store._resume_draft.tasks[index + 1].task_id,
            attempt.result_summary, (evidence.evidence_id,),
            artifact_references=(f"{name} artifact",),
        )
    _insert_attempt(store, attempt)
    connection = store._require_writer()
    now = NOW.isoformat()
    connection.execute(
        "INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
        (
            evidence.evidence_id, task.task_id, attempt.attempt_id,
            evidence.kind.value, evidence.canonical_content,
            sha256(evidence.canonical_content.encode()).hexdigest(), now,
        ),
    )
    if handoff is not None:
        connection.execute(
            "INSERT INTO handoffs VALUES (?,?,?,?,?,?,?)",
            (
                handoff.handoff_id, handoff.source_attempt_id,
                handoff.target_task_id, handoff.result_summary,
                handoff.canonical_content, handoff.content_hash, now,
            ),
        )
    result = terminal_command_result(
        store._resume_confirmed, task, attempt, (evidence,), handoff
    )
    connection.execute(
        "INSERT INTO commands VALUES (?,?,'completed',?,?,?)",
        (
            command_id("terminal", store._resume_confirmed, task, ordinal),
            "execution_stage_committed",
            json.dumps(result, sort_keys=True, separators=(",", ":")), now, now,
        ),
    )
    connection.execute(
        "UPDATE tasks SET state='completed' WHERE task_id=?", (task.task_id,)
    )


def _table_snapshot(store: SQLiteStore):
    connection = store._require_writer()
    names = tuple(row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ))
    return names, tuple(
        (name, tuple(connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid')))
        for name in names
    )


def _rewrite_command_result(store: SQLiteStore, task_name: str, mutate) -> None:
    command = command_id(
        "terminal", store._resume_confirmed, _task(store, task_name), 1
    )
    connection = store._require_writer()
    result = json.loads(connection.execute(
        "SELECT canonical_result_facts FROM commands WHERE command_id=?", (command,)
    ).fetchone()[0])
    mutate(result)
    connection.execute(
        "UPDATE commands SET canonical_result_facts=? WHERE command_id=?",
        (json.dumps(result, sort_keys=True, separators=(",", ":")), command),
    )


def _mutate(store: SQLiteStore, kind: str) -> None:
    connection = store._require_writer()
    now = NOW.isoformat()
    if kind == "task_only_completed":
        connection.execute("UPDATE tasks SET state='completed' WHERE ordinal=1")
    elif kind == "orphan_completed_attempt":
        task = _task(store, "implementation")
        _insert_attempt(store, Attempt.pending(
            "att_implementation_1", task.task_id, 1
        ).start().complete("orphan"))
    elif kind in {"terminal_command_wrong_kind", "terminal_command_wrong_attempt",
                  "terminal_command_extra_field", "missing_evidence",
                  "unreferenced_extra_evidence", "evidence_hash_drift",
                  "handoff_hash_drift", "handoff_wrong_target",
                  "handoff_evidence_drift", "non_acceptance_missing_handoff"}:
        seed_closed_stage(store, "implementation")
        if kind == "terminal_command_wrong_kind":
            connection.execute("UPDATE commands SET command_kind='wrong_kind'")
        elif kind == "terminal_command_wrong_attempt":
            _rewrite_command_result(store, "implementation", lambda value: value.update(
                attempt_id="att_hostile"
            ))
        elif kind == "terminal_command_extra_field":
            _rewrite_command_result(store, "implementation", lambda value: value.update(
                extra="hostile"
            ))
        elif kind == "missing_evidence":
            connection.execute("DELETE FROM handoffs")
            connection.execute("DELETE FROM evidence")
        elif kind == "unreferenced_extra_evidence":
            evidence = Evidence.create("ev_extra", EvidenceKind.ARTIFACT_HASH, {
                "artifact_reference": "extra", "content_hash": "a" * 64,
            })
            connection.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?)", (
                evidence.evidence_id, "tsk_implementation", "att_implementation_1",
                evidence.kind.value, evidence.canonical_content,
                sha256(evidence.canonical_content.encode()).hexdigest(), now,
            ))
        elif kind == "evidence_hash_drift":
            connection.execute("UPDATE evidence SET content_hash=?", ("f" * 64,))
        elif kind == "handoff_hash_drift":
            connection.execute("UPDATE handoffs SET content_hash=?", ("f" * 64,))
        elif kind == "handoff_wrong_target":
            connection.execute(
                "UPDATE handoffs SET target_task_id='tsk_revision'"
            )
        elif kind == "handoff_evidence_drift":
            row = connection.execute(
                "SELECT canonical_handoff_facts FROM handoffs"
            ).fetchone()
            payload = json.loads(row[0])
            payload["verification_evidence_ids"] = ["ev_hostile"]
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "UPDATE handoffs SET canonical_handoff_facts=?,content_hash=?",
                (canonical, sha256(canonical.encode()).hexdigest()),
            )
        else:
            connection.execute("DELETE FROM handoffs")
            _rewrite_command_result(
                store, "implementation", lambda value: value.update(handoff_id=None)
            )
    elif kind == "acceptance_has_handoff":
        for name in ("implementation", "review", "revision", "acceptance"):
            seed_closed_stage(store, name)
        source = "att_acceptance_1"
        evidence = "ev_acceptance_1"
        handoff = Handoff.create(
            "hnd_acceptance_1", source, "tsk_implementation",
            "acceptance complete", (evidence,),
        )
        connection.execute("INSERT INTO handoffs VALUES (?,?,?,?,?,?,?)", (
            handoff.handoff_id, source, handoff.target_task_id,
            handoff.result_summary, handoff.canonical_content,
            handoff.content_hash, now,
        ))
        _rewrite_command_result(
            store, "acceptance", lambda value: value.update(handoff_id=handoff.handoff_id)
        )
    elif kind == "attempt_ordinal_gap":
        seed_interrupted_attempt(store, "implementation", 1)
        seed_interrupted_attempt(store, "implementation", 3)
    elif kind == "completed_stage_after_open_stage":
        seed_interrupted_attempt(store, "implementation", 1)
        seed_closed_stage(store, "review")
    elif kind == "second_executing_mission":
        other = store._resume_draft.revise(objective="A second mission")
        preview = other.preview(1)
        confirmed = preview.confirm(
            preview_id=preview.preview_id, content_hash=preview.content_hash
        )
        connection.execute("INSERT INTO missions VALUES (?,?,?,?,?,?)", (
            confirmed.mission_id, "ses_1", "confirmed", 1, now, now,
        ))
        connection.execute("INSERT INTO mission_versions VALUES (?,?,?,?,?,?)", (
            confirmed.mission_id, 1, preview.preview_id, confirmed.content_hash,
            confirmed.canonical_content, now,
        ))
    elif kind == "pending_exit_present":
        connection.execute("""UPDATE product_sessions SET
            pending_exit_id='xrt_0123456789abcdef0123456789abcdef',
            pending_exit_attempt_id='att_implementation_1',
            canonical_pending_exit_attempt_facts='{}',
            pending_exit_attempt_hash=?,pending_exit_requested_at=?""",
            ("a" * 64, now),
        )
    else:
        connection.execute("UPDATE product_sessions SET state='running'")


def test_resume_projection_derives_first_unclosed_stage_from_closed_prefix(store):
    seed_closed_stage(store, "implementation")

    snapshot = store.load_execution_resume("ses_1")

    assert snapshot.closed_stage_count == 1
    assert snapshot.first_unclosed_task_id == "tsk_review"
    assert snapshot.max_prior_attempt_ordinal == 0
    assert snapshot.next_attempt_ordinal == 1
    assert snapshot.preceding_handoff_id == "hnd_implementation_1"


def test_interrupted_stage_projects_higher_ordinal(store):
    seed_closed_stage(store, "implementation")
    seed_interrupted_attempt(store, "review", 1)
    seed_interrupted_attempt(store, "review", 2)

    snapshot = store.load_execution_resume("ses_1")

    assert snapshot.first_unclosed_task_id == "tsk_review"
    assert snapshot.max_prior_attempt_ordinal == 2
    assert snapshot.next_attempt_ordinal == 3


def test_resume_projection_blocks_outcome_unknown(store):
    seed_outcome_unknown_attempt(store, "implementation", 1)

    with pytest.raises(ExecutionResumeProjectionError, match="resume_outcome_unknown"):
        store.load_execution_resume("ses_1")


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_resume_projection_failures_are_allowlisted_and_read_only(store, mutation):
    _mutate(store, mutation)
    before = _table_snapshot(store)

    with pytest.raises(ExecutionResumeProjectionError) as captured:
        store.load_execution_resume("ses_1")

    assert captured.value.code in ExecutionResumeProjectionError.ALLOWED_CODES
    assert _table_snapshot(store) == before


def test_resume_projection_preserves_v2_schema_and_table_list(store):
    before = _table_snapshot(store)

    store.load_execution_resume("ses_1")

    assert _table_snapshot(store) == before
    assert store._require_writer().execute(
        "SELECT schema_version FROM schema_metadata"
    ).fetchone() == (2,)


@pytest.mark.parametrize(
    ("stages", "evidence_id"),
    [
        (("implementation", "review"), "ev_review_1"),
        (
            ("implementation", "review", "revision", "acceptance"),
            "ev_acceptance_1",
        ),
    ],
)
def test_resume_projection_rejects_rehashed_cross_stage_evidence_drift(
    store, stages, evidence_id,
):
    for name in stages:
        seed_closed_stage(store, name)
    connection = store._require_writer()
    canonical = connection.execute(
        "SELECT canonical_evidence_facts FROM evidence WHERE evidence_id=?",
        (evidence_id,),
    ).fetchone()[0]
    payload = json.loads(canonical)
    if evidence_id == "ev_review_1":
        payload["evidence_ids"] = ["ev_missing"]
    else:
        criterion = store._resume_draft.acceptance_criteria[0]
        payload["evidence_by_criterion"][criterion] = ["ev_missing"]
    changed = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    connection.execute(
        "UPDATE evidence SET canonical_evidence_facts=?,content_hash=? "
        "WHERE evidence_id=?",
        (changed, sha256(changed.encode()).hexdigest(), evidence_id),
    )
    before = _table_snapshot(store)

    with pytest.raises(ExecutionResumeProjectionError):
        store.load_execution_resume("ses_1")

    assert _table_snapshot(store) == before
