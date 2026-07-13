"""Verified bounded client for the project-local daemon socket."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
import fcntl
import math
import os
from pathlib import Path
import secrets
import socket
import stat
import sys
import threading
from typing import Any

from agentdeck import __version__
from agentdeck.models import PROJECT_VIEW_SCHEMA_VERSION

from .lifecycle import (
    DaemonIdentityError,
    bind_daemon_endpoint,
    daemon_endpoint,
    project_root_hash,
)
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
DEFAULT_DAEMON_LOG_CAP_BYTES = 1024 * 1024
_MIN_DAEMON_LOG_CAP_BYTES = 1024
_MAX_DAEMON_LOG_CAP_BYTES = 64 * 1024 * 1024
_LOG_NAMES = ("daemon.stdout.log", "daemon.stderr.log")


class DaemonClientError(RuntimeError):
    """A sanitized local daemon request failure."""


class DaemonUnavailable(DaemonClientError):
    """No verified daemon is reachable at the expected endpoint."""


def _validate_timeout(value: object) -> float:
    if (
        type(value) not in {int, float}
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise DaemonUnavailable("daemon timeout must be a positive finite number")
    return float(value)


def _connect_remaining(deadline: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise asyncio.TimeoutError
    return remaining


def _startup_remaining(deadline: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise DaemonUnavailable("daemon did not become ready")
    return remaining


async def _close_writer_before_deadline(writer: Any, deadline: float) -> None:
    writer.close()
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        return
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=remaining)
    except (asyncio.TimeoutError, ConnectionError, BrokenPipeError):
        pass


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
        self._terminal_error: DaemonClientError | None = None
        self._terminal_event = asyncio.Event()
        self._reader_task = asyncio.create_task(self._reader_loop())

    @classmethod
    async def connect_verified(
        cls,
        root: Path,
        *,
        max_frame_bytes: int = 1024 * 1024,
        timeout_seconds: float = 10,
    ) -> "DaemonClient":
        _validate_timeout(timeout_seconds)
        deadline = asyncio.get_running_loop().time() + float(timeout_seconds)
        binding = None
        connected_socket: socket.socket | None = None
        client: DaemonClient | None = None
        try:
            binding = bind_daemon_endpoint(root)
            metadata = binding.read_metadata()
            if metadata is None or metadata["project_root_hash"] != project_root_hash(root):
                raise DaemonUnavailable("daemon project identity is unverified")
            socket_identity = binding.socket_identity()
            connected_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connected_socket.setblocking(False)
            await asyncio.wait_for(
                asyncio.get_running_loop().sock_connect(
                    connected_socket, str(binding.endpoint.socket_path)
                ),
                timeout=_connect_remaining(deadline),
            )
            binding.assert_socket_identity(socket_identity)
            client = await cls.connect(
                binding.endpoint.socket_path,
                expected_project_root_hash=str(metadata["project_root_hash"]),
                expected_start_nonce_hash=str(metadata["start_nonce_hash"]),
                expected_instance_id=str(metadata["instance_id"]),
                max_frame_bytes=max_frame_bytes,
                timeout_seconds=_connect_remaining(deadline),
                _connected_socket=connected_socket,
            )
            connected_socket = None
            binding.assert_socket_identity(socket_identity)
            return client
        except asyncio.CancelledError:
            if client is not None:
                await client.close()
            raise
        except (
            DaemonIdentityError,
            DaemonClientError,
            FileNotFoundError,
            ConnectionError,
            OSError,
            asyncio.TimeoutError,
        ):
            if client is not None:
                await client.close()
            raise DaemonUnavailable("daemon identity is unverified") from None
        finally:
            if connected_socket is not None:
                connected_socket.close()
            if binding is not None:
                binding.close()

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
        _connected_socket: socket.socket | None = None,
    ) -> "DaemonClient":
        _validate_timeout(timeout_seconds)
        deadline = asyncio.get_running_loop().time() + float(timeout_seconds)
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    None if _connected_socket is not None else str(endpoint),
                    sock=_connected_socket,
                    limit=max_frame_bytes + 1,
                ),
                timeout=_connect_remaining(deadline),
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
            await asyncio.wait_for(
                writer.drain(), timeout=_connect_remaining(deadline)
            )
            response = decode_response(
                await _read_frame(
                    reader, max_frame_bytes, _connect_remaining(deadline)
                ),
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
            await asyncio.wait_for(
                writer.drain(), timeout=_connect_remaining(deadline)
            )
            status_response = decode_response(
                await _read_frame(
                    reader, max_frame_bytes, _connect_remaining(deadline)
                ),
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
                request_timeout_seconds=float(timeout_seconds),
            )
        except asyncio.CancelledError:
            if writer is not None:
                await _close_writer_before_deadline(writer, deadline)
            raise
        except (DaemonClientError, RpcProtocolError, ConnectionError, asyncio.TimeoutError):
            if writer is not None:
                await _close_writer_before_deadline(writer, deadline)
            raise DaemonUnavailable("daemon identity is unverified") from None

    async def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        lease_id: str | None = None,
        lease_generation: int | None = None,
    ) -> dict[str, object]:
        if self._terminal_error is not None:
            raise DaemonClientError(str(self._terminal_error))
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
        sent = False
        try:
            try:
                async with asyncio.timeout(self.request_timeout_seconds):
                    payload = encode_request(
                        request,
                        max_bytes=self.max_frame_bytes,
                        allowed_methods=CLIENT_METHODS,
                    )
                    async with self._write_lock:
                        self._writer.write(payload)
                        sent = True
                        await self._writer.drain()
                    return await asyncio.shield(future)
            except asyncio.TimeoutError:
                future.cancel()
                self._expired_request_ids.append(request_id)
                raise DaemonClientError("daemon request timed out") from None
        except asyncio.CancelledError:
            future.cancel()
            if sent:
                self._expired_request_ids.append(request_id)
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
        if self._terminal_error is not None:
            raise DaemonClientError(str(self._terminal_error))

        async def wait() -> RpcEvent:
            event_task = asyncio.create_task(self._events.get())
            terminal_task = asyncio.create_task(self._terminal_event.wait())
            try:
                done, _ = await asyncio.wait(
                    {event_task, terminal_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if terminal_task in done and self._terminal_error is not None:
                    event_task.cancel()
                    await asyncio.gather(event_task, return_exceptions=True)
                    raise DaemonClientError(str(self._terminal_error))
                return event_task.result()
            finally:
                for task in (event_task, terminal_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(event_task, terminal_task, return_exceptions=True)

        try:
            if timeout_seconds is None:
                return await wait()
            return await asyncio.wait_for(wait(), timeout=timeout_seconds)
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
            return
        except (EOFError, ConnectionError, RpcProtocolError, DaemonClientError):
            failure = DaemonClientError("daemon connection closed")
        finally:
            if failure is not None:
                self._mark_terminal(failure)

    def _mark_terminal(self, error: DaemonClientError) -> None:
        if self._terminal_error is not None:
            return
        self._terminal_error = error
        self._closed = True
        self._terminal_event.set()
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(DaemonClientError(str(error)))
        self._writer.close()

    async def close(self) -> None:
        if self._terminal_error is None:
            self._mark_terminal(DaemonClientError("daemon client is closed"))
        if not self._reader_task.done():
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        try:
            await asyncio.wait_for(
                self._writer.wait_closed(), timeout=self.request_timeout_seconds
            )
        except (asyncio.TimeoutError, ConnectionError, BrokenPipeError):
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


def _secure_runtime_fd(root: Path) -> int:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    root_fd: int | None = None
    agentdeck_fd: int | None = None
    try:
        root_fd = os.open(root, directory_flags)
        try:
            os.mkdir(".agentdeck", 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        agentdeck_fd = os.open(".agentdeck", directory_flags, dir_fd=root_fd)
        try:
            os.mkdir("runtime", 0o700, dir_fd=agentdeck_fd)
        except FileExistsError:
            pass
        return os.open("runtime", directory_flags, dir_fd=agentdeck_fd)
    except OSError:
        raise DaemonUnavailable("daemon runtime symlink is forbidden") from None
    finally:
        if agentdeck_fd is not None:
            os.close(agentdeck_fd)
        if root_fd is not None:
            os.close(root_fd)


def _validate_log_cap(value: object) -> int:
    if (
        type(value) is not int
        or not _MIN_DAEMON_LOG_CAP_BYTES <= value <= _MAX_DAEMON_LOG_CAP_BYTES
    ):
        raise DaemonUnavailable("daemon log cap is invalid")
    return value


def _validate_log_targets(root: Path) -> None:
    runtime_fd = _secure_runtime_fd(root)
    try:
        for name in (*_LOG_NAMES, *(f"{name}.1" for name in _LOG_NAMES)):
            try:
                metadata = os.stat(name, dir_fd=runtime_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise DaemonUnavailable("daemon log symlink is forbidden")
    finally:
        os.close(runtime_fd)


def _try_spawn_lock(root: Path) -> int | None:
    runtime_fd = _secure_runtime_fd(root)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            "daemon.spawn.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=runtime_fd,
        )
        held = os.fstat(descriptor)
        visible = os.stat(
            "daemon.spawn.lock", dir_fd=runtime_fd, follow_symlinks=False
        )
        if not stat.S_ISREG(visible.st_mode) or (held.st_dev, held.st_ino) != (
            visible.st_dev,
            visible.st_ino,
        ):
            raise DaemonUnavailable("daemon spawn lock identity changed")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        return descriptor
    except DaemonUnavailable:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        raise DaemonUnavailable("daemon spawn lock is unavailable") from None
    finally:
        os.close(runtime_fd)


def _release_spawn_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


class _BoundedRotatingTextWriter:
    """Two-segment UTF-8 writer with a strict total byte budget."""

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, root: Path, name: str, budget_bytes: int) -> None:
        if name not in _LOG_NAMES or budget_bytes < 2:
            raise DaemonUnavailable("daemon log writer configuration is invalid")
        self._runtime_fd = _secure_runtime_fd(root)
        self._name = name
        self._rotated = f"{name}.1"
        self._segment_bytes = max(1, budget_bytes // 2)
        self._lock = threading.Lock()
        self._closed = False
        try:
            self._normalize(self._name)
            self._normalize(self._rotated)
        except Exception:
            os.close(self._runtime_fd)
            self._closed = True
            raise

    @property
    def closed(self) -> bool:
        return self._closed

    def writable(self) -> bool:
        return not self._closed

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("bounded daemon log has no stable descriptor")

    def _open(self, name: str, flags: int) -> int:
        try:
            descriptor = os.open(
                name,
                flags | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=self._runtime_fd,
            )
        except OSError:
            raise DaemonUnavailable("daemon log symlink is forbidden") from None
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise DaemonUnavailable("daemon log must be a regular file")
        return descriptor

    def _normalize(self, name: str) -> None:
        try:
            descriptor = self._open(name, os.O_RDWR)
        except DaemonUnavailable:
            try:
                os.stat(name, dir_fd=self._runtime_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise
        try:
            if os.fstat(descriptor).st_size > self._segment_bytes:
                os.ftruncate(descriptor, self._segment_bytes)
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def _size(self, name: str) -> int:
        try:
            metadata = os.stat(name, dir_fd=self._runtime_fd, follow_symlinks=False)
        except FileNotFoundError:
            return 0
        if not stat.S_ISREG(metadata.st_mode):
            raise DaemonUnavailable("daemon log symlink is forbidden")
        return metadata.st_size

    def _rotate(self) -> None:
        try:
            rotated = os.stat(
                self._rotated, dir_fd=self._runtime_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(rotated.st_mode):
                raise DaemonUnavailable("daemon log symlink is forbidden")
            os.unlink(self._rotated, dir_fd=self._runtime_fd)
        try:
            os.rename(
                self._name,
                self._rotated,
                src_dir_fd=self._runtime_fd,
                dst_dir_fd=self._runtime_fd,
            )
        except FileNotFoundError:
            pass

    def write(self, value: str) -> int:
        if type(value) is not str:
            raise TypeError("daemon log write requires text")
        payload = value.encode("utf-8")
        with self._lock:
            if self._closed:
                raise ValueError("write to closed daemon log")
            offset = 0
            while offset < len(payload):
                piece = payload[offset : offset + self._segment_bytes]
                if self._size(self._name) + len(piece) > self._segment_bytes:
                    self._rotate()
                descriptor = self._open(
                    self._name, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                )
                try:
                    os.write(descriptor, piece)
                    os.fchmod(descriptor, 0o600)
                finally:
                    os.close(descriptor)
                offset += len(piece)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                os.close(self._runtime_fd)


def install_bounded_daemon_stdio_from_env(root: Path) -> None:
    canonical = Path(root).expanduser().resolve()
    runtime = daemon_endpoint(canonical).socket_path.parent
    expected_stdout = runtime / _LOG_NAMES[0]
    expected_stderr = runtime / _LOG_NAMES[1]
    if (
        os.environ.get("AGENTDECK_DAEMON_STDOUT_LOG") != str(expected_stdout)
        or os.environ.get("AGENTDECK_DAEMON_STDERR_LOG") != str(expected_stderr)
    ):
        raise DaemonUnavailable("daemon log destination is invalid")
    try:
        cap = int(os.environ["AGENTDECK_DAEMON_LOG_CAP_BYTES"])
    except (KeyError, TypeError, ValueError):
        raise DaemonUnavailable("daemon log cap is invalid") from None
    cap = _validate_log_cap(cap)
    _validate_log_targets(canonical)
    stream_budget = cap // 2
    sys.stdout = _BoundedRotatingTextWriter(
        canonical, _LOG_NAMES[0], stream_budget
    )  # type: ignore[assignment]
    sys.stderr = _BoundedRotatingTextWriter(
        canonical, _LOG_NAMES[1], stream_budget
    )  # type: ignore[assignment]


async def connect_or_start(
    root: Path,
    config: Any,
    *,
    spawn_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
    retry_interval: float = 0.05,
    log_cap_bytes: int = DEFAULT_DAEMON_LOG_CAP_BYTES,
) -> DaemonClient:
    canonical = Path(root).expanduser().resolve()
    cap = _validate_log_cap(log_cap_bytes)
    if type(retry_interval) not in {int, float} or retry_interval <= 0:
        raise DaemonUnavailable("daemon retry interval is invalid")
    timeout_seconds = _validate_timeout(config.daemon.start_timeout_seconds)
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    try:
        return await DaemonClient.connect_verified(
            canonical,
            max_frame_bytes=config.daemon.max_frame_bytes,
            timeout_seconds=min(0.25, timeout_seconds),
        )
    except DaemonUnavailable:
        pass
    _startup_remaining(deadline)
    _validate_log_targets(canonical)
    argv = (
        sys.executable,
        "-m",
        "agentdeck",
        "_daemon",
        "serve",
        "--project",
        str(canonical),
    )
    environment = dict(os.environ)
    runtime = daemon_endpoint(canonical).socket_path.parent
    environment.update(
        {
            "AGENTDECK_DAEMON_STDOUT_LOG": str(runtime / _LOG_NAMES[0]),
            "AGENTDECK_DAEMON_STDERR_LOG": str(runtime / _LOG_NAMES[1]),
            "AGENTDECK_DAEMON_LOG_CAP_BYTES": str(cap),
        }
    )

    while True:
        remaining = _startup_remaining(deadline)
        try:
            return await DaemonClient.connect_verified(
                canonical,
                max_frame_bytes=config.daemon.max_frame_bytes,
                timeout_seconds=min(0.25, remaining),
            )
        except DaemonUnavailable:
            pass

        remaining = _startup_remaining(deadline)
        spawn_lock = _try_spawn_lock(canonical)
        if spawn_lock is None:
            remaining = _startup_remaining(deadline)
            await asyncio.sleep(min(float(retry_interval), remaining))
            continue
        try:
            # The endpoint may have become ready between the failed connection
            # and the short-lived spawn election.
            remaining = _startup_remaining(deadline)
            try:
                return await DaemonClient.connect_verified(
                    canonical,
                    max_frame_bytes=config.daemon.max_frame_bytes,
                    timeout_seconds=min(0.25, remaining),
                )
            except DaemonUnavailable:
                pass

            remaining = _startup_remaining(deadline)
            try:
                await asyncio.wait_for(
                    spawn_factory(
                        *argv,
                        cwd=str(canonical),
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        env=environment,
                        start_new_session=True,
                    ),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                raise DaemonUnavailable("daemon spawn timed out") from None

            while True:
                remaining = _startup_remaining(deadline)
                try:
                    return await DaemonClient.connect_verified(
                        canonical,
                        max_frame_bytes=config.daemon.max_frame_bytes,
                        timeout_seconds=min(0.25, remaining),
                    )
                except DaemonUnavailable:
                    remaining = _startup_remaining(deadline)
                    await asyncio.sleep(min(float(retry_interval), remaining))
        finally:
            _release_spawn_lock(spawn_lock)
