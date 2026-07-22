from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from functools import wraps
from types import SimpleNamespace

import pytest

from agentdeck.application.execution_runtime import (
    ForegroundExecutionRuntime, reject_activated_worker,
)
from agentdeck.application.execution_service import ExecutionService
from agentdeck.application.project_lifecycle_service import ProjectLifecycleService
from agentdeck.application.takeover_control import TakeoverControl, TakeoverResult
from agentdeck.kernel.events import FactArray
from agentdeck.kernel.mission import MissionDraft
from agentdeck.kernel.permissions import Effect, PermissionProfile, PermissionScope
from agentdeck.kernel.session import SessionState
from agentdeck.product.shell import ProductShell
from agentdeck.product.slash_commands import parse_command
from agentdeck.ports.observer import ObserverCursor
from agentdeck.ports.project_evidence import ProjectEvidence, project_evidence_digest
from agentdeck.ports.worker import TaskRequest, WorkerHandle
from product_kernel.fakes import FrozenClock


NOW = datetime(2026, 7, 21, 4, 0, tzinfo=timezone.utc)


class _CancelSpyWorker:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[object, str]] = []
        self._fail = fail

    async def cancel_task(self, handle, reason):
        self.calls.append((handle, reason))
        if self._fail:
            raise RuntimeError("cancel failed")


def test_reject_activated_worker_cancels_bound_worker_exactly_once() -> None:
    async def scenario() -> None:
        handle = WorkerHandle("ses_1", "agt_1", "tsk_1", "att_1")
        worker = _CancelSpyWorker()
        binding = SimpleNamespace(worker=worker, worker_handle=handle)

        await reject_activated_worker(binding)

        assert worker.calls == [(handle, "execution_binding_rejected")]

    asyncio.run(scenario())


def test_reject_activated_worker_contains_a_failing_cancel() -> None:
    async def scenario() -> None:
        handle = WorkerHandle("ses_1", "agt_1", "tsk_1", "att_1")
        worker = _CancelSpyWorker(fail=True)
        binding = SimpleNamespace(worker=worker, worker_handle=handle)

        # A worker that raises during teardown must not crash the fail-closed
        # rejection path, and it must be cancelled exactly once.
        await reject_activated_worker(binding)

        assert len(worker.calls) == 1

    asyncio.run(scenario())


def project_evidence(marker: str = "a") -> ProjectEvidence:
    parts = (marker * 64,) * 5
    digest = project_evidence_digest("git-project-evidence/v1", "prj_1", *parts)
    return ProjectEvidence("git-project-evidence/v1", "prj_1", *parts, digest)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        async def invoke():
            try:
                return await function(*args, **kwargs)
            finally:
                for value in kwargs.values():
                    if type(value) is Harness:
                        await value.close()
        return asyncio.run(invoke())
    return run


class Transaction:
    def __init__(self, store: "MemoryStore") -> None:
        self.store = store
        self.pending: list[tuple[str, object]] = []
        self.duplicate_result = None

    def load_aggregate(self, kind, identity):
        return self.store.load_aggregate(kind, identity)

    def save_aggregate(self, kind, identity, snapshot) -> None:
        self.pending.append(("aggregate", (kind, identity, dict(snapshot))))

    def append_event(self, event) -> None:
        self.pending.append(("event", event))

    def commit(self) -> None:
        for kind, value in self.pending:
            if kind == "aggregate":
                aggregate, identity, snapshot = value
                self.store.aggregates[(aggregate, identity)] = snapshot
            else:
                self.store.events.append(value)


class MemoryStore:
    def __init__(self) -> None:
        self.aggregates = {
            ("product_sessions", "ses_product"): {
                "session_id": "ses_product", "state": "running",
            },
        }
        self.commands: dict[tuple[str, str], dict[str, object]] = {}
        self.events: list[object] = []

    def execute_once(self, command_id, command_kind, callback):
        key = command_id, command_kind
        if key in self.commands:
            return dict(self.commands[key])
        transaction = Transaction(self)
        result = callback(transaction)
        transaction.commit()
        self.commands[key] = dict(result)
        return dict(result)

    def lookup_command(self, command_id, command_kind=None):
        for (identity, kind), result in self.commands.items():
            if identity == command_id and (command_kind is None or kind == command_kind):
                return dict(result)
        return None

    def load_aggregate(self, kind, identity):
        value = self.aggregates.get((kind, identity))
        return None if value is None else dict(value)

    def list_active_exit_attempts(self, _session_id):
        return ()


class BlockingApprovalService:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.responded = asyncio.Event()

    async def bridge_attempt(self, worker, handle, _context):
        self.started.set()
        await self.release.wait()
        await worker.respond_permission(
            handle, permission_request_id="perm_1", allowed=True, reason="approved",
        )
        self.responded.set()
        await asyncio.Event().wait()


class BoundWorker:
    def __init__(self) -> None:
        self.handle: WorkerHandle | None = None
        self.permission_responses: list[str] = []

    async def start_task(self, request: TaskRequest) -> WorkerHandle:
        self.handle = WorkerHandle(
            "ses_acp", request.agent_id, request.task_id, request.attempt_id,
        )
        return self.handle

    def stream_events(self, _handle):
        raise AssertionError("custom bridge owns the test wait")

    async def respond_permission(self, _handle, *, permission_request_id, **_kwargs):
        self.permission_responses.append(permission_request_id)

    async def cancel_task(self, *_args, **_kwargs):
        raise AssertionError("takeover must not cancel the ACP session")

    async def collect_result(self, _handle):
        raise AssertionError("takeover must not collect a result")


class EvidenceSource:
    def __init__(self) -> None:
        self.project = project_evidence()
        self.permission = PermissionScope(
            PermissionProfile.APPROVE_FOR_ME, frozenset({Effect.READ}),
        )
        self.cursor: ObserverCursor | None = None


class Harness:
    def __init__(self, *, missing_source: str | None = None) -> None:
        self.store = MemoryStore()
        self.worker = BoundWorker()
        self.approvals = BlockingApprovalService()
        self.runtime = ForegroundExecutionRuntime()
        self.evidence = EvidenceSource()
        self.draft = MissionDraft.coding_default(
            "drf_1", "implement explicit takeover", "/project", "codex-cli",
            "gpt-test", PermissionProfile.APPROVE_FOR_ME,
        )
        preview = self.draft.preview(1)
        self.confirmed = preview.confirm(
            preview_id=preview.preview_id, content_hash=preview.content_hash,
        )
        clock = FrozenClock(NOW)
        self.service = ExecutionService(
            store=self.store, clock=clock, approval_service=self.approvals,
            worker_factory=lambda _task: self.worker, runtime=self.runtime,
            lifecycle=ProjectLifecycleService(
                store=self.store, clock=clock, session_id="ses_product",
            ),
        )
        if missing_source != "all":
            control = TakeoverControl(
                store=self.store, clock=clock, runtime=self.runtime,
                project_evidence=(
                    None if missing_source == "project" else lambda: self.evidence.project
                ),
                permission_snapshot=(
                    None if missing_source == "permission" else lambda: self.evidence.permission
                ),
                observer_cursor=(
                    (lambda: None) if missing_source == "cursor"
                    else lambda: self.evidence.cursor
                ),
            )
            self.service.configure_takeover_control(control)
        self.mission_task: asyncio.Task | None = None

    async def start(self) -> str:
        self.mission_task = asyncio.create_task(self.service.run_confirmed_mission(
            session_id="ses_product", confirmed=self.confirmed, draft=self.draft,
            permission_scope=PermissionScope(
                PermissionProfile.APPROVE_FOR_ME, frozenset({Effect.READ}),
            ),
        ))
        await self.approvals.started.wait()
        attempt_id = self.runtime.status().attempt_id
        assert attempt_id is not None and self.worker.handle is not None
        handle = self.worker.handle
        self.evidence.cursor = ObserverCursor(
            "prj_1", handle.session_id, handle.agent_id, handle.task_id, handle.attempt_id,
            handle.transport, 1, "evt_started", "b" * 64,
        )
        return attempt_id

    async def close(self) -> None:
        if self.mission_task is not None:
            self.mission_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.mission_task


@pytest.fixture
def harness():
    return Harness()


@async_test
async def test_takeover_stops_automatic_input_and_records_full_lineage(harness) -> None:
    attempt_id = await harness.start()

    result = await harness.service.takeover(attempt_id)

    assert result.accepted is True
    assert result.diagnostic is None
    assert harness.store.load_aggregate("attempts", attempt_id)["state"] == "human_controlled"
    assert harness.service.automatic_input_enabled is False
    event = harness.store.events[-1]
    assert event.kind == "human_takeover"
    payload = dict(event.payload)
    assert payload == {
        "acp_session_id": "ses_acp",
        "acp_session_state": "active",
        "agent_instance_id": "agt_implementation",
        "attempt_id": attempt_id,
        "mission_content_hash": harness.confirmed.content_hash,
        "mission_id": harness.confirmed.mission_id,
        "mission_version": 1,
        "observer_project_id": "prj_1",
        "observer_event_id": "evt_started",
        "observer_fingerprint": "b" * 64,
        "observer_session_id": "ses_acp",
        "observer_agent_id": "agt_implementation",
        "observer_task_id": "tsk_implementation",
        "observer_attempt_id": attempt_id,
        "observer_transport": "acp",
        "observer_sequence": 1,
        "owner": "human",
        "permission_profile": "approve_for_me",
        "permission_scope": FactArray(("read",)),
        "ownership_cycle_id": harness.store.load_aggregate(
            "takeover_ownership", attempt_id,
        )["cycle_id"],
        "ownership_generation": 1,
        "project_evidence_provenance": "git-project-evidence/v1",
        "project_evidence_digest": harness.evidence.project.digest,
        "product_session_id": "ses_product",
        "task_id": "tsk_implementation",
    }
@async_test
async def test_return_control_accepts_only_exact_clean_revalidation(harness) -> None:
    attempt_id = await harness.start()
    await harness.service.takeover(attempt_id)

    result = await harness.service.return_control(attempt_id)

    assert result.accepted is True
    assert result.diagnostic is None
    assert harness.store.load_aggregate("attempts", attempt_id)["state"] == "running"
    assert harness.service.automatic_input_enabled is True
    assert harness.store.events[-1].kind == "human_return_control"
@async_test
async def test_takeover_blocks_automatic_permission_input_until_valid_return(harness) -> None:
    attempt_id = await harness.start()
    await harness.service.takeover(attempt_id)

    harness.approvals.release.set()
    await asyncio.sleep(0)
    assert harness.worker.permission_responses == []
    assert not harness.approvals.responded.is_set()

    result = await harness.service.return_control(attempt_id)
    await harness.approvals.responded.wait()
    assert result.accepted is True
    assert harness.worker.permission_responses == ["perm_1"]
@async_test
async def test_project_drift_before_return_control_fails_closed(harness) -> None:
    attempt_id = await harness.start()
    await harness.service.takeover(attempt_id)
    before = len(harness.store.commands), len(harness.store.events)
    harness.evidence.project = project_evidence("c")

    result = await harness.service.return_control(attempt_id)

    assert result.accepted is False
    assert result.diagnostic.code == "project_drift_before_return_control"
    assert harness.store.load_aggregate("attempts", attempt_id)["state"] == "human_controlled"
    assert harness.service.automatic_input_enabled is False
    assert (len(harness.store.commands), len(harness.store.events)) == before


@async_test
@pytest.mark.parametrize(
    ("drift", "code"),
    (
        ("permission", "permission_drift_before_return_control"),
        ("cursor", "observer_cursor_drift_before_return_control"),
        ("attempt", "attempt_drift_before_return_control"),
        ("acp", "acp_session_drift_before_return_control"),
    ),
)
async def test_each_return_control_authority_drift_retains_human_owner(
    harness, drift: str, code: str,
) -> None:
    attempt_id = await harness.start()
    await harness.service.takeover(attempt_id)
    before = len(harness.store.commands), len(harness.store.events)
    if drift == "permission":
        harness.evidence.permission = PermissionScope(
            PermissionProfile.ASK_FOR_APPROVAL, frozenset({Effect.READ}),
        )
    elif drift == "cursor":
        harness.evidence.cursor = ObserverCursor(
            "prj_1", "ses_acp", "agt_implementation", "tsk_implementation", attempt_id,
            "acp", 2, "evt_progress", "d" * 64,
        )
    elif drift == "attempt":
        harness.store.aggregates[("attempts", attempt_id)]["ordinal"] = 2
    else:
        harness.runtime.release(attempt_id, harness.worker.handle)

    result = await harness.service.return_control(attempt_id)

    assert result.accepted is False
    assert result.diagnostic.code == code
    assert harness.store.load_aggregate("attempts", attempt_id)["state"] == "human_controlled"
    assert harness.service.automatic_input_enabled is False
    assert (len(harness.store.commands), len(harness.store.events)) == before


@async_test
@pytest.mark.parametrize("attempt_id", ("att_foreign", "bad", "att_\ud800"))
async def test_foreign_or_malformed_takeover_has_zero_side_effects(harness, attempt_id) -> None:
    current = await harness.start()
    before = (
        harness.store.load_aggregate("attempts", current),
        len(harness.store.commands), len(harness.store.events),
        harness.runtime.status(),
    )

    result = await harness.service.takeover(attempt_id)

    assert result.accepted is False
    assert result.diagnostic.code in {"takeover_identity_invalid", "takeover_attempt_not_current"}
    assert (
        harness.store.load_aggregate("attempts", current),
        len(harness.store.commands), len(harness.store.events),
        harness.runtime.status(),
    ) == before
    assert harness.service.automatic_input_enabled is True


@async_test
async def test_terminal_or_repeated_takeover_fails_without_new_effects(harness) -> None:
    attempt_id = await harness.start()
    harness.store.aggregates[("attempts", attempt_id)]["state"] = "completed"
    before = len(harness.store.commands), len(harness.store.events), harness.runtime.status()

    terminal = await harness.service.takeover(attempt_id)

    assert terminal.accepted is False
    assert terminal.diagnostic.code == "takeover_attempt_drift"
    assert (len(harness.store.commands), len(harness.store.events), harness.runtime.status()) == before
    harness.store.aggregates[("attempts", attempt_id)]["state"] = "running"
    assert (await harness.service.takeover(attempt_id)).accepted is True
    before = len(harness.store.commands), len(harness.store.events)

    repeated = await harness.service.takeover(attempt_id)

    assert repeated.accepted is True
    assert repeated.diagnostic is None
    assert (len(harness.store.commands), len(harness.store.events)) == before
    assert harness.service.automatic_input_enabled is False
    assert (await harness.service.return_control(attempt_id)).accepted is True


@async_test
async def test_foreign_return_control_retains_exact_human_attempt(harness) -> None:
    attempt_id = await harness.start()
    await harness.service.takeover(attempt_id)
    before = (
        harness.store.load_aggregate("attempts", attempt_id),
        len(harness.store.commands), len(harness.store.events), harness.runtime.status(),
    )

    result = await harness.service.return_control("att_foreign")

    assert result.accepted is False
    assert result.diagnostic.code == "return_control_attempt_not_current"
    assert (
        harness.store.load_aggregate("attempts", attempt_id),
        len(harness.store.commands), len(harness.store.events), harness.runtime.status(),
    ) == before
    assert harness.service.automatic_input_enabled is False


@async_test
@pytest.mark.parametrize(
    ("missing_source", "code"),
    (
        ("all", "takeover_project_evidence_unavailable"),
        ("project", "takeover_project_evidence_unavailable"),
        ("permission", "takeover_permission_snapshot_unavailable"),
        ("cursor", "takeover_observer_cursor_unavailable"),
    ),
)
async def test_missing_return_proof_rejects_takeover_without_effects(
    missing_source: str, code: str,
) -> None:
    harness = Harness(missing_source=missing_source)
    try:
        attempt_id = await harness.start()
        before = (
            harness.store.load_aggregate("attempts", attempt_id),
            len(harness.store.commands), len(harness.store.events),
            harness.runtime.status(),
        )

        result = await harness.service.takeover(attempt_id)

        assert result.accepted is False
        assert result.diagnostic.code == code
        assert (
            harness.store.load_aggregate("attempts", attempt_id),
            len(harness.store.commands), len(harness.store.events),
            harness.runtime.status(),
        ) == before
        assert harness.service.automatic_input_enabled is True
    finally:
        await harness.close()


def test_tmux_adapter_remains_selection_only_without_return_authority() -> None:
    from agentdeck.adapters.tmux_observer import TmuxObserver

    assert "return_control" not in TmuxObserver.__dict__
    assert "return_ownership" not in TmuxObserver.__dict__
    source = __import__("inspect").getsource(TmuxObserver)
    assert all(token not in source for token in ("save_aggregate", "append_event", "send-keys"))


@async_test
async def test_product_shell_routes_takeover_to_application_authority() -> None:
    calls: list[str] = []
    output: list[str] = []
    view = SimpleNamespace(
        leader_backend="codex-cli", model="gpt-test", permission="approve_for_me",
        state=SessionState.RUNNING,
    )
    session = SimpleNamespace(
        accept_text=lambda *_: None, configure=lambda **_: None,
        resume=lambda: view, current=lambda: view,
    )
    execution = SimpleNamespace(
        run_confirmed_mission=lambda **_: None,
        takeover=lambda attempt_id: _takeover_call(calls, attempt_id),
    )
    shell = ProductShell(
        session_service=session,
        exit_coordinator=SimpleNamespace(
            request_exit=lambda: None, decline=lambda *_: None,
            confirm=lambda *_: None, input_closed=lambda: None,
        ),
        recovery_service=SimpleNamespace(reconcile=lambda: None),
        lifecycle=SimpleNamespace(resume=lambda *_: None),
        available_leaders={"codex-cli": ("gpt-test",)},
        read_line=lambda *_: None, write_line=output.append, close=lambda: None,
        execution_service=execution,
    )
    command = parse_command("/takeover att_1")
    assert command is not None

    await shell._accept_command(command)

    assert calls == ["att_1"]
    assert output == ["Human control recorded for Attempt att_1."]


async def _takeover_call(calls: list[str], attempt_id: str) -> TakeoverResult:
    calls.append(attempt_id)
    return TakeoverResult(True)
