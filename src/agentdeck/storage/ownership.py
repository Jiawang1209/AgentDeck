"""Exclusive, owner-only writer lease for one AgentDeck project."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
from pathlib import Path
from types import TracebackType
from typing import Self


class WriterLeaseError(RuntimeError):
    """A project writer lease cannot be acquired or validated."""


_LEASE_TOKEN = object()
_INVALID_LEASE = "active matching writer lease required"
_INVALID_PATH = "writer lease path invalid"


def _absolute_root(root: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(root)))


def _root_identity(root: Path) -> tuple[int, int]:
    try:
        root_stat = os.stat(root, follow_symlinks=True)
    except OSError as exc:
        raise WriterLeaseError(_INVALID_PATH) from None
    if not stat.S_ISDIR(root_stat.st_mode):
        raise WriterLeaseError(_INVALID_PATH)
    return root_stat.st_dev, root_stat.st_ino


def _prepare_state_directory(root: Path) -> Path:
    state_dir = root / ".agentdeck"
    try:
        state_stat = os.lstat(state_dir)
    except FileNotFoundError:
        try:
            os.mkdir(state_dir, 0o700)
            state_stat = os.lstat(state_dir)
        except OSError as exc:
            raise WriterLeaseError(_INVALID_PATH) from None
    except OSError as exc:
        raise WriterLeaseError(_INVALID_PATH) from None
    if (
        stat.S_ISLNK(state_stat.st_mode)
        or not stat.S_ISDIR(state_stat.st_mode)
        or state_stat.st_uid != os.getuid()
    ):
        raise WriterLeaseError(_INVALID_PATH)
    try:
        os.chmod(state_dir, 0o700, follow_symlinks=False)
    except OSError as exc:
        raise WriterLeaseError(_INVALID_PATH) from None
    return state_dir


class ProjectWriterLease:
    """An exact-root, inode-bound non-blocking ``flock`` lease.

    Construction is intentionally closed.  Callers receive a valid instance
    only from :meth:`acquire`; stores additionally revalidate the root and lock
    inode before every mutating open.
    """

    __slots__ = (
        "_token",
        "_root",
        "_root_identity",
        "_state_dir",
        "_state_identity",
        "_lock_path",
        "_lock_identity",
        "_fd",
        "_active",
        "_store_claim",
    )

    def __init__(
        self,
        *,
        token: object,
        root: Path,
        root_identity: tuple[int, int],
        state_dir: Path,
        state_identity: tuple[int, int],
        lock_path: Path,
        lock_identity: tuple[int, int],
        fd: int,
    ) -> None:
        if token is not _LEASE_TOKEN:
            raise WriterLeaseError(_INVALID_LEASE)
        self._token = token
        self._root = root
        self._root_identity = root_identity
        self._state_dir = state_dir
        self._state_identity = state_identity
        self._lock_path = lock_path
        self._lock_identity = lock_identity
        self._fd = fd
        self._active = True
        self._store_claim: object | None = None

    @classmethod
    def acquire(cls, root: str | os.PathLike[str]) -> Self:
        absolute_root = _absolute_root(root)
        identity = _root_identity(absolute_root)
        state_dir = _prepare_state_directory(absolute_root)
        state_stat = os.stat(state_dir, follow_symlinks=False)
        lock_path = state_dir / "state.db.lock"
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise WriterLeaseError(_INVALID_PATH) from None
        try:
            lock_stat = os.fstat(fd)
            if (
                not stat.S_ISREG(lock_stat.st_mode)
                or lock_stat.st_uid != os.getuid()
            ):
                raise WriterLeaseError(_INVALID_PATH)
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise WriterLeaseError(
                        "another project writer is active"
                    ) from None
                raise WriterLeaseError(_INVALID_PATH) from None
            return cls(
                token=_LEASE_TOKEN,
                root=absolute_root,
                root_identity=identity,
                state_dir=state_dir,
                state_identity=(state_stat.st_dev, state_stat.st_ino),
                lock_path=lock_path,
                lock_identity=(lock_stat.st_dev, lock_stat.st_ino),
                fd=fd,
            )
        except BaseException:
            os.close(fd)
            raise

    @property
    def active(self) -> bool:
        return self._active

    @property
    def root(self) -> Path:
        return self._root

    def validate_for(self, root: str | os.PathLike[str]) -> None:
        if type(self) is not ProjectWriterLease or self._token is not _LEASE_TOKEN:
            raise WriterLeaseError(_INVALID_LEASE)
        if not self._active or self._fd < 0:
            raise WriterLeaseError(_INVALID_LEASE)
        requested = _absolute_root(root)
        if requested != self._root:
            raise WriterLeaseError(_INVALID_LEASE)
        try:
            if _root_identity(requested) != self._root_identity:
                raise WriterLeaseError(_INVALID_LEASE)
            state_stat = os.stat(self._state_dir, follow_symlinks=False)
            descriptor_stat = os.fstat(self._fd)
            path_stat = os.stat(self._lock_path, follow_symlinks=False)
        except (OSError, WriterLeaseError) as exc:
            raise WriterLeaseError(_INVALID_LEASE) from None
        descriptor_identity = (descriptor_stat.st_dev, descriptor_stat.st_ino)
        path_identity = (path_stat.st_dev, path_stat.st_ino)
        state_identity = (state_stat.st_dev, state_stat.st_ino)
        if (
            state_identity != self._state_identity
            or not stat.S_ISDIR(state_stat.st_mode)
            or state_stat.st_uid != os.getuid()
            or stat.S_IMODE(state_stat.st_mode) != 0o700
            or descriptor_identity != self._lock_identity
            or path_identity != self._lock_identity
            or not stat.S_ISREG(path_stat.st_mode)
            or stat.S_IMODE(path_stat.st_mode) != 0o600
        ):
            raise WriterLeaseError(_INVALID_LEASE)

    def claim_store(self, root: str | os.PathLike[str]) -> object:
        self.validate_for(root)
        if self._store_claim is not None:
            raise WriterLeaseError("writer lease already owns a store")
        claim = object()
        self._store_claim = claim
        return claim

    def release_store(self, claim: object) -> None:
        if claim is not self._store_claim:
            raise WriterLeaseError(_INVALID_LEASE)
        self._store_claim = None

    def close(self) -> None:
        if not self._active:
            return
        fd = self._fd
        self._fd = -1
        self._active = False
        self._store_claim = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> Self:
        if not self._active:
            raise WriterLeaseError(_INVALID_LEASE)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
