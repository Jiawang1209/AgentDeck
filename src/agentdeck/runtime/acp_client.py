from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from acp import Agent, schema
from acp.exceptions import RequestError

from agentdeck.runtime.acp_mapping import map_session_update, summarize_permission


_SUPPORTED_PERMISSION_KINDS = {"allow_once", "reject_once"}
_METHOD_NOT_FOUND = -32601
_CANCEL_SETTLEMENT_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True)
class PermissionDecision:
    """A foreground permission result; it is never an authorization policy."""

    option_id: str | None
    reason: str
    ledger_status: str

    @classmethod
    def select(cls, option_id: str) -> PermissionDecision:
        if type(option_id) is not str or not option_id:
            raise ValueError("option_id must be a non-empty string")
        return cls(option_id=option_id, reason="selected", ledger_status="pending")

    @classmethod
    def cancelled(cls, reason: str = "cancelled") -> PermissionDecision:
        if type(reason) is not str or not reason:
            raise ValueError("reason must be a non-empty string")
        return cls(option_id=None, reason=reason, ledger_status="denied")

    def settled_for(self, option: schema.PermissionOption) -> PermissionDecision:
        status = "approved" if option.kind == "allow_once" else "denied"
        return PermissionDecision(self.option_id, self.reason, status)

    def to_acp_response(self) -> schema.RequestPermissionResponse:
        if self.option_id is None:
            outcome: schema.DeniedOutcome | schema.AllowedOutcome = schema.DeniedOutcome(
                outcome="cancelled"
            )
        else:
            outcome = schema.AllowedOutcome(outcome="selected", optionId=self.option_id)
        return schema.RequestPermissionResponse(outcome=outcome)


class AcpLedgerSink(Protocol):
    async def append_update(
        self, session_id: str, kind: str, payload: dict[str, Any]
    ) -> object: ...

    async def append_permission(
        self,
        session_id: str,
        summary: dict[str, Any],
        options: list[schema.PermissionOption],
    ) -> object: ...

    async def append_permission_decision(
        self, session_id: str, tool_call_id: str, decision: PermissionDecision
    ) -> None:
        """Settle by correlation idempotently; cancelled denial dominates an in-flight write."""
        ...


PermissionDecider = Callable[
    [object, list[schema.PermissionOption]],
    PermissionDecision | Awaitable[PermissionDecision],
]


def _unsupported(capability: str) -> RequestError:
    return RequestError(
        _METHOD_NOT_FOUND,
        f"ACP client capability is unsupported and was not advertised: {capability}",
    )


def _consume_detached_task_result(task: asyncio.Task[None]) -> None:
    """Retrieve a detached settlement result so asyncio never emits an unhandled warning."""
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


class AgentDeckAcpClient:
    """Official-SDK Client callbacks with injected persistence and human decision edges."""

    def __init__(self, sink: AcpLedgerSink, decide: PermissionDecider) -> None:
        self._sink = sink
        self._decide = decide
        self._permission_pending = False
        self._callback_errors: dict[int, Exception] = {}
        self._update_count = 0
        self._update_phase = "prompt"
        self._phase_generation = 0
        self._phase_lock = asyncio.Lock()
        self._update_settled: Callable[[], None] = lambda: None

    def set_update_settled_callback(self, callback: Callable[[], None]) -> None:
        self._update_settled = callback

    async def switch_update_phase(self, phase: str) -> int:
        if phase not in {"load_replay", "prompt", "sealed"}:
            raise ValueError("invalid ACP update phase")
        async with self._phase_lock:
            self._phase_generation += 1
            self._update_phase = phase
            return self._phase_generation

    async def acquire_prompt_phase(self) -> int:
        await self._phase_lock.acquire()
        self._phase_generation += 1
        self._update_phase = "prompt"
        return self._phase_generation

    def release_phase_switch(self) -> None:
        self._phase_lock.release()

    @property
    def update_count(self) -> int:
        return self._update_count

    def take_callback_error(self) -> Exception | None:
        if not self._callback_errors:
            return None
        generation = min(self._callback_errors)
        return self._callback_errors.pop(generation)

    async def _settle_cancelled(self, session_id: str, tool_call_id: str) -> None:
        """Bound and shield the idempotent fail-closed settlement write."""
        task = asyncio.create_task(self._sink.append_permission_decision(
            session_id, tool_call_id, PermissionDecision.cancelled("cancelled")
        ))
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=_CANCEL_SETTLEMENT_TIMEOUT_SECONDS
            )
        except TimeoutError:
            task.cancel()
            done, pending = await asyncio.wait({task}, timeout=0.1)
            for completed in done:
                _consume_detached_task_result(completed)
            for detached in pending:
                detached.add_done_callback(_consume_detached_task_result)
            raise RuntimeError("ACP permission cancellation settlement timed out") from None

    async def session_update(self, session_id: str, update: object, **_: Any) -> None:
        captured_generation = self._phase_generation
        captured_phase = self._update_phase
        self._update_count += 1
        # Deliberate admission boundary: callbacks already dispatched by the SDK retain
        # their captured generation even if a later lifecycle opens before they run.
        try:
            kind, payload = map_session_update(update)
            async with self._phase_lock:
                if (
                    captured_generation != self._phase_generation
                    or captured_phase != self._update_phase
                    or captured_phase not in {"load_replay", "prompt"}
                ):
                    error = RuntimeError("unexpected ACP update during sealed phase or stale generation")
                    self._callback_errors.setdefault(captured_generation, error)
                    raise error
                await self._sink.append_update(session_id, kind, payload)
        except Exception as error:
            self._callback_errors.setdefault(captured_generation, error)
            raise
        finally:
            self._update_settled()

    async def request_permission(
        self,
        session_id: str,
        tool_call: schema.ToolCallUpdate,
        options: list[schema.PermissionOption],
        **_: Any,
    ) -> schema.RequestPermissionResponse:
        if not options:
            raise ValueError("permission options must not be empty")
        if any(not isinstance(option, schema.PermissionOption) for option in options):
            raise TypeError("permission options must be typed PermissionOption values")
        option_ids = [option.option_id for option in options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("permission option ids must be unique")
        if self._permission_pending:
            raise RuntimeError("an ACP permission request is already pending")

        request = schema.RequestPermissionRequest(
            sessionId=session_id, toolCall=tool_call, options=options
        )
        self._permission_pending = True
        pending_write_started = False
        try:
            pending_write_started = True
            pending = await self._sink.append_permission(
                session_id, summarize_permission(request), options
            )
            try:
                candidate = self._decide(pending, options)
                decision = await candidate if inspect.isawaitable(candidate) else candidate
            except (TimeoutError, EOFError, KeyboardInterrupt) as error:
                reason = {
                    TimeoutError: "timeout",
                    EOFError: "eof",
                    KeyboardInterrupt: "ctrl_c",
                }[type(error)]
                decision = PermissionDecision.cancelled(reason)
            if not isinstance(decision, PermissionDecision):
                decision = PermissionDecision.cancelled("invalid_decision")

            offered = {option.option_id: option for option in options}
            option = offered.get(decision.option_id) if decision.option_id is not None else None
            if decision.option_id is not None and (
                option is None or option.kind not in _SUPPORTED_PERMISSION_KINDS
            ):
                decision = PermissionDecision.cancelled("invalid_option")
            elif option is not None:
                decision = decision.settled_for(option)

            await self._sink.append_permission_decision(
                session_id, tool_call.tool_call_id, decision
            )
            return decision.to_acp_response()
        except asyncio.CancelledError:
            if pending_write_started:
                await self._settle_cancelled(session_id, tool_call.tool_call_id)
            raise
        finally:
            self._permission_pending = False

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> schema.WriteTextFileResponse | None:
        raise _unsupported("filesystem.write_text_file")

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> schema.ReadTextFileResponse:
        raise _unsupported("filesystem.read_text_file")

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[schema.EnvVariable] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> schema.CreateTerminalResponse:
        raise _unsupported("terminal.create")

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> schema.TerminalOutputResponse:
        raise _unsupported("terminal.output")

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> schema.ReleaseTerminalResponse | None:
        raise _unsupported("terminal.release")

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> schema.WaitForTerminalExitResponse:
        raise _unsupported("terminal.wait_for_exit")

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> schema.KillTerminalResponse | None:
        raise _unsupported("terminal.kill")

    async def create_elicitation(
        self, message: str, mode: schema.ElicitationMode, **kwargs: Any
    ) -> schema.CreateElicitationResponse:
        raise _unsupported("elicitation.create")

    async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
        raise _unsupported("elicitation.complete")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise _unsupported("extension method")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        raise _unsupported("extension notification")

    def on_connect(self, conn: Agent) -> None:
        # Connection notification is part of SDK wiring, not an advertised capability.
        return None
