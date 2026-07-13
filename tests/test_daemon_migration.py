from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from agentdeck.config import write_default_config
from agentdeck import state as state_module
from agentdeck import cli
from agentdeck.state import StateStore


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _seed_m1_state_without_execution_snapshot(root: Path) -> None:
    (root / ".git").mkdir()
    write_default_config(root)
    store = StateStore(root)
    state = store.load()
    state["missions"] = [
        {
            "mission_id": "mis_131313131313",
            "schema_version": "mission/v1",
            "status": "interrupted",
            "current_step": 1,
            "step_count": 2,
            "execution_snapshot": None,
            "snapshot_hash": None,
        }
    ]
    store.save(state)


def test_old_mission_migration_preview_is_zero_write(tmp_path: Path) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    before = _tree_bytes(tmp_path)

    preview = state_module.migration_preview(tmp_path)

    assert preview["legacy_missions"][0]["mode"] == "inspect_only"
    assert _tree_bytes(tmp_path) == before


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


def test_migration_preview_binds_exact_source_changes_backup_and_expiry(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    state_path = tmp_path / ".agentdeck" / "state" / "state.json"
    preview = state_module.migration_preview(tmp_path, now=NOW)

    assert preview["source_hash"] == "sha256:" + hashlib.sha256(
        state_path.read_bytes()
    ).hexdigest()
    assert preview["backup_path"].startswith(".agentdeck/backups/mig_")
    assert not Path(str(preview["backup_path"])).is_absolute()
    assert preview["consume_once"] is True
    assert preview["expires_at"] == (NOW + timedelta(minutes=10)).isoformat()
    assert preview["digest"].startswith("sha256:")
    assert preview["confirm_command"].endswith(" --confirm")
    assert preview["legacy_missions"] == [
        {
            "mission_id": "mis_131313131313",
            "mode": "inspect_only",
            "reason": "complete frozen execution authority is unavailable",
            "inspect_command": (
                "agentdeck mission status --mission-id mis_131313131313"
            ),
            "reconfirm_command": (
                "agentdeck leader chat --message \"Reconfirm legacy Mission "
                "mis_131313131313 as a new Mission preview\""
            ),
        }
    ]
    assert all(item["operation"] == "add" for item in preview["target_changes"])


def _confirm(root: Path, preview: dict[str, object], *, now: datetime = NOW):
    return state_module.confirm_migration(
        root,
        preview_id=str(preview["preview_id"]),
        source_hash=str(preview["source_hash"]),
        digest=str(preview["digest"]),
        expires_at=str(preview["expires_at"]),
        confirm=True,
        now=now,
    )


def test_confirmed_migration_is_additive_backed_up_and_consumed_once(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    state_path = tmp_path / ".agentdeck" / "state" / "state.json"
    source = state_path.read_bytes()
    before_state = json.loads(source)
    before_state["controller_lease"] = {
        "lease_id": "SECRET-RUNTIME-LEASE",
        "state": "active",
    }
    state_path.write_text(
        json.dumps(before_state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    source = state_path.read_bytes()
    preview = state_module.migration_preview(tmp_path, now=NOW)

    result = _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    backup = tmp_path / str(preview["backup_path"])
    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert result["mode"] == "migration_confirmed"
    backup_payload = json.loads(backup.read_text(encoding="utf-8"))
    assert backup_payload["source_hash"] == preview["source_hash"]
    assert backup_payload["affected_state"] == {
        str(item["path"]): {"present": False}
        for item in preview["target_changes"]
    }
    assert "SECRET-RUNTIME-LEASE" not in backup.read_text(encoding="utf-8")
    assert "execution_snapshot" not in backup.read_text(encoding="utf-8")
    assert after["missions"] == before_state["missions"]
    assert after["legacy_mission_migrations"][0]["mode"] == "inspect_only"
    assert after["migration_previews_consumed"][0]["preview_id"] == preview["preview_id"]
    assert "execution_snapshot" not in after["legacy_mission_migrations"][0]
    with pytest.raises(ValueError, match="consumed"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=2))


@pytest.mark.parametrize("mutation", ["unknown", "expired", "drift"])
def test_invalid_migration_confirmation_is_zero_write(
    tmp_path: Path, mutation: str
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    kwargs = {
        "preview_id": str(preview["preview_id"]),
        "source_hash": str(preview["source_hash"]),
        "digest": str(preview["digest"]),
        "expires_at": str(preview["expires_at"]),
        "confirm": True,
        "now": NOW + timedelta(seconds=1),
    }
    if mutation == "unknown":
        kwargs["preview_id"] = "mig_ffffffffffff"
    elif mutation == "expired":
        kwargs["now"] = NOW + timedelta(minutes=11)
    else:
        state_path = tmp_path / ".agentdeck" / "state" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["drift"] = True
        state_path.write_text(json.dumps(state), encoding="utf-8")
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError):
        state_module.confirm_migration(tmp_path, **kwargs)

    assert _tree_bytes(tmp_path) == before


def test_migration_save_failure_removes_backup_and_leaves_source_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    before = _tree_bytes(tmp_path)
    monkeypatch.setattr(
        StateStore,
        "_atomic_save",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert _tree_bytes(tmp_path) == before


def test_existing_backup_is_never_overwritten_and_state_stays_unchanged(
    tmp_path: Path,
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    backup = tmp_path / str(preview["backup_path"])
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"existing backup\n")
    before = _tree_bytes(tmp_path)

    with pytest.raises(ValueError, match="backup already exists"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert _tree_bytes(tmp_path) == before


def test_post_replace_save_failure_rolls_back_state_before_removing_backup(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    preview = state_module.migration_preview(tmp_path, now=NOW)
    before = _tree_bytes(tmp_path)
    original = StateStore._atomic_save

    def replace_then_fail(store: StateStore, state: dict[str, object]) -> None:
        original(store, state)
        raise OSError("post replace failure")

    monkeypatch.setattr(StateStore, "_atomic_save", replace_then_fail)

    with pytest.raises(OSError, match="post replace failure"):
        _confirm(tmp_path, preview, now=NOW + timedelta(seconds=1))

    assert _tree_bytes(tmp_path) == before


def test_migration_cli_separates_read_only_preview_from_exact_confirm(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    monkeypatch.setattr(cli, "project_root", lambda: tmp_path)
    before = _tree_bytes(tmp_path)

    assert cli.main(["project", "migration-preview"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "migration_preview"
    assert _tree_bytes(tmp_path) == before

    assert cli.main(["project", "migrate"]) == 1
    assert "exact confirmation" in capsys.readouterr().err
    assert _tree_bytes(tmp_path) == before


def test_project_view_and_workbench_contracts_discover_mission_recovery_card(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _seed_m1_state_without_execution_snapshot(tmp_path)
    monkeypatch.setattr(cli, "project_root", lambda: tmp_path)

    assert cli.main(["contract", "project-view", "--example"]) == 0
    project_contract = json.loads(capsys.readouterr().out)
    assert "classification" in project_contract["mission_recovery_fields"]
    assert "decision" in project_contract["mission_recovery_fields"]
    assert project_contract["example_project_view"]["mission_recovery"]["mode"] == (
        "mission_recovery"
    )

    assert cli.main(["contract", "workbench", "--example"]) == 0
    workbench_contract = json.loads(capsys.readouterr().out)
    assert "mission_recovery_card" in workbench_contract["snapshot_fields"]
    assert workbench_contract["example_workbench"]["mission_recovery_card"] == (
        workbench_contract["example_workbench"]["project_view"]["mission_recovery"]
    )
