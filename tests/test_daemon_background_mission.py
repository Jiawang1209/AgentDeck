from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import time

import pytest

from agentdeck.config import load_config, write_default_config
import agentdeck.daemon.client as daemon_client_module
from agentdeck.daemon.client import (
    DaemonClient,
    admit_confirmed_mission,
    connect_or_start,
    govern_mission,
)
from agentdeck.daemon.lifecycle import (
    daemon_endpoint,
    project_root_hash,
    reconcile_endpoint,
)
from agentdeck.mission_orchestration import confirm_mission_for_daemon, create_mission_preview
from agentdeck.providers import LeaderPlanRequest
from agentdeck.state import StateStore


FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"


class TwoWorkerProvider:
    name = "fake"

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        del request
        return {
            "goal": "complete two deterministic steps",
            "summary": "planner then reviewer",
            "steps": [
                {"step": 1, "agent_id": "planner", "role": "planning", "task": "plan", "risk": "review", "requires_approval": True},
                {"step": 2, "agent_id": "reviewer", "role": "review", "task": "review", "risk": "review", "requires_approval": True},
            ],
            "approval_required": True,
            "dispatch_ready": False,
            "declared_tests": ["deterministic ACP fixture"],
            "acceptance_criteria": ["both workers complete"],
        }


def _wait_for_completed(store: StateStore, mission_id: str, timeout: float = 12) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        mission = store.mission_by_id(mission_id)
        if mission.get("status") == "completed":
            return mission
        time.sleep(0.05)
    state = store.load()
    raise AssertionError(
        "timed out waiting for daemon Mission completion: "
        + repr(
            {
                "mission": store.mission_by_id(mission_id),
                "attempts": state.get("mission_attempts"),
                "replies": state.get("mission_worker_replies"),
                "handoffs": state.get("mission_handoffs"),
                "recovery": state.get("recovery_decisions"),
                "daemon_error": (
                    (store.root / ".agentdeck" / "runtime" / "daemon.stderr.log")
                    .read_text(encoding="utf-8")
                    if (store.root / ".agentdeck" / "runtime" / "daemon.stderr.log").exists()
                    else None
                ),
            }
        )
    )


def _wait_for_daemon_shutdown(root: Path, pid: int, timeout: float = 5) -> None:
    endpoint = daemon_endpoint(root)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            process_alive = True
        except ProcessLookupError:
            process_alive = False
        if (
            not process_alive
            and not endpoint.socket_path.exists()
            and not endpoint.metadata_path.exists()
            and daemon_client_module._detached_reaper_count() == 0
        ):
            return
        time.sleep(0.02)
    raise AssertionError("daemon process, endpoint, or detached reaper did not clear")


def _wait_for_process_and_reaper(pid: int, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            process_alive = True
        except ProcessLookupError:
            process_alive = False
        if not process_alive and daemon_client_module._detached_reaper_count() == 0:
            return
        time.sleep(0.02)
    raise AssertionError("daemon process or detached reaper did not clear")


def _discover_daemon_pid(root: Path, timeout: float = 2) -> int | None:
    metadata_path = daemon_endpoint(root).metadata_path
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            return int(metadata["pid"])
        if daemon_client_module._detached_reaper_count() == 0:
            return None
        time.sleep(0.02)
    return None


def _stop_and_await_daemon(
    root: Path, pid: int, *, force_terminate: bool = False
) -> None:
    async def stop() -> None:
        client = await DaemonClient.connect_verified(root)
        try:
            lease = await client.request(
                "controller.acquire", {"client_id": "test-cleanup"}
            )
            await client.request(
                "daemon.stop",
                {"lease_id": lease["lease_id"], "generation": lease["generation"]},
                lease_id=lease["lease_id"],
                lease_generation=lease["generation"],
            )
        finally:
            await client.close()

    if force_terminate:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _wait_for_process_and_reaper(pid)
        reconcile_endpoint(
            root,
            expected_project_hash=project_root_hash(root),
            health_probe=lambda _metadata: {"healthy": False},
        )
        _wait_for_daemon_shutdown(root, pid)
        return
    try:
        asyncio.run(stop())
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        _wait_for_daemon_shutdown(root, pid)
    except AssertionError:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _wait_for_process_and_reaper(pid)
        reconcile_endpoint(
            root,
            expected_project_hash=project_root_hash(root),
            health_probe=lambda _metadata: {"healthy": False},
        )
        _wait_for_daemon_shutdown(root, pid)


def _run_real_daemon_case(
    root: Path,
    *,
    fail_after_admission: bool = False,
    force_cleanup_terminate: bool = False,
) -> None:
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    prompt_log = root / "worker-prompts.jsonl"
    planner_command = (
        f'[{sys.executable!r}, {str(FAKE_AGENT)!r}, "mission_worker", '
        f'{str(prompt_log)!r}, "planner"]'
    )
    reviewer_command = (
        f'[{sys.executable!r}, {str(FAKE_AGENT)!r}, "mission_worker", '
        f'{str(prompt_log)!r}, "reviewer"]'
    )
    text = text.replace(
        'role = "planning"',
        f'role = "planning"\ntransport = "acp"\ntransport_command = {planner_command}',
        1,
    )
    text = text.replace(
        'role = "review"',
        f'role = "review"\ntransport = "acp"\ntransport_command = {reviewer_command}',
        1,
    )
    config_path.write_text(text, encoding="utf-8")
    config = load_config(root)
    store = StateStore(root)
    preview = create_mission_preview(
        config=config,
        store=store,
        provider=TwoWorkerProvider(),
        user_message="让 Codex 和 Claude 严格串行完成两步审阅，共2轮",
        timeout_seconds=30,
    )
    confirmed = confirm_mission_for_daemon(
        config=config, store=store, mission_id=preview["mission_id"]
    )

    async def admit_then_disconnect() -> dict[str, object]:
        client = await connect_or_start(root, config)
        await client.close()
        result = await admit_confirmed_mission(root, config, confirmed, state_store=store)
        return result

    pid: int | None = None
    try:
        admitted = asyncio.run(admit_then_disconnect())
        endpoint = daemon_endpoint(root)
        metadata = json.loads(endpoint.metadata_path.read_text(encoding="utf-8"))
        pid = int(metadata["pid"])
        assert admitted["accepted"] is True
        if fail_after_admission:
            raise AssertionError("intentional post-admission failure")
        completed = _wait_for_completed(store, str(preview["mission_id"]))
        assert completed["current_step"] == 2
        state = store.load()
        assert [item["state"] for item in state["mission_attempts"]] == ["succeeded", "succeeded"]
        assert [item["state"] for item in state["mission_worker_replies"]] == ["validated", "validated"]
        assert [item["state"] for item in state["mission_handoffs"]] == ["recorded", "recorded"]
        assert state["mission_worker_replies"][0]["canonical_handoff"] == state["mission_handoffs"][0]["canonical_handoff"]
        persisted = json.dumps(state["mission_worker_replies"] + state["mission_handoffs"])
        assert "PRIVATE_REASONING_MARKER" not in persisted
        assert "FULL_TRANSCRIPT_MARKER" not in persisted
        assert "SECRET_MARKER" not in persisted

        prompt_records = [
            json.loads(line)
            for line in prompt_log.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("method") == "mission_prompt"
        ]
        assert [item["worker"] for item in prompt_records] == ["planner", "reviewer"]
        reviewer_prompt = prompt_records[1]["prompt"]
        assert "planner compact summary" in reviewer_prompt
        assert "planner deterministic verification" in reviewer_prompt
        assert '"risks": "none"' in reviewer_prompt
        assert "PRIVATE_REASONING_MARKER" not in reviewer_prompt
        assert "FULL_TRANSCRIPT_MARKER" not in reviewer_prompt
        assert "SECRET_MARKER" not in reviewer_prompt
        assert prompt_records[1]["recorded_handoffs"] == [state["mission_attempts"][0]["attempt_id"]]
    finally:
        if pid is None:
            pid = _discover_daemon_pid(root)
        if pid is not None:
            _stop_and_await_daemon(
                root, pid, force_terminate=force_cleanup_terminate
            )
        elif daemon_client_module._detached_reaper_count() != 0:
            raise AssertionError("daemon cleanup could not discover spawned process")


def test_confirmed_mission_continues_after_real_client_disconnect_twice(
) -> None:
    for label in ("first", "second"):
        parent = Path(tempfile.mkdtemp(prefix=f"agentdeck-m2-{label}-", dir="/tmp"))
        try:
            _run_real_daemon_case(parent.resolve() / "repo")
        finally:
            shutil.rmtree(parent, ignore_errors=True)


def test_real_daemon_cleanup_runs_after_early_post_admission_failure() -> None:
    parent = Path(tempfile.mkdtemp(prefix="agentdeck-m2-cleanup-", dir="/tmp"))
    root = parent.resolve() / "repo"
    try:
        with pytest.raises(AssertionError, match="intentional post-admission failure"):
            _run_real_daemon_case(
                root,
                fail_after_admission=True,
                force_cleanup_terminate=True,
            )
        endpoint = daemon_endpoint(root)
        assert not endpoint.socket_path.exists()
        assert not endpoint.metadata_path.exists()
        assert daemon_client_module._detached_reaper_count() == 0
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_real_daemon_reaps_blocked_tmux_command_before_process_exit(
    monkeypatch,
) -> None:
    parent = Path(tempfile.mkdtemp(prefix="agentdeck-m2-tmux-timeout-", dir="/tmp"))
    root = parent.resolve() / "repo"
    daemon_pid: int | None = None
    try:
        root.mkdir(parents=True)
        (root / ".git").mkdir()
        write_default_config(root)
        config_path = root / ".agentdeck" / "config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                'provider = "deepseek"', 'provider = "fake"', 1
            ).replace('model = "deepseek-chat"', 'model = "fake-plan"', 1),
            encoding="utf-8",
        )
        fake_bin = parent / "bin"
        fake_bin.mkdir()
        fake_tmux = fake_bin / "tmux"
        fake_tmux_pid_path = parent / "fake-tmux.pid"
        fake_tmux.write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            "from pathlib import Path\n"
            "import time\n"
            "with Path(os.environ['AGENTDECK_FAKE_TMUX_PID']).open('a') as stream:\n"
            "    stream.write(str(os.getpid()) + '\\n')\n"
            "while True:\n"
            "    time.sleep(60)\n",
            encoding="utf-8",
        )
        fake_tmux.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv(
            "AGENTDECK_FAKE_TMUX_PID", str(fake_tmux_pid_path)
        )
        config = load_config(root)
        store = StateStore(root)
        state = store.load()
        state["agents"]["planner"] = {
            "agent_id": "planner",
            "pane_id": "%1",
            "session_name": config.runtime.session_name,
            "cwd": str(root),
            "status": "running",
        }
        state["agents"]["reviewer"] = {
            "agent_id": "reviewer",
            "pane_id": "%2",
            "session_name": config.runtime.session_name,
            "cwd": str(root),
            "status": "running",
        }
        store.save(state)
        preview = create_mission_preview(
            config=config,
            store=store,
            provider=TwoWorkerProvider(),
            user_message="让 Codex 和 Claude 严格串行完成两步审阅，共2轮",
            timeout_seconds=30,
        )
        confirmed = confirm_mission_for_daemon(
            config=config, store=store, mission_id=preview["mission_id"]
        )

        async def admit_and_signal_stop() -> None:
            nonlocal daemon_pid
            client = await connect_or_start(root, config)
            await client.close()
            admitted = await admit_confirmed_mission(
                root, config, confirmed, state_store=store
            )
            assert admitted["accepted"] is True
            daemon_pid = int(json.loads(
                daemon_endpoint(root).metadata_path.read_text(encoding="utf-8")
            )["pid"])
            deadline = time.monotonic() + 3
            while not fake_tmux_pid_path.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert fake_tmux_pid_path.exists()
            os.kill(daemon_pid, signal.SIGTERM)

        started = time.monotonic()
        asyncio.run(admit_and_signal_stop())
        assert daemon_pid is not None
        _wait_for_daemon_shutdown(root, daemon_pid, timeout=12)
        assert time.monotonic() - started < 12
        fake_tmux_pids = [
            int(value)
            for value in fake_tmux_pid_path.read_text(encoding="utf-8").splitlines()
        ]
        assert fake_tmux_pids
        for child_pid in fake_tmux_pids:
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
        attempt = store.load()["mission_attempts"][0]
        assert attempt["state"] == "admitting"
    finally:
        if daemon_pid is None:
            daemon_pid = _discover_daemon_pid(root)
        if daemon_pid is not None:
            try:
                os.kill(daemon_pid, 0)
            except ProcessLookupError:
                pass
            else:
                _stop_and_await_daemon(root, daemon_pid, force_terminate=True)
        if fake_tmux_pid_path.exists():
            for value in fake_tmux_pid_path.read_text(encoding="utf-8").splitlines():
                try:
                    os.kill(int(value), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        assert daemon_client_module._detached_reaper_count() == 0
        shutil.rmtree(parent, ignore_errors=True)


def test_real_daemon_mission_resume_preview_confirm_uses_released_successor() -> None:
    parent = Path(tempfile.mkdtemp(prefix="agentdeck-m2-govern-", dir="/tmp"))
    root = parent.resolve() / "repo"
    pid: int | None = None
    try:
        root.mkdir(parents=True)
        (root / ".git").mkdir()
        write_default_config(root)
        config_path = root / ".agentdeck" / "config.toml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                'provider = "deepseek"', 'provider = "fake"', 1
            ).replace('model = "deepseek-chat"', 'model = "fake-plan"', 1),
            encoding="utf-8",
        )
        config = load_config(root)
        store = StateStore(root)
        preview = create_mission_preview(
            config=config, store=store, provider=TwoWorkerProvider(),
            user_message="让 Codex 和 Claude 严格串行完成两步审阅，共2轮",
            timeout_seconds=30,
        )
        confirmed = confirm_mission_for_daemon(
            config=config, store=store, mission_id=preview["mission_id"]
        )
        store.admit_mission_execution(
            str(preview["mission_id"]), snapshot_hash=str(confirmed["snapshot_hash"])
        )
        state = store.load()
        state["missions"][0]["status"] = "stopped"
        state["missions"][0]["stop_reason"] = "test_idle_boundary"
        store.save(state)

        async def run() -> tuple[dict[str, object], dict[str, object]]:
            client = await connect_or_start(root, config)
            await client.close()
            first = await govern_mission(
                root, config, mission_id=str(preview["mission_id"]), action="resume"
            )
            second = await govern_mission(
                root, config, mission_id=str(preview["mission_id"]), action="resume",
                preview_id=str(first["preview_id"]),
            )
            return first, second

        first, second = asyncio.run(run())
        metadata = json.loads(
            daemon_endpoint(root).metadata_path.read_text(encoding="utf-8")
        )
        pid = int(metadata["pid"])
        assert first["generation"] == 1
        assert second["state"] == "running"
        persisted = store.load()
        exact_preview = next(
            item for item in persisted["governance_previews"]
            if item["preview_id"] == first["preview_id"]
        )
        assert exact_preview["state"] == "consumed"
        events = [
            json.loads(line)
            for line in store.events_path.read_text(encoding="utf-8").splitlines()
        ]
        released = [
            item for item in events
            if item["event_type"] == "controller_lease_released"
        ]
        assert [item["payload"]["generation"] for item in released] == [1, 2]
        assert persisted["controller_lease"]["generation"] == 2
    finally:
        if pid is None:
            pid = _discover_daemon_pid(root)
        if pid is not None:
            _stop_and_await_daemon(root, pid, force_terminate=True)
        assert daemon_client_module._detached_reaper_count() == 0
        shutil.rmtree(parent, ignore_errors=True)
