"""Bounded read-only ProjectView and reconnect projections.

The projection owns no mutation capability.  Each response is assembled from one
query-only SQLite transaction and closes its reader on every exit path.
"""

from __future__ import annotations

import json
import hmac
import os
import re
import sqlite3
import stat
from collections.abc import Callable
from pathlib import Path
from typing import cast
from urllib.parse import quote

from agentdeck.app.mission_service import adapter_event_integrity_hash
from agentdeck.domain.events import DomainEvent
from agentdeck.models import PROJECT_VIEW_SCHEMA_VERSION, PROJECT_VIEW_V2_SCHEMA_VERSION
from agentdeck.storage.migrations import AUTHORITY_STATES


_MAX_SIGNED_64 = (2**63) - 1
_MAX_PAGE = 100
_MAX_ROWS_PER_COLLECTION = 4096
_MAX_JSON_BYTES = 64 * 1024
_MAX_JSON_DEPTH = 16
_MAX_TEXT_BYTES = 4096
_MAX_PROJECTION_JSON_BYTES = 4 * 1024 * 1024
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,255}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_VERSIONS = {
    "v1": PROJECT_VIEW_SCHEMA_VERSION,
    "v2": PROJECT_VIEW_V2_SCHEMA_VERSION,
}
MISSION_PROJECTION_STATES = frozenset(
    {"proposed", "confirmed", "running", "paused", "completed", "failed", "cancelled"}
)
TASK_PROJECTION_STATES = frozenset(
    {
        "pending",
        "ready",
        "running",
        "awaiting_verification",
        "paused",
        "completed",
        "failed",
        "cancelled",
    }
)
ATTEMPT_PROJECTION_STATES = frozenset(
    {
        "pending",
        "running",
        "paused",
        "recovering",
        "awaiting_verification",
        "completed",
        "failed",
        "cancelled",
        "ambiguous",
    }
)
HANDOFF_PROJECTION_STATES = frozenset({"accepted"})
EVIDENCE_PROJECTION_KINDS = frozenset(
    {"test_result", "effect_proof", "verification_result"}
)
_TERMINAL_ATTEMPT_STATES = frozenset({"completed", "failed", "cancelled"})


class ProjectionError(RuntimeError):
    """A durable row could not be projected safely."""


class _InvalidProjection(Exception):
    pass


class _DuplicateKey(Exception):
    pass


def _duplicate_safe_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _reject_constant(_: str) -> object:
    raise ValueError("non-finite JSON")


def _canonical_value(value: object) -> object:
    if type(value) is not str:
        raise _InvalidProjection
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError:
        raise _InvalidProjection from None
    if len(raw) > _MAX_JSON_BYTES:
        raise _InvalidProjection
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=_duplicate_safe_object,
            parse_constant=_reject_constant,
        )
        stack: list[tuple[object, int]] = [(decoded, 0)]
        while stack:
            item, depth = stack.pop()
            if depth > _MAX_JSON_DEPTH:
                raise _InvalidProjection
            if item is None or type(item) in {bool, str}:
                if type(item) is str and len(item.encode("utf-8")) > _MAX_JSON_BYTES:
                    raise _InvalidProjection
                continue
            if type(item) is int:
                if not -_MAX_SIGNED_64 - 1 <= item <= _MAX_SIGNED_64:
                    raise _InvalidProjection
                continue
            if type(item) is list:
                stack.extend((child, depth + 1) for child in item)
                continue
            if type(item) is dict:
                stack.extend((child, depth + 1) for child in item.values())
                continue
            raise _InvalidProjection
        canonical = json.dumps(
            decoded,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (
        json.JSONDecodeError,
        _DuplicateKey,
        RecursionError,
        TypeError,
        UnicodeEncodeError,
        ValueError,
    ):
        raise _InvalidProjection from None
    if canonical != value:
        raise _InvalidProjection
    return decoded


def _canonical_object(value: object) -> dict[str, object]:
    decoded = _canonical_value(value)
    if type(decoded) is not dict:
        raise _InvalidProjection
    return cast(dict[str, object], decoded)


def _token(value: object) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise _InvalidProjection
    return value


def _text(value: object) -> str:
    if type(value) is not str or not value:
        raise _InvalidProjection
    try:
        if len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise _InvalidProjection
    except UnicodeEncodeError:
        raise _InvalidProjection from None
    return value


def _revision(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SIGNED_64:
        raise _InvalidProjection
    return value


def _positive(value: object) -> int:
    result = _revision(value)
    if result == 0:
        raise _InvalidProjection
    return result


def _nullable_revision(value: object) -> int | None:
    return None if value is None else _revision(value)


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise _InvalidProjection
    return value


def _state(value: object, allowed: frozenset[str]) -> str:
    result = _token(value)
    if result not in allowed:
        raise _InvalidProjection
    return result


def _owner_uid() -> int:
    return os.getuid()


def _canonical_root(value: str | os.PathLike[str]) -> Path:
    try:
        raw = os.fspath(value)
        if type(raw) is not str or not raw:
            raise _InvalidProjection
        absolute = Path(os.path.abspath(raw))
        if os.path.realpath(absolute) != os.fspath(absolute):
            raise _InvalidProjection
        root_stat = os.lstat(absolute)
        state_stat = os.lstat(absolute / ".agentdeck")
        if (
            stat.S_ISLNK(root_stat.st_mode)
            or not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_uid != _owner_uid()
            or stat.S_ISLNK(state_stat.st_mode)
            or not stat.S_ISDIR(state_stat.st_mode)
            or state_stat.st_uid != _owner_uid()
        ):
            raise _InvalidProjection
        return absolute
    except (OSError, TypeError, ValueError):
        raise _InvalidProjection from None


type _DatabaseIdentity = tuple[tuple[str, int, int], ...]
type _DirectoryIdentity = tuple[tuple[int, int], tuple[int, int]]


def _directory_identity(root: Path) -> _DirectoryIdentity:
    values: list[tuple[int, int]] = []
    for directory in (root, root / ".agentdeck"):
        try:
            item = os.lstat(directory)
        except OSError:
            raise _InvalidProjection from None
        if (
            stat.S_ISLNK(item.st_mode)
            or not stat.S_ISDIR(item.st_mode)
            or item.st_uid != _owner_uid()
        ):
            raise _InvalidProjection
        values.append((item.st_dev, item.st_ino))
    return cast(_DirectoryIdentity, tuple(values))


def _database_family_identity(path: Path) -> _DatabaseIdentity:
    rows: list[tuple[str, int, int]] = []
    for suffix in ("", "-wal", "-shm"):
        member = Path(f"{path}{suffix}")
        try:
            value = os.lstat(member)
        except FileNotFoundError:
            if suffix:
                continue
            raise _InvalidProjection from None
        except OSError:
            raise _InvalidProjection from None
        if (
            stat.S_ISLNK(value.st_mode)
            or not stat.S_ISREG(value.st_mode)
            or value.st_uid != _owner_uid()
            or stat.S_IMODE(value.st_mode) != 0o600
        ):
            raise _InvalidProjection
        rows.append((suffix, value.st_dev, value.st_ino))
    return tuple(rows)


def _bounded_rows(cursor: sqlite3.Cursor) -> list[tuple[object, ...]]:
    rows = cursor.fetchmany(_MAX_ROWS_PER_COLLECTION + 1)
    if len(rows) > _MAX_ROWS_PER_COLLECTION:
        raise _InvalidProjection
    return cast(list[tuple[object, ...]], rows)


def _coherent_revision(value: object, project_revision: int) -> int:
    revision = _revision(value)
    if revision > project_revision:
        raise _InvalidProjection
    return revision


def _event_item(row: tuple[object, ...], project_revision: int) -> dict[str, object]:
    if len(row) != 11:
        raise _InvalidProjection
    (
        raw_cursor,
        raw_event_id,
        raw_revision,
        raw_trigger,
        raw_kind,
        raw_provenance,
        raw_payload,
        raw_command_id,
        raw_adapter_id,
        raw_internal_id,
        raw_created_at,
    ) = row
    event_cursor = _positive(raw_cursor)
    event_id = _token(raw_event_id)
    kind = _token(raw_kind)
    trigger = _token(raw_trigger)
    created_at = _text(raw_created_at)
    provenance = _canonical_object(raw_provenance)
    payload = _canonical_value(raw_payload)
    if trigger == "client_command":
        if set(provenance) != {"command_id", "expected_revision", "actor"}:
            raise _InvalidProjection
        command_id = _token(raw_command_id)
        if raw_adapter_id is not None or raw_internal_id is not None:
            raise _InvalidProjection
        event = DomainEvent.client_command(
            event_id=event_id,
            kind=kind,
            command_id=command_id,
            expected_revision=provenance["expected_revision"],
            actor=provenance["actor"],
            payload=payload,
            created_at=created_at,
        )
        if provenance["command_id"] != command_id:
            raise _InvalidProjection
    elif trigger == "adapter_event":
        expected = {
            "adapter_event_id",
            "mission_id",
            "mission_version",
            "task_id",
            "attempt_id",
            "session_id",
            "sequence",
            "integrity_hash",
        }
        if set(provenance) != expected:
            raise _InvalidProjection
        adapter_id = _token(raw_adapter_id)
        if raw_command_id is not None or raw_internal_id is not None:
            raise _InvalidProjection
        event = DomainEvent.adapter_event(
            event_id=event_id,
            kind=kind,
            adapter_event_id=adapter_id,
            mission_id=provenance["mission_id"],
            mission_version=provenance["mission_version"],
            task_id=provenance["task_id"],
            attempt_id=provenance["attempt_id"],
            session_id=provenance["session_id"],
            sequence=provenance["sequence"],
            integrity_hash=provenance["integrity_hash"],
            payload=payload,
            created_at=created_at,
        )
        if provenance["adapter_event_id"] != adapter_id:
            raise _InvalidProjection
        expected_integrity = adapter_event_integrity_hash(
            event_id=event_id,
            kind=kind,
            adapter_event_id=adapter_id,
            mission_id=cast(str, provenance["mission_id"]),
            mission_version=cast(str, provenance["mission_version"]),
            task_id=cast(str, provenance["task_id"]),
            attempt_id=cast(str, provenance["attempt_id"]),
            session_id=cast(str, provenance["session_id"]),
            sequence=cast(int, provenance["sequence"]),
            payload=payload,
            created_at=created_at,
        )
        stored_integrity = provenance["integrity_hash"]
        if type(stored_integrity) is not str or not hmac.compare_digest(
            stored_integrity, expected_integrity
        ):
            raise _InvalidProjection
    elif trigger == "internal_trigger":
        if set(provenance) != {
            "internal_trigger_id",
            "source_revision",
            "source_snapshot_id",
        }:
            raise _InvalidProjection
        internal_id = _token(raw_internal_id)
        if raw_command_id is not None or raw_adapter_id is not None:
            raise _InvalidProjection
        event = DomainEvent.internal_trigger(
            event_id=event_id,
            kind=kind,
            internal_trigger_id=internal_id,
            source_revision=provenance["source_revision"],
            source_snapshot_id=provenance["source_snapshot_id"],
            payload=payload,
            created_at=created_at,
        )
        if provenance["internal_trigger_id"] != internal_id:
            raise _InvalidProjection
    else:
        raise _InvalidProjection
    if event.to_dict()["provenance"] != provenance:
        raise _InvalidProjection
    return {
        "cursor": event_cursor,
        "event_id": event_id,
        "project_revision": _coherent_revision(raw_revision, project_revision),
        "trigger_kind": trigger,
        "kind": kind,
        "created_at": created_at,
    }


class ProjectViewProjection:
    """Build compact v1/v2 compatibility views from the SQLite authority."""

    __slots__ = (
        "_root",
        "_path",
        "_project_id",
        "_directory_identity",
        "_database_identity",
    )

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        expected_project_id: str,
    ) -> None:
        if type(expected_project_id) is not str or not expected_project_id:
            raise ValueError("ProjectView read authority invalid")
        try:
            if len(expected_project_id.encode("utf-8")) > _MAX_TEXT_BYTES:
                raise _InvalidProjection
            canonical_root = _canonical_root(root)
            path = canonical_root / ".agentdeck" / "state.db"
            directory_identity = _directory_identity(canonical_root)
            identity = _database_family_identity(path)
        except (UnicodeEncodeError, _InvalidProjection):
            raise ValueError("ProjectView read authority invalid") from None
        self._root = canonical_root
        self._path = path
        self._project_id = expected_project_id
        self._directory_identity = directory_identity
        self._database_identity = identity

    def _validate_database_identity(self) -> None:
        if (
            _directory_identity(self._root) != self._directory_identity
            or _database_family_identity(self._path) != self._database_identity
        ):
            raise _InvalidProjection

    def _open_reader(self) -> sqlite3.Connection:
        self._validate_database_identity()
        try:
            reader = sqlite3.connect(
                "file:" + quote(os.fspath(self._path), safe="/") + "?mode=ro",
                uri=True,
                isolation_level=None,
                timeout=0,
            )
        except sqlite3.Error:
            raise _InvalidProjection from None
        try:
            self._validate_database_identity()
            reader.execute("PRAGMA query_only=ON")
            if reader.execute("PRAGMA query_only").fetchone() != (1,):
                raise _InvalidProjection
            return reader
        except BaseException:
            reader.close()
            raise

    def _read(self, build: Callable[[sqlite3.Connection], dict[str, object]], error: str):
        try:
            reader = self._open_reader()
            try:
                reader.execute("BEGIN")
                try:
                    result = build(reader)
                    reader.commit()
                except BaseException:
                    try:
                        reader.rollback()
                    except sqlite3.Error:
                        pass
                    raise
            finally:
                try:
                    reader.close()
                finally:
                    self._validate_database_identity()
            return result
        except (ProjectionError, KeyboardInterrupt, SystemExit):
            raise
        except (
            sqlite3.Error,
            _InvalidProjection,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            raise ProjectionError(error) from None

    def snapshot(self, version: str) -> dict[str, object]:
        if type(version) is not str or version not in _VERSIONS:
            raise ValueError("ProjectView version invalid")

        def build(reader: sqlite3.Connection) -> dict[str, object]:
            project_id = self._project_id
            project = reader.execute(
                "SELECT revision, authority_state, authority_generation "
                "FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if project is None or len(project) != 3:
                raise _InvalidProjection
            project_revision = _revision(project[0])
            authority_state = _token(project[1])
            authority_generation = _revision(project[2])
            if authority_state not in AUTHORITY_STATES:
                raise _InvalidProjection

            json_budget = _MAX_PROJECTION_JSON_BYTES

            def validate_json(value: object) -> None:
                nonlocal json_budget
                _canonical_object(value)
                json_budget -= len(cast(str, value).encode("utf-8"))
                if json_budget < 0:
                    raise _InvalidProjection

            mission_rows = _bounded_rows(
                reader.execute(
                    "SELECT m.mission_id,m.current_version,m.status,m.created_revision,"
                    "m.updated_revision,v.authorization_digest,v.specification_json,"
                    "v.proposal_provenance_json,v.confirmed_revision "
                    "FROM missions m LEFT JOIN mission_versions v "
                    "ON v.mission_id=m.mission_id AND v.version=m.current_version "
                    "WHERE m.project_id=? ORDER BY m.mission_id",
                    (project_id,),
                )
            )
            missions: list[dict[str, object]] = []
            mission_keys: set[tuple[str, int]] = set()
            mission_ids: set[str] = set()
            for row in mission_rows:
                if len(row) != 9:
                    raise _InvalidProjection
                validate_json(row[6])
                validate_json(row[7])
                mission_id = _token(row[0])
                mission_version = _positive(row[1])
                status = _state(row[2], MISSION_PROJECTION_STATES)
                created = _coherent_revision(row[3], project_revision)
                updated = _coherent_revision(row[4], project_revision)
                confirmed = _nullable_revision(row[8])
                if confirmed is not None and confirmed > project_revision:
                    raise _InvalidProjection
                if created > updated:
                    raise _InvalidProjection
                if status == "proposed":
                    if confirmed is not None:
                        raise _InvalidProjection
                elif (
                    confirmed is None
                    or confirmed < created
                    or confirmed > updated
                ):
                    raise _InvalidProjection
                key = (mission_id, mission_version)
                if key in mission_keys:
                    raise _InvalidProjection
                mission_keys.add(key)
                mission_ids.add(mission_id)
                missions.append(
                    {
                        "mission_id": mission_id,
                        "version": mission_version,
                        "status": status,
                        "authorization_digest": _digest(row[5]),
                        "created_revision": created,
                        "updated_revision": updated,
                    }
                )

            version_rows = _bounded_rows(
                reader.execute(
                    "SELECT mission_id,version,confirmed_revision FROM mission_versions "
                    "ORDER BY mission_id,version"
                )
            )
            all_mission_versions: dict[tuple[str, int], int | None] = {}
            for row in version_rows:
                if len(row) != 3:
                    raise _InvalidProjection
                key = (_token(row[0]), _positive(row[1]))
                if key[0] not in mission_ids or key in all_mission_versions:
                    raise _InvalidProjection
                confirmed_revision = _nullable_revision(row[2])
                if (
                    confirmed_revision is not None
                    and confirmed_revision > project_revision
                ):
                    raise _InvalidProjection
                all_mission_versions[key] = confirmed_revision
            if not mission_keys.issubset(all_mission_versions.keys()):
                raise _InvalidProjection

            task_rows = _bounded_rows(
                reader.execute(
                    "SELECT task_id,mission_id,mission_version,specification_json,status,"
                    "created_revision,updated_revision FROM tasks ORDER BY task_id"
                )
            )
            tasks: list[dict[str, object]] = []
            task_lineage: dict[str, tuple[str, int]] = {}
            for row in task_rows:
                if len(row) != 7:
                    raise _InvalidProjection
                validate_json(row[3])
                task_id = _token(row[0])
                mission_id = _token(row[1])
                mission_version = _positive(row[2])
                if all_mission_versions.get((mission_id, mission_version)) is None:
                    raise _InvalidProjection
                if task_id in task_lineage:
                    raise _InvalidProjection
                task_lineage[task_id] = (mission_id, mission_version)
                created = _coherent_revision(row[5], project_revision)
                updated = _coherent_revision(row[6], project_revision)
                if created > updated:
                    raise _InvalidProjection
                tasks.append(
                    {
                        "task_id": task_id,
                        "mission_id": mission_id,
                        "mission_version": mission_version,
                        "status": _state(row[4], TASK_PROJECTION_STATES),
                        "created_revision": created,
                        "updated_revision": updated,
                    }
                )

            attempt_rows = _bounded_rows(
                reader.execute(
                    "SELECT attempt_id,task_id,attempt_number,status,route_position,"
                    "budget_json,started_revision,terminal_revision "
                    "FROM attempts ORDER BY task_id,attempt_number,attempt_id"
                )
            )
            attempts: list[dict[str, object]] = []
            attempt_lineage: dict[str, str] = {}
            for row in attempt_rows:
                if len(row) != 8:
                    raise _InvalidProjection
                validate_json(row[5])
                attempt_id = _token(row[0])
                task_id = _token(row[1])
                if task_id not in task_lineage or attempt_id in attempt_lineage:
                    raise _InvalidProjection
                attempt_lineage[attempt_id] = task_id
                status = _state(row[3], ATTEMPT_PROJECTION_STATES)
                started = _coherent_revision(row[6], project_revision)
                terminal = _nullable_revision(row[7])
                if terminal is not None and (terminal > project_revision or terminal < started):
                    raise _InvalidProjection
                if (status in _TERMINAL_ATTEMPT_STATES) != (terminal is not None):
                    raise _InvalidProjection
                attempts.append(
                    {
                        "attempt_id": attempt_id,
                        "task_id": task_id,
                        "attempt_number": _positive(row[2]),
                        "status": status,
                        "route_position": _revision(row[4]),
                        "started_revision": started,
                        "terminal_revision": terminal,
                    }
                )

            handoff_rows = _bounded_rows(
                reader.execute(
                    "SELECT handoff_id,mission_id,source_task_id,destination_task_id,"
                    "status,context_json,created_revision,accepted_revision "
                    "FROM handoffs ORDER BY handoff_id"
                )
            )
            handoffs: list[dict[str, object]] = []
            for row in handoff_rows:
                if len(row) != 8:
                    raise _InvalidProjection
                validate_json(row[5])
                mission_id = _token(row[1])
                source_task_id = _token(row[2])
                destination_task_id = _token(row[3])
                source_lineage = task_lineage.get(source_task_id)
                destination_lineage = task_lineage.get(destination_task_id)
                if (
                    source_lineage is None
                    or destination_lineage is None
                    or source_lineage[0] != mission_id
                    or destination_lineage[0] != mission_id
                ):
                    raise _InvalidProjection
                created = _coherent_revision(row[6], project_revision)
                accepted = _nullable_revision(row[7])
                if (
                    accepted is None
                    or accepted > project_revision
                    or accepted < created
                ):
                    raise _InvalidProjection
                handoffs.append(
                    {
                        "handoff_id": _token(row[0]),
                        "mission_id": mission_id,
                        "source_task_id": source_task_id,
                        "destination_task_id": destination_task_id,
                        "status": _state(row[4], HANDOFF_PROJECTION_STATES),
                        "created_revision": created,
                        "accepted_revision": accepted,
                    }
                )

            evidence_rows = _bounded_rows(
                reader.execute(
                    "SELECT evidence_id,task_id,attempt_id,kind,integrity_hash,"
                    "summary_json,created_revision FROM evidence ORDER BY evidence_id"
                )
            )
            evidence: list[dict[str, object]] = []
            for row in evidence_rows:
                if len(row) != 7:
                    raise _InvalidProjection
                validate_json(row[5])
                task_id = _token(row[1])
                attempt_id = _token(row[2])
                if (
                    task_id not in task_lineage
                    or attempt_lineage.get(attempt_id) != task_id
                ):
                    raise _InvalidProjection
                evidence.append(
                    {
                        "evidence_id": _token(row[0]),
                        "task_id": task_id,
                        "attempt_id": attempt_id,
                        "kind": _state(row[3], EVIDENCE_PROJECTION_KINDS),
                        "integrity_hash": _digest(row[4]),
                        "created_revision": _coherent_revision(row[6], project_revision),
                    }
                )

            cursor_row = reader.execute(
                "SELECT COALESCE(MAX(event_cursor),0) FROM events WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if cursor_row is None or len(cursor_row) != 1:
                raise _InvalidProjection
            event_cursor = _revision(cursor_row[0])
            return {
                "schema_version": _VERSIONS[version],
                "project_id": _token(project_id),
                "project_revision": project_revision,
                "authority": {
                    "state": authority_state,
                    "generation": authority_generation,
                },
                "event_cursor": event_cursor,
                "missions": {"count": len(missions), "items": missions},
                "tasks": {"count": len(tasks), "items": tasks},
                "attempts": {"count": len(attempts), "items": attempts},
                "handoffs": {"count": len(handoffs), "items": handoffs},
                "evidence": {"count": len(evidence), "items": evidence},
            }

        return self._read(build, "ProjectView projection unavailable")

    def events_after(self, cursor: int, limit: int) -> dict[str, object]:
        if type(cursor) is not int or not 0 <= cursor <= _MAX_SIGNED_64:
            raise ValueError("event cursor invalid")
        if type(limit) is not int or not 1 <= limit <= _MAX_PAGE:
            raise ValueError("event page limit invalid")

        def build(reader: sqlite3.Connection) -> dict[str, object]:
            project_id = self._project_id
            project = reader.execute(
                "SELECT revision,authority_generation FROM projects WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if project is None or len(project) != 2:
                raise _InvalidProjection
            project_revision = _revision(project[0])
            authority_generation = _revision(project[1])
            rows = reader.execute(
                "SELECT event_cursor,event_id,project_revision,trigger_kind,kind,"
                "provenance_json,payload_json,command_id,adapter_event_id,"
                "internal_trigger_id,created_at FROM events "
                "WHERE project_id=? AND event_cursor>? ORDER BY event_cursor LIMIT ?",
                (project_id, cursor, limit + 1),
            ).fetchall()
            if len(rows) > limit + 1:
                raise _InvalidProjection
            validated: list[dict[str, object]] = []
            previous = cursor
            json_budget = _MAX_PROJECTION_JSON_BYTES
            for row in rows:
                item = _event_item(cast(tuple[object, ...], row), project_revision)
                item_cursor = cast(int, item["cursor"])
                if item_cursor <= previous:
                    raise _InvalidProjection
                previous = item_cursor
                json_budget -= len(cast(str, row[5]).encode("utf-8"))
                json_budget -= len(cast(str, row[6]).encode("utf-8"))
                if json_budget < 0:
                    raise _InvalidProjection
                validated.append(item)
            events = validated[:limit]
            return {
                "project_revision": project_revision,
                "authority_generation": authority_generation,
                "cursor": events[-1]["cursor"] if events else cursor,
                "events": events,
                "has_more": len(rows) > limit,
                "limit": limit,
            }

        return self._read(build, "ProjectView event reconnect unavailable")
