"""Task 37 — explicit legacy JSON→SQLite migration (no silent import).

Legacy `.agentdeck/state.json` + `events.jsonl` are parsed as INERT external
data; they are never imported silently and cannot become a second write
authority. Slice 37.1 covers the read-only parser.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from agentdeck.adapters.legacy_state import LegacyState, parse_legacy_state

_FIXTURE = Path(__file__).parent / "fixtures" / "legacy_state"


def _legacy_project(tmp_path: Path) -> Path:
    agentdeck = tmp_path / ".agentdeck"
    agentdeck.mkdir()
    shutil.copy(_FIXTURE / "state.json", agentdeck / "state.json")
    shutil.copy(_FIXTURE / "events.jsonl", agentdeck / "events.jsonl")
    return tmp_path


def test_parse_legacy_state_reads_inert_records(tmp_path: Path) -> None:
    state = parse_legacy_state(_legacy_project(tmp_path))
    assert isinstance(state, LegacyState)
    assert state.project_id == "prj_legacy0001"
    assert state.resolved_root == "/legacy/example/project"
    assert state.created_at == "2026-07-01T00:00:00+00:00"
    assert state.agent_count == 2
    assert state.message_count == 2
    assert state.job_count == 1
    assert state.event_count == 3
    assert state.content_hash.startswith("sha256:")
    assert {source.kind for source in state.sources} == {"state", "events"}


def test_parse_legacy_state_absent_returns_none(tmp_path: Path) -> None:
    (tmp_path / ".agentdeck").mkdir()
    assert parse_legacy_state(tmp_path) is None


def test_parse_legacy_state_hash_is_content_addressed(tmp_path: Path) -> None:
    first = parse_legacy_state(_legacy_project(tmp_path))
    other = tmp_path / "second"
    other.mkdir()
    second = parse_legacy_state(_legacy_project(other))
    assert first.content_hash == second.content_hash


# --- slice 37.2: preview + authority (read-only) ---------------------------

import pytest

from agentdeck.application.migration_service import (
    MigrationError,
    MigrationPreview,
    MigrationService,
)


def _service() -> MigrationService:
    return MigrationService(legacy_reader=parse_legacy_state)


def test_preview_writes_nothing_and_requires_confirmation(tmp_path: Path) -> None:
    project = _legacy_project(tmp_path)
    preview = _service().preview(project)
    assert isinstance(preview, MigrationPreview)
    assert preview.writes == ()
    assert preview.requires_confirmation is True
    assert preview.mappings["projects"] == 1
    assert preview.skipped_items  # legacy agents/messages/jobs are unsupported
    assert preview.content_hash.startswith("sha256:")
    assert preview.preview_id.startswith("mgp_")
    assert not (project / ".agentdeck" / "agentdeck.db").exists()


def test_authority_is_legacy_before_migration(tmp_path: Path) -> None:
    assert _service().authority(_legacy_project(tmp_path)) == "legacy"


def test_authority_is_none_without_legacy_or_migrated_db(tmp_path: Path) -> None:
    (tmp_path / ".agentdeck").mkdir()
    assert _service().authority(tmp_path) == "none"


def test_preview_without_legacy_state_raises(tmp_path: Path) -> None:
    (tmp_path / ".agentdeck").mkdir()
    with pytest.raises(MigrationError, match="no legacy"):
        _service().preview(tmp_path)


# --- slice 37.3: confirmed apply (backup / import / verify / rename) --------

import sqlite3

from agentdeck.application.migration_service import ImportOutcome


def _real_importer(target_path: Path, legacy) -> ImportOutcome:
    """A real (test) importer: build the new project row in a fresh SQLite DB."""
    connection = sqlite3.connect(target_path)
    try:
        connection.execute(
            "CREATE TABLE projects (project_id TEXT PRIMARY KEY, "
            "resolved_root TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO projects VALUES (?,?,?)",
            (legacy.project_id, legacy.resolved_root, legacy.created_at),
        )
        connection.commit()
        (integrity,) = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    return ImportOutcome(imported_counts={"projects": 1}, integrity=integrity)


def _apply_service() -> MigrationService:
    return MigrationService(legacy_reader=parse_legacy_state, db_importer=_real_importer)


def test_confirmed_migration_backs_up_verifies_and_reports(tmp_path: Path) -> None:
    project = _legacy_project(tmp_path)
    service = _apply_service()
    preview = service.preview(project)
    report = service.apply(preview.preview_id, preview.content_hash, confirm=True)
    assert report.backup_hash.startswith("sha256:")
    assert report.database_integrity == "ok"
    assert report.imported_counts["projects"] == 1
    assert report.skipped_items
    assert report.rollback_command
    assert (project / ".agentdeck" / "agentdeck.db").exists()
    assert service.authority(project) == "migrated"


def test_apply_requires_explicit_confirm(tmp_path: Path) -> None:
    project = _legacy_project(tmp_path)
    service = _apply_service()
    preview = service.preview(project)
    with pytest.raises(MigrationError, match="confirm"):
        service.apply(preview.preview_id, preview.content_hash, confirm=False)
    assert not (project / ".agentdeck" / "agentdeck.db").exists()


def test_drift_after_preview_leaves_legacy_authority(tmp_path: Path) -> None:
    project = _legacy_project(tmp_path)
    service = _apply_service()
    preview = service.preview(project)
    state_json = project / ".agentdeck" / "state.json"
    state_json.write_text(
        state_json.read_text(encoding="utf-8").replace("prj_legacy0001", "prj_x"),
        encoding="utf-8",
    )
    with pytest.raises(MigrationError, match="drift"):
        service.apply(preview.preview_id, preview.content_hash, confirm=True)
    assert not (project / ".agentdeck" / "agentdeck.db").exists()
    assert service.authority(project) == "legacy"


def test_apply_unknown_preview_id_raises(tmp_path: Path) -> None:
    _legacy_project(tmp_path)
    with pytest.raises(MigrationError, match="preview"):
        _apply_service().apply("mgp_unknown", "sha256:" + "0" * 64, confirm=True)
