from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.execution_authority import attempt_snapshot
from agentdeck.application.execution_records import (
    command_id, evidence_snapshot, handoff_snapshot, terminal_command_result,
    validated_terminal_bundle,
)
from agentdeck.kernel.execution import Attempt, Evidence, EvidenceKind, Handoff
from product_kernel.fakes import FrozenClock
from product_kernel.test_execution_budgets import SafeWorkerFailure
from product_kernel.test_execution_coordinator import Harness


NOW = datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc)


def _seed_lineage(store: SQLiteStore) -> None:
    connection = store._require_writer()
    now = NOW.isoformat()
    connection.execute("INSERT INTO projects VALUES ('prj_1', ?, ?)", (str(store._project_root), now))
    connection.execute(
        """INSERT INTO product_sessions (
               session_id,project_id,state,permission_profile,pending_goal,
               created_at,updated_at,leader_backend,leader_model)
           VALUES ('ses_1','prj_1','running','approve_for_me',NULL,?,?,?,?)""",
        (now, now, "codex-cli", "native-default"),
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


def _persisted_bundle(store):
    started = Attempt.pending("att_impl_1", "tsk_implementation", 1).start()
    store.execute_once("cmd_started", "execution_attempt_started",
        lambda transaction: transaction.save_aggregate(
            "attempts", started.attempt_id, _attempt_snapshot(started)
        ) or {"attempt_id": started.attempt_id})
    terminal = started.complete("implementation complete")
    evidence = Evidence.create("ev_implementation_1", EvidenceKind.ARTIFACT_HASH,
        {"artifact_reference": "workspace patch", "content_hash": "b" * 64})
    handoff = Handoff.create("hnd_implementation_1", terminal.attempt_id,
        "tsk_review", "implementation complete", (evidence.evidence_id,),
        artifact_references=("workspace patch",))
    confirmed = SimpleNamespace(mission_id="msn_1", version=1)
    task = SimpleNamespace(task_id=terminal.task_id,
                           agent_instance_id="agt_implementation")
    result = terminal_command_result(confirmed, task, terminal, (evidence,), handoff)
    committed = store.execute_once("cmd_terminal", "execution_stage_committed",
        lambda transaction: _commit_execution(
            transaction, terminal, evidence, handoff, result))
    return terminal, evidence, handoff, confirmed, task, result, committed


@pytest.mark.parametrize("aggregate", ["attempts", "evidence", "handoffs"])
def test_execution_aggregate_readback_is_the_exact_canonical_snapshot(
    tmp_path, aggregate,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        _seed_lineage(store)
        terminal, evidence, handoff, *_ = _persisted_bundle(store)
        expected = {
            "attempts": _attempt_snapshot(terminal),
            "evidence": evidence_snapshot(evidence, terminal),
            "handoffs": handoff_snapshot(handoff),
        }[aggregate]
        identity = {"attempts": terminal.attempt_id,
                    "evidence": evidence.evidence_id,
                    "handoffs": handoff.handoff_id}[aggregate]

        assert store.load_aggregate(aggregate, identity) == expected
    finally:
        store.close()


def test_fresh_and_replayed_terminal_bundle_validate_from_sqlite_snapshots(
    tmp_path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        _seed_lineage(store)
        terminal, evidence, handoff, confirmed, task, expected, fresh = (
            _persisted_bundle(store)
        )
        replay = store.execute_once(
            "cmd_terminal", "execution_stage_committed",
            lambda transaction: (_ for _ in ()).throw(
                AssertionError("terminal replay invoked callback")),
        )
        snapshots = (
            store.load_aggregate("attempts", terminal.attempt_id),
            (store.load_aggregate("evidence", evidence.evidence_id),),
            store.load_aggregate("handoffs", handoff.handoff_id),
        )

        assert fresh == replay == expected
        for result in (fresh, replay):
            loaded = validated_terminal_bundle(
                result, confirmed, task, terminal, (evidence,), handoff, "acp_1",
                *snapshots,
            )
            assert loaded.attempt == terminal
            assert loaded.evidence == (evidence,)
            assert loaded.handoff == handoff
    finally:
        store.close()


def _stopped_result(harness, request, terminal):
    return {
        "mission_id": harness.confirmed.mission_id,
        "mission_version": harness.confirmed.version,
        "task_id": request.task_id, "attempt_id": request.attempt_id,
        "state": terminal.state.value, "reason": terminal.reason,
        "retryable": terminal.retryable, "acp_session_id": None,
    }


def _corrupt_stopped(kind, command, snapshot):
    if kind == "missing_result_field":
        command.pop("mission_version")
    elif kind == "legacy_result":
        command.clear(); command.update(attempt_id="att_old", state="failed")
    elif kind.startswith("wrong_"):
        field = kind.removeprefix("wrong_")
        command[field] = {
            "mission_id": "msn_hostile", "task_id": "tsk_hostile",
            "attempt_id": "att_hostile", "state": "running",
            "reason": "hostile-marker", "retryable": False,
            "acp_session_id": "acp_hostile",
        }[field]
    elif kind == "mismatched_snapshot":
        snapshot.update(state="running", reason=None, retryable=False)


class StoppedReplayWorker:
    def __init__(self, harness, task_name):
        self.harness, self.task_name = harness, task_name

    async def start_task(self, request):
        self.harness.started_tasks.append(self.task_name)
        if not self.harness.replay_seeded:
            self.harness.replay_seeded = True
            task = next(item for item in self.harness.draft.tasks
                        if item.task_id == request.task_id)
            terminal = Attempt.pending(
                request.attempt_id, request.task_id, 1
            ).start().fail("worker_schema_invalid", retryable=True)
            command = _stopped_result(self.harness, request, terminal)
            snapshot = attempt_snapshot(terminal, task)
            _corrupt_stopped(self.harness.stop_corruption, command, snapshot)
            key = (command_id("stop", self.harness.confirmed, task, 1),
                   "execution_attempt_stopped")
            store = self.harness.store
            if hasattr(store, "commands"):
                store.commands[key] = command
                if self.harness.stop_corruption == "missing_snapshot":
                    store.aggregates.pop(("attempts", request.attempt_id), None)
                else:
                    store.aggregates[("attempts", request.attempt_id)] = snapshot
            else:
                store.execute_once(key[0], key[1], lambda transaction: (
                    transaction.save_aggregate(
                        "attempts", request.attempt_id, snapshot
                    ) or command
                ))
                if self.harness.stop_corruption == "missing_snapshot":
                    store._require_writer().execute(
                        "DELETE FROM attempts WHERE attempt_id=?", (request.attempt_id,)
                    )
                elif self.harness.stop_corruption == "mismatched_snapshot":
                    store._require_writer().execute(
                        "UPDATE attempts SET state='running',reason=NULL,retryable=0 "
                        "WHERE attempt_id=?", (request.attempt_id,)
                    )
            raise SafeWorkerFailure(
                "worker_schema_invalid", outcome_known=True, retryable=True,
                task_id=request.task_id, attempt_id=request.attempt_id,
            )
        raise SafeWorkerFailure(
            "worker_outcome_unknown", outcome_known=False, retryable=False,
            task_id=request.task_id, attempt_id=request.attempt_id,
        )
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("corruption", [
    "missing_result_field", "legacy_result", "wrong_mission_id",
    "wrong_task_id", "wrong_attempt_id", "wrong_state", "wrong_reason",
    "wrong_retryable", "wrong_acp_session_id", "missing_snapshot",
    "mismatched_snapshot",
])
def test_stopped_attempt_replay_requires_exact_command_and_snapshot(
    tmp_path, backend, corruption,
) -> None:
    store = None if backend == "memory" else SQLiteStore.open(
        tmp_path, clock=FrozenClock(NOW)
    )
    try:
        if store is not None:
            _seed_lineage(store)
        harness = Harness() if store is None else Harness(store=store)
        harness.replay_seeded = False
        harness.stop_corruption = corruption
        harness.service._worker_factory = lambda task: StoppedReplayWorker(
            harness, task.name
        )

        result = asyncio.run(harness.run())
        assert result.diagnostic.code == "terminal_attempt_persistence_failed"
        assert harness.started_tasks == ["implementation"]
        assert "hostile-marker" not in result.diagnostic.cause
    finally:
        if store is not None:
            store.close()


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_exact_stopped_attempt_replay_allows_only_bounded_attempt_two(
    tmp_path, backend,
) -> None:
    store = None if backend == "memory" else SQLiteStore.open(
        tmp_path, clock=FrozenClock(NOW)
    )
    try:
        if store is not None:
            _seed_lineage(store)
        harness = Harness() if store is None else Harness(store=store)
        harness.replay_seeded = False
        harness.stop_corruption = "exact"
        harness.service._worker_factory = lambda task: StoppedReplayWorker(
            harness, task.name
        )

        result = asyncio.run(harness.run())
        assert harness.started_tasks == ["implementation", "implementation"]
        assert len(result.attempts) == 2
        assert result.diagnostic.code == "outcome_unknown"
    finally:
        if store is not None:
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
            """INSERT INTO product_sessions (
                   session_id,project_id,state,permission_profile,pending_goal,
                   created_at,updated_at,leader_backend,leader_model)
               VALUES ('ses_other','prj_1','running','approve_for_me',NULL,?,?,?,?)""",
            (now, now, "codex-cli", "native-default"),
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


def _commit_execution(transaction, attempt, evidence, handoff, result=None):
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
    return result or {"attempt_id": attempt.attempt_id,
                      "handoff_id": handoff.handoff_id}
