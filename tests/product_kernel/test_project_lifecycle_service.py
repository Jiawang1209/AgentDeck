from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from functools import wraps
import json

import pytest

from agentdeck.application.execution_resume import ExecutionResumePlanner
from agentdeck.application.project_lifecycle_service import (
    ProjectDispatchBlocked,
    ProjectLifecycleService,
)
from agentdeck.kernel.execution import Attempt, AttemptState
from agentdeck.kernel.session import ExitAttemptSnapshot
from agentdeck.kernel.permissions import PermissionProfile, PermissionScope
from product_kernel.fakes import FrozenClock
from product_kernel.test_execution_budgets import SafeWorkerFailure
from product_kernel.test_execution_coordinator import Harness, ScriptedWorker
from product_kernel.test_sqlite_execution_resume import (
    NOW,
    _table_snapshot,
    seed_closed_stage,
    seed_interrupted_attempt,
    seed_outcome_unknown_attempt,
    store,
    _insert_attempt,
)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def lifecycle(store) -> ProjectLifecycleService:
    return ProjectLifecycleService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1"
    )


def test_complete_mission_transitions_running_to_completed(store):
    service = lifecycle(store)
    store._require_writer().execute(
        "UPDATE product_sessions SET state='running' WHERE session_id='ses_1'"
    )

    result = service.complete_mission()

    assert result.mode == "project_completed"
    assert result.should_start is False
    assert store.load_aggregate("product_sessions", "ses_1")["state"] == "completed"
    assert store._require_writer().execute(
        "SELECT count(*) FROM events WHERE kind='project_completed'"
    ).fetchone() == (1,)
    # Completion is a terminal transition: a session that is already
    # completed is no longer running, so a second call fails closed rather
    # than silently repeating (or no-op-ing) the transition.
    with pytest.raises(ProjectDispatchBlocked):
        service.complete_mission()


def test_complete_mission_rejects_a_session_that_is_not_running(store):
    service = lifecycle(store)

    with pytest.raises(ProjectDispatchBlocked):
        service.complete_mission()

    assert store.load_aggregate("product_sessions", "ses_1")["state"] == "paused"


@async_test
async def test_dispatch_lease_requires_running_and_null_pending_exit(store):
    service = lifecycle(store)
    store._require_writer().execute(
        "UPDATE product_sessions SET state='running' WHERE session_id='ses_1'"
    )

    async with service.dispatch_lease():
        service.require_dispatchable()

    store._require_writer().execute(
        """UPDATE product_sessions SET
           pending_exit_id='xrt_0123456789abcdef0123456789abcdef',
           pending_exit_attempt_id='att_implementation_1',
           canonical_pending_exit_attempt_facts='{}',
           pending_exit_attempt_hash=?,pending_exit_requested_at=?""",
        ("a" * 64, NOW.isoformat()),
    )
    async with service.dispatch_lease():
        with pytest.raises(ProjectDispatchBlocked):
            service.require_dispatchable()


@async_test
async def test_stop_lease_and_dispatch_lease_are_mutually_exclusive(store):
    service = lifecycle(store)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def dispatch() -> None:
        async with service.dispatch_lease():
            entered.set()
            await release.wait()

    task = asyncio.create_task(dispatch())
    await entered.wait()
    stop_acquired = asyncio.Event()

    async def acquire_stop() -> None:
        async with service.stop_lease():
            stop_acquired.set()

    stop = asyncio.create_task(acquire_stop())
    await asyncio.sleep(0)
    assert stop.done() is False
    assert stop_acquired.is_set() is False
    release.set()
    await task
    await stop
    assert stop_acquired.is_set() is True


@async_test
async def test_resume_revalidates_snapshot_and_closes_one_exact_command(store):
    snapshot = store.load_execution_resume("ses_1")
    service = lifecycle(store)

    first = await service.resume(snapshot)
    second = await service.resume(snapshot)

    assert first == second
    assert first.mode == "project_resumed"
    assert first.should_start is True
    assert first.snapshot_hash == snapshot.content_hash
    assert store.load_aggregate("product_sessions", "ses_1")["state"] == "running"
    assert store._require_writer().execute(
        "SELECT kind FROM events WHERE kind='project_resumed'"
    ).fetchall() == [("project_resumed",)]
    command = store.lookup_command(
        f"project:resume:ses_1:{snapshot.content_hash[:16]}", "resume_project"
    )
    assert command == {
        "mode": "project_resumed",
        "session_id": "ses_1",
        "should_start": True,
        "snapshot_hash": snapshot.content_hash,
    }
    assert store.count("attempts") == 0


@async_test
async def test_resume_uses_transaction_projection_not_a_second_store_read(store):
    snapshot = store.load_execution_resume("ses_1")
    store.load_execution_resume = lambda _session_id: (_ for _ in ()).throw(
        AssertionError("resume opened a second Store read"))

    result = await lifecycle(store).resume(snapshot)

    assert result.mode == "project_resumed"


@async_test
@pytest.mark.parametrize(
    "drift", ["session", "pending_exit", "task", "outcome_unknown", "complete"]
)
async def test_resume_drift_rolls_back_with_zero_writes(store, drift):
    snapshot = store.load_execution_resume("ses_1")
    connection = store._require_writer()
    if drift == "session":
        connection.execute("UPDATE product_sessions SET state='running'")
    elif drift == "pending_exit":
        connection.execute(
            """UPDATE product_sessions SET
               pending_exit_id='xrt_0123456789abcdef0123456789abcdef',
               pending_exit_attempt_id='att_implementation_1',
               canonical_pending_exit_attempt_facts='{}',
               pending_exit_attempt_hash=?,pending_exit_requested_at=?""",
            ("a" * 64, NOW.isoformat()),
        )
    elif drift == "task":
        connection.execute(
            "UPDATE tasks SET canonical_task_facts='{}' WHERE ordinal=1"
        )
    elif drift == "outcome_unknown":
        seed_outcome_unknown_attempt(store, "implementation", 1)
    else:
        for name in ("implementation", "review", "revision", "acceptance"):
            seed_closed_stage(store, name)
    before = _table_snapshot(store)

    with pytest.raises(Exception):
        await lifecycle(store).resume(snapshot)

    assert _table_snapshot(store) == before


@async_test
async def test_resume_rejects_snapshot_hash_drift_before_any_write(store):
    snapshot = copy.copy(store.load_execution_resume("ses_1"))
    object.__setattr__(snapshot, "content_hash", "0" * 64)
    before = _table_snapshot(store)

    with pytest.raises(Exception):
        await lifecycle(store).resume(snapshot)

    assert _table_snapshot(store) == before


def execution_for_plan(plan):
    harness = Harness(objective=plan.draft.objective)
    harness.draft = plan.draft
    harness.confirmed = plan.confirmed
    harness.results["review"]["findings"][0]["scope"] = plan.draft.scope
    harness.results["review"]["findings"][0]["criterion"] = (
        plan.draft.acceptance_criteria[0]
    )
    harness.results["review"]["findings"][0]["evidence_ids"] = [
        "ev_implementation_1"
    ]
    return harness


async def run_plan(harness, plan):
    return await harness.service.run_confirmed_mission(
        session_id="ses_1",
        confirmed=plan.confirmed,
        draft=plan.draft,
        permission_scope=PermissionScope.for_profile(
            PermissionProfile.APPROVE_FOR_ME
        ),
        resume_plan=plan,
    )


@async_test
async def test_resume_skips_closed_stages_and_retries_interrupted_stage(store):
    seed_closed_stage(store, "implementation")
    seed_interrupted_attempt(store, "review", 1)
    seed_interrupted_attempt(store, "review", 2)
    plan = ExecutionResumePlanner().materialize(
        store.load_execution_resume("ses_1")
    )
    harness = execution_for_plan(plan)

    result = await run_plan(harness, plan)

    assert [
        (json.loads(request.instruction)["task"]["name"],
         json.loads(request.instruction)["attempt"]["ordinal"])
        for request in harness.requests
    ] == [("review", 3), ("revision", 1), ("acceptance", 1)]
    assert result.attempts[0].attempt_id == "att_implementation_1"
    assert result.handoffs[0].handoff_id == "hnd_implementation_1"
    assert result.diagnostic is None


@async_test
async def test_interrupted_ordinal_does_not_consume_failure_retry(store):
    seed_closed_stage(store, "implementation")
    seed_interrupted_attempt(store, "review", 1)
    seed_interrupted_attempt(store, "review", 2)
    plan = ExecutionResumePlanner().materialize(
        store.load_execution_resume("ses_1")
    )
    harness = execution_for_plan(plan)
    starts = 0

    class FailFirstReview(ScriptedWorker):
        async def start_task(self, request):
            nonlocal starts
            if self._task_name == "review" and starts == 0:
                starts += 1
                self._harness.started_tasks.append(self._task_name)
                raise SafeWorkerFailure(
                    "worker_schema_invalid", outcome_known=True, retryable=True,
                    task_id=request.task_id, attempt_id=request.attempt_id,
                )
            return await super().start_task(request)

    harness.service._worker_factory = lambda task: FailFirstReview(
        harness, task.name
    )

    result = await run_plan(harness, plan)

    review_attempts = [
        attempt for attempt in result.attempts
        if attempt.task_id == plan.draft.tasks[1].task_id
    ]
    assert [(item.ordinal, item.state) for item in review_attempts] == [
        (1, AttemptState.INTERRUPTED),
        (2, AttemptState.INTERRUPTED),
        (3, AttemptState.FAILED),
        (4, AttemptState.COMPLETED),
    ]
    assert result.diagnostic is None


@async_test
async def test_closed_review_rebuilds_revision_authority_without_side_effect_replay(store):
    seed_closed_stage(store, "implementation")
    seed_closed_stage(store, "review")
    plan = ExecutionResumePlanner().materialize(
        store.load_execution_resume("ses_1")
    )
    harness = execution_for_plan(plan)

    result = await run_plan(harness, plan)

    assert harness.started_tasks == ["revision", "acceptance"]
    revision = json.loads(harness.requests[0].instruction)[
        "authoritative_revision_task"
    ]
    assert revision["accepted_finding_ids"] == ["rfn_resume"]
    assert result.revision_task.review_evidence_ids == ("ev_review_1",)


@async_test
async def test_persisted_exit_request_blocks_next_worker_dispatch():
    harness = Harness()
    original = harness.service._persist_terminal

    def persist_then_stop(*args, **kwargs):
        result = original(*args, **kwargs)
        session = harness.store.aggregates[("product_sessions", "ses_1")]
        session["pending_exit_id"] = "xrt_" + "1" * 32
        return result

    harness.service._persist_terminal = persist_then_stop

    result = await harness.run()

    assert harness.started_tasks == ["implementation"]
    assert result.diagnostic.code == "project_dispatch_paused"


@async_test
async def test_second_dispatch_check_blocks_before_worker_factory():
    harness = Harness()

    class BlockOnSecondCheck(ProjectLifecycleService):
        calls = 0

        def require_dispatchable(self):
            self.calls += 1
            if self.calls == 2:
                harness.store.aggregates[("product_sessions", "ses_1")][
                    "pending_exit_id"
                ] = "xrt_" + "2" * 32
            return super().require_dispatchable()

    gate = BlockOnSecondCheck(
        store=harness.store, clock=FrozenClock(NOW), session_id="ses_1"
    )
    harness.lifecycle = harness.service._lifecycle = gate
    factory_calls = 0
    original = harness.service._worker_factory

    def factory(task):
        nonlocal factory_calls
        factory_calls += 1
        return original(task)

    harness.service._worker_factory = factory

    result = await harness.run()

    assert factory_calls == 0
    assert result.diagnostic.code == "project_dispatch_paused"
    assert result.diagnostic.outcome_known is True
    attempt = next(value for (kind, _), value in harness.store.aggregates.items()
                   if kind == "attempts")
    assert attempt["state"] == "interrupted"
    assert result.attempts[-1].state is AttemptState.INTERRUPTED
    assert result.diagnostic.attempt_id == result.attempts[-1].attempt_id


@async_test
async def test_pause_between_stages_is_atomic_and_active_attempt_blocks(store):
    service = lifecycle(store)
    connection = store._require_writer()
    connection.execute("UPDATE product_sessions SET state='running'")

    async with service.stop_lease():
        paused = service.pause_between_stages()

    assert paused.mode == "project_paused" and paused.should_start is False
    assert store.load_aggregate("product_sessions", "ses_1")["state"] == "paused"
    assert connection.execute(
        "SELECT count(*) FROM events WHERE kind='project_paused'"
    ).fetchone() == (1,)

    connection.execute("UPDATE product_sessions SET state='running'")
    task = store._resume_draft.tasks[0]
    _insert_attempt(store, Attempt.pending("att_active", task.task_id, 1).start())
    before = _table_snapshot(store)
    with pytest.raises(ProjectDispatchBlocked):
        async with service.stop_lease():
            service.pause_between_stages()
    assert _table_snapshot(store) == before


@async_test
async def test_dispatch_lease_keeps_stop_out_until_worker_binding_is_visible():
    harness = Harness()
    entered = asyncio.Event()
    release = asyncio.Event()
    binding_visible = asyncio.Event()
    original_factory = harness.service._worker_factory

    class CheckingLifecycle(ProjectLifecycleService):
        @asynccontextmanager
        async def dispatch_lease(self):
            async with super().dispatch_lease():
                yield
                if harness.requests:
                    request = harness.requests[-1]
                    ordinal = json.loads(request.instruction)["attempt"]["ordinal"]
                    snapshot = ExitAttemptSnapshot(
                        request.attempt_id, request.task_id, request.agent_id,
                        ordinal, AttemptState.RUNNING, "ses_1", False, None,
                    )
                    assert harness.runtime.resolve_exact(snapshot).attempt_id == (
                        request.attempt_id
                    )
                    binding_visible.set()

    harness.lifecycle = harness.service._lifecycle = CheckingLifecycle(
        store=harness.store, clock=FrozenClock(NOW), session_id="ses_1"
    )

    class SlowWorker:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        async def start_task(self, request):
            entered.set()
            await release.wait()
            return await self.wrapped.start_task(request)

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    harness.service._worker_factory = lambda task: SlowWorker(original_factory(task))
    running = asyncio.create_task(harness.run())
    await entered.wait()
    acquired = asyncio.Event()

    async def stop():
        async with harness.lifecycle.stop_lease():
            acquired.set()

    stopping = asyncio.create_task(stop())
    await asyncio.sleep(0)
    assert acquired.is_set() is False
    release.set()
    await stopping
    assert acquired.is_set() is True
    assert binding_visible.is_set() is True
    await running


@async_test
async def test_reused_worker_is_rejected_before_second_start():
    harness = Harness()
    singleton = ScriptedWorker(harness, "implementation")
    harness.service._worker_factory = lambda task: singleton

    result = await harness.run()

    assert result.diagnostic.code == "execution_binding_rejected"
    assert result.diagnostic.cause == (
        "the Worker identity was not available for a new Attempt"
    )
    assert [attempt.state for attempt in result.attempts] == [
        AttemptState.COMPLETED, AttemptState.FAILED,
    ]
    assert harness.started_tasks == ["implementation"]
    persisted = harness.store.load_aggregate(
        "attempts", result.attempts[-1].attempt_id
    )
    assert persisted["state"] == "failed"
    assert persisted["reason"] == "execution_binding_rejected"


@async_test
async def test_reused_worker_terminal_write_failure_is_content_free():
    harness = Harness()
    singleton = ScriptedWorker(harness, "implementation")
    harness.service._worker_factory = lambda task: singleton
    original = harness.service._persist_terminal_attempt

    def fail_runtime_terminal(attempt, *args, **kwargs):
        if attempt.reason == "execution_binding_rejected":
            raise RuntimeError("hostile raw persistence marker")
        return original(attempt, *args, **kwargs)

    harness.service._persist_terminal_attempt = fail_runtime_terminal

    result = await harness.run()

    assert result.diagnostic.code == "terminal_attempt_persistence_failed"
    assert "hostile raw persistence marker" not in result.diagnostic.cause
    assert harness.started_tasks == ["implementation"]
