from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.daemon.client import DaemonClient, admit_confirmed_mission
from agentdeck.daemon.lifecycle import daemon_endpoint, project_root_hash, reconcile_endpoint
from agentdeck.mission_orchestration import confirm_mission_for_daemon, create_mission_preview
from agentdeck.models import EventRecord
from agentdeck.providers import LeaderPlanRequest
from agentdeck.state import StateStore


WRAPPER = Path(__file__).parent / "fixtures" / "crash_daemon_wrapper.py"
FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"


class TwoWorkerProvider:
    name = "fake"

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        del request
        return {
            "goal": "crash recovery",
            "summary": "two durable steps",
            "steps": [
                {"step": 1, "agent_id": "planner", "role": "planning", "task": "plan", "risk": "review", "requires_approval": True},
                {"step": 2, "agent_id": "reviewer", "role": "review", "task": "review", "risk": "review", "requires_approval": True},
            ],
            "approval_required": True,
            "dispatch_ready": False,
            "declared_tests": ["real crash matrix"],
            "acceptance_criteria": ["no duplicate external admission"],
        }


def _wait(predicate, timeout: float = 12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("timed out waiting for real daemon crash boundary")


def _write_fake_tmux(bin_dir: Path) -> None:
    executable = bin_dir / "tmux"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, re, sys\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]; log=Path(os.environ['AGENTDECK_CRASH_ADMISSIONS'])\n"
        "if 'load-buffer' in args:\n"
        " text=sys.stdin.read(); token=re.findall(r'dsp_[0-9a-f]{32}', text)[-1]\n"
        " with log.open('a', encoding='utf-8') as f: f.write(json.dumps({'token':token,'prompt':text})+'\\n')\n"
        "elif 'capture-pane' in args:\n"
        " if os.environ.get('AGENTDECK_CRASH_NO_REPLY') == '1': pass\n"
        " elif log.exists() and log.read_text().strip():\n"
        "  item=json.loads(log.read_text().splitlines()[-1]); token=item['token']\n"
        "  print('\\n'.join([f'handoff_token: {token}','status: completed','summary: crash worker summary','verification: exact crash verification','risks: none','next_steps: continue']))\n"
        "elif 'display-message' in args: print(args[args.index('-t')+1])\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)


def _seed(root: Path, *, permission: bool = False) -> tuple[object, StateStore, dict[str, object]]:
    root.mkdir(); (root / ".git").mkdir(); write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake"', 1)
    if permission:
        command = repr([sys.executable, str(FAKE_AGENT), "mission_worker_permission", str(root / "acp.jsonl"), "planner"])
        text = text.replace('role = "planning"', f'role = "planning"\ntransport = "acp"\ntransport_command = {command}', 1)
    else:
        text = text.replace('role = "planning"', 'role = "planning"\ntransport = "tmux"', 1)
    text = text.replace('role = "review"', 'role = "review"\ntransport = "tmux"', 1)
    config_path.write_text(text, encoding="utf-8")
    config = load_config(root); store = StateStore(root)
    state = store.load()
    for index, agent_id in enumerate(("planner", "reviewer"), 1):
        state["agents"][agent_id] = {"agent_id": agent_id, "pane_id": f"%crash-{index}", "session_name": config.runtime.session_name, "cwd": str(root), "status": "running"}
    store.save(state)
    preview = create_mission_preview(config=config, store=store, provider=TwoWorkerProvider(), user_message="让 planner 和 reviewer 严格串行完成，共2轮", timeout_seconds=20)
    confirmed = confirm_mission_for_daemon(config=config, store=store, mission_id=str(preview["mission_id"]))
    return config, store, confirmed


async def _admit(root: Path, config, confirmed: dict[str, object]) -> None:
    client = await DaemonClient.connect_verified(root)
    await client.close()
    result = await admit_confirmed_mission(root, config, confirmed, state_store=StateStore(root))
    assert result["accepted"] is True


async def _stop(root: Path) -> None:
    client = await DaemonClient.connect_verified(root)
    try:
        lease = await client.request("controller.acquire", {"client_id": "crash-cleanup"})
        await client.request("daemon.stop", {"lease_id": lease["lease_id"], "generation": lease["generation"]}, lease_id=lease["lease_id"], lease_generation=lease["generation"])
    finally:
        await client.close()


async def _force_stop(root: Path) -> None:
    client = await DaemonClient.connect_verified(root)
    try:
        lease = await client.request("controller.acquire", {"client_id": "crash-force-stop"})
        authority = {
            "lease_id": lease["lease_id"],
            "lease_generation": lease["generation"],
        }
        preview = await client.request("daemon.force-stop", {}, **authority)
        await client.request(
            "daemon.force-stop",
            {"preview_id": preview["preview_id"]},
            **authority,
        )
    finally:
        await client.close()


def _start(root: Path, point: str, marker: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    process = subprocess.Popen([sys.executable, str(WRAPPER), point, str(marker), str(root)], env=env)

    async def probe() -> bool:
        try:
            client = await DaemonClient.connect_verified(root, timeout_seconds=0.2)
        except Exception:
            return False
        await client.close()
        return True

    _wait(lambda: asyncio.run(probe()))
    return process


@pytest.mark.parametrize(
    "crash_point",
    ["before_prepare", "after_prepare_before_dispatch", "after_dispatch_before_receipt", "after_receipt_before_reply", "after_reply_before_handoff", "after_handoff_before_next_dispatch", "permission_pending"],
)
def test_real_daemon_crash_boundaries_never_duplicate_external_admission(
    crash_point: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = Path(tempfile.mkdtemp(prefix="adc-", dir="/tmp"))
    root = (parent / "r").resolve(); marker = parent / "marker.json"; admissions = parent / "admissions.jsonl"
    fake_bin = parent / "bin"; fake_bin.mkdir(); _write_fake_tmux(fake_bin)
    config, store, confirmed = _seed(root, permission=crash_point == "permission_pending")
    env = dict(os.environ, PATH=f"{fake_bin}{os.pathsep}{os.environ['PATH']}", AGENTDECK_CRASH_ADMISSIONS=str(admissions))
    process = _start(root, crash_point, marker, env)
    restarted_pid: int | None = None
    observer: subprocess.Popen[bytes] | None = None
    try:
        asyncio.run(_admit(root, config, confirmed))
        _wait(marker.exists)
        os.kill(process.pid, signal.SIGKILL); process.wait(timeout=3)
        crashed = store.load()
        assert json.loads(marker.read_text())["crash_point"] == crash_point
        reconcile_endpoint(root, expected_project_hash=project_root_hash(root), health_probe=lambda _: {"healthy": False})
        recovery_marker = parent / "recovery-marker.json"
        observer = subprocess.Popen(
            [sys.executable, str(WRAPPER), "observe_recovery", str(recovery_marker), str(root)],
            env=env,
        )
        _wait(recovery_marker.exists)
        prior_decisions = crashed.get("recovery_decisions", [])
        prior_classified_at = (
            prior_decisions[-1].get("classified_at") if prior_decisions else None
        )
        _wait(
            lambda: bool(store.load().get("recovery_decisions"))
            and store.load()["recovery_decisions"][-1].get("classified_at")
            != prior_classified_at
        )
        recovered = store.load()
        decision = recovered["recovery_decisions"][-1]
        assert decision["mission_id"] == confirmed["mission_id"]
        if crash_point == "after_dispatch_before_receipt":
            assert decision["classification"] == "ambiguous"
        elif crash_point == "permission_pending":
            assert decision["classification"] == "ambiguous"
            assert crashed["permission_requests"][-1]["status"] == "pending"
        else:
            assert decision["classification"] == "resumable"
            if crash_point == "after_receipt_before_reply":
                assert crashed["mission_attempts"][-1]["state"] == "submitted"
                assert crashed["mission_attempts"][-1]["receipt_summary"] == "tmux input submitted"
                assert decision["next_transition"] == "await_worker"

        os.kill(observer.pid, signal.SIGKILL)
        observer.wait(timeout=3)
        reconcile_endpoint(
            root,
            expected_project_hash=project_root_hash(root),
            health_probe=lambda _: {"healthy": False},
        )
        restarted = subprocess.Popen(
            [sys.executable, str(WRAPPER), "none", str(parent / "normal-marker.json"), str(root)],
            env=env,
        )
        restarted_pid = restarted.pid
        _wait(lambda: daemon_endpoint(root).metadata_path.exists())
        if decision["classification"] == "resumable" and decision["next_transition"] != "await_worker":
            _wait(
                lambda: store.mission_by_id(str(confirmed["mission_id"])).get("status")
                == "completed"
            )
        else:
            time.sleep(0.2)
        records = [json.loads(line) for line in admissions.read_text().splitlines()] if admissions.exists() else []
        counts = Counter(item["token"] for item in records)
        if crash_point == "permission_pending":
            acp_records = [
                json.loads(line)
                for line in (root / "acp.jsonl").read_text().splitlines()
            ]
            counts.update(
                f"acp:{item['session_id']}"
                for item in acp_records
                if item.get("method") == "prompt"
            )
        assert counts
        assert all(count == 1 for count in counts.values())
    finally:
        if restarted_pid is not None:
            try: asyncio.run(_stop(root))
            except Exception:
                os.kill(restarted_pid, signal.SIGKILL)
            try:
                restarted.wait(timeout=3)
            except subprocess.TimeoutExpired:
                restarted.kill(); restarted.wait(timeout=3)
        if process.poll() is None: process.kill()
        if observer is not None and observer.poll() is None:
            observer.kill(); observer.wait(timeout=3)
        shutil.rmtree(parent, ignore_errors=True)


def test_real_daemon_crash_after_protocol_outbox_flush_replays_no_event(
    tmp_path: Path,
) -> None:
    parent = Path(tempfile.mkdtemp(prefix="adc-", dir="/tmp")); root = (parent / "r").resolve(); config, store, _confirmed = _seed(root)
    event = EventRecord.create("crash_matrix_outbox", {"boundary": "outbox_flush"})
    state = store.load(); state["protocol_event_outbox"] = [asdict(event)]; store.save(state)
    marker = tmp_path / "marker.json"; admissions = tmp_path / "admissions.jsonl"
    env = dict(os.environ, AGENTDECK_CRASH_ADMISSIONS=str(admissions))
    process = subprocess.Popen(
        [sys.executable, str(WRAPPER), "outbox_flush", str(marker), str(root)],
        env=env,
    )
    _wait(marker.exists); os.kill(process.pid, signal.SIGKILL); process.wait(timeout=3)
    crashed = store.load()
    assert crashed["protocol_event_outbox"] == []
    assert [item["event_id"] for item in store.all_events()].count(event.event_id) == 1
    reconcile_endpoint(root, expected_project_hash=project_root_hash(root), health_probe=lambda _: {"healthy": False})
    restarted = _start(root, "none", parent / "restart.json", env)
    assert StateStore(root).load()["protocol_event_outbox"] == []
    assert [item["event_id"] for item in StateStore(root).all_events()].count(event.event_id) == 1
    restarted.kill(); restarted.wait(timeout=3)
    shutil.rmtree(parent, ignore_errors=True)


def test_real_daemon_crash_after_durable_shutdown_state_is_observed(
    tmp_path: Path,
) -> None:
    parent = Path(tempfile.mkdtemp(prefix="adc-", dir="/tmp")); root = (parent / "r").resolve()
    fake_bin = parent / "bin"; fake_bin.mkdir(); _write_fake_tmux(fake_bin)
    config, store, confirmed = _seed(root)
    marker = parent / "marker.json"; env = dict(os.environ, PATH=f"{fake_bin}{os.pathsep}{os.environ['PATH']}", AGENTDECK_CRASH_ADMISSIONS=str(parent / "admissions.jsonl"), AGENTDECK_CRASH_NO_REPLY="1")
    process = _start(root, "shutdown", marker, env)
    asyncio.run(_admit(root, config, confirmed))
    _wait(
        lambda: bool(store.load().get("mission_attempts"))
        and store.load()["mission_attempts"][-1].get("state") == "submitted"
    )
    force_error: list[BaseException] = []

    def force_stop() -> None:
        try:
            asyncio.run(_force_stop(root))
        except BaseException as exc:
            force_error.append(exc)

    request = threading.Thread(target=force_stop, daemon=True)
    request.start(); _wait(lambda: marker.exists() or not request.is_alive())
    assert marker.exists(), repr(force_error)
    os.kill(process.pid, signal.SIGKILL); process.wait(timeout=3)
    request.join(timeout=3)
    crashed = store.load()
    mission = store.mission_by_id(str(confirmed["mission_id"]))
    assert mission["status"] == "interrupted"
    assert mission["stop_reason"] == "force_daemon_stop"
    assert crashed["mission_attempts"][-1]["state"] == "ambiguous"
    reconcile_endpoint(root, expected_project_hash=project_root_hash(root), health_probe=lambda _: {"healthy": False})
    restarted = _start(root, "none", parent / "restart.json", env)
    assert store.mission_by_id(str(confirmed["mission_id"]))["status"] == "interrupted"
    admissions = [
        json.loads(line) for line in (parent / "admissions.jsonl").read_text().splitlines()
    ]
    assert [item["token"] for item in admissions] == [
        crashed["mission_attempts"][-1]["dispatch_key"]
    ]
    restarted.kill(); restarted.wait(timeout=3)
    shutil.rmtree(parent, ignore_errors=True)
