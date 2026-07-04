from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import load_config, write_default_config
from agentdeck.state import StateStore


class FakeTmuxBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def bind_planner(root: Path) -> None:
    store = StateStore(root)
    state = store.load()
    state["agents"]["planner"] = {
        "agent_id": "planner",
        "pane_id": "%42",
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    store.save(state)


def test_default_config_includes_role_prompts(tmp_path, monkeypatch) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    config = load_config(root)

    planner = next(agent for agent in config.agents if agent.agent_id == "planner")
    assert planner.role == "planning"
    assert "任务拆解" in planner.role_prompt


def test_assign_role_updates_config_and_records_event(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(
        [
            "agent",
            "assign-role",
            "--agent",
            "planner",
            "--role",
            "architecture planning",
            "--role-prompt",
            "你负责架构规划和任务拆解。",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_id"] == "planner"
    assert payload["role"] == "architecture planning"
    assert payload["role_prompt"] == "你负责架构规划和任务拆解。"

    planner = next(agent for agent in load_config(root).agents if agent.agent_id == "planner")
    assert planner.role == "architecture planning"
    assert planner.role_prompt == "你负责架构规划和任务拆解。"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "agent_role_assigned"' in events


def test_dispatch_sends_role_prompt_task_and_records_event(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["dispatch", "--agent", "planner", "--task", "设计消息账本"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["agent_id"] == "planner"
    assert payload["pane_id"] == "%42"
    assert payload["message_id"].startswith("msg_")
    assert len(fake.sent) == 1
    pane_id, prompt = fake.sent[0]
    assert pane_id == "%42"
    assert "AgentDeck dispatch" in prompt
    assert "角色: planning" in prompt
    assert "任务拆解" in prompt
    assert "当前任务:" in prompt
    assert "设计消息账本" in prompt
    assert "status:" in prompt
    assert "summary:" in prompt

    state = StateStore(root).load()
    assert state["messages"][0]["message_id"] == payload["message_id"]
    assert state["messages"][0]["to_agent"] == "planner"
    assert state["messages"][0]["task"] == "设计消息账本"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "task_dispatched"' in events
