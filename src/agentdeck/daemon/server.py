"""Bounded project-local Unix socket RPC server."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
import inspect
import math
import os
from pathlib import Path
import socket
import threading

from .lifecycle import DaemonEndpointBinding, DaemonIdentityError, bind_daemon_endpoint
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
MutationResponseSentHandler = Callable[
    [str, dict[str, object]], object | Awaitable[object]
]


_DAEMON_BIND_LOCK = threading.RLock()


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
    closing: bool = False
    close_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    close_task: asyncio.Task[None] | None = None
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
        lease_exempt_methods: Collection[str] = (),
        mutation_handler: MutationHandler | None = None,
        lease_validator: LeaseValidator | None = None,
        mutation_response_sent_handler: MutationResponseSentHandler | None = None,
        request_queue_size: int = 128,
        event_queue_size: int = 128,
        read_timeout_seconds: float = 10,
        write_timeout_seconds: float = 10,
        mutation_drain_timeout_seconds: float = 0.25,
    ) -> None:
        if type(max_frame_bytes) is not int or max_frame_bytes <= 0:
            raise ValueError("maximum frame size is invalid")
        if type(request_queue_size) is not int or request_queue_size <= 0:
            raise ValueError("request queue size is invalid")
        if type(event_queue_size) is not int or event_queue_size <= 0:
            raise ValueError("event queue size is invalid")
        for name, value in (
            ("read timeout", read_timeout_seconds),
            ("write timeout", write_timeout_seconds),
            ("mutation drain timeout", mutation_drain_timeout_seconds),
        ):
            if type(value) not in {int, float} or not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"daemon {name} must be a positive finite number")
        methods = frozenset(allowed_methods)
        if not {"handshake", "status"}.issubset(methods):
            raise ValueError("daemon methods must include handshake and status")
        lease_exempt = frozenset(lease_exempt_methods)
        if (
            not lease_exempt.issubset(methods)
            or not lease_exempt.issubset({
                "controller.acquire", "permission.confirm-handle"
            })
        ):
            raise ValueError("daemon lease-exempt methods are invalid")
        self.endpoint = Path(endpoint)
        self.instance_id = instance_id
        self.project_root_hash = project_root_hash
        self.start_nonce_hash = start_nonce_hash
        self.daemon_version = daemon_version
        self.project_view_schema_version = project_view_schema_version
        self.max_frame_bytes = max_frame_bytes
        self.allowed_methods = methods
        self.lease_exempt_methods = lease_exempt
        self.status_provider = status_provider
        self.mutation_handler = mutation_handler
        self.lease_validator = lease_validator
        self.mutation_response_sent_handler = mutation_response_sent_handler
        self.request_queue_size = request_queue_size
        self.event_queue_size = event_queue_size
        self.read_timeout_seconds = float(read_timeout_seconds)
        self.write_timeout_seconds = float(write_timeout_seconds)
        self.mutation_drain_timeout_seconds = float(mutation_drain_timeout_seconds)
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[_Connection] = set()
        self._bound_identity: tuple[int, int] | None = None
        self._endpoint_binding: DaemonEndpointBinding | None = None
        self._close_tasks: set[asyncio.Task[None]] = set()
        self._handler_tasks: set[asyncio.Task[None]] = set()
        self._mutation_tasks: set[asyncio.Task[dict[str, object]]] = set()
        self._activity_generation = 0

    @property
    def connection_count(self) -> int:
        return sum(not connection.closed for connection in self._connections)

    @property
    def activity_generation(self) -> int:
        return self._activity_generation

    def _record_activity(self) -> None:
        self._activity_generation += 1

    @property
    def pending_close_task_count(self) -> int:
        return sum(not task.done() for task in self._close_tasks)

    @property
    def active_handler_task_count(self) -> int:
        return sum(not task.done() for task in self._handler_tasks)

    @property
    def active_mutation_task_count(self) -> int:
        return sum(not task.done() for task in self._mutation_tasks)

    async def start(self) -> None:
        if self._server is not None:
            return
        if threading.active_count() != 1:
            raise DaemonServerError(
                "daemon server startup requires a single-threaded process"
            )
        binding: DaemonEndpointBinding | None = None
        bound_identity: tuple[int, int] | None = None
        listener: socket.socket | None = None
        try:
            root = self.endpoint.parents[2]
            binding = bind_daemon_endpoint(root)
            if binding.endpoint.socket_path != self.endpoint:
                raise DaemonServerError("daemon endpoint escaped project runtime")
            binding.assert_socket_absent()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.setblocking(False)
            runtime_fd = binding.duplicate_runtime_fd()
            with _DAEMON_BIND_LOCK:
                cwd_fd: int | None = None
                try:
                    cwd_fd = os.open(".", os.O_RDONLY)
                    os.fchdir(runtime_fd)
                    previous_umask = os.umask(0o177)
                    try:
                        listener.bind("daemon.sock")
                    finally:
                        os.umask(previous_umask)
                finally:
                    try:
                        if cwd_fd is not None:
                            os.fchdir(cwd_fd)
                    finally:
                        if cwd_fd is not None:
                            os.close(cwd_fd)
                        os.close(runtime_fd)
            bound_identity = binding.held_socket_identity()
            binding.assert_socket_identity(bound_identity)
            binding.assert_socket_mode(bound_identity, 0o600)
            self._server = await asyncio.start_unix_server(
                self._handle_connection,
                sock=listener,
                limit=self.max_frame_bytes + 1,
            )
            listener = None
            binding.assert_socket_identity(bound_identity)
            self._bound_identity = bound_identity
            self._endpoint_binding = binding
            binding = None
        except DaemonIdentityError as exc:
            raise DaemonServerError("daemon endpoint identity is unverified") from exc
        except Exception:
            raise
        finally:
            if listener is not None:
                listener.close()
            if binding is not None:
                if bound_identity is not None:
                    binding.unlink_socket_if_identity(bound_identity)
                binding.close()
            if self._endpoint_binding is None and self._server is not None:
                self._server.close()
                await self._server.wait_closed()
                self._server = None

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
        handler_tasks = tuple(
            task for task in self._handler_tasks if not task.done()
        )
        for task in handler_tasks:
            task.cancel()
        if handler_tasks:
            await asyncio.gather(*handler_tasks, return_exceptions=True)
        if server is not None:
            await server.wait_closed()
        for connection in tuple(self._connections):
            self._schedule_close(connection)
        close_tasks = tuple(self._close_tasks)
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        await self._drain_mutations(self.mutation_drain_timeout_seconds)
        self._unlink_owned_socket()

    async def _drain_mutations(self, timeout_seconds: float) -> None:
        tasks = tuple(task for task in self._mutation_tasks if not task.done())
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_seconds)

    async def wait_for_mutations(self, *, timeout_seconds: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while self.active_mutation_task_count:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise DaemonServerError("daemon mutation drain timed out")
            await self._drain_mutations(remaining)

    def _unlink_owned_socket(self) -> None:
        identity, self._bound_identity = self._bound_identity, None
        binding, self._endpoint_binding = self._endpoint_binding, None
        if binding is None:
            return
        try:
            if identity is not None:
                binding.unlink_socket_if_identity(identity)
        finally:
            binding.close()

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
            if connection.closed or connection.closing or not connection.subscribed:
                continue
            try:
                connection.event_queue.put_nowait(event)
            except asyncio.QueueFull:
                self._schedule_close(connection)

    def _schedule_close(self, connection: _Connection) -> None:
        if connection.closed or connection.closing:
            return
        connection.closing = True
        task = asyncio.create_task(self._close_connection(connection))
        connection.close_task = task
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)

    async def _read_frame(self, reader: asyncio.StreamReader) -> bytes:
        try:
            frame = await asyncio.wait_for(
                reader.readuntil(b"\n"), timeout=self.read_timeout_seconds
            )
        except asyncio.IncompleteReadError as exc:
            if not exc.partial:
                raise EOFError from None
            raise RpcProtocolError("incomplete_frame", "frame is incomplete") from None
        except asyncio.LimitOverrunError:
            raise RpcProtocolError("frame_too_large", "frame exceeds maximum size") from None
        except asyncio.TimeoutError:
            raise RpcProtocolError("read_timed_out", "frame read timed out") from None
        if len(frame) > self.max_frame_bytes:
            raise RpcProtocolError("frame_too_large", "frame exceeds maximum size")
        return frame

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        handler_task = asyncio.current_task()
        if handler_task is not None:
            self._handler_tasks.add(handler_task)
        connection = _Connection(
            reader=reader,
            writer=writer,
            request_queue=asyncio.Queue(self.request_queue_size),
            event_queue=asyncio.Queue(self.event_queue_size),
        )
        self._connections.add(connection)
        self._record_activity()
        try:
            frame = await self._read_frame(reader)
            request = decode_request(
                frame,
                max_bytes=self.max_frame_bytes,
                allowed_methods={"handshake"},
            )
            self._record_activity()
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
            try:
                await self._close_connection(connection)
            finally:
                if handler_task is not None:
                    self._handler_tasks.discard(handler_task)

    async def _reader_loop(self, connection: _Connection) -> None:
        while not connection.closed:
            frame = await self._read_frame(connection.reader)
            request = decode_request(
                frame,
                max_bytes=self.max_frame_bytes,
                allowed_methods=self.allowed_methods,
            )
            self._record_activity()
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
            if (
                response.ok
                and request.method not in {"status", "subscribe"}
                and isinstance(response.result, Mapping)
            ):
                await self._mutation_response_sent(
                    request.method, dict(response.result)
                )

    async def _mutation_response_sent(
        self, method: str, result: dict[str, object]
    ) -> None:
        if self.mutation_response_sent_handler is None:
            return
        value = self.mutation_response_sent_handler(method, result)
        if inspect.isawaitable(value):
            await value

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
        if request.method in self.lease_exempt_methods:
            if "_lease" in params:
                raise DaemonClientRequestError(
                    "lease-exempt request cannot carry controller authority",
                    "invalid_request",
                )
            return await self._start_mutation(request.method, params)
        lease = params.get("_lease")
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
        return await self._start_mutation(request.method, params)

    async def _start_mutation(
        self, method: str, params: dict[str, object]
    ) -> dict[str, object]:
        if self.mutation_handler is None:
            raise DaemonClientRequestError("mutation handler is unavailable", "unavailable")
        task = asyncio.create_task(self._run_mutation_handler(method, params))
        self._mutation_tasks.add(task)
        task.add_done_callback(self._mutation_finished)
        return await asyncio.shield(task)

    def _mutation_finished(self, task: asyncio.Task[dict[str, object]]) -> None:
        self._mutation_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except Exception:
            pass

    async def _run_mutation_handler(
        self, method: str, params: dict[str, object]
    ) -> dict[str, object]:
        assert self.mutation_handler is not None
        value = self.mutation_handler(method, params)
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
        try:
            async with asyncio.timeout(self.write_timeout_seconds):
                async with connection.write_lock:
                    if connection.closed:
                        raise ConnectionError("connection is closed")
                    connection.writer.write(payload)
                    await connection.writer.drain()
        except asyncio.TimeoutError:
            raise ConnectionError("daemon write timed out") from None

    async def _close_connection(self, connection: _Connection) -> None:
        async with connection.close_lock:
            if connection.closed:
                return
            connection.closing = True
            connection.closed = True
            current = asyncio.current_task()
            tasks = [
                task
                for task in connection.tasks
                if task is not current
            ]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            connection.writer.close()
            try:
                await asyncio.wait_for(
                    connection.writer.wait_closed(), timeout=self.write_timeout_seconds
                )
            except (asyncio.TimeoutError, ConnectionError, BrokenPipeError):
                pass
            self._connections.discard(connection)


class DaemonClientRequestError(RuntimeError):
    def __init__(self, message: str, code: str = "request_blocked") -> None:
        self.code = code
        super().__init__(message)
