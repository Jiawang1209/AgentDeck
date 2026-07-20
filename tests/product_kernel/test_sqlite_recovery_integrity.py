from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.recovery_service import RecoveryError, RecoveryService
from agentdeck.kernel.execution import Attempt
from agentdeck.ports.execution_resume import ExecutionResumeProjectionError

from .fakes import FrozenClock
from .test_sqlite_execution_resume import (
    _insert_attempt,
    _seed_base,
    _task,
    seed_closed_stage,
)


NOW = datetime(2026, 7, 20, 4, 5, 6, tzinfo=timezone.utc)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def _open(root: Path) -> SQLiteStore:
    store = SQLiteStore.open(root, clock=FrozenClock(NOW))
    _seed_base(store)
    return store


def _running_attempt(store: SQLiteStore, *, effect_observed: bool = False) -> None:
    task = _task(store, "implementation")
    _insert_attempt(
        store,
        Attempt.pending("att_recovery_1", task.task_id, 1).start(),
    )
    store._require_writer().execute(
        "UPDATE attempts SET effect_observed=? WHERE attempt_id='att_recovery_1'",
        (int(effect_observed),),
    )
    store._require_writer().execute(
        "UPDATE product_sessions SET state='running' WHERE session_id='ses_1'"
    )
    store._require_writer().commit()


def _recovery(store: SQLiteStore, run_id: str = "restart_1") -> RecoveryService:
    return RecoveryService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        recovery_run_id=run_id,
    )


@async_test
@pytest.mark.parametrize(
    ("effect_observed", "expected_state"),
    [(False, "interrupted"), (True, "outcome_unknown")],
)
async def test_sqlite_fresh_process_classifies_and_pauses_exact_attempt(
    tmp_path: Path, effect_observed: bool, expected_state: str,
) -> None:
    store = _open(tmp_path)
    _running_attempt(store, effect_observed=effect_observed)

    report = await _recovery(store).reconcile()

    assert getattr(report, expected_state) == ("att_recovery_1",)
    assert store.connection.execute(
        "SELECT state FROM attempts WHERE attempt_id='att_recovery_1'"
    ).fetchone() == (expected_state,)
    assert store.load_aggregate("product_sessions", "ses_1")["state"] == "paused"
    assert store.connection.execute(
        "SELECT kind FROM events WHERE kind IN ('attempt_recovered','project_paused') "
        "ORDER BY kind"
    ).fetchall() == [("attempt_recovered",), ("project_paused",)]
    store.close()


@async_test
async def test_resume_commit_before_worker_crash_reconverges_to_paused(
    tmp_path: Path,
) -> None:
    store = _open(tmp_path)
    snapshot = store.load_execution_resume("ses_1")
    from agentdeck.application.project_lifecycle_service import ProjectLifecycleService

    lifecycle = ProjectLifecycleService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1"
    )
    assert (await lifecycle.resume(snapshot)).mode == "project_resumed"
    assert store.count("attempts") == 0

    await _recovery(store, "restart_after_resume").reconcile()

    assert store.load_aggregate("product_sessions", "ses_1")["state"] == "paused"
    assert store.count("attempts") == 0
    store.close()


@async_test
async def test_two_sqlite_active_attempts_fail_before_command_or_event(
    tmp_path: Path,
) -> None:
    store = _open(tmp_path)
    _running_attempt(store)
    task = _task(store, "implementation")
    _insert_attempt(store, Attempt.pending("att_recovery_2", task.task_id, 2).start())
    store._require_writer().commit()
    before = store.connection.execute(
        "SELECT state FROM product_sessions WHERE session_id='ses_1'"
    ).fetchone()

    with pytest.raises(RecoveryError, match="ambiguous"):
        await _recovery(store).reconcile()

    assert store.connection.execute(
        "SELECT count(*) FROM commands WHERE command_kind='recover_project'"
    ).fetchone() == (0,)
    assert store.connection.execute(
        "SELECT count(*) FROM events WHERE kind='attempt_recovered'"
    ).fetchone() == (0,)
    assert store.connection.execute(
        "SELECT state FROM product_sessions WHERE session_id='ses_1'"
    ).fetchone() == before
    store.close()


@async_test
async def test_sqlite_fingerprint_drift_between_read_and_transaction_is_zero_write(
    tmp_path: Path,
) -> None:
    store = _open(tmp_path)
    _running_attempt(store)
    execute_once = store.execute_once

    def drift_then_execute(command_id, command_kind, callback):
        store._require_writer().execute(
            "UPDATE attempts SET ordinal=2 WHERE attempt_id='att_recovery_1'"
        )
        store._require_writer().commit()
        return execute_once(command_id, command_kind, callback)

    store.execute_once = drift_then_execute  # type: ignore[method-assign]
    with pytest.raises(RecoveryError, match="changed"):
        await _recovery(store).reconcile()
    assert store.connection.execute(
        "SELECT state,ordinal FROM attempts WHERE attempt_id='att_recovery_1'"
    ).fetchone() == ("running", 2)
    assert store.connection.execute(
        "SELECT count(*) FROM commands WHERE command_kind='recover_project'"
    ).fetchone() == (0,)
    store.close()


@async_test
async def test_sqlite_event_failure_rolls_back_attempt_session_and_command(
    tmp_path: Path,
) -> None:
    store = _open(tmp_path)
    _running_attempt(store)
    execute_once = store.execute_once

    def fail_event(command_id, command_kind, callback):
        class TransactionProxy:
            def __init__(self, target):
                self._target = target

            def __getattr__(self, name):
                return getattr(self._target, name)

            def append_event(self, event):
                if event.kind == "attempt_recovered":
                    raise RuntimeError("event write failed")
                self._target.append_event(event)

        return execute_once(
            command_id, command_kind,
            lambda transaction: callback(TransactionProxy(transaction)),
        )

    store.execute_once = fail_event  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        await _recovery(store).reconcile()

    assert store.connection.execute(
        "SELECT state FROM attempts WHERE attempt_id='att_recovery_1'"
    ).fetchone() == ("running",)
    assert store.load_aggregate("product_sessions", "ses_1")["state"] == "running"
    assert store.connection.execute(
        "SELECT count(*) FROM commands WHERE command_kind='recover_project'"
    ).fetchone() == (0,)
    store.close()


@async_test
async def test_terminal_handoff_survives_recovery_and_advances_snapshot(
    tmp_path: Path,
) -> None:
    store = _open(tmp_path)
    seed_closed_stage(store, "implementation")
    store._require_writer().execute(
        "UPDATE product_sessions SET state='running' WHERE session_id='ses_1'"
    )
    store._require_writer().commit()
    before = tuple(store.connection.execute(
        "SELECT * FROM handoffs UNION ALL SELECT * FROM evidence"
    ).fetchall())

    await _recovery(store).reconcile()
    snapshot = store.load_execution_resume("ses_1")

    after = tuple(store.connection.execute(
        "SELECT * FROM handoffs UNION ALL SELECT * FROM evidence"
    ).fetchall())
    assert after == before
    assert snapshot.first_unclosed_task_id == _task(store, "review").task_id
    assert snapshot.preceding_handoff_id is not None
    assert snapshot.preceding_handoff_id.startswith("hnd_")
    store.close()


@async_test
async def test_possible_effects_make_resume_projection_fail_closed(
    tmp_path: Path,
) -> None:
    store = _open(tmp_path)
    _running_attempt(store, effect_observed=True)
    await _recovery(store).reconcile()

    with pytest.raises(ExecutionResumeProjectionError, match="resume_outcome_unknown"):
        store.load_execution_resume("ses_1")
    store.close()
