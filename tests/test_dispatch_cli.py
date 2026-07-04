from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import load_config, write_default_config
from agentdeck.contracts import validate_trace_contract
from agentdeck.state import StateStore


class FakeTmuxBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.output = ""

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        return self.output


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
    assert state["attempts"][0]["message_id"] == payload["message_id"]
    assert state["attempts"][0]["agent_id"] == "planner"
    assert state["jobs"][0]["message_id"] == payload["message_id"]
    assert state["jobs"][0]["attempt_id"] == state["attempts"][0]["attempt_id"]
    assert state["jobs"][0]["status"] == "dispatched"
    assert state["inbox"]["planner"][0]["event_type"] == "task_request"
    assert state["inbox"]["planner"][0]["message_id"] == payload["message_id"]

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "task_dispatched"' in events


def test_inbox_lists_task_requests_for_agent(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "planner", "--task", "设计消息账本"])
    capsys.readouterr()

    exit_code = cli.main(["inbox", "--agent", "planner"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_id"] == "planner"
    assert payload["count"] == 1
    assert payload["items"][0]["event_type"] == "task_request"
    assert payload["items"][0]["task"] == "设计消息账本"
    assert payload["items"][0]["status"] == "pending"


def test_reply_records_result_and_delivers_task_reply_to_sender_inbox(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--from-agent", "coder", "--agent", "planner", "--task", "审查实现方案"])
    dispatch_payload = json.loads(capsys.readouterr().out)

    exit_code = cli.main(
        [
            "reply",
            "--agent",
            "planner",
            "--message-id",
            dispatch_payload["message_id"],
            "--text",
            "status: completed\nsummary: 方案可行",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["reply_id"].startswith("rep_")
    assert payload["message_id"] == dispatch_payload["message_id"]

    state = StateStore(root).load()
    assert state["messages"][0]["status"] == "replied"
    assert state["attempts"][0]["status"] == "completed"
    assert state["jobs"][0]["status"] == "completed"
    assert state["replies"][0]["reply_id"] == payload["reply_id"]
    assert state["replies"][0]["from_agent"] == "planner"
    assert state["inbox"]["coder"][0]["event_type"] == "task_reply"
    assert state["inbox"]["coder"][0]["reply_id"] == payload["reply_id"]
    assert state["inbox"]["coder"][0]["status"] == "pending"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "task_replied"' in events


def test_ack_marks_inbox_item_acked(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "planner", "--task", "设计消息账本"])
    message_payload = json.loads(capsys.readouterr().out)
    inbox_id = StateStore(root).load()["inbox"]["planner"][0]["inbox_id"]

    exit_code = cli.main(["ack", "--agent", "planner", "--inbox-id", inbox_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "agent_id": "planner", "inbox_id": inbox_id, "status": "acked"}
    item = StateStore(root).load()["inbox"]["planner"][0]
    assert item["message_id"] == message_payload["message_id"]
    assert item["status"] == "acked"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "inbox_item_acked"' in events


def test_ack_rejects_non_head_pending_inbox_item(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "planner", "--task", "第一项"])
    capsys.readouterr()
    cli.main(["dispatch", "--agent", "planner", "--task", "第二项"])
    capsys.readouterr()
    inbox_items = StateStore(root).load()["inbox"]["planner"]
    first_id = inbox_items[0]["inbox_id"]
    second_id = inbox_items[1]["inbox_id"]

    exit_code = cli.main(["ack", "--agent", "planner", "--inbox-id", second_id])

    assert exit_code == 1
    assert f"inbox item is not head: {second_id}; head is {first_id}" in capsys.readouterr().err
    state = StateStore(root).load()
    assert [item["status"] for item in state["inbox"]["planner"]] == ["pending", "pending"]

    exit_code = cli.main(["ack", "--agent", "planner", "--inbox-id", first_id])

    assert exit_code == 0
    capsys.readouterr()
    state = StateStore(root).load()
    assert [item["status"] for item in state["inbox"]["planner"]] == ["acked", "pending"]

    exit_code = cli.main(["ack", "--agent", "planner", "--inbox-id", second_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "agent_id": "planner", "inbox_id": second_id, "status": "acked"}
    state = StateStore(root).load()
    assert [item["status"] for item in state["inbox"]["planner"]] == ["acked", "acked"]


def test_trace_reconstructs_communication_lineage_from_reply_id(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--from-agent", "coder", "--agent", "planner", "--task", "审查实现方案"])
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    cli.main(["reply", "--agent", "planner", "--message-id", message_id, "--text", "status: completed"])
    reply_id = json.loads(capsys.readouterr().out)["reply_id"]

    exit_code = cli.main(["trace", "--id", reply_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["query_id"] == reply_id
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert validate_trace_contract(payload) == {"ok": True, "errors": []}
    assert payload["message"]["message_id"] == message_id
    assert payload["message"]["from_actor"] == "coder"
    assert payload["message"]["to_agent"] == "planner"
    assert payload["message"]["prompt"].startswith("# AgentDeck dispatch")
    assert payload["attempts"][0]["message_id"] == message_id
    assert payload["jobs"][0]["message_id"] == message_id
    assert payload["replies"][0]["reply_id"] == reply_id
    event_types = {item["event_type"] for item in payload["inbox_items"]}
    assert event_types == {"task_request", "task_reply"}


def test_capture_reply_extracts_latest_structured_reply_from_agent_output(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--from-agent", "coder", "--agent", "planner", "--task", "审查实现方案"])
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    fake.output = """random shell output
status: blocked
summary: first draft was incomplete

more output
status: completed
summary: 方案可行
files_read:
  - src/agentdeck/cli.py
verification:
  - command: pytest
    result: passed
"""

    exit_code = cli.main(["capture-reply", "--agent", "planner", "--message-id", message_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["reply_id"].startswith("rep_")
    assert payload["message_id"] == message_id
    assert payload["from_agent"] == "planner"
    assert payload["captured_lines"] == 7

    state = StateStore(root).load()
    reply = state["replies"][0]
    assert reply["reply_id"] == payload["reply_id"]
    assert reply["text"].startswith("status: completed")
    assert "summary: 方案可行" in reply["text"]
    assert state["messages"][0]["status"] == "replied"
    assert state["jobs"][0]["status"] == "completed"
    assert state["inbox"]["coder"][0]["event_type"] == "task_reply"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "reply_captured"' in events


def test_capture_reply_rejects_output_without_structured_status(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    fake.output = "plain output without a structured reply"
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "planner", "--task", "设计消息账本"])
    message_id = json.loads(capsys.readouterr().out)["message_id"]

    exit_code = cli.main(["capture-reply", "--agent", "planner", "--message-id", message_id])

    assert exit_code == 1
    assert "no structured reply found for agent: planner" in capsys.readouterr().err
    assert StateStore(root).load().get("replies", []) == []
