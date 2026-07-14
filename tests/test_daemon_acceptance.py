from __future__ import annotations

import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import signal
import shutil
import subprocess
import sys
import tempfile
import time

from agentdeck.config import load_config, write_default_config
from agentdeck import cli as cli_module
from agentdeck.contracts import (
    validate_daemon_runtime_contract,
    validate_mission_scheduler_contract,
)
from agentdeck.daemon.client import DaemonClient, admit_confirmed_mission, connect_or_start
from agentdeck.daemon.lifecycle import daemon_endpoint
from agentdeck.mission_orchestration import confirm_mission_for_daemon, create_mission_preview
from agentdeck.providers import LeaderPlanRequest
from agentdeck.state import StateStore


FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"


class AcceptanceProvider:
    name = "fake"

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        del request
        return {
            "goal": "implement then review",
            "summary": "two ordered workers",
            "steps": [
                {"step": 1, "agent_id": "planner", "role": "planning", "task": "implement", "risk": "review", "requires_approval": True},
                {"step": 2, "agent_id": "reviewer", "role": "review", "task": "review", "risk": "review", "requires_approval": True},
            ],
            "approval_required": True,
            "dispatch_ready": False,
            "declared_tests": ["deterministic acceptance"],
            "acceptance_criteria": ["ordered handoff"],
        }


def _wait(store: StateStore, predicate, timeout: float = 12) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = store.load()
        if predicate(state):
            return state
        time.sleep(0.05)
    state = store.load()
    runtime = store.root / ".agentdeck" / "runtime"
    diagnostics = {
        path.name: path.read_text(encoding="utf-8", errors="replace")[-2000:]
        for path in runtime.glob("*.log")
    }
    raise AssertionError(
        "timed out waiting for deterministic daemon state: "
        + repr({key: state.get(key) for key in (
            "missions", "mission_attempts", "permission_requests",
            "agent_sessions", "protocol_turns", "recovery_decisions", "daemon_runtime",
        )}) + " runtime=" + repr(diagnostics)
    )


async def _decide_pending_permission(root: Path, permission_id: str) -> None:
    client = await DaemonClient.connect_verified(root)
    try:
        lease = await client.request("controller.acquire", {"client_id": "acceptance-reconnect"})
        authority = {
            "lease_id": lease["lease_id"],
            "lease_generation": lease["generation"],
        }
        params = {"permission_id": permission_id, "decision": "approved"}
        preview = await client.request("permission.decide", params, **authority)
        result = await client.request(
            "permission.decide", {**params, "preview_id": preview["preview_id"]}, **authority
        )
        assert result["state"] == "approved"
        await client.request(
            "controller.release",
            {"lease_id": lease["lease_id"], "generation": lease["generation"]},
            **authority,
        )
    finally:
        await client.close()


async def _stop(root: Path) -> None:
    client = await DaemonClient.connect_verified(root)
    try:
        lease = await client.request("controller.acquire", {"client_id": "acceptance-cleanup"})
        await client.request(
            "daemon.stop",
            {"lease_id": lease["lease_id"], "generation": lease["generation"]},
            lease_id=lease["lease_id"],
            lease_generation=lease["generation"],
        )
    finally:
        await client.close()


def _write_fake_tmux(bin_dir: Path, prompt_path: Path, order_path: Path) -> None:
    executable = bin_dir / "tmux"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os, re, sys\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "prompt=Path(os.environ['AGENTDECK_ACCEPTANCE_TMUX_PROMPT'])\n"
        "order=Path(os.environ['AGENTDECK_ACCEPTANCE_ORDER'])\n"
        "if 'load-buffer' in args:\n"
        " prompt.write_text(sys.stdin.read(), encoding='utf-8'); order.open('a').write('tmux-admit\\n')\n"
        "elif 'capture-pane' in args:\n"
        " text=prompt.read_text(encoding='utf-8'); token=re.findall(r'dsp_[0-9a-f]{32}', text)[-1]\n"
        " print('\\n'.join([f'handoff_token: {token}','status: completed','summary: reviewer compact summary','verification: reviewer deterministic verification','risks: none','next_steps: done']))\n"
        "elif 'display-message' in args:\n"
        " print('%acceptance')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)


def _render_recovery_through_bare_pty(root: Path, mission_id: str) -> bytes:
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", "from agentdeck.cli import main; raise SystemExit(main())"],
        cwd=root,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 8
    try:
        while time.monotonic() < deadline and mission_id.encode() not in output:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
            if process.poll() is not None:
                break
        return bytes(output)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        os.close(master)


def test_background_mission_acceptance_orders_workers_and_recovers_controller(
    monkeypatch,
) -> None:
    parent = Path(tempfile.mkdtemp(prefix="agentdeck-m2-acceptance-", dir="/tmp"))
    root = (parent / "repo").resolve()
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    prompt_log = root / "acp-events.jsonl"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    text = text.replace(
        'role = "planning"',
        'role = "planning"\ntransport = "acp"\ntransport_command = '
        + repr([sys.executable, str(FAKE_AGENT), "mission_worker_permission", str(prompt_log), "planner"]),
        1,
    )
    text = text.replace('role = "review"', 'role = "review"\ntransport = "tmux"', 1)
    config_path.write_text(text, encoding="utf-8")

    fake_bin = parent / "bin"
    fake_bin.mkdir()
    tmux_prompt = root / "tmux-prompt.txt"
    order_log = root / "transport-order.log"
    _write_fake_tmux(fake_bin, tmux_prompt, order_log)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AGENTDECK_ACCEPTANCE_TMUX_PROMPT", str(tmux_prompt))
    monkeypatch.setenv("AGENTDECK_ACCEPTANCE_ORDER", str(order_log))

    config = load_config(root)
    store = StateStore(root)
    state = store.load()
    state["agents"]["reviewer"] = {
        "agent_id": "reviewer", "pane_id": "%acceptance",
        "session_name": config.runtime.session_name, "cwd": str(root), "status": "running",
    }
    store.save(state)
    preview = create_mission_preview(
        config=config, store=store, provider=AcceptanceProvider(),
        user_message="让 Codex 和 Claude 严格串行完成实现与审阅，共2轮",
        timeout_seconds=20,
    )
    confirmed = confirm_mission_for_daemon(
        config=config, store=store, mission_id=str(preview["mission_id"])
    )

    async def admit_and_disconnect() -> None:
        client = await connect_or_start(root, config)
        await client.close()
        accepted = await admit_confirmed_mission(root, config, confirmed, state_store=store)
        assert accepted["accepted"] is True

    asyncio.run(admit_and_disconnect())
    try:
        pending = _wait(
            store,
            lambda item: bool(item.get("permission_requests"))
            and item["permission_requests"][-1].get("status") == "pending",
        )
        permission_id = str(pending["permission_requests"][-1]["permission_id"])
        assert not tmux_prompt.exists()

        daemon_metadata = json.loads(
            daemon_endpoint(root).metadata_path.read_text(encoding="utf-8")
        )
        rendered = _render_recovery_through_bare_pty(
            root, str(preview["mission_id"])
        )
        assert str(preview["mission_id"]).encode() in rendered
        assert json.loads(
            daemon_endpoint(root).metadata_path.read_text(encoding="utf-8")
        )["instance_id"] == daemon_metadata["instance_id"]
        assert store.load()["permission_requests"][-1]["status"] == "pending"

        # A fresh controller reconnects, previews the exact decision, then confirms it.
        asyncio.run(_decide_pending_permission(root, permission_id))
        completed = _wait(
            store,
            lambda item: item.get("missions")
            and item["missions"][-1].get("status") == "completed",
        )
        attempts = completed["mission_attempts"]
        assert [item["configured_transport"] for item in attempts] == ["acp", "tmux"]
        assert [item["state"] for item in attempts] == ["succeeded", "succeeded"]
        assert [item["state"] for item in completed["mission_handoffs"]] == ["recorded", "recorded"]
        assert "planner compact summary" in tmux_prompt.read_text(encoding="utf-8")
        assert completed["mission_handoffs"][0]["attempt_id"] == attempts[0]["attempt_id"]
        assert completed["mission_handoffs"][1]["attempt_id"] == attempts[1]["attempt_id"]

        view = asdict(store.project_view(config))
        assert view["missions"]["items"][-1]["status"] == "completed"
        assert view["scheduler"]["state"] == "inactive"
        assert validate_daemon_runtime_contract(
            cli_module._daemon_runtime_card(view)
        )["ok"] is True
        assert validate_mission_scheduler_contract(
            cli_module._mission_scheduler_card(view)
        )["ok"] is True
        assert view["artifacts"]["count"] == 0
        assert len(completed["mission_worker_replies"]) == 2
        assert [item["attempt_id"] for item in completed["mission_worker_replies"]] == [
            item["attempt_id"] for item in attempts
        ]
        assert all(
            not item["canonical_handoff"].get("trace_ids")
            and not item["canonical_handoff"].get("artifacts")
            for item in completed["mission_handoffs"]
        )
        assert completed["missions"][-1]["execution_snapshot"]["execution_hash"] == completed["missions"][-1]["snapshot_hash"]
        tmux_bytes = tmux_prompt.read_bytes()
        assert hashlib.sha256(tmux_bytes).hexdigest()
        assert attempts[1]["dispatch_key"].encode() in tmux_bytes
        persisted = json.dumps(completed, sort_keys=True)
        for forbidden in ("PRIVATE_REASONING_MARKER", "FULL_TRANSCRIPT_MARKER", "SECRET_MARKER"):
            assert forbidden not in persisted
        assert store.all_events()
        assert daemon_endpoint(root).metadata_path.exists()
    finally:
        try:
            asyncio.run(_stop(root))
        except Exception:
            metadata = daemon_endpoint(root).metadata_path
            if metadata.exists():
                os.kill(int(json.loads(metadata.read_text())["pid"]), signal.SIGTERM)
        shutil.rmtree(parent, ignore_errors=True)
