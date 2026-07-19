"""Lazy bounded stdio transport built on the official ACP Python SDK."""
from __future__ import annotations
import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final
from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.helpers import embedded_text_resource, resource_block
from acp.schema import (
    AgentMessageChunk, AllowedOutcome, DeniedOutcome,
    EmbeddedResourceContentBlock, InitializeResponse, LoadSessionResponse,
    NewSessionResponse, PermissionOption, PromptResponse,
    RequestPermissionResponse, ResumeSessionResponse, TextContentBlock,
    TextResourceContents, ToolCallProgress, ToolCallStart, ToolCallUpdate,
)
from agentdeck.adapters.adapter_readiness import merged_environment
from agentdeck.ports.transport import (
    TransportArtifact, TransportCapabilities, TransportDeadline, TransportFailure,
    TransportFailureCode, TransportPermissionDecision,
    TransportPermissionRequest, TransportPromptPart, TransportPromptResult,
    TransportSession, TransportUpdate, TransportUpdateKind,
    close_transport_awaitable, transport_argv, transport_byte_bound, transport_project_root, transport_timeout,
)
_DEFAULT_MAX_BYTES: Final = 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS: Final = 30.0
_MAX_TOTAL_MULTIPLIER: Final = 8
_SENTINEL: Final = object()
ClientFactory = Callable[..., AbstractAsyncContextManager[object]]
@dataclass
class _PendingPermission:
    request_id: str
    options: tuple[PermissionOption, ...]
    future: asyncio.Future[RequestPermissionResponse]
@asynccontextmanager
async def _spawn_client(
    callback: object, command: tuple[str, ...], project_root: str,
    max_bytes: int, timeout_seconds: float, environment: Mapping[str, str],
) -> AsyncIterator[object]:
    transport_kwargs = {
        "limit": max_bytes,
        "stderr": asyncio.subprocess.DEVNULL,
        "shutdown_timeout": min(timeout_seconds, 2.0),
    }
    async with spawn_agent_process(
        callback,
        command[0],
        *command[1:],
        env=dict(environment),
        cwd=project_root,
        transport_kwargs=transport_kwargs,
        receive_timeout=timeout_seconds,
        observers=[getattr(callback, "observe")],
    ) as (connection, _process):
        yield connection
class _ACPClient:
    def __init__(self, owner: "ACPStdioTransport") -> None:
        self._owner = owner
    async def session_update(
        self, session_id: str, update: object, **_kwargs: Any
    ) -> None:
        try:
            self._owner._accept_update(session_id, update)
        finally:
            self._owner._handled_update()
    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **_kwargs: Any,
    ) -> RequestPermissionResponse:
        return await self._owner._accept_permission(session_id, tool_call, options)
    def observe(self, event: object) -> None:
        direction = getattr(getattr(event, "direction", None), "value", None)
        if direction != "incoming":
            return
        message = getattr(event, "message", None)
        if type(message) is dict and message.get("method") == "session/update":
            self._owner._observed_update()
        try:
            import json
            size = len(json.dumps(message, separators=(",", ":")).encode("utf-8"))
        except Exception:
            self._owner._set_failure(TransportFailureCode.PROTOCOL_MISMATCH)
            return
        self._owner._consume(size)
class ACPStdioTransport:
    """One lazy ACP process connection and one independent Agent session."""
    def __init__(
        self,
        command: Sequence[str],
        *,
        project_root: str,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        client_factory: ClientFactory = _spawn_client,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if not callable(client_factory):
            raise TypeError("client_factory must be callable")
        self.command = transport_argv(command)
        self.project_root = transport_project_root(project_root)
        self.max_bytes = transport_byte_bound(max_bytes)
        self.timeout_seconds = transport_timeout(timeout_seconds)
        self.environment = merged_environment(environment)
        self._client_factory = client_factory
        self._deadline: TransportDeadline | None = None
        self._manager: AbstractAsyncContextManager[object] | None = None
        self._connection: object | None = None
        self._callback = _ACPClient(self)
        self._capabilities: TransportCapabilities | None = None
        self._session: TransportSession | None = None
        self._updates: asyncio.Queue[TransportUpdate | object] = asyncio.Queue()
        self._pending: _PendingPermission | None = None
        self._sequence = 0
        self._permission_count = 0
        self._total_bytes = 0
        self._failure: TransportFailureCode | None = None
        self._observed_update_count = 0
        self._handled_update_count = 0
        self._updates_handled = asyncio.Event()
        self._prompt_started = False
        self._stream_started = False
        self._entered = False
        self._closed = False
    async def __aenter__(self) -> "ACPStdioTransport":
        if self._entered or self._closed:
            raise ValueError("ACP transport lifecycle is invalid")
        self._entered = True
        self._deadline = TransportDeadline(self.timeout_seconds)
        return self
    async def __aexit__(self, exc_type, _exc, _tb) -> None:
        try:
            await self.close()
        except TransportFailure:
            if exc_type is None:
                raise
    async def initialize(self) -> TransportCapabilities:
        if self._capabilities is not None:
            raise ValueError("ACP transport is already initialized")
        connection = await self._connect()
        response = await self._call(connection, "initialize",
            TransportFailureCode.INITIALIZATION_FAILED, PROTOCOL_VERSION)
        if type(response) is not InitializeResponse or response.protocol_version != PROTOCOL_VERSION:
            raise TransportFailure(TransportFailureCode.PROTOCOL_MISMATCH)
        advertised = response.agent_capabilities
        prompt = None if advertised is None else advertised.prompt_capabilities
        session = None if advertised is None else advertised.session_capabilities
        self._capabilities = TransportCapabilities(
            protocol_version=response.protocol_version,
            embedded_context=bool(prompt and prompt.embedded_context),
            load_session=bool(advertised and advertised.load_session),
            resume_session=bool(
                advertised and advertised.load_session
                or session and session.resume is not None
            ),
        )
        return self._capabilities
    async def new_session(self) -> TransportSession:
        self._require_initialized()
        if self._session is not None:
            raise ValueError("ACP transport already owns a session")
        connection = self._require_connection()
        response = await self._call(connection, "new_session",
            TransportFailureCode.SESSION_FAILED, cwd=self.project_root)
        if type(response) is not NewSessionResponse:
            raise TransportFailure(TransportFailureCode.PROTOCOL_MISMATCH)
        try:
            model, version = _session_provenance(response.field_meta)
            self._session = TransportSession(response.session_id, model, version)
        except (TypeError, ValueError):
            raise TransportFailure(TransportFailureCode.PROTOCOL_MISMATCH) from None
        return self._session
    async def resume_session(self, session: TransportSession) -> TransportSession:
        if type(session) is not TransportSession:
            raise ValueError("ACP transport session is invalid")
        if self._session is not None and session != self._session:
            raise ValueError("ACP transport session is invalid")
        capabilities = self._require_initialized()
        if not capabilities.resume_session:
            raise TransportFailure(TransportFailureCode.CAPABILITY_MISSING)
        connection = self._require_connection()
        if capabilities.load_session:
            method = "load_session"
            arguments = ()
            keywords = {"cwd": self.project_root, "session_id": session.session_id}
            expected = LoadSessionResponse
        else:
            method = "resume_session"
            arguments = (session.session_id,)
            keywords = {"cwd": self.project_root}
            expected = ResumeSessionResponse
        fresh = self._session is None
        self._session = session
        try:
            response = await self._call(connection, method,
                TransportFailureCode.SESSION_FAILED, *arguments, **keywords)
        except BaseException:
            if fresh:
                self._session = None
            raise
        if type(response) is not expected:
            if fresh:
                self._session = None
            raise TransportFailure(TransportFailureCode.PROTOCOL_MISMATCH)
        return session
    async def prompt(
        self, session: TransportSession, parts: tuple[TransportPromptPart, ...]
    ) -> TransportPromptResult:
        self._require_session(session)
        if self._prompt_started or type(parts) is not tuple or not parts:
            raise ValueError("ACP prompt lifecycle is invalid")
        if any(type(part) is not TransportPromptPart for part in parts):
            raise TypeError("ACP prompt parts must be TransportPromptPart values")
        self._prompt_started = True
        blocks = []
        for part in parts:
            if part.kind == "text":
                blocks.append(TextContentBlock(type="text", text=part.text))
            else:
                blocks.append(resource_block(embedded_text_resource(
                    part.uri, part.text, mime_type=part.mime_type
                )))
        connection = self._require_connection()
        try:
            response = await self._call(connection, "prompt",
                TransportFailureCode.PROMPT_FAILED, session.session_id, blocks)
            await self._wait_for_updates()
        finally:
            self._updates.put_nowait(_SENTINEL)
        self._raise_stored_failure()
        if type(response) is not PromptResponse:
            raise TransportFailure(TransportFailureCode.PROTOCOL_MISMATCH)
        return TransportPromptResult(response.stop_reason)
    def stream_updates(
        self, session: TransportSession
    ) -> AsyncIterator[TransportUpdate]:
        self._require_session(session)
        if self._stream_started:
            raise ValueError("ACP update stream lifecycle is invalid")
        self._stream_started = True
        return self._stream()
    async def respond_permission(
        self, session: TransportSession, decision: TransportPermissionDecision
    ) -> None:
        self._require_session(session)
        if type(decision) is not TransportPermissionDecision:
            raise TypeError("permission decision is invalid")
        pending = self._pending
        if pending is None or pending.request_id != decision.request_id:
            raise TransportFailure(TransportFailureCode.PERMISSION_INVALID)
        desired = "allow_" if decision.allowed else "reject_"
        selected = next(
            (option for option in pending.options if option.kind.startswith(desired)),
            None,
        )
        outcome = (
            AllowedOutcome(outcome="selected", option_id=selected.option_id)
            if selected is not None
            else DeniedOutcome(outcome="cancelled")
        )
        self._pending = None
        if not pending.future.done():
            pending.future.set_result(RequestPermissionResponse(outcome=outcome))
    async def cancel(self, session: TransportSession) -> None:
        self._require_session(session)
        connection = self._require_connection()
        self._clear_pending()
        await self._call(connection, "cancel",
            TransportFailureCode.CANCELLATION_FAILED, session.session_id)
    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._clear_pending()
        manager = self._manager
        self._manager = None
        self._connection = None
        if manager is None:
            return
        await self._call(manager, "__aexit__",
            TransportFailureCode.DISCONNECTED, None, None, None)
    async def _connect(self) -> object:
        if not self._entered or self._closed:
            raise ValueError("ACP transport must be entered before use")
        if self._connection is None:
            factory_failed = False
            try:
                manager = self._client_factory(
                    self._callback, self.command, self.project_root,
                    self.max_bytes, self._budget(), self.environment,
                )
            except Exception:
                factory_failed = True
                manager = None
            if factory_failed or manager is None:
                raise TransportFailure(TransportFailureCode.INITIALIZATION_FAILED)
            self._manager = manager
            self._connection = await self._call(
                manager, "__aenter__", TransportFailureCode.INITIALIZATION_FAILED)
            if self._connection is None:
                raise TransportFailure(TransportFailureCode.INITIALIZATION_FAILED) from None
        return self._connection
    async def _invoke(self, awaitable, code: TransportFailureCode) -> object:
        failure: TransportFailureCode | None = None
        task_failed = False
        try:
            task = asyncio.ensure_future(awaitable)
        except BaseException:
            task_failed = True
            task = None
        if task_failed or task is None:
            close_transport_awaitable(awaitable)
            raise TransportFailure(code)
        try:
            done, _pending = await asyncio.wait((task,), timeout=self._budget())
            if not done:
                task.cancel()
                task.add_done_callback(lambda item: None if item.cancelled() else item.exception())
                raise asyncio.TimeoutError
            result = task.result()
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(lambda item: None if item.cancelled() else item.exception())
            raise
        except asyncio.TimeoutError:
            failure = TransportFailureCode.TIMEOUT
        except Exception:
            failure = code
        if self._failure is not None:
            failure = self._failure
        if failure is not None:
            raise TransportFailure(failure) from None
        return result
    async def _call(self, target: object, method: str, code: TransportFailureCode,
        *args: object, **kwargs: object,
    ) -> object:
        failed = False
        try:
            awaitable = getattr(target, method)(*args, **kwargs)
        except Exception:
            failed = True
            awaitable = None
        if failed:
            raise TransportFailure(code)
        return await self._invoke(awaitable, code)
    def _accept_update(self, session_id: str, update: object) -> None:
        session = self._session
        if session is None or session.session_id != session_id:
            self._set_failure(TransportFailureCode.PROTOCOL_MISMATCH)
            raise TransportFailure(TransportFailureCode.PROTOCOL_MISMATCH)
        self._consume_model(update)
        self._sequence += 1
        if type(update) is AgentMessageChunk:
            content = update.content
            if type(content) is TextContentBlock:
                item = TransportUpdate(
                    session_id, self._sequence, TransportUpdateKind.MESSAGE,
                    text=content.text,
                )
            elif (
                type(content) is EmbeddedResourceContentBlock
                and type(content.resource) is TextResourceContents
                and content.resource.mime_type is not None
            ):
                artifact = TransportArtifact(
                    content.resource.uri,
                    content.resource.mime_type,
                    content.resource.text,
                )
                item = TransportUpdate(
                    session_id, self._sequence, TransportUpdateKind.ARTIFACT,
                    artifact=artifact,
                )
            else:
                item = TransportUpdate(
                    session_id, self._sequence, TransportUpdateKind.PROGRESS,
                )
        elif type(update) in {ToolCallStart, ToolCallProgress}:
            item = TransportUpdate(
                session_id, self._sequence, TransportUpdateKind.TOOL,
            )
        else:
            item = TransportUpdate(
                session_id, self._sequence, TransportUpdateKind.PROGRESS,
            )
        self._updates.put_nowait(item)
    async def _accept_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
    ) -> RequestPermissionResponse:
        session = self._session
        if (
            session is None
            or session.session_id != session_id
            or self._pending is not None
            or type(tool_call) is not ToolCallUpdate
            or type(options) is not list
            or not options
            or any(type(option) is not PermissionOption for option in options)
        ):
            self._set_failure(TransportFailureCode.PERMISSION_INVALID)
            raise TransportFailure(TransportFailureCode.PERMISSION_INVALID)
        self._consume_model(tool_call)
        for option in options:
            self._consume_model(option)
        self._permission_count += 1
        request_id = f"permission-{self._permission_count}"
        future = asyncio.get_running_loop().create_future()
        self._pending = _PendingPermission(request_id, tuple(options), future)
        self._sequence += 1
        request = TransportPermissionRequest(
            request_id=request_id,
            tool_call_id=tool_call.tool_call_id,
            options=tuple((item.option_id, item.kind) for item in options),
        )
        self._updates.put_nowait(TransportUpdate(
            session_id, self._sequence, TransportUpdateKind.PERMISSION,
            permission=request,
        ))
        return await future
    async def _stream(self) -> AsyncIterator[TransportUpdate]:
        while True:
            item = await self._updates.get()
            if item is _SENTINEL:
                return
            assert type(item) is TransportUpdate
            yield item
    def _consume_model(self, value: object) -> None:
        serializer = getattr(value, "model_dump_json", None)
        if not callable(serializer):
            self._set_failure(TransportFailureCode.PROTOCOL_MISMATCH)
            raise TransportFailure(TransportFailureCode.PROTOCOL_MISMATCH)
        try:
            size = len(serializer(by_alias=True).encode("utf-8", "strict"))
        except Exception:
            self._set_failure(TransportFailureCode.PROTOCOL_MISMATCH)
            raise TransportFailure(TransportFailureCode.PROTOCOL_MISMATCH) from None
        self._consume(size)
    def _consume(self, size: int) -> None:
        self._total_bytes += size
        if size > self.max_bytes or self._total_bytes > self.max_bytes * _MAX_TOTAL_MULTIPLIER:
            self._set_failure(TransportFailureCode.RESPONSE_OVERSIZE)
            raise TransportFailure(TransportFailureCode.RESPONSE_OVERSIZE)
    def _set_failure(self, code: TransportFailureCode) -> None:
        if self._failure is None:
            self._failure = code
    def _observed_update(self) -> None:
        self._observed_update_count += 1
        self._updates_handled.clear()
    def _handled_update(self) -> None:
        self._handled_update_count += 1
        if self._handled_update_count >= self._observed_update_count:
            self._updates_handled.set()
    async def _wait_for_updates(self) -> None:
        while self._handled_update_count < self._observed_update_count:
            await self._invoke(
                self._updates_handled.wait(), TransportFailureCode.TIMEOUT
            )
    def _budget(self) -> float:
        if self._deadline is None:
            raise ValueError("ACP transport must be entered before use")
        return self._deadline.remaining()
    def _clear_pending(self) -> None:
        pending = self._pending
        self._pending = None
        if pending is not None and not pending.future.done():
            pending.future.cancel()
    def _raise_stored_failure(self) -> None:
        if self._failure is not None:
            raise TransportFailure(self._failure) from None
    def _require_initialized(self) -> TransportCapabilities:
        if self._capabilities is None:
            raise ValueError("ACP transport is not initialized")
        return self._capabilities
    def _require_connection(self):
        if self._connection is None or self._closed:
            raise ValueError("ACP transport is not connected")
        return self._connection
    def _require_session(self, session: TransportSession) -> TransportSession:
        if type(session) is not TransportSession or session != self._session:
            raise ValueError("ACP transport session is invalid")
        return session
def _session_provenance(metadata: object) -> tuple[str | None, str | None]:
    if metadata is None:
        return None, None
    if type(metadata) is not dict:
        raise ValueError("ACP session provenance is invalid")
    value = metadata.get("agentdeck")
    if value is None:
        return None, None
    if type(value) is not dict or set(value) != {"resolved_model", "server_version"}:
        raise ValueError("ACP session provenance is invalid")
    model, version = value["resolved_model"], value["server_version"]
    TransportSession("provenance", model, version)
    return model, version
__all__ = ["ACPStdioTransport"]
