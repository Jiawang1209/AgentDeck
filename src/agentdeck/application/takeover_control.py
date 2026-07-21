"""Durable human ownership cycles and validated automatic-control return."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType

from agentdeck.application import execution_authority as _authority
from agentdeck.application.takeover_records import (
    TakeoverBaseline, TakeoverOwnership, new_ownership,
    ownership_event, ownership_from_snapshot, ownership_snapshot,
    ownership_with_state, takeover_identity, transition_result,
)
from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.execution import Attempt, AttemptState
from agentdeck.kernel.mission import ConfirmedMissionVersion, TaskDefinition
from agentdeck.kernel.permissions import PermissionScope
from agentdeck.kernel.session import ExitAttemptSnapshot
from agentdeck.ports.clock import Clock
from agentdeck.ports.observer import ObserverCursor
from agentdeck.ports.project_evidence import ProjectEvidence
from agentdeck.ports.store import Store
from agentdeck.ports.worker import Worker, WorkerHandle


@dataclass(frozen=True)
class TakeoverResult:
    accepted: bool
    diagnostic: Diagnostic | None = None


@dataclass
class _ActiveAuthority:
    product_session_id: str
    confirmed: ConfirmedMissionVersion
    task: TaskDefinition
    attempt: Attempt
    permission: PermissionScope
    acp_session_id: str


class _SourceFailure(RuntimeError):
    pass

class _ControlledWorker:
    """Worker facade that gates automatic ACP input."""

    def __init__(self, control: "TakeoverControl", worker: Worker, handle: WorkerHandle) -> None:
        self._control, self._worker, self._handle = control, worker, handle

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
    """One foreground writer with Store-backed per-Attempt ownership."""

    def __init__(
        self, *, store: Store, clock: Clock, runtime: object,
        project_evidence: Callable[[], ProjectEvidence] | None = None,
        permission_snapshot: Callable[[], PermissionScope] | None = None,
        observer_cursor: Callable[[], ObserverCursor | None] | None = None,
    ) -> None:
        sources = (project_evidence, permission_snapshot, observer_cursor)
        if any(source is not None and not callable(source) for source in sources):
            raise TypeError("takeover evidence sources must be callable or None")
        self._store, self._clock, self._runtime = store, clock, runtime
        self._project_source = project_evidence
        self._permission_source = permission_snapshot
        self._cursor_source = observer_cursor
        self._gate = asyncio.Event()
        self._gate.set()
        self._active: _ActiveAuthority | None = None

    @property
    def automatic_input_enabled(self) -> bool:
        return self._gate.is_set()

    @property
    def sources(self) -> tuple[str, ...]:
        return (
            "typed_project_evidence", "permission_snapshot",
            "active_acp_session", "acknowledged_observer_cursor",
        )

    def arm(
        self, *, product_session_id: str, confirmed: ConfirmedMissionVersion,
        task: TaskDefinition, attempt: Attempt, permission: PermissionScope,
        acp_session_id: str,
    ) -> None:
        if (
            not takeover_identity(product_session_id, "ses_")
            or type(confirmed) is not ConfirmedMissionVersion
            or type(task) is not TaskDefinition or type(attempt) is not Attempt
            or attempt.state is not AttemptState.RUNNING
            or type(permission) is not PermissionScope
            or not takeover_identity(acp_session_id, "ses_")
        ):
            raise ValueError("takeover authority is invalid")
        previous = self._active
        if previous is not None and self._runtime_matches(previous):
            raise ValueError("takeover authority is already armed")
        active = _ActiveAuthority(
            product_session_id, confirmed, task, attempt, permission, acp_session_id,
        )
        if not self._runtime_matches(active):
            raise ValueError("takeover runtime binding is invalid")
        ownership = self._load_ownership(attempt.attempt_id)
        if ownership is not None and ownership.state in {"human", "interrupted"}:
            self._gate.clear()
        else:
            self._gate.set()
        self._active = active

    def controlled_worker(self, worker: Worker, handle: WorkerHandle) -> Worker:
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
        ownership = self._load_ownership(attempt_id)
        if (
            active is None or active.attempt.attempt_id != attempt_id
            or (ownership is not None and ownership.state == "human")
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
            ownership = self._load_ownership(attempt_id)
            if ownership is None or ownership.state in {"automatic", "returned"}:
                return

    async def takeover(self, attempt_id: str) -> TakeoverResult:
        active, rejected = self._current(attempt_id, "takeover")
        if rejected is not None:
            return rejected
        assert active is not None
        running = self._attempt_facts(active, human=False)
        ownership = self._safe_ownership(attempt_id)
        if ownership is False:
            return self._reject("takeover_attempt_drift", attempt_id, active)
        if type(ownership) is TakeoverOwnership and ownership.state == "human":
            self._gate.clear()
            return self._replay_human(active, ownership)
        if (
            type(ownership) is TakeoverOwnership
            and ownership.state not in {"automatic", "returned"}
        ) or not self._runtime_matches(active) or (
            self._store.load_aggregate("attempts", attempt_id) != running
        ):
            return self._reject("takeover_attempt_drift", attempt_id, active)
        try:
            project, permission, cursor = self._sources(active, returning=False)
        except _SourceFailure as error:
            return self._reject(str(error), attempt_id, active)
        if permission != active.permission:
            return self._reject("takeover_permission_drift", attempt_id, active)
        generation = 1 if ownership is None else ownership.generation + 1
        human = self._attempt_facts(active, human=True)
        baseline = TakeoverBaseline(
            project, permission, cursor, MappingProxyType(human),
        )
        authority = new_ownership(
            attempt_id=attempt_id, generation=generation, baseline=baseline,
        )
        result = transition_result(authority, "human_controlled")
        prior = None if ownership is None else ownership_snapshot(ownership)
        self._gate.clear()
        try:
            def commit(transaction):
                if (
                    transaction.load_aggregate("attempts", attempt_id) != running
                    or transaction.load_aggregate("takeover_ownership", attempt_id) != prior
                ):
                    raise ValueError("Attempt drifted before takeover commit")
                transaction.save_aggregate("attempts", attempt_id, human)
                transaction.save_aggregate(
                    "takeover_ownership", attempt_id, ownership_snapshot(authority),
                )
                transaction.append_event(self._event(
                    "human_takeover", "human", active, authority,
                ))
                return result
            durable = self._store.execute_once(
                authority.takeover_command_id, "human_takeover", commit,
            )
            if durable != result:
                raise ValueError("takeover replay drifted")
        except Exception:
            return self._reconcile_takeover(active, authority, prior, running, result)
        return TakeoverResult(True)

    async def return_control(self, attempt_id: str) -> TakeoverResult:
        active, rejected = self._current(attempt_id, "return_control")
        if rejected is not None:
            return rejected
        assert active is not None
        ownership = self._safe_ownership(attempt_id)
        if ownership is False or ownership is None:
            return self._reject(
                "attempt_drift_before_return_control", attempt_id, active,
            )
        if ownership.state == "returned":
            return self._replay_returned(active, ownership)
        if ownership.state != "human":
            return self._reject(
                "attempt_drift_before_return_control", attempt_id, active,
            )
        baseline = ownership.baseline
        try:
            project, permission, cursor = self._sources(active, returning=True)
        except _SourceFailure as error:
            return self._reject(str(error), attempt_id, active)
        checks = (
            (project == baseline.project_evidence, "project_drift_before_return_control"),
            (self._runtime_matches(active), "acp_session_drift_before_return_control"),
            (permission == baseline.permission, "permission_drift_before_return_control"),
            (cursor == baseline.cursor, "observer_cursor_drift_before_return_control"),
            (self._store.load_aggregate("attempts", attempt_id)
             == dict(baseline.human_attempt), "attempt_drift_before_return_control"),
        )
        failed = next((code for accepted, code in checks if not accepted), None)
        if failed is not None:
            return self._reject(failed, attempt_id, active)
        running = self._attempt_facts(active, human=False)
        returned = ownership_with_state(ownership, "returned")
        result = transition_result(returned, "running")
        try:
            def commit(transaction):
                if (
                    transaction.load_aggregate("attempts", attempt_id)
                    != dict(baseline.human_attempt)
                    or transaction.load_aggregate("takeover_ownership", attempt_id)
                    != ownership_snapshot(ownership)
                ):
                    raise ValueError("Attempt drifted before return-control commit")
                transaction.save_aggregate("attempts", attempt_id, running)
                transaction.save_aggregate(
                    "takeover_ownership", attempt_id, ownership_snapshot(returned),
                )
                transaction.append_event(self._event(
                    "human_return_control", "agentdeck", active, returned,
                ))
                return result
            durable = self._store.execute_once(
                ownership.return_command_id, "human_return_control", commit,
            )
            if durable != result:
                raise ValueError("return-control replay drifted")
        except Exception:
            return self._reconcile_return(active, ownership, returned, result)
        self._gate.set()
        return TakeoverResult(True)

    def _reconcile_takeover(self, active, authority, prior, running, result):
        try:
            command = self._store.lookup_command(
                authority.takeover_command_id, "human_takeover",
            )
            current = self._store.load_aggregate("takeover_ownership", authority.attempt_id)
            attempt = self._store.load_aggregate("attempts", authority.attempt_id)
        except Exception:
            return self._reject("takeover_outcome_unknown", authority.attempt_id, active)
        if (
            command == result and current == ownership_snapshot(authority)
            and attempt == dict(authority.baseline.human_attempt)
        ):
            return TakeoverResult(True)
        if command is None and current == prior and attempt == running:
            self._gate.set()
            return self._reject("takeover_persistence_failed", authority.attempt_id, active)
        return self._reject("takeover_outcome_unknown", authority.attempt_id, active)

    def _reconcile_return(self, active, human, returned, result):
        try:
            command = self._store.lookup_command(
                human.return_command_id, "human_return_control",
            )
            current = self._store.load_aggregate("takeover_ownership", human.attempt_id)
            attempt = self._store.load_aggregate("attempts", human.attempt_id)
        except Exception:
            return self._reject("return_control_outcome_unknown", human.attempt_id, active)
        if (
            command == result and current == ownership_snapshot(returned)
            and attempt == self._attempt_facts(active, human=False)
        ):
            self._gate.set()
            return TakeoverResult(True)
        return self._reject("return_control_persistence_failed", human.attempt_id, active)

    def _replay_human(self, active, ownership) -> TakeoverResult:
        expected = transition_result(ownership, "human_controlled")
        if (
            self._store.lookup_command(ownership.takeover_command_id, "human_takeover")
            == expected
            and self._store.load_aggregate("attempts", ownership.attempt_id)
            == dict(ownership.baseline.human_attempt)
        ):
            return TakeoverResult(True)
        return self._reject("takeover_attempt_drift", ownership.attempt_id, active)

    def _replay_returned(self, active, ownership) -> TakeoverResult:
        expected = transition_result(ownership, "running")
        if (
            self._store.lookup_command(
                ownership.return_command_id, "human_return_control",
            ) == expected
            and self._store.load_aggregate("attempts", ownership.attempt_id)
            == self._attempt_facts(active, human=False)
        ):
            self._gate.set()
            return TakeoverResult(True)
        return self._reject(
            "attempt_drift_before_return_control", ownership.attempt_id, active,
        )

    def _current(self, attempt_id: str, operation: str):
        identity_code = f"{operation}_identity_invalid"
        current_code = f"{operation}_attempt_not_current"
        if not takeover_identity(attempt_id, "att_"):
            return None, self._reject(identity_code, attempt_id)
        active = self._active
        if active is None or active.attempt.attempt_id != attempt_id:
            return active, self._reject(current_code, attempt_id, active)
        return active, None

    def _safe_ownership(self, attempt_id: str):
        try:
            return self._load_ownership(attempt_id)
        except Exception:
            return False

    def _load_ownership(self, attempt_id: str) -> TakeoverOwnership | None:
        value = self._store.load_aggregate("takeover_ownership", attempt_id)
        return None if value is None else ownership_from_snapshot(value)

    def _binding(self, active: _ActiveAuthority) -> object | None:
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
    def _attempt_facts(active: _ActiveAuthority, *, human: bool) -> dict[str, object]:
        attempt = active.attempt.take_human_control() if human else active.attempt
        return _authority.attempt_snapshot(attempt, active.task, active.acp_session_id)

    def _sources(self, active: _ActiveAuthority, *, returning: bool):
        codes = (
            ("project_drift_before_return_control", "permission_drift_before_return_control",
             "observer_cursor_drift_before_return_control")
            if returning else
            ("takeover_project_evidence_unavailable", "takeover_permission_snapshot_unavailable",
             "takeover_observer_cursor_unavailable")
        )
        try:
            project = None if self._project_source is None else self._project_source()
        except Exception:
            project = None
        if type(project) is not ProjectEvidence:
            raise _SourceFailure(codes[0])
        try:
            permission = None if self._permission_source is None else self._permission_source()
        except Exception:
            permission = None
        if type(permission) is not PermissionScope:
            raise _SourceFailure(codes[1])
        try:
            cursor = None if self._cursor_source is None else self._cursor_source()
        except Exception:
            cursor = None
        if cursor is None or (
            type(cursor) is not ObserverCursor or cursor.project_id != project.project_id
            or (cursor.session_id, cursor.agent_id, cursor.task_id,
                cursor.attempt_id, cursor.transport)
            != (active.acp_session_id, active.task.agent_instance_id,
                active.task.task_id, active.attempt.attempt_id, "acp")
        ):
            raise _SourceFailure(codes[2])
        return project, permission, cursor

    def _event(self, kind, owner, active, ownership):
        return ownership_event(
            kind=kind, owner=owner, occurred_at=self._clock.now().isoformat(),
            product_session_id=active.product_session_id,
            confirmed=active.confirmed, task=active.task,
            acp_session_id=active.acp_session_id, ownership=ownership,
        )

    def _reject(self, code, attempt_id, active=None) -> TakeoverResult:
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
            attempt_id=attempt_id if takeover_identity(attempt_id, "att_") else None,
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
            control = TakeoverControl(store=self._store, clock=self._clock, runtime=self._runtime)
            self._takeover_control = control
        return control

    @property
    def takeover_control(self) -> TakeoverControl:
        return self._takeover_instance()

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

__all__ = ["TakeoverControl", "TakeoverExecutionMixin", "TakeoverResult"]
