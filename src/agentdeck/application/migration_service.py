"""Explicit legacy → SQLite migration (preview / apply / authority).

Old JSON/JSONL state is never silently imported and never becomes a second
write authority. An existing project receives an explicit preview, then — only
on confirmation with the exact preview hash — a backed-up, verified, atomic
apply. Any drift or verification failure leaves legacy authority unchanged and
writes no new-database authority.

Architecture: this is an ``application`` service. It depends only on an injected
``legacy_reader`` and ``db_importer`` (the composition root wires the real
adapter parser and SQLite importer) plus stdlib filesystem/os primitives; it
never imports adapters, ``state.py``, or ``models.py``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import os
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
class ImportOutcome:
    imported_counts: dict
    integrity: str


@dataclass(frozen=True)
class MigrationReport:
    backup_hash: str
    database_integrity: str
    imported_counts: dict
    skipped_items: tuple[str, ...]
    rollback_command: str


def _skipped_items(legacy: object) -> tuple[str, ...]:
    skipped: list[str] = []
    if legacy.agent_count:
        skipped.append(f"agents:{legacy.agent_count}")
    if legacy.message_count:
        skipped.append(f"messages:{legacy.message_count}")
    if legacy.job_count:
        skipped.append(f"jobs:{legacy.job_count}")
    if legacy.event_count:
        skipped.append(f"events:{legacy.event_count}")
    return tuple(skipped)


class MigrationService:
    """Explicit, drift-guarded legacy migration. Stateful across preview→apply."""

    def __init__(
        self,
        *,
        legacy_reader: Callable[[object], object],
        db_importer: Callable[[Path, object], ImportOutcome] | None = None,
    ) -> None:
        self._legacy_reader = legacy_reader
        self._db_importer = db_importer
        self._pending: dict[str, tuple[Path, str]] = {}

    def authority(self, project_dir: object) -> str:
        """``migrated`` if the new DB exists, else ``legacy`` if legacy state
        exists, else ``none``. Read-only."""
        root = Path(project_dir)
        if (root / ".agentdeck" / _MIGRATED_DB).exists():
            return "migrated"
        return "legacy" if self._legacy_reader(root) is not None else "none"

    def preview(self, project_dir: object) -> MigrationPreview:
        """Describe exactly what an apply would do. Writes nothing."""
        root = Path(project_dir)
        legacy = self._legacy_reader(root)
        if legacy is None:
            raise MigrationError("no legacy state to migrate")

        preview_id = "mgp_" + legacy.content_hash.split(":", 1)[1][:24]
        preview = MigrationPreview(
            preview_id=preview_id,
            content_hash=legacy.content_hash,
            project_id=legacy.project_id,
            resolved_root=legacy.resolved_root,
            sources=tuple(
                {"path": source.path, "kind": source.kind, "hash": source.hash}
                for source in legacy.sources
            ),
            mappings={"projects": 1},
            skipped_items=_skipped_items(legacy),
            backup_target=f".agentdeck/backups/{preview_id}/",
            requires_confirmation=True,
            writes=(),
        )
        self._pending[preview_id] = (root, legacy.content_hash)
        return preview

    def apply(
        self, preview_id: str, content_hash: str, *, confirm: bool
    ) -> MigrationReport:
        """Back up, import into a fresh DB, verify, and atomically install it.

        Fails closed — leaving legacy authority and writing no new DB — on a
        missing confirmation, an unknown preview, a supplied-hash mismatch, or
        legacy content drift since the preview."""
        if confirm is not True:
            raise MigrationError("apply requires explicit confirm=True")
        if preview_id not in self._pending:
            raise MigrationError(f"unknown preview_id: {preview_id}")
        project_dir, previewed_hash = self._pending[preview_id]
        if content_hash != previewed_hash:
            raise MigrationError("preview content_hash drift")
        legacy = self._legacy_reader(project_dir)
        if legacy is None or legacy.content_hash != previewed_hash:
            raise MigrationError("legacy state drift since preview")
        if self._db_importer is None:
            raise MigrationError("no db importer configured")

        backup_hash = self._backup(project_dir, preview_id, legacy)

        agentdeck = Path(project_dir) / ".agentdeck"
        temporary_db = agentdeck / f".{_MIGRATED_DB}.{preview_id}.tmp"
        if temporary_db.exists():
            temporary_db.unlink()
        outcome = self._db_importer(temporary_db, legacy)
        if outcome.integrity != "ok":
            temporary_db.unlink(missing_ok=True)
            raise MigrationError("new database failed integrity verification")
        if outcome.imported_counts.get("projects") != 1:
            temporary_db.unlink(missing_ok=True)
            raise MigrationError("new database did not import the project")

        final_db = agentdeck / _MIGRATED_DB
        os.replace(temporary_db, final_db)  # atomic install

        return MigrationReport(
            backup_hash=backup_hash,
            database_integrity=outcome.integrity,
            imported_counts=dict(outcome.imported_counts),
            skipped_items=_skipped_items(legacy),
            rollback_command=f"rm {final_db}",
        )

    def _backup(self, project_dir: Path, preview_id: str, legacy: object) -> str:
        backup_dir = Path(project_dir) / ".agentdeck" / "backups" / preview_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        hasher = sha256()
        for source in legacy.sources:
            src = Path(source.path)
            data = src.read_bytes()
            (backup_dir / src.name).write_bytes(data)
            hasher.update(f"{src.name}:".encode("utf-8"))
            hasher.update(data)
        return "sha256:" + hasher.hexdigest()


__all__ = [
    "ImportOutcome",
    "MigrationError",
    "MigrationPreview",
    "MigrationReport",
    "MigrationService",
]
