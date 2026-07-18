from __future__ import annotations

from collections.abc import Mapping


_MAX_MAPPING_ITEMS = 256


def snapshot_mapping(
    values: Mapping[object, object], *, label: str
) -> tuple[tuple[object, object], ...]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{label} must be a mapping")
    try:
        iterator = iter(values.items())
    except Exception:
        raise TypeError(f"{label} could not be read") from None

    snapshot: list[tuple[object, object]] = []
    seen: set[object] = set()
    while True:
        try:
            item = next(iterator)
        except StopIteration:
            return tuple(snapshot)
        except Exception:
            raise TypeError(f"{label} could not be read") from None
        if len(snapshot) >= _MAX_MAPPING_ITEMS:
            raise ValueError(f"{label} has too many items")
        try:
            key, value = item
        except Exception:
            raise ValueError(f"{label} items must be key-value pairs") from None
        try:
            duplicate = key in seen
        except Exception:
            raise ValueError(f"{label} keys must be stable and hashable") from None
        if duplicate:
            raise ValueError(f"{label} contains duplicate keys")
        try:
            seen.add(key)
        except Exception:
            raise ValueError(f"{label} keys must be stable and hashable") from None
        snapshot.append((key, value))
