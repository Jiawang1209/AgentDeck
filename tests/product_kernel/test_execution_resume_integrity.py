from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json

import pytest

import agentdeck.adapters.sqlite_execution_resume as sqlite_resume_module
import agentdeck.application.execution_resume as resume_module
from agentdeck.application.execution_resume import ExecutionResumePlanner
from agentdeck.kernel.execution import Attempt, Evidence, EvidenceKind, Handoff
from agentdeck.ports.execution_resume import ExecutionResumeProjectionError
from product_kernel.test_sqlite_execution_resume import (
    NOW,
    _insert_attempt,
    _rewrite_command_result,
    _table_snapshot,
    seed_closed_stage,
    seed_interrupted_attempt,
    store,
)


@pytest.mark.parametrize("scenario", ("current", "prior_failed", "prior", "later"))
@pytest.mark.parametrize("fact_kind", ("evidence", "handoff"))
def test_projection_rejects_nonterminal_or_later_attempt_facts_read_only(
    store, scenario, fact_kind,
) -> None:
    seed_closed_stage(store, "implementation")
    task_name, attempt_id = "review", "att_review_1"
    if scenario == "prior_failed":
        task = next(item for item in store._resume_draft.tasks if item.name == "review")
        _insert_attempt(store, Attempt.pending(
            attempt_id, task.task_id, 1).start().fail("worker_failed", retryable=True))
        seed_interrupted_attempt(store, "review", 2)
    elif scenario == "prior":
        seed_interrupted_attempt(store, "review", 1)
        seed_interrupted_attempt(store, "review", 2)
    elif scenario == "later":
        task_name, attempt_id = "revision", "att_revision_1"
        seed_interrupted_attempt(store, task_name, 1)
    else:
        seed_interrupted_attempt(store, task_name, 1)
    task = next(item for item in store._resume_draft.tasks if item.name == task_name)
    connection, now = store._require_writer(), NOW.isoformat()
    if fact_kind == "evidence":
        evidence = Evidence.create("ev_orphan", EvidenceKind.ARTIFACT_HASH, {
            "artifact_reference": "orphan", "content_hash": "a" * 64})
        connection.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?)", (
            evidence.evidence_id, task.task_id, attempt_id, evidence.kind.value,
            evidence.canonical_content, sha256(evidence.canonical_content.encode()).hexdigest(),
            now))
    else:
        target = store._resume_draft.tasks[store._resume_draft.tasks.index(task) + 1]
        handoff = Handoff.create("hnd_orphan", attempt_id, target.task_id,
                                 "orphan", ("ev_orphan",))
        connection.execute("INSERT INTO handoffs VALUES (?,?,?,?,?,?,?)", (
            handoff.handoff_id, attempt_id, target.task_id, handoff.result_summary,
            handoff.canonical_content, handoff.content_hash, now))
    before = _table_snapshot(store)

    with pytest.raises(ExecutionResumeProjectionError) as captured:
        store.load_execution_resume("ses_1")

    assert captured.value.code == "resume_projection_malformed"
    assert _table_snapshot(store) == before


@pytest.mark.parametrize(
    ("stage", "kind", "payload"),
    (("implementation", EvidenceKind.DIFF_IDENTITY,
      {"base": "a", "head": "b", "diff_hash": "c" * 64}),
     ("revision", EvidenceKind.ARTIFACT_HASH,
      {"artifact_reference": "wrong", "content_hash": "d" * 64})),
)
def test_projection_rejects_stage_evidence_kind_substitution_read_only(
    store, stage, kind, payload,
) -> None:
    for name in ("implementation", "review", "revision"):
        seed_closed_stage(store, name)
        if name == stage:
            break
    evidence_id = f"ev_{stage}_1"
    evidence = Evidence.create(evidence_id, kind, payload)
    store._require_writer().execute(
        "UPDATE evidence SET kind=?,canonical_evidence_facts=?,content_hash=? "
        "WHERE evidence_id=?", (kind.value, evidence.canonical_content,
        sha256(evidence.canonical_content.encode()).hexdigest(), evidence_id))
    before = _table_snapshot(store)

    with pytest.raises(ExecutionResumeProjectionError, match="resume_projection_malformed"):
        store.load_execution_resume("ses_1")

    assert _table_snapshot(store) == before


def test_projection_rejects_stage_evidence_cardinality_drift_read_only(store) -> None:
    seed_closed_stage(store, "implementation")
    extra = Evidence.create("ev_implementation_extra", EvidenceKind.ARTIFACT_HASH, {
        "artifact_reference": "extra", "content_hash": "e" * 64})
    connection, now = store._require_writer(), NOW.isoformat()
    connection.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?)", (
        extra.evidence_id, "tsk_implementation", "att_implementation_1",
        extra.kind.value, extra.canonical_content,
        sha256(extra.canonical_content.encode()).hexdigest(), now))
    _rewrite_command_result(store, "implementation",
                            lambda value: value["evidence_ids"].append(extra.evidence_id))
    row = connection.execute("SELECT canonical_handoff_facts FROM handoffs").fetchone()
    payload = json.loads(row[0]); payload["verification_evidence_ids"].append(extra.evidence_id)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    connection.execute("UPDATE handoffs SET canonical_handoff_facts=?,content_hash=?",
                       (canonical, sha256(canonical.encode()).hexdigest()))
    before = _table_snapshot(store)

    with pytest.raises(ExecutionResumeProjectionError, match="resume_projection_malformed"):
        store.load_execution_resume("ses_1")

    assert _table_snapshot(store) == before


def _deep_mission_json() -> str:
    depth = 35_000
    value = '{"x":' * depth + "0" + "}" * depth
    assert 200_000 < len(value.encode()) < 1_048_576
    return value


@pytest.mark.parametrize("entry", ("sqlite", "planner"))
@pytest.mark.parametrize("failure", ("deep_json", "overflow"))
def test_resume_entrypoints_sanitize_hostile_structure(
    store, monkeypatch, entry, failure,
) -> None:
    snapshot = store.load_execution_resume("ses_1")
    if failure == "overflow":
        module = sqlite_resume_module if entry == "sqlite" else resume_module
        def overflow(*_args, **_kwargs):
            raise OverflowError("hostile size")
        monkeypatch.setattr(module, "_canonical_mission_draft", overflow)
    else:
        raw = _deep_mission_json()
        if entry == "sqlite":
            store._require_writer().execute(
                "UPDATE mission_versions SET canonical_mission_facts=?,content_hash=?",
                (raw, sha256(raw.encode()).hexdigest()))
        else:
            snapshot = type(snapshot).create(replace(
                snapshot.facts, canonical_mission_facts=raw,
                mission_content_hash=sha256(raw.encode()).hexdigest()))
    before = _table_snapshot(store)

    with pytest.raises(ExecutionResumeProjectionError) as captured:
        (store.load_execution_resume("ses_1") if entry == "sqlite"
         else ExecutionResumePlanner().materialize(snapshot))

    assert captured.value.code == "resume_projection_malformed"
    assert _table_snapshot(store) == before
