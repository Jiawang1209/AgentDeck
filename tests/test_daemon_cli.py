from __future__ import annotations

from dataclasses import asdict
import asyncio
import json
import os
from pathlib import Path
import time
import tempfile
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from agentdeck import cli
import agentdeck.daemon.client as daemon_client_module
from agentdeck.config import load_config, write_default_config
from agentdeck.contracts import validate_workbench_contract
from agentdeck.state import StateStore
from agentdeck.daemon.lifecycle import (
    acquire_daemon_ownership,
    build_daemon_record,
    cleanup_daemon_endpoint,
    project_root_hash,
)
from agentdeck.daemon.client import DaemonClient
from agentdeck.daemon.lease import LeaseError, grant_controller
from agentdeck.daemon.server import DaemonClientRequestError


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


def _wait_for_process_exit(pid: int, *, timeout_seconds: float = 3) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail("daemon child was not reaped before the test completed")


def _wait_for_reaper_empty(*, timeout_seconds: float = 3) -> None:
    deadline = time.monotonic() + timeout_seconds
    while (
        daemon_client_module._detached_reaper_count()
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert daemon_client_module._detached_reaper_count() == 0


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


def test_daemon_status_is_zero_write_for_config_only_partial_project(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _project(tmp_path, monkeypatch)
    for path in tuple((root / ".agentdeck").iterdir()):
        if path.name == "config.toml":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    state_dir = root / ".agentdeck" / "state"
    assert not state_dir.exists()
    before = _tree(root)

    assert cli.main(["daemon", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "stopped"
    assert _tree(root) == before
    assert not state_dir.exists()


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

    assert cli.main([
        "daemon", "stop", "--confirm", "--lease-id", "lse_" + "0" * 24,
        "--lease-generation", "1",
    ]) == 1
    assert "verified daemon is unavailable" in capsys.readouterr().err


def test_daemon_stop_requires_explicit_lease_options_as_a_pair(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _project(tmp_path, monkeypatch)
    before = _tree(root)

    assert cli.main([
        "daemon", "stop", "--confirm", "--lease-id", "lse_" + "0" * 24,
    ]) == 1
    assert "must be provided together" in capsys.readouterr().err
    assert _tree(root) == before


def test_daemon_stop_rejects_expired_controller_lease(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path, monkeypatch)
    issued_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    transition = grant_controller(
        client_id="client-test", now=issued_at, ttl_seconds=1
    )
    store = StateStore(root)
    store.commit_controller_lease(transition)

    with pytest.raises(LeaseError, match="controller lease expired"):
        cli._validate_daemon_controller_lease(
            store,
            transition.current.lease_id,
            transition.current.generation,
            now=issued_at + timedelta(seconds=2),
        )


@pytest.mark.parametrize(
    ("state_update", "other_client_count"),
    [
        ({"approvals": [{"status": "pending"}]}, 0),
        ({}, 1),
    ],
)
def test_daemon_stop_rejects_active_keepalive_work(
    tmp_path: Path,
    monkeypatch,
    state_update: dict[str, object],
    other_client_count: int,
) -> None:
    root = _project(tmp_path, monkeypatch)
    ownership = acquire_daemon_ownership(
        root,
        start_nonce="test-stop-gate",
        health_probe=lambda _metadata: None,
    )
    try:
        store = StateStore(root)
        now = datetime.now(timezone.utc).isoformat()
        store.record_daemon_state(
            {
                "instance_id": ownership.instance_id,
                "project_root_hash": ownership.project_root_hash,
                "start_nonce_hash": ownership.start_nonce_hash,
                "state": "ready",
                "created_at": now,
                "updated_at": now,
            },
            expected_project_root_hash=ownership.project_root_hash,
        )
        state = store.load()
        state.update(state_update)
        store.save(state)

        with pytest.raises(
            DaemonClientRequestError, match="daemon has active keepalive work"
        ):
            cli._validate_daemon_stop_gate(
                root,
                ownership,
                store,
                other_client_count=other_client_count,
            )
    finally:
        cleanup_daemon_endpoint(ownership)


def test_daemon_stop_rejects_persisted_identity_drift(
    tmp_path: Path, monkeypatch
) -> None:
    root = _project(tmp_path, monkeypatch)
    ownership = acquire_daemon_ownership(
        root,
        start_nonce="test-identity-drift",
        health_probe=lambda _metadata: None,
    )
    try:
        store = StateStore(root)
        now = datetime.now(timezone.utc).isoformat()
        store.record_daemon_state(
            {
                "instance_id": "dmn_different",
                "project_root_hash": ownership.project_root_hash,
                "start_nonce_hash": ownership.start_nonce_hash,
                "state": "ready",
                "created_at": now,
                "updated_at": now,
            },
            expected_project_root_hash=ownership.project_root_hash,
        )

        with pytest.raises(
            DaemonClientRequestError, match="daemon identity is unverified"
        ):
            cli._validate_daemon_stop_gate(
                root, ownership, store, other_client_count=0
            )
    finally:
        cleanup_daemon_endpoint(ownership)


def test_daemon_stop_fails_closed_on_malformed_keepalive_state(
    tmp_path: Path, monkeypatch
) -> None:
    root = _project(tmp_path, monkeypatch)
    ownership = acquire_daemon_ownership(
        root,
        start_nonce="test-malformed-keepalive",
        health_probe=lambda _metadata: None,
    )
    try:
        store = StateStore(root)
        now = datetime.now(timezone.utc).isoformat()
        store.record_daemon_state(
            {
                "instance_id": ownership.instance_id,
                "project_root_hash": ownership.project_root_hash,
                "start_nonce_hash": ownership.start_nonce_hash,
                "state": "ready",
                "created_at": now,
                "updated_at": now,
            },
            expected_project_root_hash=ownership.project_root_hash,
        )
        state = store.load()
        state["approvals"] = {}
        store.save(state)

        with pytest.raises(
            DaemonClientRequestError, match="daemon keepalive state is invalid"
        ):
            cli._validate_daemon_stop_gate(
                root, ownership, store, other_client_count=0
            )
    finally:
        cleanup_daemon_endpoint(ownership)


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
            daemon_pid = int(json.loads(
                (root / ".agentdeck" / "runtime" / "daemon.json").read_text(
                    encoding="utf-8"
                )
            )["pid"])
            assert started["state"] == "ready"
            assert started["compatibility"] == "compatible"

            idle_deadline = time.monotonic() + 2
            while (
                StateStore(root).load()["daemon_runtime"]["state"] != "idle_grace"
                and time.monotonic() < idle_deadline
            ):
                time.sleep(0.02)
            assert StateStore(root).load()["daemon_runtime"]["state"] == "idle_grace"

            before_status = _tree(root)
            assert cli.main(["daemon", "status"]) == 0
            status = json.loads(capsys.readouterr().out)
            assert status["health"] == "unknown"
            assert status["compatibility"] == "unverified"
            assert _tree(root) == before_status

            assert cli.main(["daemon", "stop", "--confirm"]) == 0
            stopped = json.loads(capsys.readouterr().out)
            assert stopped == {"mode": "daemon_stop", "ok": True, "accepted": True}
            endpoint = root / ".agentdeck" / "runtime" / "daemon.sock"
            metadata = root / ".agentdeck" / "runtime" / "daemon.json"
            deadline = time.monotonic() + 3
            while (endpoint.exists() or metadata.exists()) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not endpoint.exists()
            assert not metadata.exists()
            _wait_for_process_exit(daemon_pid)
            _wait_for_reaper_empty()
            state = StateStore(root).load()
            assert state["daemon_event_outbox"] == []
            event_types = [
                json.loads(line)["event_type"]
                for line in StateStore(root).events_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            assert event_types == [
                "controller_lease_granted",
                "controller_lease_released",
            ]
        finally:
            if (root / ".agentdeck" / "runtime" / "daemon.sock").exists():
                cli.main(["daemon", "stop", "--confirm"])
                capsys.readouterr()
                deadline = time.monotonic() + 3
                metadata = root / ".agentdeck" / "runtime" / "daemon.json"
                while metadata.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)


def test_active_controller_blocks_auto_acquire_but_can_renew_and_stop_explicitly(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="adk6-ctl-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        monkeypatch.chdir(root)
        lease: dict[str, object] = {}
        try:
            assert cli.main(["daemon", "start"]) == 0
            capsys.readouterr()
            daemon_pid = int(json.loads(
                (root / ".agentdeck" / "runtime" / "daemon.json").read_text(
                    encoding="utf-8"
                )
            )["pid"])

            async def acquire_and_renew() -> dict[str, object]:
                client = await DaemonClient.connect_verified(root, timeout_seconds=2)
                try:
                    acquired = await client.request(
                        "controller.acquire", {"client_id": "client-other"}
                    )
                    assert set(acquired) == {
                        "lease_id", "generation", "expires_at"
                    }
                    renewed = await client.request(
                        "controller.renew",
                        {
                            "lease_id": acquired["lease_id"],
                            "generation": acquired["generation"],
                        },
                        lease_id=str(acquired["lease_id"]),
                        lease_generation=int(acquired["generation"]),
                    )
                    assert renewed["lease_id"] == acquired["lease_id"]
                    assert renewed["generation"] == acquired["generation"]
                    return renewed
                finally:
                    await client.close()

            lease = asyncio.run(acquire_and_renew())
            assert cli.main(["daemon", "stop", "--confirm"]) == 1
            assert "controller lease is already held" in capsys.readouterr().err
            assert (root / ".agentdeck" / "runtime" / "daemon.sock").exists()

            assert cli.main([
                "daemon",
                "stop",
                "--confirm",
                "--lease-id",
                str(lease["lease_id"]),
                "--lease-generation",
                str(lease["generation"]),
            ]) == 0
            capsys.readouterr()
            deadline = time.monotonic() + 3
            socket_path = root / ".agentdeck" / "runtime" / "daemon.sock"
            while socket_path.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not socket_path.exists()
            _wait_for_process_exit(daemon_pid)
            _wait_for_reaper_empty()
            state = StateStore(root).load()
            assert state["daemon_event_outbox"] == []
            event_types = [
                json.loads(line)["event_type"]
                for line in StateStore(root).events_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            assert event_types == [
                "controller_lease_granted",
                "controller_lease_renewed",
                "controller_lease_released",
            ]
        finally:
            socket_path = root / ".agentdeck" / "runtime" / "daemon.sock"
            if socket_path.exists() and lease:
                cli.main([
                    "daemon",
                    "stop",
                    "--confirm",
                    "--lease-id",
                    str(lease["lease_id"]),
                    "--lease-generation",
                    str(lease["generation"]),
                ])
                capsys.readouterr()


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
