from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path
import re
from types import TracebackType
from typing import Any

from acp import schema, spawn_agent_process

from agentdeck import __version__
from agentdeck.runtime.acp_mapping import MAX_ACP_MESSAGE_BYTES


ACP_PROTOCOL_VERSION = 1
MAX_ACP_STDERR_BYTES = 64 * 1024
_SECRET_LINE = re.compile(
    rb"(?i)(?:secret|token|password|api[_-]?key|authorization)[^\r\n]*"
)


class AcpTransportError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.cancel_diagnostic: AcpCancelDiagnostic | None = None


class AcpProtocolVersionError(AcpTransportError):
    pass


class AcpRequestTimeout(AcpTransportError):
    pass


class AcpAmbiguousOutcome(AcpTransportError):
    """The Agent disappeared during an active request; completion is unknowable."""


@dataclass(frozen=True)
class AcpInitializeResult:
    protocol_version: int
    client_capabilities: dict[str, object]


@dataclass(frozen=True)
class AcpCancelDiagnostic:
    session_id: str
    status: str


@dataclass(frozen=True)
class AcpSessionResult:
    native_session_id: str


@dataclass(frozen=True)
class AcpPromptResult:
    native_session_id: str
    stop_reason: str
    outcome: str
    disconnect_reason: str


class AcpTransport:
    """One bounded foreground ACP SDK connection and its exact child process."""

    def __init__(
        self,
        argv: tuple[str, ...],
        workspace: str | Path,
        client: object,
        *,
        request_timeout: float = 30.0,
        cancel_timeout: float = 1.0,
        terminate_grace: float = 2.0,
        kill_grace: float = 2.0,
    ) -> None:
        if type(argv) is not tuple or not argv:
            raise ValueError("argv must be a non-empty tuple")
        if any(type(arg) is not str for arg in argv):
            raise TypeError("argv elements must be exact strings")
        if any(not arg for arg in argv):
            raise ValueError("argv elements must be non-empty strings")
        for name, value in (
            ("request_timeout", request_timeout),
            ("cancel_timeout", cancel_timeout),
            ("terminate_grace", terminate_grace),
            ("kill_grace", kill_grace),
        ):
            if type(value) not in (int, float) or value <= 0:
                raise ValueError(f"{name} must be positive")
        self._argv = argv
        self._workspace = Path(workspace).resolve(strict=True)
        self._client = client
        self._request_timeout = float(request_timeout)
        self._cancel_timeout = float(cancel_timeout)
        self._terminate_grace = float(terminate_grace)
        self._kill_grace = float(kill_grace)
        self._context: Any = None
        self._connection: Any = None
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[bytes] | None = None
        self._stderr = b""
        self._closed = False

    @property
    def child_pid(self) -> int:
        if self._process is None:
            raise RuntimeError("ACP child has not started")
        return self._process.pid

    @property
    def stderr_diagnostic(self) -> str:
        redacted = _SECRET_LINE.sub(b"[REDACTED]", self._stderr[:MAX_ACP_STDERR_BYTES])
        return redacted.decode("utf-8", errors="replace")

    async def _capture_stderr(self) -> bytes:
        assert self._process is not None
        stream = self._process.stderr
        if stream is None:
            return b""
        captured = bytearray()
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            remaining = MAX_ACP_STDERR_BYTES - len(captured)
            if remaining > 0:
                captured.extend(chunk[:remaining])
        return bytes(captured)

    async def _start(self) -> None:
        if self._connection is not None:
            return
        if self._closed:
            raise RuntimeError("ACP transport is closed")
        self._context = spawn_agent_process(
            self._client,
            self._argv[0],
            *self._argv[1:],
            cwd=self._workspace,
            transport_kwargs={
                "limit": MAX_ACP_MESSAGE_BYTES,
                # AcpTransport performs the two-stage shutdown before context exit.
                "shutdown_timeout": self._kill_grace,
            },
        )
        try:
            self._connection, self._process = await self._context.__aenter__()
        except Exception as error:
            raise AcpTransportError("failed to start ACP stdio transport") from error
        self._stderr_task = asyncio.create_task(self._capture_stderr())

    async def _request(self, operation: object, *, active_prompt: bool = False) -> Any:
        try:
            return await asyncio.wait_for(operation, timeout=self._request_timeout)  # type: ignore[arg-type]
        except TimeoutError as error:
            raise AcpRequestTimeout("ACP request timed out") from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if active_prompt and self._process is not None and self._process.returncode is not None:
                raise AcpAmbiguousOutcome(
                    "ACP Agent reached EOF during an active prompt; outcome is ambiguous"
                ) from error
            raise AcpTransportError("ACP stdio request failed") from error

    async def initialize(self) -> AcpInitializeResult:
        await self._start()
        capabilities = schema.ClientCapabilities(fs=None, terminal=False)
        info = schema.Implementation(name="agentdeck", title="AgentDeck", version=__version__)
        response = await self._request(
            self._connection.initialize(
                protocol_version=ACP_PROTOCOL_VERSION,
                client_capabilities=capabilities,
                client_info=info,
            )
        )
        if response.protocol_version != ACP_PROTOCOL_VERSION:
            await self.close()
            raise AcpProtocolVersionError(
                f"unsupported ACP protocol version {response.protocol_version}; expected 1"
            )
        return AcpInitializeResult(
            protocol_version=response.protocol_version,
            client_capabilities={"fs": None, "terminal": False},
        )

    async def new_session(self) -> AcpSessionResult:
        if self._connection is None:
            raise RuntimeError("ACP transport is not initialized")
        response = await self._request(
            self._connection.new_session(cwd=str(self._workspace), mcp_servers=[])
        )
        return AcpSessionResult(native_session_id=response.session_id)

    async def prompt(self, native_session_id: str, text: str) -> AcpPromptResult:
        if self._connection is None:
            raise RuntimeError("ACP transport is not initialized")
        if type(native_session_id) is not str or not native_session_id:
            raise ValueError("native_session_id must be a non-empty string")
        if type(text) is not str or not text:
            raise ValueError("prompt text must be a non-empty string")
        operation = self._connection.prompt(
            session_id=native_session_id,
            prompt=[schema.TextContentBlock(type="text", text=text)],
        )
        try:
            response = await self._request(operation, active_prompt=True)
        except AcpRequestTimeout as error:
            error.cancel_diagnostic = await self._send_bounded_cancel(native_session_id)
            raise
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    self._connection.cancel(session_id=native_session_id), timeout=1.0
                )
            raise
        callback_error = getattr(self._client, "take_callback_error", lambda: None)()
        if callback_error is not None:
            error = AcpTransportError("ACP client callback rejected a streamed update")
            error.cancel_diagnostic = await self._send_bounded_cancel(native_session_id)
            raise error from callback_error
        return AcpPromptResult(
            native_session_id=native_session_id,
            stop_reason=response.stop_reason,
            outcome="completed" if response.stop_reason == "end_turn" else "not_completed",
            disconnect_reason="clean_exit",
        )

    async def _send_bounded_cancel(self, native_session_id: str) -> AcpCancelDiagnostic:
        try:
            await asyncio.wait_for(
                self._connection.cancel(session_id=native_session_id),
                timeout=self._cancel_timeout,
            )
        except TimeoutError:
            status = "timed_out"
        except Exception:
            status = "failed"
        else:
            status = "sent"
        return AcpCancelDiagnostic(session_id=native_session_id, status=status)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if self._connection is not None:
            with contextlib.suppress(Exception):
                await self._connection.close()
        if process is not None and process.stdin is not None:
            with contextlib.suppress(Exception):
                process.stdin.close()
        if process is not None and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=self._terminate_grace)
                except TimeoutError:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=self._kill_grace)
        if self._context is not None:
            with contextlib.suppress(Exception):
                await self._context.__aexit__(None, None, None)
        if self._stderr_task is not None:
            with contextlib.suppress(Exception):
                self._stderr = await self._stderr_task

    async def __aenter__(self) -> AcpTransport:
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
