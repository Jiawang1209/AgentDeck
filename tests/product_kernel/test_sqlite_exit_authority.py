from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.adapters.sqlite_schema import (
    StoreCommandStateError,
    StoreSerializationError,
)
from agentdeck.application.exit_service import ExitService
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot, ExitRequest
from agentdeck.ports.exit_authority import ActiveExitAuthority
from agentdeck.ports.worker import WorkerHandle

from .fakes import FrozenClock


NOW = datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc)
REQUEST_ID = "xrt_" + "1" * 32
EXIT_COLUMNS = (
    "pending_exit_id", "pending_exit_attempt_id",
    "canonical_pending_exit_attempt_facts", "pending_exit_attempt_hash",
    "pending_exit_requested_at",
)


def _seed_session(store: SQLiteStore) -> None:
    now = NOW.isoformat()
    connection = store._require_writer()
    connection.execute(
        "INSERT INTO projects (project_id,resolved_root,created_at) VALUES (?,?,?)",
        (store._project_id, str(store._project_root), now),
    )
    connection.execute(
        """INSERT INTO product_sessions (
               session_id,project_id,state,permission_profile,pending_goal,
               created_at,updated_at,leader_backend,leader_model)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("ses_1", store._project_id, "running", "approve_for_me", None,
         now, now, "codex-cli", "native-default"),
    )


def _seed_lineage(store: SQLiteStore, states: tuple[str, ...]) -> None:
    _seed_session(store)
    connection = store._require_writer()
    now = NOW.isoformat()
    connection.execute(
        "INSERT INTO missions VALUES (?,?,?,?,?,?)",
        ("msn_1", "ses_1", "running", 1, now, now),
    )
    connection.execute(
        "INSERT INTO mission_versions VALUES (?,?,?,?,?,?)",
        ("msn_1", 1, "prv_1", "a" * 64, "{}", now),
    )
    for ordinal, state in enumerate(states, 1):
        suffix = str(ordinal)
        agent_id, task_id, attempt_id = (
            f"agt_{suffix}", f"tsk_{suffix}", f"att_{suffix}"
        )
        acp_session_id = f"ses_acp_{suffix}"
        connection.execute(
            "INSERT INTO agent_instances VALUES (?,?,?,?,?,?,?,?,?,?)",
            (agent_id, "ses_1", "codex-cli", "acp", "1", "implementer",
             acp_session_id, "active", now, now),
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, "msn_1", 1, ordinal, task_id, "implementer",
             "codex-cli", agent_id, "acp://route", "running", "{}", now, now),
        )
        connection.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (attempt_id, task_id, agent_id, 1, state, None, None, 0,
             acp_session_id, 0, now, now),
        )


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    value = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        yield value
    finally:
        value.close()


def _request() -> ExitRequest:
    snapshot = ExitAttemptSnapshot(
        "att_1", "tsk_1", "agt_1", 1, AttemptState.RUNNING,
        "ses_acp_1", False, "a" * 64,
    )
    return ExitRequest(REQUEST_ID, snapshot, snapshot.content_hash, NOW.isoformat())


def _pending_snapshot(request: ExitRequest) -> dict[str, object]:
    return {
        "pending_exit_id": request.request_id,
        "pending_exit_attempt_id": request.attempt.attempt_id,
        "canonical_pending_exit_attempt_facts": (
            request.attempt.canonical_bytes().decode("utf-8")
        ),
        "pending_exit_attempt_hash": request.attempt_hash,
        "pending_exit_requested_at": request.requested_at,
    }


def _save_session(store: SQLiteStore, changes: dict[str, object]) -> None:
    def save(transaction: object) -> dict[str, object]:
        transaction.save_session({  # type: ignore[attr-defined]
            "session_id": "ses_1", "state": "running", **changes,
        })
        return {"saved": True}
    store.execute_once("session:test", "test_session_exit", save)


def seed_active_exit(store: SQLiteStore) -> ExitRequest:
    _seed_lineage(store, ("running",))
    service = ExitService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        request_id_factory=lambda: REQUEST_ID,
    )
    request = service.request_exit().request
    assert request is not None
    return request


def pending_exit_fields(
    store: SQLiteStore, session_id: str = "ses_1",
) -> tuple[object, ...]:
    row = store.load_aggregate("product_sessions", session_id)
    assert row is not None
    return tuple(row[name] for name in EXIT_COLUMNS)


def test_store_lists_all_and_only_active_exit_attempts(store: SQLiteStore) -> None:
    _seed_lineage(store, (
        "completed", "human_controlled", "running", "awaiting_approval", "failed",
    ))
    snapshots = store.list_active_exit_attempts("ses_1")
    assert [item.attempt_id for item in snapshots] == ["att_2", "att_3", "att_4"]
    assert [item.state for item in snapshots] == [
        AttemptState.HUMAN_CONTROLLED,
        AttemptState.RUNNING,
        AttemptState.AWAITING_APPROVAL,
    ]
    assert all(item.durable_fingerprint is not None for item in snapshots)
    assert snapshots == store.list_active_exit_attempts("ses_1")


def test_transaction_lists_active_attempts_from_live_authority(store):
    _seed_lineage(store, ("running",))
    def inspect(transaction):
        attempts = transaction.list_active_exit_attempts("ses_1")
        assert attempts[0].attempt_id == "att_1"
        return {"count": len(attempts)}
    assert store.execute_once("inspect:attempt", "inspect_attempt", inspect) == {
        "count": 1
    }


def test_active_attempt_listing_strictly_validates_durable_row(store):
    _seed_lineage(store, ("running",))
    connection = store._require_writer()
    connection.execute("PRAGMA ignore_check_constraints=ON")
    connection.execute("UPDATE attempts SET effect_observed=2 WHERE attempt_id='att_1'")
    with pytest.raises(StoreCommandStateError, match="stored attempt"):
        store.list_active_exit_attempts("ses_1")


def test_active_attempt_listing_is_bound_to_typed_product_session(store):
    _seed_lineage(store, ("running",))
    store._require_writer().execute(
        "INSERT INTO product_sessions (session_id,project_id,state,permission_profile,"
        "pending_goal,created_at,updated_at,leader_backend,leader_model) "
        "SELECT 'ses_2',project_id,state,permission_profile,pending_goal,created_at,"
        "updated_at,leader_backend,leader_model FROM product_sessions "
        "WHERE session_id='ses_1'"
    )
    assert store.list_active_exit_attempts("ses_2") == ()
    assert [item.attempt_id for item in store.list_active_exit_attempts("ses_1")] == [
        "att_1"
    ]
    with pytest.raises((TypeError, ValueError), match="session_id"):
        store.list_active_exit_attempts("not-a-session")


def test_pending_exit_write_round_trips_exact_authority(store):
    _seed_session(store)
    request = _request()
    _save_session(store, _pending_snapshot(request))
    assert pending_exit_fields(store) == tuple(
        _pending_snapshot(request)[column] for column in EXIT_COLUMNS
    )


@pytest.mark.parametrize("missing", EXIT_COLUMNS)
def test_partial_pending_exit_group_is_rejected_without_writes(store, missing):
    _seed_session(store)
    changes = _pending_snapshot(_request())
    del changes[missing]
    before = store.load_aggregate("product_sessions", "ses_1")
    with pytest.raises(StoreSerializationError, match="closed group"):
        _save_session(store, changes)
    assert store.load_aggregate("product_sessions", "ses_1") == before
    assert store.count("commands") == 0


def test_omitted_pending_preserves_and_explicit_none_clears(store):
    _seed_session(store)
    _save_session(store, _pending_snapshot(_request()))
    def save(transaction):
        transaction.save_session({"session_id": "ses_1", "state": "paused"})
        transaction.save_session({
            "session_id": "ses_1", "state": "running", **dict.fromkeys(EXIT_COLUMNS),
        })
        return {"cleared": True}
    store.execute_once("session:clear", "clear_session_exit", save)
    assert pending_exit_fields(store) == (None,) * 5


@pytest.mark.parametrize(
    "change",
    (
        {"pending_exit_id": "xrt_" + "A" * 32},
        {"pending_exit_attempt_id": "att_other"},
        {"canonical_pending_exit_attempt_facts": "{}"},
        {"pending_exit_attempt_hash": "f" * 64},
    ),
)
def test_pending_exit_rejects_identity_or_canonical_drift(store, change):
    _seed_session(store)
    with pytest.raises(StoreSerializationError):
        _save_session(store, _pending_snapshot(_request()) | change)
    assert store.count("commands") == 0
    assert pending_exit_fields(store) == (None,) * 5


def test_load_rejects_durably_malformed_pending_exit_snapshot(store):
    _seed_session(store)
    pending = _pending_snapshot(_request())
    store._require_writer().execute(
        "UPDATE product_sessions SET pending_exit_id=?,pending_exit_attempt_id=?,"
        "canonical_pending_exit_attempt_facts=?,pending_exit_attempt_hash=?,"
        "pending_exit_requested_at=? WHERE session_id='ses_1'",
        tuple(pending[column] for column in EXIT_COLUMNS),
    )
    store._require_writer().execute(
        "UPDATE product_sessions SET canonical_pending_exit_attempt_facts='{}' "
        "WHERE session_id='ses_1'"
    )
    with pytest.raises(StoreCommandStateError, match="stored session"):
        store.load_aggregate("product_sessions", "ses_1")


# Task 15B transaction-local active-exit authority projection.

@pytest.fixture
def active_store(store):
    seed_active_exit(store)
    return store


def test_public_and_transaction_reads_share_exact_projection(active_store):
    public = active_store.load_active_exit_authority("ses_1")
    assert type(public) is ActiveExitAuthority
    def read(transaction):
        local = transaction.load_active_exit_authority("ses_1")
        assert local == public
        return {"authority_hash": local.content_hash}
    assert active_store.execute_once(
        "exit-authority:read", "read_exit_authority", read
    ) == {"authority_hash": public.content_hash}


def test_transaction_read_observes_live_drift_and_rolls_back(active_store):
    before = active_store.load_active_exit_authority("ses_1")
    class Rollback(RuntimeError):
        pass
    def drift(transaction):
        active_store._require_writer().execute(
            "UPDATE agent_instances SET state='ready' WHERE instance_id='agt_1'"
        )
        assert transaction.load_active_exit_authority(
            "ses_1"
        ).content_hash != before.content_hash
        raise Rollback
    with pytest.raises(Rollback):
        active_store.execute_once("exit-authority:drift", "drift_authority", drift)
    assert active_store.load_active_exit_authority("ses_1") == before


@pytest.mark.parametrize("table", ["agent_instances", "missions", "tasks"])
def test_any_valid_lineage_row_drift_changes_projection_hash(active_store, table):
    statements = {
        "agent_instances": "UPDATE agent_instances SET state='ready'",
        "missions": "UPDATE missions SET state='confirmed'",
        "tasks": "UPDATE tasks SET state='pending'",
    }
    before = active_store.load_active_exit_authority("ses_1")
    active_store._require_writer().execute(statements[table])
    assert active_store.load_active_exit_authority(
        "ses_1"
    ).content_hash != before.content_hash


@pytest.mark.parametrize("corruption", ["missing", "partial", "duplicate"])
def test_missing_partial_or_duplicate_lineage_fails_closed(active_store, corruption):
    connection = active_store._require_writer()
    if corruption == "missing":
        connection.execute("DELETE FROM attempts WHERE attempt_id='att_1'")
    elif corruption == "partial":
        connection.execute("UPDATE agent_instances SET acp_session_id=NULL")
    else:
        now = NOW.isoformat()
        connection.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("att_2", "tsk_1", "agt_1", 2, "running", None, None, 0,
             "ses_acp_1", 0, now, now),
        )
    with pytest.raises(ValueError, match="active exit authority"):
        active_store.load_active_exit_authority("ses_1")


@pytest.mark.parametrize("corruption", ["transport", "oversize"])
def test_malformed_or_oversized_projection_fails_closed(active_store, corruption):
    connection = active_store._require_writer()
    if corruption == "transport":
        connection.execute("UPDATE agent_instances SET transport='pty'")
    else:
        oversized = "ses_" + "x" * 5000
        connection.execute("UPDATE agent_instances SET acp_session_id=?", (oversized,))
        connection.execute("UPDATE attempts SET acp_session_id=?", (oversized,))
    with pytest.raises(ValueError, match="active exit authority"):
        active_store.load_active_exit_authority("ses_1")


def test_transaction_projection_never_uses_public_connection(
    active_store, monkeypatch,
):
    monkeypatch.setattr(
        active_store, "_read_connection",
        lambda: (_ for _ in ()).throw(AssertionError("cross-connection read")),
    )
    def read(transaction):
        return {"hash": transaction.load_active_exit_authority("ses_1").content_hash}
    assert active_store.execute_once(
        "exit-authority:local", "read_exit_authority", read
    )["hash"]


@pytest.mark.parametrize("version", [-1, 0, 2**63])
def test_active_exit_authority_rejects_versions_outside_sqlite_range(
    active_store, version,
):
    authority = active_store.load_active_exit_authority("ses_1")
    with pytest.raises(ValueError, match="Mission versions"):
        replace(
            authority, task_mission_version=version,
            mission_current_version=version,
        )


def test_active_exit_authority_rejects_control_byte_attempt_identity(active_store):
    authority = active_store.load_active_exit_authority("ses_1")
    snapshot = replace(authority.request.attempt, attempt_id="att_\x01")
    request = ExitRequest(
        authority.request.request_id, snapshot, snapshot.content_hash,
        authority.request.requested_at,
    )
    handle = WorkerHandle(
        authority.worker_handle.session_id, authority.worker_handle.agent_id,
        authority.worker_handle.task_id, snapshot.attempt_id,
    )
    with pytest.raises(ValueError, match="attempt identity"):
        replace(authority, request=request, worker_handle=handle)
