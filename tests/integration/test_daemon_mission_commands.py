from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import sqlite3

import pytest

from agentdeck.app.mission_service import MissionProposal
from agentdeck.daemon.protocol import (
    RpcProtocolError,
    RpcRequest,
    decode_request,
    encode_request,
)
from agentdeck.daemon.service import ProjectDaemonService, ServiceError
from agentdeck.domain.authorization import AuthorizationEnvelope, ExternalEffectPolicy
from agentdeck.domain.mission import MissionVersion, TaskSpec
from agentdeck.storage.ownership import ProjectWriterLease
from agentdeck.storage.sqlite_store import SQLiteMissionStore
from agentdeck.storage.sqlite_store import SQLiteStoreError


class _Server:
    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _daemon_service(server: object | None = None) -> ProjectDaemonService:
    return ProjectDaemonService(
        server=_Server() if server is None else server,
        reconcile_all=lambda: None,
        flush_safe_outboxes=lambda: None,
        load_scheduler_facts=lambda: None,
        apply_transition=lambda _decision: None,
    )


def _prepared_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    lease = ProjectWriterLease.acquire(root)
    store = SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
    store.close()
    lease.close()
    return root


def _proposal() -> MissionProposal:
    mission = MissionVersion(
        mission_id="mis_1",
        version=1,
        goal="Implement the durable Mission kernel",
        scope=("src/agentdeck",),
        exclusions=("global configuration",),
        tasks=(
            TaskSpec(
                task_id="tsk_implementation",
                objective="Implement one bounded change",
                role="codex-worker",
                scope=("src/agentdeck",),
                acceptance_contribution=("implementation exists",),
                acceptance_criteria=("focused tests pass",),
                concurrency_keys=("repository",),
                retry_limit=1,
                budget_units=10,
            ),
        ),
        acceptance_criteria=("implementation accepted",),
        constraints=("local only",),
        max_parallel_tasks=1,
        budget_units=20,
        ordered_routes=("codex",),
        expires_at=None,
        provenance_source="leader",
        provenance_id="turn_1",
        metadata={"schema": "mission/v1"},
    )
    authority = AuthorizationEnvelope(
        goal=mission.goal,
        semantic_scope=mission.scope,
        path_scope=("src/agentdeck",),
        exclusions=mission.exclusions,
        operations=("read", "write", "test"),
        allowed_agents=("codex",),
        allowed_roles=("codex-worker",),
        external_effect_policy=ExternalEffectPolicy.DENY,
        max_attempts=2,
        max_retries=1,
        max_recoveries=1,
        budget_units=20,
        acceptance_criteria=mission.acceptance_criteria,
        ordered_routes=mission.ordered_routes,
        expires_at=None,
        metadata={"authority": "human-confirmation-required"},
    )
    return MissionProposal(
        mission,
        authority,
        {"provider": "fake", "model": "deterministic", "turn_id": "turn_1"},
    )


def _command(*, command_id: str, expected_revision: int) -> dict[str, object]:
    return {
        "command_id": command_id,
        "actor": {"kind": "human", "id": "user_1"},
        "expected_revision": expected_revision,
        "created_at": "2026-07-18T08:00:00Z",
    }


def _propose_params(
    proposal: MissionProposal, *, command_id: str = "cmd_propose", revision: int = 0
) -> dict[str, object]:
    return {
        "command": _command(command_id=command_id, expected_revision=revision),
        "mission_version": proposal.mission_version.to_dict(),
        "authorization_envelope": proposal.authorization_envelope.to_dict(),
        "authorization_digest": proposal.authorization_digest,
        "leader_provenance": proposal.leader_provenance_dict(),
        "expected_authority_state": "sqlite_active",
    }


def _confirm_params(
    proposal: MissionProposal, *, command_id: str = "cmd_confirm", revision: int = 1
) -> dict[str, object]:
    return {
        "command": _command(command_id=command_id, expected_revision=revision),
        "mission_id": proposal.mission_version.mission_id,
        "version": proposal.mission_version.version,
        "authorization_digest": proposal.authorization_digest,
        "expected_authority_state": "sqlite_active",
    }


async def _execute_queued(
    runtime: object,
    service: ProjectDaemonService,
    method: str,
    params: dict[str, object],
) -> dict[str, object]:
    pending = asyncio.create_task(runtime.handle_rpc(method, params))
    await asyncio.sleep(0)
    await service.tick()
    return await pending


def test_protocol_exposes_only_the_closed_mission_rpc_surface() -> None:
    protocol = importlib.import_module("agentdeck.daemon.protocol")
    methods = protocol.MISSION_RPC_METHODS

    assert methods == frozenset(
        {"mission.propose", "mission.confirm", "mission.status", "events.after"}
    )
    request = RpcRequest("req_1", "mission.status", {"mission_id": "mis_1"})
    frame = encode_request(request, max_bytes=4096, allowed_methods=methods)
    assert decode_request(frame, max_bytes=4096, allowed_methods=methods) == request
    with pytest.raises(RpcProtocolError, match="not allowed"):
        encode_request(
            RpcRequest("req_2", "sql.execute", {"sql": "DELETE FROM missions"}),
            max_bytes=4096,
            allowed_methods=methods,
        )


def test_runtime_rejects_mutation_before_start_and_after_close(tmp_path: Path) -> None:
    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        service = _daemon_service()
        runtime = runtime_module.DaemonMissionRuntime(
            _prepared_root(tmp_path), daemon_service=service
        )
        proposal = _proposal()

        with pytest.raises(runtime_module.MissionRuntimeError, match="not started"):
            await runtime.handle_rpc("mission.propose", _propose_params(proposal))
        await runtime.start()
        await runtime.close()
        with pytest.raises(runtime_module.MissionRuntimeError, match="not started"):
            await runtime.handle_rpc("mission.propose", _propose_params(proposal))

    asyncio.run(case())


def test_daemon_service_reports_the_exact_governed_mutation_lifecycle() -> None:
    async def case() -> None:
        service = _daemon_service()
        assert service.accepting_governed_mutations is False
        await service.start()
        assert service.accepting_governed_mutations is True
        await service.close()
        assert service.accepting_governed_mutations is False

    asyncio.run(case())


def test_second_runtime_writer_lease_conflicts_without_fallback(tmp_path: Path) -> None:
    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        root = _prepared_root(tmp_path)
        first = runtime_module.DaemonMissionRuntime(
            root, daemon_service=_daemon_service()
        )
        second = runtime_module.DaemonMissionRuntime(
            root, daemon_service=_daemon_service()
        )
        await first.start()
        try:
            with pytest.raises(runtime_module.MissionRuntimeError, match="writer"):
                await second.start()
        finally:
            await second.close()
            await first.close()

    asyncio.run(case())


def test_cancelled_runtime_start_releases_the_sole_writer_lease(tmp_path: Path) -> None:
    class _BlockingServer:
        def __init__(self) -> None:
            self.entered = asyncio.Event()

        async def start(self) -> None:
            self.entered.set()
            await asyncio.Future()

        async def close(self) -> None:
            return None

    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        root = _prepared_root(tmp_path)
        blocking = _BlockingServer()
        interrupted = runtime_module.DaemonMissionRuntime(
            root, daemon_service=_daemon_service(blocking)
        )
        start = asyncio.create_task(interrupted.start())
        await blocking.entered.wait()
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start

        replacement = runtime_module.DaemonMissionRuntime(
            root, daemon_service=_daemon_service()
        )
        await replacement.start()
        await replacement.close()

    asyncio.run(case())


def test_close_during_start_cannot_resurrect_runtime_or_writer(
    tmp_path: Path,
) -> None:
    class _BlockingServer:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def start(self) -> None:
            self.entered.set()
            await self.release.wait()

        async def close(self) -> None:
            return None

    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        root = _prepared_root(tmp_path)
        blocking = _BlockingServer()
        interrupted = runtime_module.DaemonMissionRuntime(
            root, daemon_service=_daemon_service(blocking)
        )
        start = asyncio.create_task(interrupted.start())
        await blocking.entered.wait()

        await interrupted.close()
        blocking.release.set()
        with pytest.raises((asyncio.CancelledError, runtime_module.MissionRuntimeError)):
            await start
        with pytest.raises(runtime_module.MissionRuntimeError, match="not started"):
            await interrupted.handle_rpc(
                "mission.status", {"mission_id": "mis_1"}
            )

        replacement = runtime_module.DaemonMissionRuntime(
            root, daemon_service=_daemon_service()
        )
        await replacement.start()
        await replacement.close()

    asyncio.run(case())


def test_cleanup_releases_lease_even_when_store_close_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BlockingServer:
        def __init__(self) -> None:
            self.entered = asyncio.Event()

        async def start(self) -> None:
            self.entered.set()
            await asyncio.Future()

        async def close(self) -> None:
            return None

    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        root = _prepared_root(tmp_path)
        blocking = _BlockingServer()
        runtime = runtime_module.DaemonMissionRuntime(
            root, daemon_service=_daemon_service(blocking)
        )
        start = asyncio.create_task(runtime.start())
        await blocking.entered.wait()
        original_close = SQLiteMissionStore.close

        def close_then_fail(store: SQLiteMissionStore) -> None:
            original_close(store)
            raise RuntimeError(f"private cleanup detail: {tmp_path}")

        monkeypatch.setattr(SQLiteMissionStore, "close", close_then_fail)
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start

        monkeypatch.setattr(SQLiteMissionStore, "close", original_close)
        replacement = runtime_module.DaemonMissionRuntime(
            root, daemon_service=_daemon_service()
        )
        await replacement.start()
        await replacement.close()

    asyncio.run(case())


def test_runtime_close_rejects_an_admitted_but_unexecuted_mutation(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        service = _daemon_service()
        runtime = runtime_module.DaemonMissionRuntime(
            _prepared_root(tmp_path), daemon_service=service
        )
        await runtime.start()
        pending = asyncio.create_task(
            runtime.handle_rpc("mission.propose", _propose_params(_proposal()))
        )
        await asyncio.sleep(0)
        await runtime.close()
        with pytest.raises(ServiceError, match="closed"):
            await pending

    asyncio.run(case())


def test_client_params_cannot_carry_storage_callback_or_sql_authority(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        service = _daemon_service()
        runtime = runtime_module.DaemonMissionRuntime(
            _prepared_root(tmp_path), daemon_service=service
        )
        await runtime.start()
        connection = sqlite3.connect(":memory:")
        try:
            assert not any(
                hasattr(runtime, name)
                for name in ("store", "connection", "execute", "submit_sql")
            )
            proposal = _proposal()
            prohibited = (
                {**_propose_params(proposal), "store": object()},
                {**_propose_params(proposal), "connection": connection},
                {**_propose_params(proposal), "callback": lambda: None},
                {**_propose_params(proposal), "sql": "DELETE FROM missions"},
            )
            for params in prohibited:
                with pytest.raises(
                    runtime_module.MissionRuntimeError, match="invalid request"
                ):
                    await runtime.handle_rpc("mission.propose", params)
            with pytest.raises(runtime_module.MissionRuntimeError, match="not allowed"):
                await runtime.handle_rpc("sql.execute", {"sql": "SELECT 1"})
        finally:
            connection.close()
            await runtime.close()

    asyncio.run(case())


def test_read_only_observation_is_bounded_and_does_not_grant_mutation(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        service = _daemon_service()
        runtime = runtime_module.DaemonMissionRuntime(
            _prepared_root(tmp_path), daemon_service=service
        )
        await runtime.start()
        try:
            before = await runtime.handle_rpc(
                "mission.status", {"mission_id": "mis_missing"}
            )
            events = await runtime.handle_rpc("events.after", {"cursor": 0, "limit": 10})
            after = await runtime.handle_rpc(
                "mission.status", {"mission_id": "mis_missing"}
            )
            assert before == after == {
                "authority_state": "sqlite_active",
                "mission": None,
                "project_revision": 0,
            }
            assert events == {
                "cursor": 0,
                "events": [],
                "has_more": False,
                "project_revision": 0,
            }
            assert set(before) == {"authority_state", "mission", "project_revision"}
            with pytest.raises(runtime_module.MissionRuntimeError, match="invalid request"):
                await runtime.handle_rpc(
                    "mission.confirm",
                    {"mission_id": "mis_1", "authorization": before},
                )
        finally:
            await runtime.close()

    asyncio.run(case())


def test_propose_and_confirm_execute_only_inside_daemon_mutation_queue(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        service = _daemon_service()
        runtime = runtime_module.DaemonMissionRuntime(
            _prepared_root(tmp_path), daemon_service=service
        )
        proposal = _proposal()
        await runtime.start()
        try:
            pending_propose = asyncio.create_task(
                runtime.handle_rpc("mission.propose", _propose_params(proposal))
            )
            await asyncio.sleep(0)
            assert pending_propose.done() is False
            before = await runtime.handle_rpc(
                "mission.status", {"mission_id": "mis_1"}
            )
            assert before["project_revision"] == 0
            await service.tick()
            propose = await pending_propose
            assert propose["revision"] == 1
            assert propose["result"]["status"] == "proposed"
            confirm = await _execute_queued(
                runtime, service, "mission.confirm", _confirm_params(proposal)
            )
            assert confirm["revision"] == 2
            assert confirm["result"]["status"] == "confirmed"
            status = await runtime.handle_rpc("mission.status", {"mission_id": "mis_1"})
            assert status["project_revision"] == 2
            assert status["mission"] == {
                "authorization_digest": proposal.authorization_digest,
                "mission_id": "mis_1",
                "status": "confirmed",
                "task_count": 1,
                "version": 1,
            }
        finally:
            await runtime.close()

    asyncio.run(case())


def test_queued_commands_revalidate_revision_and_authority_at_execution(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        service = _daemon_service()
        runtime = runtime_module.DaemonMissionRuntime(
            _prepared_root(tmp_path), daemon_service=service
        )
        proposal = _proposal()
        await runtime.start()
        try:
            first = asyncio.create_task(
                runtime.handle_rpc("mission.propose", _propose_params(proposal))
            )
            # This confirmation is admitted before the proposal exists.  Its
            # exact revision/digest/state become valid only when it reaches the
            # mutation loop after the proposal commits.
            queued_confirm = asyncio.create_task(
                runtime.handle_rpc("mission.confirm", _confirm_params(proposal))
            )
            stale = asyncio.create_task(
                runtime.handle_rpc(
                    "mission.propose",
                    _propose_params(proposal, command_id="cmd_stale", revision=0),
                )
            )
            await asyncio.sleep(0)
            await service.tick()
            assert (await first)["revision"] == 1
            await service.tick()
            assert (await queued_confirm)["result"]["status"] == "confirmed"
            await service.tick()
            with pytest.raises(ServiceError, match="authority is stale"):
                await stale

            stale_state = asyncio.create_task(
                runtime.handle_rpc(
                    "mission.confirm",
                    _confirm_params(proposal, command_id="cmd_stale_confirm"),
                )
            )
            await asyncio.sleep(0)
            await service.tick()
            with pytest.raises(ServiceError, match="authority is stale"):
                await stale_state
        finally:
            await runtime.close()

    asyncio.run(case())


def test_lost_responses_replay_exact_propose_and_confirm_outcomes(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        service = _daemon_service()
        runtime = runtime_module.DaemonMissionRuntime(
            _prepared_root(tmp_path), daemon_service=service
        )
        proposal = _proposal()
        propose_params = _propose_params(proposal)
        await runtime.start()
        try:
            first_propose = await _execute_queued(
                runtime, service, "mission.propose", propose_params
            )
            replayed_propose = await _execute_queued(
                runtime, service, "mission.propose", propose_params
            )
            assert replayed_propose == first_propose

            conflicting = _propose_params(proposal)
            conflicting["command"] = {
                **conflicting["command"],
                "created_at": "2026-07-18T08:00:01Z",
            }
            conflict = asyncio.create_task(
                runtime.handle_rpc("mission.propose", conflicting)
            )
            await asyncio.sleep(0)
            await service.tick()
            with pytest.raises(
                runtime_module.MissionRuntimeError,
                match="^Mission command conflict$",
            ):
                await conflict

            stale = asyncio.create_task(
                runtime.handle_rpc(
                    "mission.propose",
                    _propose_params(
                        proposal, command_id="cmd_new_stale", revision=0
                    ),
                )
            )
            await asyncio.sleep(0)
            await service.tick()
            with pytest.raises(ServiceError, match="authority is stale"):
                await stale

            confirm_params = _confirm_params(proposal)
            first_confirm = await _execute_queued(
                runtime, service, "mission.confirm", confirm_params
            )
            replayed_confirm = await _execute_queued(
                runtime, service, "mission.confirm", confirm_params
            )
            assert replayed_confirm == first_confirm
        finally:
            await runtime.close()

    asyncio.run(case())


@pytest.mark.parametrize(
    "operation", ("propose", "confirm", "status", "events")
)
@pytest.mark.parametrize("fail_execute", (False, True))
def test_every_runtime_reader_is_explicitly_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    fail_execute: bool,
) -> None:
    class _TrackingReader:
        def __init__(self, connection: object) -> None:
            self.connection = connection
            self.close_count = 0

        def execute(self, *args: object, **kwargs: object) -> object:
            if fail_execute:
                raise RuntimeError("private reader failure")
            return self.connection.execute(*args, **kwargs)

        def commit(self) -> None:
            self.connection.commit()

        def close(self) -> None:
            self.close_count += 1
            self.connection.close()

    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        service = _daemon_service()
        runtime = runtime_module.DaemonMissionRuntime(
            _prepared_root(tmp_path), daemon_service=service
        )
        proposal = _proposal()
        await runtime.start()
        try:
            if operation == "confirm":
                await _execute_queued(
                    runtime, service, "mission.propose", _propose_params(proposal)
                )
            original_open = SQLiteMissionStore.open_reader
            readers: list[_TrackingReader] = []

            def tracked_open(store: SQLiteMissionStore) -> _TrackingReader:
                reader = _TrackingReader(original_open(store))
                readers.append(reader)
                return reader

            monkeypatch.setattr(SQLiteMissionStore, "open_reader", tracked_open)
            if operation in {"propose", "confirm"}:
                method = f"mission.{operation}"
                params = (
                    _propose_params(proposal)
                    if operation == "propose"
                    else _confirm_params(proposal)
                )
                pending = asyncio.create_task(runtime.handle_rpc(method, params))
                await asyncio.sleep(0)
                await service.tick()
                if fail_execute:
                    with pytest.raises(runtime_module.MissionRuntimeError):
                        await pending
                else:
                    assert (await pending)["revision"] in {1, 2}
            else:
                method = "mission.status" if operation == "status" else "events.after"
                params = (
                    {"mission_id": "mis_1"}
                    if operation == "status"
                    else {"cursor": 0, "limit": 10}
                )
                if fail_execute:
                    with pytest.raises(
                        runtime_module.MissionRuntimeError,
                        match="observation is unavailable",
                    ):
                        await runtime.handle_rpc(method, params)
                else:
                    await runtime.handle_rpc(method, params)
            assert len(readers) == 1
            assert readers[0].close_count == 1
        finally:
            await runtime.close()

    asyncio.run(case())


def test_events_after_returns_only_closed_sanitized_event_summaries(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        service = _daemon_service()
        runtime = runtime_module.DaemonMissionRuntime(
            _prepared_root(tmp_path), daemon_service=service
        )
        proposal = _proposal()
        await runtime.start()
        try:
            await _execute_queued(
                runtime, service, "mission.propose", _propose_params(proposal)
            )
            page = await runtime.handle_rpc("events.after", {"cursor": 0, "limit": 1})
            assert page["project_revision"] == 1
            assert page["cursor"] == 1
            assert page["has_more"] is False
            assert len(page["events"]) == 1
            assert set(page["events"][0]) == {
                "cursor",
                "event_id",
                "kind",
                "project_revision",
                "trigger_kind",
            }
            assert "provider" not in repr(page)
            with pytest.raises(runtime_module.MissionRuntimeError, match="invalid request"):
                await runtime.handle_rpc("events.after", {"cursor": -1, "limit": 10})
            with pytest.raises(runtime_module.MissionRuntimeError, match="invalid request"):
                await runtime.handle_rpc("events.after", {"cursor": 0, "limit": 101})
        finally:
            await runtime.close()

    asyncio.run(case())


def test_domain_and_storage_failures_cross_rpc_as_sanitized_errors(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        service = _daemon_service()
        runtime = runtime_module.DaemonMissionRuntime(
            _prepared_root(tmp_path), daemon_service=service
        )
        proposal = _proposal()
        await runtime.start()
        try:
            await _execute_queued(
                runtime, service, "mission.propose", _propose_params(proposal)
            )
            duplicate = asyncio.create_task(
                runtime.handle_rpc(
                    "mission.propose",
                    _propose_params(
                        proposal, command_id="cmd_duplicate", revision=1
                    ),
                )
            )
            await asyncio.sleep(0)
            await service.tick()
            with pytest.raises(
                runtime_module.MissionRuntimeError, match="mutation failed"
            ) as failure:
                await duplicate
            assert "mission version conflict" not in str(failure.value)
            assert str(tmp_path) not in str(failure.value)
        finally:
            await runtime.close()

    asyncio.run(case())


@pytest.mark.parametrize(
    ("method", "params"),
    (
        ("mission.status", {"mission_id": "mis_1"}),
        ("events.after", {"cursor": 0, "limit": 10}),
    ),
)
@pytest.mark.parametrize(
    "failure_type",
    (
        sqlite3.OperationalError,
        SQLiteStoreError,
        RuntimeError,
    ),
)
def test_read_observation_failures_are_fixed_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    params: dict[str, object],
    failure_type: type[Exception],
) -> None:
    async def case() -> None:
        runtime_module = importlib.import_module("agentdeck.daemon.mission_runtime")
        runtime = runtime_module.DaemonMissionRuntime(
            _prepared_root(tmp_path), daemon_service=_daemon_service()
        )
        await runtime.start()
        try:
            def fail_reader(_store: SQLiteMissionStore) -> object:
                raise failure_type("private SQL SELECT /secret/project")

            monkeypatch.setattr(SQLiteMissionStore, "open_reader", fail_reader)
            with pytest.raises(
                runtime_module.MissionRuntimeError,
                match="^Mission observation is unavailable$",
            ) as observed:
                await runtime.handle_rpc(method, params)
            assert "/secret/project" not in str(observed.value)
            assert "SELECT" not in str(observed.value)
        finally:
            await runtime.close()

    asyncio.run(case())
