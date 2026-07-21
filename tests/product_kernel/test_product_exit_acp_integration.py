from __future__ import annotations

import asyncio
from functools import wraps
import json
from pathlib import Path

import pytest

import agentdeck.adapters.sqlite as sqlite_module
from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.async_exit_coordinator import AsyncExitCoordinator
from agentdeck.application.execution_runtime import (
    ActiveExecutionBinding, ExecutionReservation, ForegroundExecutionRuntime,
)
from agentdeck.application.exit_service import ExitService
from agentdeck.application.project_lifecycle_service import (
    ProjectDispatchBlocked, ProjectLifecycleService,
)
from agentdeck.ports.worker import WorkerCancellationError, WorkerHandle

from .fakes import FrozenClock
from .test_sqlite_exit_authority import NOW, REQUEST_ID, seed_active_exit


EXIT_COLUMNS = (
    "pending_exit_id", "pending_exit_attempt_id", "canonical_pending_exit_attempt_facts",
    "pending_exit_attempt_hash", "pending_exit_requested_at",
)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


class InProcessWorker:
    def __init__(self) -> None:
        self.cancel_calls = []
        self.failure = None
        self.on_cancel = None

    def cancel_error(self, code: str, *, outcome_known: bool) -> None:
        self.failure = WorkerCancellationError(code=code, outcome_known=outcome_known)

    async def start_task(self, request): raise AssertionError("unexpected start")

    def stream_events(self, handle): raise AssertionError("unexpected stream")

    async def respond_permission(self, handle, **kwargs): raise AssertionError("unexpected permission")

    async def cancel_task(self, handle, *, reason):
        self.cancel_calls.append((handle, reason))
        if self.on_cancel is not None:
            self.on_cancel()
        if self.failure is not None:
            raise self.failure

    async def collect_result(self, handle): raise AssertionError("unexpected collect")

    @property
    def cancel_count(self):
        return len(self.cancel_calls)


class ExitHarness:
    def __init__(self, root: Path) -> None:
        self.store = SQLiteStore.open(root, clock=FrozenClock(NOW))
        self.request = seed_active_exit(self.store)
        self.worker = InProcessWorker()
        self.runtime = ForegroundExecutionRuntime()
        self.handle = WorkerHandle("ses_acp_1", "agt_1", "tsk_1", "att_1")
        self.exit_service = ExitService(
            store=self.store, clock=FrozenClock(NOW), session_id="ses_1",
            request_id_factory=lambda: "xrt_" + "2" * 32,
        )
        self.lifecycle = ProjectLifecycleService(
            store=self.store, clock=FrozenClock(NOW), session_id="ses_1"
        )
        self.coordinator = AsyncExitCoordinator(
            exit_service=self.exit_service, store=self.store, clock=FrozenClock(NOW),
            runtime=self.runtime, lifecycle=self.lifecycle, session_id="ses_1",
        )

    def bind(self, *, session_id: str = "ses_acp_1") -> None:
        self.handle = WorkerHandle(session_id, "agt_1", "tsk_1", "att_1")
        self.runtime.bind(ActiveExecutionBinding(
            "att_1", "tsk_1", "agt_1", session_id, self.handle, self.worker))

    def close(self) -> None: self.store.close()

    def attempt_state(self): return self.store.load_aggregate("attempts", "att_1")["state"]

    def session_state(self): return self.store.load_aggregate("product_sessions", "ses_1")["state"]

    def pending_exit_fields(self):
        session = self.store.load_aggregate("product_sessions", "ses_1")
        return tuple(session[field] for field in EXIT_COLUMNS)

    def event_kinds(self):
        rows = self.store._require_writer().execute("SELECT kind FROM events ORDER BY rowid")
        return tuple(row[0] for row in rows.fetchall())

    def database_facts(self):
        connection = self.store._require_writer()
        return tuple(
            row for table in ("product_sessions", "attempts", "commands", "events")
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")
        )

    def clear_for_between_stages(self, *, state="running"):
        connection = self.store._require_writer()
        leader = ",leader_backend=NULL,leader_model=NULL" if state == "setup" else ""
        connection.execute(
            f"UPDATE product_sessions SET state=?,pending_exit_id=NULL,"
            "pending_exit_attempt_id=NULL,canonical_pending_exit_attempt_facts=NULL,"
            "pending_exit_attempt_hash=NULL,pending_exit_requested_at=NULL "
            f"{leader} "
            "WHERE session_id='ses_1'", (state,),
        )
        connection.execute("DELETE FROM attempts WHERE attempt_id='att_1'")


@async_test
async def test_confirm_cancels_exact_worker_once_and_atomically_pauses_project(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()
        released = asyncio.create_task(harness.runtime.wait_released("att_1", harness.handle))
        first = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert await released == ("att_1", harness.handle)
        second = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert first == second
        assert first.mode == "project_paused" and first.should_exit is True
        assert harness.worker.cancel_calls == [
            (harness.handle, "product_exit_confirmed")
        ]
        assert harness.attempt_state() == "interrupted"
        assert harness.session_state() == "paused"
        assert harness.pending_exit_fields() == (None,) * 5
        assert harness.event_kinds()[-3:] == (
            "attempt_interrupted", "project_paused", "exit_confirmed",
        )
        assert harness.runtime.status().state == "idle"
        assert harness.store.lookup_command(
            f"exit:confirm:ses_1:{harness.request.request_id}",
            "confirm_product_exit",
        ) is not None
        assert harness.store.lookup_command(
            f"exit:confirm:{harness.request.request_id}",
            "confirm_product_exit",
        ) is None
    finally:
        harness.close()


@pytest.mark.parametrize(
    ("worker_code", "known"),
    [("cancel_rejected", True), ("cancel_timeout", False),
     ("transport_disconnected", False), ("unexpected", False)],
)
@async_test
async def test_cancel_failure_closes_replay_without_false_interruption(
    tmp_path, worker_code, known,
):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()
        if worker_code == "unexpected":
            harness.worker.failure = RuntimeError("/private/raw prompt")
        else:
            harness.worker.cancel_error(worker_code, outcome_known=known)
        first = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        harness.coordinator._clock.value = NOW.replace(year=NOW.year + 1)
        second = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert first == second
        assert first.diagnostic.code == (
            "transport_disconnected" if worker_code == "unexpected" else worker_code
        )
        assert first.diagnostic.outcome_known is known
        rendered = json.dumps(first.diagnostic.__dict__, default=list, sort_keys=True)
        for forbidden in (str(harness.store.path).lower(), "ses_acp", "prompt", "traceback"):
            assert forbidden not in rendered.lower()
        assert harness.worker.cancel_count == 1
        assert harness.attempt_state() == "running"
        assert harness.session_state() == "running"
        assert harness.pending_exit_fields()[0] == harness.request.request_id
        declined = await harness.coordinator.decline(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert declined.diagnostic.code == "project_dispatch_paused"
        assert harness.pending_exit_fields()[0] == harness.request.request_id
    finally:
        harness.close()


@async_test
async def test_handle_or_acp_drift_performs_zero_cancel_and_zero_write(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind(session_id="ses_other")
        before = harness.database_facts()
        result = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert result.diagnostic.code == "exit_binding_drift"
        assert harness.worker.cancel_count == 0
        assert harness.database_facts() == before
    finally:
        harness.close()


@async_test
async def test_durable_authority_drift_before_cancel_is_zero_io_and_write(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()
        harness.store._require_writer().execute(
            "UPDATE agent_instances SET state='ready' WHERE instance_id='agt_1'"
        )
        before = harness.database_facts()
        result = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert result.diagnostic.code == "exit_binding_drift"
        assert harness.worker.cancel_count == 0
        assert harness.database_facts() == before
    finally:
        harness.close()


@pytest.mark.parametrize("quarantined", [False, True])
@async_test
async def test_reserved_or_quarantined_owner_is_never_exit_cancelled(
    tmp_path, quarantined,
):
    harness = ExitHarness(tmp_path)
    try:
        reservation = ExecutionReservation("att_1", "tsk_1", "agt_1", harness.worker)
        harness.runtime.reserve(reservation)
        if quarantined:
            harness.runtime.quarantine(reservation)
        before = harness.database_facts()
        result = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert result.diagnostic.code == "exit_binding_drift"
        assert harness.worker.cancel_count == 0
        assert harness.database_facts() == before
    finally:
        harness.close()


@async_test
async def test_post_cancel_drift_closes_diagnostic_without_second_cancel(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()
        harness.worker.on_cancel = lambda: harness.store._require_writer().execute(
            "UPDATE agent_instances SET state='ready' WHERE instance_id='agt_1'"
        )
        first = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        second = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert first == second
        assert first.diagnostic.code == "exit_authority_changed_after_cancel"
        assert harness.worker.cancel_count == 1
        assert harness.attempt_state() == "running"
        assert harness.session_state() == "running"
        assert harness.pending_exit_fields()[0] == harness.request.request_id
    finally:
        harness.close()


@async_test
async def test_event_write_failure_rolls_back_entire_pause_commit(
    tmp_path, monkeypatch,
):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()
        before = harness.database_facts()
        original = sqlite_module._SQLiteCommandTransaction.append_event
        calls = 0

        def fail_third(transaction, event):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("synthetic event failure")
            return original(transaction, event)

        monkeypatch.setattr(
            sqlite_module._SQLiteCommandTransaction, "append_event", fail_third
        )
        first = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert first.diagnostic.code == "exit_persistence_pending"
        assert first.diagnostic.outcome_known is False
        assert harness.worker.cancel_count == 1
        assert harness.database_facts() == before
        assert harness.attempt_state() == "running"
        assert harness.session_state() == "running"
        monkeypatch.setattr(
            sqlite_module._SQLiteCommandTransaction, "append_event", original
        )
        harness.store._require_writer().execute(
            "UPDATE agent_instances SET state='ready' WHERE instance_id='agt_1'"
        )
        second = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert second.diagnostic.code == "exit_authority_changed_after_cancel"
        assert harness.worker.cancel_count == 1
    finally:
        harness.close()


@async_test
async def test_malformed_closed_replay_never_retries_worker_io(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()
        harness.worker.cancel_error("cancel_timeout", outcome_known=False)
        await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        harness.store._require_writer().execute(
            "UPDATE commands SET canonical_result_facts='{}' WHERE command_id=?",
            (f"exit:confirm:ses_1:{harness.request.request_id}",),
        )
        with pytest.raises(ValueError, match="stored exit result"):
            await harness.coordinator.confirm(
                harness.request.request_id, harness.request.attempt_hash
            )
        assert harness.worker.cancel_count == 1
    finally:
        harness.close()


@async_test
async def test_between_stage_exit_pauses_with_zero_worker_io(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        harness.clear_for_between_stages()
        result = await harness.coordinator.request_exit()
        assert result.mode == "project_paused" and result.request is None
        assert result.should_exit is True
        assert harness.session_state() == "paused"
        assert harness.pending_exit_fields() == (None,) * 5
        assert harness.worker.cancel_count == 0
        assert harness.event_kinds()[-2:] == ("project_paused", "exit_confirmed")
    finally:
        harness.close()


@async_test
async def test_pending_exit_blocks_dispatch_until_exact_decline(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        with pytest.raises(ProjectDispatchBlocked):
            harness.lifecycle.require_dispatchable()
        declined = await harness.coordinator.decline(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert declined.mode == "exit_declined"
        harness.lifecycle.require_dispatchable()
        assert harness.worker.cancel_count == 0
        assert harness.attempt_state() == "running"
    finally:
        harness.close()


@pytest.mark.parametrize("state", ["setup", "ready", "drafting", "awaiting_confirmation"])
@async_test
async def test_nonexecuting_exit_closes_without_synthetic_pause(tmp_path, state):
    harness = ExitHarness(tmp_path)
    try:
        harness.clear_for_between_stages(state=state)
        result = await harness.coordinator.request_exit()
        assert result.mode == "exit_ready" and result.should_exit is True
        assert harness.session_state() == state
        assert "project_paused" not in harness.event_kinds()
        assert harness.worker.cancel_count == 0
    finally:
        harness.close()


def test_event_loop_drift_is_zero_cancel_and_zero_write(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        asyncio.run(_bind(harness))
        before = harness.database_facts()
        result = asyncio.run(harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        ))
        assert result.diagnostic.code == "exit_binding_drift"
        assert harness.worker.cancel_count == 0
        assert harness.database_facts() == before
    finally:
        harness.close()


def test_invalid_coordinator_session_identity_fails_at_composition(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        before = harness.database_facts()
        with pytest.raises(ValueError, match="session_id"):
            AsyncExitCoordinator(
                exit_service=harness.exit_service, store=harness.store,
                clock=FrozenClock(NOW), runtime=harness.runtime,
                lifecycle=harness.lifecycle, session_id="wrong",
            )
        assert harness.worker.cancel_count == 0
        assert harness.database_facts() == before
    finally:
        harness.close()


@async_test
async def test_cross_session_coordinator_closes_before_io_or_write(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()
        coordinator = AsyncExitCoordinator(
            exit_service=harness.exit_service, store=harness.store, clock=FrozenClock(NOW),
            runtime=harness.runtime, lifecycle=harness.lifecycle, session_id="ses_other",
        )
        source_request = harness.store.lookup_command(
            f"exit:request:ses_1:{harness.request.request_id}", "request_product_exit",
        )
        harness.store.execute_once(
            f"exit:request:ses_other:{harness.request.request_id}",
            "request_product_exit", lambda transaction: source_request,
        )
        closed = {
            "attempt_hash": harness.request.attempt_hash, "attempt_id": "att_1",
            "diagnostic_code": None, "mode": "project_paused", "outcome_known": True,
            "request_id": harness.request.request_id, "should_exit": True,
        }
        harness.store.execute_once(
            f"exit:confirm:ses_other:{harness.request.request_id}",
            "confirm_product_exit", lambda transaction: closed,
        )
        before = harness.database_facts()
        result = await coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert result.diagnostic.code == "exit_request_identity_mismatch"
        assert harness.worker.cancel_count == 0
        assert harness.database_facts() == before
    finally:
        harness.close()


@async_test
async def test_completed_replay_rejects_empty_attempt_identity(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()
        command_id = f"exit:confirm:ses_1:{harness.request.request_id}"
        forged = {
            "attempt_hash": None, "attempt_id": None, "diagnostic_code": None,
            "mode": "project_paused", "outcome_known": True,
            "request_id": None, "should_exit": True,
        }
        harness.store.execute_once(
            command_id, "confirm_product_exit", lambda transaction: forged,
        )
        with pytest.raises(ValueError, match="stored exit result"):
            await harness.coordinator.confirm(
                harness.request.request_id, harness.request.attempt_hash
            )
        assert harness.worker.cancel_count == 0
    finally:
        harness.close()


async def _bind(harness):
    harness.bind()
