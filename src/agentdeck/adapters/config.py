from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from agentdeck.adapters.input_snapshot import snapshot_mapping


_MAX_KEY_BYTES = 128
_MAX_VALUE_BYTES = 4096


@dataclass(frozen=True)
class ResolvedConfig:
    value: str
    source: str


class ConfigResolver:
    _SOURCE_ORDER = ("session", "project", "global", "discovery")

    def __init__(
        self,
        *,
        discovered: Mapping[str, str],
        global_values: Mapping[str, str],
        project_values: Mapping[str, str],
        session_values: Mapping[str, str],
    ) -> None:
        layers = {
            "session": _copy_layer(session_values),
            "project": _copy_layer(project_values),
            "global": _copy_layer(global_values),
            "discovery": _copy_layer(discovered),
        }
        self._layers = MappingProxyType(layers)

    def resolve(self, key: str) -> ResolvedConfig:
        _validate_lookup_key(key)
        for source in self._SOURCE_ORDER:
            layer = self._layers[source]
            if key in layer:
                return ResolvedConfig(value=layer[key], source=source)
        raise KeyError("configuration key is not configured")


def _copy_layer(values: Mapping[str, str]) -> Mapping[str, str]:
    copied: dict[str, str] = {}
    for key, value in snapshot_mapping(values, label="configuration layer"):
        _validate_entry(key, value)
        copied[key] = value
    return MappingProxyType(copied)


def _validate_lookup_key(key: str) -> None:
    if type(key) is not str:
        raise TypeError("configuration key must be a string")
    if not key.strip():
        raise ValueError("configuration key must not be empty")
    _strict_bounded_utf8(
        key,
        maximum=_MAX_KEY_BYTES,
        invalid_message="configuration key must be bounded UTF-8",
    )


def _validate_entry(key: str, value: str) -> None:
    if type(key) is not str:
        raise TypeError("configuration keys must be strings")
    if not key.strip():
        raise ValueError("configuration keys must not be empty")
    _strict_bounded_utf8(
        key,
        maximum=_MAX_KEY_BYTES,
        invalid_message="configuration keys must be bounded UTF-8",
    )
    if type(value) is not str:
        raise TypeError("configuration values must be strings")
    if not value.strip():
        raise ValueError("configuration values must not be empty")
    _strict_bounded_utf8(
        value,
        maximum=_MAX_VALUE_BYTES,
        invalid_message="configuration values must be bounded UTF-8",
    )


def _strict_bounded_utf8(value: str, *, maximum: int, invalid_message: str) -> None:
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError(invalid_message) from None
    if len(encoded) > maximum:
        raise ValueError(invalid_message)
