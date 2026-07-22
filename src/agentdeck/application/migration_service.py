"""Explicit legacy → SQLite migration (preview / apply / authority).

Old JSON/JSONL state is never silently imported and never becomes a second
write authority. An existing project receives an explicit preview, then — only
on confirmation with the exact preview hash — a backed-up, verified, atomic
apply. Any drift or verification failure leaves legacy authority unchanged.

Architecture: this is an ``application`` service. It depends only on an injected
``legacy_reader`` (the composition root wires the adapter parser) and stdlib
filesystem checks; it never imports adapters, ``state.py``, or ``models.py``.
Slice 37.2 implements the read-only ``preview`` and ``authority``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_MIGRATED_DB = "agentdeck.db"


class MigrationError(ValueError):
    """Raised when a migration cannot proceed safely."""


@dataclass(frozen=True)
class MigrationPreview:
    preview_id: str
    content_hash: str
    project_id: str
    resolved_root: str
    sources: tuple[dict, ...]
    mappings: dict
    skipped_items: tuple[str, ...]
    backup_target: str
    requires_confirmation: bool
    writes: tuple


@dataclass(frozen=True)
class MigrationService:
    legacy_reader: Callable[[object], object]

    def authority(self, project_dir: object) -> str:
        """``migrated`` if the new DB exists, else ``legacy`` if legacy state
        exists, else ``none``. Read-only."""
        root = Path(project_dir)
        if (root / ".agentdeck" / _MIGRATED_DB).exists():
            return "migrated"
        return "legacy" if self.legacy_reader(root) is not None else "none"

    def preview(self, project_dir: object) -> MigrationPreview:
        """Describe exactly what an apply would do. Writes nothing."""
        root = Path(project_dir)
        legacy = self.legacy_reader(root)
        if legacy is None:
            raise MigrationError("no legacy state to migrate")

        preview_id = "mgp_" + legacy.content_hash.split(":", 1)[1][:24]
        skipped: list[str] = []
        if legacy.agent_count:
            skipped.append(f"agents:{legacy.agent_count}")
        if legacy.message_count:
            skipped.append(f"messages:{legacy.message_count}")
        if legacy.job_count:
            skipped.append(f"jobs:{legacy.job_count}")
        if legacy.event_count:
            skipped.append(f"events:{legacy.event_count}")

        return MigrationPreview(
            preview_id=preview_id,
            content_hash=legacy.content_hash,
            project_id=legacy.project_id,
            resolved_root=legacy.resolved_root,
            sources=tuple(
                {"path": source.path, "kind": source.kind, "hash": source.hash}
                for source in legacy.sources
            ),
            mappings={"projects": 1},
            skipped_items=tuple(skipped),
            backup_target=f".agentdeck/backups/{preview_id}/",
            requires_confirmation=True,
            writes=(),
        )


__all__ = [
    "MigrationError",
    "MigrationPreview",
    "MigrationService",
]
