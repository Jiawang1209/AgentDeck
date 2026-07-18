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
_HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")

_CLIENT_FIELDS = frozenset({"command_id", "expected_revision", "actor"})
_ADAPTER_FIELDS = frozenset(
    {
        "adapter_event_id",
        "mission_id",
        "mission_version",
        "task_id",
        "attempt_id",
        "session_id",
        "sequence",
        "integrity_hash",
    }
)
_INTERNAL_FIELDS = frozenset(
    {"internal_trigger_id", "source_revision", "source_snapshot_id"}
)


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
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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


def _validate_client_provenance(value: object) -> Mapping[str, FrozenJsonValue]:
    if not isinstance(value, dict) or set(value) != _CLIENT_FIELDS:
        raise ValueError("client command provenance invalid")
    command_id = value.get("command_id")
    expected_revision = value.get("expected_revision")
    actor = value.get("actor")
    if (
        not _valid_text(command_id)
        or not _valid_revision(expected_revision)
        or not isinstance(actor, dict)
        or not actor
    ):
        raise ValueError("client command provenance invalid")
    try:
        frozen = _freeze_bounded_json(value)
    except _InvalidJson:
        raise ValueError("client command provenance invalid") from None
    return cast(Mapping[str, FrozenJsonValue], frozen)


def _validate_adapter_provenance(value: object) -> Mapping[str, FrozenJsonValue]:
    if not isinstance(value, dict) or set(value) != _ADAPTER_FIELDS:
        raise ValueError("adapter event provenance invalid")
    identifiers = (
        value.get("adapter_event_id"),
        value.get("mission_id"),
        value.get("mission_version"),
        value.get("task_id"),
        value.get("attempt_id"),
        value.get("session_id"),
    )
    integrity_hash = value.get("integrity_hash")
    if (
        not all(_valid_text(item) for item in identifiers)
        or not _valid_revision(value.get("sequence"))
        or not isinstance(integrity_hash, str)
        or _HASH_PATTERN.fullmatch(integrity_hash) is None
    ):
        raise ValueError("adapter event provenance invalid")
    try:
        frozen = _freeze_bounded_json(value)
    except _InvalidJson:
        raise ValueError("adapter event provenance invalid") from None
    return cast(Mapping[str, FrozenJsonValue], frozen)


def _validate_internal_provenance(value: object) -> Mapping[str, FrozenJsonValue]:
    if not isinstance(value, dict) or set(value) != _INTERNAL_FIELDS:
        raise ValueError("internal trigger provenance invalid")
    if (
        not _valid_text(value.get("internal_trigger_id"))
        or not _valid_revision(value.get("source_revision"))
        or not _valid_text(value.get("source_snapshot_id"))
    ):
        raise ValueError("internal trigger provenance invalid")
    try:
        frozen = _freeze_bounded_json(value)
    except _InvalidJson:
        raise ValueError("internal trigger provenance invalid") from None
    return cast(Mapping[str, FrozenJsonValue], frozen)


@dataclass(frozen=True, slots=True, init=False)
class DomainEvent:
    """A deeply immutable event with one closed provenance shape."""

    event_id: str
    kind: str
    trigger_kind: str
    provenance: Mapping[str, FrozenJsonValue]
    payload: FrozenJsonValue
    created_at: str

    def __init__(
        self,
        *,
        event_id: str,
        kind: str,
        trigger_kind: str,
        provenance: object,
        payload: object,
        created_at: str,
    ) -> None:
        if not all(_valid_text(item) for item in (event_id, kind, created_at)):
            raise ValueError("domain event metadata invalid")

        if trigger_kind == "client_command":
            frozen_provenance = _validate_client_provenance(provenance)
        elif trigger_kind == "adapter_event":
            frozen_provenance = _validate_adapter_provenance(provenance)
        elif trigger_kind == "internal_trigger":
            frozen_provenance = _validate_internal_provenance(provenance)
        else:
            raise ValueError("domain event trigger invalid")

        try:
            frozen_payload = _freeze_bounded_json(payload)
        except _InvalidJson:
            raise ValueError("domain event payload invalid") from None

        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "trigger_kind", trigger_kind)
        object.__setattr__(self, "provenance", frozen_provenance)
        object.__setattr__(self, "payload", frozen_payload)
        object.__setattr__(self, "created_at", created_at)

        if len(self.canonical_bytes()) > MAX_EVENT_BYTES:
            raise ValueError("domain event payload invalid")

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
        return cls(
            event_id=event_id,
            kind=kind,
            trigger_kind="client_command",
            provenance={
                "command_id": command_id,
                "expected_revision": expected_revision,
                "actor": actor,
            },
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
        return cls(
            event_id=event_id,
            kind=kind,
            trigger_kind="adapter_event",
            provenance={
                "adapter_event_id": adapter_event_id,
                "mission_id": mission_id,
                "mission_version": mission_version,
                "task_id": task_id,
                "attempt_id": attempt_id,
                "session_id": session_id,
                "sequence": sequence,
                "integrity_hash": integrity_hash,
            },
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
        return cls(
            event_id=event_id,
            kind=kind,
            trigger_kind="internal_trigger",
            provenance={
                "internal_trigger_id": internal_trigger_id,
                "source_revision": source_revision,
                "source_snapshot_id": source_snapshot_id,
            },
            payload=payload,
            created_at=created_at,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a detached mutable JSON representation."""

        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "trigger_kind": self.trigger_kind,
            "provenance": _thaw_json(self.provenance),
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
    "DomainEvent",
    "JsonValue",
    "MAX_EVENT_BYTES",
    "MAX_JSON_DEPTH",
]
