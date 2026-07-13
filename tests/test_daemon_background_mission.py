from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import sys
import tempfile
import time

from agentdeck.config import load_config, write_default_config
from agentdeck.daemon.client import DaemonClient, admit_confirmed_mission, connect_or_start
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
            }
        )
    )


def test_confirmed_mission_continues_after_real_client_disconnect(request) -> None:
    root = Path(tempfile.mkdtemp(prefix="agentdeck-mission-", dir="/tmp")).resolve() / "repo"
    request.addfinalizer(lambda: shutil.rmtree(root.parent, ignore_errors=True))
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    command = f'[{sys.executable!r}, {str(FAKE_AGENT)!r}, "mission_worker"]'
    text = text.replace(
        'role = "planning"',
        f'role = "planning"\ntransport = "acp"\ntransport_command = {command}',
        1,
    )
    text = text.replace(
        'role = "review"',
        f'role = "review"\ntransport = "acp"\ntransport_command = {command}',
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

    admitted = asyncio.run(admit_then_disconnect())
    assert admitted["accepted"] is True
    completed = _wait_for_completed(store, str(preview["mission_id"]))
    assert completed["current_step"] == 2
    state = store.load()
    assert [item["state"] for item in state["mission_attempts"]] == ["succeeded", "succeeded"]
    assert [item["state"] for item in state["mission_worker_replies"]] == ["validated", "validated"]
    assert [item["state"] for item in state["mission_handoffs"]] == ["recorded", "recorded"]

    async def stop() -> None:
        client = await DaemonClient.connect_verified(root)
        lease = await client.request("controller.acquire", {"client_id": "test-cleanup"})
        await client.request(
            "daemon.stop",
            {"lease_id": lease["lease_id"], "generation": lease["generation"]},
            lease_id=lease["lease_id"], lease_generation=lease["generation"],
        )
        await client.close()

    asyncio.run(stop())
