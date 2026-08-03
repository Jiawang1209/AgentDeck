from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_assign_role_roundtrip_does_not_add_transport_keys_to_legacy_config(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_path = root / ".agentdeck" / "config.toml"
    assert "transport" not in config_path.read_text(encoding="utf-8")

    assert cli.main(
        [
            "agent", "assign-role", "--agent", "planner",
            "--role", "architecture", "--role-prompt", "Plan only.",
        ]
    ) == 0
    capsys.readouterr()

    text = config_path.read_text(encoding="utf-8")
    assert "transport =" not in text
    assert "transport_command =" not in text
    planner = next(agent for agent in load_config(root).agents if agent.agent_id == "planner")
    assert planner.transport == "tmux"
    assert planner.transport_command == ()


@pytest.mark.parametrize(
    "malformed",
    [
        'transport = "ssh"\ntransport_command = ["agent"]',
        'transport = "acp"\ntransport_command = [true]',
        'transport = "acp"\ntransport_command = []',
    ],
)
def test_assign_role_rejects_malformed_transport_without_rewriting_config(
    tmp_path, monkeypatch, malformed
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_path = root / ".agentdeck" / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'command = "codex"', f'command = "codex"\n{malformed}', 1
        ),
        encoding="utf-8",
    )
    before = config_path.read_bytes()

    with pytest.raises(ValueError, match="transport"):
        cli.main(
            [
                "agent", "assign-role", "--agent", "planner",
                "--role", "architecture", "--role-prompt", "Plan only.",
            ]
        )

    assert config_path.read_bytes() == before


def test_dispatch_sends_role_prompt_task_and_records_event(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    planner = next(agent for agent in load_config(root).agents if agent.agent_id == "planner")
    assert planner.command == "codex"
    assert planner.transport == "tmux"
    assert planner.transport_command == ()

    exit_code = cli.main(["dispatch", "--agent", "planner", "--task", "设计消息账本"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["agent_id"] == "planner"
    assert payload["pane_id"] == "%42"
    assert payload["message_id"].startswith("msg_")
    assert payload["trace_command"] == f"agentdeck trace --id {payload['message_id']}"
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
    assert payload["head_inbox_id"] == payload["items"][0]["inbox_id"]
    assert payload["items"][0]["is_head"] is True
    assert payload["items"][0]["can_ack"] is True
    assert payload["items"][0]["ack_blocker"] is None
    assert payload["items"][0]["ack_command"] == f"agentdeck ack --agent planner --inbox-id {payload['head_inbox_id']}"
    assert payload["items"][0]["trace_command"] == f"agentdeck trace --id {payload['head_inbox_id']}"


def test_inbox_refuses_contract_violation(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "planner", "--task", "坏 inbox 不能输出"])
    capsys.readouterr()

    def broken_validation(_payload):
        return {"ok": False, "errors": ["missing inbox item field: ack_blocker"]}

    monkeypatch.setattr(cli, "validate_inbox_contract", broken_validation)

    exit_code = cli.main(["inbox", "--agent", "planner"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Inbox contract validation failed" in captured.err
    assert "missing inbox item field: ack_blocker" in captured.err
    assert StateStore(root).load()["inbox"]["planner"][0]["status"] == "pending"


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
    assert payload["trace_command"] == f"agentdeck trace --id {payload['reply_id']}"

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
    assert payload["trace_command"] == f"agentdeck trace --id {payload['reply_id']}"

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


def test_dispatch_prompt_declares_reply_file_channel(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["dispatch", "--agent", "planner", "--task", "分析首页布局"])

    assert exit_code == 0
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    reply_file = root / ".agentdeck" / "replies" / f"{message_id}.reply.txt"
    prompt = StateStore(root).load()["messages"][0]["prompt"]
    assert "回复通道:" in prompt
    assert str(reply_file) in prompt
    assert reply_file.parent.is_dir()


def test_capture_reply_prefers_reply_file_channel_over_pane(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "planner", "--task", "分析首页布局"])
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    # 2026-07-24 live finding: Claude Code TUI 清滚动区，回复滚出可视区后 pane 刮取永久失败；
    # worker 按约定把同一份结构化回复写进 reply 文件，capture 必须优先读文件。
    reply_file = root / ".agentdeck" / "replies" / f"{message_id}.reply.txt"
    reply_file.parent.mkdir(parents=True, exist_ok=True)
    reply_file.write_text(
        "status: completed\nsummary: 来自文件通道\nfull_output_path: /tmp/report.md\n",
        encoding="utf-8",
    )
    fake.output = "tui noise without any structured block"

    exit_code = cli.main(["capture-reply", "--agent", "planner", "--message-id", message_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["captured_from"] == "file"
    reply = StateStore(root).load()["replies"][0]
    assert reply["text"].splitlines()[0] == "status: completed"
    assert "summary: 来自文件通道" in reply["text"]


def test_capture_reply_parses_multiline_full_output_path(tmp_path, monkeypatch, capsys) -> None:
    # round 6 live 发现：codex 把 full_output_path 的值写在字段下一行
    # （YAML 缩进风格），单行解析器取不到值导致 artifact 未登记。
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "planner", "--task", "分析首页布局"])
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    reply_file = root / ".agentdeck" / "replies" / f"{message_id}.reply.txt"
    reply_file.parent.mkdir(parents=True, exist_ok=True)
    reply_file.write_text(
        "status: completed\n"
        "summary: 产物路径在下一行\n"
        "full_output_path:\n"
        "  /tmp/round6-report.md\n",
        encoding="utf-8",
    )

    exit_code = cli.main(["capture-reply", "--agent", "planner", "--message-id", message_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    artifacts = StateStore(root).load().get("artifacts", [])
    assert [a["path"] for a in artifacts] == ["/tmp/round6-report.md"]


def test_capture_reply_multiline_key_without_value_registers_no_artifact(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "planner", "--task", "分析首页布局"])
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    reply_file = root / ".agentdeck" / "replies" / f"{message_id}.reply.txt"
    reply_file.parent.mkdir(parents=True, exist_ok=True)
    # 空的 full_output_path 后面紧跟另一个 key：不得把下一个 key 行当路径。
    reply_file.write_text(
        "status: completed\n"
        "summary: 没有产物\n"
        "full_output_path:\n"
        "risks: 无\n",
        encoding="utf-8",
    )

    exit_code = cli.main(["capture-reply", "--agent", "planner", "--message-id", message_id])

    assert exit_code == 0
    capsys.readouterr()
    assert StateStore(root).load().get("artifacts", []) == []


def test_capture_reply_falls_back_to_pane_when_reply_file_has_no_status(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "planner", "--task", "分析首页布局"])
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    reply_file = root / ".agentdeck" / "replies" / f"{message_id}.reply.txt"
    reply_file.parent.mkdir(parents=True, exist_ok=True)
    reply_file.write_text("还在干活，没有结构化回复\n", encoding="utf-8")
    fake.output = "shell noise\nstatus: completed\nsummary: 来自 pane\n"

    exit_code = cli.main(["capture-reply", "--agent", "planner", "--message-id", message_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["captured_from"] == "pane"
    reply = StateStore(root).load()["replies"][0]
    assert "summary: 来自 pane" in reply["text"]


def test_capture_reply_tolerates_codex_bullet_decorated_status_line(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--from-agent", "coder", "--agent", "planner", "--task", "分析首页布局"])
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    # 2026-07-24 live finding: codex TUI 渲染回复首行为 "• status: completed"，
    # 装饰符导致 startswith("status:") 匹配失败（round 2 两个 codex worker 均复现）。
    fake.output = """codex ui noise
  请按以下格式返回:
  status: completed | blocked | failed

• status: completed
  summary: 已完成首页布局分析
  files_written: iae-homepage-analysis.md
  full_output_path: /tmp/iae-homepage-analysis.md
"""

    exit_code = cli.main(["capture-reply", "--agent", "planner", "--message-id", message_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True

    state = StateStore(root).load()
    reply = state["replies"][0]
    assert reply["text"].splitlines()[0] == "status: completed"
    assert "summary: 已完成首页布局分析" in reply["text"]
    assert state["messages"][0]["status"] == "replied"


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


def _bind_agent(root: Path, agent_id: str, pane_id: str = "%42") -> None:
    store = StateStore(root)
    state = store.load()
    state["agents"][agent_id] = {
        "agent_id": agent_id,
        "pane_id": pane_id,
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    store.save(state)


def _init_real_git(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)


def test_dispatch_creates_task_worktree_for_worktree_mode_agent(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    _bind_agent(root, "coder", "%50")  # default config: coder workspace_mode=worktree
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["dispatch", "--agent", "coder", "--task", "在隔离 worktree 中实现功能"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    message_id = payload["message_id"]
    state = StateStore(root).load()
    message = next(m for m in state["messages"] if m["message_id"] == message_id)
    expected_branch = f"agentdeck/coder/{message_id}"
    expected_path = str(root / ".agentdeck" / "worktrees" / "coder" / message_id)
    assert message["worktree_branch"] == expected_branch
    assert message["worktree_path"] == expected_path
    assert (Path(expected_path) / "README.md").is_file()
    worktrees = subprocess.run(
        ["git", "-C", str(root), "worktree", "list"], capture_output=True, text=True, check=True
    ).stdout
    assert expected_branch in worktrees
    prompt = fake.sent[0][1]
    assert expected_path in prompt
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "worktree_created"' in events


def test_worktree_dispatch_prompt_requires_commit_to_task_branch(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    _bind_agent(root, "coder", "%50")  # default config: coder workspace_mode=worktree
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["dispatch", "--agent", "coder", "--task", "在隔离环境中实现功能"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    message_id = payload["message_id"]
    prompt = fake.sent[0][1]
    assert f"agentdeck/coder/{message_id}" in prompt
    assert "git commit" in prompt
    assert "不要 push" in prompt


def test_worktree_dispatch_prompt_pins_artifacts_to_main_repo(tmp_path, monkeypatch, capsys) -> None:
    # round 6 live 发现：reviewer 把产物写进自己 worktree 内嵌的
    # .agentdeck/artifacts/，prune 时连带删除；产物必须钉到主仓库。
    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    _bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["dispatch", "--agent", "coder", "--task", "生成分析报告"])

    assert exit_code == 0
    capsys.readouterr()
    prompt = fake.sent[0][1]
    assert str(root / ".agentdeck" / "artifacts") in prompt
    assert "不要写进本 worktree" in prompt


def test_shared_dispatch_prompt_has_no_commit_requirement(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    _bind_agent(root, "planner", "%51")  # default config: planner workspace_mode=shared
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["dispatch", "--agent", "planner", "--task", "分析现状"])

    assert exit_code == 0
    capsys.readouterr()
    prompt = fake.sent[0][1]
    assert "git commit" not in prompt
    # 探针换成 worktree 段独有的措辞:原本用的 "不要 push" 现在也出现在发给
    # **每个** worker 的编排边界里(Round 1 live:reviewer 是 shared 工作区,
    # 因而从未收到过合并边界,然后它自己合并了)。本测试守的是"shared agent
    # 不该被要求 commit 到任务分支",那条不变;"不要 push / 不要合并"对 shared
    # agent 只会更该说——它干活的地方就是主工作区。
    assert "本任务专用 worktree" not in prompt


def test_dispatch_degrades_cleanly_without_real_git_repo(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)  # fake .git dir, not a real repo
    _bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["dispatch", "--agent", "coder", "--task", "普通派发"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    message_id = payload["message_id"]
    state = StateStore(root).load()
    message = next(m for m in state["messages"] if m["message_id"] == message_id)
    assert message["worktree_path"] is None
    assert message["worktree_branch"] is None
    assert ".agentdeck/worktrees" not in fake.sent[0][1]
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "worktree_skipped"' in events


def test_dispatch_shared_mode_agent_gets_no_worktree(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    bind_planner(root)  # planner workspace_mode=shared
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["dispatch", "--agent", "planner", "--task", "共享模式照旧"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    message_id = payload["message_id"]
    state = StateStore(root).load()
    message = next(m for m in state["messages"] if m["message_id"] == message_id)
    assert message["worktree_path"] is None
    assert message["worktree_branch"] is None
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "worktree_skipped"' not in events
    assert '"event_type": "worktree_created"' not in events


def _seed_approved_approval(root: Path, approval_id: str, plan_id: str, step: int, agent_id: str) -> None:
    store = StateStore(root)
    state = store.load()
    state.setdefault("approvals", []).append({
        "approval_id": approval_id, "plan_id": plan_id, "step": step,
        "agent_id": agent_id, "role": "implementation", "task": f"step {step} work",
        "risk": "low", "status": "approved", "created_at": "2026-07-25T00:00:00+00:00",
    })
    store.save(state)


def test_review_step_worktree_checks_out_earlier_step_branch(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    root = prepare_project(tmp_path, monkeypatch)
    config_path = root / ".agentdeck" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config_text.replace('workspace_mode = "shared"\nrole_prompt = "你是 AgentDeck 的审查', 'workspace_mode = "worktree"\nrole_prompt = "你是 AgentDeck 的审查'),
        encoding="utf-8",
    )
    _init_real_git(root)
    _bind_agent(root, "coder", "%50")
    _bind_agent(root, "reviewer", "%51")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    _seed_approved_approval(root, "apv_w1", "pln_w", 1, "coder")
    _seed_approved_approval(root, "apv_w2", "pln_w", 2, "reviewer")

    assert cli.main(["approval", "dispatch", "--approval-id", "apv_w1"]) == 0
    msg1 = json.loads(capsys.readouterr().out)["message_id"]
    state = StateStore(root).load()
    coder_message = next(m for m in state["messages"] if m["message_id"] == msg1)
    coder_branch = coder_message["worktree_branch"]
    assert coder_branch == f"agentdeck/coder/{msg1}"
    coder_wt = coder_message["worktree_path"]
    # coder 在自己的 worktree 分支上产出一个提交
    (Path(coder_wt) / "feature.txt").write_text("done\n", encoding="utf-8")
    subprocess.run(["git", "-C", coder_wt, "add", "feature.txt"], check=True)
    subprocess.run(["git", "-C", coder_wt, "commit", "-qm", "feature"], check=True)

    assert cli.main(["approval", "dispatch", "--approval-id", "apv_w2"]) == 0
    msg2 = json.loads(capsys.readouterr().out)["message_id"]
    state = StateStore(root).load()
    review_message = next(m for m in state["messages"] if m["message_id"] == msg2)
    assert review_message["worktree_branch"] == f"agentdeck/reviewer/{msg2}"
    assert review_message["worktree_base_branch"] == coder_branch
    # reviewer 的 worktree 基于 coder 分支——能看到 coder 的产出
    assert (Path(review_message["worktree_path"]) / "feature.txt").is_file()
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert events.count('"event_type": "worktree_created"') == 2


def test_worktree_list_and_diff_are_read_only(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    _bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "coder", "--task", "worktree 工作"])
    msg = json.loads(capsys.readouterr().out)["message_id"]
    state = StateStore(root).load()
    message = next(m for m in state["messages"] if m["message_id"] == msg)
    wt = message["worktree_path"]
    (Path(wt) / "new.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", wt, "add", "new.txt"], check=True)
    subprocess.run(["git", "-C", wt, "commit", "-qm", "add new"], check=True)
    (Path(wt) / "uncommitted.txt").write_text("y\n", encoding="utf-8")
    state_before = StateStore(root).load()

    assert cli.main(["worktree", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "worktree_list"
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["agent_id"] == "coder"
    assert item["message_id"] == msg
    assert item["branch"] == f"agentdeck/coder/{msg}"
    assert item["exists"] is True
    assert item["dirty"] is True
    assert item["merged"] is False
    assert item["abandoned"] is False
    assert item["diff_command"] == f"agentdeck worktree diff --message-id {msg}"
    assert item["trace_command"] == f"agentdeck trace --id {msg}"

    assert cli.main(["worktree", "diff", "--message-id", msg]) == 0
    diff_payload = json.loads(capsys.readouterr().out)
    assert diff_payload["mode"] == "worktree_diff"
    assert diff_payload["branch"] == f"agentdeck/coder/{msg}"
    assert "new.txt" in diff_payload["stat"]
    assert any(f["path"] == "new.txt" and f["status"] == "A" for f in diff_payload["files"])
    assert diff_payload["merge_command"] == f"agentdeck worktree merge --message-id {msg} --confirm"
    assert diff_payload["abandon_command"] == f"agentdeck worktree abandon --message-id {msg} --confirm"

    assert StateStore(root).load() == state_before


def test_worktree_diff_rejects_unknown_message(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)

    assert cli.main(["worktree", "diff", "--message-id", "msg_missing"]) == 1
    assert "unknown worktree message" in capsys.readouterr().err


def test_worktree_merge_requires_confirm_and_merges_branch(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    _bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "coder", "--task", "做功能"])
    msg = json.loads(capsys.readouterr().out)["message_id"]
    state = StateStore(root).load()
    wt = next(m for m in state["messages"] if m["message_id"] == msg)["worktree_path"]
    (Path(wt) / "merged.txt").write_text("done\n", encoding="utf-8")
    subprocess.run(["git", "-C", wt, "add", "merged.txt"], check=True)
    subprocess.run(["git", "-C", wt, "commit", "-qm", "feature"], check=True)
    before = StateStore(root).load()

    assert cli.main(["worktree", "merge", "--message-id", msg]) == 1
    assert "confirm" in capsys.readouterr().err
    assert StateStore(root).load() == before
    assert not (root / "merged.txt").is_file()

    assert cli.main(["worktree", "merge", "--message-id", msg, "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "worktree_merged"
    assert payload["branch"] == f"agentdeck/coder/{msg}"
    assert (root / "merged.txt").is_file()
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "worktree_merged"' in events

    # merged worktree becomes prunable
    assert cli.main(["worktree", "prune", "--confirm"]) == 0
    prune_payload = json.loads(capsys.readouterr().out)
    assert prune_payload["mode"] == "worktree_prune"
    assert msg in [item["message_id"] for item in prune_payload["removed"]]
    assert not Path(wt).exists()
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "worktree_pruned"' in events


def test_worktree_prune_skips_in_flight_zero_commit_worktree(tmp_path, monkeypatch, capsys) -> None:
    # round 6 live 发现：零 commit 分支尖==main 尖，merged 被平凡判真，
    # prune 会把仍在进行中的任务 worktree 当 merged-and-clean 删掉。
    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    _bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "coder", "--task", "刚派发还没动工"])
    msg = json.loads(capsys.readouterr().out)["message_id"]
    state = StateStore(root).load()
    wt = next(m for m in state["messages"] if m["message_id"] == msg)["worktree_path"]

    assert cli.main(["worktree", "list"]) == 0
    item = json.loads(capsys.readouterr().out)["items"][0]
    assert item["in_flight"] is True

    assert cli.main(["worktree", "prune", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["removed"] == []
    skipped = next(s for s in payload["skipped"] if s["message_id"] == msg)
    assert skipped["reason"] == "task still in flight"
    assert Path(wt).exists()


def test_worktree_replied_zero_commit_worktree_becomes_prunable(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    _bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "coder", "--task", "任务完成但无产物"])
    msg = json.loads(capsys.readouterr().out)["message_id"]
    state = StateStore(root).load()
    wt = next(m for m in state["messages"] if m["message_id"] == msg)["worktree_path"]
    assert cli.main(["reply", "--agent", "coder", "--message-id", msg, "--text", "status: completed\nsummary: 无需改动"]) == 0
    capsys.readouterr()

    assert cli.main(["worktree", "list"]) == 0
    item = json.loads(capsys.readouterr().out)["items"][0]
    assert item["in_flight"] is False

    assert cli.main(["worktree", "prune", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert msg in [r["message_id"] for r in payload["removed"]]
    assert not Path(wt).exists()


def _enable_autonomous_policy(capsys, allow: list[str]) -> None:
    cli.main([
        "policy", "set-mode", "--mode", "autonomous", "--confirm",
        *sum((["--allow-agent", a] for a in allow), []),
        "--max-approvals", "5",
    ])
    capsys.readouterr()


def _seed_worktree_plan(root: Path, agent_id: str) -> str:
    store = StateStore(root)
    state = store.load()
    plan_id = "pln_mergeplan_1"
    role = next(a.role for a in cli.load_config(root).agents if a.agent_id == agent_id)
    state.setdefault("plans", []).append({
        "plan_id": plan_id, "task": "g", "status": "planned",
        "provider": "fake", "model": "fake-plan",
        "plan": {
            "goal": "g", "summary": "s",
            "steps": [{"step": 1, "agent_id": agent_id, "role": role, "task": "do",
                       "risk": "low", "requires_approval": True}],
        },
        "created_at": "2026-07-26T00:00:00+00:00",
    })
    store.save(state)
    store.create_approvals_from_plan(plan_id)
    return plan_id


def test_worktree_merge_plan_requires_confirm_and_complete_gate(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    _bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    _enable_autonomous_policy(capsys, ["coder"])
    plan_id = _seed_worktree_plan(root, "coder")
    cli.main(["run-loop", "--plan-id", plan_id, "--confirm"])
    capsys.readouterr()

    head_before = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    # 缺 confirm：拒绝零写
    assert cli.main(["worktree", "merge-plan", "--plan-id", plan_id]) == 1
    assert "confirm" in capsys.readouterr().err
    # gate 未 complete（还在等回复）：拒绝零写
    assert cli.main(["worktree", "merge-plan", "--plan-id", plan_id, "--confirm"]) == 1
    assert "complete" in capsys.readouterr().err
    # 未知 plan：拒绝
    assert cli.main(["worktree", "merge-plan", "--plan-id", "pln_missing", "--confirm"]) == 1
    capsys.readouterr()
    head_after = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    assert head_after == head_before


def test_worktree_merge_plan_merges_completed_plan_branches(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    _bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    _enable_autonomous_policy(capsys, ["coder"])
    plan_id = _seed_worktree_plan(root, "coder")
    cli.main(["run-loop", "--plan-id", plan_id, "--confirm"])
    capsys.readouterr()
    state = StateStore(root).load()
    message = next(m for m in state["messages"] if m.get("worktree_path"))
    wt = message["worktree_path"]
    (Path(wt) / "feature.txt").write_text("done\n", encoding="utf-8")
    subprocess.run(["git", "-C", wt, "add", "feature.txt"], check=True)
    subprocess.run(["git", "-C", wt, "commit", "-qm", "feature"], check=True)
    reply_file = root / ".agentdeck" / "replies" / f"{message['message_id']}.reply.txt"
    reply_file.write_text("status: completed\nsummary: done\n", encoding="utf-8")
    cli.main(["run-loop", "--plan-id", plan_id, "--confirm"])
    capsys.readouterr()

    assert cli.main(["worktree", "merge-plan", "--plan-id", plan_id, "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "worktree_merge_plan"
    assert payload["plan_id"] == plan_id
    assert [m["message_id"] for m in payload["merged"]] == [message["message_id"]]
    assert (root / "feature.txt").is_file()
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "worktree_merged"' in events

    # 幂等：再跑一次全部 skip（already merged），零新 merge
    assert cli.main(["worktree", "merge-plan", "--plan-id", plan_id, "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["merged"] == []
    assert [s["reason"] for s in payload["skipped"]] == ["already merged"]


def test_worktree_prune_protects_dirty_until_abandoned(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    _bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "coder", "--task", "半途任务"])
    msg = json.loads(capsys.readouterr().out)["message_id"]
    state = StateStore(root).load()
    wt = next(m for m in state["messages"] if m["message_id"] == msg)["worktree_path"]
    (Path(wt) / "wip.txt").write_text("uncommitted\n", encoding="utf-8")

    # dirty 且未 abandon：prune 必须跳过并保留目录
    assert cli.main(["worktree", "prune", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["removed"] == []
    skipped_ids = [item["message_id"] for item in payload["skipped"]]
    assert msg in skipped_ids
    assert Path(wt).exists()

    # abandon 缺 confirm：拒绝零写
    before = StateStore(root).load()
    assert cli.main(["worktree", "abandon", "--message-id", msg]) == 1
    assert StateStore(root).load() == before

    assert cli.main(["worktree", "abandon", "--message-id", msg, "--confirm"]) == 0
    abandon_payload = json.loads(capsys.readouterr().out)
    assert abandon_payload["mode"] == "worktree_abandoned"
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "worktree_abandoned"' in events

    assert cli.main(["worktree", "prune", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert msg in [item["message_id"] for item in payload["removed"]]
    assert not Path(wt).exists()
