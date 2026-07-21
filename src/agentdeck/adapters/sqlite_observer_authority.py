"""SQLite v3 wrappers and canonical Observer/takeover authority rows."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sqlite3

from agentdeck.adapters.sqlite_migrations import (
    _validate_before_durability as _validate_before_v3,
    _validate_existing_schema as _validate_existing_v2,
    migrate_schema as _migrate_v2,
)
from agentdeck.adapters.sqlite_schema import (
    is_schema_v3, migrate_schema_v3, validate_schema_v3,
)
from agentdeck.adapters.sqlite_validation import _canonical, _decode_canonical


OBSERVER_AUTHORITY_TYPES = frozenset({
    "observer_cursors", "takeover_ownership",
})
_SPECS = {
    "observer_cursors": (
        "cursor_id", "canonical_cursor_facts",
    ),
    "takeover_ownership": (
        "attempt_id", "canonical_ownership_facts",
    ),
}


def validate_before_durability(
    connection: sqlite3.Connection, root: Path,
) -> None:
    if is_schema_v3(connection):
        validate_schema_v3(connection, root)
    else:
        _validate_before_v3(connection, root)


def validate_existing_schema(
    connection: sqlite3.Connection, root: Path,
) -> None:
    if is_schema_v3(connection):
        validate_schema_v3(connection, root)
    else:
        _validate_existing_v2(connection, root)


def migrate_schema(connection: sqlite3.Connection, root: Path) -> None:
    if not is_schema_v3(connection):
        _migrate_v2(connection, root)
    migrate_schema_v3(connection, root)


def save_authority(
    connection: sqlite3.Connection, aggregate_type: str, aggregate_id: str,
    snapshot: Mapping[str, object], updated_at: str,
) -> None:
    identity, facts = _spec(aggregate_type)
    if snapshot.get(identity) != aggregate_id:
        raise ValueError("authority aggregate identity does not match")
    canonical, normalized = _canonical(snapshot)
    if normalized.get(identity) != aggregate_id:
        raise ValueError("authority aggregate identity changed during encoding")
    connection.execute(
        f"""INSERT INTO {aggregate_type} ({identity},{facts},updated_at)
            VALUES (?,?,?) ON CONFLICT({identity}) DO UPDATE SET
            {facts}=excluded.{facts},updated_at=excluded.updated_at""",
        (aggregate_id, canonical, updated_at),
    )


def load_authority(
    connection: sqlite3.Connection, aggregate_type: str, aggregate_id: str,
) -> dict[str, object] | None:
    identity, facts = _spec(aggregate_type)
    row = connection.execute(
        f"SELECT {facts} FROM {aggregate_type} WHERE {identity}=?",
        (aggregate_id,),
    ).fetchone()
    if row is None:
        return None
    value = _decode_canonical(row[0])
    if value.get(identity) != aggregate_id:
        raise ValueError("stored authority aggregate identity drifted")
    return value


def _spec(aggregate_type: str) -> tuple[str, str]:
    try:
        return _SPECS[aggregate_type]
    except (KeyError, TypeError):
        raise ValueError("unsupported observer authority aggregate") from None


__all__ = [
    "OBSERVER_AUTHORITY_TYPES", "load_authority", "migrate_schema",
    "save_authority", "validate_before_durability", "validate_existing_schema",
]
