"""Task 37 slice 4 — `_product migrate preview|apply` CLI with a real importer.

Deterministic end-to-end over a local legacy fixture (no provider/ACP/tmux):
preview writes nothing; a confirmed apply builds a real migrated SQLite DB with
the imported project and reports integrity.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from agentdeck.entrypoint import main

_FIXTURE = Path(__file__).parent / "fixtures" / "legacy_state"


def _legacy_project(tmp_path: Path) -> Path:
    agentdeck = tmp_path / ".agentdeck"
    agentdeck.mkdir()
    shutil.copy(_FIXTURE / "state.json", agentdeck / "state.json")
    shutil.copy(_FIXTURE / "events.jsonl", agentdeck / "events.jsonl")
    return tmp_path


def test_migrate_preview_writes_nothing(tmp_path, capsys) -> None:
    project = _legacy_project(tmp_path)
    assert main(["_product", "migrate", "preview", "--project", str(project), "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["requires_confirmation"] is True
    assert preview["mappings"]["projects"] == 1
    assert not (project / ".agentdeck" / "agentdeck.db").exists()


def test_migrate_apply_requires_confirm(tmp_path, capsys) -> None:
    project = _legacy_project(tmp_path)
    assert main(["_product", "migrate", "preview", "--project", str(project), "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    code = main([
        "_product", "migrate", "apply", "--project", str(project),
        "--preview-id", preview["preview_id"],
        "--content-hash", preview["content_hash"],
    ])
    assert code == 2
    assert not (project / ".agentdeck" / "agentdeck.db").exists()


def test_migrate_apply_builds_migrated_db(tmp_path, capsys) -> None:
    project = _legacy_project(tmp_path)
    assert main(["_product", "migrate", "preview", "--project", str(project), "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    code = main([
        "_product", "migrate", "apply", "--project", str(project),
        "--preview-id", preview["preview_id"],
        "--content-hash", preview["content_hash"], "--confirm",
    ])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["database_integrity"] == "ok"
    assert report["imported_counts"]["projects"] == 1
    assert report["backup_hash"].startswith("sha256:")
    assert (project / ".agentdeck" / "agentdeck.db").exists()


def test_migrate_apply_rejects_wrong_hash(tmp_path, capsys) -> None:
    project = _legacy_project(tmp_path)
    assert main(["_product", "migrate", "preview", "--project", str(project), "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    code = main([
        "_product", "migrate", "apply", "--project", str(project),
        "--preview-id", preview["preview_id"],
        "--content-hash", "sha256:" + "0" * 64, "--confirm",
    ])
    assert code == 1
    assert not (project / ".agentdeck" / "agentdeck.db").exists()
