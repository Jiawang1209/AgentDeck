"""Same-loop ownership of the exact foreground ACP Worker binding."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from agentdeck.application import execution_records as _records
from agentdeck.application.execution_resume import ExecutionResult
from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.execution import Attempt
from agentdeck.kernel.mission import ConfirmedMissionVersion, TaskDefinition
from agentdeck.kernel.session import ExitAttemptSnapshot
from agentdeck.ports.clock import Clock
from agentdeck.ports.worker import Worker, WorkerHandle
from agentdeck.ports.worker import TaskRequest


_MAX_MISSION_BINDINGS = 64
_WORKER_METHODS = (
    "start_task",
    "stream_events",
    "respond_permission",
    "cancel_task",
    "collect_result",
)


@dataclass(frozen=True)
class ActiveExecutionBinding:
    attempt_id: str
    task_id: str
    agent_instance_id: str
    acp_session_id: str
    worker_handle: WorkerHandle
    worker: Worker


@dataclass(frozen=True)
class ExecutionReservation:
    attempt_id: str
    task_id: str
    agent_instance_id: str
    worker: Worker


@dataclass(frozen=True)
class ExecutionRuntimeStatus:
    state: str
    attempt_id: str | None
    task_id: str | None
    agent_instance_id: str | None
    has_handle: bool

    def __post_init__(self) -> None:
        if self.state not in {"idle", "reserved", "active", "quarantined"}:
            raise ValueError("execution runtime status state is invalid")
        identities = (self.attempt_id, self.task_id, self.agent_instance_id)
        if self.state == "idle" and identities != (None, None, None):
            raise ValueError("idle execution runtime status has lineage")
        if self.state != "idle" and any(value is None for value in identities):
            raise ValueError("owned execution runtime status lacks lineage")
        if self.state != "idle" and not all(
            _typed_identity(value, prefix) for value, prefix in zip(
                identities, ("att_", "tsk_", "agt_"), strict=True)
        ):
            raise ValueError("execution runtime status lineage is invalid")
        if type(self.has_handle) is not bool:
            raise TypeError("execution runtime has_handle must be an exact bool")
        if self.state == "idle" and self.has_handle:
            raise ValueError("idle execution runtime status cannot have a handle")


class ExecutionBindingError(RuntimeError):
    """Raised when foreground Worker identity is absent, reused, or drifted."""


def execution_diagnostic(
    clock: Clock, code: str, confirmed: ConfirmedMissionVersion,
    task: TaskDefinition | None, attempt: Attempt, cause: str,
) -> Diagnostic:
    outcome_known = attempt.state.value in {
        "completed", "failed", "cancelled", "interrupted",
    }
    return Diagnostic.create(
        code=code, stage="execution", severity=Severity.ERROR,
        actor="agentdeck", summary="automatic execution stopped", cause=cause,
        impact="the dependent Task was not started",
        protection="durable dependency and handoff authority remained closed",
        recovery_actions=(
            ("inspect and reconcile the durable Attempt before any new action",)
            if not outcome_known
            else ("inspect the durable Attempt before an explicit new action",)
        ),
        retryable=False, outcome_known=outcome_known,
        occurred_at=clock.now().isoformat(), mission_id=confirmed.mission_id,
        task_id=None if task is None else task.task_id,
        attempt_id=attempt.attempt_id,
    )


async def reject_reserved_worker(
    runtime: "ForegroundExecutionRuntime",
    reservation: ExecutionReservation,
    handle: object,
) -> bool:
    if type(handle) is not WorkerHandle:
        runtime.quarantine(reservation)
        return False
    try:
        await reservation.worker.cancel_task(
            handle, reason="execution_binding_rejected"
        )
    except Exception:
        runtime.quarantine(reservation)
        return False
    return True


async def start_reserved_worker(
    runtime: "ForegroundExecutionRuntime",
    reservation: ExecutionReservation,
    request: TaskRequest,
) -> ActiveExecutionBinding | None:
    handle = await reservation.worker.start_task(request)
    try:
        return runtime.claim_handle(reservation, handle)
    except Exception:
        await reject_reserved_worker(runtime, reservation, handle)
        return None


def retry_execution_attempt(
    *, runtime: "ForegroundExecutionRuntime", persist: Callable,
    policy, condition, attempt, task, confirmed, attempts,
    retry_ordinal, can_retry, acp_session_id, worker_handle=None,
    reservation=None,
) -> bool:
    decision = policy.decision(condition, ordinal=retry_ordinal)
    if not decision.retry or not can_retry:
        return False
    reason = (
        "recoverable_transport_interruption"
        if condition == "transport_before_effect" else "worker_schema_invalid"
    )
    terminal = attempt.fail(reason, retryable=True)
    try:
        terminal = persist(terminal, task, confirmed, acp_session_id)
    except Exception:
        if reservation is not None:
            runtime.quarantine(reservation)
        return False
    attempts[-1] = terminal
    if reservation is not None:
        try:
            runtime.rollback(reservation)
        except ExecutionBindingError:
            pass
    if worker_handle is not None:
        runtime.release(attempt.attempt_id, worker_handle)
    return True


def stop_execution_attempt(
    *, runtime: "ForegroundExecutionRuntime", persist: Callable,
    diagnostic: Callable, confirmed, attempts, evidence, handoffs,
    revision_task, task, terminal, code, cause, acp_session_id=None,
    worker_handle=None, reservation=None,
) -> ExecutionResult:
    try:
        terminal = persist(terminal, task, confirmed, acp_session_id)
    except Exception:
        if reservation is not None:
            runtime.quarantine(reservation)
        failure = diagnostic(
            "terminal_attempt_persistence_failed", confirmed, task,
            attempts[-1], "the safe terminal Attempt did not commit",
        )
        return ExecutionResult(
            tuple(attempts), tuple(evidence), tuple(handoffs),
            revision_task, failure,
        )
    attempts[-1] = terminal
    if reservation is not None:
        try:
            runtime.rollback(reservation)
        except ExecutionBindingError:
            pass
    if worker_handle is not None:
        runtime.release(terminal.attempt_id, worker_handle)
    return ExecutionResult(
        tuple(attempts), tuple(evidence), tuple(handoffs), revision_task,
        diagnostic(code, confirmed, task, terminal, cause),
    )


def worker_failure_result(
    stop: Callable, confirmed, attempts, evidence, handoffs, revision_task,
    task, attempt, result, worker_handle,
) -> ExecutionResult:
    condition = _records.worker_failure_condition(result)
    terminal = (
        attempt.cancel("worker_cancelled")
        if result.status == "cancelled"
        else attempt.fail(condition or "worker_failed", retryable=False)
    )
    return stop(
        confirmed, attempts, evidence, handoffs, revision_task, task, terminal,
        condition or "worker_stage_failed",
        "ACP Worker returned a non-completed terminal result",
        acp_session_id=worker_handle.session_id, worker_handle=worker_handle,
    )


def _typed_identity(value: object, prefix: str) -> bool:
    return (
        type(value) is str
        and value.startswith(prefix)
        and bool(value.removeprefix(prefix))
        and not any(character.isspace() for character in value)
    )


def _validate_binding(binding: object) -> ActiveExecutionBinding:
    if type(binding) is not ActiveExecutionBinding:
        raise ExecutionBindingError("execution binding is invalid")
    handle = binding.worker_handle
    if (
        not _typed_identity(binding.attempt_id, "att_")
        or not _typed_identity(binding.task_id, "tsk_")
        or not _typed_identity(binding.agent_instance_id, "agt_")
        or not _typed_identity(binding.acp_session_id, "ses_")
        or type(handle) is not WorkerHandle
        or (
            handle.attempt_id,
            handle.task_id,
            handle.agent_id,
            handle.session_id,
            handle.transport,
        )
        != (
            binding.attempt_id,
            binding.task_id,
            binding.agent_instance_id,
            binding.acp_session_id,
            "acp",
        )
        or any(not callable(getattr(binding.worker, name, None)) for name in _WORKER_METHODS)
    ):
        raise ExecutionBindingError("execution binding lineage is invalid")
    return binding


def _validate_reservation(reservation: object) -> ExecutionReservation:
    if type(reservation) is not ExecutionReservation or (
        not _typed_identity(reservation.attempt_id, "att_")
        or not _typed_identity(reservation.task_id, "tsk_")
        or not _typed_identity(reservation.agent_instance_id, "agt_")
    ):
        raise ExecutionBindingError("execution reservation lineage is invalid")
    return reservation


def _matches(binding: ActiveExecutionBinding, snapshot: object) -> bool:
    return type(snapshot) is ExitAttemptSnapshot and (
        binding.attempt_id,
        binding.task_id,
        binding.agent_instance_id,
        binding.acp_session_id,
        binding.worker_handle.attempt_id,
        binding.worker_handle.task_id,
        binding.worker_handle.agent_id,
        binding.worker_handle.session_id,
        binding.worker_handle.transport,
    ) == (
        snapshot.attempt_id,
        snapshot.task_id,
        snapshot.agent_instance_id,
        snapshot.acp_session_id,
        snapshot.attempt_id,
        snapshot.task_id,
        snapshot.agent_instance_id,
        snapshot.acp_session_id,
        "acp",
    )


class ForegroundExecutionRuntime:
    """Mission-local registry for one exact Worker on one foreground loop."""

    def __init__(self) -> None:
        self._binding: ActiveExecutionBinding | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._released: tuple[str, WorkerHandle] | None = None
        self._used_worker_ids: set[int] = set()
        self._used_handles: list[WorkerHandle] = []
        self._reservation: ExecutionReservation | None = None
        self._reservation_handle: WorkerHandle | None = None
        self._quarantined = False

    def is_empty(self) -> bool:
        return (
            self._binding is None
            and self._loop is None
            and self._released is None
            and not self._used_worker_ids
            and not self._used_handles
            and self._reservation is None
            and self._quarantined is False
        )

    def status(self) -> ExecutionRuntimeStatus:
        if self._reservation is not None:
            state = "quarantined" if self._quarantined else "reserved"
            item = self._reservation
            return ExecutionRuntimeStatus(
                state, item.attempt_id, item.task_id, item.agent_instance_id,
                self._reservation_handle is not None,
            )
        if self._binding is not None:
            item = self._binding
            return ExecutionRuntimeStatus(
                "active", item.attempt_id, item.task_id,
                item.agent_instance_id, True,
            )
        return ExecutionRuntimeStatus("idle", None, None, None, False)

    def reserve(self, reservation: ExecutionReservation) -> None:
        loop = asyncio.get_running_loop()
        reservation = _validate_reservation(reservation)
        if self._binding is not None or self._reservation is not None:
            raise ExecutionBindingError("execution runtime owner is not available")
        if self._loop is not None and self._loop is not loop:
            raise ExecutionBindingError("execution loop identity drifted")
        if id(reservation.worker) in self._used_worker_ids:
            raise ExecutionBindingError("execution Worker identity was reused")
        if self._released is not None and self._released[0] == reservation.attempt_id:
            raise ExecutionBindingError("released attempt cannot be reserved")
        if len(self._used_worker_ids) >= _MAX_MISSION_BINDINGS:
            raise ExecutionBindingError("execution binding budget was exhausted")
        self._loop = loop
        self._released = None
        self._used_worker_ids.add(id(reservation.worker))
        self._reservation = reservation

    def claim_handle(
        self, reservation: ExecutionReservation, handle: object,
    ) -> ActiveExecutionBinding:
        self._require_reservation(reservation)
        if self._quarantined or self._reservation_handle is not None:
            raise ExecutionBindingError("execution reservation cannot claim a handle")
        if type(handle) is not WorkerHandle:
            raise ExecutionBindingError("execution Worker handle is invalid")
        self._reservation_handle = handle
        binding = _validate_binding(ActiveExecutionBinding(
            reservation.attempt_id, reservation.task_id,
            reservation.agent_instance_id, handle.session_id, handle,
            reservation.worker,
        ))
        if handle in self._used_handles:
            raise ExecutionBindingError("execution Worker handle was reused")
        self._used_handles.append(handle)
        return binding

    def activate(
        self, reservation: ExecutionReservation,
        binding: ActiveExecutionBinding,
    ) -> None:
        self._require_reservation(reservation)
        binding = _validate_binding(binding)
        if self._quarantined or (
            binding.worker is not reservation.worker
            or binding.worker_handle != self._reservation_handle
            or binding.worker_handle not in self._used_handles
            or (binding.attempt_id, binding.task_id, binding.agent_instance_id)
            != (reservation.attempt_id, reservation.task_id,
                reservation.agent_instance_id)
        ):
            raise ExecutionBindingError("execution reservation activation drifted")
        self._binding = binding
        self._reservation = None
        self._reservation_handle = None

    def rollback(self, reservation: ExecutionReservation) -> None:
        self._require_reservation(reservation)
        if self._quarantined:
            raise ExecutionBindingError("quarantined owner cannot be rolled back")
        self._reservation = None
        self._reservation_handle = None

    def quarantine(self, reservation: ExecutionReservation) -> None:
        self._require_reservation(reservation)
        self._quarantined = True

    def bind(self, binding: ActiveExecutionBinding) -> None:
        loop = asyncio.get_running_loop()
        binding = _validate_binding(binding)
        if self._binding is not None or self._reservation is not None:
            raise ExecutionBindingError("execution binding is not available")
        if self._loop is not None and self._loop is not loop:
            raise ExecutionBindingError("execution loop identity drifted")
        if (
            id(binding.worker) in self._used_worker_ids
            or binding.worker_handle in self._used_handles
        ):
            raise ExecutionBindingError("execution binding identity was reused")
        if self._released is not None:
            if self._released[0] == binding.attempt_id:
                raise ExecutionBindingError("released attempt cannot be rebound")
            self._released = None
        if len(self._used_worker_ids) >= _MAX_MISSION_BINDINGS:
            raise ExecutionBindingError("execution binding budget was exhausted")
        self._loop = loop
        self._used_worker_ids.add(id(binding.worker))
        self._used_handles.append(binding.worker_handle)
        self._binding = binding

    def resolve_exact(
        self, snapshot: ExitAttemptSnapshot
    ) -> ActiveExecutionBinding:
        self._require_loop()
        binding = self._binding
        if binding is None or not _matches(binding, snapshot):
            raise ExecutionBindingError("exact execution binding is unavailable")
        return binding

    def release(self, attempt_id: str, worker_handle: WorkerHandle) -> None:
        self._require_loop()
        if self._reservation is not None:
            raise ExecutionBindingError("reserved execution owner cannot be released")
        pair = (attempt_id, worker_handle)
        if self._binding is None:
            if self._released == pair:
                return
            raise ExecutionBindingError("execution release lineage drifted")
        if pair != (self._binding.attempt_id, self._binding.worker_handle):
            raise ExecutionBindingError("execution release lineage drifted")
        self._binding = None
        self._released = pair

    def _require_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise ExecutionBindingError("execution loop is unavailable") from None
        if self._loop is None or self._loop is not loop:
            raise ExecutionBindingError("execution loop identity drifted")

    def _require_reservation(self, reservation: object) -> None:
        self._require_loop()
        if type(reservation) is not ExecutionReservation or (
            self._reservation is not reservation
        ):
            raise ExecutionBindingError("exact execution reservation is unavailable")


__all__ = [
    "ActiveExecutionBinding",
    "ExecutionBindingError",
    "ExecutionReservation",
    "ExecutionRuntimeStatus",
    "ForegroundExecutionRuntime",
    "execution_diagnostic",
    "reject_reserved_worker",
    "retry_execution_attempt",
    "start_reserved_worker",
    "stop_execution_attempt",
    "worker_failure_result",
]
