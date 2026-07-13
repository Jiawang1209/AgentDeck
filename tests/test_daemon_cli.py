from __future__ import annotations

from dataclasses import asdict
import asyncio
import json
import os
from pathlib import Path
import time
import tempfile
import shutil
import socket
import threading
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
from agentdeck.daemon.client import DaemonClient, DaemonUnavailable
from agentdeck.daemon.lease import LeaseError, expire_controller, grant_controller
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


def _configure_daemon(root: Path, **values: int) -> None:
    config_path = root / ".agentdeck" / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n[daemon]\n"
        + "".join(f"{name} = {value}\n" for name, value in values.items()),
        encoding="utf-8",
    )


def _wait_for_runtime_state(
    root: Path, expected: str, *, timeout_seconds: float = 3
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        runtime = StateStore(root).load().get("daemon_runtime")
        if isinstance(runtime, dict) and runtime.get("state") == expected:
            return
        time.sleep(0.02)
    pytest.fail(f"daemon never reached {expected}")


def _held_daemon_client(
    root: Path, ready: threading.Event, release: threading.Event,
) -> threading.Thread:
    def run() -> None:
        async def hold() -> None:
            client = await DaemonClient.connect_verified(root, timeout_seconds=2)
            ready.set()
            try:
                while not release.is_set():
                    await asyncio.sleep(0.01)
            finally:
                await client.close()

        asyncio.run(hold())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(timeout=2)
    return thread


def _reacquire_and_release(root: Path, client_id: str) -> None:
    async def exercise() -> None:
        client = await DaemonClient.connect_verified(root, timeout_seconds=2)
        try:
            lease = await client.request(
                "controller.acquire", {"client_id": client_id}
            )
            await client.request(
                "controller.release",
                {
                    "lease_id": lease["lease_id"],
                    "generation": lease["generation"],
                },
                lease_id=str(lease["lease_id"]),
                lease_generation=int(lease["generation"]),
            )
        finally:
            await client.close()

    asyncio.run(exercise())


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


@pytest.mark.parametrize(
    ("lease_kind", "expected"),
    [
        ("active", True),
        ("expired", False),
        ("terminal", False),
        ("naive", False),
        ("malformed", False),
    ],
)
def test_offline_project_view_controller_presence_is_time_aware_and_zero_write(
    tmp_path: Path, monkeypatch, lease_kind: str, expected: bool,
) -> None:
    root = _project(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    if lease_kind == "active":
        summary = grant_controller(
            client_id="client-offline", now=now, ttl_seconds=60
        ).current.summary()
    else:
        granted = grant_controller(
            client_id="client-offline",
            now=now - timedelta(seconds=60),
            ttl_seconds=1,
        )
        if lease_kind == "terminal":
            summary = expire_controller(granted.current, now=now).current.summary()
        else:
            summary = granted.current.summary()
            if lease_kind == "naive":
                summary["expires_at"] = now.replace(tzinfo=None).isoformat()
            elif lease_kind == "malformed":
                summary["expires_at"] = "not-a-timestamp"
    state = StateStore(root).load()
    state["controller_lease"] = summary
    StateStore(root).save(state)
    before = _tree(root)

    project_view = asdict(StateStore(root).project_view(load_config(root)))

    assert project_view["daemon"]["state"] == "stopped"
    assert project_view["daemon"]["controller_present"] is expected
    assert _tree(root) == before


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


def test_daemon_shutdown_signal_is_bound_to_durable_commit_not_rpc_ack() -> None:
    async def case() -> None:
        stop_event = asyncio.Event()
        committed: list[str] = []
        result = cli._commit_daemon_shutdown(
            lambda: committed.append("durable") or {"state": "stopping"},
            stop_event,
        )
        assert result == {"state": "stopping"}
        assert committed == ["durable"]
        assert stop_event.is_set()

        failed_event = asyncio.Event()
        with pytest.raises(OSError, match="durable write failed"):
            cli._commit_daemon_shutdown(
                lambda: (_ for _ in ()).throw(OSError("durable write failed")),
                failed_event,
            )
        assert failed_event.is_set() is False

    asyncio.run(case())


def test_force_stop_signals_shutdown_even_when_post_stop_lease_release_fails() -> None:
    async def case() -> None:
        stop_event = asyncio.Event()
        with pytest.raises(OSError, match="lease release failed"):
            cli._commit_daemon_shutdown(
                lambda: (_ for _ in ()).throw(OSError("lease release failed")),
                stop_event,
                shutdown_on_failure=True,
            )
        assert stop_event.is_set()

    asyncio.run(case())


@pytest.mark.parametrize("failure_stage", ["load", "release", "flush"])
def test_force_stop_finalizer_signals_after_any_post_commit_failure(
    monkeypatch, failure_stage: str,
) -> None:
    now = datetime.now(timezone.utc)
    lease = grant_controller(
        client_id="force-stop-test", now=now, ttl_seconds=60
    ).current
    assert lease is not None

    class Store:
        def load(self):
            if failure_stage == "load":
                raise OSError("load failed")
            return {"controller_lease": lease.summary()}

    if failure_stage == "release":
        monkeypatch.setattr(
            cli,
            "release_controller",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("release failed")),
        )

    def commit_and_flush(_transition):
        if failure_stage == "flush":
            raise OSError("flush failed")
        return {}

    async def case() -> None:
        stop_event = asyncio.Event()
        diagnostic = cli._finalize_force_stop_shutdown(
            Store(),
            lease_id=lease.lease_id,
            generation=lease.generation,
            now=now,
            commit_and_flush=commit_and_flush,
            stop_event=stop_event,
        )
        assert diagnostic == {
            "status": "failed",
            "reason": "controller cleanup incomplete",
            "recovery": "daemon restart reconciliation required",
        }
        assert lease.lease_id not in repr(diagnostic)
        assert stop_event.is_set()

    asyncio.run(case())


def test_force_stop_rpc_reports_cleanup_failure_after_durable_acceptance(
    monkeypatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="adk-force-rpc-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        original_commit = StateStore.commit_controller_lease

        def fail_release(self, transition):
            if transition.action == "release":
                raise OSError("simulated release failure")
            return original_commit(self, transition)

        monkeypatch.setattr(StateStore, "commit_controller_lease", fail_release)

        async def case() -> None:
            serving = asyncio.create_task(
                cli._serve_daemon(root, load_config(root), StateStore(root))
            )
            client = None
            try:
                for _ in range(100):
                    try:
                        client = await DaemonClient.connect_verified(
                            root, timeout_seconds=0.1
                        )
                    except DaemonUnavailable:
                        await asyncio.sleep(0.01)
                    else:
                        break
                assert client is not None
                lease = await client.request(
                    "controller.acquire", {"client_id": "force-rpc-test"}
                )
                authority = {
                    "lease_id": str(lease["lease_id"]),
                    "lease_generation": int(lease["generation"]),
                }
                preview = await client.request(
                    "daemon.force-stop", {}, **authority
                )
                result = await client.request(
                    "daemon.force-stop",
                    {"preview_id": preview["preview_id"]},
                    **authority,
                )
                assert result["accepted"] is True
                assert result["state"] == "stopping"
                assert result["cleanup"] == {
                    "status": "failed",
                    "reason": "controller cleanup incomplete",
                    "recovery": "daemon restart reconciliation required",
                }
                assert str(lease["lease_id"]) not in repr(result["cleanup"])
            finally:
                if client is not None:
                    await client.close()
            assert await asyncio.wait_for(serving, timeout=2) == 0
            state = StateStore(root).load()
            audit = [
                item for item in state["protocol_event_outbox"]
                if item["event_type"] == "daemon_force_stopped"
            ]
            assert len(audit) == 1
            assert not (root / ".agentdeck" / "runtime" / "daemon.sock").exists()

        asyncio.run(case())


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


@pytest.mark.parametrize("release_behavior", ["error", "malformed"])
def test_temporary_controller_cleanup_failure_is_an_explicit_blocker(
    tmp_path: Path, monkeypatch, release_behavior: str,
) -> None:
    root = _project(tmp_path, monkeypatch)

    class FakeClient:
        async def request(self, method, _params, **_kwargs):
            if method == "controller.acquire":
                return {"lease_id": "lse_" + "1" * 24, "generation": 1}
            if method == "daemon.stop":
                raise DaemonUnavailable("daemon has active keepalive work")
            assert method == "controller.release"
            if release_behavior == "error":
                raise DaemonUnavailable("daemon request failed")
            return {}

        async def close(self) -> None:
            return None

    async def connect(*_args, **_kwargs):
        return FakeClient()

    monkeypatch.setattr(DaemonClient, "connect_verified", connect)
    with pytest.raises(
        DaemonUnavailable,
        match="daemon has active keepalive work; temporary controller cleanup failed",
    ):
        asyncio.run(_request_daemon_stop_for_test(root))


async def _request_daemon_stop_for_test(root: Path) -> dict[str, object]:
    return await cli._request_daemon_stop(
        root, load_config(root), lease_id=None, lease_generation=None
    )


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


@pytest.mark.parametrize(
    ("state_update", "view_field"),
    [
        ({"missions": [{"status": "running"}]}, "active_mission_count"),
        ({"agents": {"worker": {"status": "running"}}}, "active_worker_count"),
        ({"approvals": [{"status": "pending"}]}, "pending_approval_count"),
        ({"permission_requests": [{"status": "pending"}]}, "pending_permission_count"),
        ({"inbox": {"worker": [{"status": "pending", "event_type": "task_reply"}]}}, "pending_reply_count"),
        ({"recovery_decisions": [{"status": "pending"}]}, "pending_recovery_decision_count"),
        ({"decisions": [{"status": "pending"}]}, "pending_decision_count"),
        ({"decisions": [{"status": "ambiguous"}]}, "ambiguous_decision_count"),
        ({"protocol_event_outbox": [{"event_id": "pending"}]}, "outbox_count"),
        ({"recovery_active": True}, "recovery_active"),
        ({"safe_shutdown_active": True}, "safe_shutdown_active"),
        ({"atomic_write_active": True}, "atomic_write_active"),
    ],
)
def test_daemon_keepalive_view_derives_each_persisted_lifetime_fact(
    tmp_path: Path, monkeypatch, state_update: dict[str, object], view_field: str,
) -> None:
    root = _project(tmp_path, monkeypatch)
    state = StateStore(root).load()
    state.update(state_update)
    view = cli._daemon_keepalive_view(state, other_client_count=0)
    assert view[view_field] in {1, True}


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


def test_restarting_after_explicit_stop_does_not_reclassify_release_as_expiry(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="adk6-restart-release-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        monkeypatch.chdir(root)
        try:
            assert cli.main(["daemon", "start"]) == 0
            capsys.readouterr()
            assert cli.main(["daemon", "stop", "--confirm"]) == 0
            capsys.readouterr()
            endpoint = root / ".agentdeck" / "runtime" / "daemon.sock"
            deadline = time.monotonic() + 3
            while endpoint.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not endpoint.exists()

            assert cli.main(["daemon", "start"]) == 0
            capsys.readouterr()
            time.sleep(0.2)
            event_types = [
                json.loads(line)["event_type"]
                for line in StateStore(root).events_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            assert event_types == [
                "controller_lease_granted", "controller_lease_released",
            ]
        finally:
            if (root / ".agentdeck" / "runtime" / "daemon.sock").exists():
                cli.main(["daemon", "stop", "--confirm"])
                capsys.readouterr()
            _wait_for_reaper_empty()


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


def test_failed_explicit_stop_never_releases_user_controller(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="adk6-explicit-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        monkeypatch.chdir(root)
        lease: dict[str, object] = {}
        try:
            assert cli.main(["daemon", "start"]) == 0
            capsys.readouterr()

            async def acquire() -> dict[str, object]:
                client = await DaemonClient.connect_verified(root, timeout_seconds=2)
                try:
                    return await client.request(
                        "controller.acquire", {"client_id": "client-explicit"}
                    )
                finally:
                    await client.close()

            lease = asyncio.run(acquire())
            state = StateStore(root).load()
            state["approvals"] = [{"status": "pending"}]
            StateStore(root).save(state)
            command = [
                "daemon", "stop", "--confirm", "--lease-id", str(lease["lease_id"]),
                "--lease-generation", str(lease["generation"]),
            ]
            assert cli.main(command) == 1
            assert capsys.readouterr().err == "daemon has active keepalive work\n"
            assert cli._controller_lease_is_active(StateStore(root).load()) is True
            state = StateStore(root).load()
            state["approvals"] = []
            StateStore(root).save(state)
            assert cli.main(command) == 0
            capsys.readouterr()
            _wait_for_reaper_empty()
        finally:
            if (root / ".agentdeck" / "runtime" / "daemon.sock").exists() and lease:
                state = StateStore(root).load()
                state["approvals"] = []
                StateStore(root).save(state)
                cli.main([
                    "daemon", "stop", "--confirm", "--lease-id", str(lease["lease_id"]),
                    "--lease-generation", str(lease["generation"]),
                ])
                capsys.readouterr()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approvals", [{"status": "pending"}]),
    ],
)
def test_nonclient_keepalive_facts_block_idle_exit_and_new_client_resets_timer(
    tmp_path: Path, monkeypatch, capsys, field: str, value: object,
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="adk6-idle-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        _configure_daemon(root, idle_grace_seconds=1)
        monkeypatch.chdir(root)
        state = StateStore(root).load()
        state[field] = value
        StateStore(root).save(state)
        daemon_pid = 0
        try:
            assert cli.main(["daemon", "start"]) == 0
            capsys.readouterr()
            daemon_pid = int(json.loads(
                (root / ".agentdeck" / "runtime" / "daemon.json").read_text(
                    encoding="utf-8"
                )
            )["pid"])
            _wait_for_runtime_state(root, "busy")
            time.sleep(1.2)
            assert (root / ".agentdeck" / "runtime" / "daemon.sock").exists()

            state = StateStore(root).load()
            state[field] = []
            StateStore(root).save(state)
            _wait_for_runtime_state(root, "idle_grace")

            async def hold_past_original_deadline() -> None:
                client = await DaemonClient.connect_verified(root, timeout_seconds=2)
                try:
                    await asyncio.sleep(1.2)
                    assert (await client.request("status", {}))["state"] == "ready"
                finally:
                    await client.close()

            asyncio.run(hold_past_original_deadline())
            assert (root / ".agentdeck" / "runtime" / "daemon.sock").exists()
            deadline = time.monotonic() + 3
            while (
                (root / ".agentdeck" / "runtime" / "daemon.sock").exists()
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            assert not (root / ".agentdeck" / "runtime" / "daemon.sock").exists()
            _wait_for_process_exit(daemon_pid)
            _wait_for_reaper_empty()
        finally:
            if (root / ".agentdeck" / "runtime" / "daemon.sock").exists():
                state = StateStore(root).load()
                state[field] = []
                StateStore(root).save(state)
                cli.main(["daemon", "stop", "--confirm"])
                capsys.readouterr()


def test_sub_poll_short_connection_resets_a_full_idle_grace_window(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="adk6-activity-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        _configure_daemon(root, idle_grace_seconds=1)
        monkeypatch.chdir(root)
        daemon_pid = 0
        try:
            assert cli.main(["daemon", "start"]) == 0
            capsys.readouterr()
            daemon_pid = int(json.loads(
                (root / ".agentdeck" / "runtime" / "daemon.json").read_text(
                    encoding="utf-8"
                )
            )["pid"])
            _wait_for_runtime_state(root, "idle_grace")
            time.sleep(0.82)
            started = time.monotonic()
            short = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                short.connect(str(root / ".agentdeck" / "runtime" / "daemon.sock"))
            finally:
                short.close()
            assert time.monotonic() - started < 0.1

            time.sleep(0.3)
            assert (root / ".agentdeck" / "runtime" / "daemon.sock").exists()
            deadline = time.monotonic() + 2
            while (
                (root / ".agentdeck" / "runtime" / "daemon.sock").exists()
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            assert not (root / ".agentdeck" / "runtime" / "daemon.sock").exists()
            _wait_for_process_exit(daemon_pid)
            _wait_for_reaper_empty()
        finally:
            if (root / ".agentdeck" / "runtime" / "daemon.sock").exists():
                cli.main(["daemon", "stop", "--confirm"])
                capsys.readouterr()


def test_temporary_stop_controller_is_released_after_active_work_rejection(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="adk6-release-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        monkeypatch.chdir(root)
        try:
            assert cli.main(["daemon", "start"]) == 0
            capsys.readouterr()
            state = StateStore(root).load()
            state["approvals"] = [{"status": "pending"}]
            StateStore(root).save(state)

            assert cli.main(["daemon", "stop", "--confirm"]) == 1
            stop_error = capsys.readouterr().err
            assert stop_error == "daemon has active keepalive work\n"
            state = StateStore(root).load()
            assert cli._controller_lease_is_active(state) is False
            assert state["daemon_event_outbox"] == []
            assert [
                json.loads(line)["event_type"]
                for line in StateStore(root).events_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ] == ["controller_lease_granted", "controller_lease_released"]

            async def reacquire_and_release() -> None:
                client = await DaemonClient.connect_verified(root, timeout_seconds=2)
                try:
                    lease = await client.request(
                        "controller.acquire", {"client_id": "client-reacquire"}
                    )
                    await client.request(
                        "controller.release",
                        {
                            "lease_id": lease["lease_id"],
                            "generation": lease["generation"],
                        },
                        lease_id=str(lease["lease_id"]),
                        lease_generation=int(lease["generation"]),
                    )
                finally:
                    await client.close()

            asyncio.run(reacquire_and_release())
            state = StateStore(root).load()
            state["approvals"] = []
            StateStore(root).save(state)
            assert cli.main(["daemon", "stop", "--confirm"]) == 0
            capsys.readouterr()
            _wait_for_reaper_empty()
        finally:
            if (root / ".agentdeck" / "runtime" / "daemon.sock").exists():
                state = StateStore(root).load()
                state["approvals"] = []
                StateStore(root).save(state)
                cli.main(["daemon", "stop", "--confirm"])
                capsys.readouterr()


def test_temporary_stop_controller_is_released_after_other_client_rejection(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="adk6-release-client-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        monkeypatch.chdir(root)
        ready = threading.Event()
        release = threading.Event()
        held: threading.Thread | None = None
        try:
            assert cli.main(["daemon", "start"]) == 0
            capsys.readouterr()
            held = _held_daemon_client(root, ready, release)
            _wait_for_runtime_state(root, "ready")

            assert cli.main(["daemon", "stop", "--confirm"]) == 1
            assert capsys.readouterr().err == "daemon has active keepalive work\n"
            assert cli._controller_lease_is_active(StateStore(root).load()) is False
            assert StateStore(root).load()["daemon_event_outbox"] == []

            release.set()
            held.join(timeout=2)
            assert not held.is_alive()
            _reacquire_and_release(root, "client-after-other")
            assert cli.main(["daemon", "stop", "--confirm"]) == 0
            capsys.readouterr()
            _wait_for_reaper_empty()
        finally:
            release.set()
            if held is not None:
                held.join(timeout=2)
            if (root / ".agentdeck" / "runtime" / "daemon.sock").exists():
                cli.main(["daemon", "stop", "--confirm"])
                capsys.readouterr()


def test_temporary_stop_controller_is_released_after_identity_rejection(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="adk6-release-identity-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        monkeypatch.chdir(root)
        ready = threading.Event()
        release = threading.Event()
        held: threading.Thread | None = None
        original_runtime: dict[str, object] | None = None
        try:
            assert cli.main(["daemon", "start"]) == 0
            capsys.readouterr()
            held = _held_daemon_client(root, ready, release)
            _wait_for_runtime_state(root, "ready")
            state = StateStore(root).load()
            original_runtime = dict(state["daemon_runtime"])
            state["daemon_runtime"]["instance_id"] = "dmn_identity_drift"
            StateStore(root).save(state)

            assert cli.main(["daemon", "stop", "--confirm"]) == 1
            assert capsys.readouterr().err == "daemon identity is unverified\n"
            assert cli._controller_lease_is_active(StateStore(root).load()) is False
            assert StateStore(root).load()["daemon_event_outbox"] == []

            _reacquire_and_release(root, "client-after-identity")
            state = StateStore(root).load()
            state["daemon_runtime"] = original_runtime
            StateStore(root).save(state)
            release.set()
            held.join(timeout=2)
            assert not held.is_alive()
            assert cli.main(["daemon", "stop", "--confirm"]) == 0
            capsys.readouterr()
            _wait_for_reaper_empty()
        finally:
            release.set()
            if held is not None:
                held.join(timeout=2)
            if original_runtime is not None and (
                root / ".agentdeck" / "runtime" / "daemon.sock"
            ).exists():
                state = StateStore(root).load()
                state["daemon_runtime"] = original_runtime
                StateStore(root).save(state)
                cli.main(["daemon", "stop", "--confirm"])
                capsys.readouterr()


def test_live_status_tracks_controller_acquire_release_across_clients(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="adk6-live-controller-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        monkeypatch.chdir(root)
        try:
            assert cli.main(["daemon", "start"]) == 0
            capsys.readouterr()

            async def exercise() -> None:
                controller = await DaemonClient.connect_verified(root, timeout_seconds=2)
                observer = await DaemonClient.connect_verified(root, timeout_seconds=2)
                try:
                    lease = await controller.request(
                        "controller.acquire", {"client_id": "client-controller"}
                    )
                    assert (await observer.request("status", {}))["controller_present"] is True
                    await controller.request(
                        "controller.release",
                        {
                            "lease_id": lease["lease_id"],
                            "generation": lease["generation"],
                        },
                        lease_id=str(lease["lease_id"]),
                        lease_generation=int(lease["generation"]),
                    )
                    assert (await observer.request("status", {}))["controller_present"] is False
                finally:
                    await observer.close()
                    await controller.close()

            asyncio.run(exercise())
            assert cli.main(["daemon", "stop", "--confirm"]) == 0
            capsys.readouterr()
            _wait_for_reaper_empty()
        finally:
            if (root / ".agentdeck" / "runtime" / "daemon.sock").exists():
                cli.main(["daemon", "stop", "--confirm"])
                capsys.readouterr()


def test_expired_controller_is_refreshed_in_runtime_and_project_view(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="adk6-expiry-", dir="/tmp") as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        write_default_config(root)
        _configure_daemon(root, controller_ttl_seconds=1)
        monkeypatch.chdir(root)
        try:
            assert cli.main(["daemon", "start"]) == 0
            capsys.readouterr()

            async def acquire() -> None:
                client = await DaemonClient.connect_verified(root, timeout_seconds=2)
                try:
                    await client.request(
                        "controller.acquire", {"client_id": "client-expiring"}
                    )
                finally:
                    await client.close()

            asyncio.run(acquire())
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = StateStore(root).load()
                lease_id = str(state.get("controller_lease", {}).get("lease_id", ""))
                if lease_id.startswith("lst_"):
                    break
                time.sleep(0.02)
            assert lease_id.startswith("lst_")
            time.sleep(0.3)
            state = StateStore(root).load()
            assert state["daemon_event_outbox"] == []
            event_types = [
                json.loads(line)["event_type"]
                for line in StateStore(root).events_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            assert event_types == [
                "controller_lease_granted", "controller_lease_expired"
            ]
            assert state["daemon_runtime"]["state"] == "idle_grace"
            project_view = asdict(StateStore(root).project_view(load_config(root)))
            assert project_view["daemon"]["controller_present"] is False

            async def status() -> dict[str, object]:
                client = await DaemonClient.connect_verified(root, timeout_seconds=2)
                try:
                    return await client.request("status", {})
                finally:
                    await client.close()

            assert asyncio.run(status())["controller_present"] is False
            assert cli.main(["daemon", "stop", "--confirm"]) == 0
            capsys.readouterr()
            _wait_for_reaper_empty()
        finally:
            if (root / ".agentdeck" / "runtime" / "daemon.sock").exists():
                cli.main(["daemon", "stop", "--confirm"])
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
