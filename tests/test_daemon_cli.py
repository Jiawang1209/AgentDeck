from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import time
import tempfile

import pytest

from agentdeck import cli
from agentdeck.config import load_config, write_default_config
from agentdeck.contracts import validate_workbench_contract
from agentdeck.state import StateStore
from agentdeck.daemon.lifecycle import build_daemon_record, project_root_hash


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("name", ["daemon-runtime", "mission-scheduler", "client-session"])
def test_daemon_contract_cli_is_discoverable(name: str, capsys) -> None:
    assert cli.main(["contract", name, "--example"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["example"]["schema_version"].endswith("/v1")


def test_daemon_status_is_read_only_when_endpoint_is_absent(tmp_path: Path, monkeypatch, capsys) -> None:
    root = _project(tmp_path, monkeypatch)
    before = _tree(root)
    assert cli.main(["daemon", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "daemon-runtime/v1"
    assert payload["state"] == "stopped"
    assert _tree(root) == before


def test_daemon_status_does_not_report_stale_durable_ready_as_healthy(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _project(tmp_path, monkeypatch)
    store = StateStore(root)
    record = build_daemon_record(
        instance_id="dmn_stale", project_root_hash=project_root_hash(root),
        start_nonce="stale-nonce", state="ready",
        created_at="2026-07-13T00:00:00+00:00",
    )
    store.record_daemon_state(record, expected_project_root_hash=project_root_hash(root))
    before = _tree(root)

    assert cli.main(["daemon", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "blocked"
    assert payload["health"] == "unavailable"
    assert payload["compatibility"] == "unverified"
    assert payload["blockers"] == ["verified daemon endpoint is unavailable"]
    assert _tree(root) == before


def test_project_view_and_workbench_embed_same_source_daemon_cards(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path, monkeypatch)
    config = load_config(root)
    store = StateStore(root)
    project_view = asdict(store.project_view(config))
    assert set(project_view["daemon"]) == {
        "state", "health", "client_count", "controller_present",
        "idle_exit_pending", "protocol_version", "compatibility", "blockers",
    }
    assert set(project_view["scheduler"]) == {
        "state", "active_mission_id", "active_step", "next_transition", "blockers",
    }
    workbench = cli._workbench_snapshot_payload(project_view, store, since_event_id=None)
    assert workbench["daemon_runtime_card"]["state"] == project_view["daemon"]["state"]
    assert workbench["mission_scheduler_card"]["state"] == project_view["scheduler"]["state"]
    assert workbench["client_session_card"]["schema_version"] == "client-session/v1"
    assert validate_workbench_contract(workbench) == {"ok": True, "errors": []}


def test_daemon_logs_is_bounded_and_read_only(tmp_path: Path, monkeypatch, capsys) -> None:
    root = _project(tmp_path, monkeypatch)
    runtime = root / ".agentdeck" / "runtime"
    runtime.mkdir()
    (runtime / "daemon.stdout.log").write_text("first\nsecond\n", encoding="utf-8")
    before = _tree(root)
    assert cli.main(["daemon", "logs", "--lines", "1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stdout"] == ["second"]
    assert payload["stderr"] == []
    assert _tree(root) == before


def test_daemon_logs_reject_runtime_symlink_without_leaking_content(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _project(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "daemon.stdout.log").write_text("token=secret-value\n", encoding="utf-8")
    (root / ".agentdeck" / "runtime").symlink_to(outside, target_is_directory=True)

    assert cli.main(["daemon", "logs"]) == 1
    captured = capsys.readouterr()
    assert "secret-value" not in captured.out + captured.err


def test_daemon_stop_never_signals_an_unverified_endpoint(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _project(tmp_path, monkeypatch)
    runtime = root / ".agentdeck" / "runtime"
    runtime.mkdir()
    (runtime / "daemon.json").write_text("{}\n", encoding="utf-8")
    (runtime / "daemon.sock").write_text("not a socket\n", encoding="utf-8")
    monkeypatch.setattr(cli.os, "kill", lambda *_args: pytest.fail("must not signal"))

    assert cli.main(["daemon", "stop", "--confirm"]) == 1
    assert "verified daemon is unavailable" in capsys.readouterr().err


def test_daemon_start_status_and_confirmed_stop_use_hidden_server(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="adk6-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        monkeypatch.chdir(root)
        try:
            assert cli.main(["daemon", "start"]) == 0
            started = json.loads(capsys.readouterr().out)
            assert started["state"] == "ready"
            assert started["compatibility"] == "compatible"

            idle_deadline = time.monotonic() + 2
            while (
                StateStore(root).load()["daemon_runtime"]["state"] != "idle_grace"
                and time.monotonic() < idle_deadline
            ):
                time.sleep(0.02)
            assert StateStore(root).load()["daemon_runtime"]["state"] == "idle_grace"

            assert cli.main(["daemon", "status"]) == 0
            status = json.loads(capsys.readouterr().out)
            assert status["health"] == "healthy"

            assert cli.main(["daemon", "stop"]) == 1
            assert "requires --confirm" in capsys.readouterr().err
            assert cli.main(["daemon", "stop", "--confirm"]) == 0
            stopped = json.loads(capsys.readouterr().out)
            assert stopped == {"mode": "daemon_stop", "ok": True, "stopped": True}
            endpoint = root / ".agentdeck" / "runtime" / "daemon.sock"
            metadata = root / ".agentdeck" / "runtime" / "daemon.json"
            deadline = time.monotonic() + 3
            while (endpoint.exists() or metadata.exists()) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not endpoint.exists()
            assert not metadata.exists()
        finally:
            if (root / ".agentdeck" / "runtime" / "daemon.sock").exists():
                cli.main(["daemon", "stop", "--confirm"])
                capsys.readouterr()
                deadline = time.monotonic() + 3
                metadata = root / ".agentdeck" / "runtime" / "daemon.json"
                while metadata.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)


def test_bare_tty_connects_or_starts_daemon_before_foreground_ui(
    tmp_path: Path, monkeypatch
) -> None:
    root = _project(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    async def fake_start(start_root, config):
        assert start_root == root
        assert config.root == str(root)
        calls.append("daemon")
        return {}, True

    class FakeUI:
        def __init__(self, _session):
            calls.append("ui")

        def run(self):
            calls.append("run")
            return 0

    monkeypatch.setattr(cli, "_start_daemon", fake_start)
    monkeypatch.setattr(cli, "_foreground_conversation_session", lambda: object())
    monkeypatch.setattr(cli, "TerminalConversationUI", FakeUI)

    assert cli.main([]) == 0
    assert calls == ["daemon", "ui", "run"]
