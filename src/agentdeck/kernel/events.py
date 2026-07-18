from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import TypeAlias
from uuid import uuid4


FactScalar: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True)
class FactObject:
    items: tuple[tuple[str, "FactValue"], ...]

    def __post_init__(self) -> None:
        _validate_object_items(self.items)


@dataclass(frozen=True)
class FactArray:
    items: tuple["FactValue", ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple:
            raise TypeError("fact array items must be a tuple")
        for item in self.items:
            _validate_fact(item)


FactValue: TypeAlias = FactScalar | FactObject | FactArray
FactPayload: TypeAlias = tuple[tuple[str, FactValue], ...]


def normalize_occurred_at(occurred_at: str) -> str:
    if type(occurred_at) is not str:
        raise TypeError("occurred_at must be a string")
    try:
        parsed = datetime.fromisoformat(occurred_at)
    except ValueError:
        raise ValueError("occurred_at must be a valid ISO datetime") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("occurred_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _freeze_fact(value: object) -> FactValue:
    if value is None or type(value) in {str, int, bool}:
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("fact floats must be finite")
        return value
    if isinstance(value, Mapping):
        return FactObject(items=_freeze_mapping(value))
    if type(value) in {list, tuple}:
        return FactArray(items=tuple(_freeze_fact(item) for item in value))
    raise TypeError("unsupported fact value")


def _freeze_mapping(value: Mapping[object, object]) -> FactPayload:
    if any(type(key) is not str for key in value):
        raise TypeError("fact mapping keys must be strings")
    return tuple((key, _freeze_fact(value[key])) for key in sorted(value))


def _validate_fact(value: object) -> None:
    if value is None or type(value) in {str, int, bool}:
        return
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("fact floats must be finite")
        return
    if type(value) is FactObject:
        _validate_object_items(value.items)
        return
    if type(value) is FactArray:
        if type(value.items) is not tuple:
            raise TypeError("fact array items must be a tuple")
        for item in value.items:
            _validate_fact(item)
        return
    raise TypeError("event facts must be canonical immutable values")


def _validate_object_items(items: object) -> None:
    if type(items) is not tuple:
        raise TypeError("fact object items must be a canonical tuple")
    keys: list[str] = []
    for item in items:
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
            raise TypeError("fact object entries must be string-keyed tuples")
        keys.append(item[0])
        _validate_fact(item[1])
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError("fact object keys must be unique and sorted")


def _validate_payload(payload: object) -> None:
    try:
        _validate_object_items(payload)
    except (TypeError, ValueError) as error:
        raise type(error)(str(error).replace("fact object", "event payload")) from None


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    kind: str
    aggregate_type: str
    aggregate_id: str
    payload: FactPayload
    occurred_at: str

    def __post_init__(self) -> None:
        for value in (self.event_id, self.kind, self.aggregate_type, self.aggregate_id):
            if type(value) is not str:
                raise TypeError("event identity fields must be strings")
        _validate_payload(self.payload)
        object.__setattr__(
            self, "occurred_at", normalize_occurred_at(self.occurred_at)
        )

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        occurred_at: str,
    ) -> "DomainEvent":
        if not isinstance(payload, Mapping):
            raise TypeError("event payload must be a mapping")
        return cls(
            event_id=f"evt_{uuid4().hex}",
            kind=kind,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=_freeze_mapping(payload),
            occurred_at=normalize_occurred_at(occurred_at),
        )
