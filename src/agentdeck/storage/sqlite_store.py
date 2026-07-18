"""Lease-bound SQLite authority bootstrap and read views."""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self
from urllib.parse import quote

from .migrations import (
    AUTHORITY_STATES,
    SCHEMA_TABLES,
    SCHEMA_VERSION,
    apply_schema_v1,
    expected_schema_fingerprint,
    schema_fingerprint,
)
from .ownership import ProjectWriterLease, WriterLeaseError


class SQLiteStoreError(RuntimeError):
    """The SQLite authority cannot be safely created or opened."""


_INVALID_PATH = "SQLite state path invalid"
_INVALID_SCHEMA = "SQLite schema invalid"
_UNSUPPORTED_SCHEMA = "unsupported SQLite schema"
_INVALID_AUTHORITY = "SQLite authority state invalid"
_INVALID_PROJECT = "SQLite project identity invalid"
_INVALID_AUTHORITY_IDENTITY = "SQLite authority identity invalid"
_STORE_TOKEN = object()


@dataclass(frozen=True, slots=True)
class _AuthoritySnapshot:
    schema_version: int
    project_id: str
    revision: int
    authority_state: str


class _ReadOnlyConnection(sqlite3.Connection):
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self.in_transaction:
                self.rollback()
        finally:
            self.close()


def _state_dir(root: Path) -> Path:
    return root / ".agentdeck"


def _database_path(root: Path) -> Path:
    return _state_dir(root) / "state.db"


def _validate_project_id(project_id: object) -> str:
    if not isinstance(project_id, str) or not project_id:
        raise SQLiteStoreError(_INVALID_PROJECT)
    try:
        encoded = project_id.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SQLiteStoreError(_INVALID_PROJECT) from None
    if len(encoded) > 4096:
        raise SQLiteStoreError(_INVALID_PROJECT)
    return project_id


def _ensure_regular_owner_file(path: Path) -> os.stat_result:
    try:
        file_stat = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise SQLiteStoreError(_INVALID_PATH) from None
    if (
        stat.S_ISLNK(file_stat.st_mode)
        or not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid != os.getuid()
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise SQLiteStoreError(_INVALID_PATH)
    return file_stat


def _file_identity(file_stat: os.stat_result) -> tuple[int, int]:
    return file_stat.st_dev, file_stat.st_ino


def _validate_existing_paths(path: Path) -> tuple[int, int]:
    identity = _file_identity(_ensure_regular_owner_file(path))
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists() or sidecar.is_symlink():
            _ensure_regular_owner_file(sidecar)
    return identity


def _validate_authority_paths(
    path: Path,
    expected: tuple[int, int],
) -> None:
    try:
        actual = _validate_existing_paths(path)
    except SQLiteStoreError:
        raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY) from None
    if actual != expected:
        raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY)


def _remove_database_family(path: Path) -> None:
    for member in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            if member.is_file() or member.is_symlink():
                member.unlink()
        except FileNotFoundError:
            pass


def _read_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/')}?mode=ro"


def _write_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/')}?mode=rw"


def _validate_connection(connection: sqlite3.Connection) -> _AuthoritySnapshot:
    try:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        if any(
            not isinstance(version, int) or isinstance(version, bool)
            for version in versions
        ):
            raise SQLiteStoreError(_INVALID_SCHEMA)
        if versions and max(versions) > SCHEMA_VERSION:
            raise SQLiteStoreError(_UNSUPPORTED_SCHEMA)
        if versions != [SCHEMA_VERSION]:
            raise SQLiteStoreError(_INVALID_SCHEMA)

        projects = connection.execute(
            "SELECT project_id, revision, authority_state FROM projects"
        ).fetchall()
        if (
            len(projects) != 1
            or not isinstance(projects[0][0], str)
            or not projects[0][0]
            or not isinstance(projects[0][1], int)
            or isinstance(projects[0][1], bool)
            or projects[0][1] < 0
        ):
            raise SQLiteStoreError(_INVALID_SCHEMA)
        project_id, revision, authority_state = projects[0]
        if (
            not isinstance(authority_state, str)
            or authority_state not in AUTHORITY_STATES
        ):
            raise SQLiteStoreError(_INVALID_AUTHORITY)

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != set(SCHEMA_TABLES):
            raise SQLiteStoreError(_INVALID_SCHEMA)
        if schema_fingerprint(connection) != expected_schema_fingerprint():
            raise SQLiteStoreError(_INVALID_SCHEMA)
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check != ("ok",):
            raise SQLiteStoreError(_INVALID_SCHEMA)
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SQLiteStoreError(_INVALID_SCHEMA)
        return _AuthoritySnapshot(
            schema_version=SCHEMA_VERSION,
            project_id=project_id,
            revision=revision,
            authority_state=authority_state,
        )
    except SQLiteStoreError:
        raise
    except sqlite3.Error as exc:
        raise SQLiteStoreError(_INVALID_SCHEMA) from None


def _family_signature(path: Path) -> tuple[int, int, int, int, int]:
    file_stat = _ensure_regular_owner_file(path)
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _capture_database_family(
    path: Path,
) -> dict[str, tuple[int, int, int, int, int]]:
    captured = {"": _family_signature(path)}
    for suffix in ("-wal", "-shm"):
        member = Path(f"{path}{suffix}")
        try:
            os.lstat(member)
        except FileNotFoundError:
            continue
        except OSError:
            raise SQLiteStoreError(_INVALID_PATH) from None
        captured[suffix] = _family_signature(member)
    return captured


def _copy_family_member(
    source: Path,
    destination: Path,
    expected: tuple[int, int, int, int, int],
) -> None:
    read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    read_flags |= getattr(os, "O_NOFOLLOW", 0)
    write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    write_flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        source_fd = os.open(source, read_flags)
    except OSError:
        raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY) from None
    try:
        source_stat = os.fstat(source_fd)
        if (
            not stat.S_ISREG(source_stat.st_mode)
            or (
                source_stat.st_dev,
                source_stat.st_ino,
                source_stat.st_size,
                source_stat.st_mtime_ns,
                source_stat.st_ctime_ns,
            )
            != expected
        ):
            raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY)
        try:
            destination_fd = os.open(destination, write_flags, 0o600)
        except OSError:
            raise SQLiteStoreError(_INVALID_SCHEMA) from None
        try:
            while True:
                block = os.read(source_fd, 1024 * 1024)
                if not block:
                    break
                view = memoryview(block)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
            os.fchmod(destination_fd, 0o600)
        finally:
            os.close(destination_fd)
        after = os.fstat(source_fd)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != expected:
            raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY)
    finally:
        os.close(source_fd)


def _wal_aware_preflight(
    path: Path,
) -> tuple[_AuthoritySnapshot, tuple[int, int]]:
    """Validate a private WAL-aware copy without touching authority bytes."""

    family = _capture_database_family(path)
    database_identity = family[""][:2]
    temporary_dir = path.parent / f".state-preflight-{uuid.uuid4().hex}"
    temporary_created = False
    try:
        os.mkdir(temporary_dir, 0o700)
        temporary_created = True
        os.chmod(temporary_dir, 0o700, follow_symlinks=False)
        for suffix, signature in family.items():
            _copy_family_member(
                Path(f"{path}{suffix}"),
                temporary_dir / f"state.db{suffix}",
                signature,
            )
        if _capture_database_family(path) != family:
            raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY)
        copied_database = temporary_dir / "state.db"
        try:
            connection = sqlite3.connect(
                _write_uri(copied_database),
                uri=True,
                timeout=0,
                isolation_level=None,
            )
        except sqlite3.Error:
            raise SQLiteStoreError(_INVALID_SCHEMA) from None
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            snapshot = _validate_connection(connection)
        finally:
            connection.close()
        if _capture_database_family(path) != family:
            raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY)
        return snapshot, database_identity
    finally:
        if temporary_created:
            try:
                shutil.rmtree(temporary_dir)
            except OSError:
                raise SQLiteStoreError(_INVALID_SCHEMA) from None


def _remove_installed_database(
    path: Path,
    installed_identity: tuple[int, int],
) -> None:
    try:
        current = _file_identity(_ensure_regular_owner_file(path))
    except SQLiteStoreError:
        return
    if current == installed_identity:
        _remove_database_family(path)


class SQLiteMissionStore:
    """One lease-bound writer with independent query-only readers."""

    __slots__ = (
        "_root",
        "_path",
        "_lease",
        "_connection",
        "_project_id",
        "_authority_state",
        "_closed",
        "_lease_claim",
        "_token",
        "_database_identity",
        "_project_revision",
        "_owner_pid",
    )

    def __init__(
        self,
        *,
        root: Path,
        path: Path,
        lease: ProjectWriterLease,
        connection: sqlite3.Connection,
        project_id: str,
        authority_state: str,
        lease_claim: object,
        token: object,
        database_identity: tuple[int, int],
        project_revision: int,
        owner_pid: int,
    ) -> None:
        if token is not _STORE_TOKEN:
            raise WriterLeaseError("active matching writer lease required")
        self._root = root
        self._path = path
        self._lease = lease
        self._connection = connection
        self._project_id = project_id
        self._authority_state = authority_state
        self._closed = False
        self._lease_claim = lease_claim
        self._token = token
        self._database_identity = database_identity
        self._project_revision = project_revision
        self._owner_pid = owner_pid

    @classmethod
    def create(
        cls,
        root: str | os.PathLike[str],
        *,
        lease: ProjectWriterLease,
        project_id: str,
        authority_state: str = "sqlite_active",
    ) -> Self:
        absolute_root = Path(os.path.abspath(os.fspath(root)))
        cls._require_lease(lease, absolute_root)
        project_id = _validate_project_id(project_id)
        if (
            not isinstance(authority_state, str)
            or authority_state not in AUTHORITY_STATES
        ):
            raise SQLiteStoreError(_INVALID_AUTHORITY)
        path = _database_path(absolute_root)
        if path.exists() or path.is_symlink():
            raise SQLiteStoreError(_INVALID_PATH)

        lease_claim = lease.claim_store(absolute_root)
        temporary = _state_dir(absolute_root) / f".state.db.{uuid.uuid4().hex}.tmp"
        connection: sqlite3.Connection | None = None
        installed_identity: tuple[int, int] | None = None
        try:
            connection = sqlite3.connect(temporary, isolation_level=None)
            os.chmod(temporary, 0o600, follow_symlinks=False)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                apply_schema_v1(
                    connection,
                    project_id=project_id,
                    authority_state=authority_state,
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            connection.close()
            connection = None
            os.chmod(temporary, 0o600, follow_symlinks=False)
            temporary_identity = _file_identity(_ensure_regular_owner_file(temporary))
            try:
                os.link(temporary, path, follow_symlinks=False)
            except OSError:
                raise SQLiteStoreError(_INVALID_PATH) from None
            installed_identity = temporary_identity
            _validate_authority_paths(path, installed_identity)
            temporary.unlink()
            directory_fd = os.open(_state_dir(absolute_root), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return cls._open_validated(
                absolute_root,
                lease=lease,
                lease_claim=lease_claim,
            )
        except BaseException:
            if connection is not None:
                connection.close()
            _remove_database_family(temporary)
            if installed_identity is not None:
                _remove_installed_database(path, installed_identity)
            lease.release_store(lease_claim)
            raise

    @classmethod
    def open(
        cls,
        root: str | os.PathLike[str],
        *,
        lease: ProjectWriterLease,
    ) -> Self:
        absolute_root = Path(os.path.abspath(os.fspath(root)))
        cls._require_lease(lease, absolute_root)
        lease_claim = lease.claim_store(absolute_root)
        try:
            return cls._open_validated(
                absolute_root,
                lease=lease,
                lease_claim=lease_claim,
            )
        except BaseException:
            lease.release_store(lease_claim)
            raise

    @staticmethod
    def _require_lease(lease: object, root: Path) -> None:
        if type(lease) is not ProjectWriterLease:
            raise WriterLeaseError("active matching writer lease required")
        lease.validate_for(root)

    @classmethod
    def _open_validated(
        cls,
        root: Path,
        *,
        lease: ProjectWriterLease,
        lease_claim: object,
    ) -> Self:
        cls._require_lease(lease, root)
        path = _database_path(root)
        snapshot, database_identity = _wal_aware_preflight(path)
        _validate_authority_paths(path, database_identity)
        connection: sqlite3.Connection | None = None
        try:
            try:
                connection = sqlite3.connect(
                    _write_uri(path),
                    uri=True,
                    isolation_level=None,
                    timeout=0,
                )
            except sqlite3.Error:
                raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY) from None
            _validate_authority_paths(path, database_identity)
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
                raise SQLiteStoreError(_INVALID_SCHEMA)
            writer_snapshot = _validate_connection(connection)
            if writer_snapshot != snapshot:
                raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY)
            if connection.execute("PRAGMA journal_mode=WAL").fetchone() != ("wal",):
                raise SQLiteStoreError(_INVALID_SCHEMA)
            connection.execute("PRAGMA synchronous=FULL")
            if connection.execute("PRAGMA synchronous").fetchone() != (2,):
                raise SQLiteStoreError(_INVALID_SCHEMA)
            _validate_authority_paths(path, database_identity)
            if _validate_connection(connection) != snapshot:
                raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY)
        except BaseException:
            if connection is not None:
                connection.close()
            raise
        return cls(
            root=root,
            path=path,
            lease=lease,
            connection=connection,
            project_id=snapshot.project_id,
            authority_state=snapshot.authority_state,
            lease_claim=lease_claim,
            token=_STORE_TOKEN,
            database_identity=database_identity,
            project_revision=snapshot.revision,
            owner_pid=os.getpid(),
        )

    @property
    def schema_version(self) -> int:
        self._validate_authority()
        return SCHEMA_VERSION

    @property
    def project_id(self) -> str:
        self._validate_authority()
        return self._project_id

    @property
    def authority_state(self) -> str:
        self._validate_authority()
        return self._authority_state

    def _validate_authority(self) -> None:
        if self._closed:
            raise SQLiteStoreError("SQLite store is closed")
        if os.getpid() != self._owner_pid:
            raise WriterLeaseError("writer lease process mismatch")
        self._lease.validate_store_claim(self._root, self._lease_claim)
        _validate_authority_paths(self._path, self._database_identity)

    def open_reader(self) -> sqlite3.Connection:
        self._validate_authority()
        reader = sqlite3.connect(
            _read_uri(self._path),
            uri=True,
            isolation_level=None,
            timeout=0,
            factory=_ReadOnlyConnection,
        )
        try:
            self._validate_authority()
            reader.execute("PRAGMA foreign_keys=ON")
            reader.execute("PRAGMA synchronous=FULL")
            reader.execute("PRAGMA query_only=ON")
            if reader.execute("PRAGMA query_only").fetchone() != (1,):
                raise SQLiteStoreError(_INVALID_SCHEMA)
            self._validate_authority()
            return reader
        except BaseException:
            reader.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        if os.getpid() != self._owner_pid:
            self._closed = True
            try:
                self._connection.close()
            finally:
                self._lease.close()
            return
        try:
            self._validate_authority()
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._validate_authority()
        finally:
            self._closed = True
            try:
                self._connection.close()
            finally:
                self._lease.release_store(self._lease_claim)

    def __enter__(self) -> Self:
        self._validate_authority()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
