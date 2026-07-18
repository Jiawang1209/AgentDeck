"""Sole-writer composition for closed durable Mission RPC commands."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import cast

from agentdeck.app.mission_service import (
    MissionProposal,
    MissionService,
)
from agentdeck.daemon.protocol import (
    MISSION_RPC_METHODS,
    RpcProtocolError,
    RpcRequest,
    encode_request,
)
from agentdeck.daemon.service import ProjectDaemonService, ServiceError
from agentdeck.storage.ownership import ProjectWriterLease, WriterLeaseError
from agentdeck.storage.sqlite_store import (
    CommandConflict,
    CommandEnvelope,
    MutationOutcome,
    MutationValidationError,
    SQLiteMissionStore,
)


_MAX_REQUEST_BYTES = 128 * 1024
_MAX_PERSISTED_ACTOR_BYTES = 16 * 1024
_MAX_PERSISTED_COMMAND_BYTES = 64 * 1024
_MAX_PERSISTED_JSON_DEPTH = 16
_MAX_TEXT_BYTES = 4096
_MAX_EVENT_PAGE = 100
_MAX_SIGNED_64 = (2**63) - 1
_DIGEST_PREFIX = "sha256:"

_COMMAND_FIELDS = frozenset(
    {"command_id", "actor", "expected_revision", "created_at"}
)
_PROPOSE_FIELDS = frozenset(
    {
        "command",
        "mission_version",
        "authorization_envelope",
        "authorization_digest",
        "leader_provenance",
        "expected_authority_state",
    }
)
_CONFIRM_FIELDS = frozenset(
    {
        "command",
        "mission_id",
        "version",
        "authorization_digest",
        "expected_authority_state",
    }
)


class MissionRuntimeError(RuntimeError):
    """A sanitized Mission RPC or sole-writer lifecycle failure."""


class _InvalidRequest(Exception):
    pass


class _StartupInterrupted(Exception):
    pass


class _DuplicateJsonKey(Exception):
    pass


def _text(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise _InvalidRequest
    try:
        if len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise _InvalidRequest
    except UnicodeEncodeError:
        raise _InvalidRequest from None
    return value


def _revision(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SIGNED_64:
        raise _InvalidRequest
    return value


def _positive(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_SIGNED_64:
        raise _InvalidRequest
    return value


def _object(value: object, fields: frozenset[str] | None = None) -> dict[str, object]:
    if type(value) is not dict:
        raise _InvalidRequest
    result = dict(cast(Mapping[str, object], value))
    if fields is not None and set(result) != fields:
        raise _InvalidRequest
    return result


def _validate_request_size(value: object, *, method: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _InvalidRequest
    try:
        frame = encode_request(
            RpcRequest("req_mission_runtime", method, value),
            max_bytes=_MAX_REQUEST_BYTES,
            allowed_methods=MISSION_RPC_METHODS,
        )
        document = json.loads(frame)
        params = document["params"]
        if type(params) is not dict:
            raise _InvalidRequest
        return cast(dict[str, object], params)
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeEncodeError,
        RecursionError,
        RpcProtocolError,
    ):
        raise _InvalidRequest from None


def _command(value: object, *, kind: str, payload: dict[str, object]) -> CommandEnvelope:
    item = _object(value, _COMMAND_FIELDS)
    actor = _object(item["actor"], frozenset({"kind", "id"}))
    if actor.get("kind") != "human":
        raise _InvalidRequest
    _text(actor.get("id"))
    return CommandEnvelope(
        command_id=_text(item["command_id"]),
        kind=kind,
        actor=actor,
        payload=payload,
        expected_revision=_revision(item["expected_revision"]),
        created_at=_text(item["created_at"]),
    )


def _digest(value: object) -> str:
    digest = _text(value)
    if (
        len(digest) != len(_DIGEST_PREFIX) + 64
        or not digest.startswith(_DIGEST_PREFIX)
    ):
        raise _InvalidRequest
    try:
        int(digest[len(_DIGEST_PREFIX) :], 16)
    except ValueError:
        raise _InvalidRequest from None
    return digest


def _reject_json_constant(_: str) -> object:
    raise ValueError("non-finite number")


def _object_without_duplicates(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _strict_canonical_json(value: object, *, maximum: int) -> object:
    """Parse one persisted JSON value only after its byte bound is proven."""
    if type(value) is not str:
        raise _InvalidRequest
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise _InvalidRequest from None
    if len(encoded) > maximum:
        raise _InvalidRequest
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
        stack = [(parsed, 0)]
        while stack:
            item, depth = stack.pop()
            if depth > _MAX_PERSISTED_JSON_DEPTH:
                raise _InvalidRequest
            if item is None or type(item) in {bool, str}:
                continue
            if type(item) is int:
                if not -_MAX_SIGNED_64 - 1 <= item <= _MAX_SIGNED_64:
                    raise _InvalidRequest
                continue
            if type(item) is list:
                stack.extend((child, depth + 1) for child in item)
                continue
            if type(item) is dict:
                stack.extend((child, depth + 1) for child in item.values())
                continue
            raise _InvalidRequest
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (
        json.JSONDecodeError,
        _DuplicateJsonKey,
        RecursionError,
        TypeError,
        UnicodeEncodeError,
        ValueError,
    ):
        raise _InvalidRequest from None
    if canonical != value:
        raise _InvalidRequest
    return parsed


class DaemonMissionRuntime:
    """Own one lease/store/service stack and expose only closed RPC methods."""

    __slots__ = (
        "_root",
        "_daemon_service",
        "_lease",
        "_store",
        "_mission_service",
        "_state",
        "_startup_task",
    )

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        daemon_service: ProjectDaemonService,
    ) -> None:
        if type(daemon_service) is not ProjectDaemonService:
            raise MissionRuntimeError("daemon Mission runtime is invalid")
        self._root = Path(os.path.abspath(os.fspath(root)))
        self._daemon_service = daemon_service
        self._lease: ProjectWriterLease | None = None
        self._store: SQLiteMissionStore | None = None
        self._mission_service: MissionService | None = None
        self._state = "new"
        self._startup_task: asyncio.Task[object] | None = None

    async def _cleanup_resources(
        self,
        *,
        store: SQLiteMissionStore | None,
        lease: ProjectWriterLease | None,
    ) -> BaseException | None:
        """Close every authority layer independently and return the first failure."""
        failure: BaseException | None = None

        def remember(exc: BaseException) -> None:
            nonlocal failure
            if failure is None:
                failure = exc

        try:
            try:
                await self._daemon_service.close()
            except BaseException as exc:
                remember(exc)
        finally:
            try:
                try:
                    if store is not None:
                        store.close()
                except BaseException as exc:
                    remember(exc)
            finally:
                try:
                    if lease is not None:
                        lease.close()
                except BaseException as exc:
                    remember(exc)
                finally:
                    if self._store is store:
                        self._store = None
                        self._mission_service = None
                    if self._lease is lease:
                        self._lease = None
        return failure

    async def start(self) -> None:
        if self._state != "new" or self._daemon_service.started:
            raise MissionRuntimeError("daemon Mission runtime cannot start")
        startup_task = asyncio.current_task()
        if startup_task is None:
            raise MissionRuntimeError("daemon Mission runtime cannot start")
        self._state = "starting"
        self._startup_task = startup_task
        lease: ProjectWriterLease | None = None
        store: SQLiteMissionStore | None = None
        try:
            lease = ProjectWriterLease.acquire(self._root)
            store = SQLiteMissionStore.open(self._root, lease=lease)
            service = MissionService(store)
            await self._daemon_service.start()
            if self._state != "starting":
                raise _StartupInterrupted
        except asyncio.CancelledError:
            await self._cleanup_resources(store=store, lease=lease)
            self._state = "closed"
            raise
        except WriterLeaseError:
            await self._cleanup_resources(store=store, lease=lease)
            self._state = "closed"
            raise MissionRuntimeError("daemon Mission writer is unavailable") from None
        except _StartupInterrupted:
            await self._cleanup_resources(store=store, lease=lease)
            self._state = "closed"
            raise MissionRuntimeError("daemon Mission runtime startup interrupted") from None
        except Exception:
            await self._cleanup_resources(store=store, lease=lease)
            self._state = "closed"
            raise MissionRuntimeError("daemon Mission runtime startup failed") from None
        finally:
            self._startup_task = None
        if self._state != "starting":
            await self._cleanup_resources(store=store, lease=lease)
            self._state = "closed"
            raise MissionRuntimeError("daemon Mission runtime startup interrupted")
        self._lease = lease
        self._store = store
        self._mission_service = service
        self._state = "started"

    async def close(self) -> None:
        if self._state == "closed":
            return
        startup_task = self._startup_task
        self._state = "closing"
        if startup_task is not None and startup_task is not asyncio.current_task():
            startup_task.cancel()
            try:
                await startup_task
            except (asyncio.CancelledError, MissionRuntimeError):
                pass
        failure = await self._cleanup_resources(
            store=self._store,
            lease=self._lease,
        )
        self._state = "closed"
        if isinstance(failure, asyncio.CancelledError):
            raise failure
        if failure is not None:
            raise MissionRuntimeError("daemon Mission runtime shutdown failed") from None

    def _require_started(self) -> tuple[SQLiteMissionStore, MissionService]:
        if (
            self._state != "started"
            or not self._daemon_service.accepting_governed_mutations
            or self._store is None
            or self._mission_service is None
        ):
            raise MissionRuntimeError("daemon Mission runtime is not started")
        return self._store, self._mission_service

    async def handle_rpc(
        self, method: str, params: Mapping[str, object]
    ) -> dict[str, object]:
        store, service = self._require_started()
        if method not in MISSION_RPC_METHODS:
            raise MissionRuntimeError("Mission RPC method is not allowed")
        try:
            closed = _validate_request_size(params, method=method)
            if method in {"mission.status", "events.after"}:
                return self._read_observation(store, method, closed)
            if method == "mission.propose":
                command, proposal, expected_state = self._parse_propose(closed)
                revalidate = lambda: self._state == "started" and self._revalidate_propose(
                    store, command, proposal, expected_state
                )
                mutate = lambda: service.propose(command, proposal).to_dict()
            else:
                command, mission_id, version, digest, expected_state = (
                    self._parse_confirm(closed)
                )
                revalidate = lambda: self._state == "started" and self._revalidate_confirm(
                    store,
                    command,
                    mission_id,
                    version,
                    digest,
                    expected_state,
                )
                mutate = lambda: service.confirm(
                    command, mission_id=mission_id, version=version, digest=digest
                ).to_dict()
        except (
            _InvalidRequest,
            KeyError,
            OverflowError,
            TypeError,
            ValueError,
            MutationValidationError,
        ):
            raise MissionRuntimeError("Mission RPC invalid request") from None
        pending = self._daemon_service.submit_governed_mutation(
            revalidate=revalidate,
            mutate=mutate,
        )
        try:
            result = await pending
        except asyncio.CancelledError:
            raise
        except ServiceError:
            raise
        except CommandConflict:
            raise MissionRuntimeError("Mission command conflict") from None
        except Exception:
            raise MissionRuntimeError("Mission RPC mutation failed") from None
        if not isinstance(result, dict):
            raise MissionRuntimeError("Mission RPC response is invalid")
        return result

    @classmethod
    def _read_observation(
        cls,
        store: SQLiteMissionStore,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        """Route one closed read method and sanitize every reader failure."""
        try:
            if method == "mission.status":
                return cls._mission_status(store, params)
            if method == "events.after":
                return cls._events_after(store, params)
            raise _InvalidRequest
        except MissionRuntimeError:
            raise
        except (sqlite3.Error, RuntimeError):
            raise MissionRuntimeError(
                "Mission observation is unavailable"
            ) from None

    @staticmethod
    def _parse_propose(
        params: dict[str, object],
    ) -> tuple[CommandEnvelope, MissionProposal, str]:
        item = _object(params, _PROPOSE_FIELDS)
        proposal = MissionProposal.from_rpc_dict(
            {
                "mission_version": item["mission_version"],
                "authorization_envelope": item["authorization_envelope"],
                "authorization_digest": item["authorization_digest"],
                "leader_provenance": item["leader_provenance"],
            }
        )
        expected_digest = _digest(item["authorization_digest"])
        if not hmac.compare_digest(proposal.authorization_digest, expected_digest):
            raise _InvalidRequest
        payload = {
            "mission_id": proposal.mission_version.mission_id,
            "version": proposal.mission_version.version,
            "authorization_digest": proposal.authorization_digest,
            "leader_provenance_hash": proposal.leader_provenance_hash,
        }
        return (
            _command(item["command"], kind="mission.propose", payload=payload),
            proposal,
            _text(item["expected_authority_state"]),
        )

    @staticmethod
    def _parse_confirm(
        params: dict[str, object],
    ) -> tuple[CommandEnvelope, str, int, str, str]:
        item = _object(params, _CONFIRM_FIELDS)
        mission_id = _text(item["mission_id"])
        version = _positive(item["version"])
        digest = _digest(item["authorization_digest"])
        payload = {
            "mission_id": mission_id,
            "version": version,
            "authorization_digest": digest,
        }
        return (
            _command(item["command"], kind="mission.confirm", payload=payload),
            mission_id,
            version,
            digest,
            _text(item["expected_authority_state"]),
        )

    @staticmethod
    def _project_authority(
        store: SQLiteMissionStore,
        command: CommandEnvelope,
    ) -> tuple[object, object, str]:
        with closing(store.open_reader()) as reader:
            reader.execute("BEGIN")
            project = reader.execute(
                "SELECT revision, authority_state FROM projects WHERE project_id = ?",
                (store.project_id,),
            ).fetchone()
            command_row = reader.execute(
                "SELECT project_id, input_hash, expected_revision, status, "
                "outcome_json, accepted_revision, completed_revision, "
                "actor_json, created_at FROM commands WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            reader.commit()
        if project is None or len(project) != 2:
            return None, None, "invalid"
        return project[0], project[1], DaemonMissionRuntime._command_state(
            store, command, command_row, project_revision=project[0]
        )

    @staticmethod
    def _command_state(
        store: SQLiteMissionStore,
        command: CommandEnvelope,
        row: object,
        *,
        project_revision: object,
    ) -> str:
        if row is None:
            return "absent"
        if not isinstance(row, tuple) or len(row) != 9:
            return "invalid"
        (
            project_id,
            input_hash,
            expected_revision,
            status,
            outcome_json,
            accepted_revision,
            completed_revision,
            actor_json,
            created_at,
        ) = row
        try:
            actor = _strict_canonical_json(
                actor_json,
                maximum=_MAX_PERSISTED_ACTOR_BYTES,
            )
            outcome = _strict_canonical_json(
                outcome_json,
                maximum=_MAX_PERSISTED_COMMAND_BYTES,
            )
            persisted = MutationOutcome.from_dict(outcome)
            if not isinstance(input_hash, str):
                raise _InvalidRequest
            exact_input = hmac.compare_digest(input_hash, command.input_hash)
            closed = (
                project_id == store.project_id
                and len(input_hash) == len(_DIGEST_PREFIX) + 64
                and input_hash.startswith(_DIGEST_PREFIX)
                and all(
                    character in "0123456789abcdef"
                    for character in input_hash[len(_DIGEST_PREFIX) :]
                )
                and all(
                    type(item) is int and 0 <= item <= _MAX_SIGNED_64
                    for item in (
                        project_revision,
                        expected_revision,
                        accepted_revision,
                        completed_revision,
                    )
                )
                and status == "completed"
                and accepted_revision == completed_revision == expected_revision + 1
                and project_revision >= completed_revision
                and isinstance(actor, dict)
                and actor == command.actor_dict()
                and persisted.command_id == command.command_id
                and persisted.revision == completed_revision
                and persisted.to_dict() == outcome
                and isinstance(created_at, str)
                and bool(created_at)
                and (
                    not exact_input
                    or (
                        expected_revision == command.expected_revision
                        and actor == command.actor_dict()
                        and created_at == command.created_at
                    )
                )
            )
        except (
            _InvalidRequest,
            MutationValidationError,
            TypeError,
            ValueError,
        ):
            return "invalid"
        if not closed:
            return "invalid"
        return (
            "completed_exact"
            if exact_input
            else "completed_conflict"
        )

    @classmethod
    def _revalidate_propose(
        cls,
        store: SQLiteMissionStore,
        command: CommandEnvelope,
        proposal: MissionProposal,
        expected_state: str,
    ) -> bool:
        revision, state, command_state = cls._project_authority(store, command)
        if command_state == "completed_exact":
            return state == expected_state == "sqlite_active"
        if command_state != "absent":
            return False
        return (
            revision == command.expected_revision
            and state == expected_state == "sqlite_active"
            and hmac.compare_digest(
                proposal.authorization_digest,
                cast(str, command.payload_dict()["authorization_digest"]),
            )
        )

    @classmethod
    def _revalidate_confirm(
        cls,
        store: SQLiteMissionStore,
        command: CommandEnvelope,
        mission_id: str,
        version: int,
        digest: str,
        expected_state: str,
    ) -> bool:
        with closing(store.open_reader()) as reader:
            reader.execute("BEGIN")
            project = reader.execute(
                "SELECT revision, authority_state FROM projects WHERE project_id = ?",
                (store.project_id,),
            ).fetchone()
            mission = reader.execute(
                "SELECT status, current_version FROM missions WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
            stored = reader.execute(
                "SELECT authorization_digest, confirmed_revision "
                "FROM mission_versions WHERE mission_id = ? AND version = ?",
                (mission_id, version),
            ).fetchone()
            command_row = reader.execute(
                "SELECT project_id, input_hash, expected_revision, status, "
                "outcome_json, accepted_revision, completed_revision, "
                "actor_json, created_at FROM commands WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            reader.commit()
        command_state = cls._command_state(
            store,
            command,
            command_row,
            project_revision=project[0] if project is not None else None,
        )
        if command_state == "completed_exact":
            return bool(
                project is not None
                and project[1] == expected_state == "sqlite_active"
            )
        if command_state != "absent":
            return False
        return bool(
            project == (command.expected_revision, expected_state)
            and expected_state == "sqlite_active"
            and mission == ("proposed", version)
            and stored is not None
            and stored[1] is None
            and isinstance(stored[0], str)
            and hmac.compare_digest(stored[0], digest)
            and hmac.compare_digest(
                digest, cast(str, command.payload_dict()["authorization_digest"])
            )
        )

    @staticmethod
    def _mission_status(
        store: SQLiteMissionStore, params: dict[str, object]
    ) -> dict[str, object]:
        item = _object(params, frozenset({"mission_id"}))
        mission_id = _text(item["mission_id"])
        with closing(store.open_reader()) as reader:
            reader.execute("BEGIN")
            project = reader.execute(
                "SELECT revision, authority_state FROM projects WHERE project_id = ?",
                (store.project_id,),
            ).fetchone()
            mission = reader.execute(
                "SELECT current_version, status FROM missions WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
            version = (
                reader.execute(
                    "SELECT authorization_digest FROM mission_versions "
                    "WHERE mission_id = ? AND version = ?",
                    (mission_id, mission[0]),
                ).fetchone()
                if mission is not None
                else None
            )
            task_count = (
                reader.execute(
                    "SELECT COUNT(*) FROM tasks WHERE mission_id = ?",
                    (mission_id,),
                ).fetchone()[0]
                if mission is not None
                else 0
            )
            reader.commit()
        if project is None:
            raise MissionRuntimeError("Mission status is unavailable")
        summary = None
        if mission is not None:
            if version is None or not isinstance(version[0], str):
                raise MissionRuntimeError("Mission status is unavailable")
            summary = {
                "mission_id": mission_id,
                "version": mission[0],
                "status": mission[1],
                "authorization_digest": version[0],
                "task_count": task_count,
            }
        return {
            "project_revision": project[0],
            "authority_state": project[1],
            "mission": summary,
        }

    @staticmethod
    def _events_after(
        store: SQLiteMissionStore, params: dict[str, object]
    ) -> dict[str, object]:
        item = _object(params, frozenset({"cursor", "limit"}))
        cursor = _revision(item["cursor"])
        limit = _positive(item["limit"])
        if limit > _MAX_EVENT_PAGE:
            raise _InvalidRequest
        with closing(store.open_reader()) as reader:
            reader.execute("BEGIN")
            project = reader.execute(
                "SELECT revision FROM projects WHERE project_id = ?",
                (store.project_id,),
            ).fetchone()
            rows = reader.execute(
                "SELECT event_cursor, event_id, project_revision, trigger_kind, kind "
                "FROM events WHERE event_cursor > ? ORDER BY event_cursor LIMIT ?",
                (cursor, limit + 1),
            ).fetchall()
            reader.commit()
        if project is None:
            raise MissionRuntimeError("Mission events are unavailable")
        visible = rows[:limit]
        events = [
            {
                "cursor": row[0],
                "event_id": row[1],
                "project_revision": row[2],
                "trigger_kind": row[3],
                "kind": row[4],
            }
            for row in visible
        ]
        return {
            "project_revision": project[0],
            "cursor": visible[-1][0] if visible else cursor,
            "events": events,
            "has_more": len(rows) > limit,
        }
