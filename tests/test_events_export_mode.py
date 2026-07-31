from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.models import EventRecord
from agentdeck.state import StateStore
from agentdeck.storage.shadow import (
    events_export_mode,
    journal_bytes_from_table,
    shadow_database_path,
)


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def _append(store: StateStore, event_type: str, payload: dict[str, object]) -> None:
    store.append_event(EventRecord.create(event_type, payload))


def _meta_value(root: Path, key: str) -> str | None:
    connection = sqlite3.connect(shadow_database_path(root))
    try:
        rows = dict(connection.execute("SELECT key, value FROM meta"))
        return rows.get(key)
    finally:
        connection.close()


def _journal_text(root: Path) -> str:
    return (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")


def _enable_and_cutover(capsys) -> None:
    assert cli.main(["storage", "shadow-enable", "--confirm"]) == 0
    capsys.readouterr()
    assert cli.main(["storage", "events-cutover", "--confirm"]) == 0
    capsys.readouterr()


def _switch_mode(mode: str, capsys) -> None:
    assert cli.main(["storage", "events-export-mode", "--mode", mode, "--confirm"]) == 0
    capsys.readouterr()


def test_export_mode_defaults_to_sync_and_fails_safe(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    # No shadow database at all: default sync.
    assert events_export_mode(root) == "sync"
    # Corrupt database file: fail-safe sync, never an exception.
    database = shadow_database_path(root)
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_bytes(b"this is not a sqlite database")
    assert events_export_mode(root) == "sync"
    database.unlink()
    # Enabled shadow without the meta key: still sync.
    assert cli.main(["storage", "shadow-enable", "--confirm"]) == 0
    capsys.readouterr()
    assert events_export_mode(root) == "sync"
    # Garbage meta value: fail-safe sync.
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('events_export_mode', 'garbage')"
        )
        connection.commit()
    finally:
        connection.close()
    assert events_export_mode(root) == "sync"


def test_export_mode_switch_gate_matrix(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)

    # Missing --confirm refuses.
    assert cli.main(["storage", "events-export-mode", "--mode", "on_demand"]) == 1
    assert "requires --confirm" in capsys.readouterr().err
    # No shadow database refuses.
    assert (
        cli.main(["storage", "events-export-mode", "--mode", "on_demand", "--confirm"]) == 1
    )
    assert "not enabled" in capsys.readouterr().err

    assert cli.main(["storage", "shadow-enable", "--confirm"]) == 0
    capsys.readouterr()
    # Journal authority refuses (zero write).
    assert (
        cli.main(["storage", "events-export-mode", "--mode", "on_demand", "--confirm"]) == 1
    )
    assert "authority" in capsys.readouterr().err
    assert events_export_mode(root) == "sync"
    assert _meta_value(root, "events_export_mode") is None

    assert cli.main(["storage", "events-cutover", "--confirm"]) == 0
    capsys.readouterr()
    # Same-value repeat refuses (sync -> sync).
    assert cli.main(["storage", "events-export-mode", "--mode", "sync", "--confirm"]) == 1
    assert "already" in capsys.readouterr().err
    assert _meta_value(root, "events_export_mode") is None

    # Switch to on_demand flips meta and appends an audit event.
    assert (
        cli.main(["storage", "events-export-mode", "--mode", "on_demand", "--confirm"]) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "storage_events_export_mode_updated"
    assert payload["events_export_mode"] == "on_demand"
    assert _meta_value(root, "events_export_mode") == "on_demand"
    types = [event["event_type"] for event in store.list_events(limit=100)]
    assert "storage_events_export_mode_updated" in types

    # Same-value repeat refuses (on_demand -> on_demand).
    assert (
        cli.main(["storage", "events-export-mode", "--mode", "on_demand", "--confirm"]) == 1
    )
    assert "already" in capsys.readouterr().err

    # While on_demand the journal file goes stale.
    _append(store, "stale_one", {"n": 1})
    assert '"stale_one"' not in _journal_text(root)

    # Switch back to sync forces a full export before flipping.
    assert cli.main(["storage", "events-export-mode", "--mode", "sync", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["events_export_mode"] == "sync"
    assert _meta_value(root, "events_export_mode") == "sync"
    assert '"stale_one"' in _journal_text(root)
    # After the flip the audit event goes through the sync export, so the file
    # is byte-identical to the authoritative table stream.
    assert _journal_text(root).encode("utf-8") == journal_bytes_from_table(root)


def test_on_demand_append_skips_journal_export_sync_stays_identical(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    _enable_and_cutover(capsys)

    # Regression pin: sync mode keeps the file byte-identical to the table.
    _append(store, "sync_event", {"n": 1})
    assert _journal_text(root).encode("utf-8") == journal_bytes_from_table(root)

    _switch_mode("on_demand", capsys)
    journal_before = _journal_text(root)
    _append(store, "od_event", {"n": 2})
    # The table (authority) has the event; the file does not move.
    types = [event["event_type"] for event in store.list_events(limit=100)]
    assert "od_event" in types
    assert _journal_text(root) == journal_before
    # No export errors were logged: the skip is deliberate, not a failure.
    assert not (root / ".agentdeck" / "logs" / "shadow-errors.jsonl").exists()


def test_events_export_command_rebuilds_file(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)

    # Missing --confirm refuses.
    assert cli.main(["storage", "events-export"]) == 1
    assert "requires --confirm" in capsys.readouterr().err
    # No shadow database refuses.
    assert cli.main(["storage", "events-export", "--confirm"]) == 1
    assert "not enabled" in capsys.readouterr().err

    assert cli.main(["storage", "shadow-enable", "--confirm"]) == 0
    capsys.readouterr()
    # Journal authority refuses (export is meaningless), zero write.
    journal_before = _journal_text(root)
    assert cli.main(["storage", "events-export", "--confirm"]) == 1
    assert "authority" in capsys.readouterr().err
    assert _journal_text(root) == journal_before

    assert cli.main(["storage", "events-cutover", "--confirm"]) == 0
    capsys.readouterr()
    _switch_mode("on_demand", capsys)
    _append(store, "pending_one", {"n": 1})
    _append(store, "pending_two", {"n": 2})
    assert '"pending_one"' not in _journal_text(root)

    assert cli.main(["storage", "events-export", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "storage_events_exported"
    file_bytes = _journal_text(root).encode("utf-8")
    stream = journal_bytes_from_table(root)
    # The exported file is byte-identical to the table stream at export time;
    # the storage_events_exported audit event is appended afterwards and (in
    # on_demand mode) is the only lagging line.
    assert stream.startswith(file_bytes)
    lagging = stream[len(file_bytes):].decode("utf-8").splitlines()
    assert len(lagging) == 1
    assert "storage_events_exported" in lagging[0]
    assert '"pending_one"' in _journal_text(root)
    assert '"pending_two"' in _journal_text(root)


def test_events_diff_on_demand_prefix_lag_and_drift(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    _enable_and_cutover(capsys)

    # Regression pin: sync behaviour unchanged (no export_lag field).
    assert cli.main(["storage", "events-diff"]) == 0
    diff = json.loads(capsys.readouterr().out)
    assert diff["in_sync"] is True
    assert "export_lag" not in diff

    _switch_mode("on_demand", capsys)
    _append(store, "lag_one", {"n": 1})
    _append(store, "lag_two", {"n": 2})
    _append(store, "lag_three", {"n": 3})

    assert cli.main(["storage", "events-diff"]) == 0
    diff = json.loads(capsys.readouterr().out)
    assert diff["in_sync"] is True
    # Lag = mode-switch audit event + the three appends.
    assert diff["export_lag"] == 4
    assert diff["events_export_mode"] == "on_demand"

    # Corrupt a line inside the overlap: prefix comparison must flag drift.
    journal_path = root / ".agentdeck" / "state" / "events.jsonl"
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["event_type"] = "tampered_event"
    lines[0] = json.dumps(tampered, ensure_ascii=False, sort_keys=True)
    journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert cli.main(["storage", "events-diff"]) == 1
    diff = json.loads(capsys.readouterr().out)
    assert diff["in_sync"] is False
    assert diff["mismatches"]


def test_rollback_under_on_demand_exports_first_and_resets_mode(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    _enable_and_cutover(capsys)
    _switch_mode("on_demand", capsys)
    _append(store, "od_pending", {"n": 1})
    assert '"od_pending"' not in _journal_text(root)

    assert cli.main(["storage", "events-rollback", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["authority"] == "journal"
    assert _meta_value(root, "events_authority") == "journal"
    # Mode is reset: on-demand export does not exist under journal authority.
    assert _meta_value(root, "events_export_mode") == "sync"
    assert events_export_mode(root) == "sync"
    # The forced export landed the pending event before the flip.
    assert '"od_pending"' in _journal_text(root)

    # Full round trip: journal appends work again.
    _append(store, "after_rollback", {"n": 2})
    types = [event["event_type"] for event in store.list_events(limit=100)]
    assert "od_pending" in types
    assert "after_rollback" in types
    assert "storage_events_rollback" in types
    assert '"after_rollback"' in _journal_text(root)


def test_shadow_status_exposes_export_mode_and_on_demand_warning(
    tmp_path, monkeypatch, capsys
) -> None:
    prepare_project(tmp_path, monkeypatch)
    _enable_and_cutover(capsys)

    assert cli.main(["storage", "shadow-status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["events_export_mode"] == "sync"
    assert "events_export_warning" not in status

    _switch_mode("on_demand", capsys)
    assert cli.main(["storage", "shadow-status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["events_export_mode"] == "on_demand"
    warning = status["events_export_warning"]
    assert "not lossless" in warning
    assert "events-export" in warning
