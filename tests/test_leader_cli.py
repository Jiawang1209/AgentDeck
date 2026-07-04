from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.providers.fake import FakeLeaderProvider
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


def bind_agent(root: Path, agent_id: str, pane_id: str = "%42") -> None:
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


def break_project_view_recovery(monkeypatch) -> None:
    original_asdict = cli.asdict

    def broken_project_view_asdict(obj):
        payload = original_asdict(obj)
        if obj.__class__.__name__ == "ProjectView":
            payload.pop("recovery", None)
        return payload

    monkeypatch.setattr(cli, "asdict", broken_project_view_asdict)


def test_leader_plan_creates_structured_plan_without_dispatching(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "plan", "--task", "实现自动 reply extraction"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["plan_id"].startswith("pln_")
    assert payload["status"] == "planned"
    assert payload["provider"] == "fake"
    assert payload["dispatch_ready"] is False
    assert payload["plan"]["goal"] == "实现自动 reply extraction"
    assert [step["agent_id"] for step in payload["plan"]["steps"]] == ["planner", "coder", "reviewer"]
    assert all(step["requires_approval"] is True for step in payload["plan"]["steps"])

    state = StateStore(root).load()
    assert state["plans"][0]["plan_id"] == payload["plan_id"]
    assert state["plans"][0]["task"] == "实现自动 reply extraction"
    assert state["plans"][0]["provider"] == "fake"
    assert state["plans"][0]["status"] == "planned"
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_plan_created"' in events


def test_leader_plan_rejects_unsupported_provider(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "plan", "--provider", "deepseek", "--task", "真实 provider 尚未接入"])

    assert exit_code == 1
    assert "unsupported leader provider: deepseek" in capsys.readouterr().err
    state = StateStore(root).load()
    assert state.get("plans", []) == []


def test_leader_plan_uses_openai_compatible_provider_without_dispatching(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    class StubProvider(FakeLeaderProvider):
        name = "openai-compatible"

    monkeypatch.setattr(cli, "leader_provider", lambda name: StubProvider())

    exit_code = cli.main(
        [
            "leader",
            "plan",
            "--provider",
            "openai-compatible",
            "--model",
            "leader-model",
            "--task",
            "真实 API 计划",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "openai-compatible"
    assert payload["model"] == "leader-model"
    assert payload["dispatch_ready"] is False

    state = StateStore(root).load()
    assert state["plans"][0]["provider"] == "openai-compatible"
    assert state["plans"][0]["model"] == "leader-model"
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}


def test_leader_plan_records_provider_error_without_dispatching(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    class BrokenProvider:
        name = "openai-compatible"

        def plan(self, _request):
            raise RuntimeError("provider plan content is not valid JSON")

    monkeypatch.setattr(cli, "leader_provider", lambda name: BrokenProvider())

    exit_code = cli.main(["leader", "plan", "--provider", "openai-compatible", "--task", "坏响应"])

    assert exit_code == 1
    assert "leader provider failed: provider plan content is not valid JSON" in capsys.readouterr().err
    state = StateStore(root).load()
    assert state["plans"] == []
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}
    assert state["leader_errors"][0]["mode"] == "plan"
    assert state["leader_errors"][0]["provider"] == "openai-compatible"
    assert state["leader_errors"][0]["task"] == "坏响应"
    assert state["leader_errors"][0]["error"] == "provider plan content is not valid JSON"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_provider_failed"' in events


def test_leader_chat_creates_plan_from_natural_language_without_dispatching(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "chat", "--message", "帮我实现自动回复回收"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "plan"
    assert payload["message"] == "帮我实现自动回复回收"
    assert payload["project_view"]["plans"]["count"] == 0
    assert payload["turn_id"].startswith("cht_")
    assert payload["plan_id"].startswith("pln_")
    assert payload["next_command"] == f"agentdeck approval create-from-plan --plan-id {payload['plan_id']}"
    assert payload["review"] is None

    state = StateStore(root).load()
    assert state["chat_turns"][0]["turn_id"] == payload["turn_id"]
    assert state["chat_turns"][0]["mode"] == "plan"
    assert state["chat_turns"][0]["message"] == "帮我实现自动回复回收"
    assert state["chat_turns"][0]["plan_id"] == payload["plan_id"]
    assert state["chat_turns"][0]["next_command"] == payload["next_command"]
    assert len(state["plans"]) == 1
    assert state["plans"][0]["task"] == "帮我实现自动回复回收"
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_chat_turn"' in events


def test_leader_chat_refuses_invalid_project_view_before_planning(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    break_project_view_recovery(monkeypatch)

    exit_code = cli.main(["leader", "chat", "--message", "帮我实现自动回复回收"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "ProjectView contract validation failed" in captured.err
    assert "missing top-level field: recovery" in captured.err
    state = StateStore(root).load()
    assert state["plans"] == []
    assert state["chat_turns"] == []
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}


def test_leader_chat_reviews_latest_plan_instead_of_creating_another_plan(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "已有计划"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()

    exit_code = cli.main(["leader", "chat", "--message", "继续"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "review"
    assert payload["message"] == "继续"
    assert payload["turn_id"].startswith("cht_")
    assert payload["plan_id"] == plan_id
    assert payload["project_view"]["plans"]["count"] == 1
    assert payload["review"]["next_action"] == "dispatch_approved"
    assert payload["next_command"] == f"agentdeck approval dispatch --approval-id {approval_id}"
    assert payload["leader_action"]["kind"] == "dispatch_approved"
    assert payload["leader_action"]["approval_id"] == approval_id
    assert payload["leader_action"]["command"] == payload["next_command"]
    assert payload["leader_action"]["can_apply"] is False
    assert payload["leader_action"]["apply_command"] is None
    assert payload["leader_action"]["explicit_command"] == payload["next_command"]
    assert payload["leader_action"]["apply_blocker"] == "leader action requires explicit command"

    state = StateStore(root).load()
    assert state["chat_turns"][0]["turn_id"] == payload["turn_id"]
    assert state["chat_turns"][0]["mode"] == "review"
    assert state["chat_turns"][0]["plan_id"] == plan_id
    assert state["chat_turns"][0]["review"]["next_action"] == "dispatch_approved"
    assert state["chat_turns"][0]["action_id"] == payload["leader_action"]["action_id"]
    assert state["chat_turns"][0]["action_kind"] == "dispatch_approved"
    assert len(state["leader_actions"]) == 1
    assert state["leader_actions"][0]["action_id"] == payload["leader_action"]["action_id"]
    assert len(state["plans"]) == 1
    assert state["messages"] == []
    assert state["jobs"] == []

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_chat_turn"' in events


def test_leader_chat_persists_create_approvals_action_for_existing_plan(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "已有计划但未审批"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]

    exit_code = cli.main(["leader", "chat", "--message", "下一步"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "review"
    assert payload["plan_id"] == plan_id
    assert payload["leader_action"]["kind"] == "create_approvals"
    assert payload["leader_action"]["plan_id"] == plan_id
    assert payload["leader_action"]["command"] == f"agentdeck approval create-from-plan --plan-id {plan_id}"
    assert payload["leader_action"]["can_apply"] is True
    assert (
        payload["leader_action"]["apply_command"]
        == f"agentdeck leader apply-action --action-id {payload['leader_action']['action_id']}"
    )
    assert payload["leader_action"]["explicit_command"] == payload["leader_action"]["command"]
    assert payload["leader_action"]["apply_blocker"] is None
    assert payload["recovery"]["status"] == "action_required"
    assert payload["recovery"]["leader_action"]["action_id"] == payload["leader_action"]["action_id"]
    assert payload["recovery"]["next_command"] == payload["leader_action"]["apply_command"]
    assert payload["next_command"] == payload["recovery"]["next_command"]

    state = StateStore(root).load()
    assert state["chat_turns"][0]["action_id"] == payload["leader_action"]["action_id"]
    assert state["chat_turns"][0]["action_kind"] == "create_approvals"
    assert state["chat_turns"][0]["next_command"] == payload["recovery"]["next_command"]
    assert len(state["leader_actions"]) == 1
    assert state["leader_actions"][0]["kind"] == "create_approvals"
    assert state["approvals"] == []
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_chat_history_lists_persisted_turns(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "chat", "--message", "第一轮"])
    first = json.loads(capsys.readouterr().out)
    cli.main(["leader", "chat", "--message", "继续"])
    second = json.loads(capsys.readouterr().out)

    exit_code = cli.main(["leader", "chat-history"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert [item["turn_id"] for item in payload["turns"]] == [first["turn_id"], second["turn_id"]]
    assert [item["mode"] for item in payload["turns"]] == ["plan", "review"]
    assert [item["message"] for item in payload["turns"]] == ["第一轮", "继续"]
    assert payload["turns"][0]["next_command"] == first["next_command"]
    assert payload["turns"][1]["next_command"] == second["next_command"]
    assert "project_view" not in payload["turns"][0]


def test_leader_next_records_create_approvals_action_without_executing(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "需要审批队列"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]

    exit_code = cli.main(["leader", "next"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["action_id"].startswith("act_")
    assert payload["status"] == "pending"
    assert payload["requires_confirmation"] is True
    assert payload["kind"] == "create_approvals"
    assert payload["plan_id"] == plan_id
    assert payload["command"] == f"agentdeck approval create-from-plan --plan-id {plan_id}"

    state = StateStore(root).load()
    assert state["leader_actions"][0]["action_id"] == payload["action_id"]
    assert state["leader_actions"][0]["kind"] == "create_approvals"
    assert state["leader_actions"][0]["status"] == "pending"
    assert state["approvals"] == []
    assert state["messages"] == []
    assert state["jobs"] == []

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_action_suggested"' in events


def test_leader_next_refuses_invalid_project_view_before_recording_action(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "坏状态不能建议下一步"])
    capsys.readouterr()
    break_project_view_recovery(monkeypatch)

    exit_code = cli.main(["leader", "next"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "ProjectView contract validation failed" in captured.err
    assert "missing top-level field: recovery" in captured.err
    state = StateStore(root).load()
    assert state["leader_actions"] == []
    assert state["approvals"] == []
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_next_reuses_existing_pending_create_approvals_action(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "重复查看下一步"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["leader", "next", "--plan-id", plan_id])
    first = json.loads(capsys.readouterr().out)

    exit_code = cli.main(["leader", "next", "--plan-id", plan_id])

    assert exit_code == 0
    second = json.loads(capsys.readouterr().out)
    assert second["action_id"] == first["action_id"]
    assert second["kind"] == "create_approvals"
    state = StateStore(root).load()
    assert len(state["leader_actions"]) == 1
    assert state["leader_actions"][0]["action_id"] == first["action_id"]
    assert state["approvals"] == []
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_next_records_dispatch_action_without_dispatching(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "需要派发"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()

    exit_code = cli.main(["leader", "next", "--plan-id", plan_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "dispatch_approved"
    assert payload["approval_id"] == approval_id
    assert payload["command"] == f"agentdeck approval dispatch --approval-id {approval_id}"

    state = StateStore(root).load()
    assert len(state["leader_actions"]) == 1
    assert state["leader_actions"][0]["approval_id"] == approval_id
    assert state["approvals"][0]["status"] == "approved"
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_next_reuses_existing_pending_dispatch_action(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "重复派发建议"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    cli.main(["leader", "next", "--plan-id", plan_id])
    first = json.loads(capsys.readouterr().out)

    exit_code = cli.main(["leader", "next", "--plan-id", plan_id])

    assert exit_code == 0
    second = json.loads(capsys.readouterr().out)
    assert second["action_id"] == first["action_id"]
    assert second["kind"] == "dispatch_approved"
    assert second["approval_id"] == approval_id
    state = StateStore(root).load()
    assert len(state["leader_actions"]) == 1
    assert state["leader_actions"][0]["approval_id"] == approval_id
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_actions_lists_persisted_actions(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "需要 action history"])
    capsys.readouterr()
    cli.main(["leader", "next"])
    first = json.loads(capsys.readouterr().out)

    exit_code = cli.main(["leader", "actions"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["actions"][0]["action_id"] == first["action_id"]
    assert payload["actions"][0]["kind"] == "create_approvals"
    assert payload["actions"][0]["status"] == "pending"


def test_leader_action_show_outputs_full_action_with_applyability(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "查看 action 详情"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["leader", "next", "--plan-id", plan_id])
    action_id = json.loads(capsys.readouterr().out)["action_id"]

    exit_code = cli.main(["leader", "action", "--action-id", action_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action_id"] == action_id
    assert payload["kind"] == "create_approvals"
    assert payload["status"] == "pending"
    assert payload["can_apply"] is True
    assert payload["apply_command"] == f"agentdeck leader apply-action --action-id {action_id}"
    assert payload["explicit_command"] == f"agentdeck approval create-from-plan --plan-id {plan_id}"
    assert payload["apply_blocker"] is None
    assert payload["reason"] == "plan has no approval records"

    state = StateStore(root).load()
    assert state["leader_actions"][0]["status"] == "pending"
    assert state["approvals"] == []
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_action_show_includes_recovery_recommended_action_match(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "查看 action recovery 对照"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["leader", "next", "--plan-id", plan_id])
    action_id = json.loads(capsys.readouterr().out)["action_id"]

    exit_code = cli.main(["leader", "action", "--action-id", action_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action_id"] == action_id
    assert payload["recovery"]["status"] == "action_required"
    assert payload["recovery"]["recommended_action"]["target_id"] == action_id
    assert payload["recovery"]["leader_action"]["action_id"] == action_id
    assert payload["matches_recommended_action"] is True
    assert payload["recommended_action"] == payload["recovery"]["recommended_action"]

    state = StateStore(root).load()
    assert state["leader_actions"][0]["status"] == "pending"
    assert state["approvals"] == []
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_action_show_marks_dispatch_action_as_not_applyable(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "查看 dispatch action"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    cli.main(["leader", "next", "--plan-id", plan_id])
    action_id = json.loads(capsys.readouterr().out)["action_id"]

    exit_code = cli.main(["leader", "action", "--action-id", action_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action_id"] == action_id
    assert payload["kind"] == "dispatch_approved"
    assert payload["status"] == "pending"
    assert payload["can_apply"] is False
    assert payload["apply_command"] is None
    assert payload["explicit_command"] == f"agentdeck approval dispatch --approval-id {approval_id}"
    assert payload["apply_blocker"] == "leader action requires explicit command"

    state = StateStore(root).load()
    assert state["leader_actions"][0]["status"] == "pending"
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_action_show_rejects_unknown_action_id(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "action", "--action-id", "act_missing"])

    assert exit_code == 1
    assert "unknown leader action: act_missing" in capsys.readouterr().err


def test_leader_apply_action_creates_approvals_and_marks_action_applied(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "应用审批 action"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["leader", "next", "--plan-id", plan_id])
    action_id = json.loads(capsys.readouterr().out)["action_id"]

    exit_code = cli.main(["leader", "apply-action", "--action-id", action_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["action_id"] == action_id
    assert payload["kind"] == "create_approvals"
    assert payload["status"] == "applied"
    assert payload["result"]["count"] == 3
    assert [item["agent_id"] for item in payload["result"]["approvals"]] == ["planner", "coder", "reviewer"]

    state = StateStore(root).load()
    assert len(state["approvals"]) == 3
    assert state["leader_actions"][0]["status"] == "applied"
    assert state["leader_actions"][0]["applied_at"]
    assert state["messages"] == []
    assert state["jobs"] == []

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_action_applied"' in events


def test_leader_apply_action_refuses_invalid_project_view_before_applying(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "坏状态不能应用 action"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["leader", "next", "--plan-id", plan_id])
    action_id = json.loads(capsys.readouterr().out)["action_id"]
    break_project_view_recovery(monkeypatch)

    exit_code = cli.main(["leader", "apply-action", "--action-id", action_id])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "ProjectView contract validation failed" in captured.err
    assert "missing top-level field: recovery" in captured.err
    state = StateStore(root).load()
    assert state["leader_actions"][0]["status"] == "pending"
    assert state["approvals"] == []
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_chat_applies_create_approvals_action_when_explicitly_requested(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "对话应用 action"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["leader", "next", "--plan-id", plan_id])
    action_id = json.loads(capsys.readouterr().out)["action_id"]

    exit_code = cli.main(["leader", "chat", "--message", f"apply action {action_id}"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "apply_action"
    assert payload["message"] == f"apply action {action_id}"
    assert payload["leader_action"]["action_id"] == action_id
    assert payload["leader_action"]["status"] == "applied"
    assert payload["leader_action"]["can_apply"] is False
    assert payload["leader_action"]["apply_blocker"] == f"leader action is not pending: {action_id}"
    assert payload["result"]["count"] == 3

    state = StateStore(root).load()
    assert len(state["approvals"]) == 3
    assert state["leader_actions"][0]["status"] == "applied"
    assert state["chat_turns"][0]["mode"] == "apply_action"
    assert state["chat_turns"][0]["action_id"] == action_id
    assert state["chat_turns"][0]["action_kind"] == "create_approvals"
    assert state["messages"] == []
    assert state["jobs"] == []

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_action_applied"' in events
    assert '"event_type": "leader_chat_turn"' in events


def test_leader_apply_action_rejects_already_applied_action(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "重复应用"])
    capsys.readouterr()
    cli.main(["leader", "next"])
    action_id = json.loads(capsys.readouterr().out)["action_id"]
    cli.main(["leader", "apply-action", "--action-id", action_id])
    capsys.readouterr()

    exit_code = cli.main(["leader", "apply-action", "--action-id", action_id])

    assert exit_code == 1
    assert f"leader action is not pending: {action_id}" in capsys.readouterr().err


def test_leader_apply_action_refuses_dispatch_action(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "不能自动 dispatch"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    cli.main(["leader", "next", "--plan-id", plan_id])
    action_id = json.loads(capsys.readouterr().out)["action_id"]

    exit_code = cli.main(["leader", "apply-action", "--action-id", action_id])

    assert exit_code == 1
    assert f"leader action requires explicit command: {action_id}" in capsys.readouterr().err
    state = StateStore(root).load()
    assert state["leader_actions"][0]["status"] == "pending"
    assert state["approvals"][0]["status"] == "approved"
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_chat_refuses_runtime_action_apply_request(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "对话不能自动 dispatch"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    cli.main(["leader", "next", "--plan-id", plan_id])
    action_id = json.loads(capsys.readouterr().out)["action_id"]

    exit_code = cli.main(["leader", "chat", "--message", f"apply action {action_id}"])

    assert exit_code == 1
    assert f"leader action requires explicit command: {action_id}" in capsys.readouterr().err
    state = StateStore(root).load()
    assert state["leader_actions"][0]["status"] == "pending"
    assert state["approvals"][0]["status"] == "approved"
    assert state["messages"] == []
    assert state["jobs"] == []


def test_plan_list_outputs_plan_summaries(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "第一项任务"])
    first = json.loads(capsys.readouterr().out)
    cli.main(["leader", "plan", "--task", "第二项任务"])
    second = json.loads(capsys.readouterr().out)

    exit_code = cli.main(["plan", "list"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert [item["plan_id"] for item in payload["plans"]] == [first["plan_id"], second["plan_id"]]
    assert [item["task"] for item in payload["plans"]] == ["第一项任务", "第二项任务"]
    assert all("plan" not in item for item in payload["plans"])
    assert payload["plans"][0]["status"] == "planned"
    assert payload["plans"][0]["provider"] == "fake"
    assert payload["plans"][0]["step_count"] == 3


def test_plan_show_outputs_full_plan_by_id(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "查看计划详情"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]

    exit_code = cli.main(["plan", "show", "--plan-id", plan_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan_id"] == plan_id
    assert payload["task"] == "查看计划详情"
    assert payload["status"] == "planned"
    assert payload["plan"]["goal"] == "查看计划详情"
    assert [step["agent_id"] for step in payload["plan"]["steps"]] == ["planner", "coder", "reviewer"]


def test_plan_show_rejects_unknown_plan_id(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["plan", "show", "--plan-id", "pln_missing"])

    assert exit_code == 1
    assert "unknown plan: pln_missing" in capsys.readouterr().err


def test_plan_status_summarizes_approvals_and_dispatch_lineage(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%77")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--task", "查看计划状态"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approvals = json.loads(capsys.readouterr().out)["approvals"]
    planner_approval_id = approvals[0]["approval_id"]
    coder_approval_id = approvals[1]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", planner_approval_id])
    capsys.readouterr()
    cli.main(["approval", "dispatch", "--approval-id", planner_approval_id])
    dispatch_payload = json.loads(capsys.readouterr().out)
    cli.main(["approval", "reject", "--approval-id", coder_approval_id, "--reason", "先等 planner"])
    capsys.readouterr()

    exit_code = cli.main(["plan", "status", "--plan-id", plan_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan_id"] == plan_id
    assert payload["task"] == "查看计划状态"
    assert payload["counts"] == {
        "steps": 3,
        "approvals": 3,
        "pending": 1,
        "approved": 0,
        "rejected": 1,
        "dispatched": 1,
    }
    assert [step["agent_id"] for step in payload["steps"]] == ["planner", "coder", "reviewer"]
    assert payload["steps"][0]["approval_status"] == "dispatched"
    assert payload["steps"][0]["message_id"] == dispatch_payload["message_id"]
    assert payload["steps"][0]["job_id"].startswith("job_")
    assert payload["steps"][1]["approval_status"] == "rejected"
    assert payload["steps"][1]["reason"] == "先等 planner"
    assert payload["steps"][2]["approval_status"] == "pending"


def test_plan_status_rejects_unknown_plan_id(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["plan", "status", "--plan-id", "pln_missing"])

    assert exit_code == 1
    assert "unknown plan: pln_missing" in capsys.readouterr().err


def test_leader_review_recommends_next_dispatch_when_pending_approved_step_exists(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "review loop"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()

    exit_code = cli.main(["leader", "review", "--plan-id", plan_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan_id"] == plan_id
    assert payload["next_action"] == "dispatch_approved"
    assert payload["approval_id"] == approval_id
    assert payload["agent_id"] == "planner"
    assert payload["reason"] == "approved step is waiting for dispatch"
    assert payload["counts"]["approved"] == 1


def test_leader_review_refuses_invalid_project_view_before_recommending_next_step(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "坏状态不能 review"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    capsys.readouterr()
    break_project_view_recovery(monkeypatch)

    exit_code = cli.main(["leader", "review", "--plan-id", plan_id])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "ProjectView contract validation failed" in captured.err
    assert "missing top-level field: recovery" in captured.err
    state = StateStore(root).load()
    assert state["leader_actions"] == []
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_review_recommends_waiting_for_dispatched_reply(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%77")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--task", "review waiting"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    cli.main(["approval", "dispatch", "--approval-id", approval_id])
    message_id = json.loads(capsys.readouterr().out)["message_id"]

    exit_code = cli.main(["leader", "review", "--plan-id", plan_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["next_action"] == "wait_for_reply"
    assert payload["message_id"] == message_id
    assert payload["agent_id"] == "planner"
    assert payload["reason"] == "dispatched step has no reply yet"


def test_leader_review_summarizes_when_all_dispatched_steps_have_replies(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%77")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--task", "review completed"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    cli.main(["approval", "dispatch", "--approval-id", approval_id])
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    cli.main(["reply", "--agent", "planner", "--message-id", message_id, "--text", "status: completed\nsummary: done"])
    reply_id = json.loads(capsys.readouterr().out)["reply_id"]

    exit_code = cli.main(["leader", "review", "--plan-id", plan_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["next_action"] == "summarize"
    assert payload["reason"] == "all dispatched steps have replies"
    assert payload["replies"] == [{"agent_id": "planner", "message_id": message_id, "reply_id": reply_id}]


def test_leader_review_rejects_unknown_plan_id(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "review", "--plan-id", "pln_missing"])

    assert exit_code == 1
    assert "unknown plan: pln_missing" in capsys.readouterr().err


def test_approval_create_from_plan_generates_step_approvals(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "审批后再派发"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]

    exit_code = cli.main(["approval", "create-from-plan", "--plan-id", plan_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["plan_id"] == plan_id
    assert payload["count"] == 3
    assert [item["agent_id"] for item in payload["approvals"]] == ["planner", "coder", "reviewer"]
    assert all(item["status"] == "pending" for item in payload["approvals"])
    assert all(item["approval_id"].startswith("apv_") for item in payload["approvals"])

    state = StateStore(root).load()
    assert len(state["approvals"]) == 3
    assert state["approvals"][0]["plan_id"] == plan_id
    assert state["approvals"][0]["step"] == 1
    assert state["approvals"][0]["status"] == "pending"
    assert state["messages"] == []
    assert state["jobs"] == []

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "approvals_created_from_plan"' in events


def test_approval_list_and_decisions_update_status(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "审批状态流转"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approvals = json.loads(capsys.readouterr().out)["approvals"]
    first_id = approvals[0]["approval_id"]
    second_id = approvals[1]["approval_id"]

    exit_code = cli.main(["approval", "approve", "--approval-id", first_id])

    assert exit_code == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["ok"] is True
    assert approved["approval_id"] == first_id
    assert approved["status"] == "approved"

    exit_code = cli.main(["approval", "reject", "--approval-id", second_id, "--reason", "范围过大"])

    assert exit_code == 0
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["ok"] is True
    assert rejected["approval_id"] == second_id
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "范围过大"

    exit_code = cli.main(["approval", "list"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 3
    assert [item["status"] for item in payload["approvals"]] == ["approved", "rejected", "pending"]
    assert payload["approvals"][1]["reason"] == "范围过大"

    state = StateStore(root).load()
    assert state["approvals"][0]["status"] == "approved"
    assert state["approvals"][1]["status"] == "rejected"
    assert state["approvals"][1]["reason"] == "范围过大"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "approval_decided"' in events


def test_approval_commands_reject_unknown_ids(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["approval", "create-from-plan", "--plan-id", "pln_missing"])

    assert exit_code == 1
    assert "unknown plan: pln_missing" in capsys.readouterr().err

    exit_code = cli.main(["approval", "approve", "--approval-id", "apv_missing"])

    assert exit_code == 1
    assert "unknown approval: apv_missing" in capsys.readouterr().err


def test_approval_dispatch_rejects_unapproved_item(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "必须审批后派发"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]

    exit_code = cli.main(["approval", "dispatch", "--approval-id", approval_id])

    assert exit_code == 1
    assert f"approval is not approved: {approval_id}" in capsys.readouterr().err


def test_approval_dispatch_sends_approved_step_to_agent_and_records_lineage(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%77")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--task", "审批后派发 planner step"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()

    exit_code = cli.main(["approval", "dispatch", "--approval-id", approval_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["approval_id"] == approval_id
    assert payload["agent_id"] == "planner"
    assert payload["pane_id"] == "%77"
    assert payload["message_id"].startswith("msg_")
    assert fake.sent and fake.sent[0][0] == "%77"
    assert "AgentDeck dispatch" in fake.sent[0][1]
    assert "Break down the goal" in fake.sent[0][1]

    state = StateStore(root).load()
    approval = state["approvals"][0]
    assert approval["status"] == "dispatched"
    assert approval["message_id"] == payload["message_id"]
    assert state["messages"][0]["message_id"] == payload["message_id"]
    assert state["messages"][0]["from_actor"] == "leader"
    assert state["messages"][0]["to_agent"] == "planner"
    assert state["jobs"][0]["pane_id"] == "%77"
    assert state["inbox"]["planner"][0]["event_type"] == "task_request"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "approval_dispatched"' in events
