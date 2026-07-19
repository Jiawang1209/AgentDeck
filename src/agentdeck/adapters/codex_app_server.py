"""Bounded client and passive readiness probe for Codex app-server protocol v2."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import inspect
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Final

FROZEN_CODEX_VERSION: Final = "codex-cli 0.131.0"
FROZEN_STABLE_SCHEMA_DIGEST: Final = (
    "91fae2120975b74d2d02184de2d8fed5f90770ce5009f308bbcaeec02dedcc23"
)
_SCHEMA_NAME: Final = "codex_app_server_protocol.v2.schemas.json"
_MAX_LINE_BYTES: Final = 1024 * 1024
_MAX_TOTAL_BYTES: Final = 8 * 1024 * 1024
_MAX_SCHEMA_BYTES: Final = 8 * 1024 * 1024
_MAX_PROBE_OUTPUT: Final = 4096
_TIMEOUT_SECONDS: Final = 30.0
NotificationHandler = Callable[[str, dict[str, object]], Awaitable[None] | None]
PermissionHandler = Callable[["AppServerPermissionRequest"], Awaitable[object] | object]
class AppServerProtocolError(RuntimeError):
    """Content-free app-server transport or protocol failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Codex app-server unavailable: {code}")
@dataclass(frozen=True)
class BridgeDiagnostic:
    code: str
@dataclass(frozen=True)
class CodexBridgeReadiness:
    ready: bool
    version: str | None
    schema_digest: str | None
    diagnostic: BridgeDiagnostic | None
@dataclass(frozen=True)
class AppServerPermissionRequest:
    native_id: object
    native_request_id: str
    method: str
    thread_id: str
    turn_id: str
    item_id: str
    kind: str
    requested_permissions: dict[str, object] | None = None
@dataclass(frozen=True)
class AppServerTurnResult:
    turn_id: str
    status: str


def _command(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError("command must be an argv sequence")
    result = tuple(value)
    if not result or any(type(part) is not str or not part for part in result):
        raise ValueError("command must contain nonempty argv strings")
    return result


def _probe_run(command: tuple[str, ...]) -> bytes:
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.DEVNULL, timeout=10, check=False)
            if result.returncode != 0 or output.tell() > _MAX_PROBE_OUTPUT:
                raise AppServerProtocolError("codex_app_server_probe_failed")
            output.seek(0)
            return output.read()
    except (OSError, subprocess.SubprocessError):
        raise AppServerProtocolError("codex_app_server_probe_failed") from None


def probe_codex_bridge(
    app_server_command: Sequence[str], *,
    expected_version: str = FROZEN_CODEX_VERSION,
    expected_digest: str = FROZEN_STABLE_SCHEMA_DIGEST,
) -> CodexBridgeReadiness:
    """Passively verify the frozen CLI and stable generated schema in temp space."""
    command = _command(app_server_command)
    if command[-1] != "app-server":
        raise ValueError("app-server command must end with app-server")
    root = command[:-1]
    try:
        version = _probe_run((*root, "--version")).decode("utf-8", "strict").strip()
    except (UnicodeDecodeError, AppServerProtocolError):
        return CodexBridgeReadiness(
            False, None, None, BridgeDiagnostic("codex_app_server_probe_failed")
        )
    if version != expected_version:
        return CodexBridgeReadiness(
            False, None, None, BridgeDiagnostic("codex_app_server_version_drift")
        )
    try:
        with tempfile.TemporaryDirectory(prefix="agentdeck-codex-schema-") as directory:
            _probe_run((*command, "generate-json-schema", "--out", directory))
            schema_path = Path(directory) / _SCHEMA_NAME
            size = schema_path.stat().st_size
            if not 0 < size <= _MAX_SCHEMA_BYTES:
                raise AppServerProtocolError("codex_app_server_probe_failed")
            canonical = json.dumps(json.loads(schema_path.read_text("utf-8")), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            digest = sha256(canonical.encode("utf-8", "strict")).hexdigest()
    except (OSError, ValueError, AppServerProtocolError):
        return CodexBridgeReadiness(
            False, version, None, BridgeDiagnostic("codex_app_server_probe_failed")
        )
    if digest != expected_digest:
        return CodexBridgeReadiness(
            False, version, None, BridgeDiagnostic("codex_app_server_schema_drift")
        )
    return CodexBridgeReadiness(True, version, digest, None)


class CodexAppServerClient:
    """One bounded, stable-only Codex app-server stdio connection."""

    def __init__(
        self, command: Sequence[str], *, max_line_bytes: int = _MAX_LINE_BYTES,
        max_total_bytes: int = _MAX_TOTAL_BYTES,
        timeout_seconds: float = _TIMEOUT_SECONDS,
    ) -> None:
        self.command = _command(command)
        if type(max_line_bytes) is not int or not 0 < max_line_bytes <= _MAX_LINE_BYTES:
            raise ValueError("max_line_bytes is invalid")
        if type(max_total_bytes) is not int or not max_line_bytes <= max_total_bytes <= _MAX_TOTAL_BYTES:
            raise ValueError("max_total_bytes is invalid")
        if type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 120:
            raise ValueError("timeout_seconds is invalid")
        self._max_line = max_line_bytes
        self._max_total = max_total_bytes
        self._timeout = float(timeout_seconds)
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._request_tasks: set[asyncio.Task[None]] = set()
        self._pending: dict[int, asyncio.Future[object]] = {}
        self._next_id = 1
        self._total_bytes = 0
        self._initialized = False
        self._closed = False
        self._fatal: AppServerProtocolError | None = None
        self._notification: NotificationHandler | None = None
        self._permission: PermissionHandler | None = None
        self._active_thread: str | None = None
        self._active_turn: str | None = None
        self._turn_done: asyncio.Future[AppServerTurnResult] | None = None

    async def __aenter__(self) -> "CodexAppServerClient":
        return self

    async def __aexit__(self, _type, _value, _traceback) -> None:
        await self.close()

    @property
    def active_turn_id(self) -> str | None:
        return self._active_turn

    async def initialize(self) -> dict[str, object]:
        if self._initialized:
            raise ValueError("app-server is already initialized")
        await self._start()
        result = await self._request("initialize", {
            "clientInfo": {"name": "agentdeck", "version": "0.1.0"},
            "capabilities": {"experimentalApi": False},
        })
        value = _object(result)
        if not all(type(value.get(key)) is str for key in (
            "userAgent", "codexHome", "platformFamily", "platformOs",
        )):
            raise AppServerProtocolError("codex_app_server_protocol")
        await self._notify("initialized")
        self._initialized = True
        return value

    async def start_thread(self, *, cwd: str, model: str | None = None) -> str:
        self._require_initialized()
        params: dict[str, object] = {"cwd": _text(cwd), "approvalPolicy": "on-request"}
        if model is not None and model != "native-default":
            params["model"] = _text(model)
        return _thread_id(await self._request("thread/start", params))

    async def resume_thread(self, thread_id: str, *, cwd: str) -> str:
        self._require_initialized()
        requested = _text(thread_id)
        actual = _thread_id(await self._request(
            "thread/resume", {"threadId": requested, "cwd": _text(cwd)}
        ))
        if actual != requested:
            raise AppServerProtocolError("codex_app_server_protocol")
        return actual

    async def start_turn(
        self, *, thread_id: str, text: str,
        output_schema: dict[str, object] | None = None,
        on_notification: NotificationHandler,
        on_permission: PermissionHandler,
    ) -> AppServerTurnResult:
        self._require_initialized()
        if self._turn_done is not None:
            raise ValueError("app-server turn already active")
        thread_id = _text(thread_id)
        text = _text(text, limit=1024 * 1024)
        if not callable(on_notification) or not callable(on_permission):
            raise TypeError("turn callbacks must be callable")
        self._active_thread = thread_id
        self._notification = on_notification
        self._permission = on_permission
        self._turn_done = asyncio.get_running_loop().create_future()
        try:
            params: dict[str, object] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": text}],
            }
            if output_schema is not None:
                params["outputSchema"] = _object(output_schema)
            response = _object(await self._request("turn/start", params))
            turn = _object(response.get("turn"))
            turn_id = _text(turn.get("id"))
            if self._active_turn is not None and self._active_turn != turn_id:
                raise AppServerProtocolError("codex_app_server_protocol")
            self._active_turn = turn_id
            return await asyncio.wait_for(self._turn_done, self._timeout)
        except asyncio.TimeoutError:
            raise AppServerProtocolError("codex_app_server_timeout") from None
        finally:
            self._notification = None
            self._permission = None
            self._active_thread = None
            self._active_turn = None
            self._turn_done = None

    async def interrupt_turn(self, *, thread_id: str, turn_id: str) -> None:
        self._require_initialized()
        if thread_id != self._active_thread or turn_id != self._active_turn:
            raise ValueError("turn interrupt lineage is invalid")
        await self._request("turn/interrupt", {
            "threadId": _text(thread_id), "turnId": _text(turn_id),
        })

    async def interrupt_active_turn(self, *, thread_id: str) -> None:
        for _ in range(100):
            if self._active_turn is not None:
                await self.interrupt_turn(
                    thread_id=thread_id, turn_id=self._active_turn
                )
                return
            await asyncio.sleep(0)
        raise AppServerProtocolError("codex_app_server_turn_unavailable")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None and process.stdin is not None:
            process.stdin.close()
            await asyncio.gather(process.stdin.wait_closed(), return_exceptions=True)
        if process is not None and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), 2)
            except asyncio.TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        if self._reader_task is not None:
            await asyncio.gather(self._reader_task, return_exceptions=True)
        if process is not None and process.stdout is not None:
            while await process.stdout.read(64 * 1024):
                pass
        for task in tuple(self._request_tasks):
            task.cancel()
        if self._request_tasks:
            await asyncio.gather(*self._request_tasks, return_exceptions=True)

    async def _start(self) -> None:
        if self._process is not None or self._closed:
            raise ValueError("app-server lifecycle is invalid")
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=self._max_line,
            )
        except OSError:
            raise AppServerProtocolError("codex_app_server_start_failed") from None
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _request(self, method: str, params: dict[str, object]) -> object:
        self._raise_fatal()
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write({"id": request_id, "method": method, "params": params})
        try:
            return await asyncio.wait_for(future, self._timeout)
        except asyncio.TimeoutError:
            raise AppServerProtocolError("codex_app_server_timeout") from None
        finally:
            self._pending.pop(request_id, None)

    async def _notify(self, method: str, params: dict[str, object] | None = None) -> None:
        payload: dict[str, object] = {"method": method}
        if params is not None:
            payload["params"] = params
        await self._write(payload)

    async def _write(self, payload: dict[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise AppServerProtocolError("codex_app_server_disconnected")
        try:
            encoded = json.dumps(
                payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
            ).encode("utf-8", "strict") + b"\n"
        except (TypeError, ValueError, UnicodeEncodeError):
            raise AppServerProtocolError("codex_app_server_protocol") from None
        if len(encoded) > self._max_line:
            raise AppServerProtocolError("codex_app_server_oversize")
        process.stdin.write(encoded)
        try:
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise AppServerProtocolError("codex_app_server_disconnected") from None

    async def _read_loop(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            while line := await process.stdout.readline():
                self._total_bytes += len(line)
                if len(line) > self._max_line or self._total_bytes > self._max_total:
                    raise AppServerProtocolError("codex_app_server_oversize")
                try:
                    message = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise AppServerProtocolError("codex_app_server_protocol") from None
                await self._dispatch(_object(message))
            if not self._closed:
                raise AppServerProtocolError("codex_app_server_disconnected")
        except asyncio.CancelledError:
            raise
        except (ValueError, AppServerProtocolError) as error:
            failure = error if isinstance(error, AppServerProtocolError) else AppServerProtocolError(
                "codex_app_server_protocol"
            )
            self._set_fatal(failure)

    async def _dispatch(self, message: dict[str, object]) -> None:
        if "method" in message:
            if "id" in message:
                task = asyncio.create_task(self._handle_server_request(message))
                self._request_tasks.add(task)
                task.add_done_callback(self._request_tasks.discard)
            else:
                await self._handle_notification(message)
            return
        request_id = message.get("id")
        if type(request_id) is not int or request_id not in self._pending:
            raise AppServerProtocolError("codex_app_server_protocol")
        if set(message) == {"id", "result"}:
            self._pending[request_id].set_result(message["result"])
        elif set(message) == {"id", "error"}:
            self._pending[request_id].set_exception(
                AppServerProtocolError("codex_app_server_request_failed")
            )
        else:
            raise AppServerProtocolError("codex_app_server_protocol")

    async def _handle_notification(self, message: dict[str, object]) -> None:
        if set(message) != {"method", "params"}:
            raise AppServerProtocolError("codex_app_server_protocol")
        method = _text(message["method"])
        params = _object(message["params"])
        if method == "turn/started":
            self._accept_turn(_object(params.get("turn")))
        elif method == "turn/completed":
            turn = _object(params.get("turn"))
            self._accept_turn(turn)
            status = turn.get("status")
            if status not in {"completed", "interrupted", "failed"}:
                raise AppServerProtocolError("codex_app_server_protocol")
            if self._turn_done is None or self._turn_done.done():
                raise AppServerProtocolError("codex_app_server_protocol")
            self._turn_done.set_result(AppServerTurnResult(_text(turn["id"]), status))
        handler = self._notification
        if handler is not None:
            await _maybe_await(handler(method, params))

    def _accept_turn(self, turn: dict[str, object]) -> None:
        turn_id = _text(turn.get("id"))
        if self._active_turn is not None and self._active_turn != turn_id:
            raise AppServerProtocolError("codex_app_server_protocol")
        self._active_turn = turn_id

    async def _handle_server_request(self, message: dict[str, object]) -> None:
        native_id = message.get("id")
        try:
            if set(message) not in (
                {"id", "method", "params"}, {"id", "method", "params", "trace"},
            ):
                raise AppServerProtocolError("codex_app_server_protocol")
            request = _permission_request(native_id, message["method"], message["params"])
            if request.thread_id != self._active_thread or request.turn_id != self._active_turn:
                raise AppServerProtocolError("codex_app_server_protocol")
            if self._permission is None:
                raise AppServerProtocolError("codex_app_server_protocol")
            result = await _maybe_await(self._permission(request))
            await self._write({
                "id": native_id, "result": _permission_result(request, result),
            })
        except asyncio.CancelledError:
            raise
        except Exception:
            self._set_fatal(AppServerProtocolError("codex_app_server_permission_failed"))

    def _set_fatal(self, error: AppServerProtocolError) -> None:
        if self._fatal is None:
            self._fatal = error
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(self._fatal)
        if self._turn_done is not None and not self._turn_done.done():
            self._turn_done.set_exception(self._fatal)

    def _raise_fatal(self) -> None:
        if self._fatal is not None:
            raise self._fatal

    def _require_initialized(self) -> None:
        self._raise_fatal()
        if not self._initialized or self._closed:
            raise ValueError("app-server is not initialized")


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise AppServerProtocolError("codex_app_server_protocol")
    return value


def _text(value: object, *, limit: int = 4096) -> str:
    if type(value) is not str or not value.strip():
        raise AppServerProtocolError("codex_app_server_protocol")
    try:
        if len(value.encode("utf-8", "strict")) > limit:
            raise ValueError
    except (UnicodeEncodeError, ValueError):
        raise AppServerProtocolError("codex_app_server_protocol") from None
    return value


def _thread_id(response: object) -> str:
    return _text(_object(_object(response).get("thread")).get("id"))


def _permission_request(native_id: object, method_value: object, params_value: object) -> AppServerPermissionRequest:
    if type(native_id) not in {int, str} or isinstance(native_id, bool):
        raise AppServerProtocolError("codex_app_server_protocol")
    method = _text(method_value)
    effects = {
        "item/commandExecution/requestApproval": "execute",
        "item/fileChange/requestApproval": "edit",
        "item/permissions/requestApproval": "other",
    }
    if method not in effects:
        raise AppServerProtocolError("codex_app_server_protocol")
    params = _object(params_value)
    requested = _object(params.get("permissions")) if method == "item/permissions/requestApproval" else None
    return AppServerPermissionRequest(
        native_id=native_id, native_request_id=_text(str(native_id), limit=256),
        method=method, thread_id=_text(params.get("threadId")),
        turn_id=_text(params.get("turnId")), item_id=_text(params.get("itemId")),
        kind=effects[method], requested_permissions=requested,
    )


def _permission_result(request: AppServerPermissionRequest, value: object) -> dict[str, object]:
    allowed = value is True or value == "accept"
    if request.method == "item/permissions/requestApproval":
        return {
            "permissions": request.requested_permissions if allowed else {},
            "scope": "turn", "strictAutoReview": None,
        }
    return {"decision": "accept" if allowed else "decline"}
