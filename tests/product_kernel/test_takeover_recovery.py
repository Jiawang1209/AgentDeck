from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.execution_authority import attempt_snapshot
from agentdeck.application.execution_runtime import (
    ActiveExecutionBinding, ForegroundExecutionRuntime,
)
from agentdeck.application.takeover_control import TakeoverControl
from agentdeck.kernel.execution import Attempt
from agentdeck.kernel.permissions import Effect, PermissionProfile, PermissionScope
from agentdeck.ports.observer import ObserverCursor
from agentdeck.ports.worker import WorkerHandle
from product_kernel.fakes import FrozenClock
from product_kernel.test_takeover import Harness, NOW, project_evidence


def _run(awaitable):
    return asyncio.run(awaitable)


def _rebuilt_control(harness: Harness, attempt_id: str) -> TakeoverControl:
    control = TakeoverControl(
        store=harness.store, clock=FrozenClock(NOW), runtime=harness.runtime,
        project_evidence=lambda: harness.evidence.project,
        permission_snapshot=lambda: harness.evidence.permission,
        observer_cursor=lambda: harness.evidence.cursor,
    )
    task = harness.draft.tasks[0]
    control.arm(
        product_session_id="ses_product", confirmed=harness.confirmed, task=task,
        attempt=Attempt.pending(attempt_id, task.task_id, 1).start(),
        permission=harness.evidence.permission, acp_session_id="ses_acp",
    )
    return control


def test_commit_then_raise_keeps_human_gate_closed() -> None:
    async def scenario() -> None:
        harness = Harness()
        try:
            attempt_id = await harness.start()
            original = harness.store.execute_once
            raised = False

            def commit_then_raise(command_id, command_kind, callback):
                nonlocal raised
                result = original(command_id, command_kind, callback)
                if command_kind == "human_takeover" and not raised:
                    raised = True
                    raise RuntimeError("post-commit transport loss")
                return result

            harness.store.execute_once = commit_then_raise
            result = await harness.service.takeover(attempt_id)
            assert result.accepted is True
            assert harness.store.load_aggregate("attempts", attempt_id)["state"] \
                == "human_controlled"
            assert harness.service.automatic_input_enabled is False
        finally:
            await harness.close()

    _run(scenario())


def test_return_commit_then_raise_reopens_only_from_durable_return() -> None:
    async def scenario() -> None:
        harness = Harness()
        try:
            attempt_id = await harness.start()
            await harness.service.takeover(attempt_id)
            original = harness.store.execute_once
            raised = False

            def commit_then_raise(command_id, command_kind, callback):
                nonlocal raised
                result = original(command_id, command_kind, callback)
                if command_kind == "human_return_control" and not raised:
                    raised = True
                    raise RuntimeError("post-commit transport loss")
                return result

            harness.store.execute_once = commit_then_raise
            result = await harness.service.return_control(attempt_id)
            assert result.accepted is True
            assert harness.store.load_aggregate("attempts", attempt_id)["state"] == "running"
            assert harness.service.automatic_input_enabled is True
        finally:
            await harness.close()

    _run(scenario())


def test_reconstructed_controller_replays_active_cycle_without_new_effects() -> None:
    async def scenario() -> None:
        harness = Harness()
        try:
            attempt_id = await harness.start()
            first = await harness.service.takeover(attempt_id)
            before = len(harness.store.commands), len(harness.store.events)
            rebuilt = _rebuilt_control(harness, attempt_id)
            replay = await rebuilt.takeover(attempt_id)
            assert replay == first
            assert rebuilt.automatic_input_enabled is False
            assert (len(harness.store.commands), len(harness.store.events)) == before
        finally:
            await harness.close()

    _run(scenario())


def test_consumed_old_cycle_cannot_reclose_gate() -> None:
    async def scenario() -> None:
        harness = Harness()
        try:
            attempt_id = await harness.start()
            await harness.service.takeover(attempt_id)
            await harness.service.return_control(attempt_id)
            rebuilt = _rebuilt_control(harness, attempt_id)
            assert rebuilt.automatic_input_enabled is True
            assert harness.store.load_aggregate("attempts", attempt_id)["state"] == "running"
        finally:
            await harness.close()

    _run(scenario())


def test_second_legitimate_takeover_uses_new_durable_generation() -> None:
    async def scenario() -> None:
        harness = Harness()
        try:
            attempt_id = await harness.start()
            await harness.service.takeover(attempt_id)
            await harness.service.return_control(attempt_id)
            rebuilt = _rebuilt_control(harness, attempt_id)
            before = len(harness.store.commands), len(harness.store.events)
            assert (await rebuilt.takeover(attempt_id)).accepted is True
            ownership = harness.store.load_aggregate("takeover_ownership", attempt_id)
            assert ownership["generation"] == 2 and ownership["state"] == "human"
            assert (len(harness.store.commands), len(harness.store.events)) \
                == (before[0] + 1, before[1] + 1)
        finally:
            await harness.close()

    _run(scenario())


def test_mission_hash_is_rejected_as_untyped_project_evidence() -> None:
    async def scenario() -> None:
        harness = Harness()
        try:
            attempt_id = await harness.start()
            harness.evidence.project = harness.confirmed.content_hash
            result = await harness.service.takeover(attempt_id)
            assert result.accepted is False
            assert result.diagnostic.code == "takeover_project_evidence_unavailable"
            assert harness.service.automatic_input_enabled is True
        finally:
            await harness.close()

    _run(scenario())


def test_takeover_exports_only_shared_observer_cursor() -> None:
    import agentdeck.application.takeover_control as module

    assert "TakeoverCursor" not in module.__dict__
    assert module.ObserverCursor is ObserverCursor


def test_takeover_units_remain_bounded() -> None:
    import agentdeck.application.takeover_control as control
    import agentdeck.application.takeover_records as records

    assert len(Path(control.__file__).read_text().splitlines()) <= 500
    assert len(Path(records.__file__).read_text().splitlines()) <= 500


class _CommitThenRaiseStore:
    def __init__(self, store: SQLiteStore) -> None:
        self.store, self.raised = store, False

    def __getattr__(self, name):
        return getattr(self.store, name)

    def execute_once(self, command_id, command_kind, callback):
        result = self.store.execute_once(command_id, command_kind, callback)
        if command_kind == "human_takeover" and not self.raised:
            self.raised = True
            raise RuntimeError("post-commit transport loss")
        return result


def _seed_sqlite(store, confirmed, task, attempt) -> None:
    connection = store._require_writer()
    now = NOW.isoformat()
    connection.execute("INSERT INTO projects VALUES ('prj_1', ?, ?)", (str(store._project_root), now))
    connection.execute(
        """INSERT INTO product_sessions (
               session_id,project_id,state,permission_profile,pending_goal,
               created_at,updated_at,leader_backend,leader_model)
           VALUES ('ses_product','prj_1','running','approve_for_me',NULL,?,?,?,?)""",
        (now, now, "codex-cli", "gpt-test"),
    )
    connection.execute(
        "INSERT INTO agent_instances VALUES ('agt_implementation','ses_product','codex-cli','acp','1','implementer','ses_acp','active',?,?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO missions VALUES (?,'ses_product','running',1,?,?)",
        (confirmed.mission_id, now, now),
    )
    connection.execute(
        "INSERT INTO mission_versions VALUES (?,1,'prv_1',?,?,?)",
        (confirmed.mission_id, confirmed.content_hash, confirmed.canonical_content, now),
    )
    canonical = json.dumps({
        "task_id": task.task_id, "agent_instance_id": task.agent_instance_id,
        "dependencies": [],
    }, sort_keys=True, separators=(",", ":"))
    connection.execute(
        """INSERT INTO tasks VALUES (?,?,?,?,?,'implementer','codex-cli',?,
           'acp://route','running',?,?,?)""",
        (task.task_id, confirmed.mission_id, 1, 1, task.name,
         task.agent_instance_id, canonical, now, now),
    )
    snapshot = attempt_snapshot(attempt, task, "ses_acp")
    store.execute_once(
        "cmd_seed_attempt", "execution_attempt_started",
        lambda transaction: transaction.save_aggregate(
            "attempts", attempt.attempt_id, snapshot,
        ) or {"attempt_id": attempt.attempt_id},
    )


def test_real_sqlite_commit_then_raise_reconciles_durable_human_owner(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = Harness()
        store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
        try:
            task = harness.draft.tasks[0]
            attempt = Attempt.pending("att_sqlite", task.task_id, 1).start()
            _seed_sqlite(store, harness.confirmed, task, attempt)
            worker = harness.worker
            handle = WorkerHandle("ses_acp", task.agent_instance_id, task.task_id, attempt.attempt_id)
            runtime = ForegroundExecutionRuntime()
            runtime.bind(ActiveExecutionBinding(
                attempt.attempt_id, task.task_id, task.agent_instance_id,
                handle.session_id, handle, worker,
            ))
            permission = PermissionScope(
                PermissionProfile.APPROVE_FOR_ME, frozenset({Effect.READ}),
            )
            control = TakeoverControl(
                store=_CommitThenRaiseStore(store), clock=FrozenClock(NOW), runtime=runtime,
                project_evidence=project_evidence, permission_snapshot=lambda: permission,
                observer_cursor=lambda: ObserverCursor(
                    "prj_1", "ses_acp", task.agent_instance_id, task.task_id,
                    attempt.attempt_id, "acp", 1, "evt_started", "b" * 64,
                ),
            )
            control.arm(
                product_session_id="ses_product", confirmed=harness.confirmed,
                task=task, attempt=attempt, permission=permission,
                acp_session_id="ses_acp",
            )
            result = await control.takeover(attempt.attempt_id)
            assert result.accepted is True
            assert store.load_aggregate("attempts", attempt.attempt_id)["state"] \
                == "human_controlled"
            assert store.load_aggregate("takeover_ownership", attempt.attempt_id)["state"] \
                == "human"
            assert control.automatic_input_enabled is False
        finally:
            store.close()
            await harness.close()

    _run(scenario())
