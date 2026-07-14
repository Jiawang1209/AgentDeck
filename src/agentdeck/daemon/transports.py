"""State-free Worker transport adapters for the authoritative project daemon.

The adapters perform external I/O only.  They never import or mutate the
StateStore, and they deliberately provide no cross-transport fallback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

from acp import schema

from ..models import AgentSpec, RuntimeConfig
from ..runtime.acp import AcpTransport
from ..runtime.acp_client import AgentDeckAcpClient, PermissionDecision
from ..runtime.acp_mapping import ensure_turn_within_bounds
from ..runtime.protocol import UPDATE_KINDS
from ..runtime.tmux import TmuxBackend
from ..workflow import build_workflow_prompt, parse_correlated_reply
from .supervisor import SubmittedReceipt, TransportResult


class WorkerTransportError(RuntimeError):
    """One configured Worker transport could not complete its bounded I/O."""


@dataclass(frozen=True)
class _CleanupResult:
    close: str
    disconnect: str

    @property
    def completed(self) -> bool:
        return self.close == "completed" and self.disconnect in {
            "completed", "not_required"
        }


class _TmuxIo(Protocol):
    def send_input(self, config: RuntimeConfig, pane_id: str, text: str) -> None: ...

    def capture_output(
        self, config: RuntimeConfig, pane_id: str, lines: int = 200
    ) -> str: ...


def _attempt_authority(
    attempt: Mapping[str, object], *, expected_transport: str
) -> tuple[str, str, str]:
    if not isinstance(attempt, Mapping):
        raise WorkerTransportError("Worker attempt authority is invalid")
    attempt_id = attempt.get("attempt_id")
    agent_id = attempt.get("agent_id")
    dispatch_key = attempt.get("dispatch_key")
    if (
        type(attempt_id) is not str
        or not attempt_id
        or type(agent_id) is not str
        or not agent_id
        or attempt.get("configured_transport") != expected_transport
        or type(dispatch_key) is not str
        or not dispatch_key.startswith("dsp_")
        or len(dispatch_key) != 36
    ):
        raise WorkerTransportError("Worker attempt authority is invalid")
    return attempt_id, agent_id, dispatch_key


def _validate_receipt(
    attempt: Mapping[str, object], receipt: SubmittedReceipt, *, transport: str
) -> tuple[str, str]:
    attempt_id, _agent_id, dispatch_key = _attempt_authority(
        attempt, expected_transport=transport
    )
    if not isinstance(receipt, SubmittedReceipt) or receipt.dispatch_key != dispatch_key:
        raise WorkerTransportError("Worker receipt lineage drift")
    return attempt_id, dispatch_key


def build_worker_prompt(
    attempt: Mapping[str, object],
    agent: AgentSpec,
    *,
    task: str,
    previous_handoff: dict[str, Any] | None = None,
) -> str:
    """Build one canonical prompt from explicit attempt and Agent configuration."""
    if not isinstance(agent, AgentSpec):
        raise WorkerTransportError("Worker Agent configuration is invalid")
    _attempt_id, agent_id, dispatch_key = _attempt_authority(
        attempt, expected_transport=agent.transport
    )
    if (
        agent.agent_id != agent_id
        or type(task) is not str
        or not task.strip()
        or (previous_handoff is not None and type(previous_handoff) is not dict)
    ):
        raise WorkerTransportError("Worker prompt authority drift")
    return build_workflow_prompt(
        role=agent.role,
        role_prompt=agent.role_prompt,
        task=task,
        handoff_token=dispatch_key,
        previous_handoff=previous_handoff,
    )


class TmuxWorkerTransport:
    """One configured tmux pane with bounded correlated-reply polling."""

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        pane_id: str,
        prompt: str,
        backend: _TmuxIo | None = None,
        max_polls: int = 30,
        poll_interval_seconds: float = 1.0,
        capture_lines: int = 200,
    ) -> None:
        if (
            not isinstance(config, RuntimeConfig)
            or type(pane_id) is not str
            or not pane_id
            or type(prompt) is not str
            or not prompt
            or type(max_polls) is not int
            or max_polls <= 0
            or type(poll_interval_seconds) not in {int, float}
            or poll_interval_seconds < 0
            or type(capture_lines) is not int
            or capture_lines <= 0
        ):
            raise ValueError("invalid tmux Worker transport configuration")
        selected = TmuxBackend() if backend is None else backend
        if not callable(getattr(selected, "send_input", None)) or not callable(
            getattr(selected, "capture_output", None)
        ):
            raise TypeError("tmux Worker backend is invalid")
        self._config = config
        self._pane_id = pane_id
        self._prompt = prompt
        self._backend = selected
        self._max_polls = max_polls
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._capture_lines = capture_lines

    async def admit(self, attempt: Mapping[str, object]) -> SubmittedReceipt:
        attempt_id, _agent_id, dispatch_key = _attempt_authority(
            attempt, expected_transport="tmux"
        )
        try:
            await asyncio.to_thread(
                self._backend.send_input,
                self._config,
                self._pane_id,
                self._prompt,
            )
        except Exception as error:
            raise WorkerTransportError("tmux Worker admission failed") from error
        return SubmittedReceipt(
            receipt_id=f"tmux:{attempt_id}:{self._pane_id}",
            dispatch_key=dispatch_key,
            summary="tmux input submitted",
        )

    async def complete(
        self, attempt: Mapping[str, object], receipt: SubmittedReceipt
    ) -> TransportResult:
        _attempt_id, dispatch_key = _validate_receipt(
            attempt, receipt, transport="tmux"
        )
        for poll in range(self._max_polls):
            try:
                output = await asyncio.to_thread(
                    self._backend.capture_output,
                    self._config,
                    self._pane_id,
                    self._capture_lines,
                )
            except Exception as error:
                raise WorkerTransportError("tmux Worker capture failed") from error
            if type(output) is not str:
                raise WorkerTransportError("tmux Worker capture is invalid")
            reply = parse_correlated_reply(output, dispatch_key)
            if reply is not None:
                return TransportResult(
                    stop_reason="structured_reply",
                    validated=True,
                    reply={
                        key: reply[key]
                        for key in (
                            "handoff_token",
                            "status",
                            "summary",
                            "verification",
                            "risks",
                            "next_steps",
                        )
                    },
                )
            if poll + 1 < self._max_polls:
                await asyncio.sleep(self._poll_interval_seconds)
        raise WorkerTransportError("tmux Worker bounded poll exhausted")


class _AcpFactory(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        workspace: str | Path,
        client: object,
        *,
        request_timeout: float,
    ) -> object: ...


class _MemoryAcpSink:
    """Bounded process memory only; never a durable ledger or authorization."""

    def __init__(self) -> None:
        self.fragments: list[str] = []
        self.payload_bytes = 0
        self.update_count = 0
        self.permission_seen = False
        self.permission_decisions: list[PermissionDecision] = []

    async def append_update(
        self, _session_id: str, kind: str, payload: dict[str, Any]
    ) -> object:
        if (
            type(kind) is not str
            or kind not in UPDATE_KINDS
            or type(payload) is not dict
        ):
            raise WorkerTransportError("ACP Worker emitted an unsupported update")
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise WorkerTransportError("ACP Worker emitted an unsupported update") from None
        prospective_bytes = self.payload_bytes + len(encoded)
        ensure_turn_within_bounds(prospective_bytes, self.update_count + 1)
        if kind == "text":
            content = payload.get("content")
            text = content.get("text") if type(content) is dict else None
            if (
                payload.get("role") not in {"agent", "user", "thought"}
                or content.get("type") != "text"
                or type(text) is not str
            ):
                raise WorkerTransportError("ACP Worker emitted an unsupported update")
            if payload["role"] == "agent":
                self.fragments.append(text)
        self.payload_bytes = prospective_bytes
        self.update_count += 1
        return {"kind": kind}

    async def append_permission(
        self,
        _session_id: str,
        summary: dict[str, Any],
        _options: list[schema.PermissionOption],
    ) -> object:
        self.permission_seen = True
        return {"status": "denied", "summary": summary}

    async def append_permission_decision(
        self,
        _session_id: str,
        _tool_call_id: str,
        decision: PermissionDecision,
    ) -> None:
        self.permission_decisions.append(decision)


@dataclass
class _ActiveAcpAttempt:
    transport: Any
    session_id: str
    sink: Any
    dispatch_key: str


class AcpWorkerTransport:
    """One ACP process/session lifecycle with an in-memory fail-closed Client."""

    def __init__(
        self,
        *,
        argv: tuple[str, ...],
        workspace: str | Path,
        prompt: str,
        transport_factory: _AcpFactory = AcpTransport,
        request_timeout: float = 30.0,
        sink: object | None = None,
        decide: Callable[
            [dict[str, Any], list[schema.PermissionOption]],
            PermissionDecision | Awaitable[PermissionDecision],
        ]
        | None = None,
    ) -> None:
        if (
            type(argv) is not tuple
            or not argv
            or any(type(item) is not str or not item for item in argv)
            or type(prompt) is not str
            or not prompt
            or not callable(transport_factory)
            or type(request_timeout) not in {int, float}
            or request_timeout <= 0
            or (decide is not None and not callable(decide))
        ):
            raise ValueError("invalid ACP Worker transport configuration")
        selected_sink = _MemoryAcpSink() if sink is None else sink
        if not all(
            callable(getattr(selected_sink, name, None))
            for name in (
                "append_update",
                "append_permission",
                "append_permission_decision",
            )
        ) or not isinstance(getattr(selected_sink, "fragments", None), list):
            raise ValueError("invalid ACP Worker ledger sink")
        resolved = Path(workspace).resolve()
        if not resolved.is_dir():
            raise ValueError("ACP Worker workspace is invalid")
        self._argv = argv
        self._workspace = resolved
        self._prompt = prompt
        self._factory = transport_factory
        self._request_timeout = float(request_timeout)
        self._sink = selected_sink
        self._decide = decide
        self._active: dict[str, _ActiveAcpAttempt] = {}
        self._admission_in_progress = False
        self._cleanup_tasks: set[asyncio.Future[Any]] = set()
        self._cleanup_diagnostics: list[dict[str, str]] = []

    @property
    def cleanup_diagnostics(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(item) for item in self._cleanup_diagnostics)

    def _retain_cleanup_task(self, task: asyncio.Future[Any]) -> None:
        self._cleanup_tasks.add(task)

        def consume(completed: asyncio.Future[Any]) -> None:
            self._cleanup_tasks.discard(completed)
            try:
                completed.result()
            except BaseException:
                pass

        task.add_done_callback(consume)

    async def _bounded_cleanup(self, operation: object) -> str:
        if not inspect.isawaitable(operation):
            return "completed"
        task = asyncio.ensure_future(operation)
        try:
            done, _pending = await asyncio.wait(
                {task}, timeout=self._request_timeout
            )
        except asyncio.CancelledError:
            task.cancel()
            asyncio.get_running_loop().call_soon(task.cancel)
            self._retain_cleanup_task(task)
            raise
        except BaseException:
            task.cancel()
            asyncio.get_running_loop().call_soon(task.cancel)
            self._retain_cleanup_task(task)
            return "failed"
        if task not in done:
            task.cancel()
            asyncio.get_running_loop().call_soon(task.cancel)
            self._retain_cleanup_task(task)
            return "timed_out"
        try:
            task.result()
        except BaseException:
            return "failed"
        return "completed"

    async def _close_then_disconnect(
        self,
        transport: object,
        sink: object,
        *,
        session_id: str | None,
        success_reason: str,
        failure_reason: str,
    ) -> _CleanupResult:
        pending_cancellation: asyncio.CancelledError | None = None
        try:
            close_operation = transport.close()  # type: ignore[attr-defined]
        except BaseException:
            close_status = "failed"
        else:
            try:
                close_status = await self._bounded_cleanup(close_operation)
            except asyncio.CancelledError as exc:
                # A repeated cancellation must not revoke the registered
                # Worker's final opportunity to submit its durable session
                # disconnect.  _bounded_cleanup already retained close.
                pending_cancellation = exc
                close_status = "failed"
        if session_id is None:
            result = _CleanupResult(close_status, "not_required")
            if not result.completed:
                self._cleanup_diagnostics.append(
                    {"close": result.close, "disconnect": result.disconnect}
                )
            if pending_cancellation is not None:
                raise pending_cancellation
            return result
        reason = success_reason if close_status == "completed" else failure_reason
        begin_disconnect = getattr(sink, "begin_disconnect", None)
        disconnect = getattr(sink, "disconnect", None)
        try:
            operation = (
                begin_disconnect(reason)
                if callable(begin_disconnect)
                else disconnect(reason) if callable(disconnect) else None
            )
        except BaseException:
            disconnect_status = "failed"
        else:
            if pending_cancellation is not None and inspect.isawaitable(operation):
                disconnect_task = asyncio.ensure_future(operation)
                self._retain_cleanup_task(disconnect_task)
                disconnect_status = "pending"
            elif pending_cancellation is not None:
                disconnect_status = "completed"
            else:
                disconnect_status = await self._bounded_cleanup(operation)
        result = _CleanupResult(close_status, disconnect_status)
        if not result.completed:
            self._cleanup_diagnostics.append(
                {"close": result.close, "disconnect": result.disconnect}
            )
        if pending_cancellation is not None:
            raise pending_cancellation
        return result

    async def admit(self, attempt: Mapping[str, object]) -> SubmittedReceipt:
        attempt_id, _agent_id, dispatch_key = _attempt_authority(
            attempt, expected_transport="acp"
        )
        if self._cleanup_tasks:
            raise WorkerTransportError("ACP Worker cleanup is pending")
        if self._admission_in_progress or self._active:
            raise WorkerTransportError("ACP Worker transport is already active")
        if attempt_id in self._active:
            raise WorkerTransportError("ACP Worker attempt is already admitted")
        self._admission_in_progress = True
        try:
            sink = self._sink
            client = AgentDeckAcpClient(
                sink=sink,
                decide=(
                    self._decide
                    if self._decide is not None
                    else lambda *_: PermissionDecision.cancelled(
                        "daemon_worker_permission_denied"
                    )
                ),
            )
            transport = self._factory(
                self._argv,
                self._workspace,
                client,
                request_timeout=self._request_timeout,
            )
            if not all(
                callable(getattr(transport, name, None))
                for name in ("initialize", "new_session", "prompt", "close")
            ):
                raise WorkerTransportError("ACP Worker transport factory is invalid")
        except BaseException:
            self._admission_in_progress = False
            raise
        session_id: str | None = None
        try:
            try:
                initialized = await transport.initialize()
                session = await transport.new_session()
                session_id = getattr(session, "native_session_id", None)
                if type(session_id) is not str or not session_id:
                    raise WorkerTransportError("ACP Worker session is invalid")
                activate = getattr(sink, "activate", None)
                if callable(activate):
                    activated = activate(session_id, initialized)
                    if inspect.isawaitable(activated):
                        await activated
            except asyncio.CancelledError:
                await self._close_then_disconnect(
                    transport,
                    sink,
                    session_id=session_id,
                    success_reason="admission_cancelled",
                    failure_reason="admission_cancelled",
                )
                raise
            except Exception as error:
                await self._close_then_disconnect(
                    transport,
                    sink,
                    session_id=session_id,
                    success_reason="admission_failed",
                    failure_reason="admission_failed",
                )
                if isinstance(error, WorkerTransportError):
                    raise
                raise WorkerTransportError("ACP Worker admission failed") from error
        finally:
            self._admission_in_progress = False
        self._active[attempt_id] = _ActiveAcpAttempt(
            transport=transport,
            session_id=session_id,
            sink=sink,
            dispatch_key=dispatch_key,
        )
        return SubmittedReceipt(
            receipt_id=f"acp:{attempt_id}:{session_id}",
            dispatch_key=dispatch_key,
            summary="ACP session admitted",
        )

    async def complete(
        self, attempt: Mapping[str, object], receipt: SubmittedReceipt
    ) -> TransportResult:
        attempt_id, dispatch_key = _validate_receipt(
            attempt, receipt, transport="acp"
        )
        active = self._active.get(attempt_id)
        if active is None or active.dispatch_key != dispatch_key:
            raise WorkerTransportError("ACP Worker session is not admitted")
        try:
            try:
                result = await active.transport.prompt(active.session_id, self._prompt)
                if bool(getattr(active.sink, "permission_seen", False)) and bool(
                    getattr(active.sink, "fail_on_permission", True)
                ):
                    raise WorkerTransportError("ACP Worker requested a forbidden permission")
                stop_reason = getattr(result, "stop_reason", None)
                if type(stop_reason) is not str or not stop_reason:
                    raise WorkerTransportError("ACP Worker result is invalid")
                reply = parse_correlated_reply("".join(active.sink.fragments), dispatch_key)
                if reply is None:
                    raise WorkerTransportError("ACP Worker returned no correlated reply")
                finish = getattr(active.sink, "finish", None)
                if callable(finish):
                    finished = finish(stop_reason)
                    if inspect.isawaitable(finished):
                        await finished
                completed = TransportResult(
                    stop_reason=stop_reason,
                    validated=True,
                    reply={
                        key: reply[key]
                        for key in (
                            "handoff_token",
                            "status",
                            "summary",
                            "verification",
                            "risks",
                            "next_steps",
                        )
                    },
                )
            except asyncio.CancelledError:
                await self._close_then_disconnect(
                    active.transport, active.sink, session_id=active.session_id,
                    success_reason="transport_closed",
                    failure_reason="transport_close_failed",
                )
                raise
            except WorkerTransportError:
                await self._close_then_disconnect(
                    active.transport, active.sink, session_id=active.session_id,
                    success_reason="transport_closed",
                    failure_reason="transport_close_failed",
                )
                raise
            except Exception as error:
                await self._close_then_disconnect(
                    active.transport, active.sink, session_id=active.session_id,
                    success_reason="transport_closed",
                    failure_reason="transport_close_failed",
                )
                raise WorkerTransportError("ACP Worker completion failed") from error
            cleanup = await self._close_then_disconnect(
                active.transport, active.sink, session_id=active.session_id,
                success_reason="transport_closed",
                failure_reason="transport_close_failed",
            )
            if not cleanup.completed:
                raise WorkerTransportError("ACP Worker cleanup failed")
            return completed
        finally:
            self._active.pop(attempt_id, None)
