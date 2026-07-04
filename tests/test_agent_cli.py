from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore


class FakeTmuxBackend:
    def __init__(self) -> None:
        self.created_sessions = 0
        self.spawned: list[tuple[str, str]] = []
        self.captured: list[tuple[str, int]] = []
        self.sent: list[tuple[str, str]] = []
        self.killed: list[str] = []

    def create_session(self, _config) -> None:
        self.created_sessions += 1

    def spawn_agent(self, _config, agent, cwd: str) -> str:
        self.spawned.append((agent.agent_id, cwd))
        return "%42"

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        self.captured.append((pane_id, lines))
        return "planner output\n"

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def kill_pane(self, _config, pane_id: str) -> None:
        self.killed.append(pane_id)


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def test_agent_list_outputs_configured_agents(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["agent", "list"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [agent["agent_id"] for agent in payload["agents"]] == ["planner", "coder", "reviewer"]
    assert payload["agents"][0]["runtime"]["status"] == "configured"


def test_doctor_reports_openai_compatible_provider_state(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    monkeypatch.delenv("AGENTDECK_LEADER_API_KEY", raising=False)

    exit_code = cli.main(["doctor"])

    assert exit_code in {0, 1}
    payload = json.loads(capsys.readouterr().out)
    assert payload["openai_compatible"] == {
        "ok": False,
        "detail": "AGENTDECK_LEADER_API_KEY is not set; provider calls are disabled",
    }


def test_status_includes_project_state_summaries(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["plans"].append(
        {
            "plan_id": "pln_demo",
            "task": "构建 ProjectView",
            "provider": "fake",
            "model": "local-plan",
            "status": "planned",
            "dispatch_ready": False,
            "created_at": "2026-07-04T00:00:00+00:00",
            "plan": {
                "steps": [
                    {"agent_id": "planner", "task": "拆解状态快照"},
                    {"agent_id": "coder", "task": "实现聚合"},
                ]
            },
        }
    )
    state["approvals"].append(
        {
            "approval_id": "apv_demo",
            "plan_id": "pln_demo",
            "step_index": 0,
            "agent_id": "planner",
            "task": "拆解状态快照",
            "status": "pending",
            "message_id": None,
            "job_id": None,
        }
    )
    state["messages"].append(
        {
            "message_id": "msg_demo",
            "from_actor": "leader",
            "to_agent": "planner",
            "task": "拆解状态快照",
            "status": "replied",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    )
    state["jobs"].append(
        {
            "job_id": "job_demo",
            "message_id": "msg_demo",
            "agent_id": "planner",
            "status": "completed",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    )
    state["replies"].append(
        {
            "reply_id": "rep_demo",
            "message_id": "msg_demo",
            "job_id": "job_demo",
            "from_agent": "planner",
            "to_actor": "leader",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    )
    state["chat_turns"] = [
        {
            "turn_id": "cht_demo",
            "mode": "review",
            "message": "继续",
            "plan_id": "pln_demo",
            "next_command": "agentdeck approval create-from-plan --plan-id pln_demo",
            "action_id": "act_demo",
            "action_kind": "create_approvals",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["leader_errors"] = [
        {
            "error_id": "err_demo",
            "mode": "plan",
            "provider": "openai-compatible",
            "model": "leader-model",
            "task": "坏响应",
            "error": "provider plan content is not valid JSON",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["leader_actions"] = [
        {
            "action_id": "act_demo",
            "kind": "create_approvals",
            "status": "pending",
            "requires_confirmation": True,
            "plan_id": "pln_demo",
            "approval_id": None,
            "agent_id": None,
            "message_id": None,
            "command": "agentdeck approval create-from-plan --plan-id pln_demo",
            "reason": "plan has no approval records",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["inbox"] = {
        "planner": [
            {
                "inbox_id": "inb_demo",
                "event_type": "task_request",
                "message_id": "msg_demo",
                "from_actor": "leader",
                "to_agent": "planner",
                "task": "构建 ProjectView",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ],
        "coder": [
            {
                "inbox_id": "inb_coder_done",
                "event_type": "task_request",
                "message_id": "msg_done",
                "from_actor": "leader",
                "to_agent": "coder",
                "task": "已处理任务",
                "status": "acked",
                "created_at": "2026-07-04T00:00:01+00:00",
            },
            {
                "inbox_id": "inb_coder_head",
                "event_type": "task_reply",
                "message_id": "msg_coder",
                "reply_id": "rep_coder",
                "from_agent": "planner",
                "to_agent": "coder",
                "task": "等待处理回复",
                "status": "pending",
                "created_at": "2026-07-04T00:00:02+00:00",
            },
        ],
        "reviewer": [
            {
                "inbox_id": "inb_reviewer_done",
                "event_type": "task_request",
                "message_id": "msg_reviewer",
                "from_actor": "leader",
                "to_agent": "reviewer",
                "task": "已确认",
                "status": "acked",
                "created_at": "2026-07-04T00:00:03+00:00",
            }
        ]
    }
    store.save(state)

    exit_code = cli.main(["status"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plans"]["count"] == 1
    assert payload["plans"]["items"][0] == {
        "plan_id": "pln_demo",
        "task": "构建 ProjectView",
        "status": "planned",
        "provider": "fake",
        "model": "local-plan",
        "dispatch_ready": False,
        "step_count": 2,
        "created_at": "2026-07-04T00:00:00+00:00",
    }
    assert payload["approvals"]["count"] == 1
    assert payload["approvals"]["pending"] == 1
    assert payload["messages"]["by_status"] == {"replied": 1}
    assert payload["jobs"]["by_status"] == {"completed": 1}
    assert payload["replies"]["items"][0]["reply_id"] == "rep_demo"
    assert payload["chat_turns"] == {
        "count": 1,
        "by_mode": {"review": 1},
        "items": [
            {
                "turn_id": "cht_demo",
                "mode": "review",
                "message": "继续",
                "plan_id": "pln_demo",
                "next_command": "agentdeck approval create-from-plan --plan-id pln_demo",
                "action_id": "act_demo",
                "action_kind": "create_approvals",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ],
    }
    assert payload["leader_actions"] == {
        "count": 1,
        "by_kind": {"create_approvals": 1},
        "by_status": {"pending": 1},
        "items": [
            {
                "action_id": "act_demo",
                "kind": "create_approvals",
                "status": "pending",
                "requires_confirmation": True,
                "plan_id": "pln_demo",
                "approval_id": None,
                "agent_id": None,
                "message_id": None,
                "command": "agentdeck approval create-from-plan --plan-id pln_demo",
                "reason": "plan has no approval records",
                "can_apply": True,
                "apply_command": "agentdeck leader apply-action --action-id act_demo",
                "explicit_command": "agentdeck approval create-from-plan --plan-id pln_demo",
                "apply_blocker": None,
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ],
    }
    assert payload["leader_errors"] == {
        "count": 1,
        "by_mode": {"plan": 1},
        "items": [
            {
                "error_id": "err_demo",
                "mode": "plan",
                "provider": "openai-compatible",
                "model": "leader-model",
                "task": "坏响应",
                "error": "provider plan content is not valid JSON",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ],
    }
    assert payload["inbox"] == {
        "total": 4,
        "by_agent": {"planner": 1, "coder": 2, "reviewer": 1},
        "by_status": {"pending": 2, "acked": 2},
        "heads": {
            "planner": {
                "inbox_id": "inb_demo",
                "event_type": "task_request",
                "message_id": "msg_demo",
                "reply_id": None,
                "from_actor": "leader",
                "from_agent": None,
                "to_agent": "planner",
                "task": "构建 ProjectView",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            },
            "coder": {
                "inbox_id": "inb_coder_head",
                "event_type": "task_reply",
                "message_id": "msg_coder",
                "reply_id": "rep_coder",
                "from_actor": None,
                "from_agent": "planner",
                "to_agent": "coder",
                "task": "等待处理回复",
                "status": "pending",
                "created_at": "2026-07-04T00:00:02+00:00",
            },
            "reviewer": None,
        },
    }


def test_agent_spawn_records_pane_binding_and_event(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["agent", "spawn", "--agent", "planner"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_id"] == "planner"
    assert payload["pane_id"] == "%42"
    assert fake.created_sessions == 1
    assert fake.spawned == [("planner", str(root))]

    state = StateStore(root).load()
    assert state["agents"]["planner"]["pane_id"] == "%42"
    assert state["agents"]["planner"]["status"] == "running"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "agent_spawned"' in events


def test_agent_spawn_refuses_existing_running_binding(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
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
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["agent", "spawn", "--agent", "planner"])

    assert exit_code == 1
    assert "already running" in capsys.readouterr().err
    assert fake.created_sessions == 0
    assert fake.spawned == []


def test_agent_capture_reads_bound_pane(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
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
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["agent", "capture", "--agent", "planner", "--lines", "50"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_id"] == "planner"
    assert payload["pane_id"] == "%42"
    assert payload["output"] == "planner output\n"
    assert fake.captured == [("%42", 50)]


def test_agent_send_uses_bound_pane_and_records_event(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
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
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["agent", "send", "--agent", "planner", "--text", "continue"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "agent_id": "planner", "pane_id": "%42"}
    assert fake.sent == [("%42", "continue")]

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "agent_input_sent"' in events


def test_agent_stop_kills_bound_pane_and_marks_stopped(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
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
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["agent", "stop", "--agent", "planner"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "agent_id": "planner", "pane_id": "%42", "status": "stopped"}
    assert fake.killed == ["%42"]

    state = StateStore(root).load()
    assert state["agents"]["planner"]["pane_id"] is None
    assert state["agents"]["planner"]["status"] == "stopped"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "agent_stopped"' in events
