"""Verified bounded client for the project-local daemon socket."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any

from agentdeck import __version__
from agentdeck.models import PROJECT_VIEW_SCHEMA_VERSION

from .lifecycle import daemon_endpoint, project_root_hash
from .protocol import (
    DAEMON_RPC_PROTOCOL_VERSION,
    RpcEvent,
    RpcProtocolError,
    RpcRequest,
    decode_event,
    decode_response,
    encode_request,
)


CLIENT_METHODS = frozenset({"handshake", "status", "subscribe", "mission.pause"})
_METADATA_FIELDS = {"instance_id", "project_root_hash", "start_nonce_hash", "pid"}
_HASH = re.compile(r"[0-9a-f]{64}")


class DaemonClientError(RuntimeError):
    """A sanitized local daemon request failure."""


class DaemonUnavailable(DaemonClientError):
    """No verified daemon is reachable at the expected endpoint."""


class DaemonClient:
    def __init__(
        self,
        *,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        max_frame_bytes: int,
        compatible: bool,
        instance_id: str,
        request_timeout_seconds: float,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self.max_frame_bytes = max_frame_bytes
        self.compatible = compatible
        self.instance_id = instance_id
        self.request_timeout_seconds = request_timeout_seconds
        self._counter = 0
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[dict[str, object]]] = {}
        self._events: asyncio.Queue[RpcEvent] = asyncio.Queue(128)
        self._expired_request_ids: deque[str] = deque(maxlen=128)
        self._closed = False
        self._reader_task = asyncio.create_task(self._reader_loop())

    @classmethod
    async def connect_verified(
        cls,
        root: Path,
        *,
        max_frame_bytes: int = 1024 * 1024,
        timeout_seconds: float = 10,
    ) -> "DaemonClient":
        endpoint = daemon_endpoint(root)
        metadata = _read_metadata(endpoint.metadata_path)
        if metadata["project_root_hash"] != project_root_hash(root):
            raise DaemonUnavailable("daemon project identity is unverified")
        return await cls.connect(
            endpoint.socket_path,
            expected_project_root_hash=str(metadata["project_root_hash"]),
            expected_start_nonce_hash=str(metadata["start_nonce_hash"]),
            expected_instance_id=str(metadata["instance_id"]),
            max_frame_bytes=max_frame_bytes,
            timeout_seconds=timeout_seconds,
        )

    @classmethod
    async def connect(
        cls,
        endpoint: Path,
        *,
        expected_project_root_hash: str,
        expected_start_nonce_hash: str,
        expected_instance_id: str | None = None,
        protocol_version: str = DAEMON_RPC_PROTOCOL_VERSION,
        max_frame_bytes: int = 1024 * 1024,
        timeout_seconds: float = 10,
    ) -> "DaemonClient":
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    str(endpoint), limit=max_frame_bytes + 1
                ),
                timeout=timeout_seconds,
            )
        except (FileNotFoundError, ConnectionError, OSError, asyncio.TimeoutError):
            raise DaemonUnavailable("daemon endpoint is unavailable") from None
        try:
            handshake_id = "req_handshake_" + secrets.token_hex(8)
            handshake = RpcRequest.handshake(
                request_id=handshake_id,
                project_root_hash=expected_project_root_hash,
                client_version=__version__,
                protocol_version=protocol_version,
            )
            writer.write(encode_request(handshake, max_bytes=max_frame_bytes))
            await asyncio.wait_for(writer.drain(), timeout=timeout_seconds)
            response = decode_response(
                await _read_frame(reader, max_frame_bytes, timeout_seconds),
                max_bytes=max_frame_bytes,
            )
            if response.request_id != handshake_id or not response.ok or response.result is None:
                raise DaemonUnavailable("daemon handshake is unverified")
            compatible = response.result.get("compatible") is True

            status_id = "req_identity_" + secrets.token_hex(8)
            status_request = RpcRequest(status_id, "status", {})
            writer.write(
                encode_request(
                    status_request,
                    max_bytes=max_frame_bytes,
                    allowed_methods=CLIENT_METHODS,
                )
            )
            await asyncio.wait_for(writer.drain(), timeout=timeout_seconds)
            status_response = decode_response(
                await _read_frame(reader, max_frame_bytes, timeout_seconds),
                max_bytes=max_frame_bytes,
            )
            status = status_response.result
            if (
                status_response.request_id != status_id
                or not status_response.ok
                or status is None
                or status.get("project_root_hash") != expected_project_root_hash
                or status.get("start_nonce_hash") != expected_start_nonce_hash
                or (
                    expected_instance_id is not None
                    and status.get("instance_id") != expected_instance_id
                )
                or status.get("protocol_version") != DAEMON_RPC_PROTOCOL_VERSION
                or status.get("project_view_schema_version") != PROJECT_VIEW_SCHEMA_VERSION
            ):
                raise DaemonUnavailable("daemon identity is unverified")
            return cls(
                reader=reader,
                writer=writer,
                max_frame_bytes=max_frame_bytes,
                compatible=compatible,
                instance_id=str(status["instance_id"]),
                request_timeout_seconds=timeout_seconds,
            )
        except asyncio.CancelledError:
            writer.close()
            await writer.wait_closed()
            raise
        except (DaemonClientError, RpcProtocolError, ConnectionError, asyncio.TimeoutError):
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass
            raise DaemonUnavailable("daemon identity is unverified") from None

    async def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        lease_id: str | None = None,
        lease_generation: int | None = None,
    ) -> dict[str, object]:
        if self._closed:
            raise DaemonClientError("daemon client is closed")
        if method not in CLIENT_METHODS or method == "handshake":
            raise DaemonClientError("daemon request method is not allowed")
        if not self.compatible and method != "status":
            raise DaemonClientError("incompatible daemon is read-only")
        request_params = dict(params)
        if lease_id is not None or lease_generation is not None:
            request_params["_lease"] = {
                "lease_id": lease_id,
                "generation": lease_generation,
            }
        self._counter += 1
        request_id = f"req_{self._counter}_{secrets.token_hex(4)}"
        request = RpcRequest(request_id, method, request_params)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, object]] = loop.create_future()
        self._pending[request_id] = future
        try:
            payload = encode_request(
                request,
                max_bytes=self.max_frame_bytes,
                allowed_methods=CLIENT_METHODS,
            )
            async with self._write_lock:
                self._writer.write(payload)
                await self._writer.drain()
            try:
                return await asyncio.wait_for(
                    asyncio.shield(future), timeout=self.request_timeout_seconds
                )
            except asyncio.TimeoutError:
                future.cancel()
                self._expired_request_ids.append(request_id)
                raise DaemonClientError("daemon request timed out") from None
        except asyncio.CancelledError:
            future.cancel()
            raise
        except (ConnectionError, RpcProtocolError):
            raise DaemonClientError("daemon request failed") from None
        finally:
            self._pending.pop(request_id, None)

    async def subscribe(self) -> None:
        result = await self.request("subscribe", {})
        if result != {"subscribed": True}:
            raise DaemonClientError("daemon subscription failed")

    async def next_event(self, *, timeout_seconds: float | None = None) -> RpcEvent:
        try:
            if timeout_seconds is None:
                return await self._events.get()
            return await asyncio.wait_for(self._events.get(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            raise DaemonClientError("daemon event wait timed out") from None

    async def _reader_loop(self) -> None:
        failure: DaemonClientError | None = None
        try:
            while not self._closed:
                frame = await _read_frame(self._reader, self.max_frame_bytes, None)
                try:
                    response = decode_response(frame, max_bytes=self.max_frame_bytes)
                except RpcProtocolError:
                    event = decode_event(frame, max_bytes=self.max_frame_bytes)
                    try:
                        self._events.put_nowait(event)
                    except asyncio.QueueFull:
                        raise DaemonClientError("daemon client event queue is full") from None
                    continue
                future = self._pending.get(response.request_id)
                if future is None and response.request_id in self._expired_request_ids:
                    self._expired_request_ids.remove(response.request_id)
                    continue
                if future is None or future.done():
                    raise DaemonClientError("daemon response correlation failed")
                if response.ok:
                    assert response.result is not None
                    future.set_result(dict(response.result))
                else:
                    assert response.error is not None
                    future.set_exception(DaemonClientError(str(response.error["message"])))
        except asyncio.CancelledError:
            raise
        except (EOFError, ConnectionError, RpcProtocolError, DaemonClientError):
            failure = DaemonClientError("daemon connection closed")
        finally:
            if failure is not None:
                for future in tuple(self._pending.values()):
                    if not future.done():
                        future.set_exception(failure)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(DaemonClientError("daemon client is closed"))
        self._reader_task.cancel()
        await asyncio.gather(self._reader_task, return_exceptions=True)
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, BrokenPipeError):
            pass


async def _read_frame(
    reader: asyncio.StreamReader,
    max_frame_bytes: int,
    timeout_seconds: float | None,
) -> bytes:
    try:
        operation = reader.readuntil(b"\n")
        frame = (
            await operation
            if timeout_seconds is None
            else await asyncio.wait_for(operation, timeout=timeout_seconds)
        )
    except asyncio.IncompleteReadError:
        raise EOFError from None
    except asyncio.LimitOverrunError:
        raise RpcProtocolError("frame_too_large", "frame exceeds maximum size") from None
    if len(frame) > max_frame_bytes:
        raise RpcProtocolError("frame_too_large", "frame exceeds maximum size")
    return frame


def _read_metadata(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise DaemonUnavailable("daemon metadata is unavailable") from None
    if (
        type(value) is not dict
        or set(value) != _METADATA_FIELDS
        or type(value["instance_id"]) is not str
        or not value["instance_id"].strip()
        or type(value["project_root_hash"]) is not str
        or _HASH.fullmatch(value["project_root_hash"]) is None
        or type(value["start_nonce_hash"]) is not str
        or _HASH.fullmatch(value["start_nonce_hash"]) is None
        or type(value["pid"]) is not int
        or value["pid"] <= 0
    ):
        raise DaemonUnavailable("daemon metadata is invalid")
    return value


def _open_project_log(root: Path, name: str):
    if name not in {"daemon.stdout.log", "daemon.stderr.log"}:
        raise DaemonUnavailable("daemon log name is invalid")

    def secure_opener(_path: str, _flags: int) -> int:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        root_fd: int | None = None
        agentdeck_fd: int | None = None
        runtime_fd: int | None = None
        try:
            root_fd = os.open(root, directory_flags)
            for parent_fd, child in ((root_fd, ".agentdeck"),):
                try:
                    os.mkdir(child, 0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
            agentdeck_fd = os.open(".agentdeck", directory_flags, dir_fd=root_fd)
            try:
                os.mkdir("runtime", 0o700, dir_fd=agentdeck_fd)
            except FileExistsError:
                pass
            runtime_fd = os.open("runtime", directory_flags, dir_fd=agentdeck_fd)
            descriptor = os.open(
                name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_TRUNC
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                0o600,
                dir_fd=runtime_fd,
            )
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                os.close(descriptor)
                raise DaemonUnavailable("daemon log must be a regular file")
            return descriptor
        except DaemonUnavailable:
            raise
        except OSError:
            raise DaemonUnavailable("daemon log symlink is forbidden") from None
        finally:
            for descriptor in (runtime_fd, agentdeck_fd, root_fd):
                if descriptor is not None:
                    os.close(descriptor)

    path = root / ".agentdeck" / "runtime" / name
    return open(path, "wb", opener=secure_opener)


async def connect_or_start(
    root: Path,
    config: Any,
    *,
    spawn_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
    retry_interval: float = 0.05,
) -> DaemonClient:
    canonical = Path(root).expanduser().resolve()
    try:
        return await DaemonClient.connect_verified(
            canonical,
            max_frame_bytes=config.daemon.max_frame_bytes,
            timeout_seconds=config.daemon.start_timeout_seconds,
        )
    except DaemonUnavailable:
        pass

    stdout = None
    stderr = None
    argv = (
        sys.executable,
        "-m",
        "agentdeck",
        "_daemon",
        "serve",
        "--project",
        str(canonical),
    )
    try:
        stdout = _open_project_log(canonical, "daemon.stdout.log")
        stderr = _open_project_log(canonical, "daemon.stderr.log")
        await spawn_factory(
            *argv,
            cwd=str(canonical),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    finally:
        if stdout is not None:
            stdout.close()
        if stderr is not None:
            stderr.close()

    deadline = asyncio.get_running_loop().time() + config.daemon.start_timeout_seconds
    while True:
        try:
            return await DaemonClient.connect_verified(
                canonical,
                max_frame_bytes=config.daemon.max_frame_bytes,
                timeout_seconds=min(1, config.daemon.start_timeout_seconds),
            )
        except DaemonUnavailable:
            if asyncio.get_running_loop().time() >= deadline:
                raise DaemonUnavailable("daemon did not become ready") from None
            await asyncio.sleep(retry_interval)
