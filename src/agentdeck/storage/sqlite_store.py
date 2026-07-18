"""Lease-bound SQLite authority bootstrap and read views."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Self, cast
from urllib.parse import quote

from agentdeck.domain.events import DomainEvent

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


class CommandConflict(RuntimeError):
    """A command id was reused for different immutable input."""


class RevisionConflict(RuntimeError):
    """A new command targeted a revision that is no longer current."""


class MutationValidationError(ValueError):
    """A mutation value violated the closed durable-kernel contract."""


_INVALID_PATH = "SQLite state path invalid"
_INVALID_SCHEMA = "SQLite schema invalid"
_UNSUPPORTED_SCHEMA = "unsupported SQLite schema"
_INVALID_AUTHORITY = "SQLite authority state invalid"
_INVALID_PROJECT = "SQLite project identity invalid"
_INVALID_AUTHORITY_IDENTITY = "SQLite authority identity invalid"
_STORE_TOKEN = object()
type _DatabaseFamilyIdentity = tuple[tuple[str, int, int], ...]
type _JsonValue = None | bool | int | str | list["_JsonValue"] | dict[str, "_JsonValue"]
type _FrozenJsonValue = (
    None
    | bool
    | int
    | str
    | tuple["_FrozenJsonValue", ...]
    | Mapping[str, "_FrozenJsonValue"]
)

_MAX_SIGNED_64 = (2**63) - 1
_MAX_MUTATION_BYTES = 64 * 1024
_MAX_MUTATION_DEPTH = 16
_MAX_TEXT_BYTES = 4096
MAX_SNAPSHOT_ROWS = 1024
MAX_SNAPSHOT_BYTES = 64 * 1024


class _InvalidMutationValue(Exception):
    pass


class _MutationToken:
    __slots__ = ("poisoned",)

    def __init__(self) -> None:
        self.poisoned = False


def _valid_mutation_text(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return len(value.encode("utf-8")) <= _MAX_TEXT_BYTES
    except UnicodeEncodeError:
        return False


def _valid_revision(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _MAX_SIGNED_64
    )


def _freeze_mutation_json(
    value: object,
    *,
    depth: int = 0,
    active: set[int] | None = None,
) -> _FrozenJsonValue:
    if depth > _MAX_MUTATION_DEPTH:
        raise _InvalidMutationValue
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if not (-_MAX_SIGNED_64 - 1 <= value <= _MAX_SIGNED_64):
            raise _InvalidMutationValue
        return value
    if isinstance(value, str):
        try:
            if len(value.encode("utf-8")) > _MAX_MUTATION_BYTES:
                raise _InvalidMutationValue
        except UnicodeEncodeError as exc:
            raise _InvalidMutationValue from exc
        return value
    if isinstance(value, list):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            raise _InvalidMutationValue
        active.add(identity)
        try:
            return tuple(
                _freeze_mutation_json(item, depth=depth + 1, active=active)
                for item in value
            )
        finally:
            active.remove(identity)
    if isinstance(value, Mapping):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            raise _InvalidMutationValue
        active.add(identity)
        try:
            frozen: dict[str, _FrozenJsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise _InvalidMutationValue
                try:
                    if len(key.encode("utf-8")) > _MAX_TEXT_BYTES:
                        raise _InvalidMutationValue
                except UnicodeEncodeError as exc:
                    raise _InvalidMutationValue from exc
                frozen[key] = _freeze_mutation_json(
                    item,
                    depth=depth + 1,
                    active=active,
                )
            return MappingProxyType(frozen)
        finally:
            active.remove(identity)
    raise _InvalidMutationValue


def _thaw_mutation_json(value: _FrozenJsonValue) -> _JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw_mutation_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_mutation_json(item) for item in value]
    return value


def _canonical_mutation_bytes(value: _FrozenJsonValue) -> bytes:
    try:
        encoded = json.dumps(
            _thaw_mutation_json(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _InvalidMutationValue from exc
    if len(encoded) > _MAX_MUTATION_BYTES:
        raise _InvalidMutationValue
    return encoded


def _parse_canonical_mutation_json(value: object) -> _JsonValue:
    if not isinstance(value, str):
        raise _InvalidMutationValue
    try:
        parsed = json.loads(value)
        frozen = _freeze_mutation_json(parsed)
        if _canonical_mutation_bytes(frozen).decode("utf-8") != value:
            raise _InvalidMutationValue
        return _thaw_mutation_json(frozen)
    except (json.JSONDecodeError, UnicodeDecodeError, UnicodeEncodeError, ValueError):
        raise _InvalidMutationValue from None


def _frozen_mapping(value: object) -> Mapping[str, _FrozenJsonValue]:
    frozen = _freeze_mutation_json(value)
    if not isinstance(frozen, Mapping):
        raise _InvalidMutationValue
    _canonical_mutation_bytes(frozen)
    return frozen


def _input_hash(value: _FrozenJsonValue) -> str:
    return "sha256:" + hashlib.sha256(_canonical_mutation_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    """Deeply immutable input identity for one revisioned client command."""

    command_id: str
    kind: str
    actor: Mapping[str, _FrozenJsonValue]
    payload: Mapping[str, _FrozenJsonValue]
    expected_revision: int
    created_at: str
    input_hash: str = field(init=False)

    def __post_init__(self) -> None:
        try:
            if not all(
                _valid_mutation_text(item)
                for item in (self.command_id, self.kind, self.created_at)
            ) or not _valid_revision(self.expected_revision):
                raise _InvalidMutationValue
            actor_source = (
                _thaw_mutation_json(self.actor)
                if type(self.actor) is MappingProxyType
                else self.actor
            )
            payload_source = (
                _thaw_mutation_json(self.payload)
                if type(self.payload) is MappingProxyType
                else self.payload
            )
            actor = _frozen_mapping(actor_source)
            payload = _frozen_mapping(payload_source)
            if not actor:
                raise _InvalidMutationValue
            canonical_input = _frozen_mapping(
                {
                    "command_id": self.command_id,
                    "kind": self.kind,
                    "actor": _thaw_mutation_json(actor),
                    "payload": _thaw_mutation_json(payload),
                    "expected_revision": self.expected_revision,
                    "created_at": self.created_at,
                }
            )
            digest = _input_hash(canonical_input)
        except _InvalidMutationValue:
            raise MutationValidationError("command envelope invalid") from None
        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "input_hash", digest)

    def actor_dict(self) -> dict[str, _JsonValue]:
        return cast(dict[str, _JsonValue], _thaw_mutation_json(self.actor))

    def payload_dict(self) -> dict[str, _JsonValue]:
        return cast(dict[str, _JsonValue], _thaw_mutation_json(self.payload))


_ENTITY_COLUMNS: dict[str, frozenset[str]] = {
    "missions": frozenset(
        {
            "mission_id",
            "project_id",
            "current_version",
            "status",
            "created_revision",
            "updated_revision",
        }
    ),
    "mission_versions": frozenset(
        {
            "mission_id",
            "version",
            "specification_json",
            "authorization_digest",
            "proposal_provenance_json",
            "confirmed_revision",
        }
    ),
    "tasks": frozenset(
        {
            "task_id",
            "mission_id",
            "mission_version",
            "specification_json",
            "status",
            "created_revision",
            "updated_revision",
        }
    ),
    "attempts": frozenset(
        {
            "attempt_id",
            "task_id",
            "attempt_number",
            "status",
            "route_position",
            "budget_json",
            "started_revision",
            "terminal_revision",
        }
    ),
    "sessions": frozenset(
        {
            "session_id",
            "attempt_id",
            "agent_id",
            "model_id",
            "transport",
            "status",
            "last_sequence",
            "lease_json",
            "reconciliation_json",
        }
    ),
    "permissions": frozenset(
        {
            "permission_id",
            "mission_id",
            "task_id",
            "attempt_id",
            "session_id",
            "request_json",
            "status",
            "decision_json",
            "created_revision",
            "decided_revision",
        }
    ),
    "handoffs": frozenset(
        {
            "handoff_id",
            "mission_id",
            "source_task_id",
            "destination_task_id",
            "status",
            "context_json",
            "created_revision",
            "accepted_revision",
        }
    ),
    "evidence": frozenset(
        {
            "evidence_id",
            "task_id",
            "attempt_id",
            "kind",
            "integrity_hash",
            "summary_json",
            "created_revision",
        }
    ),
    "approvals": frozenset(
        {
            "approval_id",
            "project_id",
            "subject_kind",
            "subject_id",
            "subject_digest",
            "status",
            "actor_json",
            "decision_revision",
        }
    ),
    "artifacts": frozenset(
        {
            "artifact_id",
            "project_id",
            "task_id",
            "relative_path",
            "content_hash",
            "media_type",
            "summary_json",
            "provenance_json",
            "created_revision",
        }
    ),
    "learning": frozenset(
        {
            "learning_id",
            "project_id",
            "source_evidence_id",
            "review_json",
            "application_json",
            "created_revision",
        }
    ),
    "suggestions": frozenset(
        {
            "suggestion_id",
            "project_id",
            "kind",
            "status",
            "proposed_hash",
            "proposed_json",
            "provenance_json",
            "created_revision",
            "applied_revision",
        }
    ),
    "legacy_records": frozenset(
        {
            "record_id",
            "project_id",
            "collection",
            "source_identity",
            "source_hash",
            "record_json",
            "imported_revision",
        }
    ),
}

_ENTITY_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "missions": ("mission_id",),
    "mission_versions": ("mission_id", "version"),
    "tasks": ("task_id",),
    "attempts": ("attempt_id",),
    "sessions": ("session_id",),
    "permissions": ("permission_id",),
    "handoffs": ("handoff_id",),
    "evidence": ("evidence_id",),
    "approvals": ("approval_id",),
    "artifacts": ("artifact_id",),
    "learning": ("learning_id",),
    "suggestions": ("suggestion_id",),
    "legacy_records": ("record_id",),
}

_ENTITY_UPDATE_COLUMNS: dict[str, frozenset[str]] = {
    "missions": frozenset({"current_version", "status", "updated_revision"}),
    "mission_versions": frozenset(
        {"authorization_digest", "confirmed_revision"}
    ),
    "tasks": frozenset({"status", "updated_revision"}),
    "attempts": frozenset({"status", "terminal_revision"}),
    "sessions": frozenset(
        {"status", "last_sequence", "lease_json", "reconciliation_json"}
    ),
    "permissions": frozenset(
        {"status", "decision_json", "decided_revision"}
    ),
    "handoffs": frozenset({"status", "accepted_revision"}),
    "approvals": frozenset({"status", "actor_json", "decision_revision"}),
    "learning": frozenset({"application_json"}),
    "suggestions": frozenset({"status", "applied_revision"}),
}


def _freeze_sql_values(value: object) -> Mapping[str, None | int | str]:
    if not isinstance(value, dict) or not value:
        raise _InvalidMutationValue
    frozen: dict[str, None | int | str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise _InvalidMutationValue
        if isinstance(item, bool):
            raise _InvalidMutationValue
        if item is not None and not isinstance(item, (int, str)):
            raise _InvalidMutationValue
        if isinstance(item, int):
            if not (-_MAX_SIGNED_64 - 1 <= item <= _MAX_SIGNED_64):
                raise _InvalidMutationValue
        if isinstance(item, str):
            try:
                if len(item.encode("utf-8")) > _MAX_MUTATION_BYTES:
                    raise _InvalidMutationValue
            except UnicodeEncodeError as exc:
                raise _InvalidMutationValue from exc
        frozen[key] = item
    return MappingProxyType(frozen)


def _validate_entity_primary_key(
    table: str,
    values: Mapping[str, None | int | str],
) -> None:
    primary_key = _ENTITY_PRIMARY_KEYS[table]
    if not set(primary_key).issubset(values):
        raise _InvalidMutationValue
    for column in primary_key:
        value = values[column]
        if table == "mission_versions" and column == "version":
            if not _valid_revision(value) or value == 0:
                raise _InvalidMutationValue
        elif not _valid_mutation_text(value):
            raise _InvalidMutationValue


@dataclass(frozen=True, slots=True)
class EntityChange:
    """Closed row-level change; SQL text is always generated by the store."""

    operation: str
    table: str
    values: Mapping[str, None | int | str]
    where: Mapping[str, None | int | str]

    def __post_init__(self) -> None:
        try:
            if self.operation not in {"insert", "update"}:
                raise _InvalidMutationValue
            columns = _ENTITY_COLUMNS.get(self.table)
            if columns is None:
                raise _InvalidMutationValue
            values = _freeze_sql_values(dict(self.values)) if self.values else MappingProxyType({})
            where = _freeze_sql_values(dict(self.where)) if self.where else MappingProxyType({})
            if not set(values).issubset(columns) or not set(where).issubset(columns):
                raise _InvalidMutationValue
            if self.operation == "insert" and (not values or where):
                raise _InvalidMutationValue
            if self.operation == "update" and (not values or not where):
                raise _InvalidMutationValue
            primary_key = _ENTITY_PRIMARY_KEYS[self.table]
            if self.operation == "insert":
                _validate_entity_primary_key(self.table, values)
            update_columns = _ENTITY_UPDATE_COLUMNS.get(self.table, frozenset())
            if self.operation == "update" and (
                set(where) != set(primary_key)
                or not set(primary_key).isdisjoint(values)
                or not set(values).issubset(update_columns)
            ):
                raise _InvalidMutationValue
            if self.operation == "update":
                _validate_entity_primary_key(self.table, where)
            _frozen_mapping(
                {
                    "operation": self.operation,
                    "table": self.table,
                    "values": dict(values),
                    "where": dict(where),
                }
            )
        except (_InvalidMutationValue, TypeError, ValueError):
            raise MutationValidationError("entity change invalid") from None
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "where", where)

    @classmethod
    def insert(cls, table: str, values: Mapping[str, object]) -> Self:
        return cls("insert", table, values, {})  # type: ignore[arg-type]

    @classmethod
    def update(
        cls,
        table: str,
        values: Mapping[str, object],
        *,
        where: Mapping[str, object],
    ) -> Self:
        return cls("update", table, values, where)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ProjectMutationSnapshot:
    project_id: str
    revision: int
    authority_state: str
    entities: Mapping[str, tuple[Mapping[str, _FrozenJsonValue], ...]]

    def __post_init__(self) -> None:
        frozen_entities: dict[str, tuple[Mapping[str, _FrozenJsonValue], ...]] = {}
        row_count = 0
        byte_count = 0
        try:
            if (
                not _valid_mutation_text(self.project_id)
                or not _valid_revision(self.revision)
                or not _valid_mutation_text(self.authority_state)
                or not isinstance(self.entities, dict)
            ):
                raise _InvalidMutationValue
            for table, rows in self.entities.items():
                if table not in _ENTITY_COLUMNS or not isinstance(rows, (list, tuple)):
                    raise _InvalidMutationValue
                frozen_rows: list[Mapping[str, _FrozenJsonValue]] = []
                byte_count += len(table.encode("utf-8"))
                for row in rows:
                    row_count += 1
                    if row_count > MAX_SNAPSHOT_ROWS:
                        raise _InvalidMutationValue
                    frozen_row = _frozen_mapping(dict(row))
                    byte_count += len(_canonical_mutation_bytes(frozen_row))
                    if byte_count > MAX_SNAPSHOT_BYTES:
                        raise _InvalidMutationValue
                    frozen_rows.append(frozen_row)
                frozen_entities[table] = tuple(frozen_rows)
        except (_InvalidMutationValue, TypeError, ValueError):
            raise MutationValidationError("mutation snapshot invalid") from None
        object.__setattr__(self, "entities", MappingProxyType(frozen_entities))


@dataclass(frozen=True, slots=True)
class MutationDecision:
    changes: tuple[EntityChange, ...] = ()
    events: tuple[DomainEvent, ...] = ()
    result: Mapping[str, _FrozenJsonValue] = MappingProxyType({})

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.changes, tuple) or not all(
                type(item) is EntityChange for item in self.changes
            ):
                raise _InvalidMutationValue
            if not isinstance(self.events, tuple) or not all(
                type(item) is DomainEvent for item in self.events
            ):
                raise _InvalidMutationValue
            event_ids = [event.event_id for event in self.events]
            if len(event_ids) != len(set(event_ids)):
                raise _InvalidMutationValue
            result = _frozen_mapping(dict(self.result))
        except (_InvalidMutationValue, TypeError, ValueError):
            raise MutationValidationError("mutation decision invalid") from None
        object.__setattr__(self, "result", result)

    def result_dict(self) -> dict[str, _JsonValue]:
        return cast(dict[str, _JsonValue], _thaw_mutation_json(self.result))


@dataclass(frozen=True, slots=True)
class MutationOutcome:
    command_id: str
    revision: int
    event_ids: tuple[str, ...]
    result: Mapping[str, _FrozenJsonValue]

    def __post_init__(self) -> None:
        try:
            if (
                not _valid_mutation_text(self.command_id)
                or not _valid_revision(self.revision)
                or not isinstance(self.event_ids, tuple)
                or not all(_valid_mutation_text(item) for item in self.event_ids)
                or len(self.event_ids) != len(set(self.event_ids))
            ):
                raise _InvalidMutationValue
            result = _frozen_mapping(dict(self.result))
            _frozen_mapping(
                {
                    "command_id": self.command_id,
                    "revision": self.revision,
                    "event_ids": list(self.event_ids),
                    "result": _thaw_mutation_json(result),
                }
            )
        except (_InvalidMutationValue, TypeError, ValueError):
            raise MutationValidationError("mutation outcome invalid") from None
        object.__setattr__(self, "result", result)

    def to_dict(self) -> dict[str, _JsonValue]:
        return {
            "command_id": self.command_id,
            "revision": self.revision,
            "event_ids": list(self.event_ids),
            "result": _thaw_mutation_json(self.result),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        try:
            if not isinstance(value, dict) or set(value) != {
                "command_id", "revision", "event_ids", "result"
            }:
                raise _InvalidMutationValue
            event_ids = value["event_ids"]
            if not isinstance(event_ids, list):
                raise _InvalidMutationValue
            return cls(
                command_id=value["command_id"],
                revision=value["revision"],
                event_ids=tuple(event_ids),
                result=value["result"],
            )  # type: ignore[arg-type]
        except (KeyError, TypeError, MutationValidationError, _InvalidMutationValue):
            raise MutationValidationError("mutation outcome invalid") from None


def _client_event_matches_command(
    event: DomainEvent,
    command: CommandEnvelope,
) -> bool:
    provenance = event.provenance.to_dict()
    return (
        event.trigger_kind == "client_command"
        and provenance.get("command_id") == command.command_id
        and provenance.get("expected_revision") == command.expected_revision
        and provenance.get("actor") == command.actor_dict()
        and event.created_at == command.created_at
    )


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


def _database_family_identity(path: Path) -> _DatabaseFamilyIdentity:
    captured = _capture_database_family(path)
    return tuple(
        (suffix, captured[suffix][0], captured[suffix][1])
        for suffix in ("", "-wal", "-shm")
        if suffix in captured
    )


def _validate_database_family_identity(
    path: Path,
    expected: _DatabaseFamilyIdentity,
) -> None:
    try:
        actual = _database_family_identity(path)
    except SQLiteStoreError:
        raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY) from None
    if actual != expected:
        raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY)


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
        "_database_family_identity",
        "_project_revision",
        "_owner_pid",
        "_active_mutation_token",
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
        database_family_identity: _DatabaseFamilyIdentity,
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
        self._database_family_identity = database_family_identity
        self._project_revision = project_revision
        self._owner_pid = owner_pid
        self._active_mutation_token: _MutationToken | None = None

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
            database_family_identity = _database_family_identity(path)
            if database_family_identity[0][1:] != database_identity:
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
            database_family_identity=database_family_identity,
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
        _validate_database_family_identity(
            self._path,
            self._database_family_identity,
        )

    def _mutation_snapshot(
        self,
        *,
        revision: int,
        authority_state: str,
    ) -> ProjectMutationSnapshot:
        entities: dict[str, list[dict[str, object]]] = {}
        row_count = 0
        byte_count = 0
        for table in _ENTITY_COLUMNS:
            columns = [
                row[1]
                for row in self._connection.execute(
                    f'PRAGMA table_info("{table}")'
                )
            ]
            primary_key = _ENTITY_PRIMARY_KEYS[table]
            order_by = ",".join(f'"{column}"' for column in primary_key)
            cursor = self._connection.execute(
                f'SELECT * FROM "{table}" ORDER BY {order_by}'
            )
            rows: list[dict[str, object]] = []
            byte_count += len(table.encode("utf-8"))
            for row in cursor:
                row_count += 1
                if row_count > MAX_SNAPSHOT_ROWS:
                    raise MutationValidationError("mutation snapshot invalid")
                value = dict(zip(columns, row, strict=True))
                try:
                    frozen = _frozen_mapping(value)
                    byte_count += len(_canonical_mutation_bytes(frozen))
                except _InvalidMutationValue:
                    raise MutationValidationError("mutation snapshot invalid") from None
                if byte_count > MAX_SNAPSHOT_BYTES:
                    raise MutationValidationError("mutation snapshot invalid")
                rows.append(value)
            entities[table] = rows
        return ProjectMutationSnapshot(
            project_id=self._project_id,
            revision=revision,
            authority_state=authority_state,
            entities=entities,
        )

    def _validate_decision(
        self,
        command: CommandEnvelope,
        decision: object,
    ) -> MutationDecision:
        if type(decision) is not MutationDecision:
            raise MutationValidationError("mutation decision invalid")
        for event in decision.events:
            if not _client_event_matches_command(event, command):
                raise MutationValidationError("mutation decision invalid")
        return decision

    def _apply_entity_change(
        self,
        change: EntityChange,
        *,
        revision: int,
    ) -> None:
        for column, value in change.values.items():
            if column.endswith("_revision") and value is not None and value != revision:
                raise MutationValidationError("entity change invalid")
        if "project_id" in change.values and change.values["project_id"] != self._project_id:
            raise MutationValidationError("entity change invalid")

        if change.operation == "insert":
            columns = tuple(sorted(change.values))
            placeholders = ",".join("?" for _ in columns)
            quoted = ",".join(f'"{column}"' for column in columns)
            self._connection.execute(
                f'INSERT INTO "{change.table}" ({quoted}) VALUES ({placeholders})',
                tuple(change.values[column] for column in columns),
            )
            return

        value_columns = tuple(sorted(change.values))
        where_columns = tuple(sorted(change.where))
        assignments = ",".join(f'"{column}" = ?' for column in value_columns)
        predicates = " AND ".join(f'"{column}" IS ?' for column in where_columns)
        cursor = self._connection.execute(
            f'UPDATE "{change.table}" SET {assignments} WHERE {predicates}',
            tuple(change.values[column] for column in value_columns)
            + tuple(change.where[column] for column in where_columns),
        )
        if cursor.rowcount != 1:
            raise MutationValidationError("entity change invalid")

    def _insert_event(
        self,
        event: DomainEvent,
        *,
        revision: int,
    ) -> None:
        value = event.to_dict()
        provenance = cast(dict[str, object], value["provenance"])
        self._connection.execute(
            "INSERT INTO events("
            "event_id, project_id, project_revision, trigger_kind, kind, "
            "provenance_json, payload_json, command_id, adapter_event_id, "
            "internal_trigger_id, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                self._project_id,
                revision,
                event.trigger_kind,
                event.kind,
                json.dumps(
                    provenance,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                json.dumps(
                    value["payload"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                provenance.get("command_id"),
                provenance.get("adapter_event_id"),
                provenance.get("internal_trigger_id"),
                event.created_at,
            ),
        )

    def apply_command(
        self,
        command: CommandEnvelope,
        decide: Callable[[ProjectMutationSnapshot], MutationDecision],
    ) -> MutationOutcome:
        """Atomically apply or exactly replay one immutable client command."""

        active = self._active_mutation_token
        if active is not None:
            active.poisoned = True
            raise MutationValidationError("nested mutation rejected") from None
        self._validate_authority()
        if type(command) is not CommandEnvelope or not callable(decide):
            raise MutationValidationError("command envelope invalid")

        token = _MutationToken()
        self._active_mutation_token = token
        committed_revision: int | None = None
        began_transaction = False
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            began_transaction = True
            authority = self._connection.execute(
                "SELECT project_id, revision, authority_state FROM projects"
            ).fetchall()
            if authority != [
                (self._project_id, self._project_revision, self._authority_state)
            ]:
                raise SQLiteStoreError(_INVALID_AUTHORITY_IDENTITY)
            current_revision = self._project_revision

            existing = self._connection.execute(
                "SELECT command_id, project_id, input_hash, expected_revision, "
                "status, outcome_json, accepted_revision, completed_revision, "
                "actor_json, created_at FROM commands WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            if existing is not None:
                (
                    stored_command_id,
                    stored_project_id,
                    input_hash,
                    stored_expected_revision,
                    status,
                    outcome_json,
                    accepted_revision,
                    completed_revision,
                    actor_json,
                    stored_created_at,
                ) = existing
                if input_hash != command.input_hash:
                    raise CommandConflict("command input mismatch")
                try:
                    stored_actor = _parse_canonical_mutation_json(actor_json)
                    if not isinstance(stored_actor, dict):
                        raise _InvalidMutationValue
                    persisted_value = _parse_canonical_mutation_json(outcome_json)
                    persisted = MutationOutcome.from_dict(persisted_value)
                except (
                    _InvalidMutationValue,
                    MutationValidationError,
                    TypeError,
                ):
                    raise MutationValidationError("command outcome invalid") from None
                if (
                    stored_command_id != command.command_id
                    or stored_project_id != self._project_id
                    or stored_expected_revision != command.expected_revision
                    or status != "completed"
                    or stored_actor != command.actor_dict()
                    or stored_created_at != command.created_at
                    or persisted.command_id != command.command_id
                    or not _valid_revision(accepted_revision)
                    or not _valid_revision(completed_revision)
                    or accepted_revision != completed_revision
                    or completed_revision != persisted.revision
                    or persisted.revision != command.expected_revision + 1
                    or persisted.revision > current_revision
                ):
                    raise MutationValidationError("command outcome invalid")
                event_rows = self._connection.execute(
                    "SELECT event_id, project_id, project_revision, trigger_kind, "
                    "kind, provenance_json, payload_json, command_id, "
                    "adapter_event_id, internal_trigger_id, created_at FROM events "
                    "WHERE command_id = ? ORDER BY event_cursor",
                    (command.command_id,),
                )
                seen_event_ids: list[str] = []
                for row in event_rows:
                    if len(seen_event_ids) >= len(persisted.event_ids):
                        raise MutationValidationError("command outcome invalid")
                    (
                        event_id,
                        event_project_id,
                        event_revision,
                        trigger_kind,
                        event_kind,
                        provenance_json,
                        payload_json,
                        event_command_id,
                        adapter_event_id,
                        internal_trigger_id,
                        event_created_at,
                    ) = row
                    try:
                        provenance = _parse_canonical_mutation_json(provenance_json)
                        payload = _parse_canonical_mutation_json(payload_json)
                        if not isinstance(provenance, dict) or set(provenance) != {
                            "command_id",
                            "expected_revision",
                            "actor",
                        }:
                            raise _InvalidMutationValue
                        reconstructed = DomainEvent.client_command(
                            event_id=event_id,
                            kind=event_kind,
                            command_id=provenance["command_id"],
                            expected_revision=provenance["expected_revision"],
                            actor=provenance["actor"],
                            payload=payload,
                            created_at=event_created_at,
                        )
                    except (
                        _InvalidMutationValue,
                        KeyError,
                        TypeError,
                        ValueError,
                    ):
                        raise MutationValidationError("command outcome invalid") from None
                    if (
                        event_project_id != self._project_id
                        or event_revision != persisted.revision
                        or trigger_kind != "client_command"
                        or event_command_id != command.command_id
                        or adapter_event_id is not None
                        or internal_trigger_id is not None
                        or not _client_event_matches_command(reconstructed, command)
                        or event_id != persisted.event_ids[len(seen_event_ids)]
                    ):
                        raise MutationValidationError("command outcome invalid")
                    seen_event_ids.append(event_id)
                if tuple(seen_event_ids) != persisted.event_ids:
                    raise MutationValidationError("command outcome invalid")
                self._connection.rollback()
                began_transaction = False
                self._validate_authority()
                return persisted

            if command.expected_revision != current_revision:
                raise RevisionConflict("stale project revision")
            if current_revision == _MAX_SIGNED_64:
                raise RevisionConflict("stale project revision")
            next_revision = current_revision + 1
            snapshot = self._mutation_snapshot(
                revision=current_revision,
                authority_state=self._authority_state,
            )
            proposed = decide(snapshot)
            if (
                self._active_mutation_token is not token
                or token.poisoned
                or not self._connection.in_transaction
            ):
                message = (
                    "nested mutation rejected"
                    if token.poisoned
                    else "mutation transaction invalid"
                )
                raise MutationValidationError(message)
            decision = self._validate_decision(command, proposed)

            self._connection.execute(
                "INSERT INTO commands("
                "command_id, project_id, input_hash, expected_revision, status, "
                "outcome_json, accepted_revision, completed_revision, actor_json, created_at"
                ") VALUES (?, ?, ?, ?, 'accepted', NULL, ?, NULL, ?, ?)",
                (
                    command.command_id,
                    self._project_id,
                    command.input_hash,
                    command.expected_revision,
                    next_revision,
                    json.dumps(
                        command.actor_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    command.created_at,
                ),
            )
            for change in decision.changes:
                self._apply_entity_change(change, revision=next_revision)
            self._mutation_snapshot(
                revision=next_revision,
                authority_state=self._authority_state,
            )
            for event in decision.events:
                self._insert_event(event, revision=next_revision)

            outcome = MutationOutcome(
                command_id=command.command_id,
                revision=next_revision,
                event_ids=tuple(event.event_id for event in decision.events),
                result=decision.result,
            )
            outcome_json = json.dumps(
                outcome.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            updated_command = self._connection.execute(
                "UPDATE commands SET status = 'completed', outcome_json = ?, "
                "completed_revision = ? WHERE command_id = ? AND status = 'accepted'",
                (outcome_json, next_revision, command.command_id),
            )
            if updated_command.rowcount != 1:
                raise SQLiteStoreError(_INVALID_SCHEMA)
            updated_project = self._connection.execute(
                "UPDATE projects SET revision = ? "
                "WHERE project_id = ? AND revision = ?",
                (next_revision, self._project_id, current_revision),
            )
            if updated_project.rowcount != 1:
                raise RevisionConflict("stale project revision")
            self._connection.commit()
            began_transaction = False
            committed_revision = next_revision
        except BaseException:
            if (
                began_transaction
                and self._active_mutation_token is token
                and self._connection.in_transaction
            ):
                self._connection.rollback()
            self._validate_authority()
            raise
        finally:
            if self._active_mutation_token is token:
                self._active_mutation_token = None

        self._project_revision = cast(int, committed_revision)
        self._validate_authority()
        return outcome

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
