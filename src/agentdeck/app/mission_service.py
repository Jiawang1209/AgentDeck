"""Daemon-owned Mission application boundary.

The pure decision functions in this module consume only frozen domain values and
the detached store snapshot.  ``MissionService`` validates the client command
binding and delegates the one durable transaction to ``SQLiteMissionStore``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import cast

from agentdeck.domain.authorization import (
    AuthorizationEnvelope,
    ConfirmedMissionVersion,
    ExternalEffectPolicy,
    authorization_digest,
)
from agentdeck.domain.events import DomainEvent
from agentdeck.domain.mission import (
    MissionVersion,
    TaskSpec,
    validate_mutating_mission_slot,
)
from agentdeck.storage.sqlite_store import (
    CommandEnvelope,
    EntityChange,
    MutationDecision,
    MutationOutcome,
    MutationValidationError,
    ProjectMutationSnapshot,
    SQLiteMissionStore,
)


type _JsonValue = None | bool | int | str | list["_JsonValue"] | dict[str, "_JsonValue"]
type _FrozenJson = (
    None | bool | int | str | tuple["_FrozenJson", ...] | Mapping[str, "_FrozenJson"]
)

_MAX_SIGNED_64 = (2**63) - 1
_MAX_PROVENANCE_BYTES = 8 * 1024
_MAX_SPECIFICATION_BYTES = 64 * 1024
_MAX_JSON_DEPTH = 16
_TERMINAL_MISSION_STATES = frozenset({"completed", "failed", "cancelled"})
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SPECIFICATION_FIELDS = frozenset(
    {"mission_version", "authorization_envelope", "authorization_digest"}
)
_MISSION_FIELDS = frozenset(
    {
        "mission_id",
        "version",
        "goal",
        "scope",
        "exclusions",
        "tasks",
        "acceptance_criteria",
        "constraints",
        "max_parallel_tasks",
        "budget_units",
        "ordered_routes",
        "expires_at",
        "provenance_source",
        "provenance_id",
        "metadata",
    }
)
_TASK_FIELDS = frozenset(
    {
        "task_id",
        "objective",
        "role",
        "scope",
        "acceptance_contribution",
        "acceptance_criteria",
        "dependencies",
        "concurrency_keys",
        "retry_limit",
        "budget_units",
    }
)
_AUTHORIZATION_FIELDS = frozenset(
    {
        "goal",
        "semantic_scope",
        "path_scope",
        "exclusions",
        "operations",
        "allowed_agents",
        "allowed_roles",
        "external_effect_policy",
        "max_attempts",
        "max_retries",
        "max_recoveries",
        "budget_units",
        "acceptance_criteria",
        "ordered_routes",
        "expires_at",
        "metadata",
    }
)


class _InvalidMissionValue(Exception):
    pass


def _freeze_json(
    value: object,
    *,
    depth: int = 0,
    active: set[int] | None = None,
) -> _FrozenJson:
    if depth > _MAX_JSON_DEPTH:
        raise _InvalidMissionValue
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if not (-_MAX_SIGNED_64 - 1 <= value <= _MAX_SIGNED_64):
            raise _InvalidMissionValue
        return value
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _InvalidMissionValue from exc
        return value
    if isinstance(value, list):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            raise _InvalidMissionValue
        active.add(identity)
        try:
            return tuple(
                _freeze_json(item, depth=depth + 1, active=active) for item in value
            )
        finally:
            active.remove(identity)
    if isinstance(value, Mapping):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            raise _InvalidMissionValue
        active.add(identity)
        try:
            frozen: dict[str, _FrozenJson] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise _InvalidMissionValue
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise _InvalidMissionValue from exc
                frozen[key] = _freeze_json(item, depth=depth + 1, active=active)
            return MappingProxyType(frozen)
        finally:
            active.remove(identity)
    raise _InvalidMissionValue


def _thaw_json(value: _FrozenJson) -> _JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_json(value: object, *, maximum: int) -> str:
    try:
        frozen = _freeze_json(value)
        encoded = json.dumps(
            _thaw_json(frozen),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, _InvalidMissionValue):
        raise _InvalidMissionValue from None
    if len(encoded) > maximum:
        raise _InvalidMissionValue
    return encoded.decode("utf-8")


@dataclass(frozen=True, slots=True)
class MissionProposal:
    """A Leader proposal preserved as provenance, never mutation authority."""

    mission_version: MissionVersion
    authorization_envelope: AuthorizationEnvelope
    leader_provenance: Mapping[str, _FrozenJson]
    authorization_digest: str = field(init=False)
    leader_provenance_hash: str = field(init=False)

    def __post_init__(self) -> None:
        try:
            if (
                type(self.mission_version) is not MissionVersion
                or type(self.authorization_envelope) is not AuthorizationEnvelope
                or not isinstance(self.leader_provenance, Mapping)
                or not self.leader_provenance
            ):
                raise _InvalidMissionValue
            frozen = _freeze_json(self.leader_provenance)
            if not isinstance(frozen, Mapping):
                raise _InvalidMissionValue
            canonical_provenance = _canonical_json(
                _thaw_json(frozen), maximum=_MAX_PROVENANCE_BYTES
            )
            provenance_hash = "sha256:" + hashlib.sha256(
                canonical_provenance.encode("utf-8")
            ).hexdigest()
            digest = authorization_digest(
                self.mission_version, self.authorization_envelope
            )
            specification = {
                "mission_version": self.mission_version.to_dict(),
                "authorization_envelope": self.authorization_envelope.to_dict(),
                "authorization_digest": digest,
            }
            _canonical_json(specification, maximum=_MAX_SPECIFICATION_BYTES)
        except (TypeError, ValueError, _InvalidMissionValue):
            raise ValueError("mission proposal invalid") from None
        object.__setattr__(
            self,
            "leader_provenance",
            cast(Mapping[str, _FrozenJson], frozen),
        )
        object.__setattr__(self, "authorization_digest", digest)
        object.__setattr__(self, "leader_provenance_hash", provenance_hash)

    def leader_provenance_dict(self) -> dict[str, _JsonValue]:
        return cast(dict[str, _JsonValue], _thaw_json(self.leader_provenance))

    def specification_dict(self) -> dict[str, object]:
        return {
            "mission_version": self.mission_version.to_dict(),
            "authorization_envelope": self.authorization_envelope.to_dict(),
            "authorization_digest": self.authorization_digest,
        }


def _event_id(command: CommandEnvelope, action: str) -> str:
    identity = f"{command.command_id}\0{action}".encode("utf-8")
    return f"evt_{action}_{hashlib.sha256(identity).hexdigest()[:24]}"


def _valid_identifier(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return len(value.encode("utf-8")) <= 4096
    except UnicodeEncodeError:
        return False


def _valid_positive_version(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= _MAX_SIGNED_64
    )


def _event(
    command: CommandEnvelope,
    *,
    action: str,
    kind: str,
    payload: Mapping[str, object],
) -> DomainEvent:
    return DomainEvent.client_command(
        event_id=_event_id(command, action),
        kind=kind,
        command_id=command.command_id,
        expected_revision=command.expected_revision,
        actor=command.actor_dict(),
        payload=dict(payload),
        created_at=command.created_at,
    )


def _rows(
    snapshot: ProjectMutationSnapshot, table: str
) -> tuple[Mapping[str, _FrozenJson], ...]:
    rows = snapshot.entities.get(table, ())
    return cast(tuple[Mapping[str, _FrozenJson], ...], rows)


def _row_value(row: Mapping[str, _FrozenJson], key: str) -> object:
    return _thaw_json(row[key])


def propose_mission(
    snapshot: ProjectMutationSnapshot,
    proposal: MissionProposal,
    command: CommandEnvelope,
) -> MutationDecision:
    """Purely decide creation of one unconfirmed Mission version."""

    if (
        type(snapshot) is not ProjectMutationSnapshot
        or type(proposal) is not MissionProposal
        or type(command) is not CommandEnvelope
    ):
        raise MutationValidationError("mission proposal invalid")
    mission_version = proposal.mission_version
    if mission_version.version != 1:
        raise ValueError("mission version conflict")
    missions = _rows(snapshot, "missions")
    if any(_row_value(row, "mission_id") == mission_version.mission_id for row in missions):
        raise ValueError("mission version conflict")
    active = tuple(
        cast(str, _row_value(row, "mission_id"))
        for row in missions
        if _row_value(row, "status") not in _TERMINAL_MISSION_STATES
    )
    validate_mutating_mission_slot(mission_version.mission_id, active)

    next_revision = snapshot.revision + 1
    specification_json = _canonical_json(
        proposal.specification_dict(), maximum=_MAX_SPECIFICATION_BYTES
    )
    provenance_json = _canonical_json(
        proposal.leader_provenance_dict(), maximum=_MAX_PROVENANCE_BYTES
    )
    result = {
        "mission_id": mission_version.mission_id,
        "version": mission_version.version,
        "authorization_digest": proposal.authorization_digest,
        "status": "proposed",
    }
    return MutationDecision(
        changes=(
            EntityChange.insert(
                "missions",
                {
                    "mission_id": mission_version.mission_id,
                    "project_id": snapshot.project_id,
                    "current_version": mission_version.version,
                    "status": "proposed",
                    "created_revision": next_revision,
                    "updated_revision": next_revision,
                },
            ),
            EntityChange.insert(
                "mission_versions",
                {
                    "mission_id": mission_version.mission_id,
                    "version": mission_version.version,
                    "specification_json": specification_json,
                    "authorization_digest": proposal.authorization_digest,
                    "proposal_provenance_json": provenance_json,
                    "confirmed_revision": None,
                },
            ),
        ),
        events=(
            _event(
                command,
                action="mission_proposed",
                kind="mission_proposed",
                payload=result,
            ),
        ),
        result=result,
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _InvalidMissionValue
    return tuple(value)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _InvalidMissionValue
    return value


def _parse_task(value: object) -> TaskSpec:
    item = _mapping(value)
    if set(item) != _TASK_FIELDS:
        raise _InvalidMissionValue
    return TaskSpec(
        task_id=item["task_id"],
        objective=item["objective"],
        role=item["role"],
        scope=_string_tuple(item["scope"]),
        acceptance_contribution=_string_tuple(item["acceptance_contribution"]),
        acceptance_criteria=_string_tuple(item["acceptance_criteria"]),
        dependencies=_string_tuple(item["dependencies"]),
        concurrency_keys=_string_tuple(item["concurrency_keys"]),
        retry_limit=item["retry_limit"],
        budget_units=item["budget_units"],
    )  # type: ignore[arg-type]


def _parse_mission_version(value: object) -> MissionVersion:
    item = _mapping(value)
    if set(item) != _MISSION_FIELDS or not isinstance(item["tasks"], list):
        raise _InvalidMissionValue
    return MissionVersion(
        mission_id=item["mission_id"],
        version=item["version"],
        goal=item["goal"],
        scope=_string_tuple(item["scope"]),
        exclusions=_string_tuple(item["exclusions"]),
        tasks=tuple(_parse_task(task) for task in item["tasks"]),
        acceptance_criteria=_string_tuple(item["acceptance_criteria"]),
        constraints=_string_tuple(item["constraints"]),
        max_parallel_tasks=item["max_parallel_tasks"],
        budget_units=item["budget_units"],
        ordered_routes=_string_tuple(item["ordered_routes"]),
        expires_at=item["expires_at"],
        provenance_source=item["provenance_source"],
        provenance_id=item["provenance_id"],
        metadata=_mapping(item["metadata"]),
    )  # type: ignore[arg-type]


def _parse_authorization(value: object) -> AuthorizationEnvelope:
    item = _mapping(value)
    if set(item) != _AUTHORIZATION_FIELDS:
        raise _InvalidMissionValue
    policy = item["external_effect_policy"]
    if not isinstance(policy, str):
        raise _InvalidMissionValue
    return AuthorizationEnvelope(
        goal=item["goal"],
        semantic_scope=_string_tuple(item["semantic_scope"]),
        path_scope=_string_tuple(item["path_scope"]),
        exclusions=_string_tuple(item["exclusions"]),
        operations=_string_tuple(item["operations"]),
        allowed_agents=_string_tuple(item["allowed_agents"]),
        allowed_roles=_string_tuple(item["allowed_roles"]),
        external_effect_policy=ExternalEffectPolicy(policy),
        max_attempts=item["max_attempts"],
        max_retries=item["max_retries"],
        max_recoveries=item["max_recoveries"],
        budget_units=item["budget_units"],
        acceptance_criteria=_string_tuple(item["acceptance_criteria"]),
        ordered_routes=_string_tuple(item["ordered_routes"]),
        expires_at=item["expires_at"],
        metadata=_mapping(item["metadata"]),
    )  # type: ignore[arg-type]


def _decode_stored_specification(value: object) -> ConfirmedMissionVersion:
    try:
        if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_SPECIFICATION_BYTES:
            raise _InvalidMissionValue
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or set(parsed) != _SPECIFICATION_FIELDS:
            raise _InvalidMissionValue
        if _canonical_json(parsed, maximum=_MAX_SPECIFICATION_BYTES) != value:
            raise _InvalidMissionValue
        mission = _parse_mission_version(parsed["mission_version"])
        envelope = _parse_authorization(parsed["authorization_envelope"])
        digest = parsed["authorization_digest"]
        if not isinstance(digest, str):
            raise _InvalidMissionValue
        return ConfirmedMissionVersion(mission, envelope, digest)
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeEncodeError,
        ValueError,
        _InvalidMissionValue,
    ):
        raise ValueError("stored mission specification invalid") from None


def confirm_mission(
    snapshot: ProjectMutationSnapshot,
    *,
    mission_id: str,
    version: int,
    digest: str,
    command: CommandEnvelope,
) -> MutationDecision:
    """Purely confirm the exact persisted proposal and materialize its Tasks."""

    if (
        type(snapshot) is not ProjectMutationSnapshot
        or type(command) is not CommandEnvelope
        or not _valid_identifier(mission_id)
        or not _valid_positive_version(version)
    ):
        raise MutationValidationError("mission confirmation invalid")
    if not isinstance(digest, str) or _DIGEST_PATTERN.fullmatch(digest) is None:
        raise ValueError("authorization digest mismatch")
    matching = [
        row for row in _rows(snapshot, "missions") if _row_value(row, "mission_id") == mission_id
    ]
    if (
        len(matching) != 1
        or _row_value(matching[0], "status") != "proposed"
        or _row_value(matching[0], "current_version") != version
    ):
        raise ValueError("mission confirmation invalid")
    versions = [
        row
        for row in _rows(snapshot, "mission_versions")
        if _row_value(row, "mission_id") == mission_id
        and _row_value(row, "version") == version
    ]
    if len(versions) != 1:
        raise ValueError("mission confirmation invalid")
    stored = versions[0]
    if _row_value(stored, "confirmed_revision") is not None:
        raise ValueError("mission confirmation invalid")
    confirmed = _decode_stored_specification(_row_value(stored, "specification_json"))
    recomputed = authorization_digest(
        confirmed.mission_version, confirmed.authorization_envelope
    )
    if (
        confirmed.mission_version.mission_id != mission_id
        or confirmed.mission_version.version != version
        or _row_value(stored, "authorization_digest") != recomputed
        or confirmed.authorization_digest != recomputed
    ):
        raise ValueError("stored mission specification invalid")
    if digest != recomputed:
        raise ValueError("authorization digest mismatch")
    confirmed.confirm(digest)

    next_revision = snapshot.revision + 1
    result = {
        "mission_id": mission_id,
        "version": version,
        "authorization_digest": recomputed,
        "status": "confirmed",
    }
    changes: list[EntityChange] = [
        EntityChange.update(
            "mission_versions",
            {
                "authorization_digest": recomputed,
                "confirmed_revision": next_revision,
            },
            where={"mission_id": mission_id, "version": version},
        ),
        EntityChange.update(
            "missions",
            {"status": "confirmed", "updated_revision": next_revision},
            where={"mission_id": mission_id},
        ),
    ]
    changes.extend(
        EntityChange.insert(
            "tasks",
            {
                "task_id": task.task_id,
                "mission_id": mission_id,
                "mission_version": version,
                "specification_json": _canonical_json(
                    task.to_dict(), maximum=_MAX_SPECIFICATION_BYTES
                ),
                "status": "pending",
                "created_revision": next_revision,
                "updated_revision": next_revision,
            },
        )
        for task in confirmed.mission_version.tasks
    )
    return MutationDecision(
        changes=tuple(changes),
        events=(
            _event(
                command,
                action="mission_confirmed",
                kind="mission_confirmed",
                payload=result,
            ),
        ),
        result=result,
    )


def cancel_mission(
    snapshot: ProjectMutationSnapshot,
    *,
    mission_id: str,
    command: CommandEnvelope,
) -> MutationDecision:
    """Purely absorb one nonterminal Mission into the cancelled terminal state."""

    if (
        type(snapshot) is not ProjectMutationSnapshot
        or type(command) is not CommandEnvelope
        or not _valid_identifier(mission_id)
    ):
        raise MutationValidationError("mission cancellation invalid")
    matching = [
        row for row in _rows(snapshot, "missions") if _row_value(row, "mission_id") == mission_id
    ]
    if len(matching) != 1:
        raise ValueError("mission cancellation invalid")
    if _row_value(matching[0], "status") in _TERMINAL_MISSION_STATES:
        raise ValueError("mission terminal")
    next_revision = snapshot.revision + 1
    result = {"mission_id": mission_id, "status": "cancelled"}
    return MutationDecision(
        changes=(
            EntityChange.update(
                "missions",
                {"status": "cancelled", "updated_revision": next_revision},
                where={"mission_id": mission_id},
            ),
        ),
        events=(
            _event(
                command,
                action="mission_cancelled",
                kind="mission_cancelled",
                payload=result,
            ),
        ),
        result=result,
    )


def _validate_command(
    command: CommandEnvelope,
    *,
    kind: str,
    payload: Mapping[str, object],
) -> None:
    try:
        actor = command.actor_dict()
        payload_matches = _canonical_json(
            command.payload_dict(), maximum=_MAX_SPECIFICATION_BYTES
        ) == _canonical_json(dict(payload), maximum=_MAX_SPECIFICATION_BYTES)
    except (AttributeError, TypeError, _InvalidMissionValue):
        actor = {}
        payload_matches = False
    if (
        type(command) is not CommandEnvelope
        or command.kind != kind
        or actor.get("kind") != "human"
        or not payload_matches
    ):
        raise MutationValidationError("mission command invalid")


class MissionService:
    """Validate application DTOs and delegate the sole durable write."""

    def __init__(self, store: SQLiteMissionStore) -> None:
        if type(store) is not SQLiteMissionStore:
            raise MutationValidationError("mission service invalid")
        self._store = store

    def propose(
        self, command: CommandEnvelope, proposal: MissionProposal
    ) -> MutationOutcome:
        if type(proposal) is not MissionProposal:
            raise MutationValidationError("mission proposal invalid")
        _validate_command(
            command,
            kind="mission.propose",
            payload={
                "mission_id": proposal.mission_version.mission_id,
                "version": proposal.mission_version.version,
                "authorization_digest": proposal.authorization_digest,
                "leader_provenance_hash": proposal.leader_provenance_hash,
            },
        )
        return self._store.apply_command(
            command,
            lambda snapshot: propose_mission(snapshot, proposal, command),
        )

    def confirm(
        self,
        command: CommandEnvelope,
        *,
        mission_id: str,
        version: int,
        digest: str,
    ) -> MutationOutcome:
        if not _valid_identifier(mission_id) or not _valid_positive_version(version):
            raise MutationValidationError("mission command invalid")
        if not isinstance(digest, str) or _DIGEST_PATTERN.fullmatch(digest) is None:
            raise ValueError("authorization digest mismatch")
        _validate_command(
            command,
            kind="mission.confirm",
            payload={
                "mission_id": mission_id,
                "version": version,
                "authorization_digest": digest,
            },
        )
        return self._store.apply_command(
            command,
            lambda snapshot: confirm_mission(
                snapshot,
                mission_id=mission_id,
                version=version,
                digest=digest,
                command=command,
            ),
        )

    def cancel(
        self, command: CommandEnvelope, *, mission_id: str
    ) -> MutationOutcome:
        if not _valid_identifier(mission_id):
            raise MutationValidationError("mission command invalid")
        _validate_command(
            command,
            kind="mission.cancel",
            payload={"mission_id": mission_id},
        )
        return self._store.apply_command(
            command,
            lambda snapshot: cancel_mission(
                snapshot, mission_id=mission_id, command=command
            ),
        )
