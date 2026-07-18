"""Daemon-owned Mission application boundary.

The pure decision functions in this module consume only frozen domain values and
the detached store snapshot.  ``MissionService`` validates the client command
binding and delegates the one durable transaction to ``SQLiteMissionStore``.
"""

from __future__ import annotations

import hashlib
import hmac
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
    RecoveryContext,
    RuntimeFact,
    TaskSpec,
    TaskRuntimeState,
    record_evidence as decide_record_evidence,
    record_handoff as decide_record_handoff,
    record_worker_event as decide_record_worker_event,
    release_ready_tasks as decide_release_ready_tasks,
    start_attempt as decide_start_attempt,
    validate_mutating_mission_slot,
)
from agentdeck.domain.verification import EvidenceFact, verify_task as grade_task
from agentdeck.storage.sqlite_store import (
    CommandEnvelope,
    EntityChange,
    EventMutationOutcome,
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
_MAX_RECONCILIATION_BLOCKERS = 16
_TERMINAL_MISSION_STATES = frozenset({"completed", "failed", "cancelled"})
_RECONCILIATION_BLOCKER_SCOPES = {
    "terminal_failed": "task",
    "terminal_cancelled": "task",
    "ambiguous_effect": "mission",
    "permission_conflict": "mission",
    "task_local_pause": "task",
    "session_takeover": "session",
}
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


@dataclass(frozen=True, slots=True)
class StartAttemptRequest:
    mission_id: str
    mission_version: int
    task_id: str
    attempt_id: str
    session_id: str
    agent_id: str
    model_id: str | None
    transport: str
    route_position: int
    budget_units: int
    operation_id: str

    def __post_init__(self) -> None:
        if not (
            all(
                _valid_identifier(item)
                for item in (
                    self.mission_id,
                    self.task_id,
                    self.attempt_id,
                    self.session_id,
                    self.agent_id,
                    self.transport,
                    self.operation_id,
                )
            )
            and _valid_positive_version(self.mission_version)
            and (self.model_id is None or _valid_identifier(self.model_id))
            and isinstance(self.route_position, int)
            and not isinstance(self.route_position, bool)
            and 0 <= self.route_position <= _MAX_SIGNED_64
            and isinstance(self.budget_units, int)
            and not isinstance(self.budget_units, bool)
            and 1 <= self.budget_units <= _MAX_SIGNED_64
        ):
            raise MutationValidationError("attempt request invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id,
            "mission_version": self.mission_version,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "model_id": self.model_id,
            "transport": self.transport,
            "route_position": self.route_position,
            "budget_units": self.budget_units,
            "operation_id": self.operation_id,
        }


@dataclass(frozen=True, slots=True)
class HandoffRequest:
    handoff_id: str
    mission_id: str
    mission_version: int
    source_task_id: str
    source_attempt_id: str
    destination_task_id: str
    evidence_ids: tuple[str, ...]
    context: Mapping[str, _FrozenJson]

    def __post_init__(self) -> None:
        try:
            if not (
                all(
                    _valid_identifier(item)
                    for item in (
                        self.handoff_id,
                        self.mission_id,
                        self.source_task_id,
                        self.source_attempt_id,
                        self.destination_task_id,
                    )
                )
                and _valid_positive_version(self.mission_version)
                and isinstance(self.evidence_ids, tuple)
                and self.evidence_ids
                and all(_valid_identifier(item) for item in self.evidence_ids)
                and len(set(self.evidence_ids)) == len(self.evidence_ids)
                and isinstance(self.context, Mapping)
            ):
                raise _InvalidMissionValue
            frozen = _freeze_json(self.context)
            if not isinstance(frozen, Mapping):
                raise _InvalidMissionValue
            _canonical_json(_thaw_json(frozen), maximum=_MAX_PROVENANCE_BYTES)
        except (_InvalidMissionValue, TypeError, ValueError):
            raise MutationValidationError("handoff request invalid") from None
        object.__setattr__(self, "context", cast(Mapping[str, _FrozenJson], frozen))

    def to_dict(self) -> dict[str, object]:
        return {
            "handoff_id": self.handoff_id,
            "mission_id": self.mission_id,
            "mission_version": self.mission_version,
            "source_task_id": self.source_task_id,
            "source_attempt_id": self.source_attempt_id,
            "destination_task_id": self.destination_task_id,
            "evidence_ids": list(self.evidence_ids),
            "context": _thaw_json(cast(_FrozenJson, self.context)),
        }


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


def adapter_event_integrity_hash(
    *,
    event_id: str,
    kind: str,
    adapter_event_id: str,
    mission_id: str,
    mission_version: str,
    task_id: str,
    attempt_id: str,
    session_id: str,
    sequence: int,
    payload: object,
    created_at: str,
) -> str:
    """Bind every ordered adapter-event field except the digest itself."""

    try:
        if not all(
            _valid_identifier(item)
            for item in (
                event_id,
                kind,
                adapter_event_id,
                mission_id,
                mission_version,
                task_id,
                attempt_id,
                session_id,
                created_at,
            )
        ) or not (
            isinstance(sequence, int)
            and not isinstance(sequence, bool)
            and 0 <= sequence <= _MAX_SIGNED_64
        ):
            raise _InvalidMissionValue
        canonical = _canonical_json(
            {
                "event_id": event_id,
                "kind": kind,
                "adapter_event_id": adapter_event_id,
                "mission_id": mission_id,
                "mission_version": mission_version,
                "task_id": task_id,
                "attempt_id": attempt_id,
                "session_id": session_id,
                "sequence": sequence,
                "payload": payload,
                "created_at": created_at,
            },
            maximum=_MAX_SPECIFICATION_BYTES,
        )
    except (_InvalidMissionValue, TypeError, ValueError):
        raise MutationValidationError("mission event invalid") from None
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
            provenance_envelope = {
                "leader_provenance": _thaw_json(frozen),
                "leader_provenance_hash": provenance_hash,
            }
            _canonical_json(provenance_envelope, maximum=_MAX_PROVENANCE_BYTES)
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

    def proposal_provenance_dict(self) -> dict[str, object]:
        return {
            "leader_provenance": self.leader_provenance_dict(),
            "leader_provenance_hash": self.leader_provenance_hash,
        }

    @classmethod
    def from_rpc_dict(cls, value: object) -> "MissionProposal":
        """Decode one closed untrusted RPC proposal without stored-row semantics."""
        try:
            item = _mapping(value)
            if set(item) != {
                "mission_version",
                "authorization_envelope",
                "authorization_digest",
                "leader_provenance",
            }:
                raise _InvalidMissionValue
            asserted_digest = item["authorization_digest"]
            if (
                not isinstance(asserted_digest, str)
                or _DIGEST_PATTERN.fullmatch(asserted_digest) is None
            ):
                raise _InvalidMissionValue
            proposal = cls(
                _parse_mission_version(item["mission_version"]),
                _parse_authorization(item["authorization_envelope"]),
                _mapping(item["leader_provenance"]),
            )
            if not hmac.compare_digest(
                proposal.authorization_digest, asserted_digest
            ):
                raise _InvalidMissionValue
            return proposal
        except (
            KeyError,
            OverflowError,
            RecursionError,
            TypeError,
            UnicodeEncodeError,
            ValueError,
            _InvalidMissionValue,
        ):
            raise ValueError("mission proposal RPC invalid") from None


def _event_id(command: CommandEnvelope, action: str) -> str:
    identity = f"{command.command_id}\0{action}".encode("utf-8")
    return f"evt_{action}_{hashlib.sha256(identity).hexdigest()[:24]}"


def _valid_identifier(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
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


def _archived_rows(
    snapshot: ProjectMutationSnapshot, table: str
) -> tuple[Mapping[str, _FrozenJson], ...]:
    rows = snapshot.archived_lineage.get(table, ())
    return cast(tuple[Mapping[str, _FrozenJson], ...], rows)


def _event_ledger_rows(
    snapshot: ProjectMutationSnapshot, view: str
) -> tuple[Mapping[str, _FrozenJson], ...]:
    rows = snapshot.event_ledger.get(view, ())
    return cast(tuple[Mapping[str, _FrozenJson], ...], rows)


def _identities(
    snapshot: ProjectMutationSnapshot, table: str
) -> tuple[Mapping[str, _FrozenJson], ...]:
    rows = snapshot.identities.get(table, ())
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
    if any(
        _row_value(row, "mission_id") == mission_version.mission_id
        for row in _identities(snapshot, "missions")
    ):
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
        proposal.proposal_provenance_dict(), maximum=_MAX_PROVENANCE_BYTES
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
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeEncodeError,
        ValueError,
        _InvalidMissionValue,
    ):
        raise ValueError("stored mission specification invalid") from None


def _decode_stored_provenance(value: object) -> tuple[dict[str, object], str]:
    try:
        if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_PROVENANCE_BYTES:
            raise _InvalidMissionValue
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or set(parsed) != {
            "leader_provenance",
            "leader_provenance_hash",
        }:
            raise _InvalidMissionValue
        if _canonical_json(parsed, maximum=_MAX_PROVENANCE_BYTES) != value:
            raise _InvalidMissionValue
        provenance = parsed["leader_provenance"]
        persisted_hash = parsed["leader_provenance_hash"]
        if (
            not isinstance(provenance, dict)
            or not provenance
            or not isinstance(persisted_hash, str)
            or _DIGEST_PATTERN.fullmatch(persisted_hash) is None
        ):
            raise _InvalidMissionValue
        canonical = _canonical_json(provenance, maximum=_MAX_PROVENANCE_BYTES)
        recomputed = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if recomputed != persisted_hash:
            raise _InvalidMissionValue
        return provenance, persisted_hash
    except (
        json.JSONDecodeError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeEncodeError,
        ValueError,
        _InvalidMissionValue,
    ):
        raise ValueError("stored mission provenance invalid") from None


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
    _decode_stored_provenance(_row_value(stored, "proposal_provenance_json"))
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
    existing_task_ids = {
        cast(str, _row_value(row, "task_id"))
        for row in _identities(snapshot, "tasks")
    }
    if any(
        task.task_id in existing_task_ids for task in confirmed.mission_version.tasks
    ):
        raise ValueError("task identity conflict")

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
        if any(
            _row_value(row, "mission_id") == mission_id
            for row in _identities(snapshot, "missions")
        ):
            raise ValueError("mission terminal")
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
        or not _valid_identifier(actor.get("id"))
        or not payload_matches
    ):
        raise MutationValidationError("mission command invalid")


def _event_payload(event: DomainEvent) -> dict[str, object]:
    if type(event) is not DomainEvent:
        raise MutationValidationError("mission event invalid")
    payload = event.to_dict()["payload"]
    if not isinstance(payload, dict):
        raise MutationValidationError("mission event invalid")
    return payload


def _validate_internal_event(
    event: DomainEvent,
    *,
    kind: str,
    payload: Mapping[str, object],
) -> None:
    try:
        matches = _canonical_json(
            _event_payload(event), maximum=_MAX_SPECIFICATION_BYTES
        ) == _canonical_json(dict(payload), maximum=_MAX_SPECIFICATION_BYTES)
    except _InvalidMissionValue:
        matches = False
    if (
        type(event) is not DomainEvent
        or event.trigger_kind != "internal_trigger"
        or event.kind != kind
        or not matches
    ):
        raise MutationValidationError("mission event invalid")


def _validate_adapter_event(event: DomainEvent, *, kinds: frozenset[str]) -> None:
    if type(event) is not DomainEvent or event.trigger_kind != "adapter_event":
        raise MutationValidationError("mission event invalid")
    try:
        lineage = event.provenance.to_dict()
        if event.kind not in kinds:
            raise _InvalidMissionValue
        expected = adapter_event_integrity_hash(
            event_id=event.event_id,
            kind=event.kind,
            adapter_event_id=cast(str, lineage["adapter_event_id"]),
            mission_id=cast(str, lineage["mission_id"]),
            mission_version=cast(str, lineage["mission_version"]),
            task_id=cast(str, lineage["task_id"]),
            attempt_id=cast(str, lineage["attempt_id"]),
            session_id=cast(str, lineage["session_id"]),
            sequence=cast(int, lineage["sequence"]),
            payload=_event_payload(event),
            created_at=event.created_at,
        )
        actual = lineage["integrity_hash"]
        if not isinstance(actual, str) or not hmac.compare_digest(expected, actual):
            raise _InvalidMissionValue
    except (
        KeyError,
        TypeError,
        ValueError,
        _InvalidMissionValue,
    ):
        raise MutationValidationError("mission event invalid") from None


def _one_row(
    snapshot: ProjectMutationSnapshot,
    table: str,
    **matches: object,
) -> Mapping[str, _FrozenJson]:
    rows = [
        row
        for row in _rows(snapshot, table)
        if all(_row_value(row, key) == value for key, value in matches.items())
    ]
    if len(rows) != 1:
        raise ValueError("mission lineage invalid")
    return rows[0]


def _one_archived_row(
    snapshot: ProjectMutationSnapshot,
    table: str,
    **matches: object,
) -> Mapping[str, _FrozenJson]:
    rows = [
        row
        for row in _archived_rows(snapshot, table)
        if all(_row_value(row, key) == value for key, value in matches.items())
    ]
    if len(rows) != 1:
        raise ValueError("mission lineage invalid")
    return rows[0]


def _decode_task_row(row: Mapping[str, _FrozenJson]) -> TaskSpec:
    value = _row_value(row, "specification_json")
    try:
        if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_SPECIFICATION_BYTES:
            raise _InvalidMissionValue
        parsed = json.loads(value)
        if _canonical_json(parsed, maximum=_MAX_SPECIFICATION_BYTES) != value:
            raise _InvalidMissionValue
        return _parse_task(parsed)
    except (
        json.JSONDecodeError,
        TypeError,
        ValueError,
        _InvalidMissionValue,
    ):
        raise ValueError("stored task specification invalid") from None


def _confirmed_context(
    snapshot: ProjectMutationSnapshot,
    mission_id: str,
    version: int,
    *,
    allowed_statuses: frozenset[str],
) -> tuple[Mapping[str, _FrozenJson], ConfirmedMissionVersion]:
    mission = _one_row(snapshot, "missions", mission_id=mission_id)
    if (
        _row_value(mission, "current_version") != version
        or _row_value(mission, "status") not in allowed_statuses
    ):
        raise ValueError("mission lineage invalid")
    version_row = _one_row(
        snapshot, "mission_versions", mission_id=mission_id, version=version
    )
    if (
        _row_value(version_row, "confirmed_revision") is None
        or not isinstance(_row_value(version_row, "authorization_digest"), str)
    ):
        raise ValueError("mission authorization invalid")
    confirmed = _decode_stored_specification(
        _row_value(version_row, "specification_json")
    )
    digest = authorization_digest(
        confirmed.mission_version, confirmed.authorization_envelope
    )
    if (
        confirmed.authorization_digest != digest
        or _row_value(version_row, "authorization_digest") != digest
        or confirmed.mission_version.mission_id != mission_id
        or confirmed.mission_version.version != version
    ):
        raise ValueError("mission authorization invalid")
    return mission, confirmed


def _task_context(
    snapshot: ProjectMutationSnapshot,
    mission_id: str,
    version: int,
    task_id: str,
) -> tuple[Mapping[str, _FrozenJson], TaskSpec]:
    row = _one_row(snapshot, "tasks", task_id=task_id)
    if (
        _row_value(row, "mission_id") != mission_id
        or _row_value(row, "mission_version") != version
    ):
        raise ValueError("mission lineage invalid")
    specification = _decode_task_row(row)
    if specification.task_id != task_id:
        raise ValueError("stored task specification invalid")
    return row, specification


def _event_lineage(event: DomainEvent) -> dict[str, object]:
    value = event.provenance.to_dict()
    return cast(dict[str, object], value)


def _release_ready_decision(
    snapshot: ProjectMutationSnapshot,
    event: DomainEvent,
) -> MutationDecision:
    payload = _event_payload(event)
    mission_id = payload.get("mission_id")
    if not _valid_identifier(mission_id):
        raise MutationValidationError("mission event invalid")
    mission = _one_row(snapshot, "missions", mission_id=mission_id)
    version = _row_value(mission, "current_version")
    if not _valid_positive_version(version):
        raise ValueError("mission lineage invalid")
    _, confirmed = _confirmed_context(
        snapshot,
        cast(str, mission_id),
        cast(int, version),
        allowed_statuses=frozenset({"confirmed", "running"}),
    )
    task_rows = tuple(
        row
        for row in _rows(snapshot, "tasks")
        if _row_value(row, "mission_id") == mission_id
        and _row_value(row, "mission_version") == version
    )
    tasks_by_id = {item.task_id: item for item in confirmed.mission_version.tasks}
    if len(task_rows) != len(tasks_by_id):
        raise ValueError("mission lineage invalid")
    states = {
        cast(str, _row_value(row, "task_id")): cast(str, _row_value(row, "status"))
        for row in task_rows
    }
    handoffs = tuple(
        (
            cast(str, _row_value(row, "source_task_id")),
            cast(str, _row_value(row, "destination_task_id")),
        )
        for row in _rows(snapshot, "handoffs")
        if _row_value(row, "mission_id") == mission_id
        and _row_value(row, "status") == "accepted"
    )
    ready = decide_release_ready_tasks(
        confirmed.mission_version.tasks, states, handoffs
    )
    next_revision = snapshot.revision + 1
    return MutationDecision(
        changes=tuple(
            EntityChange.update(
                "tasks",
                {"status": "ready", "updated_revision": next_revision},
                where={"task_id": task_id},
            )
            for task_id in ready
        ),
        events=(event,),
        result={"mission_id": mission_id, "ready_task_ids": list(ready)},
    )


def _attempt_budget_record(
    row: Mapping[str, _FrozenJson],
) -> tuple[int, str]:
    value = _row_value(row, "budget_json")
    try:
        parsed = json.loads(cast(str, value))
        if (
            not isinstance(value, str)
            or not isinstance(parsed, dict)
            or set(parsed) != {"budget_units", "operation_id"}
            or not isinstance(parsed["budget_units"], int)
            or isinstance(parsed["budget_units"], bool)
            or parsed["budget_units"] < 1
            or not _valid_identifier(parsed["operation_id"])
            or _canonical_json(parsed, maximum=_MAX_PROVENANCE_BYTES) != value
        ):
            raise _InvalidMissionValue
        return cast(int, parsed["budget_units"]), cast(str, parsed["operation_id"])
    except (json.JSONDecodeError, TypeError, ValueError, _InvalidMissionValue):
        raise ValueError("stored attempt budget invalid") from None


def _attempt_budget(row: Mapping[str, _FrozenJson]) -> int:
    return _attempt_budget_record(row)[0]


def _attempt_operation(row: Mapping[str, _FrozenJson]) -> str:
    return _attempt_budget_record(row)[1]


def _start_attempt_decision(
    snapshot: ProjectMutationSnapshot,
    event: DomainEvent,
    request: StartAttemptRequest,
) -> MutationDecision:
    mission, confirmed = _confirmed_context(
        snapshot,
        request.mission_id,
        request.mission_version,
        allowed_statuses=frozenset({"confirmed", "running"}),
    )
    task_row, task = _task_context(
        snapshot, request.mission_id, request.mission_version, request.task_id
    )
    if (
        request.agent_id not in confirmed.authorization_envelope.allowed_agents
        or task.role not in confirmed.authorization_envelope.allowed_roles
        or request.route_position >= len(confirmed.authorization_envelope.ordered_routes)
        or confirmed.authorization_envelope.ordered_routes[request.route_position]
        != request.agent_id
    ):
        raise ValueError("attempt authorization invalid")
    if any(
        _row_value(row, "attempt_id") == request.attempt_id
        for row in _identities(snapshot, "attempts")
    ) or any(
        _row_value(row, "session_id") == request.session_id
        for row in _identities(snapshot, "sessions")
    ):
        raise ValueError("attempt identity conflict")
    prior = tuple(
        sorted(
            cast(int, _row_value(row, "attempt_number"))
            for row in _rows(snapshot, "attempts")
            if _row_value(row, "task_id") == request.task_id
        )
    )
    decision = decide_start_attempt(task, cast(str, _row_value(task_row, "status")), prior)
    mission_task_ids = {
        cast(str, _row_value(row, "task_id"))
        for row in _rows(snapshot, "tasks")
        if _row_value(row, "mission_id") == request.mission_id
        and _row_value(row, "mission_version") == request.mission_version
    }
    mission_attempts = tuple(
        row
        for row in _rows(snapshot, "attempts")
        if _row_value(row, "task_id") in mission_task_ids
    )
    active_attempt_states = frozenset(
        {"running", "awaiting_verification", "paused", "recovering"}
    )
    active_task_ids = {
        cast(str, _row_value(row, "task_id"))
        for row in mission_attempts
        if _row_value(row, "status") in active_attempt_states
    }
    if len(active_task_ids) >= confirmed.mission_version.max_parallel_tasks:
        raise ValueError("mission parallel limit reached")
    task_rows_by_id = {
        cast(str, _row_value(row, "task_id")): row
        for row in _rows(snapshot, "tasks")
        if _row_value(row, "mission_id") == request.mission_id
        and _row_value(row, "mission_version") == request.mission_version
    }
    active_keys: set[str] = set()
    for active_task_id in active_task_ids:
        active_row = task_rows_by_id.get(active_task_id)
        if active_row is None:
            raise ValueError("mission lineage invalid")
        active_specification = _decode_task_row(active_row)
        if active_specification.task_id != active_task_id:
            raise ValueError("stored task specification invalid")
        active_keys.update(active_specification.concurrency_keys)
    if active_keys.intersection(task.concurrency_keys):
        raise ValueError("task concurrency conflict")
    task_attempts = tuple(
        row
        for row in mission_attempts
        if _row_value(row, "task_id") == request.task_id
    )
    task_budget_used = sum(_attempt_budget(row) for row in task_attempts)
    mission_budget_used = sum(_attempt_budget(row) for row in mission_attempts)
    mission_budget_limit = min(
        confirmed.mission_version.budget_units,
        confirmed.authorization_envelope.budget_units,
    )
    if (
        task_budget_used + request.budget_units > task.budget_units
        or mission_budget_used + request.budget_units > mission_budget_limit
    ):
        raise ValueError("attempt budget exhausted")
    retry_count = sum(
        1
        for row in mission_attempts
        if cast(int, _row_value(row, "attempt_number")) > 1
    ) + (1 if decision.attempt_number > 1 else 0)
    if (
        len(mission_attempts) + 1
        > confirmed.authorization_envelope.max_attempts
        or retry_count > confirmed.authorization_envelope.max_retries
    ):
        raise ValueError("attempt authorization invalid")
    next_revision = snapshot.revision + 1
    changes: list[EntityChange] = [
        EntityChange.update(
            "tasks",
            {"status": decision.task_state.value, "updated_revision": next_revision},
            where={"task_id": request.task_id},
        ),
        EntityChange.insert(
            "attempts",
            {
                "attempt_id": request.attempt_id,
                "task_id": request.task_id,
                "attempt_number": decision.attempt_number,
                "status": decision.attempt_state,
                "route_position": request.route_position,
                "budget_json": _canonical_json(
                    {
                        "budget_units": request.budget_units,
                        "operation_id": request.operation_id,
                    },
                    maximum=_MAX_PROVENANCE_BYTES,
                ),
                "started_revision": next_revision,
                "terminal_revision": None,
            },
        ),
        EntityChange.insert(
            "sessions",
            {
                "session_id": request.session_id,
                "attempt_id": request.attempt_id,
                "agent_id": request.agent_id,
                "model_id": request.model_id,
                "transport": request.transport,
                "status": "running",
                "last_sequence": 0,
                "lease_json": None,
                "reconciliation_json": None,
            },
        ),
    ]
    if _row_value(mission, "status") == "confirmed":
        changes.append(
            EntityChange.update(
                "missions",
                {"status": "running", "updated_revision": next_revision},
                where={"mission_id": request.mission_id},
            )
        )
    return MutationDecision(
        changes=tuple(changes),
        events=(event,),
        result={
            "mission_id": request.mission_id,
            "task_id": request.task_id,
            "attempt_id": request.attempt_id,
            "session_id": request.session_id,
            "attempt_number": decision.attempt_number,
        },
    )


@dataclass(frozen=True, slots=True)
class _AdapterContext:
    lineage: dict[str, object]
    task_row: Mapping[str, _FrozenJson]
    task: TaskSpec | None
    attempt: Mapping[str, _FrozenJson]
    session: Mapping[str, _FrozenJson]
    confirmed: ConfirmedMissionVersion | None
    archived: bool


def _adapter_context(
    snapshot: ProjectMutationSnapshot,
    event: DomainEvent,
) -> _AdapterContext:
    lineage = _event_lineage(event)
    try:
        raw_version = lineage["mission_version"]
        version = int(cast(str, raw_version))
    except (KeyError, TypeError, ValueError):
        raise ValueError("adapter lineage invalid") from None
    if str(version) != raw_version or not _valid_positive_version(version):
        raise ValueError("adapter lineage invalid")
    mission_id = lineage.get("mission_id")
    task_id = lineage.get("task_id")
    attempt_id = lineage.get("attempt_id")
    session_id = lineage.get("session_id")
    if not all(
        _valid_identifier(item)
        for item in (mission_id, task_id, attempt_id, session_id)
    ):
        raise ValueError("adapter lineage invalid")
    active_missions = tuple(
        row
        for row in _rows(snapshot, "missions")
        if _row_value(row, "mission_id") == mission_id
    )
    if active_missions:
        if len(active_missions) != 1:
            raise ValueError("adapter lineage invalid")
        _, confirmed = _confirmed_context(
            snapshot,
            cast(str, mission_id),
            version,
            allowed_statuses=frozenset({"confirmed", "running", "paused"}),
        )
        task_row, task = _task_context(
            snapshot, cast(str, mission_id), version, cast(str, task_id)
        )
        attempt = _one_row(snapshot, "attempts", attempt_id=attempt_id)
        session = _one_row(snapshot, "sessions", session_id=session_id)
        archived = False
    else:
        mission = _one_archived_row(
            snapshot, "missions", mission_id=cast(str, mission_id)
        )
        if (
            _row_value(mission, "current_version") != version
            or _row_value(mission, "status") not in _TERMINAL_MISSION_STATES
        ):
            raise ValueError("adapter lineage invalid")
        version_row = _one_archived_row(
            snapshot,
            "mission_versions",
            mission_id=cast(str, mission_id),
            version=version,
        )
        digest = _row_value(version_row, "authorization_digest")
        if (
            _row_value(version_row, "confirmed_revision") is None
            or not isinstance(digest, str)
            or _DIGEST_PATTERN.fullmatch(digest) is None
        ):
            raise ValueError("mission authorization invalid")
        task_row = _one_archived_row(
            snapshot, "tasks", task_id=cast(str, task_id)
        )
        if (
            _row_value(task_row, "mission_id") != mission_id
            or _row_value(task_row, "mission_version") != version
        ):
            raise ValueError("adapter lineage invalid")
        attempt = _one_archived_row(
            snapshot, "attempts", attempt_id=cast(str, attempt_id)
        )
        session = _one_archived_row(
            snapshot, "sessions", session_id=cast(str, session_id)
        )
        task = None
        confirmed = None
        archived = True
    if (
        _row_value(attempt, "task_id") != task_id
        or _row_value(session, "attempt_id") != attempt_id
    ):
        raise ValueError("adapter lineage invalid")
    sequence = lineage.get("sequence")
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence != cast(int, _row_value(session, "last_sequence")) + 1
    ):
        raise ValueError("adapter sequence conflict")
    return _AdapterContext(
        lineage, task_row, task, attempt, session, confirmed, archived
    )


def _runtime_facts(event: DomainEvent) -> tuple[RuntimeFact, ...]:
    payload = _event_payload(event)
    if event.kind == "failed":
        effect_status = payload.get("effect_status")
        expected_fields = (
            {"reason", "effect_status", "operation_id", "proof_evidence_id"}
            if effect_status == "proven_no_effect"
            else {"reason", "effect_status", "operation_id"}
        )
        if set(payload) != expected_fields:
            raise MutationValidationError("worker event payload invalid")
        reason = payload["reason"]
        if not _valid_identifier(reason) or effect_status not in {
            "proven_no_effect",
            "ambiguous_effect",
            "known_effect",
        }:
            raise MutationValidationError("worker event payload invalid")
        if effect_status == "proven_no_effect" and not _valid_identifier(
            payload.get("proof_evidence_id")
        ):
            raise MutationValidationError("worker event payload invalid")
        if not _valid_identifier(payload.get("operation_id")):
            raise MutationValidationError("worker event payload invalid")
        fact_kind = {
            "proven_no_effect": "proven_no_effect",
            "ambiguous_effect": "ambiguous_effect",
            "known_effect": "terminal_failed",
        }[cast(str, effect_status)]
        scope = "mission" if effect_status == "ambiguous_effect" else "task"
        return (RuntimeFact(scope, fact_kind, cast(str, reason)),)
    if event.kind not in {
        "ambiguous_effect",
        "permission_conflict",
        "permission_requested",
        "cancelled",
        "session_takeover",
    }:
        return ()
    reason = payload.get("reason", event.kind.replace("_", " "))
    if set(payload).difference({"reason"}) or not _valid_identifier(reason):
        raise MutationValidationError("worker event payload invalid")
    kind = {
        "cancelled": "terminal_cancelled",
        "permission_requested": "task_local_pause",
        "session_takeover": "session_takeover",
    }.get(event.kind, event.kind)
    scope = {
        "ambiguous_effect": "mission",
        "permission_conflict": "mission",
        "session_takeover": "session",
    }.get(event.kind, "task")
    return (RuntimeFact(scope, kind, cast(str, reason)),)


def _validate_effect_proof(
    snapshot: ProjectMutationSnapshot,
    context: _AdapterContext,
    proof_evidence_id: object,
    operation_id: object,
    failure_sequence: object,
) -> None:
    if (
        not _valid_identifier(proof_evidence_id)
        or not _valid_identifier(operation_id)
        or not isinstance(failure_sequence, int)
        or isinstance(failure_sequence, bool)
        or _attempt_operation(context.attempt) != operation_id
    ):
        raise ValueError("effect proof invalid")
    matches = tuple(
        row
        for row in _rows(snapshot, "evidence")
        if _row_value(row, "evidence_id") == proof_evidence_id
    )
    if len(matches) != 1:
        raise ValueError("effect proof invalid")
    row = matches[0]
    if (
        _row_value(row, "task_id") != context.lineage["task_id"]
        or _row_value(row, "attempt_id") != context.lineage["attempt_id"]
        or _row_value(row, "kind") != "effect_proof"
    ):
        raise ValueError("effect proof invalid")
    value = _row_value(row, "summary_json")
    try:
        parsed = json.loads(cast(str, value))
        if (
            not isinstance(value, str)
            or not isinstance(parsed, dict)
            or set(parsed)
            != {
                "criterion",
                "fact",
                "reason",
                "operation_id",
                "source_session_id",
                "source_sequence",
            }
            or parsed["fact"] != "proven_no_effect"
            or parsed["operation_id"] != operation_id
            or parsed["source_session_id"] != context.lineage["session_id"]
            or parsed["source_sequence"] != failure_sequence - 1
            or _canonical_json(parsed, maximum=_MAX_PROVENANCE_BYTES) != value
        ):
            raise _InvalidMissionValue
        decide_record_evidence(
            cast(str, parsed["criterion"]),
            cast(str, parsed["fact"]),
            cast(str, parsed["reason"]),
        )
        source_rows = _event_ledger_rows(snapshot, "source_events")
        if len(source_rows) != 1:
            raise _InvalidMissionValue
        source = source_rows[0]
        source_payload = _thaw_json(source["payload"])
        expected_source_payload = {
            "evidence_id": proof_evidence_id,
            "kind": "effect_proof",
            "criterion": parsed["criterion"],
            "fact": parsed["fact"],
            "operation_id": operation_id,
            "reason": parsed["reason"],
        }
        source_integrity = source["integrity_hash"]
        evidence_integrity = _row_value(row, "integrity_hash")
        if (
            source["kind"] != "evidence"
            or source["mission_id"] != context.lineage["mission_id"]
            or source["mission_version"] != context.lineage["mission_version"]
            or source["task_id"] != context.lineage["task_id"]
            or source["attempt_id"] != context.lineage["attempt_id"]
            or source["session_id"] != context.lineage["session_id"]
            or source["sequence"] != failure_sequence - 1
            or source_payload != expected_source_payload
            or not isinstance(source_integrity, str)
            or not isinstance(evidence_integrity, str)
        ):
            raise _InvalidMissionValue
        expected_integrity = adapter_event_integrity_hash(
            event_id=cast(str, source["event_id"]),
            kind=cast(str, source["kind"]),
            adapter_event_id=cast(str, source["adapter_event_id"]),
            mission_id=cast(str, source["mission_id"]),
            mission_version=cast(str, source["mission_version"]),
            task_id=cast(str, source["task_id"]),
            attempt_id=cast(str, source["attempt_id"]),
            session_id=cast(str, source["session_id"]),
            sequence=cast(int, source["sequence"]),
            payload=source_payload,
            created_at=cast(str, source["created_at"]),
        )
        if not hmac.compare_digest(
            expected_integrity, source_integrity
        ) or not hmac.compare_digest(source_integrity, evidence_integrity):
            raise _InvalidMissionValue
    except (
        KeyError,
        json.JSONDecodeError,
        MutationValidationError,
        TypeError,
        ValueError,
        _InvalidMissionValue,
    ):
        raise ValueError("effect proof invalid") from None


def _stored_reconciliation(
    snapshot: ProjectMutationSnapshot,
    session: Mapping[str, _FrozenJson],
) -> tuple[list[dict[str, object]], int, int]:
    reconciliation = _row_value(session, "reconciliation_json")
    try:
        ledger_rows = _event_ledger_rows(snapshot, "reconciliations")
        if len(ledger_rows) != 1:
            raise _InvalidMissionValue
        ledger = ledger_rows[0]
        expected = {
            "active_blockers": _thaw_json(ledger["active_blockers"]),
            "fact_count": ledger["fact_count"],
            "latest_sequence": ledger["latest_sequence"],
        }
        session_id = _row_value(session, "session_id")
        session_sequence = _row_value(session, "last_sequence")
        if reconciliation is None:
            stored = {
                "active_blockers": [],
                "fact_count": 0,
                "latest_sequence": 0,
            }
        else:
            stored = json.loads(cast(str, reconciliation))
        if (
            reconciliation is not None
            and not isinstance(reconciliation, str)
            or not isinstance(stored, dict)
            or set(stored) != {"active_blockers", "fact_count", "latest_sequence"}
            or not isinstance(stored["active_blockers"], list)
            or len(stored["active_blockers"]) > _MAX_RECONCILIATION_BLOCKERS
            or not isinstance(stored["fact_count"], int)
            or isinstance(stored["fact_count"], bool)
            or not 0 <= stored["fact_count"] <= _MAX_SIGNED_64
            or not isinstance(stored["latest_sequence"], int)
            or isinstance(stored["latest_sequence"], bool)
            or not 0 <= stored["latest_sequence"] <= _MAX_SIGNED_64
            or any(
                not isinstance(item, dict)
                or set(item) != {"scope", "kind", "sequence"}
                or item.get("kind") not in _RECONCILIATION_BLOCKER_SCOPES
                or item.get("scope")
                != _RECONCILIATION_BLOCKER_SCOPES.get(cast(str, item.get("kind")))
                or not isinstance(item.get("sequence"), int)
                or isinstance(item.get("sequence"), bool)
                or not 1 <= cast(int, item.get("sequence")) <= stored["latest_sequence"]
                for item in stored["active_blockers"]
            )
            or stored["active_blockers"]
            != sorted(
                stored["active_blockers"],
                key=lambda item: (item["scope"], item["kind"]),
            )
            or len(
                {
                    (item["scope"], item["kind"])
                    for item in stored["active_blockers"]
                }
            )
            != len(stored["active_blockers"])
            or stored["fact_count"] < len(stored["active_blockers"])
            or stored["latest_sequence"] != session_sequence
            or ledger["session_id"] != session_id
            or stored != expected
            or reconciliation is not None
            and _canonical_json(stored, maximum=_MAX_PROVENANCE_BYTES)
            != reconciliation
        ):
            raise _InvalidMissionValue
        return (
            cast(list[dict[str, object]], stored["active_blockers"]),
            cast(int, stored["fact_count"]),
            cast(int, stored["latest_sequence"]),
        )
    except (json.JSONDecodeError, TypeError, ValueError, _InvalidMissionValue):
        raise ValueError("stored reconciliation invalid") from None


def _recovery_context(
    snapshot: ProjectMutationSnapshot,
    context: _AdapterContext,
) -> RecoveryContext:
    if context.confirmed is None or context.task is None or context.archived:
        raise ValueError("mission lineage invalid")
    task_ids = {
        item.task_id for item in context.confirmed.mission_version.tasks
    }
    attempts = tuple(
        row
        for row in _rows(snapshot, "attempts")
        if _row_value(row, "task_id") in task_ids
    )
    task_attempts = tuple(
        row
        for row in attempts
        if _row_value(row, "task_id") == context.task.task_id
    )
    recovery_count = sum(
        1 for attempt in attempts if _row_value(attempt, "status") == "failed"
    )
    envelope = context.confirmed.authorization_envelope
    return RecoveryContext(
        attempt_number=cast(int, _row_value(context.attempt, "attempt_number")),
        task_retry_limit=context.task.retry_limit,
        mission_attempt_count=len(attempts),
        mission_max_attempts=envelope.max_attempts,
        mission_retry_count=sum(
            1
            for row in attempts
            if cast(int, _row_value(row, "attempt_number")) > 1
        ),
        mission_max_retries=envelope.max_retries,
        mission_recovery_count=recovery_count,
        mission_max_recoveries=envelope.max_recoveries,
        task_budget_used=sum(_attempt_budget(row) for row in task_attempts),
        task_budget_limit=context.task.budget_units,
        mission_budget_used=sum(_attempt_budget(row) for row in attempts),
        mission_budget_limit=min(
            context.confirmed.mission_version.budget_units,
            envelope.budget_units,
        ),
    )


def _worker_event_decision(
    snapshot: ProjectMutationSnapshot,
    event: DomainEvent,
) -> MutationDecision:
    context = _adapter_context(snapshot, event)
    lineage = context.lineage
    task_row = context.task_row
    attempt = context.attempt
    session = context.session
    new_facts = _runtime_facts(event)
    current_task_state = cast(str, _row_value(task_row, "status"))
    current_attempt_state = cast(str, _row_value(attempt, "status"))
    if context.archived or current_task_state in {
        "completed",
        "failed",
        "cancelled",
    } or current_attempt_state in {"completed", "failed", "cancelled"}:
        return MutationDecision(
            changes=(
                EntityChange.update(
                    "sessions",
                    {"last_sequence": lineage["sequence"]},
                    where={"session_id": lineage["session_id"]},
                ),
            ),
            events=(event,),
            result={
                "task_id": lineage["task_id"],
                "attempt_id": lineage["attempt_id"],
                "session_id": lineage["session_id"],
                "task_state": current_task_state,
                "observation_only": True,
            },
        )
    stored_blockers, fact_count, _latest_sequence = _stored_reconciliation(
        snapshot, session
    )
    try:
        facts = tuple(
            RuntimeFact(
                cast(str, item["scope"]),
                cast(str, item["kind"]),
                f"persisted blocker: {item['kind']}",
            )
            for item in stored_blockers
        ) + new_facts
    except (KeyError, TypeError, ValueError):
        raise ValueError("stored reconciliation invalid") from None
    effect_status = (
        cast(str, _event_payload(event)["effect_status"])
        if event.kind == "failed"
        else None
    )
    if effect_status == "proven_no_effect":
        _validate_effect_proof(
            snapshot,
            context,
            _event_payload(event).get("proof_evidence_id"),
            _event_payload(event).get("operation_id"),
            lineage["sequence"],
        )
    elif event.kind == "failed" and _attempt_operation(attempt) != _event_payload(
        event
    ).get("operation_id"):
        raise ValueError("attempt operation invalid")
    decision = decide_record_worker_event(
        current_task_state,
        current_attempt_state,
        event.kind,
        facts=facts,
        effect_status=effect_status,
        recovery=(
            _recovery_context(snapshot, context)
            if event.kind == "failed"
            else None
        ),
    )
    next_revision = snapshot.revision + 1
    sequence = cast(int, lineage["sequence"])
    changes: list[EntityChange] = []
    if fact_count > _MAX_SIGNED_64 - len(new_facts):
        raise ValueError("stored reconciliation invalid")
    blockers_by_key = {
        (cast(str, item["scope"]), cast(str, item["kind"])): dict(item)
        for item in stored_blockers
    }
    for fact in new_facts:
        expected_scope = _RECONCILIATION_BLOCKER_SCOPES.get(fact.kind)
        if expected_scope is not None:
            if fact.scope != expected_scope:
                raise ValueError("worker event payload invalid")
            blockers_by_key[(fact.scope, fact.kind)] = {
                "scope": fact.scope,
                "kind": fact.kind,
                "sequence": sequence,
            }
    active_blockers = sorted(
        blockers_by_key.values(), key=lambda item: (item["scope"], item["kind"])
    )
    if len(active_blockers) > _MAX_RECONCILIATION_BLOCKERS:
        raise ValueError("stored reconciliation invalid")
    session_values: dict[str, object] = {
        "status": decision.attempt_state,
        "last_sequence": sequence,
        "reconciliation_json": _canonical_json(
            {
                "active_blockers": active_blockers,
                "fact_count": fact_count + len(new_facts),
                "latest_sequence": sequence,
            },
            maximum=_MAX_PROVENANCE_BYTES,
        ),
    }
    changes.append(
        EntityChange.update(
            "sessions", session_values, where={"session_id": lineage["session_id"]}
        )
    )
    if _row_value(task_row, "status") != decision.task_state.value:
        changes.append(
            EntityChange.update(
                "tasks",
                {"status": decision.task_state.value, "updated_revision": next_revision},
                where={"task_id": lineage["task_id"]},
            )
        )
    attempt_values: dict[str, object] = {"status": decision.attempt_state}
    if decision.attempt_state in {"completed", "failed", "cancelled"}:
        attempt_values["terminal_revision"] = next_revision
    if _row_value(attempt, "status") != decision.attempt_state:
        changes.append(
            EntityChange.update(
                "attempts", attempt_values, where={"attempt_id": lineage["attempt_id"]}
            )
        )
    mission = _one_row(snapshot, "missions", mission_id=lineage["mission_id"])
    if _row_value(mission, "status") != decision.mission_state:
        changes.append(
            EntityChange.update(
                "missions",
                {"status": decision.mission_state, "updated_revision": next_revision},
                where={"mission_id": lineage["mission_id"]},
            )
        )
    return MutationDecision(
        changes=tuple(changes),
        events=(event,),
        result={
            "task_id": lineage["task_id"],
            "attempt_id": lineage["attempt_id"],
            "session_id": lineage["session_id"],
            "task_state": decision.task_state.value,
            "mission_state": decision.mission_state,
            "effective_scope": decision.effective_scope,
            "dispatch_allowed": decision.dispatch_allowed,
            "automation_allowed": decision.automation_allowed,
            "recovery_allowed": decision.recovery_allowed,
        },
    )


def _record_evidence_decision(
    snapshot: ProjectMutationSnapshot,
    event: DomainEvent,
) -> MutationDecision:
    context = _adapter_context(snapshot, event)
    lineage = context.lineage
    task_row = context.task_row
    task = context.task
    attempt = context.attempt
    payload = _event_payload(event)
    kind = payload.get("kind")
    expected_fields = {"evidence_id", "kind", "criterion", "fact", "reason"}
    if kind == "effect_proof":
        expected_fields.add("operation_id")
    if set(payload) != expected_fields:
        raise MutationValidationError("evidence payload invalid")
    evidence_id = payload["evidence_id"]
    if not _valid_identifier(evidence_id) or not _valid_identifier(kind):
        raise MutationValidationError("evidence payload invalid")
    if (
        kind == "effect_proof"
        and payload.get("fact") != "proven_no_effect"
    ) or (
        kind != "effect_proof"
        and payload.get("fact") not in {"check_passed", "check_failed"}
    ):
        raise MutationValidationError("evidence payload invalid")
    if kind == "effect_proof" and not _valid_identifier(payload.get("operation_id")):
        raise MutationValidationError("evidence payload invalid")
    evidence = decide_record_evidence(
        cast(str, payload["criterion"]),
        cast(str, payload["fact"]),
        cast(str, payload["reason"]),
    )
    if (
        context.archived
        or _row_value(task_row, "status") in {"completed", "failed", "cancelled"}
        or _row_value(attempt, "status") in {"completed", "failed", "cancelled"}
    ):
        return MutationDecision(
            changes=(
                EntityChange.update(
                    "sessions",
                    {"last_sequence": lineage["sequence"]},
                    where={"session_id": lineage["session_id"]},
                ),
            ),
            events=(event,),
            result={
                "evidence_id": evidence_id,
                "task_id": lineage["task_id"],
                "observation_only": True,
            },
        )
    if (
        task is None
        or kind != "effect_proof"
        and evidence.criterion not in task.acceptance_criteria
    ):
        raise ValueError("evidence criterion invalid")
    if any(
        _row_value(row, "evidence_id") == evidence_id
        for row in _identities(snapshot, "evidence")
    ):
        raise ValueError("evidence identity conflict")
    stored_blockers, fact_count, _latest_sequence = _stored_reconciliation(
        snapshot, context.session
    )
    next_revision = snapshot.revision + 1
    integrity_hash = lineage["integrity_hash"]
    summary: dict[str, object] = {
        "criterion": evidence.criterion,
        "fact": evidence.fact,
        "reason": evidence.reason,
    }
    if kind == "effect_proof":
        summary.update(
            {
                "operation_id": payload["operation_id"],
                "source_session_id": lineage["session_id"],
                "source_sequence": lineage["sequence"],
            }
        )
    return MutationDecision(
        changes=(
            EntityChange.update(
                "sessions",
                {
                    "last_sequence": lineage["sequence"],
                    "reconciliation_json": _canonical_json(
                        {
                            "active_blockers": stored_blockers,
                            "fact_count": fact_count,
                            "latest_sequence": lineage["sequence"],
                        },
                        maximum=_MAX_PROVENANCE_BYTES,
                    ),
                },
                where={"session_id": lineage["session_id"]},
            ),
            EntityChange.insert(
                "evidence",
                {
                    "evidence_id": evidence_id,
                    "task_id": lineage["task_id"],
                    "attempt_id": lineage["attempt_id"],
                    "kind": kind,
                    "integrity_hash": integrity_hash,
                    "summary_json": _canonical_json(
                        summary,
                        maximum=_MAX_PROVENANCE_BYTES,
                    ),
                    "created_revision": next_revision,
                },
            ),
        ),
        events=(event,),
        result={"evidence_id": evidence_id, "task_id": lineage["task_id"]},
    )


def _verification_decision(
    snapshot: ProjectMutationSnapshot,
    event: DomainEvent,
    task_id: str,
) -> MutationDecision:
    task_row = _one_row(snapshot, "tasks", task_id=task_id)
    mission_id = cast(str, _row_value(task_row, "mission_id"))
    version = cast(int, _row_value(task_row, "mission_version"))
    _confirmed_context(
        snapshot,
        mission_id,
        version,
        allowed_statuses=frozenset({"running", "paused"}),
    )
    task = _decode_task_row(task_row)
    if _row_value(task_row, "status") not in {"awaiting_verification", "paused"}:
        raise ValueError("task not awaiting verification")
    attempts = tuple(
        row
        for row in _rows(snapshot, "attempts")
        if _row_value(row, "task_id") == task_id
    )
    if not attempts:
        raise ValueError("verification attempt missing")
    attempt = max(attempts, key=lambda row: cast(int, _row_value(row, "attempt_number")))
    attempt_id = cast(str, _row_value(attempt, "attempt_id"))
    sessions = tuple(
        row
        for row in _rows(snapshot, "sessions")
        if _row_value(row, "attempt_id") == attempt_id
    )
    if len(sessions) != 1:
        raise ValueError("verification session lineage invalid")
    session_id = cast(str, _row_value(sessions[0], "session_id"))
    facts: list[EvidenceFact] = []
    for row in _rows(snapshot, "evidence"):
        if (
            _row_value(row, "task_id") != task_id
            or _row_value(row, "attempt_id") != attempt_id
            or _row_value(row, "kind")
            in {"verification_result", "effect_proof"}
        ):
            continue
        value = _row_value(row, "summary_json")
        try:
            parsed = json.loads(cast(str, value))
            if not isinstance(parsed, dict) or set(parsed) != {"criterion", "fact", "reason"}:
                raise _InvalidMissionValue
            facts.append(EvidenceFact(**parsed))
        except (json.JSONDecodeError, TypeError, ValueError, _InvalidMissionValue):
            raise ValueError("stored evidence invalid") from None
    result = grade_task(task, tuple(facts))
    next_revision = snapshot.revision + 1
    trigger_id = cast(str, _event_lineage(event)["internal_trigger_id"])
    verification_id = "evd_verify_" + hashlib.sha256(
        trigger_id.encode("utf-8")
    ).hexdigest()[:24]
    result_json = _canonical_json(result.to_dict(), maximum=_MAX_PROVENANCE_BYTES)
    integrity_hash = "sha256:" + hashlib.sha256(result_json.encode("utf-8")).hexdigest()
    changes: list[EntityChange] = [
        EntityChange.insert(
            "evidence",
            {
                "evidence_id": verification_id,
                "task_id": task_id,
                "attempt_id": attempt_id,
                "kind": "verification_result",
                "integrity_hash": integrity_hash,
                "summary_json": result_json,
                "created_revision": next_revision,
            },
        ),
        EntityChange.update(
            "tasks",
            {"status": result.aggregate_state.value, "updated_revision": next_revision},
            where={"task_id": task_id},
        ),
        EntityChange.update(
            "attempts",
            {
                "status": result.aggregate_state.value,
                **(
                    {"terminal_revision": next_revision}
                    if result.aggregate_state
                    in {TaskRuntimeState.COMPLETED, TaskRuntimeState.FAILED}
                    else {}
                ),
            },
            where={"attempt_id": attempt_id},
        ),
        EntityChange.update(
            "sessions",
            {"status": result.aggregate_state.value},
            where={"session_id": session_id},
        ),
    ]
    all_states = {
        cast(str, _row_value(row, "task_id")): cast(str, _row_value(row, "status"))
        for row in _rows(snapshot, "tasks")
        if _row_value(row, "mission_id") == mission_id
    }
    all_states[task_id] = result.aggregate_state.value
    mission_state = (
        "failed"
        if result.aggregate_state is TaskRuntimeState.FAILED
        else "completed"
        if all(value == "completed" for value in all_states.values())
        else "paused"
        if result.aggregate_state is TaskRuntimeState.PAUSED
        else "running"
    )
    mission = _one_row(snapshot, "missions", mission_id=mission_id)
    if _row_value(mission, "status") != mission_state:
        changes.append(
            EntityChange.update(
                "missions",
                {"status": mission_state, "updated_revision": next_revision},
                where={"mission_id": mission_id},
            )
        )
    return MutationDecision(
        changes=tuple(changes),
        events=(event,),
        result={
            "task_id": task_id,
            "verification_evidence_id": verification_id,
            "task_state": result.aggregate_state.value,
            "mission_state": mission_state,
        },
    )


def _handoff_decision(
    snapshot: ProjectMutationSnapshot,
    event: DomainEvent,
    request: HandoffRequest,
) -> MutationDecision:
    _confirmed_context(
        snapshot,
        request.mission_id,
        request.mission_version,
        allowed_statuses=frozenset({"running"}),
    )
    source_row, _source = _task_context(
        snapshot,
        request.mission_id,
        request.mission_version,
        request.source_task_id,
    )
    _destination_row, destination = _task_context(
        snapshot,
        request.mission_id,
        request.mission_version,
        request.destination_task_id,
    )
    attempt = _one_row(snapshot, "attempts", attempt_id=request.source_attempt_id)
    if (
        _row_value(attempt, "task_id") != request.source_task_id
        or _row_value(attempt, "status") != "completed"
    ):
        raise ValueError("handoff attempt invalid")
    decide_record_handoff(
        request.source_task_id,
        cast(str, _row_value(source_row, "status")),
        destination,
    )
    evidence_rows = {
        cast(str, _row_value(row, "evidence_id")): row
        for row in _rows(snapshot, "evidence")
    }
    if any(
        evidence_id not in evidence_rows
        or _row_value(evidence_rows[evidence_id], "task_id") != request.source_task_id
        or _row_value(evidence_rows[evidence_id], "attempt_id") != request.source_attempt_id
        for evidence_id in request.evidence_ids
    ):
        raise ValueError("handoff evidence invalid")
    if any(
        _row_value(row, "handoff_id") == request.handoff_id
        for row in _identities(snapshot, "handoffs")
    ):
        raise ValueError("handoff identity conflict")
    next_revision = snapshot.revision + 1
    context = {
        "mission_version": request.mission_version,
        "source_attempt_id": request.source_attempt_id,
        "evidence_ids": list(request.evidence_ids),
        "context": _thaw_json(cast(_FrozenJson, request.context)),
    }
    return MutationDecision(
        changes=(
            EntityChange.insert(
                "handoffs",
                {
                    "handoff_id": request.handoff_id,
                    "mission_id": request.mission_id,
                    "source_task_id": request.source_task_id,
                    "destination_task_id": request.destination_task_id,
                    "status": "accepted",
                    "context_json": _canonical_json(
                        context, maximum=_MAX_SPECIFICATION_BYTES
                    ),
                    "created_revision": next_revision,
                    "accepted_revision": next_revision,
                },
            ),
        ),
        events=(event,),
        result={"handoff_id": request.handoff_id, "status": "accepted"},
    )


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

    def release_ready_tasks(self, event: DomainEvent) -> EventMutationOutcome:
        payload = _event_payload(event)
        _validate_internal_event(event, kind="tasks.release", payload=payload)
        if set(payload) != {"mission_id"}:
            raise MutationValidationError("mission event invalid")
        return self._store.apply_event(
            event, lambda snapshot: _release_ready_decision(snapshot, event)
        )

    def start_attempt(
        self,
        event: DomainEvent,
        request: StartAttemptRequest,
    ) -> EventMutationOutcome:
        if type(request) is not StartAttemptRequest:
            raise MutationValidationError("attempt request invalid")
        _validate_internal_event(
            event, kind="attempt.start", payload=request.to_dict()
        )
        return self._store.apply_event(
            event,
            lambda snapshot: _start_attempt_decision(snapshot, event, request),
        )

    def record_worker_event(self, event: DomainEvent) -> EventMutationOutcome:
        if type(event) is not DomainEvent:
            raise MutationValidationError("mission event invalid")
        if event.kind == "evidence":
            return self.record_evidence(event)
        _validate_adapter_event(
            event,
            kinds=frozenset(
                {
                    "worker_message",
                    "progress",
                    "turn_completed",
                    "permission_requested",
                    "permission_conflict",
                    "ambiguous_effect",
                    "failed",
                    "cancelled",
                    "session_takeover",
                }
            ),
        )
        return self._store.apply_event(
            event, lambda snapshot: _worker_event_decision(snapshot, event)
        )

    def record_evidence(self, event: DomainEvent) -> EventMutationOutcome:
        _validate_adapter_event(event, kinds=frozenset({"evidence"}))
        return self._store.apply_event(
            event, lambda snapshot: _record_evidence_decision(snapshot, event)
        )

    def verify_task(
        self,
        event: DomainEvent,
        task_id: str,
    ) -> EventMutationOutcome:
        if not _valid_identifier(task_id):
            raise MutationValidationError("verification request invalid")
        _validate_internal_event(
            event, kind="task.verify", payload={"task_id": task_id}
        )
        return self._store.apply_event(
            event,
            lambda snapshot: _verification_decision(snapshot, event, task_id),
        )

    def record_handoff(
        self,
        event: DomainEvent,
        request: HandoffRequest,
    ) -> EventMutationOutcome:
        if type(request) is not HandoffRequest:
            raise MutationValidationError("handoff request invalid")
        _validate_internal_event(
            event, kind="handoff.record", payload=request.to_dict()
        )
        return self._store.apply_event(
            event,
            lambda snapshot: _handoff_decision(snapshot, event, request),
        )
