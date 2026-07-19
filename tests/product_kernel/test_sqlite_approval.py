from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from agentdeck.adapters.sqlite import SQLiteStore, StoreSerializationError
from product_kernel.fakes import FrozenClock


NOW = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)


def seed_lineage(store: SQLiteStore) -> None:
    now = NOW.isoformat()
    connection = store._writer
    connection.execute("INSERT INTO projects VALUES (?,?,?)", ("prj_1", "/tmp/project", now))
    connection.execute(
        "INSERT INTO product_sessions VALUES (?,?,?,?,?,?,?)",
        ("ses_1", "prj_1", "running", "approve_for_me", None, now, now),
    )
    connection.execute(
        "INSERT INTO missions VALUES (?,?,?,?,?,?)",
        ("msn_1", "ses_1", "running", 1, now, now),
    )
    connection.execute(
        "INSERT INTO mission_versions VALUES (?,?,?,?,?,?)",
        ("msn_1", 1, "prv_1", "h" * 64, "{}", now),
    )
    connection.execute(
        "INSERT INTO agent_instances VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("agt_1", "ses_1", "codex", "acp", "1", "implementer", "ses_acp", "active", now, now),
    )
    connection.execute(
        "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("tsk_1", "msn_1", 1, 1, "implementation", "implementer", "codex", "agt_1", "acp", "running", "{}", now, now),
    )
    connection.execute(
        "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("att_1", "tsk_1", "agt_1", 1, "running", None, None, 0, "ses_acp", 0, now, now),
    )


def snapshot(state: str, decision=None):
    return {
        "approval_id": "apv_1", "mission_id": "msn_1", "mission_version": 1,
        "attempt_id": "att_1", "effect": "write_project", "state": state,
        "scope_hash": "a" * 64,
        "request": {
            "mission_id": "msn_1", "mission_version": 1,
            "task_id": "tsk_1", "agent_id": "agt_1", "attempt_id": "att_1",
            "permission_request_id": "perm_1", "effect": "write_project",
            "risk": "project mutation",
        },
        "decision": decision, "requested_at": NOW.isoformat(),
        "decided_at": None if decision is None else NOW.isoformat(),
    }


def test_sqlite_persists_request_then_decision_as_command_atomic_rows(tmp_path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        seed_lineage(store)
        store.execute_once("cmd_request", "approval_request", lambda tx: _save(tx, snapshot("pending")))
        decision = {"reviewer_id": "agentdeck", "allowed": True, "reason": "routine_project_effect"}
        store.execute_once("cmd_decision", "approval_decision", lambda tx: _save(tx, snapshot("approved", decision)))

        row = store.connection.execute(
            "SELECT state,canonical_request_facts,canonical_decision_facts FROM approvals"
        ).fetchone()
        assert row[0] == "approved"
        assert json.loads(row[1])["permission_request_id"] == "perm_1"
        assert json.loads(row[2]) == decision
        assert store.count("events") == 2
    finally:
        store.close()


def test_sqlite_rejects_decision_drift_and_rolls_back_event(tmp_path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        seed_lineage(store)
        store.execute_once("cmd_request", "approval_request", lambda tx: _save(tx, snapshot("pending")))
        decision = {"reviewer_id": "agentdeck", "allowed": True, "reason": "safe"}
        store.execute_once("cmd_decision", "approval_decision", lambda tx: _save(tx, snapshot("approved", decision)))
        drift = {"reviewer_id": "agentdeck", "allowed": False, "reason": "changed"}
        with pytest.raises((ValueError, StoreSerializationError)):
            store.execute_once("cmd_drift", "approval_decision", lambda tx: _save(tx, snapshot("denied", drift)))
        assert store.count("approvals") == 1
        assert store.count("events") == 2
    finally:
        store.close()


def test_sqlite_rejects_cross_mission_attempt_lineage(tmp_path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        seed_lineage(store)
        connection = store._writer
        connection.execute(
            "INSERT INTO missions VALUES (?,?,?,?,?,?)",
            ("msn_2", "ses_1", "running", 1, NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO mission_versions VALUES (?,?,?,?,?,?)",
            ("msn_2", 1, "prv_2", "j" * 64, "{}", NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tsk_2", "msn_2", 1, 1, "implementation", "implementer",
             "codex", "agt_1", "acp", "running", "{}", NOW.isoformat(),
             NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("att_2", "tsk_2", "agt_1", 1, "running", None, None, 0,
             "ses_acp", 0, NOW.isoformat(), NOW.isoformat()),
        )
        value = json.loads(json.dumps(snapshot("pending")))
        value["attempt_id"] = "att_2"
        value["request"]["attempt_id"] = "att_2"
        value["request"]["task_id"] = "tsk_2"

        with pytest.raises(
            StoreSerializationError,
            match="approval durable lineage is inconsistent",
        ):
            store.execute_once(
                "cmd_cross_mission", "approval_request",
                lambda tx: _save(tx, value),
            )
        assert store.count("approvals") == 0
        assert store.count("events") == 0
    finally:
        store.close()


def _save(transaction, value):
    transaction.save_aggregate("approvals", "apv_1", value)
    transaction.append_event({
        "event_id": "evt_" + value["state"], "kind": "approval_" + value["state"],
        "aggregate_type": "approval", "aggregate_id": "apv_1", "payload": {},
        "occurred_at": NOW.isoformat(),
    })
    return {"approval_id": "apv_1", "state": value["state"]}
