from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore
from agentdeck.storage.fingerprint import schema_fingerprint
from agentdeck.storage.schema import (
    EXPECTED_SCHEMA_V1_FINGERPRINT,
    apply_schema_v1,
)
from agentdeck.storage.shadow import mirror_state, shadow_database_path


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def test_schema_v1_matches_pinned_fingerprint() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        apply_schema_v1(connection)
        assert schema_fingerprint(connection) == EXPECTED_SCHEMA_V1_FINGERPRINT
    finally:
        connection.close()


def test_mirror_state_roundtrip_and_update() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        apply_schema_v1(connection)
        state = {
            "agents": {"coder": {"agent_id": "coder", "status": "running"}},
            "messages": [{"message_id": "msg_1", "task": "do"}],
            "plans": [],
            "daemon_runtime": {"pid": 42},
            "controller_lease": None,
        }
        mirror_state(connection, state)
        rows = {
            (r[0], r[1]): json.loads(r[2])
            for r in connection.execute("SELECT collection, record_id, record_json FROM records")
        }
        assert rows[("agents", "coder")]["status"] == "running"
        assert rows[("messages", "msg_1")]["task"] == "do"
        singles = {
            r[0]: json.loads(r[1])
            for r in connection.execute("SELECT key, value_json FROM singletons")
        }
        assert singles["daemon_runtime"] == {"pid": 42}
        assert singles["controller_lease"] is None

        state["messages"].append({"message_id": "msg_2", "task": "next"})
        state["agents"] = {}
        mirror_state(connection, state)
        rows = {
            (r[0], r[1])
            for r in connection.execute("SELECT collection, record_id FROM records")
        }
        assert ("messages", "msg_2") in rows
        assert not any(c == "agents" for c, _ in rows)
    finally:
        connection.close()


def test_storage_shadow_enable_requires_confirm_and_writes_meta(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    assert cli.main(["storage", "shadow-enable"]) == 1
    assert "confirm" in capsys.readouterr().err
    assert not shadow_database_path(root).exists()

    assert cli.main(["storage", "shadow-enable", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "storage_shadow_enabled"
    assert payload["authority_state"] == "sqlite_installed_quarantined"
    db = shadow_database_path(root)
    assert db.exists()
    connection = sqlite3.connect(db)
    try:
        meta = dict(connection.execute("SELECT key, value FROM meta"))
    finally:
        connection.close()
    assert meta["authority_state"] == "sqlite_installed_quarantined"
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "storage_shadow_enabled"' in events

    # 重复 enable 拒绝零写
    assert cli.main(["storage", "shadow-enable", "--confirm"]) == 1
    assert "already" in capsys.readouterr().err


def test_save_mirrors_into_shadow_db_and_rm_rolls_back(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["storage", "shadow-enable", "--confirm"])
    capsys.readouterr()

    store = StateStore(root)
    state = store.load()
    state["plans"].append({"plan_id": "pln_shadow", "task": "g", "status": "planned"})
    store.save(state)

    connection = sqlite3.connect(shadow_database_path(root))
    try:
        row = connection.execute(
            "SELECT record_json FROM records WHERE collection='plans' AND record_id='pln_shadow'"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert json.loads(row[0])["task"] == "g"

    # 回滚：rm state.db 后 JSON 写路径不受任何影响
    shadow_database_path(root).unlink()
    state = store.load()
    state["plans"].append({"plan_id": "pln_after_rm", "task": "h", "status": "planned"})
    store.save(state)
    assert any(p["plan_id"] == "pln_after_rm" for p in store.load()["plans"])


def test_storage_shadow_status_read_only(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    assert cli.main(["storage", "shadow-status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "storage_shadow_status"
    assert payload["enabled"] is False

    cli.main(["storage", "shadow-enable", "--confirm"])
    capsys.readouterr()
    store = StateStore(root)
    state = store.load()
    state["messages"].append({"message_id": "msg_s", "task": "t"})
    store.save(state)
    before = StateStore(root).load()

    assert cli.main(["storage", "shadow-status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled"] is True
    assert payload["authority_state"] == "sqlite_installed_quarantined"
    assert payload["fingerprint_ok"] is True
    assert payload["record_counts"]["messages"] == 1
    assert StateStore(root).load() == before
