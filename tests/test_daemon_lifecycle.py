from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.daemon.lifecycle import (
    DAEMON_STATES,
    build_daemon_record,
    validate_daemon_record,
)
from agentdeck.models import DaemonConfig
from agentdeck.state import StateStore


CREATED_AT = "2026-07-13T10:00:00+00:00"


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | None, int]]:
    return {
        str(path.relative_to(root)): (
            "dir" if path.is_dir() else "file",
            path.read_bytes() if path.is_file() else None,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
    }


def _valid_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "instance_id": "dmn_123",
        "project_root_hash": "project-hash",
        "start_nonce_hash": hashlib.sha256(b"nonce-value").hexdigest(),
        "state": "starting",
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }
    record.update(overrides)
    return record


def test_daemon_config_defaults_are_compact_and_stable(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)

    assert load_config(root).daemon == DaemonConfig(
        idle_grace_seconds=600,
        start_timeout_seconds=10,
        controller_ttl_seconds=30,
        max_frame_bytes=1024 * 1024,
    )


def test_daemon_config_loads_bounded_custom_values(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    config_path = write_default_config(root)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n[daemon]\n"
        + "idle_grace_seconds = 1\n"
        + "start_timeout_seconds = 300\n"
        + "controller_ttl_seconds = 3600\n"
        + "max_frame_bytes = 16777216\n",
        encoding="utf-8",
    )

    assert load_config(root).daemon == DaemonConfig(
        idle_grace_seconds=1,
        start_timeout_seconds=300,
        controller_ttl_seconds=3600,
        max_frame_bytes=16 * 1024 * 1024,
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("idle_grace_seconds", "true"),
        ("idle_grace_seconds", "0"),
        ("idle_grace_seconds", "86401"),
        ("start_timeout_seconds", '"10"'),
        ("start_timeout_seconds", "0"),
        ("start_timeout_seconds", "301"),
        ("controller_ttl_seconds", "1.5"),
        ("controller_ttl_seconds", "0"),
        ("controller_ttl_seconds", "3601"),
        ("max_frame_bytes", "true"),
        ("max_frame_bytes", "1023"),
        ("max_frame_bytes", "16777217"),
    ],
)
def test_daemon_config_rejects_invalid_types_and_ranges(
    tmp_path: Path, name: str, value: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    config_path = write_default_config(root)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + f"\n[daemon]\n{name} = {value}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=rf"daemon {name}"):
        load_config(root)


def test_daemon_config_rejects_non_table_section(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    config_path = write_default_config(root)
    config_path.write_text(
        "daemon = 1\n" + config_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="daemon configuration"):
        load_config(root)


def test_build_daemon_record_hashes_nonce_and_validates_compact_shape() -> None:
    record = build_daemon_record(
        instance_id="dmn_123",
        project_root_hash="project-hash",
        start_nonce="nonce-value",
        state="starting",
        created_at=CREATED_AT,
    )

    assert record == _valid_record()
    assert "start_nonce" not in record
    assert validate_daemon_record(record) == record


@pytest.mark.parametrize("state", sorted(DAEMON_STATES))
def test_daemon_record_accepts_each_declared_state(state: str) -> None:
    assert validate_daemon_record(_valid_record(state=state))["state"] == state


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"instance_id": ""}, "instance_id"),
        ({"instance_id": True}, "instance_id"),
        ({"project_root_hash": ""}, "project_root_hash"),
        ({"start_nonce_hash": "raw-nonce"}, "start_nonce_hash"),
        ({"start_nonce_hash": "A" * 64}, "start_nonce_hash"),
        ({"state": "running"}, "state"),
        ({"created_at": "2026-07-13T10:00:00"}, "created_at"),
        ({"updated_at": "not-a-timestamp"}, "updated_at"),
    ],
)
def test_daemon_record_rejects_invalid_compact_fields(
    overrides: dict[str, object], match: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=match):
        validate_daemon_record(_valid_record(**overrides))


def test_daemon_record_rejects_missing_and_unknown_fields() -> None:
    missing = _valid_record()
    missing.pop("state")
    with pytest.raises(ValueError, match="fields"):
        validate_daemon_record(missing)

    with pytest.raises(ValueError, match="fields"):
        validate_daemon_record(_valid_record(pid=1234))


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("instance_id", ""),
        ("project_root_hash", ""),
        ("start_nonce", ""),
        ("state", "running"),
        ("created_at", "2026-07-13T10:00:00"),
    ],
)
def test_build_daemon_record_rejects_invalid_inputs(argument: str, value: str) -> None:
    inputs = {
        "instance_id": "dmn_123",
        "project_root_hash": "project-hash",
        "start_nonce": "nonce-value",
        "state": "starting",
        "created_at": CREATED_AT,
    }
    inputs[argument] = value

    with pytest.raises((TypeError, ValueError)):
        build_daemon_record(**inputs)


def test_new_state_has_additive_daemon_runtime_slot(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "repo")

    assert store.load()["daemon_runtime"] is None


def test_record_daemon_state_rejects_project_drift_without_any_write(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    store = StateStore(root)
    state = store.load()
    state["existing"] = {"preserved": True}
    store.save(state)
    before = _tree_snapshot(root)

    with pytest.raises(ValueError, match="project identity mismatch"):
        store.record_daemon_state(
            _valid_record(project_root_hash="other-project"),
            expected_project_root_hash="expected-project",
        )

    assert _tree_snapshot(root) == before


def test_record_daemon_state_validates_before_any_write(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    store = StateStore(root)
    store.save(store.load())
    before = _tree_snapshot(root)

    with pytest.raises(ValueError, match="fields"):
        store.record_daemon_state(
            _valid_record(pid=1234),
            expected_project_root_hash="project-hash",
        )

    assert _tree_snapshot(root) == before


def test_record_daemon_state_atomically_replaces_compact_runtime_record(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    store = StateStore(root)
    record = _valid_record(state="ready")

    result = store.record_daemon_state(
        record,
        expected_project_root_hash="project-hash",
    )

    assert result == record
    assert result is not record
    assert store.load()["daemon_runtime"] == record
    assert not list((root / ".agentdeck" / "state").glob("*.tmp"))
    assert not (root / ".agentdeck" / "state" / "events.jsonl").read_text(
        encoding="utf-8"
    )
    assert datetime.fromisoformat(result["updated_at"]).tzinfo == timezone.utc
