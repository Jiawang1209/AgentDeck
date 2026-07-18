"""Deterministic, transport-independent domain events.

This module deliberately has no store, daemon, provider, runtime, filesystem,
network, or clock dependencies.  Callers must supply every identity and the
event timestamp explicitly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast


type JsonValue = None | bool | int | str | list[JsonValue] | dict[str, JsonValue]
type FrozenJsonValue = (
    None
    | bool
    | int
    | str
    | tuple[FrozenJsonValue, ...]
    | Mapping[str, FrozenJsonValue]
)


MAX_EVENT_BYTES = 64 * 1024
MAX_JSON_DEPTH = 16
_MAX_METADATA_BYTES = 4 * 1024
_MAX_SIGNED_64 = (2**63) - 1
_HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class _InvalidJson(ValueError):
    """Private sentinel whose details are never exposed to callers."""


def _valid_text(value: object, *, allow_empty: bool = False) -> bool:
    if not isinstance(value, str) or (not allow_empty and not value):
        return False
    try:
        return len(value.encode("utf-8")) <= _MAX_METADATA_BYTES
    except UnicodeEncodeError:
        return False


def _valid_revision(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _MAX_SIGNED_64
    )


def _freeze_json(
    value: object,
    *,
    depth: int = 0,
    active: set[int] | None = None,
) -> FrozenJsonValue:
    if depth > MAX_JSON_DEPTH:
        raise _InvalidJson
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _InvalidJson from exc
        if len(encoded) > MAX_EVENT_BYTES:
            raise _InvalidJson
        return value
    if isinstance(value, list):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            raise _InvalidJson
        active.add(identity)
        try:
            return tuple(
                _freeze_json(item, depth=depth + 1, active=active) for item in value
            )
        finally:
            active.remove(identity)
    if isinstance(value, dict):
        active = set() if active is None else active
        identity = id(value)
        if identity in active:
            raise _InvalidJson
        active.add(identity)
        try:
            frozen: dict[str, FrozenJsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise _InvalidJson
                try:
                    encoded_key = key.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise _InvalidJson from exc
                if len(encoded_key) > MAX_EVENT_BYTES:
                    raise _InvalidJson
                frozen[key] = _freeze_json(item, depth=depth + 1, active=active)
            return MappingProxyType(frozen)
        finally:
            active.remove(identity)
    raise _InvalidJson


def _thaw_json(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_json_size(value: FrozenJsonValue) -> int:
    thawed = _thaw_json(value)
    try:
        return len(
            json.dumps(
                thawed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise _InvalidJson from exc


def _freeze_bounded_json(value: object) -> FrozenJsonValue:
    frozen = _freeze_json(value)
    if _canonical_json_size(frozen) > MAX_EVENT_BYTES:
        raise _InvalidJson
    return frozen


@dataclass(frozen=True, slots=True)
class ClientCommandProvenance:
    """Closed provenance for one explicitly revisioned client command."""

    command_id: str
    expected_revision: int
    actor: Mapping[str, FrozenJsonValue]

    def __post_init__(self) -> None:
        if (
            not _valid_text(self.command_id)
            or not _valid_revision(self.expected_revision)
            or not isinstance(self.actor, dict)
            or not self.actor
        ):
            raise ValueError("client command provenance invalid")
        try:
            frozen_actor = _freeze_bounded_json(self.actor)
        except _InvalidJson:
            raise ValueError("client command provenance invalid") from None
        object.__setattr__(
            self, "actor", cast(Mapping[str, FrozenJsonValue], frozen_actor)
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "command_id": self.command_id,
            "expected_revision": self.expected_revision,
            "actor": _thaw_json(self.actor),
        }


@dataclass(frozen=True, slots=True)
class AdapterEventProvenance:
    """Closed provenance for one sequenced, integrity-bound adapter event."""

    adapter_event_id: str
    mission_id: str
    mission_version: str
    task_id: str
    attempt_id: str
    session_id: str
    sequence: int
    integrity_hash: str

    def __post_init__(self) -> None:
        identifiers = (
            self.adapter_event_id,
            self.mission_id,
            self.mission_version,
            self.task_id,
            self.attempt_id,
            self.session_id,
        )
        if (
            not all(_valid_text(item) for item in identifiers)
            or not _valid_revision(self.sequence)
            or not isinstance(self.integrity_hash, str)
            or _HASH_PATTERN.fullmatch(self.integrity_hash) is None
        ):
            raise ValueError("adapter event provenance invalid")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "adapter_event_id": self.adapter_event_id,
            "mission_id": self.mission_id,
            "mission_version": self.mission_version,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "integrity_hash": self.integrity_hash,
        }


@dataclass(frozen=True, slots=True)
class InternalTriggerProvenance:
    """Closed provenance for one deterministic internal trigger."""

    internal_trigger_id: str
    source_revision: int
    source_snapshot_id: str

    def __post_init__(self) -> None:
        if (
            not _valid_text(self.internal_trigger_id)
            or not _valid_revision(self.source_revision)
            or not _valid_text(self.source_snapshot_id)
        ):
            raise ValueError("internal trigger provenance invalid")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "internal_trigger_id": self.internal_trigger_id,
            "source_revision": self.source_revision,
            "source_snapshot_id": self.source_snapshot_id,
        }


type EventProvenance = (
    ClientCommandProvenance | AdapterEventProvenance | InternalTriggerProvenance
)


@dataclass(frozen=True, slots=True, init=False)
class DomainEvent:
    """A deeply immutable event with one closed provenance shape."""

    event_id: str
    kind: str
    trigger_kind: str
    provenance: EventProvenance
    payload: FrozenJsonValue
    created_at: str

    def __init__(
        self,
        *args: object,
        **kwargs: object,
    ) -> None:
        del args, kwargs
        raise TypeError("domain event construction requires a trigger constructor")

    @classmethod
    def _create(
        cls,
        *,
        event_id: str,
        kind: str,
        trigger_kind: str,
        provenance: EventProvenance,
        payload: object,
        created_at: str,
    ) -> DomainEvent:
        if not all(_valid_text(item) for item in (event_id, kind, created_at)):
            raise ValueError("domain event metadata invalid")
        expected_type = {
            "client_command": ClientCommandProvenance,
            "adapter_event": AdapterEventProvenance,
            "internal_trigger": InternalTriggerProvenance,
        }.get(trigger_kind)
        if expected_type is None or not isinstance(provenance, expected_type):
            raise ValueError("domain event trigger invalid")

        try:
            frozen_payload = _freeze_bounded_json(payload)
        except _InvalidJson:
            raise ValueError("domain event payload invalid") from None

        event = object.__new__(cls)
        object.__setattr__(event, "event_id", event_id)
        object.__setattr__(event, "kind", kind)
        object.__setattr__(event, "trigger_kind", trigger_kind)
        object.__setattr__(event, "provenance", provenance)
        object.__setattr__(event, "payload", frozen_payload)
        object.__setattr__(event, "created_at", created_at)

        if len(event.canonical_bytes()) > MAX_EVENT_BYTES:
            raise ValueError("domain event payload invalid")
        return event

    @classmethod
    def client_command(
        cls,
        *,
        event_id: str,
        kind: str,
        command_id: str,
        expected_revision: int,
        actor: object,
        payload: object,
        created_at: str,
        **unexpected: object,
    ) -> DomainEvent:
        if unexpected:
            raise ValueError("client command provenance invalid")
        provenance = ClientCommandProvenance(
            command_id=command_id,
            expected_revision=expected_revision,
            actor=actor,  # type: ignore[arg-type]
        )
        return cls._create(
            event_id=event_id,
            kind=kind,
            trigger_kind="client_command",
            provenance=provenance,
            payload=payload,
            created_at=created_at,
        )

    @classmethod
    def adapter_event(
        cls,
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
        integrity_hash: str,
        payload: object,
        created_at: str,
        **unexpected: object,
    ) -> DomainEvent:
        if unexpected:
            raise ValueError("adapter event provenance invalid")
        provenance = AdapterEventProvenance(
            adapter_event_id=adapter_event_id,
            mission_id=mission_id,
            mission_version=mission_version,
            task_id=task_id,
            attempt_id=attempt_id,
            session_id=session_id,
            sequence=sequence,
            integrity_hash=integrity_hash,
        )
        return cls._create(
            event_id=event_id,
            kind=kind,
            trigger_kind="adapter_event",
            provenance=provenance,
            payload=payload,
            created_at=created_at,
        )

    @classmethod
    def internal_trigger(
        cls,
        *,
        event_id: str,
        kind: str,
        internal_trigger_id: str,
        source_revision: int,
        source_snapshot_id: str,
        payload: object,
        created_at: str,
        **unexpected: object,
    ) -> DomainEvent:
        if unexpected:
            raise ValueError("internal trigger provenance invalid")
        provenance = InternalTriggerProvenance(
            internal_trigger_id=internal_trigger_id,
            source_revision=source_revision,
            source_snapshot_id=source_snapshot_id,
        )
        return cls._create(
            event_id=event_id,
            kind=kind,
            trigger_kind="internal_trigger",
            provenance=provenance,
            payload=payload,
            created_at=created_at,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a detached mutable JSON representation."""

        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "trigger_kind": self.trigger_kind,
            "provenance": self.provenance.to_dict(),
            "payload": _thaw_json(self.payload),
            "created_at": self.created_at,
        }

    def canonical_bytes(self) -> bytes:
        """Return byte-identical canonical UTF-8 JSON for persistence/hashing."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


__all__ = [
    "AdapterEventProvenance",
    "ClientCommandProvenance",
    "DomainEvent",
    "InternalTriggerProvenance",
    "JsonValue",
    "MAX_EVENT_BYTES",
    "MAX_JSON_DEPTH",
]
