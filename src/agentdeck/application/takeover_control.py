"""Explicit human ownership and validated automatic-control return authority."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from agentdeck.application import execution_authority as _authority
from agentdeck.application import execution_records as _records
from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.events import DomainEvent
from agentdeck.kernel.execution import Attempt, AttemptState
from agentdeck.kernel.mission import ConfirmedMissionVersion, TaskDefinition
from agentdeck.kernel.permissions import PermissionScope
from agentdeck.kernel.session import ExitAttemptSnapshot
from agentdeck.ports.clock import Clock
from agentdeck.ports.store import Store
from agentdeck.ports.worker import Worker, WorkerHandle


_HEX = frozenset("0123456789abcdef")


def _identity(value: object, prefix: str) -> bool:
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        return False
    return bool(
        value.startswith(prefix)
        and value.removeprefix(prefix)
        and len(encoded) <= 255
        and not any(character.isspace() for character in value)
    )


@dataclass(frozen=True)
class TakeoverCursor:
    session_id: str
    agent_id: str
    task_id: str
    attempt_id: str
    transport: str
    sequence: int
    event_id: str
    fingerprint: str

    def __post_init__(self) -> None:
        for value, prefix in (
            (self.session_id, "ses_"), (self.agent_id, "agt_"),
            (self.task_id, "tsk_"), (self.attempt_id, "att_"),
            (self.event_id, "evt_"),
        ):
            if not _identity(value, prefix):
                raise ValueError("takeover cursor identity is invalid")
        if self.transport != "acp":
            raise ValueError("takeover cursor transport must be acp")
        if type(self.sequence) is not int or not 1 <= self.sequence < 2**63:
            raise ValueError("takeover cursor sequence is invalid")
        if type(self.fingerprint) is not str or len(self.fingerprint) != 64 or any(
            character not in _HEX for character in self.fingerprint
        ):
            raise ValueError("takeover cursor fingerprint is invalid")


@dataclass(frozen=True)
class TakeoverResult:
    accepted: bool
    diagnostic: Diagnostic | None = None


@dataclass(frozen=True)
class _Baseline:
    project_evidence_identity: str
    permission: PermissionScope
    cursor: TakeoverCursor | None
    human_attempt: dict[str, object]


@dataclass
class _ActiveAuthority:
    product_session_id: str
    confirmed: ConfirmedMissionVersion
    task: TaskDefinition
    attempt: Attempt
    permission: PermissionScope
    acp_session_id: str
    baseline: _Baseline | None = None
    transition_consumed: bool = False


class _SourceFailure(RuntimeError):
    pass


class _ControlledWorker:
    """Worker facade that gates only automatic ACP permission input."""
    def __init__(
        self, control: "TakeoverControl", worker: Worker, handle: WorkerHandle,
    ) -> None:
        self._control = control
        self._worker = worker
        self._handle = handle
    async def start_task(self, _request):
        raise RuntimeError("controlled Worker cannot start another task")
    def stream_events(self, handle):
        return self._worker.stream_events(handle)
    async def respond_permission(self, handle, **response) -> None:
        await self._control.wait_for_automatic_input(self._handle.attempt_id)
        await self._worker.respond_permission(handle, **response)
    async def cancel_task(self, handle, **request) -> None:
        await self._worker.cancel_task(handle, **request)
    async def collect_result(self, handle):
        await self._control.wait_for_automatic_input(self._handle.attempt_id)
        return await self._worker.collect_result(handle)


class TakeoverControl:
    """One foreground writer's exact Attempt ownership gate."""

    def __init__(
        self, *, store: Store, clock: Clock, runtime: object,
        project_evidence_identity: Callable[[], str] | None = None,
        permission_snapshot: Callable[[], PermissionScope] | None = None,
        observer_cursor: Callable[[], TakeoverCursor | None] | None = None,
    ) -> None:
        sources = (
            project_evidence_identity, permission_snapshot, observer_cursor,
        )
        if any(source is not None and not callable(source) for source in sources):
            raise TypeError("takeover evidence sources must be callable or None")
        self._store = store
        self._clock = clock
        self._runtime = runtime
        self._project_source = project_evidence_identity
        self._permission_source = permission_snapshot
        self._cursor_source = observer_cursor
        self._gate = asyncio.Event()
        self._gate.set()
        self._active: _ActiveAuthority | None = None

    @property
    def automatic_input_enabled(self) -> bool:
        return self._gate.is_set()

    def arm(
        self, *, product_session_id: str, confirmed: ConfirmedMissionVersion,
        task: TaskDefinition, attempt: Attempt, permission: PermissionScope,
        acp_session_id: str,
    ) -> None:
        if (
            not _identity(product_session_id, "ses_")
            or type(confirmed) is not ConfirmedMissionVersion
            or type(task) is not TaskDefinition
            or type(attempt) is not Attempt
            or attempt.state is not AttemptState.RUNNING
            or type(permission) is not PermissionScope
            or not _identity(acp_session_id, "ses_")
        ):
            raise ValueError("takeover authority is invalid")
        previous = self._active
        if previous is not None and (
            previous.baseline is not None or self._runtime_matches(previous)
        ):
            raise ValueError("takeover authority is already armed")
        active = _ActiveAuthority(
            product_session_id, confirmed, task, attempt, permission,
            acp_session_id,
        )
        if not self._runtime_matches(active):
            raise ValueError("takeover runtime binding is invalid")
        self._active = active
        self._gate.set()

    def controlled_worker(
        self, worker: Worker, handle: WorkerHandle,
    ) -> Worker:
        active = self._active
        binding = None if active is None else self._binding(active)
        if (
            active is None or binding is None or type(handle) is not WorkerHandle
            or binding.worker is not worker or binding.worker_handle != handle
        ):
            raise ValueError("controlled Worker lineage is invalid")
        return _ControlledWorker(self, worker, handle)

    def disarm(self, attempt_id: str) -> None:
        active = self._active
        if (
            active is None or active.attempt.attempt_id != attempt_id
            or active.baseline is not None
        ):
            raise ValueError("takeover authority cannot be disarmed")
        self._active = None
        self._gate.set()

    async def wait_for_automatic_input(self, attempt_id: str) -> None:
        while True:
            await self._gate.wait()
            active = self._active
            if active is None or active.attempt.attempt_id != attempt_id:
                raise RuntimeError("automatic input authority is stale")
            if active.baseline is None:
                return
    async def takeover(self, attempt_id: str) -> TakeoverResult:
        if not _identity(attempt_id, "att_"):
            return self._reject("takeover_identity_invalid", attempt_id)
        active = self._active
        if active is None or active.attempt.attempt_id != attempt_id:
            return self._reject("takeover_attempt_not_current", attempt_id, active)
        if active.baseline is not None:
            unchanged = self._store.load_aggregate(
                "attempts", attempt_id) == active.baseline.human_attempt
            return TakeoverResult(True) if unchanged else self._reject(
                "takeover_attempt_drift", attempt_id, active)
        running = self._attempt_facts(active, human=False)
        if (
            active.transition_consumed or not self._runtime_matches(active)
            or self._store.load_aggregate("attempts", attempt_id) != running
        ):
            return self._reject("takeover_attempt_drift", attempt_id, active)
        try:
            project, permission, cursor = self._sources(active, returning=False)
        except _SourceFailure as error:
            return self._reject(str(error), attempt_id, active)
        if permission != active.permission:
            return self._reject("takeover_permission_drift", attempt_id, active)
        human = self._attempt_facts(active, human=True)
        result = {"accepted": True, "attempt_id": attempt_id,
                  "state": "human_controlled"}
        self._gate.clear()
        try:
            def commit(transaction):
                if transaction.load_aggregate("attempts", attempt_id) != running:
                    raise ValueError("Attempt drifted before takeover commit")
                transaction.save_aggregate("attempts", attempt_id, human)
                transaction.append_event(self._event(
                    "human_takeover", "human", active, project,
                    permission, cursor,
                ))
                return result
            durable = self._store.execute_once(
                self._command("takeover", active), "human_takeover", commit,
            )
            if durable != result:
                raise ValueError("takeover replay drifted")
        except Exception:
            self._gate.set()
            return self._reject("takeover_persistence_failed", attempt_id, active)
        active.baseline = _Baseline(project, permission, cursor, human)
        return TakeoverResult(True)

    async def return_control(self, attempt_id: str) -> TakeoverResult:
        if not _identity(attempt_id, "att_"):
            return self._reject("return_control_identity_invalid", attempt_id)
        active = self._active
        if active is None or active.attempt.attempt_id != attempt_id:
            return self._reject(
                "return_control_attempt_not_current", attempt_id, active,
            )
        baseline = active.baseline
        if baseline is None:
            return self._reject(
                "attempt_drift_before_return_control", attempt_id, active,
            )
        try:
            project, permission, cursor = self._sources(active, returning=True)
        except _SourceFailure as error:
            return self._reject(str(error), attempt_id, active)
        checks = (
            (project == baseline.project_evidence_identity,
             "project_drift_before_return_control"),
            (self._runtime_matches(active),
             "acp_session_drift_before_return_control"),
            (permission == baseline.permission,
             "permission_drift_before_return_control"),
            (cursor == baseline.cursor,
             "observer_cursor_drift_before_return_control"),
            (self._store.load_aggregate("attempts", attempt_id)
             == baseline.human_attempt, "attempt_drift_before_return_control"),
        )
        failed = next((code for accepted, code in checks if not accepted), None)
        if failed is not None:
            return self._reject(failed, attempt_id, active)
        running = self._attempt_facts(active, human=False)
        result = {"accepted": True, "attempt_id": attempt_id, "state": "running"}
        try:
            def commit(transaction):
                if transaction.load_aggregate(
                    "attempts", attempt_id,
                ) != baseline.human_attempt:
                    raise ValueError("Attempt drifted before return-control commit")
                transaction.save_aggregate("attempts", attempt_id, running)
                transaction.append_event(self._event(
                    "human_return_control", "agentdeck", active, project,
                    permission, cursor,
                ))
                return result
            durable = self._store.execute_once(
                self._command("return_control", active),
                "human_return_control", commit,
            )
            if durable != result:
                raise ValueError("return-control replay drifted")
        except Exception:
            return self._reject(
                "return_control_persistence_failed", attempt_id, active,
            )
        active.baseline = None
        active.transition_consumed = True
        self._gate.set()
        return TakeoverResult(True)

    def _binding(
        self, active: _ActiveAuthority,
    ) -> object | None:
        snapshot = ExitAttemptSnapshot(
            active.attempt.attempt_id, active.task.task_id,
            active.task.agent_instance_id, active.attempt.ordinal,
            active.attempt.state, active.acp_session_id, False, None,
        )
        try:
            status = self._runtime.status()
            binding = self._runtime.resolve_exact(snapshot)
        except Exception:
            return None
        if (
            status.state != "active" or status.attempt_id != active.attempt.attempt_id
            or status.task_id != active.task.task_id
            or status.agent_instance_id != active.task.agent_instance_id
            or not status.has_handle or binding.acp_session_id != active.acp_session_id
        ):
            return None
        return binding

    def _runtime_matches(self, active: _ActiveAuthority) -> bool:
        return self._binding(active) is not None

    @staticmethod
    def _attempt_facts(
        active: _ActiveAuthority, *, human: bool,
    ) -> dict[str, object]:
        attempt = active.attempt.take_human_control() if human else active.attempt
        return _authority.attempt_snapshot(
            attempt, active.task, active.acp_session_id,
        )

    def _sources(
        self, active: _ActiveAuthority, *, returning: bool,
    ) -> tuple[str, PermissionScope, TakeoverCursor | None]:
        codes = (
            ("project_drift_before_return_control",
             "permission_drift_before_return_control",
             "observer_cursor_drift_before_return_control")
            if returning else
            ("takeover_project_evidence_unavailable",
             "takeover_permission_snapshot_unavailable",
             "takeover_observer_cursor_unavailable")
        )
        if self._project_source is None:
            raise _SourceFailure(codes[0])
        try:
            project = self._project_source()
        except Exception:
            raise _SourceFailure(codes[0]) from None
        if type(project) is not str or len(project) != 64 or any(
            character not in _HEX for character in project
        ):
            raise _SourceFailure(codes[0])
        if self._permission_source is None:
            raise _SourceFailure(codes[1])
        try:
            permission = self._permission_source()
        except Exception:
            raise _SourceFailure(codes[1]) from None
        if type(permission) is not PermissionScope:
            raise _SourceFailure(codes[1])
        if self._cursor_source is None:
            raise _SourceFailure(codes[2])
        try:
            cursor = self._cursor_source()
        except Exception:
            raise _SourceFailure(codes[2]) from None
        if cursor is None or (
            type(cursor) is not TakeoverCursor
            or (cursor.session_id, cursor.agent_id, cursor.task_id,
                cursor.attempt_id, cursor.transport)
            != (active.acp_session_id, active.task.agent_instance_id,
                active.task.task_id, active.attempt.attempt_id, "acp")
        ):
            raise _SourceFailure(codes[2])
        return project, permission, cursor

    def _event(
        self, kind: str, owner: str, active: _ActiveAuthority,
        project: str, permission: PermissionScope,
        cursor: TakeoverCursor | None,
    ) -> DomainEvent:
        cursor_facts = {
            "observer_session_id": None if cursor is None else cursor.session_id,
            "observer_agent_id": None if cursor is None else cursor.agent_id,
            "observer_task_id": None if cursor is None else cursor.task_id,
            "observer_attempt_id": None if cursor is None else cursor.attempt_id,
            "observer_transport": None if cursor is None else cursor.transport,
            "observer_sequence": None if cursor is None else cursor.sequence,
            "observer_event_id": None if cursor is None else cursor.event_id,
            "observer_fingerprint": None if cursor is None else cursor.fingerprint,
        }
        return DomainEvent.create(
            kind=kind, aggregate_type="attempt",
            aggregate_id=active.attempt.attempt_id,
            occurred_at=self._clock.now().isoformat(), payload={
                "product_session_id": active.product_session_id,
                "mission_id": active.confirmed.mission_id,
                "mission_version": active.confirmed.version,
                "mission_content_hash": active.confirmed.content_hash,
                "task_id": active.task.task_id,
                "attempt_id": active.attempt.attempt_id,
                "agent_instance_id": active.task.agent_instance_id,
                "acp_session_id": active.acp_session_id,
                "acp_session_state": "active", "owner": owner,
                "project_evidence_identity": project,
                "permission_profile": permission.profile.value,
                "permission_scope": sorted(
                    effect.value for effect in permission.effects
                ),
                **cursor_facts,
            },
        )

    @staticmethod
    def _command(operation: str, active: _ActiveAuthority) -> str:
        return _records.command_id(
            operation, active.confirmed, active.task, active.attempt.ordinal,
        )

    def _reject(
        self, code: str, attempt_id: object,
        active: _ActiveAuthority | None = None,
    ) -> TakeoverResult:
        return TakeoverResult(False, Diagnostic.create(
            code=code, stage="takeover", severity=Severity.ERROR,
            actor="agentdeck", summary="ownership transition was rejected",
            cause="the exact takeover authority did not revalidate",
            impact="automatic control was not changed",
            protection="human ownership and durable authority were preserved",
            recovery_actions=("inspect the exact Attempt and runtime lineage",),
            retryable=False, outcome_known=False,
            occurred_at=self._clock.now().isoformat(),
            mission_id=None if active is None else active.confirmed.mission_id,
            task_id=None if active is None else active.task.task_id,
            attempt_id=attempt_id if _identity(attempt_id, "att_") else None,
        ))


class TakeoverExecutionMixin:
    """Thin ExecutionService composition at existing Worker boundaries."""
    def configure_takeover_control(self, control: TakeoverControl) -> None:
        if type(control) is not TakeoverControl or hasattr(self, "_takeover_control"):
            raise ValueError("takeover control composition is invalid")
        self._takeover_control = control
    def _takeover_instance(self) -> TakeoverControl:
        control = getattr(self, "_takeover_control", None)
        if control is None:
            control = TakeoverControl(
                store=self._store, clock=self._clock, runtime=self._runtime,
            )
            self._takeover_control = control
        return control
    @property
    def automatic_input_enabled(self) -> bool:
        return self._takeover_instance().automatic_input_enabled
    async def takeover(self, attempt_id: str) -> TakeoverResult:
        return await self._takeover_instance().takeover(attempt_id)
    async def return_control(self, attempt_id: str) -> TakeoverResult:
        return await self._takeover_instance().return_control(attempt_id)
    def _takeover_activate(self, reservation, binding, authority) -> None:
        self._runtime.activate(reservation, binding)
        try:
            self._takeover_instance().arm(
                product_session_id=authority[0], confirmed=authority[1],
                task=authority[2], attempt=authority[3], permission=authority[4],
                acp_session_id=binding.worker_handle.session_id,
            )
        except Exception:
            self._runtime.release(authority[3].attempt_id, binding.worker_handle)
            raise
    def _takeover_worker(self, worker, handle):
        return self._takeover_instance().controlled_worker(worker, handle)
    def _takeover_release(self, attempt_id, handle) -> None:
        self._runtime.release(attempt_id, handle)
        self._takeover_instance().disarm(attempt_id)


__all__ = [
    "TakeoverControl", "TakeoverCursor", "TakeoverExecutionMixin", "TakeoverResult",
]
