from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.models import EventRecord
from agentdeck.state import StateStore
from agentdeck.storage.shadow import shadow_database_path


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def _append(store: StateStore, event_type: str, payload: dict[str, object]) -> None:
    store.append_event(EventRecord.create(event_type, payload))


def _meta_authority(root: Path) -> str | None:
    connection = sqlite3.connect(shadow_database_path(root))
    try:
        rows = dict(connection.execute("SELECT key, value FROM meta"))
        return rows.get("events_authority")
    finally:
        connection.close()


def _journal_text(root: Path) -> str:
    return (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")


def test_cutover_requires_confirm_and_enabled_shadow(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    assert cli.main(["storage", "events-cutover"]) == 1
    assert "requires --confirm" in capsys.readouterr().err

    assert cli.main(["storage", "events-cutover", "--confirm"]) == 1
    assert "not enabled" in capsys.readouterr().err


def test_cutover_backfills_flips_authority_and_keeps_reads_identical(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    _append(store, "pre_enable_one", {"n": 1})
    _append(store, "pre_enable_two", {"n": 2, "text": "中文"})
    assert cli.main(["storage", "shadow-enable", "--confirm"]) == 0
    capsys.readouterr()
    _append(store, "post_enable_one", {"n": 3})
    events_before = store.list_events(limit=100)
    journal_before = _journal_text(root)

    assert cli.main(["storage", "events-cutover", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "storage_events_cutover"
    assert payload["authority"] == "sqlite"
    assert payload["total_events"] >= 4  # 3 appended + shadow_enabled
    assert _meta_authority(root) == "sqlite"

    # Reads now rebuild from the table and must include the cutover event on top
    # of an otherwise identical stream.
    events_after = store.list_events(limit=100)
    after_types = [event["event_type"] for event in events_after]
    assert "storage_events_cutover" in after_types
    filtered = [e for e in events_after if e["event_type"] != "storage_events_cutover"]
    assert filtered == events_before


def test_post_cutover_append_uses_table_authority_and_exports_journal(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    assert cli.main(["storage", "shadow-enable", "--confirm"]) == 0
    capsys.readouterr()
    assert cli.main(["storage", "events-cutover", "--confirm"]) == 0
    capsys.readouterr()

    _append(store, "after_cutover", {"marker": "live"})

    # Table is authoritative and serves reads.
    types = [event["event_type"] for event in store.list_events(limit=50)]
    assert "after_cutover" in types
    # The synchronous export keeps events.jsonl byte-readable with the same line.
    assert '"after_cutover"' in _journal_text(root)
    # events-diff must remain in sync (journal mirrors the table).
    assert cli.main(["storage", "events-diff"]) == 0
    diff = json.loads(capsys.readouterr().out)
    assert diff["in_sync"] is True
    # No export errors were logged.
    assert not (root / ".agentdeck" / "logs" / "shadow-errors.jsonl").exists()


def test_rollback_flips_back_to_journal_authority(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    assert cli.main(["storage", "shadow-enable", "--confirm"]) == 0
    capsys.readouterr()
    assert cli.main(["storage", "events-cutover", "--confirm"]) == 0
    capsys.readouterr()
    _append(store, "while_sqlite", {"n": 1})

    assert cli.main(["storage", "events-rollback", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["authority"] == "journal"
    assert _meta_authority(root) == "journal"

    _append(store, "after_rollback", {"n": 2})
    types = [event["event_type"] for event in store.list_events(limit=50)]
    assert "while_sqlite" in types
    assert "after_rollback" in types
    assert "storage_events_rollback" in types


def test_rollback_requires_sqlite_authority(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    assert cli.main(["storage", "shadow-enable", "--confirm"]) == 0
    capsys.readouterr()

    assert cli.main(["storage", "events-rollback", "--confirm"]) == 1
    assert "authority" in capsys.readouterr().err


def test_shadow_status_exposes_events_authority(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    assert cli.main(["storage", "shadow-enable", "--confirm"]) == 0
    capsys.readouterr()

    assert cli.main(["storage", "shadow-status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["events_authority"] == "journal"

    assert cli.main(["storage", "events-cutover", "--confirm"]) == 0
    capsys.readouterr()
    assert cli.main(["storage", "shadow-status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["events_authority"] == "sqlite"
