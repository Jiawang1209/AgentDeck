from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from types import TracebackType
from typing import BinaryIO, Callable, Literal, Mapping
import uuid


DAEMON_STATES = frozenset(
    {
        "starting",
        "ready",
        "busy",
        "idle_grace",
        "stopping",
        "stopped",
        "blocked",
    }
)

_DAEMON_RECORD_FIELDS = {
    "instance_id",
    "project_root_hash",
    "start_nonce_hash",
    "state",
    "created_at",
    "updated_at",
}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class DaemonIdentityError(RuntimeError):
    """Raised when endpoint identity cannot be verified without guessing."""


@dataclass(frozen=True)
class DaemonEndpoint:
    metadata_path: Path
    socket_path: Path
    lock_path: Path


@dataclass
class _BoundEndpoint:
    endpoint: DaemonEndpoint
    runtime_fd: int
    runtime_device: int
    runtime_inode: int
    closed: bool = False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self.runtime_fd)


HealthProbe = Callable[[Mapping[str, object]], Mapping[str, object] | None]


@dataclass
class _OwnershipCapability:
    lock_file: BinaryIO | None
    bound_endpoint: _BoundEndpoint | None
    released: bool = False


_OWNERSHIP_FACTORY_TOKEN = object()


@dataclass(frozen=True, init=False)
class DaemonOwnership:
    role: Literal["owner", "follower"]
    instance_id: str
    endpoint: DaemonEndpoint
    project_root_hash: str
    start_nonce_hash: str
    pid: int
    _capability: _OwnershipCapability = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        role: Literal["owner", "follower"],
        instance_id: str,
        endpoint: DaemonEndpoint,
        project_root_hash: str,
        start_nonce_hash: str,
        pid: int,
        _factory_token: object | None = None,
        _lock_file: BinaryIO | None = None,
        _bound_endpoint: _BoundEndpoint | None = None,
    ) -> None:
        if _factory_token is not _OWNERSHIP_FACTORY_TOKEN:
            raise TypeError("DaemonOwnership cannot be constructed directly")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "instance_id", instance_id)
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "project_root_hash", project_root_hash)
        object.__setattr__(self, "start_nonce_hash", start_nonce_hash)
        object.__setattr__(self, "pid", pid)
        object.__setattr__(
            self,
            "_capability",
            _OwnershipCapability(_lock_file, _bound_endpoint),
        )

    def health_proof(self) -> dict[str, object]:
        return {
            "instance_id": self.instance_id,
            "project_root_hash": self.project_root_hash,
            "start_nonce_hash": self.start_nonce_hash,
            "pid": self.pid,
            "healthy": True,
        }

    def release(self) -> None:
        capability = self._capability
        if capability.released:
            return
        capability.released = True
        try:
            if capability.lock_file is not None:
                try:
                    fcntl.flock(capability.lock_file.fileno(), fcntl.LOCK_UN)
                finally:
                    capability.lock_file.close()
        finally:
            if capability.bound_endpoint is not None:
                capability.bound_endpoint.close()

    def __enter__(self) -> DaemonOwnership:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def _new_ownership(
    *,
    role: Literal["owner", "follower"],
    instance_id: str,
    endpoint: DaemonEndpoint,
    project_root_hash: str,
    start_nonce_hash: str,
    pid: int,
    lock_file: BinaryIO | None = None,
    bound_endpoint: _BoundEndpoint | None = None,
) -> DaemonOwnership:
    return DaemonOwnership(
        role=role,
        instance_id=instance_id,
        endpoint=endpoint,
        project_root_hash=project_root_hash,
        start_nonce_hash=start_nonce_hash,
        pid=pid,
        _factory_token=_OWNERSHIP_FACTORY_TOKEN,
        _lock_file=lock_file,
        _bound_endpoint=bound_endpoint,
    )


_ENDPOINT_METADATA_FIELDS = {
    "instance_id",
    "project_root_hash",
    "start_nonce_hash",
    "pid",
}


def _canonical_project_root(root: Path) -> Path:
    return Path(root).expanduser().resolve()


def project_root_hash(root: Path) -> str:
    canonical = _canonical_project_root(root)
    return hashlib.sha256(os.fsencode(canonical)).hexdigest()


def daemon_endpoint(root: Path) -> DaemonEndpoint:
    runtime = _canonical_project_root(root) / ".agentdeck" / "runtime"
    return DaemonEndpoint(
        metadata_path=runtime / "daemon.json",
        socket_path=runtime / "daemon.sock",
        lock_path=runtime / "daemon.lock",
    )


def _open_directory_no_follow(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise DaemonIdentityError("daemon runtime symlink is forbidden") from exc
        raise DaemonIdentityError("daemon project directory is unavailable") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise DaemonIdentityError("daemon runtime path must be a directory")
    return descriptor


def _open_child_directory_no_follow(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise DaemonIdentityError("daemon runtime symlink is forbidden") from exc
        raise DaemonIdentityError("daemon runtime directory is unavailable") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise DaemonIdentityError("daemon runtime path must be a directory")
    return descriptor


def _secure_runtime_fd(root: Path) -> tuple[Path, int]:
    canonical = _canonical_project_root(root)
    root_fd = _open_directory_no_follow(canonical)
    agentdeck_fd: int | None = None
    runtime_fd: int | None = None
    try:
        agentdeck_fd = _open_child_directory_no_follow(root_fd, ".agentdeck")
        runtime_fd = _open_child_directory_no_follow(agentdeck_fd, "runtime")
        return canonical / ".agentdeck" / "runtime", runtime_fd
    except Exception:
        if runtime_fd is not None:
            os.close(runtime_fd)
        raise
    finally:
        if agentdeck_fd is not None:
            os.close(agentdeck_fd)
        os.close(root_fd)


def _reject_endpoint_symlinks(bound: _BoundEndpoint) -> None:
    for name in ("daemon.json", "daemon.sock", "daemon.lock"):
        try:
            mode = os.stat(
                name,
                dir_fd=bound.runtime_fd,
                follow_symlinks=False,
            ).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise DaemonIdentityError("daemon endpoint symlink is forbidden")


def _assert_runtime_binding(bound: _BoundEndpoint) -> None:
    if bound.closed:
        raise DaemonIdentityError("daemon runtime binding is closed")
    try:
        current_fd = _open_directory_no_follow(bound.endpoint.metadata_path.parent)
    except DaemonIdentityError as exc:
        raise DaemonIdentityError("daemon runtime binding changed") from exc
    try:
        current = os.fstat(current_fd)
        held = os.fstat(bound.runtime_fd)
    finally:
        os.close(current_fd)
    if (
        current.st_dev,
        current.st_ino,
        held.st_dev,
        held.st_ino,
    ) != (
        bound.runtime_device,
        bound.runtime_inode,
        bound.runtime_device,
        bound.runtime_inode,
    ):
        raise DaemonIdentityError("daemon runtime binding changed")


def _prepare_endpoint(root: Path) -> _BoundEndpoint:
    endpoint = daemon_endpoint(root)
    runtime, runtime_fd = _secure_runtime_fd(root)
    if endpoint.metadata_path.parent != runtime:
        os.close(runtime_fd)
        raise DaemonIdentityError("daemon endpoint escaped project runtime")
    runtime_stat = os.fstat(runtime_fd)
    bound = _BoundEndpoint(
        endpoint=endpoint,
        runtime_fd=runtime_fd,
        runtime_device=runtime_stat.st_dev,
        runtime_inode=runtime_stat.st_ino,
    )
    try:
        _reject_endpoint_symlinks(bound)
        _assert_runtime_binding(bound)
        return bound
    except Exception:
        bound.close()
        raise


def _validate_endpoint_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _ENDPOINT_METADATA_FIELDS:
        raise DaemonIdentityError("daemon endpoint metadata is invalid")
    instance_id = value["instance_id"]
    root_hash = value["project_root_hash"]
    nonce_hash = value["start_nonce_hash"]
    pid = value["pid"]
    if type(instance_id) is not str or not instance_id.strip():
        raise DaemonIdentityError("daemon endpoint metadata is invalid")
    if type(root_hash) is not str or _SHA256_PATTERN.fullmatch(root_hash) is None:
        raise DaemonIdentityError("daemon endpoint metadata is invalid")
    if type(nonce_hash) is not str or _SHA256_PATTERN.fullmatch(nonce_hash) is None:
        raise DaemonIdentityError("daemon endpoint metadata is invalid")
    if type(pid) is not int or pid <= 0:
        raise DaemonIdentityError("daemon endpoint metadata is invalid")
    return dict(value)


def _read_endpoint_metadata(bound: _BoundEndpoint) -> dict[str, object] | None:
    _assert_runtime_binding(bound)
    try:
        descriptor = os.open(
            "daemon.json",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=bound.runtime_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise DaemonIdentityError("daemon endpoint symlink is forbidden") from exc
        raise DaemonIdentityError("daemon endpoint metadata is unreadable") from exc
    try:
        try:
            stream = os.fdopen(descriptor, "r", encoding="utf-8", closefd=True)
        except Exception:
            os.close(descriptor)
            raise
        with stream:
            raw = stream.read()
    except (OSError, UnicodeError) as exc:
        raise DaemonIdentityError("daemon endpoint metadata is unreadable") from exc
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise DaemonIdentityError("daemon endpoint metadata is invalid") from exc
    return _validate_endpoint_metadata(value)


def _atomic_write_metadata(
    bound: _BoundEndpoint, metadata: Mapping[str, object]
) -> None:
    _assert_runtime_binding(bound)
    value = _validate_endpoint_metadata(metadata)
    temporary = f".daemon.json.{uuid.uuid4().hex}.tmp"
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=bound.runtime_fd,
    )
    try:
        try:
            stream = os.fdopen(descriptor, "wb", closefd=True)
        except Exception:
            os.close(descriptor)
            raise
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _assert_runtime_binding(bound)
        os.replace(
            temporary,
            "daemon.json",
            src_dir_fd=bound.runtime_fd,
            dst_dir_fd=bound.runtime_fd,
        )
        os.fsync(bound.runtime_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=bound.runtime_fd)
        except FileNotFoundError:
            pass


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _verified_health_proof(
    metadata: Mapping[str, object], health_probe: HealthProbe
) -> bool:
    try:
        proof = health_probe(metadata)
    except Exception:
        return False
    if not isinstance(proof, Mapping) or proof.get("healthy") is not True:
        return False
    return all(proof.get(field) == metadata[field] for field in _ENDPOINT_METADATA_FIELDS)


def _unlink_endpoint_files(bound: _BoundEndpoint) -> bool:
    _assert_runtime_binding(bound)
    _reject_endpoint_symlinks(bound)
    removed = False
    for name in ("daemon.sock", "daemon.json"):
        try:
            os.unlink(name, dir_fd=bound.runtime_fd)
            removed = True
        except FileNotFoundError:
            pass
    if removed:
        os.fsync(bound.runtime_fd)
    return removed


def _try_startup_lock(bound: _BoundEndpoint) -> tuple[BinaryIO, bool]:
    _assert_runtime_binding(bound)
    _reject_endpoint_symlinks(bound)
    descriptor: int | None = None
    for attempt in range(3):
        try:
            descriptor = os.open(
                "daemon.lock",
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=bound.runtime_fd,
            )
            break
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise DaemonIdentityError("daemon endpoint symlink is forbidden") from exc
            if exc.errno != errno.ENOENT or attempt == 2:
                raise
            _assert_runtime_binding(bound)
            time.sleep(0.001)
    if descriptor is None:  # pragma: no cover - loop always returns or raises
        raise DaemonIdentityError("daemon startup lock is unavailable")
    try:
        lock_file = os.fdopen(descriptor, "a+b", closefd=True)
    except Exception:
        os.close(descriptor)
        raise
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return lock_file, False
    except Exception:
        lock_file.close()
        raise
    return lock_file, True


def _ownership_holds_startup_lock(ownership: DaemonOwnership) -> bool:
    capability = ownership._capability
    lock_file = capability.lock_file
    bound = capability.bound_endpoint
    if (
        lock_file is None
        or bound is None
        or capability.released
        or lock_file.closed
        or bound.closed
    ):
        return False
    try:
        _assert_runtime_binding(bound)
        held_stat = os.fstat(lock_file.fileno())
        path_stat = os.stat(
            "daemon.lock",
            dir_fd=bound.runtime_fd,
            follow_symlinks=False,
        )
    except (DaemonIdentityError, OSError, ValueError):
        return False
    if stat.S_ISLNK(path_stat.st_mode) or (
        held_stat.st_dev,
        held_stat.st_ino,
    ) != (path_stat.st_dev, path_stat.st_ino):
        return False

    try:
        check_fd = os.open(
            "daemon.lock",
            os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=bound.runtime_fd,
        )
    except OSError:
        return False
    try:
        try:
            fcntl.flock(check_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            fcntl.flock(check_fd, fcntl.LOCK_UN)
            return False
    finally:
        os.close(check_fd)


def _validated_wait_number(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float | None = None,
    minimum_is_exclusive: bool = False,
) -> float:
    if type(value) not in {int, float}:
        raise TypeError(f"daemon {field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"daemon {field_name} must be a finite number")
    below_minimum = number <= minimum if minimum_is_exclusive else number < minimum
    if below_minimum or (maximum is not None and number > maximum):
        upper = f" and at most {maximum:g}" if maximum is not None else ""
        raise ValueError(
            f"daemon {field_name} must be at least {minimum:g}{upper}"
        )
    return number


def _wait_for_verified_owner(
    bound: _BoundEndpoint,
    *,
    expected_project_hash: str,
    health_probe: HealthProbe,
    wait_timeout_seconds: float,
    poll_interval_seconds: float,
) -> DaemonOwnership:
    deadline = time.monotonic() + wait_timeout_seconds
    while True:
        try:
            metadata = _read_endpoint_metadata(bound)
        except DaemonIdentityError:
            metadata = None
        if (
            metadata is not None
            and metadata["project_root_hash"] == expected_project_hash
            and _verified_health_proof(metadata, health_probe)
        ):
            return _new_ownership(
                role="follower",
                instance_id=str(metadata["instance_id"]),
                endpoint=bound.endpoint,
                project_root_hash=str(metadata["project_root_hash"]),
                start_nonce_hash=str(metadata["start_nonce_hash"]),
                pid=int(metadata["pid"]),
            )
        if time.monotonic() >= deadline:
            raise DaemonIdentityError("no verified daemon became ready")
        time.sleep(poll_interval_seconds)


def acquire_daemon_ownership(
    root: Path,
    *,
    start_nonce: str,
    health_probe: HealthProbe,
    wait_timeout_seconds: float = 1.0,
    poll_interval_seconds: float = 0.01,
) -> DaemonOwnership:
    nonce = _required_string(start_nonce, "start_nonce")
    wait_timeout_seconds = _validated_wait_number(
        wait_timeout_seconds,
        "wait_timeout_seconds",
        minimum=1,
        maximum=300,
    )
    poll_interval_seconds = _validated_wait_number(
        poll_interval_seconds,
        "poll_interval_seconds",
        minimum=0,
        minimum_is_exclusive=True,
    )
    if poll_interval_seconds > wait_timeout_seconds:
        raise ValueError(
            "daemon poll_interval_seconds must not exceed wait_timeout_seconds"
        )
    bound = _prepare_endpoint(root)
    endpoint = bound.endpoint
    expected_hash = project_root_hash(root)
    try:
        lock_file, acquired = _try_startup_lock(bound)
    except Exception:
        bound.close()
        raise
    if not acquired:
        lock_file.close()
        try:
            return _wait_for_verified_owner(
                bound,
                expected_project_hash=expected_hash,
                health_probe=health_probe,
                wait_timeout_seconds=wait_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
        finally:
            bound.close()

    try:
        existing = _read_endpoint_metadata(bound)
        if existing is not None:
            if (
                existing["project_root_hash"] == expected_hash
                and _verified_health_proof(existing, health_probe)
            ):
                ownership = _new_ownership(
                    role="follower",
                    instance_id=str(existing["instance_id"]),
                    endpoint=endpoint,
                    project_root_hash=str(existing["project_root_hash"]),
                    start_nonce_hash=str(existing["start_nonce_hash"]),
                    pid=int(existing["pid"]),
                )
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()
                bound.close()
                return ownership
            if _process_exists(int(existing["pid"])):
                raise DaemonIdentityError("existing process is not a verified daemon")
            _unlink_endpoint_files(bound)
        else:
            try:
                os.stat("daemon.sock", dir_fd=bound.runtime_fd)
            except FileNotFoundError:
                pass
            else:
                _unlink_endpoint_files(bound)

        nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        metadata: dict[str, object] = {
            "instance_id": f"dmn_{uuid.uuid4().hex}",
            "project_root_hash": expected_hash,
            "start_nonce_hash": nonce_hash,
            "pid": os.getpid(),
        }
        _atomic_write_metadata(bound, metadata)
        return _new_ownership(
            role="owner",
            instance_id=str(metadata["instance_id"]),
            endpoint=endpoint,
            project_root_hash=expected_hash,
            start_nonce_hash=nonce_hash,
            pid=os.getpid(),
            lock_file=lock_file,
            bound_endpoint=bound,
        )
    except Exception:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
            bound.close()
        raise


def reconcile_endpoint(
    root: Path,
    *,
    expected_project_hash: str,
    health_probe: HealthProbe,
) -> bool:
    bound = _prepare_endpoint(root)
    try:
        lock_file, acquired = _try_startup_lock(bound)
    except Exception:
        bound.close()
        raise
    if not acquired:
        lock_file.close()
        bound.close()
        return False
    try:
        try:
            metadata = _read_endpoint_metadata(bound)
        except DaemonIdentityError:
            return False
        if metadata is None:
            try:
                os.stat("daemon.sock", dir_fd=bound.runtime_fd)
            except FileNotFoundError:
                return False
            else:
                return _unlink_endpoint_files(bound)
        if (
            metadata["project_root_hash"] == expected_project_hash
            and _verified_health_proof(metadata, health_probe)
        ):
            return False
        if _process_exists(int(metadata["pid"])):
            return False
        return _unlink_endpoint_files(bound)
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
            bound.close()


def cleanup_daemon_endpoint(ownership: DaemonOwnership) -> bool:
    if ownership.role != "owner" or not _ownership_holds_startup_lock(ownership):
        return False
    expected = {
        "instance_id": ownership.instance_id,
        "project_root_hash": ownership.project_root_hash,
        "start_nonce_hash": ownership.start_nonce_hash,
        "pid": ownership.pid,
    }
    try:
        bound = ownership._capability.bound_endpoint
        if bound is None or bound.endpoint != ownership.endpoint:
            return False
        try:
            _reject_endpoint_symlinks(bound)
            _assert_runtime_binding(bound)
        except DaemonIdentityError:
            return False
        try:
            metadata = _read_endpoint_metadata(bound)
        except DaemonIdentityError:
            return False
        if metadata != expected:
            return False
        return _unlink_endpoint_files(bound)
    finally:
        ownership.release()


_KEEPALIVE_COUNTS = (
    ("client_count", "clients_connected"),
    ("active_mission_count", "active_mission"),
    ("active_worker_count", "active_worker"),
    ("pending_approval_count", "pending_approval"),
    ("pending_permission_count", "pending_permission"),
    ("pending_reply_count", "pending_reply"),
    ("pending_recovery_decision_count", "pending_recovery"),
    ("pending_decision_count", "pending_decision"),
    ("ambiguous_decision_count", "pending_ambiguity"),
    ("outbox_count", "outbox_pending"),
)
_KEEPALIVE_FLAGS = (
    ("recovery_active", "recovery_active"),
    ("safe_shutdown_active", "safe_shutdown_active"),
    ("atomic_write_active", "atomic_write_active"),
)


def daemon_keepalive_reasons(view: Mapping[str, object]) -> tuple[str, ...]:
    if not isinstance(view, Mapping):
        raise TypeError("daemon keepalive view must be a mapping")
    reasons: list[str] = []
    for field_name, reason in _KEEPALIVE_COUNTS:
        value = view.get(field_name, 0)
        if type(value) is not int or value < 0:
            raise ValueError(f"daemon {field_name} must be a non-negative integer")
        if value:
            reasons.append(reason)
    for field_name, reason in _KEEPALIVE_FLAGS:
        value = view.get(field_name, False)
        if type(value) is not bool:
            raise TypeError(f"daemon {field_name} must be a boolean")
        if value:
            reasons.append(reason)
    return tuple(reasons)


def can_stop_daemon(view: Mapping[str, object]) -> bool:
    return not daemon_keepalive_reasons(view)


def _required_string(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"daemon {field} must be a non-empty string")
    return value


def _aware_timestamp(value: object, field: str) -> datetime:
    timestamp = _required_string(value, field)
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError(f"daemon {field} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"daemon {field} must be a timezone-aware timestamp")
    return parsed


def validate_daemon_record(record: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(record, Mapping):
        raise TypeError("daemon record must be a mapping")
    if set(record) != _DAEMON_RECORD_FIELDS:
        raise ValueError("daemon record fields are invalid")

    _required_string(record["instance_id"], "instance_id")
    _required_string(record["project_root_hash"], "project_root_hash")
    nonce_hash = _required_string(record["start_nonce_hash"], "start_nonce_hash")
    if _SHA256_PATTERN.fullmatch(nonce_hash) is None:
        raise ValueError("daemon start_nonce_hash must be a lowercase sha256 hash")
    state = record["state"]
    if type(state) is not str or state not in DAEMON_STATES:
        raise ValueError("daemon state is invalid")
    created_at = _aware_timestamp(record["created_at"], "created_at")
    updated_at = _aware_timestamp(record["updated_at"], "updated_at")
    if updated_at < created_at:
        raise ValueError("daemon updated_at must not be earlier than created_at")
    return dict(record)


def build_daemon_record(
    *,
    instance_id: str,
    project_root_hash: str,
    start_nonce: str,
    state: str,
    created_at: str,
) -> dict[str, object]:
    nonce = _required_string(start_nonce, "start_nonce")
    record: dict[str, object] = {
        "instance_id": instance_id,
        "project_root_hash": project_root_hash,
        "start_nonce_hash": hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
        "state": state,
        "created_at": created_at,
        "updated_at": created_at,
    }
    return validate_daemon_record(record)
