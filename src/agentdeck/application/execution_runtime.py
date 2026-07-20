"""Same-loop ownership of the exact foreground ACP Worker binding."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agentdeck.kernel.session import ExitAttemptSnapshot
from agentdeck.ports.worker import Worker, WorkerHandle


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


class ExecutionBindingError(RuntimeError):
    """Raised when foreground Worker identity is absent, reused, or drifted."""


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

    def is_empty(self) -> bool:
        return (
            self._binding is None
            and self._loop is None
            and self._released is None
            and not self._used_worker_ids
            and not self._used_handles
        )

    def bind(self, binding: ActiveExecutionBinding) -> None:
        loop = asyncio.get_running_loop()
        binding = _validate_binding(binding)
        if self._binding is not None:
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


__all__ = [
    "ActiveExecutionBinding",
    "ExecutionBindingError",
    "ForegroundExecutionRuntime",
]
