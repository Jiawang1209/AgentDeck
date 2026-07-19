"""Map official ACP session traffic into the stable Worker Port."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Final

from acp import PROTOCOL_VERSION
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    DeniedOutcome,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
)

from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.events import normalize_occurred_at
from agentdeck.ports.clock import Clock
from agentdeck.ports.worker import (
    TaskRequest,
    WorkerEvent,
    WorkerHandle,
    WorkerResult,
    validate_worker_reason,
)


_MAX_ACP_UPDATE_BYTES: Final = 64 * 1024
_MAX_ACP_TOTAL_BYTES: Final = 1024 * 1024
_PROVEN_READ_ONLY_TOOL_KINDS: Final = frozenset({"read", "search", "think"})


class ACPWorkerError(RuntimeError):
    """Content-free typed failure at the ACP Worker boundary."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(f"ACP Worker failed: {diagnostic.code}")
        self.diagnostic = diagnostic


@dataclass
class _PermissionWaiter:
    permission_id: str
    options: tuple[PermissionOption, ...]
    future: asyncio.Future[RequestPermissionResponse]


@dataclass
class _Run:
    request: TaskRequest
    handle: WorkerHandle
    raw_session_id: str
    queue: asyncio.Queue[WorkerEvent | None] = field(default_factory=asyncio.Queue)
    prompt_task: asyncio.Task[None] | None = None
    result: WorkerResult | None = None
    error: ACPWorkerError | None = None
    pending_permission: _PermissionWaiter | None = None
    sequence: int = 0
    permission_count: int = 0
    raw_total_bytes: int = 0
    raw_sequence: int = 0
    raw_event_ids: set[str] = field(default_factory=set)
    effect_may_have_occurred: bool = False
    cancellation_requested: bool = False
    stream_started: bool = False
    terminal: bool = False


class ACPWorker:
    """One-task ACP Worker adapter; Task 26 supplies the real connection."""

    def __init__(
        self, *, agent: object, project_root: str, clock: Clock,
        max_update_bytes: int = _MAX_ACP_UPDATE_BYTES,
        max_total_bytes: int = _MAX_ACP_TOTAL_BYTES,
    ) -> None:
        if not all(callable(getattr(agent, name, None)) for name in (
            "initialize", "new_session", "prompt", "cancel",
        )):
            raise TypeError("agent must expose the ACP Agent operations")
        if type(project_root) is not str or not project_root.strip():
            raise ValueError("project_root must be a nonempty string")
        if not callable(getattr(clock, "now", None)):
            raise TypeError("clock must expose now")
        if type(max_update_bytes) is not int or not 0 < max_update_bytes <= _MAX_ACP_UPDATE_BYTES:
            raise ValueError("max_update_bytes is invalid")
        if (
            type(max_total_bytes) is not int
            or not max_update_bytes <= max_total_bytes <= _MAX_ACP_TOTAL_BYTES
        ):
            raise ValueError("max_total_bytes is invalid")
        self._agent = agent
        self._project_root = project_root
        self._clock = clock
        self._max_update_bytes = max_update_bytes
        self._max_total_bytes = max_total_bytes
        self._run: _Run | None = None
        connector = getattr(agent, "on_connect", None)
        if callable(connector):
            connector(self)

    async def start_task(self, request: TaskRequest) -> WorkerHandle:
        if type(request) is not TaskRequest or self._run is not None:
            raise ValueError("ACP Worker task start invalid")
        try:
            initialized = await self._agent.initialize(PROTOCOL_VERSION)
        except Exception:
            raise self._error("acp_initialization_failed", True, request) from None
        if (
            type(initialized) is not InitializeResponse
            or initialized.protocol_version != PROTOCOL_VERSION
        ):
            raise self._error("acp_protocol_mismatch", True, request)
        try:
            session = await self._agent.new_session(cwd=self._project_root)
        except Exception:
            raise self._error("acp_session_failed", True, request) from None
        if type(session) is not NewSessionResponse or not session.session_id:
            raise self._error("acp_protocol_mismatch", True, request)
        session_digest = sha256(
            f"{session.session_id}:{request.agent_id}:{request.attempt_id}".encode()
        ).hexdigest()[:32]
        handle = WorkerHandle(
            session_id=f"ses_{session_digest}", agent_id=request.agent_id,
            task_id=request.task_id, attempt_id=request.attempt_id,
        )
        self._run = _Run(request=request, handle=handle, raw_session_id=session.session_id)
        self._emit("started", {"protocol_version": PROTOCOL_VERSION})
        self._run.prompt_task = asyncio.create_task(self._run_prompt())
        return handle

    def stream_events(self, handle: WorkerHandle) -> AsyncIterator[WorkerEvent]:
        run = self._require_handle(handle)
        if run.stream_started:
            raise ValueError("ACP Worker event stream already consumed")
        run.stream_started = True
        return self._event_stream(run)

    async def respond_permission(
        self, handle: WorkerHandle, *, permission_request_id: str,
        allowed: bool, reason: str,
    ) -> None:
        run = self._require_handle(handle)
        if run.terminal:
            raise ValueError("ACP Worker task is terminal")
        permission_id = _permission_id(permission_request_id)
        waiter = run.pending_permission
        if waiter is None or waiter.permission_id != permission_id:
            raise ValueError("permission request is not pending")
        if type(allowed) is not bool:
            raise ValueError("permission response invalid")
        validate_worker_reason(reason)
        desired = "allow_" if allowed else "reject_"
        selected = next(
            (option for option in waiter.options if option.kind.startswith(desired)),
            None,
        )
        response = RequestPermissionResponse(
            outcome=(
                AllowedOutcome(outcome="selected", option_id=selected.option_id)
                if selected is not None
                else DeniedOutcome(outcome="cancelled")
            )
        )
        run.pending_permission = None
        if not waiter.future.done():
            waiter.future.set_result(response)

    async def cancel_task(self, handle: WorkerHandle, *, reason: str) -> None:
        run = self._require_handle(handle)
        if run.terminal:
            raise ValueError("ACP Worker task is not cancellable")
        reason = validate_worker_reason(reason)
        run.cancellation_requested = True
        try:
            await self._agent.cancel(run.raw_session_id)
        except Exception:
            error = self._error("acp_cancel_failed", False, run.request)
            await self._cancel_prompt(run)
            self._fail(error)
            raise error from None
        await self._cancel_prompt(run)
        self._finish("cancelled", {"reason": reason})

    async def collect_result(self, handle: WorkerHandle) -> WorkerResult:
        run = self._require_handle(handle)
        if not run.terminal:
            raise ValueError("ACP Worker result unavailable")
        if run.error is not None:
            raise run.error
        if run.result is None:
            raise ValueError("ACP Worker result unavailable")
        return run.result

    async def session_update(
        self, session_id: str, update: object, **kwargs: Any,
    ) -> None:
        run = self._require_raw_session(session_id)
        if run.terminal:
            raise self._run_error("acp_sequence_violation", run)
        if (
            type(update) in {ToolCallStart, ToolCallProgress}
            and update.kind not in _PROVEN_READ_ONLY_TOOL_KINDS
        ) or (type(update) is ToolCallProgress and update.locations):
            run.effect_may_have_occurred = True
        self._inspect_raw_update(run, update)
        try:
            if type(update) is AgentMessageChunk:
                content = update.content
                payload = (
                    {"text": content.text}
                    if type(content) is TextContentBlock
                    else {"content_type": content.type}
                )
                self._emit("message", payload)
            elif type(update) is ToolCallStart:
                self._emit("tool_started", _tool_payload(update))
            elif type(update) is ToolCallProgress:
                if update.status == "completed":
                    self._emit("tool_completed", _tool_payload(update))
                else:
                    self._emit("progress", _tool_payload(update))
                if update.locations:
                    self._emit("artifact_changed", {"artifact_count": len(update.locations)})
            else:
                self._emit("progress", {"update_type": type(update).__name__})
        except ACPWorkerError:
            raise
        except (TypeError, ValueError):
            raise self._run_error("acp_sensitive_output_redacted", run) from None

    async def request_permission(
        self, session_id: str, tool_call: ToolCallUpdate,
        options: list[PermissionOption], **kwargs: Any,
    ) -> RequestPermissionResponse:
        run = self._require_raw_session(session_id)
        if run.pending_permission is not None or run.terminal:
            raise self._run_error("acp_sequence_violation", run)
        if type(tool_call) is not ToolCallUpdate or type(options) is not list or not options:
            raise self._run_error("acp_protocol_mismatch", run)
        if any(type(option) is not PermissionOption for option in options):
            raise self._run_error("acp_protocol_mismatch", run)
        try:
            raw_size = len(tool_call.model_dump_json(by_alias=True).encode("utf-8"))
            raw_size += sum(
                len(option.model_dump_json(by_alias=True).encode("utf-8"))
                for option in options
            )
        except Exception:
            raise self._run_error("acp_protocol_mismatch", run) from None
        self._consume_raw_size(run, raw_size)
        run.permission_count += 1
        permission_id = f"perm_{run.permission_count}"
        future = asyncio.get_running_loop().create_future()
        run.pending_permission = _PermissionWaiter(
            permission_id, tuple(options), future,
        )
        try:
            self._emit("permission_requested", {
                "permission_request_id": permission_id,
                "tool_call_id": tool_call.tool_call_id,
                "option_count": len(options),
            })
        except (TypeError, ValueError):
            run.pending_permission = None
            raise self._run_error("acp_sensitive_output_redacted", run) from None
        return await future

    async def _run_prompt(self) -> None:
        run = self._current()
        try:
            response = await self._agent.prompt(
                run.raw_session_id,
                [TextContentBlock(type="text", text=run.request.instruction)],
            )
            if run.cancellation_requested:
                return
            if type(response) is not PromptResponse:
                raise self._run_error("acp_protocol_mismatch", run)
            if run.pending_permission is not None:
                try:
                    await self._agent.cancel(run.raw_session_id)
                    error = self._run_error("acp_sequence_violation", run)
                except Exception:
                    error = self._error("acp_cancel_failed", False, run.request)
                self._fail(error)
                return
            if response.stop_reason == "end_turn":
                self._finish("completed", {"stop_reason": "end_turn"})
            elif response.stop_reason == "cancelled":
                self._finish("cancelled", {"reason": "agent_cancelled"})
            else:
                self._finish("failed", {"stop_reason": response.stop_reason})
        except asyncio.CancelledError:
            raise
        except ACPWorkerError as error:
            if not run.cancellation_requested:
                self._fail(error)
        except Exception as error:
            if run.cancellation_requested:
                return
            code = (
                "worker_outcome_unknown"
                if run.effect_may_have_occurred
                else "acp_disconnected_before_effect"
            )
            known = not run.effect_may_have_occurred
            self._fail(self._error(
                code, known, run.request,
                retryable=known and _is_recoverable_disconnect(error),
            ))

    async def _cancel_prompt(self, run: _Run) -> None:
        task = run.prompt_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _inspect_raw_update(self, run: _Run, update: object) -> None:
        serializer = getattr(update, "model_dump_json", None)
        if not callable(serializer):
            raise self._run_error("acp_protocol_mismatch", run)
        try:
            size = len(serializer(by_alias=True).encode("utf-8", "strict"))
        except Exception:
            raise self._run_error("acp_protocol_mismatch", run) from None
        self._consume_raw_size(run, size)
        metadata = getattr(update, "field_meta", None)
        if not isinstance(metadata, dict):
            return
        event_id = metadata.get("event_id")
        sequence = metadata.get("sequence")
        if event_id is not None:
            if type(event_id) is not str or event_id in run.raw_event_ids:
                raise self._run_error("acp_duplicate_event", run)
            run.raw_event_ids.add(event_id)
        if sequence is not None:
            if type(sequence) is not int or sequence <= run.raw_sequence:
                raise self._run_error("acp_sequence_violation", run)
            run.raw_sequence = sequence

    def _consume_raw_size(self, run: _Run, size: int) -> None:
        run.raw_total_bytes += size
        if size > self._max_update_bytes or run.raw_total_bytes > self._max_total_bytes:
            raise self._run_error("acp_output_oversize", run)

    def _emit(self, kind: str, payload: dict[str, object]) -> None:
        run = self._current()
        if run.terminal:
            raise self._run_error("acp_sequence_violation", run)
        run.sequence += 1
        digest = sha256(
            f"{run.handle.session_id}:{run.sequence}:{kind}".encode()
        ).hexdigest()[:32]
        try:
            event = WorkerEvent(
                event_id=f"evt_{digest}", session_id=run.handle.session_id,
                agent_id=run.handle.agent_id, task_id=run.handle.task_id,
                attempt_id=run.handle.attempt_id, transport="acp",
                sequence=run.sequence, kind=kind, timestamp=self._now(),
                payload=payload,
            )
        except (TypeError, ValueError):
            raise self._run_error("acp_sensitive_output_redacted", run) from None
        run.queue.put_nowait(event)

    def _finish(self, status: str, payload: dict[str, object]) -> None:
        run = self._current()
        if run.terminal:
            return
        self._cancel_pending_permission(run)
        self._emit(status, payload)
        run.terminal = True
        run.result = WorkerResult(
            session_id=run.handle.session_id, agent_id=run.handle.agent_id,
            task_id=run.handle.task_id, attempt_id=run.handle.attempt_id,
            status=status, payload=payload,
        )
        run.queue.put_nowait(None)

    def _fail(self, error: ACPWorkerError) -> None:
        run = self._current()
        if run.terminal:
            return
        self._cancel_pending_permission(run)
        self._emit("failed", {
            "diagnostic_code": error.diagnostic.code,
            "outcome_known": error.diagnostic.outcome_known,
        })
        run.error = error
        run.terminal = True
        run.queue.put_nowait(None)

    async def _event_stream(self, run: _Run) -> AsyncIterator[WorkerEvent]:
        while True:
            event = await run.queue.get()
            if event is None:
                return
            yield event

    def _require_handle(self, handle: WorkerHandle) -> _Run:
        run = self._current()
        if type(handle) is not WorkerHandle or handle != run.handle:
            raise ValueError("ACP Worker handle invalid")
        return run

    def _require_raw_session(self, session_id: str) -> _Run:
        run = self._current()
        if type(session_id) is not str or session_id != run.raw_session_id:
            raise self._run_error("acp_protocol_mismatch", run)
        return run

    @staticmethod
    def _cancel_pending_permission(run: _Run) -> None:
        waiter = run.pending_permission
        run.pending_permission = None
        if waiter is not None and not waiter.future.done():
            waiter.future.cancel()

    def _current(self) -> _Run:
        if self._run is None:
            raise ValueError("ACP Worker has no active task")
        return self._run

    def _error(
        self, code: str, outcome_known: bool, request: TaskRequest, *,
        retryable: bool = False,
    ) -> ACPWorkerError:
        return ACPWorkerError(Diagnostic.create(
            code=code, stage="worker_transport", severity=Severity.ERROR,
            actor="agentdeck", summary="The ACP Worker operation did not complete.",
            cause="The ACP transport or decoded update violated its bounded contract.",
            impact="The current Worker Attempt cannot advance.",
            protection="AgentDeck retained the last known safe outcome and redacted raw protocol data.",
            recovery_actions=("Inspect the typed diagnostic and retry only when its outcome is known.",),
            retryable=retryable, outcome_known=outcome_known,
            occurred_at=self._now(), task_id=request.task_id,
            attempt_id=request.attempt_id,
        ))

    def _run_error(self, code: str, run: _Run) -> ACPWorkerError:
        return self._error(code, not run.effect_may_have_occurred, run.request)

    def _now(self) -> str:
        try:
            return normalize_occurred_at(self._clock.now().isoformat())
        except Exception:
            raise RuntimeError("clock did not provide a canonical aware time") from None


def _permission_id(value: object) -> str:
    if type(value) is not str or not value.startswith("perm_") or not value[5:]:
        raise ValueError("permission_request_id must be typed and bounded")
    try:
        if len(value.encode("utf-8", "strict")) > 256 or any(c.isspace() for c in value):
            raise ValueError
    except (UnicodeEncodeError, ValueError):
        raise ValueError("permission_request_id must be typed and bounded") from None
    return value


def _is_recoverable_disconnect(error: Exception) -> bool:
    return isinstance(error, (ConnectionError, EOFError, TimeoutError))


def _tool_payload(update: ToolCallStart | ToolCallProgress) -> dict[str, object]:
    payload: dict[str, object] = {"tool_call_id": update.tool_call_id}
    for name in ("kind", "status", "title"):
        value = getattr(update, name)
        if value is not None:
            payload[name] = value
    return payload


__all__ = ["ACPWorker", "ACPWorkerError"]
