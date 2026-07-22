"""Bounded project-local Unix-socket transport for Observer publications."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import socket
import stat
import threading

from agentdeck.adapters.observer_protocol import (
    MAX_FRAME_BYTES, ObserverProtocolError, acknowledgement_frame,
    binding_frame, binding_from_frame, cursor_from_dict, decode_frame,
    encode_frame, event_frame, event_from_frame, handshake_frame,
)
from agentdeck.ports.observer import (
    ObserverAcknowledgement, ObserverBinding, ObserverCursor,
    ObserverPublication, ObserverSubscription,
)


_RUNTIME_DIRECTORY = ".agentdeck/observer-runtime"
_SOCKET_NAME = "observer.sock"
_QUEUE_SIZE = 256
_IO_TIMEOUT = 2.0


class ObserverIPCError(RuntimeError):
    ALLOWED = frozenset({
        "observer_endpoint_unavailable", "observer_endpoint_replaced",
        "observer_subscription_mismatch", "observer_delivery_failed",
        "observer_protocol_invalid", "observer_shutdown_failed",
    })

    def __init__(self, code: str) -> None:
        if code not in self.ALLOWED:
            raise ValueError("unknown Observer IPC error")
        self.code = code
        super().__init__(code)


def _project_root(value: Path) -> Path:
    root = Path(value)
    if root.is_symlink() or not root.is_dir():
        raise ObserverIPCError("observer_endpoint_unavailable")
    resolved = root.resolve(strict=True)
    if root.absolute() != resolved:
        raise ObserverIPCError("observer_endpoint_unavailable")
    return resolved


def _runtime(root: Path, *, create: bool) -> Path:
    state = root / ".agentdeck"
    if state.exists() or state.is_symlink():
        if state.is_symlink() or not state.is_dir():
            raise ObserverIPCError("observer_endpoint_replaced")
    elif create:
        state.mkdir(mode=0o700)
    else:
        raise ObserverIPCError("observer_endpoint_unavailable")
    state_info = state.stat()
    if state_info.st_uid != os.getuid() or stat.S_IMODE(state_info.st_mode) != 0o700:
        raise ObserverIPCError("observer_endpoint_replaced")
    runtime = root / _RUNTIME_DIRECTORY
    if runtime.exists() or runtime.is_symlink():
        if runtime.is_symlink() or not runtime.is_dir():
            raise ObserverIPCError("observer_endpoint_replaced")
    elif create:
        runtime.mkdir(mode=0o700, parents=True)
    else:
        raise ObserverIPCError("observer_endpoint_unavailable")
    info = runtime.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ObserverIPCError("observer_endpoint_replaced")
    return runtime


def _endpoint_identity(path: Path) -> tuple[int, int, int, int]:
    try:
        info = path.lstat()
    except OSError:
        raise ObserverIPCError("observer_endpoint_unavailable") from None
    if path.is_symlink() or not stat.S_ISSOCK(info.st_mode):
        raise ObserverIPCError("observer_endpoint_replaced")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ObserverIPCError("observer_endpoint_replaced")
    return info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)


def _address(root: Path, endpoint: Path) -> str:
    try:
        relative = endpoint.relative_to(Path.cwd().resolve(strict=True))
    except ValueError:
        relative = None
    address = str(relative if relative is not None else endpoint)
    if len(address.encode("utf-8", "strict")) >= 100:
        raise ObserverIPCError("observer_endpoint_unavailable")
    return address


def _read_frame(connection: socket.socket, buffer: bytearray) -> bytes:
    while True:
        split = buffer.find(b"\n")
        if split >= 0:
            wire = bytes(buffer[:split + 1])
            del buffer[:split + 1]
            return wire
        if len(buffer) >= MAX_FRAME_BYTES:
            raise ObserverProtocolError("observer_frame_oversize")
        chunk = connection.recv(min(64 * 1024, MAX_FRAME_BYTES - len(buffer)))
        if not chunk:
            raise ObserverIPCError("observer_delivery_failed")
        buffer.extend(chunk)


class _Subscriber:
    def __init__(
        self, connection: socket.socket, subscription: ObserverSubscription,
    ) -> None:
        self.connection = connection
        self.subscription = subscription
        self.outbound: queue.Queue[
            tuple[ObserverBinding, ObserverCursor | None, tuple[ObserverPublication, ...]]
            | None
        ] = queue.Queue(_QUEUE_SIZE)
        self.binding: ObserverBinding | None = None
        self.planned_binding: ObserverBinding | None = None
        self.send_lock = threading.Lock()

    def send(self, frame: dict[str, object]) -> None:
        with self.send_lock:
            self.connection.sendall(encode_frame(frame))


class UnixObserverServer:
    """Foreground Broker channel with bounded per-subscriber queues."""

    def __init__(
        self, *, project_root: Path, project_id: str, acknowledge,
        cursor_reader,
    ) -> None:
        root = _project_root(project_root)
        ObserverSubscription(project_id, "ses_validation", "agt_validation")
        if not callable(acknowledge) or not callable(cursor_reader):
            raise TypeError("Observer server capabilities must be callable")
        self._root, self._project_id = root, project_id
        self._acknowledge, self._cursor_reader = acknowledge, cursor_reader
        self.endpoint = root / _RUNTIME_DIRECTORY / _SOCKET_NAME
        self._listener: socket.socket | None = None
        self._identity: tuple[int, int, int, int] | None = None
        self._subscribers: list[_Subscriber] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._acknowledgements: queue.Queue[ObserverAcknowledgement] = queue.Queue(
            _QUEUE_SIZE
        )
        self._owner_thread = threading.get_ident()
        self._history: dict[ObserverBinding, list[ObserverPublication]] = {}
        self._contained_acknowledgements = 0

    @property
    def contained_acknowledgement_count(self) -> int:
        return self._contained_acknowledgements

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @property
    def pending_acknowledgement_count(self) -> int:
        return self._acknowledgements.qsize()

    def start(self) -> None:
        if self._listener is not None:
            raise ObserverIPCError("observer_endpoint_replaced")
        _runtime(self._root, create=True)
        if self.endpoint.exists() or self.endpoint.is_symlink():
            raise ObserverIPCError("observer_endpoint_replaced")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.settimeout(0.1)
        try:
            listener.bind(_address(self._root, self.endpoint))
            os.chmod(self.endpoint, 0o600, follow_symlinks=False)
            self._identity = _endpoint_identity(self.endpoint)
            listener.listen(16)
        except Exception:
            listener.close()
            if self.endpoint.exists() and self.endpoint.is_socket():
                self.endpoint.unlink()
            raise
        self._listener = listener
        self._spawn(self._accept_loop)

    def publish(self, publication: ObserverPublication) -> bool:
        if type(publication) is not ObserverPublication:
            raise TypeError("Observer server publishes exact publications")
        if self._identity is None or _endpoint_identity(self.endpoint) != self._identity:
            raise ObserverIPCError("observer_endpoint_replaced")
        with self._lock:
            history = self._history.setdefault(publication.binding, [])
            history.append(publication)
            del history[:-_QUEUE_SIZE]
            targets = tuple(
                item for item in self._subscribers
                if (
                    item.subscription.project_id,
                    item.subscription.session_id,
                    item.subscription.agent_id,
                ) == (
                    publication.binding.project_id,
                    publication.binding.session_id,
                    publication.binding.agent_id,
                )
            )
        delivered = False
        prior = self._cursor_reader(publication.binding) if targets else None
        if prior is not None and type(prior) is not ObserverCursor:
            raise ObserverIPCError("observer_protocol_invalid")
        for target in targets:
            try:
                if target.planned_binding == publication.binding:
                    delivery = (publication,)
                else:
                    delivery = self._replay(history, prior)
                    if not delivery:
                        continue
                    target.planned_binding = publication.binding
                target.outbound.put_nowait((publication.binding, prior, delivery))
                delivered = True
            except queue.Full:
                continue
        return delivered

    @staticmethod
    def _replay(
        history: list[ObserverPublication], prior: ObserverCursor | None,
    ) -> tuple[ObserverPublication, ...]:
        start = 0
        if prior is not None:
            matches = [
                index for index, item in enumerate(history) if item.cursor == prior
            ]
            if len(matches) != 1:
                return ()
            start = matches[0]
        selected = tuple(history[start:])
        expected = prior.sequence if prior is not None else 1
        if not selected or any(
            item.cursor.sequence != expected + index
            for index, item in enumerate(selected)
        ):
            return ()
        return selected

    def drain_acknowledgements(self, limit: int = _QUEUE_SIZE) -> int:
        if threading.get_ident() != self._owner_thread:
            raise ObserverIPCError("observer_protocol_invalid")
        if type(limit) is not int or not 1 <= limit <= _QUEUE_SIZE:
            raise ValueError("Observer acknowledgement drain limit is invalid")
        drained = 0
        while drained < limit:
            try:
                acknowledgement = self._acknowledgements.get_nowait()
            except queue.Empty:
                break
            try:
                self._acknowledge(acknowledgement)
            except Exception:
                # One acknowledgement's failure is observation degradation; it
                # must never abort draining the rest of the queue or crash the
                # foreground pump. The durable cursor writer remains the sole
                # authority, so a contained failure simply never advances it.
                self._contained_acknowledgements += 1
            drained += 1
        return drained

    def close(self) -> None:
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.outbound.put_nowait(None)
            except queue.Full:
                pass
            try:
                subscriber.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            subscriber.connection.close()
        for thread in tuple(self._threads):
            thread.join(timeout=_IO_TIMEOUT)
        if self.endpoint.exists() and self._identity is not None:
            if _endpoint_identity(self.endpoint) != self._identity:
                raise ObserverIPCError("observer_endpoint_replaced")
            self.endpoint.unlink()

    def _spawn(self, target, *args) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _accept_loop(self) -> None:
        listener = self._listener
        while listener is not None and not self._stop.is_set():
            try:
                connection, _ = listener.accept()
                connection.settimeout(_IO_TIMEOUT)
                self._spawn(self._serve, connection)
            except TimeoutError:
                continue
            except OSError:
                return

    def _serve(self, connection: socket.socket) -> None:
        subscriber: _Subscriber | None = None
        buffer = bytearray()
        try:
            frame = decode_frame(_read_frame(connection, buffer))
            if frame["type"] != "handshake" or frame["project_id"] != self._project_id:
                raise ObserverIPCError("observer_subscription_mismatch")
            subscription = ObserverSubscription(
                frame["project_id"], frame["session_id"], frame["agent_id"],
            )
            subscriber = _Subscriber(connection, subscription)
            with self._lock:
                self._subscribers.append(subscriber)
            self._spawn(self._write_loop, subscriber)
            while not self._stop.is_set():
                value = decode_frame(
                    _read_frame(connection, buffer), expected=subscriber.binding,
                )
                if value["type"] == "close":
                    return
                if value["type"] != "ack" or subscriber.binding is None:
                    raise ObserverIPCError("observer_protocol_invalid")
                self._acknowledgements.put_nowait(
                    ObserverAcknowledgement(cursor_from_dict(value))
                )
        except Exception:
            return
        finally:
            if subscriber is not None:
                with self._lock:
                    if subscriber in self._subscribers:
                        self._subscribers.remove(subscriber)
            try:
                connection.close()
            except OSError:
                pass

    def _write_loop(self, subscriber: _Subscriber) -> None:
        try:
            while not self._stop.is_set():
                try:
                    item = subscriber.outbound.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is None:
                    return
                binding, cursor, publications = item
                if binding != subscriber.binding:
                    subscriber.send(binding_frame(binding, cursor))
                    subscriber.binding = binding
                for publication in publications:
                    subscriber.send(event_frame(publication))
        except Exception:
            try:
                subscriber.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class UnixObserverClient:
    def __init__(
        self, connection: socket.socket, endpoint: Path,
        identity: tuple[int, int, int, int], subscription: ObserverSubscription,
    ) -> None:
        self._connection, self._endpoint = connection, endpoint
        self._identity, self._subscription = identity, subscription
        self._buffer = bytearray()
        self._binding: ObserverBinding | None = None

    @classmethod
    def connect(
        cls, *, project_root: Path, subscription: ObserverSubscription,
    ) -> "UnixObserverClient":
        if type(subscription) is not ObserverSubscription:
            raise TypeError("Observer client requires an exact subscription")
        root = _project_root(project_root)
        endpoint = _runtime(root, create=False) / _SOCKET_NAME
        identity = _endpoint_identity(endpoint)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(_IO_TIMEOUT)
        try:
            connection.connect(_address(root, endpoint))
            if _endpoint_identity(endpoint) != identity:
                raise ObserverIPCError("observer_endpoint_replaced")
            connection.sendall(encode_frame(handshake_frame(subscription)))
        except Exception:
            connection.close()
            raise
        return cls(connection, endpoint, identity, subscription)

    def receive_binding(self) -> tuple[ObserverBinding, ObserverCursor | None]:
        value = self._receive()
        if value["type"] != "binding":
            raise ObserverIPCError("observer_protocol_invalid")
        binding = binding_from_frame(value)
        self._binding = binding
        cursor = None if value["cursor"] is None else cursor_from_dict(
            value["cursor"], binding=value,
        )
        return binding, cursor

    def receive_event(self):
        if self._binding is None:
            raise ObserverIPCError("observer_protocol_invalid")
        value = self._receive()
        if value["type"] != "event":
            raise ObserverIPCError("observer_protocol_invalid")
        return event_from_frame(value)

    def acknowledge(self, cursor: ObserverCursor) -> None:
        if self._binding is None or cursor.binding != self._binding:
            raise ObserverIPCError("observer_subscription_mismatch")
        self._connection.sendall(encode_frame(acknowledgement_frame(cursor)))

    def close(self) -> None:
        try:
            self._connection.sendall(encode_frame({
                "version": "observer-ipc/v1", "type": "close",
                "reason": "client_closed",
            }))
        except OSError:
            pass
        self._connection.close()

    def _receive(self) -> dict[str, object]:
        if _endpoint_identity(self._endpoint) != self._identity:
            raise ObserverIPCError("observer_endpoint_replaced")
        try:
            return decode_frame(
                _read_frame(self._connection, self._buffer),
                expected=self._binding or self._subscription,
            )
        except ObserverProtocolError:
            raise ObserverIPCError("observer_protocol_invalid") from None


__all__ = ["ObserverIPCError", "UnixObserverClient", "UnixObserverServer"]
