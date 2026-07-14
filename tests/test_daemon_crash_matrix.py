from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
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
    try:
        if point == "observe_after_cycle":
            _wait(lambda: daemon_endpoint(root).metadata_path.exists())
            return process

        async def probe() -> bool:
            try:
                client = await DaemonClient.connect_verified(root, timeout_seconds=0.2)
            except Exception:
                return False
            await client.close()
            return True

        _wait(lambda: asyncio.run(probe()))
        return process
    except BaseException as primary:
        cleanup_errors: list[BaseException] = []
        try:
            _kill_and_wait(process)
        except BaseException as error:
            cleanup_errors.append(error)
        try:
            _reconcile(root)
        except BaseException as error:
            cleanup_errors.append(error)
        if cleanup_errors:
            primary.add_note(
                "daemon start cleanup failures: "
                + "; ".join(repr(error) for error in cleanup_errors)
            )
        raise


def _kill_and_wait(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.kill()
    process.wait(timeout=3)
    assert process.poll() is not None


def _reconcile(root: Path) -> None:
    reconcile_endpoint(
        root,
        expected_project_hash=project_root_hash(root),
        health_probe=lambda _: {"healthy": False},
    )
    endpoint = daemon_endpoint(root)
    assert not endpoint.socket_path.exists()
    assert not endpoint.metadata_path.exists()


@dataclass
class _TestResources:
    root: Path
    parent: Path
    processes: list[object] = field(default_factory=list)
    threads: list[threading.Thread] = field(default_factory=list)

    def cleanup(self) -> list[BaseException]:
        errors: list[BaseException] = []
        for process in reversed(self.processes):
            try:
                _kill_and_wait(process)  # type: ignore[arg-type]
            except BaseException as error:
                errors.append(error)
        for thread in reversed(self.threads):
            try:
                thread.join(timeout=3)
                if thread.is_alive():
                    raise AssertionError("daemon test request thread did not exit")
            except BaseException as error:
                errors.append(error)
        try:
            _reconcile(self.root)
        except BaseException as error:
            errors.append(error)
        try:
            shutil.rmtree(self.parent, ignore_errors=False)
            if self.parent.exists():
                raise AssertionError("daemon test temporary project was not removed")
        except FileNotFoundError:
            pass
        except BaseException as error:
            errors.append(error)
        return errors


@contextmanager
def _resource_guard(*, root: Path, parent: Path):
    resources = _TestResources(root=root, parent=parent)
    primary: BaseException | None = None
    try:
        yield resources
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup_errors = resources.cleanup()
        if cleanup_errors:
            summary = "; ".join(repr(error) for error in cleanup_errors)
            if primary is not None:
                primary.add_note(f"daemon test cleanup failures: {summary}")
            else:
                raise ExceptionGroup("daemon test cleanup failed", cleanup_errors)


def _logical_admission_counts(
    store: StateStore, admissions: Path, acp_log: Path
) -> Counter[tuple[str, str, str]]:
    attempts = store.load().get("mission_attempts", [])
    by_token = {
        item["dispatch_key"]: (
            item["mission_id"], item["step_id"], item["agent_id"]
        )
        for item in attempts
    }
    tokens: list[str] = []
    if admissions.exists():
        tokens.extend(
            str(json.loads(line)["token"])
            for line in admissions.read_text(encoding="utf-8").splitlines()
        )
    if acp_log.exists():
        tokens.extend(
            str(row["dispatch_token"])
            for row in (
                json.loads(line)
                for line in acp_log.read_text(encoding="utf-8").splitlines()
            )
            if row.get("method") == "prompt" and row.get("dispatch_token") is not None
        )
    assert all(token in by_token for token in tokens)
    return Counter(by_token[token] for token in tokens)


def test_start_reaps_spawned_daemon_when_readiness_wait_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "start-failure").resolve()
    _config, _store, _confirmed = _seed(root)
    spawned: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    def fail_wait(*_args, **_kwargs):
        raise RuntimeError("injected readiness failure")

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    monkeypatch.setattr(sys.modules[__name__], "_wait", fail_wait)
    try:
        with pytest.raises(RuntimeError, match="injected readiness failure"):
            _start(root, "none", tmp_path / "marker.json", dict(os.environ))
        assert len(spawned) == 1
        assert spawned[0].poll() is not None
        endpoint = daemon_endpoint(root)
        assert not endpoint.socket_path.exists()
        assert not endpoint.metadata_path.exists()
    finally:
        for process in spawned:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        reconcile_endpoint(
            root,
            expected_project_hash=project_root_hash(root),
            health_probe=lambda _: {"healthy": False},
        )


def test_resource_guard_runs_all_cleanup_without_masking_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "guard"
    root = parent / "r"
    root.mkdir(parents=True)
    first = object()
    second = object()
    calls: list[object] = []
    reconciled: list[Path] = []

    def cleanup(process) -> None:
        calls.append(process)
        if process is first:
            raise RuntimeError("injected first cleanup failure")

    monkeypatch.setattr(sys.modules[__name__], "_kill_and_wait", cleanup)
    monkeypatch.setattr(
        sys.modules[__name__], "_reconcile", lambda value: reconciled.append(value)
    )

    with pytest.raises(AssertionError, match="primary failure") as caught:
        with _resource_guard(root=root, parent=parent) as resources:
            resources.processes.extend((second, first))
            raise AssertionError("primary failure")

    assert calls == [first, second]
    assert reconciled == [root]
    assert not parent.exists()
    assert any("injected first cleanup failure" in note for note in caught.value.__notes__)


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
    with _resource_guard(root=root, parent=parent) as resources:
        process = _start(root, crash_point, marker, env)
        resources.processes.append(process)
        asyncio.run(_admit(root, config, confirmed))
        _wait(marker.exists)
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
        crashed = store.load()
        assert json.loads(marker.read_text())["crash_point"] == crash_point
        _reconcile(root)
        cycle_marker = parent / "cycle-marker.json"
        restarted = _start(
            root, "observe_after_cycle", cycle_marker, env
        )
        resources.processes.append(restarted)
        _wait(cycle_marker.exists)
        cycle = json.loads(cycle_marker.read_text(encoding="utf-8"))
        assert cycle["scheduler_decision_kind"] != "idle"
        assert len(cycle["startup_recovery_decisions"]) == 1
        decision = cycle["startup_recovery_decisions"][0]
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
        logical_key = (str(confirmed["mission_id"]), "step_1", "planner")
        expected_counts = (
            Counter()
            if crash_point in {"before_prepare", "after_prepare_before_dispatch"}
            else Counter({logical_key: 1})
        )
        assert _logical_admission_counts(
            store, admissions, root / "acp.jsonl"
        ) == expected_counts


def test_real_daemon_crash_after_protocol_outbox_flush_replays_no_event(
    tmp_path: Path,
) -> None:
    parent = Path(tempfile.mkdtemp(prefix="adc-", dir="/tmp")); root = (parent / "r").resolve(); config, store, _confirmed = _seed(root)
    event = EventRecord.create("crash_matrix_outbox", {"boundary": "outbox_flush"})
    state = store.load(); state["protocol_event_outbox"] = [asdict(event)]; store.save(state)
    marker = tmp_path / "marker.json"; admissions = tmp_path / "admissions.jsonl"
    env = dict(os.environ, AGENTDECK_CRASH_ADMISSIONS=str(admissions))
    with _resource_guard(root=root, parent=parent) as resources:
        process = subprocess.Popen(
            [sys.executable, str(WRAPPER), "outbox_flush", str(marker), str(root)],
            env=env,
        )
        resources.processes.append(process)
        _wait(marker.exists)
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
        crashed = store.load()
        assert crashed["protocol_event_outbox"] == []
        assert [item["event_id"] for item in store.all_events()].count(event.event_id) == 1
        _reconcile(root)
        restarted = _start(root, "none", parent / "restart.json", env)
        resources.processes.append(restarted)
        assert StateStore(root).load()["protocol_event_outbox"] == []
        assert [item["event_id"] for item in StateStore(root).all_events()].count(event.event_id) == 1
        assert _logical_admission_counts(store, admissions, root / "acp.jsonl") == Counter()


def test_real_daemon_crash_after_durable_shutdown_state_is_observed(
    tmp_path: Path,
) -> None:
    parent = Path(tempfile.mkdtemp(prefix="adc-", dir="/tmp")); root = (parent / "r").resolve()
    fake_bin = parent / "bin"; fake_bin.mkdir(); _write_fake_tmux(fake_bin)
    config, store, confirmed = _seed(root)
    marker = parent / "marker.json"; env = dict(os.environ, PATH=f"{fake_bin}{os.pathsep}{os.environ['PATH']}", AGENTDECK_CRASH_ADMISSIONS=str(parent / "admissions.jsonl"), AGENTDECK_CRASH_NO_REPLY="1")
    force_error: list[BaseException] = []

    def force_stop() -> None:
        try:
            asyncio.run(_force_stop(root))
        except BaseException as exc:
            force_error.append(exc)

    with _resource_guard(root=root, parent=parent) as resources:
        process = _start(root, "shutdown", marker, env)
        resources.processes.append(process)
        asyncio.run(_admit(root, config, confirmed))
        _wait(
            lambda: bool(store.load().get("mission_attempts"))
            and store.load()["mission_attempts"][-1].get("state") == "submitted"
        )
        request = threading.Thread(target=force_stop, daemon=True)
        request.start()
        resources.threads.append(request)
        _wait(lambda: marker.exists() or not request.is_alive())
        assert marker.exists(), repr(force_error)
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
        request.join(timeout=3)
        assert not request.is_alive()
        crashed = store.load()
        mission = store.mission_by_id(str(confirmed["mission_id"]))
        assert mission["status"] == "interrupted"
        assert mission["stop_reason"] == "force_daemon_stop"
        assert crashed["mission_attempts"][-1]["state"] == "ambiguous"
        _reconcile(root)
        restarted = _start(root, "none", parent / "restart.json", env)
        resources.processes.append(restarted)
        assert store.mission_by_id(str(confirmed["mission_id"]))["status"] == "interrupted"
        logical_key = (str(confirmed["mission_id"]), "step_1", "planner")
        assert _logical_admission_counts(
            store, parent / "admissions.jsonl", root / "acp.jsonl"
        ) == Counter({logical_key: 1})
