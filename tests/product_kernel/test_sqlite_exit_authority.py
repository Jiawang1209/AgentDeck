from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.adapters.sqlite_schema import (
    StoreCommandStateError,
    StoreSerializationError,
)
from agentdeck.kernel.execution import AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot, ExitRequest

from .fakes import FrozenClock


NOW = datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc)
EXIT_COLUMNS = (
    "pending_exit_id",
    "pending_exit_attempt_id",
    "canonical_pending_exit_attempt_facts",
    "pending_exit_attempt_hash",
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
        (
            "ses_1", store._project_id, "running", "approve_for_me", None,
            now, now, "codex-cli", "native-default",
        ),
    )


def _seed_lineage(store: SQLiteStore, states: tuple[str, ...]) -> None:
    _seed_session(store)
    connection = store._require_writer()
    now = NOW.isoformat()
    connection.execute(
        """INSERT INTO missions (
               mission_id,session_id,state,current_version,created_at,updated_at)
           VALUES (?,?,?,?,?,?)""",
        ("msn_1", "ses_1", "running", 1, now, now),
    )
    connection.execute(
        """INSERT INTO mission_versions (
               mission_id,version,preview_id,content_hash,
               canonical_mission_facts,confirmed_at)
           VALUES (?,?,?,?,?,?)""",
        ("msn_1", 1, "prv_1", "a" * 64, "{}", now),
    )
    for ordinal, state in enumerate(states, 1):
        suffix = str(ordinal)
        agent_id = f"agt_{suffix}"
        task_id = f"tsk_{suffix}"
        attempt_id = f"att_{suffix}"
        connection.execute(
            """INSERT INTO agent_instances (
                   instance_id,session_id,backend_id,transport,backend_version,
                   role,acp_session_id,state,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                agent_id, "ses_1", "codex-cli", "acp", "1", "implementer",
                f"acp_{suffix}", "active", now, now,
            ),
        )
        connection.execute(
            """INSERT INTO tasks (
                   task_id,mission_id,mission_version,ordinal,name,role,
                   planned_backend,planned_agent_instance_id,acp_route,state,
                   canonical_task_facts,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id, "msn_1", 1, ordinal, task_id, "implementer",
                "codex-cli", agent_id, "acp://route", "running", "{}", now, now,
            ),
        )
        connection.execute(
            """INSERT INTO attempts (
                   attempt_id,task_id,agent_instance_id,ordinal,state,reason,
                   result_summary,retryable,acp_session_id,effect_observed,
                   created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                attempt_id, task_id, agent_id, 1, state, None, None, 0,
                f"acp_{suffix}", 0, now, now,
            ),
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
        "att_1", "tsk_1", "agt_1", 1, AttemptState.RUNNING, "acp_1", False,
        "a" * 64,
    )
    return ExitRequest(
        "xrt_" + "1" * 32, snapshot, snapshot.content_hash, NOW.isoformat()
    )


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


def test_store_lists_all_and_only_active_exit_attempts(store: SQLiteStore) -> None:
    _seed_lineage(store, (
        "completed", "human_controlled", "running", "awaiting_approval", "failed",
    ))

    snapshots = store.list_active_exit_attempts()

    assert [item.attempt_id for item in snapshots] == ["att_2", "att_3", "att_4"]
    assert [item.state for item in snapshots] == [
        AttemptState.HUMAN_CONTROLLED,
        AttemptState.RUNNING,
        AttemptState.AWAITING_APPROVAL,
    ]
    assert all(item.durable_fingerprint is not None for item in snapshots)
    assert snapshots == store.list_active_exit_attempts()


def test_transaction_lists_active_attempts_from_its_live_authority(
    store: SQLiteStore,
) -> None:
    _seed_lineage(store, ("running",))

    def inspect(transaction: object) -> dict[str, object]:
        attempts = transaction.list_active_exit_attempts()  # type: ignore[attr-defined]
        assert attempts[0].attempt_id == "att_1"
        return {"count": len(attempts)}

    assert store.execute_once("inspect:attempt", "inspect_attempt", inspect) == {
        "count": 1
    }


def test_active_attempt_listing_strictly_validates_the_durable_row(
    store: SQLiteStore,
) -> None:
    _seed_lineage(store, ("running",))
    connection = store._require_writer()
    connection.execute("PRAGMA ignore_check_constraints=ON")
    connection.execute("UPDATE attempts SET effect_observed=2 WHERE attempt_id='att_1'")

    with pytest.raises(StoreCommandStateError, match="stored attempt"):
        store.list_active_exit_attempts()


def test_pending_exit_write_round_trips_exact_canonical_authority(
    store: SQLiteStore,
) -> None:
    _seed_session(store)
    request = _request()

    _save_session(store, _pending_snapshot(request))

    row = store.load_aggregate("product_sessions", "ses_1")
    assert row is not None
    assert tuple(row[column] for column in EXIT_COLUMNS) == tuple(
        _pending_snapshot(request)[column] for column in EXIT_COLUMNS
    )


@pytest.mark.parametrize("missing", EXIT_COLUMNS)
def test_partial_pending_exit_group_is_rejected_without_writes(
    store: SQLiteStore, missing: str,
) -> None:
    _seed_session(store)
    changes = _pending_snapshot(_request())
    del changes[missing]
    before = store.load_aggregate("product_sessions", "ses_1")

    with pytest.raises(StoreSerializationError, match="closed group"):
        _save_session(store, changes)

    assert store.load_aggregate("product_sessions", "ses_1") == before
    assert store.count("commands") == 0


def test_omitted_pending_group_preserves_and_explicit_all_none_clears(
    store: SQLiteStore,
) -> None:
    _seed_session(store)
    _save_session(store, _pending_snapshot(_request()))

    def save(transaction: object) -> dict[str, object]:
        transaction.save_session({  # type: ignore[attr-defined]
            "session_id": "ses_1", "state": "paused",
        })
        transaction.save_session({  # type: ignore[attr-defined]
            "session_id": "ses_1", "state": "running",
            **dict.fromkeys(EXIT_COLUMNS),
        })
        return {"cleared": True}

    store.execute_once("session:clear", "clear_session_exit", save)

    row = store.load_aggregate("product_sessions", "ses_1")
    assert row is not None
    assert tuple(row[column] for column in EXIT_COLUMNS) == (None,) * 5


@pytest.mark.parametrize(
    "change",
    (
        {"pending_exit_id": "xrt_" + "A" * 32},
        {"pending_exit_attempt_id": "att_other"},
        {"canonical_pending_exit_attempt_facts": "{}"},
        {"pending_exit_attempt_hash": "f" * 64},
    ),
)
def test_pending_exit_rejects_identity_or_canonical_drift_without_writes(
    store: SQLiteStore, change: dict[str, object],
) -> None:
    _seed_session(store)
    pending = _pending_snapshot(_request()) | change

    with pytest.raises(StoreSerializationError):
        _save_session(store, pending)

    assert store.count("commands") == 0
    assert pending_exit_fields(store) == (None,) * 5


def test_load_rejects_a_durably_malformed_pending_exit_snapshot(
    store: SQLiteStore,
) -> None:
    _seed_session(store)
    pending = _pending_snapshot(_request())
    store._require_writer().execute(
        """UPDATE product_sessions SET pending_exit_id=?,
              pending_exit_attempt_id=?,canonical_pending_exit_attempt_facts=?,
              pending_exit_attempt_hash=?,pending_exit_requested_at=?
           WHERE session_id='ses_1'""",
        tuple(pending[column] for column in EXIT_COLUMNS),
    )
    store._require_writer().execute(
        "UPDATE product_sessions SET canonical_pending_exit_attempt_facts='{}' "
        "WHERE session_id='ses_1'"
    )

    with pytest.raises(StoreCommandStateError, match="stored session"):
        store.load_aggregate("product_sessions", "ses_1")


def pending_exit_fields(
    store: SQLiteStore, session_id: str = "ses_1",
) -> tuple[object, ...]:
    row = store.load_aggregate("product_sessions", session_id)
    assert row is not None
    return tuple(row[name] for name in EXIT_COLUMNS)
