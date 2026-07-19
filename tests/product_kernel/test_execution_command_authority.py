from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
import inspect
import json
import textwrap

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.execution_authority import attempt_snapshot
from agentdeck.application.execution_records import command_id
from agentdeck.application.execution_service import ExecutionService
from agentdeck.kernel.execution import Attempt
from agentdeck.ports.worker import WorkerHandle
from product_kernel.fakes import FrozenClock
from product_kernel.test_execution_coordinator import Harness, ScriptedWorker
from product_kernel.test_review_revision_semantics import _review_replay
from product_kernel.test_sqlite_execution import NOW, _seed_lineage


def _execute_once_inventory() -> dict[str, set[str]]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(ExecutionService)))
    inventory = {}
    for method in (node for node in tree.body[0].body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(method) if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        if "execute_once" in calls:
            inventory[method.name] = calls
    return inventory


def test_every_execution_command_boundary_has_explicit_replay_authority() -> None:
    inventory = _execute_once_inventory()

    assert set(inventory) == {
        "_persist_started", "_bind_acp_session", "_persist_terminal",
        "_persist_terminal_attempt",
    }
    assert "load_aggregate" not in inventory["_persist_started"]
    assert {"load_aggregate", "bound_attempt_reference",
            "validated_bound_attempt"} <= inventory["_bind_acp_session"]
    assert {"load_aggregate", "terminal_references",
            "validated_terminal_bundle"} <= inventory["_persist_terminal"]
    assert {"load_aggregate", "stopped_attempt_reference",
            "validated_stopped_attempt"} <= inventory["_persist_terminal_attempt"]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_start_replay_is_nonadvancing_before_worker_io(tmp_path, backend) -> None:
    store = None if backend == "memory" else SQLiteStore.open(
        tmp_path, clock=FrozenClock(NOW)
    )
    try:
        harness = Harness() if store is None else Harness(store=store)
        task = harness.draft.tasks[0]
        key = (command_id("start", harness.confirmed, task, 1),
               "execution_attempt_started")
        if hasattr(harness.store, "commands"):
            harness.store.commands[key] = {"legacy": "untrusted"}
        else:
            harness.store.execute_once(key[0], key[1], lambda transaction: {
                "legacy": "untrusted"
            })

        result = asyncio.run(harness.run())

        assert result.diagnostic.code == "mission_execution_replayed"
        assert harness.started_tasks == []
        assert result.attempts == ()
    finally:
        if store is not None:
            store.close()


def _binding_result(harness, request, session_id):
    return {
        "mission_id": harness.confirmed.mission_id,
        "mission_version": harness.confirmed.version,
        "task_id": request.task_id, "attempt_id": request.attempt_id,
        "acp_session_id": session_id,
    }


def _corrupt_binding(kind, command):
    if kind == "missing_result_field":
        command.pop("mission_version")
    elif kind == "legacy_result":
        command.clear(); command.update(
            attempt_id="att_legacy", acp_session_id="ses_legacy"
        )
    elif kind.startswith("wrong_"):
        field = kind.removeprefix("wrong_")
        command[field] = {
            "mission_id": "msn_hostile", "mission_version": 99,
            "task_id": "tsk_hostile", "attempt_id": "att_hostile",
            "acp_session_id": "ses_hostile",
        }[field]


class BindingReplayWorker(ScriptedWorker):
    async def start_task(self, request):
        self._harness.started_tasks.append(self._task_name)
        self._harness.requests.append(request)
        self._request = request
        self._handle = WorkerHandle(
            "ses_1", request.agent_id, request.task_id, request.attempt_id
        )
        handle = self._handle
        if not self._harness.bind_replay_seeded:
            self._harness.bind_replay_seeded = True
            task = next(item for item in self._harness.draft.tasks
                        if item.task_id == request.task_id)
            running = Attempt.pending(request.attempt_id, request.task_id, 1).start()
            command = _binding_result(self._harness, request, handle.session_id)
            snapshot = attempt_snapshot(running, task, handle.session_id)
            _corrupt_binding(self._harness.bind_corruption, command)
            key = (command_id("bind_acp", self._harness.confirmed, task, 1),
                   "execution_acp_session_bound")
            store = self._harness.store
            store.execute_once(key[0], key[1], lambda transaction: (
                transaction.save_aggregate("attempts", request.attempt_id, snapshot)
                or command
            ))
            if self._harness.bind_corruption == "missing_snapshot":
                if hasattr(store, "aggregates"):
                    store.aggregates.pop(("attempts", request.attempt_id), None)
                else:
                    store._require_writer().execute(
                        "DELETE FROM attempts WHERE attempt_id=?", (request.attempt_id,)
                    )
            elif self._harness.bind_corruption == "mismatched_snapshot":
                if hasattr(store, "aggregates"):
                    store.aggregates[("attempts", request.attempt_id)][
                        "retryable"
                    ] = True
                else:
                    store._require_writer().execute(
                        "UPDATE attempts SET retryable=1 WHERE attempt_id=?",
                        (request.attempt_id,),
                    )
        return handle

    async def _events(self):
        raise RuntimeError("bridge stop sentinel")
        yield


def _binding_harness(tmp_path, backend, corruption):
    store = None if backend == "memory" else SQLiteStore.open(
        tmp_path, clock=FrozenClock(NOW)
    )
    if store is not None:
        _seed_lineage(store)
    harness = Harness() if store is None else Harness(store=store)
    harness.bind_replay_seeded = False
    harness.bind_corruption = corruption
    harness.bind_callback_count = 0
    execute_once = harness.store.execute_once

    def counted(command_id_value, command_kind, callback):
        def tracked(transaction):
            if command_kind == "execution_acp_session_bound":
                harness.bind_callback_count += 1
            return callback(transaction)
        return execute_once(command_id_value, command_kind, tracked)

    harness.store.execute_once = counted
    harness.service._worker_factory = lambda task: BindingReplayWorker(
        harness, task.name
    )
    return harness, store


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("corruption", [
    "missing_result_field", "legacy_result", "wrong_mission_id",
    "wrong_mission_version", "wrong_task_id", "wrong_attempt_id",
    "wrong_acp_session_id", "missing_snapshot", "mismatched_snapshot",
])
def test_binding_replay_requires_exact_command_and_attempt_snapshot(
    tmp_path, backend, corruption,
) -> None:
    harness, store = _binding_harness(tmp_path, backend, corruption)
    try:
        result = asyncio.run(harness.run())

        assert result.diagnostic.code == "acp_session_binding_failed"
        assert harness.approvals.scopes == []
        assert harness.started_tasks == ["implementation"]
        assert harness.bind_callback_count == 1
        assert "hostile" not in result.diagnostic.cause
    finally:
        if store is not None:
            store.close()


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_exact_binding_replay_advances_without_duplicate_mutation(
    tmp_path, backend,
) -> None:
    harness, store = _binding_harness(tmp_path, backend, "exact")
    try:
        result = asyncio.run(harness.run())

        assert result.diagnostic.code == "worker_bridge_failed"
        assert len(harness.approvals.scopes) == 1
        assert harness.started_tasks == ["implementation"]
        assert harness.bind_callback_count == 1
    finally:
        if store is not None:
            store.close()


SQLITE_TERMINAL_CORRUPTIONS = (
    "missing_result_field", "old_result", "missing_attempt",
    "missing_evidence", "missing_handoff", "mismatched_attempt",
    "mismatched_evidence", "mismatched_handoff", "partial_ids",
    "reordered_ids", "extra_ids",
)


def _seed_complete_sqlite_lineage(store, harness) -> None:
    _seed_lineage(store)
    connection = store._require_writer()
    now = NOW.isoformat()
    for ordinal, task in enumerate(harness.draft.tasks[1:], 2):
        connection.execute(
            "INSERT INTO agent_instances VALUES (?,?,?,?,?,?,?,?,?,?)",
            (task.agent_instance_id, "ses_1", task.backend, "acp", "1",
             task.role.value, f"acp_seed_{task.name}", "active", now, now),
        )
        canonical = json.dumps({
            "task_id": task.task_id,
            "agent_instance_id": task.agent_instance_id,
            "dependencies": list(task.dependencies),
        }, sort_keys=True, separators=(",", ":"))
        if task.name == "review":
            connection.execute(
                """UPDATE tasks SET name=?,role=?,planned_backend=?,
                          planned_agent_instance_id=?,acp_route=?,
                          canonical_task_facts=? WHERE task_id=?""",
                (task.name, task.role.value, task.backend,
                 task.agent_instance_id, task.acp_route, canonical, task.task_id),
            )
        else:
            connection.execute(
                """INSERT INTO tasks (
                       task_id,mission_id,mission_version,ordinal,name,role,
                       planned_backend,planned_agent_instance_id,acp_route,state,
                       canonical_task_facts,created_at,updated_at)
                   VALUES (?,'msn_1',1,?,?,?,?,?,?,'running',?,?,?)""",
                (task.task_id, ordinal, task.name, task.role.value, task.backend,
                 task.agent_instance_id, task.acp_route, canonical, now, now),
            )


def _corrupt_terminal_command(command, corruption, aggregates):
    if corruption == "missing_result_field":
        command.pop("mission_version")
    elif corruption == "old_result":
        command.clear(); command.update(
            attempt_id="att_old", evidence_id="ev_old"
        )
    elif corruption == "partial_ids":
        command["evidence_ids"] = command["evidence_ids"][:1]
    elif corruption == "reordered_ids":
        command["evidence_ids"].reverse()
    elif corruption == "extra_ids":
        command["evidence_ids"].append("ev_extra")
        source = next(
            snapshot for (kind, _), snapshot in aggregates.items()
            if kind == "evidence"
        )
        aggregates[("evidence", "ev_extra")] = dict(
            source, evidence_id="ev_extra"
        )


def _corrupt_terminal_aggregate(store, corruption, command) -> None:
    connection = store._require_writer()
    attempt_id = command["attempt_id"]
    evidence_id = command["evidence_ids"][0]
    handoff_id = command["handoff_id"]
    if corruption == "missing_attempt":
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "DELETE FROM attempts WHERE attempt_id=?", (attempt_id,)
        )
        connection.execute("PRAGMA foreign_keys=ON")
    elif corruption == "missing_evidence":
        connection.execute(
            "DELETE FROM evidence WHERE evidence_id=?", (evidence_id,)
        )
    elif corruption == "missing_handoff":
        connection.execute(
            "DELETE FROM handoffs WHERE handoff_id=?", (handoff_id,)
        )
    elif corruption == "mismatched_attempt":
        connection.execute(
            "UPDATE attempts SET result_summary='hostile-marker' "
            "WHERE attempt_id=?", (attempt_id,),
        )
    elif corruption == "mismatched_evidence":
        connection.execute(
            "UPDATE evidence SET canonical_evidence_facts='{}' "
            "WHERE evidence_id=?", (evidence_id,),
        )
    elif corruption == "mismatched_handoff":
        connection.execute(
            "UPDATE handoffs SET result_summary='hostile-marker' "
            "WHERE handoff_id=?", (handoff_id,),
        )


def _seed_sqlite_terminal_replay(store, replay, corruption) -> None:
    command = deepcopy(replay["command"])
    aggregates = deepcopy(replay["aggregates"])
    _corrupt_terminal_command(command, corruption, aggregates)

    def commit(transaction):
        for (kind, identity), snapshot in aggregates.items():
            transaction.save_aggregate(kind, identity, snapshot)
        return command

    store.execute_once(replay["key"][0], replay["key"][1], commit)
    _corrupt_terminal_aggregate(store, corruption, replay["command"])


class SQLiteTerminalReplayWorker(ScriptedWorker):
    async def start_task(self, request):
        self._harness.started_tasks.append(self._task_name)
        self._harness.requests.append(request)
        self._request = request
        self._handle = WorkerHandle(
            "ses_1", request.agent_id, request.task_id, request.attempt_id
        )
        return self._handle

    def _result_payload(self):
        payload = deepcopy(self._harness.results[self._task_name])
        previous = {
            "review": "implementation", "acceptance": "revision",
        }.get(self._task_name)
        prior = []
        if previous is not None:
            task_id = next(
                task.task_id for task in self._harness.draft.tasks
                if task.name == previous
            )
            prior = [row[0] for row in self._harness.store._require_writer().execute(
                "SELECT evidence_id FROM evidence WHERE task_id=? ORDER BY evidence_id",
                (task_id,),
            )]
        if self._task_name == "review":
            for finding in payload["findings"]:
                finding["evidence_ids"] = prior
        elif self._task_name == "revision":
            authority = json.loads(self._request.instruction)[
                "authoritative_revision_task"
            ]
            payload["resolved_finding_ids"] = [
                finding["finding_id"] for finding in authority["accepted_findings"]
            ]
            payload["evidence_ids"] = [
                finding["evidence_lineage"]["review_evidence_id"]
                for finding in authority["accepted_findings"]
            ]
        elif self._task_name == "acceptance":
            payload["evidence_by_criterion"] = {
                criterion: prior for criterion in payload["evidence_by_criterion"]
            }
        return payload

    async def collect_result(self, handle):
        result = await super().collect_result(handle)
        if self._task_name == "review":
            _seed_sqlite_terminal_replay(
                self._harness.store, self._harness.terminal_replay,
                self._harness.terminal_corruption,
            )
        return result


def _sqlite_terminal_harness(tmp_path, corruption):
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    harness = Harness(store=store)
    _seed_complete_sqlite_lineage(store, harness)
    harness.results["review"]["findings"].append(dict(
        harness.results["review"]["findings"][0], finding_id="rfn_2"
    ))
    command, aggregates, key = _review_replay()
    harness.terminal_replay = {
        "command": command, "aggregates": aggregates, "key": key,
    }
    harness.terminal_corruption = corruption
    harness.service._worker_factory = lambda task: SQLiteTerminalReplayWorker(
        harness, task.name
    )
    return harness, store


@pytest.mark.parametrize("corruption", SQLITE_TERMINAL_CORRUPTIONS)
def test_sqlite_terminal_replay_requires_complete_exact_durable_bundle(
    tmp_path, corruption,
) -> None:
    harness, store = _sqlite_terminal_harness(tmp_path, corruption)
    try:
        result = asyncio.run(harness.run())

        assert result.diagnostic.code == "stage_bundle_persistence_failed"
        assert result.diagnostic.cause == "terminal execution bundle did not commit"
        assert harness.started_tasks == ["implementation", "review"]
        assert len(result.evidence) == 1 and len(result.handoffs) == 1
        assert "hostile-marker" not in result.diagnostic.cause
    finally:
        store.close()


def test_exact_sqlite_terminal_replay_completes_the_frozen_graph(tmp_path) -> None:
    harness, store = _sqlite_terminal_harness(tmp_path, "exact")
    try:
        result = asyncio.run(harness.run())

        assert result.diagnostic is None
        assert harness.started_tasks == [
            "implementation", "review", "revision", "acceptance"
        ]
        assert len(result.attempts) == 4
    finally:
        store.close()
