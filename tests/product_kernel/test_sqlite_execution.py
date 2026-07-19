from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.kernel.execution import Attempt, Evidence, EvidenceKind, Handoff
from product_kernel.fakes import FrozenClock


NOW = datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc)


def _seed_lineage(store: SQLiteStore) -> None:
    connection = store._require_writer()
    now = NOW.isoformat()
    connection.execute("INSERT INTO projects VALUES ('prj_1', ?, ?)", (str(store._project_root), now))
    connection.execute(
        "INSERT INTO product_sessions VALUES ('ses_1','prj_1','running','approve_for_me',NULL,?,?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO agent_instances VALUES ('agt_implementation','ses_1','codex-cli','acp','1','implementer','acp_1','active',?,?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO missions VALUES ('msn_1','ses_1','running',1,?,?)", (now, now)
    )
    connection.execute(
        "INSERT INTO mission_versions VALUES ('msn_1',1,'prv_1',?, '{}', ?)",
        ("a" * 64, now),
    )
    task_ids = ("tsk_implementation", "tsk_review")
    for ordinal, task_id in enumerate(task_ids, 1):
        agent = "agt_implementation"
        dependencies = [] if ordinal == 1 else [task_ids[ordinal - 2]]
        canonical = json.dumps(
            {"task_id": task_id, "agent_instance_id": agent,
             "dependencies": dependencies},
            sort_keys=True, separators=(",", ":"),
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,'implementer','codex-cli',?,'acp://route','running',?,?,?)",
            (task_id, "msn_1", 1, ordinal, task_id, agent, canonical, now, now),
        )


def _attempt_snapshot(attempt: Attempt) -> dict[str, object]:
    return {
        "attempt_id": attempt.attempt_id, "task_id": attempt.task_id,
        "agent_instance_id": "agt_implementation", "ordinal": attempt.ordinal,
        "state": attempt.state.value, "reason": attempt.reason,
        "result_summary": attempt.result_summary, "retryable": attempt.retryable,
        "acp_session_id": "acp_1", "effect_observed": False,
    }


def test_terminal_attempt_evidence_and_handoff_commit_atomically(tmp_path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        _seed_lineage(store)
        started = Attempt.pending("att_impl_1", "tsk_implementation", 1).start()
        store.execute_once(
            "cmd_started", "execution_attempt_started",
            lambda transaction: (
                transaction.save_aggregate("attempts", started.attempt_id, _attempt_snapshot(started))
                or {"attempt_id": started.attempt_id}
            ),
        )
        terminal = started.complete("implementation complete")
        evidence = Evidence.create(
            "ev_implementation_1", EvidenceKind.ARTIFACT_HASH,
            {"artifact_reference": "workspace patch", "content_hash": "b" * 64},
        )
        handoff = Handoff.create(
            "hnd_implementation_1", terminal.attempt_id, "tsk_review",
            "implementation complete", (evidence.evidence_id,),
            artifact_references=("workspace patch",),
        )

        store.execute_once(
            "cmd_terminal", "execution_stage_committed",
            lambda transaction: _commit_execution(
                transaction, terminal, evidence, handoff
            ),
        )

        assert store.count("attempts") == 1
        assert store.count("evidence") == 1
        assert store.count("handoffs") == 1
        row = store.connection.execute(
            "SELECT state,result_summary FROM attempts WHERE attempt_id='att_impl_1'"
        ).fetchone()
        assert row == ("completed", "implementation complete")
    finally:
        store.close()


def test_handoff_lineage_drift_rolls_back_terminal_bundle(tmp_path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        _seed_lineage(store)
        started = Attempt.pending("att_impl_1", "tsk_implementation", 1).start()
        store.execute_once(
            "cmd_started", "execution_attempt_started",
            lambda transaction: (
                transaction.save_aggregate("attempts", started.attempt_id, _attempt_snapshot(started))
                or {"attempt_id": started.attempt_id}
            ),
        )
        terminal = started.complete("implementation complete")
        evidence = Evidence.create(
            "ev_implementation_1", EvidenceKind.ARTIFACT_HASH,
            {"artifact_reference": "workspace patch", "content_hash": "b" * 64},
        )
        wrong = Handoff.create(
            "hnd_wrong", terminal.attempt_id, "tsk_missing", "complete",
            (evidence.evidence_id,),
        )

        with pytest.raises(ValueError, match="handoff durable lineage"):
            store.execute_once(
                "cmd_terminal", "execution_stage_committed",
                lambda transaction: _commit_execution(
                    transaction, terminal, evidence, wrong
                ),
            )

        assert store.count("evidence") == 0
        assert store.count("handoffs") == 0
        state = store.connection.execute(
            "SELECT state FROM attempts WHERE attempt_id='att_impl_1'"
        ).fetchone()[0]
        assert state == "running"
    finally:
        store.close()


def test_handoff_target_must_be_the_frozen_direct_dependency(tmp_path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        _seed_lineage(store)
        now = NOW.isoformat()
        canonical = json.dumps(
            {"task_id": "tsk_acceptance", "agent_instance_id": "agt_implementation",
             "dependencies": ["tsk_review"]},
            sort_keys=True, separators=(",", ":"),
        )
        store._require_writer().execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,'acceptance_reviewer','claude-cli',?,'acp://route','pending',?,?,?)",
            ("tsk_acceptance", "msn_1", 1, 3, "acceptance",
             "agt_implementation", canonical, now, now),
        )
        started = Attempt.pending("att_impl_1", "tsk_implementation", 1).start()
        store.execute_once(
            "cmd_started", "execution_attempt_started",
            lambda transaction: (
                transaction.save_aggregate(
                    "attempts", started.attempt_id, _attempt_snapshot(started)
                ) or {"attempt_id": started.attempt_id}
            ),
        )
        terminal = started.complete("complete")
        evidence = Evidence.create(
            "ev_implementation_1", EvidenceKind.ARTIFACT_HASH,
            {"artifact_reference": "patch", "content_hash": "b" * 64},
        )
        skipped = Handoff.create(
            "hnd_skip", terminal.attempt_id, "tsk_acceptance", "complete",
            (evidence.evidence_id,),
        )

        with pytest.raises(ValueError, match="direct dependency"):
            store.execute_once(
                "cmd_skip", "execution_stage_committed",
                lambda transaction: _commit_execution(
                    transaction, terminal, evidence, skipped
                ),
            )

        assert store.count("handoffs") == 0
        assert store.connection.execute(
            "SELECT state FROM attempts WHERE attempt_id='att_impl_1'"
        ).fetchone()[0] == "running"
    finally:
        store.close()


def test_attempt_agent_must_belong_to_the_mission_product_session(tmp_path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        _seed_lineage(store)
        now = NOW.isoformat()
        store._require_writer().execute(
            "INSERT INTO product_sessions VALUES ('ses_other','prj_1','running','approve_for_me',NULL,?,?)",
            (now, now),
        )
        store._require_writer().execute(
            "INSERT INTO agent_instances VALUES ('agt_other','ses_other','codex-cli','acp','1','implementer','acp_other','active',?,?)",
            (now, now),
        )
        store._require_writer().execute(
            "UPDATE tasks SET planned_agent_instance_id='agt_other' WHERE task_id='tsk_implementation'"
        )
        started = Attempt.pending("att_impl_1", "tsk_implementation", 1).start()
        snapshot = _attempt_snapshot(started)
        snapshot["agent_instance_id"] = "agt_other"
        store.execute_once(
            "cmd_started", "execution_attempt_started",
            lambda transaction: (
                transaction.save_aggregate("attempts", started.attempt_id, snapshot)
                or {"attempt_id": started.attempt_id}
            ),
        )
        terminal = started.complete("complete")
        terminal_snapshot = _attempt_snapshot(terminal)
        terminal_snapshot["agent_instance_id"] = "agt_other"
        evidence = Evidence.create(
            "ev_implementation_1", EvidenceKind.ARTIFACT_HASH,
            {"artifact_reference": "patch", "content_hash": "b" * 64},
        )

        with pytest.raises(ValueError, match="mission session"):
            store.execute_once(
                "cmd_terminal", "execution_stage_committed",
                lambda transaction: (
                    transaction.save_aggregate(
                        "attempts", terminal.attempt_id, terminal_snapshot
                    )
                    or transaction.save_aggregate(
                        "evidence", evidence.evidence_id,
                        {
                            "evidence_id": evidence.evidence_id,
                            "task_id": terminal.task_id,
                            "attempt_id": terminal.attempt_id,
                            "kind": evidence.kind.value,
                            "canonical_evidence_facts": evidence.canonical_content,
                        },
                    )
                    or {"attempt_id": terminal.attempt_id}
                ),
            )
        assert store._require_writer().execute(
            "SELECT state FROM attempts WHERE attempt_id='att_impl_1'"
        ).fetchone()[0] == "running"
    finally:
        store.close()


def _commit_execution(transaction, attempt, evidence, handoff):
    transaction.save_aggregate("attempts", attempt.attempt_id, _attempt_snapshot(attempt))
    transaction.save_aggregate(
        "evidence", evidence.evidence_id,
        {
            "evidence_id": evidence.evidence_id, "task_id": attempt.task_id,
            "attempt_id": attempt.attempt_id, "kind": evidence.kind.value,
            "canonical_evidence_facts": evidence.canonical_content,
        },
    )
    transaction.save_aggregate(
        "handoffs", handoff.handoff_id,
        {
            "handoff_id": handoff.handoff_id,
            "source_attempt_id": handoff.source_attempt_id,
            "target_task_id": handoff.target_task_id,
            "result_summary": handoff.result_summary,
            "canonical_handoff_facts": handoff.canonical_content,
            "content_hash": handoff.content_hash,
        },
    )
    return {"attempt_id": attempt.attempt_id, "handoff_id": handoff.handoff_id}
