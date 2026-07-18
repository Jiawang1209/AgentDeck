"""Lease-bound SQLite authority bootstrap and read views."""

from __future__ import annotations

import os
import sqlite3
import stat
import uuid
from pathlib import Path
from types import TracebackType
from typing import Self
from urllib.parse import quote

from .migrations import (
    AUTHORITY_STATES,
    SCHEMA_TABLES,
    SCHEMA_VERSION,
    apply_schema_v1,
)
from .ownership import ProjectWriterLease, WriterLeaseError


class SQLiteStoreError(RuntimeError):
    """The SQLite authority cannot be safely created or opened."""


_INVALID_PATH = "SQLite state path invalid"
_INVALID_SCHEMA = "SQLite schema invalid"
_UNSUPPORTED_SCHEMA = "unsupported SQLite schema"
_INVALID_AUTHORITY = "SQLite authority state invalid"
_INVALID_PROJECT = "SQLite project identity invalid"
_STORE_TOKEN = object()


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


def _validate_existing_paths(path: Path) -> None:
    _ensure_regular_owner_file(path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists() or sidecar.is_symlink():
            _ensure_regular_owner_file(sidecar)


def _chmod_database_family(path: Path) -> None:
    for member in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if not member.exists() and not member.is_symlink():
            continue
        try:
            member_stat = os.stat(member, follow_symlinks=False)
            if stat.S_ISLNK(member_stat.st_mode) or not stat.S_ISREG(
                member_stat.st_mode
            ):
                raise SQLiteStoreError(_INVALID_PATH)
            os.chmod(member, 0o600, follow_symlinks=False)
        except OSError as exc:
            raise SQLiteStoreError(_INVALID_PATH) from None


def _remove_database_family(path: Path) -> None:
    for member in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            if member.is_file() or member.is_symlink():
                member.unlink()
        except FileNotFoundError:
            pass


def _immutable_read_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/')}?mode=ro&immutable=1"


def _read_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/')}?mode=ro"


def _read_only_preflight(path: Path) -> tuple[str, str]:
    """Validate bytes without opening a writer or creating SQLite sidecars."""

    _validate_existing_paths(path)
    try:
        connection = sqlite3.connect(
            _immutable_read_uri(path),
            uri=True,
            timeout=0,
            isolation_level=None,
        )
    except sqlite3.Error as exc:
        raise SQLiteStoreError(_INVALID_SCHEMA) from None
    try:
        try:
            versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
        except sqlite3.Error as exc:
            raise SQLiteStoreError(_INVALID_SCHEMA) from None
        if any(
            not isinstance(version, int) or isinstance(version, bool)
            for version in versions
        ):
            raise SQLiteStoreError(_INVALID_SCHEMA)
        if versions and max(versions) > SCHEMA_VERSION:
            raise SQLiteStoreError(_UNSUPPORTED_SCHEMA)
        if versions != [SCHEMA_VERSION]:
            raise SQLiteStoreError(_INVALID_SCHEMA)

        try:
            projects = connection.execute(
                "SELECT project_id, authority_state FROM projects"
            ).fetchall()
        except sqlite3.Error as exc:
            raise SQLiteStoreError(_INVALID_SCHEMA) from None
        if (
            len(projects) != 1
            or not isinstance(projects[0][0], str)
            or not projects[0][0]
        ):
            raise SQLiteStoreError(_INVALID_SCHEMA)
        project_id, authority_state = projects[0]
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
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check != ("ok",):
            raise SQLiteStoreError(_INVALID_SCHEMA)
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SQLiteStoreError(_INVALID_SCHEMA)
        return project_id, authority_state
    except SQLiteStoreError:
        raise
    except sqlite3.Error as exc:
        raise SQLiteStoreError(_INVALID_SCHEMA) from None
    finally:
        connection.close()


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
        installed = False
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
            os.replace(temporary, path)
            installed = True
            _chmod_database_family(path)
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
            if installed:
                _remove_database_family(path)
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
        project_id, authority_state = _read_only_preflight(path)
        try:
            connection = sqlite3.connect(path, isolation_level=None, timeout=0)
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
                raise SQLiteStoreError(_INVALID_SCHEMA)
            if connection.execute("PRAGMA journal_mode=WAL").fetchone() != ("wal",):
                raise SQLiteStoreError(_INVALID_SCHEMA)
            connection.execute("PRAGMA synchronous=FULL")
            if connection.execute("PRAGMA synchronous").fetchone() != (2,):
                raise SQLiteStoreError(_INVALID_SCHEMA)
            _chmod_database_family(path)
        except BaseException:
            try:
                connection.close()
            except UnboundLocalError:
                pass
            raise
        return cls(
            root=root,
            path=path,
            lease=lease,
            connection=connection,
            project_id=project_id,
            authority_state=authority_state,
            lease_claim=lease_claim,
            token=_STORE_TOKEN,
        )

    @property
    def schema_version(self) -> int:
        return SCHEMA_VERSION

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def authority_state(self) -> str:
        return self._authority_state

    def open_reader(self) -> sqlite3.Connection:
        if self._closed:
            raise SQLiteStoreError("SQLite store is closed")
        reader = sqlite3.connect(
            _read_uri(self._path),
            uri=True,
            isolation_level=None,
            timeout=0,
            factory=_ReadOnlyConnection,
        )
        try:
            reader.execute("PRAGMA foreign_keys=ON")
            reader.execute("PRAGMA synchronous=FULL")
            reader.execute("PRAGMA query_only=ON")
            if reader.execute("PRAGMA query_only").fetchone() != (1,):
                raise SQLiteStoreError(_INVALID_SCHEMA)
            _chmod_database_family(self._path)
            return reader
        except BaseException:
            reader.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self._connection.close()
                _chmod_database_family(self._path)
        finally:
            self._lease.release_store(self._lease_claim)

    def __enter__(self) -> Self:
        if self._closed:
            raise SQLiteStoreError("SQLite store is closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
