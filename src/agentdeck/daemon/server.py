"""Bounded project-local Unix socket RPC server."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
import inspect
import os
from pathlib import Path
import socket
import stat

from .lease import LeaseError
from .protocol import (
    DAEMON_RPC_PROTOCOL_VERSION,
    RpcEvent,
    RpcProtocolError,
    RpcRequest,
    RpcResponse,
    decode_request,
    encode_event,
    encode_response,
    negotiate_handshake,
)


StatusProvider = Callable[[], Mapping[str, object] | Awaitable[Mapping[str, object]]]
MutationHandler = Callable[
    [str, dict[str, object]],
    Mapping[str, object] | Awaitable[Mapping[str, object]],
]
LeaseValidator = Callable[[str, int], object]


class DaemonServerError(RuntimeError):
    """A sanitized daemon server failure."""


@dataclass(eq=False)
class _Connection:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    request_queue: asyncio.Queue[RpcRequest | None]
    event_queue: asyncio.Queue[RpcEvent | None]
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    compatible: bool = False
    subscribed: bool = False
    closed: bool = False
    tasks: set[asyncio.Task[None]] = field(default_factory=set)


class DaemonServer:
    """One bounded RPC server for one already-verified daemon identity."""

    def __init__(
        self,
        *,
        endpoint: Path,
        instance_id: str,
        project_root_hash: str,
        start_nonce_hash: str,
        daemon_version: str,
        project_view_schema_version: str,
        max_frame_bytes: int,
        allowed_methods: Collection[str],
        status_provider: StatusProvider,
        mutation_handler: MutationHandler | None = None,
        lease_validator: LeaseValidator | None = None,
        request_queue_size: int = 128,
        event_queue_size: int = 128,
    ) -> None:
        if type(max_frame_bytes) is not int or max_frame_bytes <= 0:
            raise ValueError("maximum frame size is invalid")
        if type(request_queue_size) is not int or request_queue_size <= 0:
            raise ValueError("request queue size is invalid")
        if type(event_queue_size) is not int or event_queue_size <= 0:
            raise ValueError("event queue size is invalid")
        methods = frozenset(allowed_methods)
        if not {"handshake", "status"}.issubset(methods):
            raise ValueError("daemon methods must include handshake and status")
        self.endpoint = Path(endpoint)
        self.instance_id = instance_id
        self.project_root_hash = project_root_hash
        self.start_nonce_hash = start_nonce_hash
        self.daemon_version = daemon_version
        self.project_view_schema_version = project_view_schema_version
        self.max_frame_bytes = max_frame_bytes
        self.allowed_methods = methods
        self.status_provider = status_provider
        self.mutation_handler = mutation_handler
        self.lease_validator = lease_validator
        self.request_queue_size = request_queue_size
        self.event_queue_size = event_queue_size
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[_Connection] = set()
        self._bound_identity: tuple[int, int] | None = None

    @property
    def connection_count(self) -> int:
        return sum(not connection.closed for connection in self._connections)

    async def start(self) -> None:
        if self._server is not None:
            return
        self.endpoint.parent.mkdir(parents=True, exist_ok=True)
        if self.endpoint.exists() or self.endpoint.is_symlink():
            raise DaemonServerError("daemon endpoint already exists")
        listener: socket.socket | None = None
        try:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.setblocking(False)
            listener.bind(str(self.endpoint))
            metadata = self.endpoint.lstat()
            if not stat.S_ISSOCK(metadata.st_mode):
                raise DaemonServerError("daemon endpoint is not a socket")
            self._bound_identity = (metadata.st_dev, metadata.st_ino)
            os.chmod(self.endpoint, 0o600)
            self._server = await asyncio.start_unix_server(
                self._handle_connection,
                sock=listener,
                limit=self.max_frame_bytes + 1,
            )
            listener = None
        except Exception:
            if listener is not None:
                listener.close()
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
                self._server = None
            self._unlink_owned_socket()
            raise

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        connections = list(self._connections)
        if connections:
            await asyncio.gather(
                *(self._close_connection(connection) for connection in connections),
                return_exceptions=True,
            )
        self._unlink_owned_socket()

    def _unlink_owned_socket(self) -> None:
        identity, self._bound_identity = self._bound_identity, None
        if identity is None:
            return
        try:
            metadata = self.endpoint.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(metadata.st_mode) and (metadata.st_dev, metadata.st_ino) == identity:
            self.endpoint.unlink()

    async def wait_connection_count(
        self, expected: int, *, timeout_seconds: float
    ) -> int:
        async def wait() -> int:
            while self.connection_count != expected:
                await asyncio.sleep(0)
            return self.connection_count

        return await asyncio.wait_for(wait(), timeout=timeout_seconds)

    def publish_event(self, event: RpcEvent) -> None:
        # Encoding here validates size before any subscriber state changes.
        encode_event(event, max_bytes=self.max_frame_bytes)
        for connection in tuple(self._connections):
            if connection.closed or not connection.subscribed:
                continue
            try:
                connection.event_queue.put_nowait(event)
            except asyncio.QueueFull:
                asyncio.create_task(self._close_connection(connection))

    async def _read_frame(self, reader: asyncio.StreamReader) -> bytes:
        try:
            frame = await reader.readuntil(b"\n")
        except asyncio.IncompleteReadError as exc:
            if not exc.partial:
                raise EOFError from None
            raise RpcProtocolError("incomplete_frame", "frame is incomplete") from None
        except asyncio.LimitOverrunError:
            raise RpcProtocolError("frame_too_large", "frame exceeds maximum size") from None
        if len(frame) > self.max_frame_bytes:
            raise RpcProtocolError("frame_too_large", "frame exceeds maximum size")
        return frame

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        connection = _Connection(
            reader=reader,
            writer=writer,
            request_queue=asyncio.Queue(self.request_queue_size),
            event_queue=asyncio.Queue(self.event_queue_size),
        )
        self._connections.add(connection)
        try:
            frame = await self._read_frame(reader)
            request = decode_request(
                frame,
                max_bytes=self.max_frame_bytes,
                allowed_methods={"handshake"},
            )
            handshake = negotiate_handshake(
                request,
                project_root_hash=self.project_root_hash,
                daemon_version=self.daemon_version,
                project_view_version=self.project_view_schema_version,
            )
            connection.compatible = bool(handshake["compatible"])
            await self._send_response(
                connection,
                RpcResponse(request.request_id, True, handshake, None),
            )
            reader_task = asyncio.create_task(self._reader_loop(connection))
            worker_task = asyncio.create_task(self._request_loop(connection))
            event_task = asyncio.create_task(self._event_loop(connection))
            connection.tasks.update({reader_task, worker_task, event_task})
            done, _ = await asyncio.wait(
                connection.tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        except (EOFError, RpcProtocolError, ConnectionError, BrokenPipeError):
            pass
        finally:
            await self._close_connection(connection)

    async def _reader_loop(self, connection: _Connection) -> None:
        while not connection.closed:
            frame = await self._read_frame(connection.reader)
            request = decode_request(
                frame,
                max_bytes=self.max_frame_bytes,
                allowed_methods=self.allowed_methods,
            )
            try:
                connection.request_queue.put_nowait(request)
            except asyncio.QueueFull:
                raise RpcProtocolError("request_queue_full", "request queue is full")

    async def _request_loop(self, connection: _Connection) -> None:
        while not connection.closed:
            request = await connection.request_queue.get()
            if request is None:
                return
            response = await self._dispatch(connection, request)
            await self._send_response(connection, response)

    async def _event_loop(self, connection: _Connection) -> None:
        while not connection.closed:
            event = await connection.event_queue.get()
            if event is None:
                return
            await self._send_bytes(
                connection, encode_event(event, max_bytes=self.max_frame_bytes)
            )

    async def _status(self, *, compatible: bool) -> dict[str, object]:
        if not compatible:
            return {
                "mode": "daemon_status",
                "compatible": False,
                "protocol_version": DAEMON_RPC_PROTOCOL_VERSION,
                "project_view_schema_version": self.project_view_schema_version,
                "instance_id": self.instance_id,
                "project_root_hash": self.project_root_hash,
                "start_nonce_hash": self.start_nonce_hash,
            }
        value = self.status_provider()
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, Mapping):
            raise DaemonServerError("daemon status provider returned invalid data")
        return {
            **dict(value),
            "compatible": True,
            "protocol_version": DAEMON_RPC_PROTOCOL_VERSION,
            "project_view_schema_version": self.project_view_schema_version,
            "instance_id": self.instance_id,
            "project_root_hash": self.project_root_hash,
            "start_nonce_hash": self.start_nonce_hash,
        }

    async def _dispatch(
        self, connection: _Connection, request: RpcRequest
    ) -> RpcResponse:
        try:
            if request.method == "status":
                result = await self._status(compatible=connection.compatible)
            elif not connection.compatible:
                raise DaemonClientRequestError("incompatible daemon is read-only")
            elif request.method == "subscribe":
                connection.subscribed = True
                result = {"subscribed": True}
            else:
                result = await self._mutate(request)
            return RpcResponse(request.request_id, True, result, None)
        except DaemonClientRequestError as exc:
            return RpcResponse(
                request.request_id,
                False,
                None,
                {"code": exc.code, "message": str(exc)},
            )
        except Exception:
            return RpcResponse(
                request.request_id,
                False,
                None,
                {"code": "request_failed", "message": "request failed"},
            )

    async def _mutate(self, request: RpcRequest) -> dict[str, object]:
        params = dict(request.params)
        lease = params.pop("_lease", None)
        if not isinstance(lease, Mapping) or set(lease) != {"lease_id", "generation"}:
            raise DaemonClientRequestError("controller lease required", "lease_required")
        lease_id, generation = lease["lease_id"], lease["generation"]
        if type(lease_id) is not str or type(generation) is not int:
            raise DaemonClientRequestError("controller lease required", "lease_required")
        if self.lease_validator is None:
            raise DaemonClientRequestError("controller lease required", "lease_required")
        try:
            self.lease_validator(lease_id, generation)
        except LeaseError as exc:
            reason = str(exc)
            if reason not in {
                "controller lease required",
                "stale controller lease",
                "controller lease expired",
            }:
                reason = "controller lease required"
            raise DaemonClientRequestError(reason, "lease_required") from None
        except Exception:
            raise DaemonClientRequestError("controller lease required", "lease_required") from None
        if self.mutation_handler is None:
            raise DaemonClientRequestError("mutation handler is unavailable", "unavailable")
        value = self.mutation_handler(request.method, params)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, Mapping):
            raise DaemonClientRequestError("mutation result is invalid", "invalid_result")
        return dict(value)

    async def _send_response(
        self, connection: _Connection, response: RpcResponse
    ) -> None:
        await self._send_bytes(
            connection, encode_response(response, max_bytes=self.max_frame_bytes)
        )

    async def _send_bytes(self, connection: _Connection, payload: bytes) -> None:
        async with connection.write_lock:
            if connection.closed:
                raise ConnectionError("connection is closed")
            connection.writer.write(payload)
            await connection.writer.drain()

    async def _close_connection(self, connection: _Connection) -> None:
        if connection.closed:
            return
        connection.closed = True
        current = asyncio.current_task()
        tasks = [task for task in connection.tasks if task is not current and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        connection.writer.close()
        try:
            await connection.writer.wait_closed()
        except (ConnectionError, BrokenPipeError):
            pass
        self._connections.discard(connection)


class DaemonClientRequestError(RuntimeError):
    def __init__(self, message: str, code: str = "request_blocked") -> None:
        self.code = code
        super().__init__(message)
