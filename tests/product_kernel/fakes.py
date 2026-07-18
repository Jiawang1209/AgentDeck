from dataclasses import dataclass
from datetime import datetime

from agentdeck.ports.worker import (
    TaskRequest, WorkerEvent, WorkerHandle, WorkerResult, validate_worker_reason,
)


@dataclass
class FrozenClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


class FakeWorker:
    """Deterministic Worker Port fake with no Store capability."""

    def __init__(self) -> None:
        self._handle: WorkerHandle | None = None
        self._events: tuple[WorkerEvent, ...] = ()
        self._result: WorkerResult | None = None
        self._permission_responses: list[tuple[str, str, bool, str]] = []
        self._phase = "new"
        self._cursor = 0
        self._last_sequence = 0
        self._stream_started = False
        self._pending_permission: str | None = None
        self._cancel_event: WorkerEvent | None = None

    @classmethod
    def successful(cls) -> "FakeWorker":
        return cls()

    @property
    def permission_responses(self) -> tuple[tuple[str, str, bool, str], ...]:
        return tuple(self._permission_responses)

    async def start_task(self, request: TaskRequest) -> WorkerHandle:
        if type(request) is not TaskRequest or self._handle is not None:
            raise ValueError("FakeWorker task start invalid")
        handle = WorkerHandle(
            session_id="ses_1",
            agent_id=request.agent_id,
            task_id=request.task_id,
            attempt_id=request.attempt_id,
        )
        self._handle = handle
        self._events = tuple(
            _worker_event(handle, sequence, kind, payload)
            for sequence, kind, payload in (
                (1, "started", {}),
                (2, "permission_requested", {"permission_request_id": "perm_1"}),
                (3, "progress", {"summary": "working"}),
                (4, "completed", {"summary": "done"}),
            )
        )
        self._phase = "running"
        return handle

    def stream_events(self, handle: WorkerHandle):
        self._require_handle(handle)
        if self._stream_started:
            raise ValueError("FakeWorker event stream already consumed")
        self._stream_started = True
        return _FakeEventStream(self, handle)

    def _next_event(self, handle: WorkerHandle) -> WorkerEvent:
        self._require_handle(handle)
        if self._pending_permission is not None:
            raise ValueError("pending permission response required")
        if self._phase == "cancelling":
            event = self._cancel_event
        elif self._phase == "running" and self._cursor < len(self._events):
            event = self._events[self._cursor]
            self._cursor += 1
        else:
            raise StopAsyncIteration
        if event is None:
            raise ValueError("FakeWorker terminal event unavailable")
        self._last_sequence = event.sequence
        if event.kind == "permission_requested":
            self._pending_permission = event.payload["permission_request_id"]
        if event.kind in {"completed", "failed", "cancelled"}:
            self._pending_permission = None
            self._phase = event.kind
            self._result = _worker_result(handle, event.kind, event.payload)
        return event

    async def respond_permission(
        self,
        handle: WorkerHandle,
        *,
        permission_request_id: str,
        allowed: bool,
        reason: str,
    ) -> None:
        self._require_handle(handle)
        if self._phase in {"completed", "failed", "cancelled"}:
            raise ValueError("FakeWorker task is terminal")
        permission_request_id = _permission_id(permission_request_id)
        if permission_request_id != self._pending_permission:
            raise ValueError("permission request is not pending")
        if type(allowed) is not bool:
            raise ValueError("permission response invalid")
        reason = validate_worker_reason(reason)
        self._permission_responses.append(
            (handle.session_id, permission_request_id, allowed, reason)
        )
        self._pending_permission = None

    async def cancel_task(self, handle: WorkerHandle, *, reason: str) -> None:
        self._require_handle(handle)
        if self._phase != "running":
            raise ValueError("FakeWorker task is not cancellable")
        reason = validate_worker_reason(reason)
        self._phase = "cancelling"
        self._pending_permission = None
        self._cancel_event = _worker_event(
            handle, self._last_sequence + 1, "cancelled", {"reason": reason}
        )

    async def collect_result(self, handle: WorkerHandle) -> WorkerResult:
        self._require_handle(handle)
        if self._phase not in {"completed", "failed", "cancelled"} or self._result is None:
            raise ValueError("FakeWorker result unavailable")
        return self._result

    def _require_handle(self, handle: WorkerHandle) -> None:
        if type(handle) is not WorkerHandle or handle != self._handle:
            raise ValueError("FakeWorker handle invalid")


def _lineage(handle: WorkerHandle) -> dict[str, str]:
    return {name: getattr(handle, name) for name in ("session_id", "agent_id", "task_id", "attempt_id")}


def _worker_event(handle, sequence, kind, payload) -> WorkerEvent:
    return WorkerEvent(
        event_id=f"evt_{sequence}", **_lineage(handle), transport="acp", sequence=sequence,
        kind=kind, timestamp=f"2026-07-19T00:00:0{sequence}+00:00", payload=payload,
    )


def _worker_result(handle, status, payload) -> WorkerResult:
    return WorkerResult(**_lineage(handle), status=status, payload=payload)


def _permission_id(value: object) -> str:
    if type(value) is not str:
        raise ValueError("permission_request_id must be typed and bounded")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError("permission_request_id must be typed and bounded") from None
    if not value.startswith("perm_") or not value[5:] or len(encoded) > 256 or any(c.isspace() for c in value):
        raise ValueError("permission_request_id must be typed and bounded")
    return value


class _FakeEventStream:
    def __init__(self, worker: FakeWorker, handle: WorkerHandle) -> None:
        self._worker = worker
        self._handle = handle

    def __aiter__(self):
        return self

    async def __anext__(self) -> WorkerEvent:
        return self._worker._next_event(self._handle)
