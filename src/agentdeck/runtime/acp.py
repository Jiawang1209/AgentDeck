from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import os
from pathlib import Path
import re
from types import TracebackType
from typing import Any

from acp import schema, spawn_agent_process

from agentdeck import __version__
from agentdeck.runtime.acp_mapping import MAX_ACP_MESSAGE_BYTES


ACP_PROTOCOL_VERSION = 1
MAX_ACP_STDERR_BYTES = 64 * 1024
ACP_TRANSPORT_STATES = (
    "new", "starting", "open", "cleaning", "cleanup_incomplete", "closed"
)
ACP_INHERITED_ENV_VARS = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy",
    "http_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
)
_SECRET_LINE = re.compile(
    rb"(?i)(?:secret|token|password|api[_-]?key|authorization)[^\r\n]*"
)


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _consume_task(task: asyncio.Task[Any]) -> None:
    with contextlib.suppress(BaseException):
        task.exception()


async def _hard_bound(operation: object, timeout: float) -> str:
    task = asyncio.create_task(operation)  # type: ignore[arg-type]
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        try:
            task.result()
        except BaseException:
            return "failed"
        return "completed"
    task.cancel()
    task.add_done_callback(_consume_task)
    return "timed_out"


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


@dataclass(frozen=True)
class AcpCleanupDiagnostic:
    stage: str
    status: str


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
        close_grace: float = 5.0,
        terminate_grace: float = 2.0,
        kill_grace: float = 2.0,
        test_env: dict[str, str] | None = None,
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
            ("close_grace", close_grace),
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
        self._close_grace = float(close_grace)
        self._terminate_grace = float(terminate_grace)
        self._kill_grace = float(kill_grace)
        self._context: Any = None
        self._connection: Any = None
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[bytes] | None = None
        self._stderr = b""
        self._lifecycle_state = "new"
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_incomplete = False
        self._cleanup_diagnostics: list[AcpCleanupDiagnostic] = []
        self._cleanup_completed_stages: set[str] = set()
        if test_env is not None and (
            type(test_env) is not dict
            or any(type(k) is not str or type(v) is not str for k, v in test_env.items())
        ):
            raise TypeError("test_env must contain exact string keys and values")
        self._spawn_env = {
            key: os.environ[key] for key in ACP_INHERITED_ENV_VARS if key in os.environ
        }
        self._spawn_env.update({
            key: value for key, value in os.environ.items() if key.startswith("CLAUDE_CODE")
        })
        if test_env:
            self._spawn_env.update(test_env)
        self._diagnostic_redactions = tuple(
            value.encode("utf-8") for value in self._spawn_env.values() if value
        )

    @property
    def child_pid(self) -> int:
        if self._process is None:
            raise RuntimeError("ACP child has not started")
        return self._process.pid

    @property
    def lifecycle_state(self) -> str:
        return self._lifecycle_state

    @property
    def stderr_diagnostic(self) -> str:
        redacted = _SECRET_LINE.sub(b"[REDACTED]", self._stderr[:MAX_ACP_STDERR_BYTES])
        for value in self._diagnostic_redactions:
            redacted = redacted.replace(value, b"[REDACTED]")
        return redacted.decode("utf-8", errors="replace")

    @property
    def cleanup_diagnostics(self) -> tuple[AcpCleanupDiagnostic, ...]:
        return tuple(self._cleanup_diagnostics)

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
        if self._lifecycle_state != "new":
            raise RuntimeError(
                f"ACP transport cannot start from {self._lifecycle_state} state"
            )
        self._lifecycle_state = "starting"
        self._context = spawn_agent_process(
            self._client,
            self._argv[0],
            *self._argv[1:],
            cwd=self._workspace,
            env=self._spawn_env,
            transport_kwargs={
                "limit": MAX_ACP_MESSAGE_BYTES,
                # AcpTransport performs the two-stage shutdown before context exit.
                "shutdown_timeout": self._kill_grace,
            },
        )
        try:
            self._connection, self._process = await self._context.__aenter__()
        except Exception as error:
            self._lifecycle_state = "closed"
            raise AcpTransportError("failed to start ACP stdio transport") from error
        self._stderr_task = asyncio.create_task(self._capture_stderr())
        self._lifecycle_state = "open"

    async def _request(self, operation: object, *, eof_is_ambiguous: bool = False) -> Any:
        try:
            return await asyncio.wait_for(operation, timeout=self._request_timeout)  # type: ignore[arg-type]
        except TimeoutError as error:
            raise AcpRequestTimeout("ACP request timed out") from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if eof_is_ambiguous and await self._connection_ended(error):
                raise AcpAmbiguousOutcome(
                    "ACP Agent reached EOF during an active operation; outcome is ambiguous"
                ) from error
            raise AcpTransportError("ACP stdio request failed") from error

    async def initialize(self) -> AcpInitializeResult:
        await self._start()
        capabilities = schema.ClientCapabilities(fs=None, terminal=False)
        info = schema.Implementation(name="agentdeck", title="AgentDeck", version=__version__)
        try:
            response = await self._request(
                self._connection.initialize(
                    protocol_version=ACP_PROTOCOL_VERSION,
                    client_capabilities=capabilities,
                    client_info=info,
                ),
                eof_is_ambiguous=True,
            )
            if response.protocol_version != ACP_PROTOCOL_VERSION:
                raise AcpProtocolVersionError(
                    f"unsupported ACP protocol version {response.protocol_version}; expected 1"
                )
        except BaseException:
            try:
                await asyncio.shield(self._ensure_cleanup())
            except BaseException:
                # The operation error remains primary; cleanup diagnostics are inspectable.
                pass
            raise
        return AcpInitializeResult(
            protocol_version=response.protocol_version,
            client_capabilities={"fs": None, "terminal": False},
        )

    async def _connection_ended(self, error: Exception) -> bool:
        process = self._process
        if process is not None and process.returncode is not None:
            return True
        messages = " ".join(str(item).lower() for item in _exception_chain(error))
        if "connection closed" in messages or "unexpected eof" in messages:
            return True
        if process is not None:
            try:
                await asyncio.wait_for(asyncio.shield(process.wait()), timeout=0.05)
                return True
            except TimeoutError:
                pass
        return False

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
            response = await self._request(operation, eof_is_ambiguous=True)
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

    def _ensure_cleanup(self) -> asyncio.Task[None]:
        if self._lifecycle_state == "new":
            self._lifecycle_state = "closed"
            return asyncio.create_task(asyncio.sleep(0))
        if self._lifecycle_state == "closed":
            return asyncio.create_task(asyncio.sleep(0))
        if self._cleanup_task is None or (
            self._cleanup_task.done() and self._cleanup_incomplete
        ):
            self._cleanup_task = asyncio.create_task(self._cleanup())
        return self._cleanup_task

    async def close(self) -> None:
        await asyncio.shield(self._ensure_cleanup())

    async def _cleanup(self) -> None:
        self._lifecycle_state = "cleaning"
        self._cleanup_incomplete = False
        process = self._process
        if (
            self._connection is not None
            and "connection_close" not in self._cleanup_completed_stages
            and (process is None or process.returncode is None)
        ):
            self._record_cleanup(
                "connection_close",
                await _hard_bound(self._connection.close(), self._cancel_timeout),
            )
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except Exception:
                self._record_cleanup("stdin_close", "failed")
        if process is not None and process.returncode is None:
            graceful = await _hard_bound(process.wait(), self._close_grace)
            self._record_cleanup("process_wait", graceful)
            if graceful != "completed" and process.returncode is None:
                self._signal_process(process, "terminate")
                terminated = await _hard_bound(process.wait(), self._terminate_grace)
                self._record_cleanup("process_terminate_wait", terminated)
                if terminated != "completed" and process.returncode is None:
                    self._signal_process(process, "kill")
                    killed = await _hard_bound(process.wait(), self._kill_grace)
                    self._record_cleanup("process_kill_wait", killed)
        if self._context is not None and "context_exit" not in self._cleanup_completed_stages:
            self._record_cleanup(
                "context_exit",
                await _hard_bound(
                    self._context.__aexit__(None, None, None), self._kill_grace
                ),
            )
            self._cleanup_completed_stages.add("context_exit")
        if self._stderr_task is not None and "stderr_join" not in self._cleanup_completed_stages:
            cancelled_by_cleanup = False
            if not self._stderr_task.done():
                self._stderr_task.cancel()
                cancelled_by_cleanup = True
            done, _ = await asyncio.wait({self._stderr_task}, timeout=self._kill_grace)
            if self._stderr_task in done:
                try:
                    self._stderr = self._stderr_task.result()
                except asyncio.CancelledError:
                    self._record_cleanup(
                        "stderr_join", "completed" if cancelled_by_cleanup else "failed"
                    )
                except BaseException:
                    self._record_cleanup("stderr_join", "failed")
                else:
                    self._record_cleanup("stderr_join", "completed")
            else:
                self._stderr_task.add_done_callback(_consume_task)
                self._record_cleanup("stderr_join", "timed_out")
            self._cleanup_completed_stages.add("stderr_join")
        if process is not None:
            process_transport = getattr(process, "_transport", None)
            if process_transport is not None:
                with contextlib.suppress(Exception):
                    process_transport.close()
        self._cleanup_incomplete = process is not None and process.returncode is None
        self._lifecycle_state = (
            "cleanup_incomplete" if self._cleanup_incomplete else "closed"
        )

    def _record_cleanup(self, stage: str, status: str) -> None:
        self._cleanup_diagnostics.append(AcpCleanupDiagnostic(stage=stage, status=status))
        if status == "completed":
            self._cleanup_completed_stages.add(stage)

    def _signal_process(self, process: asyncio.subprocess.Process, signal: str) -> None:
        try:
            getattr(process, signal)()
        except ProcessLookupError:
            self._record_cleanup(f"process_{signal}", "completed")
        except Exception:
            self._record_cleanup(f"process_{signal}", "failed")
        else:
            self._record_cleanup(f"process_{signal}", "completed")

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
