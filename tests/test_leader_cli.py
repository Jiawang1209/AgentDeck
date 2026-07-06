from __future__ import annotations

import json
from pathlib import Path
import subprocess

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.providers.fake import FakeLeaderProvider
from agentdeck.state import StateStore


class FakeTmuxBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.captured: list[tuple[str, int]] = []
        self.killed: list[str] = []
        self.checked_panes: list[str] = []

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        self.captured.append((pane_id, lines))
        return "status: running\nsummary: planner is thinking\n"

    def kill_pane(self, _config, pane_id: str) -> None:
        self.killed.append(pane_id)

    def pane_exists(self, _config, pane_id: str) -> bool:
        self.checked_panes.append(pane_id)
        return True


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    config_text = config_text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    config_path.write_text(config_text, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def prepare_project_with_default_leader(tmp_path: Path, monkeypatch) -> Path:
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
    assert payload["provider_backend"] == "local"
    assert payload["provider_transport"] == "local"
    assert payload["leader_backend"] == {
        "agent_id": "leader",
        "provider": "fake",
        "model": "fake-plan",
        "provider_backend": "local",
        "provider_transport": "local",
        "reasoning_backend": "local-fake",
        "runtime_kind": "logical_leader",
        "pane_backed": False,
        "pane_id": None,
        "approval_required": True,
        "dispatch_ready": False,
    }
    assert payload["dispatch_ready"] is False
    assert payload["plan"]["goal"] == "实现自动 reply extraction"
    assert [step["agent_id"] for step in payload["plan"]["steps"]] == ["planner", "coder", "reviewer"]
    assert all(step["requires_approval"] is True for step in payload["plan"]["steps"])

    state = StateStore(root).load()
    assert state["plans"][0]["plan_id"] == payload["plan_id"]
    assert state["plans"][0]["task"] == "实现自动 reply extraction"
    assert state["plans"][0]["provider"] == "fake"
    assert state["plans"][0]["provider_backend"] == "local"
    assert state["plans"][0]["provider_transport"] == "local"
    assert state["plans"][0]["leader_backend"] == payload["leader_backend"]
    assert state["plans"][0]["status"] == "planned"
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_plan_created"' in events


def test_run_task_creates_plan_and_pending_approvals_without_dispatching(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["run", "--task", "实现多 Agent smoke"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    plan_id = payload["plan_id"]
    first_approval_id = payload["approval_card"]["approvals"][0]["approval_id"]
    assert payload["ok"] is True
    assert payload["mode"] == "run_start"
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["task"] == "实现多 Agent smoke"
    assert plan_id.startswith("pln_")
    assert payload["provider"] == "fake"
    assert payload["model"] == "fake-plan"
    assert payload["approval_count"] == 3
    assert payload["pending_approval_count"] == 3
    assert payload["next_command"] == "agentdeck approval list"
    assert payload["approve_next_command"] == f"agentdeck approval approve --approval-id {first_approval_id}"
    assert payload["review_command"] == f"agentdeck leader review --plan-id {plan_id}"
    assert payload["continue_command"] == "agentdeck continue"
    assert payload["workbench_command"] == "agentdeck workbench"
    assert payload["safety"] == "approval_gated"
    assert payload["requires_explicit_user"] is True
    assert payload["plan"]["goal"] == "实现多 Agent smoke"
    assert payload["approval_card"]["count"] == 3
    assert all(approval["status"] == "pending" for approval in payload["approval_card"]["approvals"])
    assert payload["controls"][0] == {
        "kind": "preview",
        "label": "Review approval queue",
        "command": "agentdeck approval list",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["controls"][1]["command"] == payload["approve_next_command"]
    assert payload["controls"][1]["safety"] == "explicit_runtime"
    assert payload["controls"][2]["command"] == payload["review_command"]

    state = StateStore(root).load()
    assert len(state["plans"]) == 1
    assert len(state["approvals"]) == 3
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state["replies"] == []
    assert state.get("inbox", {}) == {}
    assert state["leader_actions"] == []
    assert fake.sent == []
    assert fake.captured == []

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "run_started"' in events
    assert f'"plan_id": "{plan_id}"' in events


def test_run_plan_id_returns_progress_card_without_dispatching(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["run", "--task", "实现 run progress"])
    started = json.loads(capsys.readouterr().out)
    plan_id = started["plan_id"]
    approval_id = started["approval_card"]["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    state_before = StateStore(root).load()

    exit_code = cli.main(["run", "--plan-id", plan_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "run_progress"
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["plan_id"] == plan_id
    assert payload["task"] == "实现 run progress"
    assert payload["status"] == "planned"
    assert payload["leader_backend"] == started["leader_backend"]
    assert payload["review"]["leader_backend"] == payload["leader_backend"]
    assert payload["counts"]["approved"] == 1
    assert payload["counts"]["pending"] == 2
    assert payload["review"]["next_action"] == "dispatch_approved"
    assert payload["review"]["approval_id"] == approval_id
    assert payload["next_command"] == f"agentdeck approval dispatch --approval-id {approval_id}"
    assert payload["approval_card"]["count"] == 3
    assert payload["approval_card"]["approvals"][0]["status"] == "approved"
    assert payload["approval_card"]["approvals"][0]["dispatch_command"] == payload["next_command"]
    assert payload["plan_status_command"] == f"agentdeck plan status --plan-id {plan_id}"
    assert payload["review_command"] == f"agentdeck leader review --plan-id {plan_id}"
    assert payload["continue_command"] == "agentdeck continue"
    assert payload["workbench_command"] == "agentdeck workbench"
    assert payload["safety"] == "approval_gated"
    assert payload["requires_explicit_user"] is True
    assert payload["controls"][0]["command"] == payload["plan_status_command"]
    assert payload["controls"][1]["command"] == payload["review_command"]
    assert payload["controls"][2]["command"] == "agentdeck approval list"
    assert payload["controls"][3]["command"] == payload["next_command"]
    assert payload["controls"][3]["safety"] == "explicit_runtime"

    assert StateStore(root).load() == state_before
    assert fake.sent == []
    assert fake.captured == []


def test_leader_chat_run_intent_starts_approval_gated_run_without_dispatching(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["leader", "chat", "--message", "开始运行 实现自然语言 run loop"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    plan_id = payload["plan_id"]
    run_start_card = payload["run_start_card"]
    first_approval_id = run_start_card["approval_card"]["approvals"][0]["approval_id"]
    assert payload["ok"] is True
    assert payload["mode"] == "run_start"
    assert payload["message"] == "开始运行 实现自然语言 run loop"
    assert payload["leader_action"] is None
    assert payload["leader_action_card"] is None
    assert payload["approval_card"] == run_start_card["approval_card"]
    assert payload["next_command"] == "agentdeck approval list"
    assert payload["intent_card"]["embedded_card"] == "run_start_card"
    assert payload["intent_card"]["read_only"] is False
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect run_start_card",
        "command": "agentdeck approval list",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert run_start_card["mode"] == "run_start"
    assert run_start_card["task"] == "实现自然语言 run loop"
    assert run_start_card["plan_id"] == plan_id
    assert run_start_card["approval_count"] == 3
    assert run_start_card["pending_approval_count"] == 3
    assert run_start_card["approve_next_command"] == f"agentdeck approval approve --approval-id {first_approval_id}"
    assert run_start_card["review_command"] == f"agentdeck leader review --plan-id {plan_id}"
    assert run_start_card["safety"] == "approval_gated"
    assert run_start_card["requires_explicit_user"] is True

    state = StateStore(root).load()
    assert len(state["plans"]) == 1
    assert len(state["approvals"]) == 3
    assert state["leader_actions"] == []
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state["replies"] == []
    assert state.get("inbox", {}) == {}
    assert state["chat_turns"][0]["mode"] == "run_start"
    assert state["chat_turns"][0]["plan_id"] == plan_id
    assert state["chat_turns"][0]["action_kind"] == "run_start"
    assert fake.sent == []
    assert fake.captured == []

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "run_started"' in events
    assert '"source": "leader_chat"' in events


def test_leader_chat_run_progress_intent_returns_read_only_card_without_dispatching(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["run", "--task", "实现自然语言进度查看"])
    started = json.loads(capsys.readouterr().out)
    plan_id = started["plan_id"]
    approval_id = started["approval_card"]["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "chat", "--message", f"查看运行进度 {plan_id}"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    run_progress_card = payload["run_progress_card"]
    assert payload["ok"] is True
    assert payload["mode"] == "run_progress"
    assert payload["plan_id"] == plan_id
    assert payload["leader_action"] is None
    assert payload["leader_action_card"] is None
    assert payload["approval_card"] == run_progress_card["approval_card"]
    assert payload["review"] == run_progress_card["review"]
    assert payload["next_command"] == run_progress_card["next_command"]
    assert payload["intent_card"]["embedded_card"] == "run_progress_card"
    assert payload["intent_card"]["read_only"] is True
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect run_progress_card",
        "command": f"agentdeck run --plan-id {plan_id}",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert run_progress_card["mode"] == "run_progress"
    assert run_progress_card["plan_id"] == plan_id
    assert run_progress_card["counts"]["approved"] == 1
    assert run_progress_card["counts"]["pending"] == 2
    assert run_progress_card["review"]["next_action"] == "dispatch_approved"
    assert run_progress_card["next_command"] == f"agentdeck approval dispatch --approval-id {approval_id}"
    assert run_progress_card["safety"] == "approval_gated"
    assert run_progress_card["requires_explicit_user"] is True

    state_after = StateStore(root).load()
    assert state_after["plans"] == state_before["plans"]
    assert state_after["approvals"] == state_before["approvals"]
    assert state_after["messages"] == state_before["messages"]
    assert state_after["jobs"] == state_before["jobs"]
    assert state_after["replies"] == state_before["replies"]
    assert state_after.get("inbox", {}) == state_before.get("inbox", {})
    assert len(state_after["chat_turns"]) == len(state_before["chat_turns"]) + 1
    assert state_after["chat_turns"][-1]["mode"] == "run_progress"
    assert state_after["chat_turns"][-1]["plan_id"] == plan_id
    assert fake.sent == []
    assert fake.captured == []


def test_leader_chat_run_progress_without_plan_id_uses_latest_plan_without_dispatching(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["run", "--task", "实现 latest run progress intent"])
    first_started = json.loads(capsys.readouterr().out)
    cli.main(["run", "--task", "实现第二个 run"])
    latest_started = json.loads(capsys.readouterr().out)
    latest_plan_id = latest_started["plan_id"]
    approval_id = latest_started["approval_card"]["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "chat", "--message", "查看运行进度"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_progress"
    assert payload["plan_id"] == latest_plan_id
    assert payload["plan_id"] != first_started["plan_id"]
    assert payload["run_progress_card"]["plan_id"] == latest_plan_id
    assert payload["run_progress_card"]["counts"]["approved"] == 1
    assert payload["next_command"] == f"agentdeck approval dispatch --approval-id {approval_id}"
    assert payload["intent_card"]["controls"][0]["command"] == f"agentdeck run --plan-id {latest_plan_id}"

    state_after = StateStore(root).load()
    assert state_after["plans"] == state_before["plans"]
    assert state_after["approvals"] == state_before["approvals"]
    assert state_after["messages"] == state_before["messages"]
    assert state_after["jobs"] == state_before["jobs"]
    assert state_after["replies"] == state_before["replies"]
    assert state_after.get("inbox", {}) == state_before.get("inbox", {})
    assert len(state_after["chat_turns"]) == len(state_before["chat_turns"]) + 1
    assert state_after["chat_turns"][-1]["mode"] == "run_progress"
    assert state_after["chat_turns"][-1]["plan_id"] == latest_plan_id
    assert fake.sent == []
    assert fake.captured == []


def test_leader_chat_run_progress_without_any_plan_does_not_create_plan(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "chat", "--message", "查看运行进度"])

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "no plans available for run progress"
    assert StateStore(root).load() == state_before


def test_leader_plan_defaults_to_configured_leader_provider_and_model(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project_with_default_leader(tmp_path, monkeypatch)
    seen: dict[str, str] = {}

    class StubProvider(FakeLeaderProvider):
        def __init__(self, name: str) -> None:
            self.name = name

    def fake_leader_provider(name: str):
        seen["provider"] = name
        return StubProvider(name)

    monkeypatch.setattr(cli, "leader_provider", fake_leader_provider)

    exit_code = cli.main(["leader", "plan", "--task", "使用配置 Leader"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert seen["provider"] == "deepseek"
    assert payload["provider"] == "deepseek"
    assert payload["model"] == "deepseek-chat"
    state = StateStore(root).load()
    assert state["plans"][0]["provider"] == "deepseek"
    assert state["plans"][0]["model"] == "deepseek-chat"
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_plan_passes_model_to_codex_cli_backend_without_dispatching(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    seen: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "goal": "Codex CLI Leader",
                    "summary": "plan from codex cli",
                    "steps": [
                        {
                            "step": 1,
                            "agent_id": "planner",
                            "role": "planning",
                            "task": "Plan via local Codex CLI",
                            "risk": "requires human review before dispatch",
                            "requires_approval": True,
                        }
                    ],
                    "approval_required": True,
                    "dispatch_ready": False,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    exit_code = cli.main(
        [
            "leader",
            "plan",
            "--provider",
            "codex-cli",
            "--model",
            "gpt-5-codex",
            "--task",
            "让 Codex CLI 作为 Leader",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert seen["command"] == [
        "codex",
        "--model",
        "gpt-5-codex",
        "exec",
        "--sandbox",
        "read-only",
        "-",
    ]
    assert payload["provider"] == "codex-cli"
    assert payload["provider_backend"] == "cli"
    assert payload["provider_transport"] == "subprocess"
    assert payload["leader_backend"] == {
        "agent_id": "leader",
        "provider": "codex-cli",
        "model": "gpt-5-codex",
        "provider_backend": "cli",
        "provider_transport": "subprocess",
        "reasoning_backend": "cli-subprocess",
        "runtime_kind": "logical_leader",
        "pane_backed": False,
        "pane_id": None,
        "approval_required": True,
        "dispatch_ready": False,
    }
    assert payload["model"] == "gpt-5-codex"
    assert payload["plan"]["goal"] == "Codex CLI Leader"

    state = StateStore(root).load()
    assert state["plans"][0]["provider"] == "codex-cli"
    assert state["plans"][0]["provider_backend"] == "cli"
    assert state["plans"][0]["provider_transport"] == "subprocess"
    assert state["plans"][0]["leader_backend"] == payload["leader_backend"]
    assert state["plans"][0]["model"] == "gpt-5-codex"
    assert state["approvals"] == []
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}


def test_leader_chat_defaults_to_configured_leader_provider_and_model(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project_with_default_leader(tmp_path, monkeypatch)
    seen: dict[str, str] = {}

    class StubProvider(FakeLeaderProvider):
        def __init__(self, name: str) -> None:
            self.name = name

    def fake_leader_provider(name: str):
        seen["provider"] = name
        return StubProvider(name)

    monkeypatch.setattr(cli, "leader_provider", fake_leader_provider)

    exit_code = cli.main(["leader", "chat", "--message", "用配置 Leader 对话"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert seen["provider"] == "deepseek"
    assert payload["mode"] == "plan"
    assert payload["plan_id"].startswith("pln_")
    assert payload["project_view"]["plans"]["items"][0]["provider"] == "deepseek"
    assert payload["project_view"]["plans"]["items"][0]["model"] == "deepseek-chat"
    state = StateStore(root).load()
    assert state["plans"][0]["provider"] == "deepseek"
    assert state["plans"][0]["model"] == "deepseek-chat"
    assert state["chat_turns"][0]["provider"] == "deepseek"
    assert state["chat_turns"][0]["model"] == "deepseek-chat"
    assert state["messages"] == []
    assert state["jobs"] == []


def test_leader_plan_rejects_unknown_provider(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "plan", "--provider", "unknown-llm", "--task", "未知 provider"])

    assert exit_code == 1
    assert "unsupported leader provider: unknown-llm" in capsys.readouterr().err
    state = StateStore(root).load()
    assert state.get("plans", []) == []


def test_leader_plan_uses_deepseek_provider_without_dispatching(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    class StubProvider(FakeLeaderProvider):
        name = "deepseek"

    monkeypatch.setattr(cli, "leader_provider", lambda name: StubProvider())

    exit_code = cli.main(
        [
            "leader",
            "plan",
            "--provider",
            "deepseek",
            "--model",
            "deepseek-chat",
            "--task",
            "DeepSeek 计划",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "deepseek"
    assert payload["model"] == "deepseek-chat"
    assert payload["dispatch_ready"] is False

    state = StateStore(root).load()
    assert state["plans"][0]["provider"] == "deepseek"
    assert state["plans"][0]["model"] == "deepseek-chat"
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}


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
    assert payload["project_view"]["plans"]["count"] == 1
    assert payload["project_view"]["plans"]["items"][0]["plan_id"] == payload["plan_id"]
    assert payload["project_view"]["plans"]["items"][0]["task"] == "帮我实现自动回复回收"
    assert payload["project_view"]["chat_turns"]["count"] == 1
    assert payload["project_view"]["chat_turns"]["items"][0]["turn_id"] == payload["turn_id"]
    assert payload["project_view"]["chat_turns"]["items"][0]["action_id"] == payload["leader_action"]["action_id"]
    assert payload["project_view"]["chat_turns"]["items"][0]["action_kind"] == "create_approvals"
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["leader_actions"]["count"] == 1
    assert payload["leader_actions"]["recommended_action_id"] == payload["leader_action"]["action_id"]
    assert payload["leader_actions"]["items"][0]["kind"] == "create_approvals"
    assert payload["leader_actions"]["items"][0]["can_apply"] is True
    assert payload["leader_actions"]["items"][0]["is_recommended"] is True
    assert payload["leader_action"]["kind"] == "create_approvals"
    assert payload["leader_action"]["plan_id"] == payload["plan_id"]
    assert payload["leader_action"]["can_apply"] is True
    assert payload["leader_action"]["apply_blocker"] is None
    assert payload["leader_action_card"] == {
        "mode": "leader_action",
        "title": "Leader action",
        "action_id": payload["leader_action"]["action_id"],
        "kind": "create_approvals",
        "status": "pending",
        "reason": payload["leader_action"]["reason"],
        "preview_command": payload["leader_action"]["preview_command"],
        "can_apply": True,
        "apply_command": payload["leader_action"]["apply_command"],
        "explicit_command": payload["leader_action"]["explicit_command"],
        "apply_blocker": None,
        "controls": payload["leader_action"]["controls"],
    }
    assert payload["recovery"]["status"] == "action_required"
    assert payload["recovery"]["leader_action"]["action_id"] == payload["leader_action"]["action_id"]
    assert payload["recovery"]["next_command"] == payload["leader_action"]["apply_command"]
    assert payload["leader_explanation"]["mode"] == "plan"
    assert payload["leader_explanation"]["safety"] == "safe_apply"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["leader_explanation"]["recommended_action_id"] == payload["leader_action"]["action_id"]
    assert payload["leader_explanation"]["action_kind"] == "create_approvals"
    assert payload["leader_explanation"]["action_status"] == "pending"
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert "approval" in payload["leader_explanation"]["summary"]
    assert payload["turn_id"].startswith("cht_")
    assert payload["plan_id"].startswith("pln_")
    assert payload["next_command"] == payload["recovery"]["next_command"]
    assert payload["review"] is None

    state = StateStore(root).load()
    assert state["chat_turns"][0]["turn_id"] == payload["turn_id"]
    assert state["chat_turns"][0]["mode"] == "plan"
    assert state["chat_turns"][0]["message"] == "帮我实现自动回复回收"
    assert state["chat_turns"][0]["plan_id"] == payload["plan_id"]
    assert state["chat_turns"][0]["next_command"] == payload["next_command"]
    assert state["chat_turns"][0]["action_id"] == payload["leader_action"]["action_id"]
    assert state["chat_turns"][0]["action_kind"] == "create_approvals"
    assert len(state["plans"]) == 1
    assert state["plans"][0]["task"] == "帮我实现自动回复回收"
    assert len(state["leader_actions"]) == 1
    assert state["leader_actions"][0]["action_id"] == payload["leader_action"]["action_id"]
    assert state["leader_actions"][0]["kind"] == "create_approvals"
    assert state["approvals"] == []
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_chat_turn"' in events


def test_leader_chat_refuses_invalid_chat_response_before_printing(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    original_explanation = cli._leader_chat_explanation

    def broken_explanation(*args, **kwargs):
        payload = original_explanation(*args, **kwargs)
        payload.pop("safety", None)
        return payload

    monkeypatch.setattr(cli, "_leader_chat_explanation", broken_explanation)

    exit_code = cli.main(["leader", "chat", "--message", "帮我实现自动回复回收"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Leader chat contract validation failed" in captured.err
    assert "missing leader_explanation field: safety" in captured.err
    state = StateStore(root).load()
    assert state["leader_errors"][0]["mode"] == "plan"
    assert state["leader_errors"][0]["provider"] == "agentdeck-contract"
    assert state["leader_errors"][0]["task"] == "帮我实现自动回复回收"
    assert state["leader_errors"][0]["error"] == "missing leader_explanation field: safety"
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_chat_contract_failed"' in events


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


def test_leader_chat_setup_intent_surfaces_provider_diagnostics_without_planning(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project_with_default_leader(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    exit_code = cli.main(["leader", "chat", "--message", "检查 Leader provider 配置"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "setup"
    assert payload["message"] == "检查 Leader provider 配置"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["next_command"] == "agentdeck doctor"
    assert payload["recovery"]["status"] == "provider_setup_required"
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    provider_health = payload["provider_health"]
    assert {key: value for key, value in provider_health.items() if key != "controls"} == {
        "agent_id": "leader",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "approval_mode": "confirm",
        "api_backed": True,
        "provider_backend": "api",
        "provider_transport": "http",
        "leader_backend": {
            "agent_id": "leader",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "provider_backend": "api",
            "provider_transport": "http",
            "reasoning_backend": "api-llm",
            "runtime_kind": "logical_leader",
            "pane_backed": False,
            "pane_id": None,
            "approval_required": True,
            "dispatch_ready": False,
        },
        "supported": True,
        "ready": False,
        "missing_env": ["DEEPSEEK_API_KEY"],
        "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
        "command_path": None,
        "doctor_command": "agentdeck doctor",
        "doctor_contract": "agentdeck contract doctor",
        "setup_commands": [
            'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
            'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
            'export DEEPSEEK_MODEL="deepseek-chat"',
        ],
    }
    deepseek_control = next(
        item
        for item in provider_health["controls"]
        if item["command"] == "agentdeck leader set-provider --provider deepseek --model deepseek-chat"
    )
    assert deepseek_control["enabled"] is False
    assert deepseek_control["blocker"] == "already current provider"
    assert any(
        item["kind"] == "set_provider"
        and item["command"] == "agentdeck leader set-provider --provider codex-cli --model codex-default"
        for item in provider_health["controls"]
    )
    assert payload["leader_explanation"] == {
        "mode": "setup",
        "summary": "Leader recommends inspecting provider setup before planning or dispatching work.",
        "reason": "human asked to inspect Leader provider setup",
        "next_command": "agentdeck doctor",
        "recommended_action_id": "deepseek",
        "action_kind": "provider_health",
        "action_status": "provider_setup_required",
        "safety": "inspect",
        "requires_explicit_user": False,
    }
    assert cli.validate_leader_chat_contract(payload) == {"ok": True, "errors": []}

    state = StateStore(root).load()
    assert state["plans"] == []
    assert state["leader_actions"] == []
    assert state["chat_turns"][0]["mode"] == "setup"
    assert state["chat_turns"][0]["next_command"] == "agentdeck doctor"
    assert state["chat_turns"][0]["action_id"] is None
    assert state["chat_turns"][0]["action_kind"] == "provider_health"
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_chat_turn"' in events


def test_leader_chat_setup_commands_never_expose_real_provider_key(
    tmp_path, monkeypatch, capsys
) -> None:
    prepare_project_with_default_leader(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "real-secret-key")

    exit_code = cli.main(["leader", "chat", "--message", "doctor"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload, ensure_ascii=False)
    assert payload["mode"] == "setup"
    assert payload["provider_health"]["ready"] is True
    assert payload["provider_health"]["missing_env"] == []
    assert payload["provider_health"]["setup_commands"] == [
        'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
        'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
        'export DEEPSEEK_MODEL="deepseek-chat"',
    ]
    assert "real-secret-key" not in rendered


def test_leader_chat_provider_switch_intent_suggests_explicit_command_without_mutating_config(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project_with_default_leader(tmp_path, monkeypatch)
    config_before = (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(cli, "_command_path", lambda command: None)

    exit_code = cli.main(["leader", "chat", "--message", "切换 Leader 到 Codex CLI"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "setup"
    assert payload["message"] == "切换 Leader 到 Codex CLI"
    assert payload["next_command"] == "agentdeck leader set-provider --provider codex-cli --model codex-default"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["provider_health"]["provider"] == "deepseek"
    assert payload["provider_switch_card"] == {
        "mode": "provider_switch",
        "title": "Switch Leader provider",
        "current_provider": "deepseek",
        "current_model": "deepseek-chat",
        "target_provider": "codex-cli",
        "target_model": "codex-default",
        "target_leader_backend": {
            "agent_id": "leader",
            "provider": "codex-cli",
            "model": "codex-default",
            "provider_backend": "cli",
            "provider_transport": "subprocess",
            "reasoning_backend": "cli-subprocess",
            "runtime_kind": "logical_leader",
            "pane_backed": False,
            "pane_id": None,
            "approval_required": True,
            "dispatch_ready": False,
        },
        "target_readiness": {
            "agent_id": "leader",
            "provider": "codex-cli",
            "model": "codex-default",
            "approval_mode": "confirm",
            "provider_backend": "cli",
            "provider_transport": "subprocess",
            "leader_backend": {
                "agent_id": "leader",
                "provider": "codex-cli",
                "model": "codex-default",
                "provider_backend": "cli",
                "provider_transport": "subprocess",
                "reasoning_backend": "cli-subprocess",
                "runtime_kind": "logical_leader",
                "pane_backed": False,
                "pane_id": None,
                "approval_required": True,
                "dispatch_ready": False,
            },
            "ready": False,
            "supported": True,
            "missing_env": [],
            "detail": "codex is not found on PATH",
            "command_path": None,
            "setup_commands": ["codex login", "codex doctor"],
        },
        "require_ready": False,
        "command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
        "diagnostics_command": "agentdeck doctor",
        "safety": "explicit_user",
        "requires_explicit_user": True,
        "mutates_config": False,
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect provider setup",
                "command": "agentdeck doctor",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "set_provider",
                "label": "Switch Leader provider",
                "command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    assert payload["leader_explanation"] == {
        "mode": "setup",
        "summary": "Leader recommends an explicit provider switch command without mutating provider config.",
        "reason": "human asked to switch Leader provider",
        "next_command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
        "recommended_action_id": "codex-cli",
        "action_kind": "provider_switch",
        "action_status": "suggested",
        "safety": "explicit_user",
        "requires_explicit_user": True,
    }
    assert payload["intent_card"] == {
        "mode": "setup",
        "matched_intent": "setup",
        "route_source": "local_rule",
        "embedded_card": "provider_health",
        "secondary_embedded_cards": ["provider_switch_card"],
        "read_only": True,
        "next_command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
        "requires_explicit_user": True,
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect provider_health",
                "command": "agentdeck doctor",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "next",
                "label": "Switch Leader provider",
                "command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    assert cli.validate_leader_chat_contract(payload) == {"ok": True, "errors": []}
    assert (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8") == config_before

    state = StateStore(root).load()
    assert state["plans"] == []
    assert state["leader_actions"] == []
    assert state["chat_turns"][0]["mode"] == "setup"
    assert state["chat_turns"][0]["next_command"] == (
        "agentdeck leader set-provider --provider codex-cli --model codex-default"
    )
    assert state["chat_turns"][0]["action_kind"] == "provider_switch"
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}


def test_leader_chat_provider_switch_require_ready_intent_suggests_guarded_command_without_mutating_config(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project_with_default_leader(tmp_path, monkeypatch)
    config_before = (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(cli, "_command_path", lambda command: None)

    exit_code = cli.main(["leader", "chat", "--message", "切换 Leader 到 Claude CLI，要求可用"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected_command = (
        "agentdeck leader set-provider --provider claude-cli --model claude-default --require-ready"
    )
    assert payload["mode"] == "setup"
    assert payload["message"] == "切换 Leader 到 Claude CLI，要求可用"
    assert payload["next_command"] == expected_command
    assert payload["leader_explanation"]["next_command"] == expected_command
    assert payload["leader_explanation"]["recommended_action_id"] == "claude-cli"
    assert payload["leader_explanation"]["action_kind"] == "provider_switch"
    assert payload["leader_explanation"]["safety"] == "explicit_user"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["intent_card"]["embedded_card"] == "provider_health"
    assert payload["intent_card"]["secondary_embedded_cards"] == ["provider_switch_card"]
    assert payload["intent_card"]["next_command"] == expected_command
    assert payload["intent_card"]["controls"][1] == {
        "kind": "next",
        "label": "Switch Leader provider",
        "command": expected_command,
        "safety": "explicit_user",
        "enabled": True,
        "blocker": None,
    }
    assert payload["provider_switch_card"]["target_provider"] == "claude-cli"
    assert payload["provider_switch_card"]["target_model"] == "claude-default"
    assert payload["provider_switch_card"]["require_ready"] is True
    assert payload["provider_switch_card"]["target_readiness"]["ready"] is False
    assert payload["provider_switch_card"]["target_readiness"]["detail"] == "claude is not found on PATH"
    assert payload["provider_switch_card"]["command"] == expected_command
    assert payload["provider_switch_card"]["controls"][1] == {
        "kind": "guarded_set_provider",
        "label": "Switch Leader provider if ready",
        "command": expected_command,
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "target provider is not ready",
    }
    assert cli.validate_leader_chat_contract(payload) == {"ok": True, "errors": []}
    assert (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8") == config_before

    state = StateStore(root).load()
    assert state["plans"] == []
    assert state["leader_actions"] == []
    assert state["chat_turns"][0]["mode"] == "setup"
    assert state["chat_turns"][0]["next_command"] == expected_command
    assert state["chat_turns"][0]["action_kind"] == "provider_switch"
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state.get("inbox", {}) == {}


def test_leader_chat_continue_returns_recovery_card_without_creating_action(tmp_path, monkeypatch, capsys) -> None:
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
    assert payload["mode"] == "continue"
    assert payload["message"] == "继续"
    assert payload["turn_id"].startswith("cht_")
    assert payload["plan_id"] == plan_id
    assert payload["project_view"]["plans"]["count"] == 1
    assert payload["project_view"]["chat_turns"]["count"] == 1
    assert payload["project_view"]["chat_turns"]["items"][0]["turn_id"] == payload["turn_id"]
    assert payload["project_view"]["chat_turns"]["items"][0]["mode"] == "continue"
    assert payload["review"] is None
    assert payload["recovery"]["status"] == "dispatch_ready"
    assert payload["next_command"] == f"agentdeck approval dispatch --approval-id {approval_id}"
    assert payload["continue_card"]["status"] == "dispatch_ready"
    assert payload["continue_card"]["next_command"] == payload["next_command"]
    assert payload["continue_card"]["recommended_action"]["target_id"] == approval_id
    assert payload["continue_card"]["recommended_action"]["safety"] == "explicit_runtime"
    assert payload["continue_card"]["project_view_command"] == "agentdeck status"
    assert payload["continue_card"]["leader_action"] is None
    assert payload["approval_card"]["count"] == 3
    assert payload["approval_card"]["approvals"][0]["approval_id"] == approval_id
    assert payload["approval_card"]["approvals"][0]["dispatch_command"] == payload["next_command"]
    assert payload["approval_card"]["approvals"][0]["preview_command"] == "agentdeck approval list"
    assert payload["inbox_card"] is None
    assert payload["leader_action"] is None
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["leader_actions"]["count"] == 0
    assert payload["leader_explanation"]["mode"] == "continue"
    assert payload["leader_explanation"]["recommended_action_id"] == approval_id
    assert payload["leader_explanation"]["action_kind"] == "approval"
    assert payload["leader_explanation"]["action_status"] == "dispatch_ready"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert payload["leader_explanation"]["reason"] == payload["recovery"]["reason"]

    state = StateStore(root).load()
    assert state["chat_turns"][0]["turn_id"] == payload["turn_id"]
    assert state["chat_turns"][0]["mode"] == "continue"
    assert state["chat_turns"][0]["plan_id"] == plan_id
    assert state["chat_turns"][0]["review"] is None
    assert state["chat_turns"][0]["action_id"] is None
    assert state["chat_turns"][0]["action_kind"] is None
    assert state["leader_actions"] == []
    assert len(state["plans"]) == 1
    assert state["messages"] == []
    assert state["jobs"] == []

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_chat_turn"' in events


def test_leader_chat_continue_promotes_dispatch_ready_card_next_command(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["agents"] = {
        "planner": {
            "agent_id": "planner",
            "pane_id": "%42",
            "session_name": "agentdeck",
            "cwd": str(root),
            "status": "running",
        },
        "coder": {
            "agent_id": "coder",
            "pane_id": "%43",
            "session_name": "agentdeck",
            "cwd": str(root),
            "status": "running",
        },
    }
    state["approvals"] = [
        {
            "approval_id": "apv_planner",
            "plan_id": "pln_ready",
            "step_id": "step_1",
            "step": 1,
            "agent_id": "planner",
            "role": "planning",
            "task": "规划继续批量派发",
            "risk": "low",
            "status": "approved",
            "created_at": "2026-07-05T00:00:00+00:00",
        },
        {
            "approval_id": "apv_coder",
            "plan_id": "pln_ready",
            "step_id": "step_2",
            "step": 2,
            "agent_id": "coder",
            "role": "implementation",
            "task": "实现继续批量派发",
            "risk": "low",
            "status": "approved",
            "created_at": "2026-07-05T00:00:01+00:00",
        },
    ]
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "继续"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "continue"
    assert payload["recovery"]["status"] == "dispatch_ready"
    assert payload["next_command"] == "agentdeck approval dispatch-ready --confirm"
    assert payload["continue_card"]["next_command"] == payload["next_command"]
    assert payload["continue_card"]["recommended_action"] == {
        "label": "Dispatch ready approvals",
        "command": "agentdeck approval dispatch-ready --confirm",
        "safety": "explicit_runtime",
        "requires_explicit_user": True,
        "source": "approval",
        "target_id": "dispatch_ready",
    }
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert payload["leader_explanation"]["recommended_action_id"] == "dispatch_ready"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert [
        approval["status"]
        for approval in payload["approval_card"]["approvals"]
        if approval["approval_id"] in {"apv_planner", "apv_coder"}
    ] == ["approved", "approved"]
    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck approval dispatch-ready --confirm"
    assert state_after["approvals"][0]["status"] == "approved"
    assert state_after["approvals"][1]["status"] == "approved"
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_continue_embeds_inbox_card_for_pending_inbox(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["inbox"] = {
        "planner": [
            {
                "inbox_id": "inb_continue_head",
                "event_type": "task_request",
                "message_id": "msg_continue",
                "attempt_id": "att_continue",
                "job_id": "job_continue",
                "reply_id": None,
                "from_actor": "leader",
                "to_agent": "planner",
                "task": "继续时展示 mailbox",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ]
    }
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "继续"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "continue"
    assert payload["recovery"]["status"] == "inbox_pending"
    assert payload["next_command"] == "agentdeck inbox --agent planner"
    assert payload["continue_card"]["status"] == "inbox_pending"
    assert payload["continue_card"]["next_command"] == payload["next_command"]
    assert payload["inbox_card"]["agent_id"] == "planner"
    assert payload["inbox_card"]["count"] == 1
    assert payload["inbox_card"]["head_inbox_id"] == "inb_continue_head"
    assert payload["inbox_card"]["items"][0]["inbox_id"] == "inb_continue_head"
    assert payload["inbox_card"]["items"][0]["ack_command"] == (
        "agentdeck ack --agent planner --inbox-id inb_continue_head"
    )
    assert payload["approval_card"] is None
    assert payload["leader_action"] is None
    assert payload["leader_actions"]["count"] == 0

    state_after = StateStore(root).load()
    assert state_after["inbox"]["planner"][0]["status"] == "pending"
    assert state_after["chat_turns"][0]["mode"] == "continue"
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_continue_embeds_runtime_card_for_stale_runtime(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["agents"]["planner"] = {
        "agent_id": "planner",
        "pane_id": None,
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "stale",
    }
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "继续"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "continue"
    assert payload["recovery"]["status"] == "runtime_stale"
    assert payload["next_command"] == "agentdeck agent refresh"
    assert payload["continue_card"]["status"] == "runtime_stale"
    assert payload["runtime_card"]["refresh_command"] == "agentdeck agent refresh"
    assert payload["runtime_card"]["by_status"]["stale"] == 1
    assert payload["runtime_card"]["agents"][0]["agent_id"] == "planner"
    assert payload["runtime_card"]["agents"][0]["status"] == "stale"
    assert payload["runtime_card"]["agents"][0]["controls"][0] == {
        "kind": "spawn",
        "label": "Spawn pane",
        "command": "agentdeck agent spawn --agent planner",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    assert payload["terminal_session_card"]["mode"] == "terminal_session"
    assert payload["terminal_session_card"]["running_count"] == 0
    assert payload["terminal_session_card"]["refresh_command"] == "agentdeck agent refresh"
    assert payload["terminal_session_card"]["terminals"][0]["agent_id"] == "planner"
    assert payload["terminal_session_card"]["terminals"][0]["enabled"] is False
    assert payload["terminal_session_card"]["terminals"][0]["blocker"] == "agent is not running"
    assert payload["intent_card"]["embedded_card"] == "continue_card"
    assert payload["intent_card"]["secondary_embedded_cards"] == ["runtime_card", "terminal_session_card"]
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["leader_explanation"]["action_kind"] == "runtime"
    assert payload["leader_explanation"]["action_status"] == "runtime_stale"
    assert payload["leader_explanation"]["next_command"] == "agentdeck agent refresh"

    state_after = StateStore(root).load()
    assert state_after["agents"]["planner"]["status"] == "stale"
    assert state_after["chat_turns"][0]["mode"] == "continue"
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_continue_embeds_trace_card_for_reply_waiting(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--provider", "fake", "--model", "fake-plan", "--task", "等待自然语言继续"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approvals = json.loads(capsys.readouterr().out)["approvals"]
    approval_id = approvals[0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    for approval in approvals[1:]:
        cli.main(["approval", "reject", "--approval-id", approval["approval_id"], "--reason", "focus first reply"])
        capsys.readouterr()
    cli.main(["approval", "dispatch", "--approval-id", approval_id])
    dispatch_payload = json.loads(capsys.readouterr().out)
    message_id = dispatch_payload["message_id"]
    inbox_id = dispatch_payload["inbox_card"]["head_inbox_id"]
    cli.main(["ack", "--agent", "planner", "--inbox-id", inbox_id])
    capsys.readouterr()
    expected_command = f"agentdeck capture-reply --agent planner --message-id {message_id}"
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "chat", "--message", "继续"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "continue"
    assert payload["plan_id"] == plan_id
    assert payload["recovery"]["status"] == "reply_waiting"
    assert payload["next_command"] == expected_command
    assert payload["continue_card"]["status"] == "reply_waiting"
    assert payload["continue_card"]["recommended_action"] == {
        "label": "Capture pending reply",
        "command": expected_command,
        "safety": "explicit_runtime",
        "requires_explicit_user": True,
        "source": "reply",
        "target_id": message_id,
    }
    assert payload["trace_card"]["message"]["message_id"] == message_id
    assert payload["trace_card"]["message"]["status"] == "dispatched"
    assert payload["trace_card"]["inbox_items"][0]["inbox_id"] == inbox_id
    assert payload["trace_card"]["inbox_items"][0]["status"] == "acked"
    assert payload["trace_card"]["replies"] == []
    assert payload["intent_card"]["embedded_card"] == "trace_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect trace_card",
        "command": f"agentdeck trace --id {message_id}",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Capture reply",
        "command": expected_command,
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["runtime_card"] is None
    assert payload["leader_explanation"]["action_kind"] == "reply"
    assert payload["leader_explanation"]["recommended_action_id"] == message_id
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["leader_explanation"]["next_command"] == expected_command
    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "continue"
    assert state_after["chat_turns"][0]["next_command"] == expected_command
    assert state_after["messages"] == state_before["messages"]
    assert state_after["jobs"] == state_before["jobs"]
    assert state_after["replies"] == []
    assert state_after["inbox"]["planner"][0]["status"] == "acked"
    assert fake.captured == []


def test_leader_chat_inspects_runtime_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")

    exit_code = cli.main(["leader", "chat", "--message", "查看 runtime"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "runtime"
    assert payload["message"] == "查看 runtime"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["next_command"] == "agentdeck agent list"
    assert payload["runtime_card"]["refresh_command"] == "agentdeck agent refresh"
    assert payload["runtime_card"]["by_status"]["running"] == 1
    assert payload["runtime_card"]["agents"][0]["agent_id"] == "planner"
    assert payload["runtime_card"]["agents"][0]["pane_id"] == "%42"
    assert payload["runtime_card"]["agents"][0]["controls"][0]["command"] == (
        "agentdeck agent terminal --agent planner"
    )
    assert payload["runtime_card"]["agents"][0]["controls"][1]["command"] == (
        "agentdeck agent capture --agent planner --lines 200"
    )
    assert payload["terminal_session_card"]["mode"] == "terminal_session"
    assert payload["terminal_session_card"]["session_name"] == "agentdeck"
    assert payload["terminal_session_card"]["running_count"] == 1
    assert payload["terminal_session_card"]["refresh_command"] == "agentdeck agent refresh"
    assert payload["terminal_session_card"]["controls"][0]["command"] == (
        "tmux -L agentdeck-repo attach -t agentdeck"
    )
    assert payload["terminal_session_card"]["terminals"][0]["agent_id"] == "planner"
    assert payload["terminal_session_card"]["terminals"][0]["select_pane_command"] == (
        "tmux -L agentdeck-repo select-pane -t %42"
    )
    assert payload["intent_card"]["embedded_card"] == "runtime_card"
    assert payload["intent_card"]["secondary_embedded_cards"] == ["terminal_session_card"]
    assert payload["leader_explanation"]["mode"] == "runtime"
    assert payload["leader_explanation"]["action_kind"] == "runtime"
    assert payload["leader_explanation"]["action_status"] == "running"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["project_view"]["chat_turns"]["items"][0]["mode"] == "runtime"

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "runtime"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck agent list"
    assert state_after["agents"]["planner"]["status"] == "running"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_suggests_runtime_refresh_without_reconciling_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["leader", "chat", "--message", "刷新 runtime"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "runtime"
    assert payload["message"] == "刷新 runtime"
    assert payload["next_command"] == "agentdeck agent refresh"
    assert payload["runtime_card"]["agents"][0]["agent_id"] == "planner"
    assert payload["runtime_card"]["agents"][0]["status"] == "running"
    assert payload["leader_explanation"]["mode"] == "runtime"
    assert payload["leader_explanation"]["summary"] == (
        "Leader recommends explicitly refreshing runtime bindings without mutating runtime state."
    )
    assert payload["leader_explanation"]["reason"] == "human asked to refresh runtime bindings"
    assert payload["leader_explanation"]["recommended_action_id"] is None
    assert payload["leader_explanation"]["action_kind"] == "runtime_refresh"
    assert payload["leader_explanation"]["action_status"] == "suggested"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["intent_card"]["embedded_card"] == "runtime_card"
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Refresh runtime",
        "command": "agentdeck agent refresh",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "runtime"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck agent refresh"
    assert state_after["agents"]["planner"]["status"] == "running"
    assert state_after["agents"]["planner"]["pane_id"] == "%42"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert fake.checked_panes == []
    assert fake.sent == []
    assert fake.captured == []
    assert fake.killed == []


def test_leader_chat_suggests_agent_spawn_without_mutating_runtime(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["leader", "chat", "--message", "启动 planner"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "runtime"
    assert payload["message"] == "启动 planner"
    assert payload["next_command"] == "agentdeck agent spawn --agent planner"
    assert payload["runtime_card"]["agents"][0]["agent_id"] == "planner"
    assert payload["runtime_card"]["agents"][0]["status"] == "configured"
    assert payload["runtime_card"]["agents"][0]["controls"][0] == {
        "kind": "spawn",
        "label": "Spawn pane",
        "command": "agentdeck agent spawn --agent planner",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    assert payload["leader_explanation"]["mode"] == "runtime"
    assert payload["leader_explanation"]["summary"] == (
        "Leader recommends explicitly spawning planner without mutating runtime state."
    )
    assert payload["leader_explanation"]["reason"] == "human asked to spawn one agent runtime"
    assert payload["leader_explanation"]["recommended_action_id"] == "planner"
    assert payload["leader_explanation"]["action_kind"] == "runtime_spawn"
    assert payload["leader_explanation"]["action_status"] == "configured"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["intent_card"]["embedded_card"] == "runtime_card"
    assert payload["intent_card"]["requires_explicit_user"] is True
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Spawn planner",
        "command": "agentdeck agent spawn --agent planner",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "runtime"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck agent spawn --agent planner"
    assert state_after["agents"].get("planner") is None
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert fake.sent == []
    assert fake.captured == []


def test_leader_chat_surfaces_agent_ready_card_for_multi_agent_startup(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["leader", "chat", "--message", "启动所有 agent"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "runtime"
    assert payload["message"] == "启动所有 agent"
    assert payload["next_command"] == "agentdeck agent spawn-ready --confirm"
    assert payload["agent_ready_card"]["mode"] == "agent_runtime_ready"
    assert payload["agent_ready_card"]["total_count"] == 3
    assert payload["agent_ready_card"]["running_count"] == 1
    assert payload["agent_ready_card"]["not_running_count"] == 2
    assert payload["agent_ready_card"]["all_running"] is False
    assert payload["agent_ready_card"]["next_command"] == payload["next_command"]
    assert payload["agent_ready_card"]["spawn_commands"] == [
        "agentdeck agent spawn --agent coder",
        "agentdeck agent spawn --agent reviewer",
    ]
    assert payload["agent_ready_card"]["spawn_ready_command"] == "agentdeck agent spawn-ready --confirm"
    assert payload["agent_ready_card"]["runtime_card"] == payload["runtime_card"]
    assert payload["leader_explanation"]["mode"] == "runtime"
    assert payload["leader_explanation"]["summary"] == (
        "Leader recommends explicitly preparing all configured agent runtimes without mutating runtime state."
    )
    assert payload["leader_explanation"]["reason"] == "human asked to prepare all agent runtimes"
    assert payload["leader_explanation"]["recommended_action_id"] == "agent_runtime_ready"
    assert payload["leader_explanation"]["action_kind"] == "runtime_ready"
    assert payload["leader_explanation"]["action_status"] == "partial"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["intent_card"]["embedded_card"] == "agent_ready_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect agent_ready_card",
        "command": "agentdeck agent ready",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Spawn ready agents",
        "command": "agentdeck agent spawn-ready --confirm",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "runtime"
    assert state_after["chat_turns"][0]["action_kind"] == "runtime_ready"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck agent spawn-ready --confirm"
    assert state_after["agents"]["planner"]["status"] == "running"
    assert state_after["agents"].get("coder") is None
    assert state_after["agents"].get("reviewer") is None
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert fake.sent == []
    assert fake.captured == []
    assert fake.killed == []
    assert fake.checked_panes == []


def test_leader_chat_open_agent_inbox_does_not_trigger_spawn_intent(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["inbox"] = {
        "planner": [
            {
                "inbox_id": "inb_open_inbox",
                "from": "coder",
                "to": "planner",
                "type": "task_reply",
                "status": "pending",
                "task": "检查 inbox 路由",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ]
    }
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "打开 planner inbox"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "inbox"
    assert payload["next_command"] == "agentdeck inbox --agent planner"
    assert payload["inbox_card"]["agent_id"] == "planner"
    assert payload["runtime_card"] is None
    assert payload["leader_explanation"]["safety"] == "inspect"
    state_after = StateStore(root).load()
    assert state_after["agents"].get("planner") is None
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_suggests_agent_send_without_sending_input(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["leader", "chat", "--message", "发送给 planner：继续 实现测试"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "runtime"
    assert payload["message"] == "发送给 planner：继续 实现测试"
    assert payload["next_command"] == "agentdeck agent send --agent planner --text '继续 实现测试'"
    assert payload["runtime_card"]["agents"][0]["agent_id"] == "planner"
    assert payload["runtime_card"]["agents"][0]["status"] == "running"
    assert payload["leader_explanation"]["mode"] == "runtime"
    assert payload["leader_explanation"]["summary"] == (
        "Leader recommends explicitly sending input to planner without mutating runtime state."
    )
    assert payload["leader_explanation"]["reason"] == "human asked to send input to one agent runtime"
    assert payload["leader_explanation"]["recommended_action_id"] == "planner"
    assert payload["leader_explanation"]["action_kind"] == "runtime_send"
    assert payload["leader_explanation"]["action_status"] == "running"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["intent_card"]["embedded_card"] == "runtime_card"
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Send input to planner",
        "command": "agentdeck agent send --agent planner --text '继续 实现测试'",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "runtime"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck agent send --agent planner --text '继续 实现测试'"
    assert state_after["agents"]["planner"]["pane_id"] == "%42"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert fake.sent == []
    assert fake.captured == []


def test_leader_chat_rejects_agent_send_when_agent_is_not_spawned(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "chat", "--message", "发送给 planner：继续 实现测试"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "agent is not spawned: planner"
    state_after = StateStore(root).load()
    assert state_after["chat_turns"] == []
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_suggests_agent_stop_without_killing_pane(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["leader", "chat", "--message", "停止 planner"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "runtime"
    assert payload["message"] == "停止 planner"
    assert payload["next_command"] == "agentdeck agent stop --agent planner"
    assert payload["runtime_card"]["agents"][0]["agent_id"] == "planner"
    assert payload["runtime_card"]["agents"][0]["status"] == "running"
    assert payload["leader_explanation"]["mode"] == "runtime"
    assert payload["leader_explanation"]["summary"] == (
        "Leader recommends explicitly stopping planner without mutating runtime state."
    )
    assert payload["leader_explanation"]["reason"] == "human asked to stop one agent runtime"
    assert payload["leader_explanation"]["recommended_action_id"] == "planner"
    assert payload["leader_explanation"]["action_kind"] == "runtime_stop"
    assert payload["leader_explanation"]["action_status"] == "running"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["intent_card"]["embedded_card"] == "runtime_card"
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Stop planner",
        "command": "agentdeck agent stop --agent planner",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "runtime"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck agent stop --agent planner"
    assert state_after["agents"]["planner"]["pane_id"] == "%42"
    assert state_after["agents"]["planner"]["status"] == "running"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert fake.sent == []
    assert fake.captured == []
    assert fake.killed == []


def test_leader_chat_rejects_agent_stop_when_agent_is_not_spawned(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "chat", "--message", "停止 planner"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "agent is not spawned: planner"
    state_after = StateStore(root).load()
    assert state_after["chat_turns"] == []
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_captures_agent_output_as_read_only_card(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["leader", "chat", "--message", "查看 planner 输出"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "capture"
    assert payload["message"] == "查看 planner 输出"
    assert payload["next_command"] == "agentdeck agent capture --agent planner --lines 200"
    assert payload["runtime_card"] is None
    assert payload["capture_card"] == {
        "agent_id": "planner",
        "pane_id": "%42",
        "lines": 200,
        "capture_command": "agentdeck agent capture --agent planner --lines 200",
        "output": "status: running\nsummary: planner is thinking\n",
    }
    assert payload["leader_explanation"] == {
        "mode": "capture",
        "summary": "Leader captured a visible agent pane as a read-only terminal snapshot.",
        "reason": "human asked to inspect one agent pane output",
        "next_command": "agentdeck agent capture --agent planner --lines 200",
        "recommended_action_id": "planner",
        "action_kind": "capture",
        "action_status": "captured",
        "safety": "inspect",
        "requires_explicit_user": False,
    }
    assert payload["intent_card"]["embedded_card"] == "capture_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect capture_card",
        "command": "agentdeck agent capture --agent planner --lines 200",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Capture agent output",
        "command": payload["next_command"],
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert fake.captured == [("%42", 200)]
    assert fake.sent == []

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "capture"
    assert state_after["chat_turns"][0]["next_command"] == payload["next_command"]
    assert state_after["chat_turns"][0]["action_kind"] == "capture"
    assert state_after["agents"]["planner"]["status"] == "running"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["approvals"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_opens_agent_terminal_card_without_reading_pane(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["leader", "chat", "--message", "打开 planner 终端"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "terminal"
    assert payload["message"] == "打开 planner 终端"
    assert payload["next_command"] == "tmux -L agentdeck-repo attach -t agentdeck"
    assert payload["runtime_card"] is None
    assert payload["capture_card"] is None
    assert payload["terminal_card"]["agent_id"] == "planner"
    assert payload["terminal_card"]["pane_id"] == "%42"
    assert payload["terminal_card"]["attach_command"] == payload["next_command"]
    assert payload["terminal_card"]["select_pane_command"] == (
        "tmux -L agentdeck-repo select-pane -t %42"
    )
    assert payload["terminal_card"]["capture_command"] == "agentdeck agent capture --agent planner --lines 200"
    assert payload["leader_explanation"] == {
        "mode": "terminal",
        "summary": "Leader recommends opening a visible agent terminal pane without reading or mutating it.",
        "reason": "human asked to open one agent terminal pane",
        "next_command": "tmux -L agentdeck-repo attach -t agentdeck",
        "recommended_action_id": "planner",
        "action_kind": "terminal",
        "action_status": "running",
        "safety": "inspect",
        "requires_explicit_user": False,
    }
    assert payload["intent_card"]["embedded_card"] == "terminal_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect terminal_card",
        "command": "tmux -L agentdeck-repo attach -t agentdeck",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Open terminal",
        "command": payload["next_command"],
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert fake.captured == []
    assert fake.sent == []

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "terminal"
    assert state_after["chat_turns"][0]["next_command"] == payload["next_command"]
    assert state_after["chat_turns"][0]["action_kind"] == "terminal"
    assert state_after["agents"]["planner"]["status"] == "running"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["approvals"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_rejects_capture_for_unspawned_agent_without_planning(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "chat", "--message", "查看 planner 输出"])

    assert exit_code == 1
    assert capsys.readouterr().err == "agent is not spawned: planner\n"
    state_after = StateStore(root).load()
    assert state_after["chat_turns"] == []
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_inspects_queue_without_applying_action(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "chat", "--message", "帮我实现任务入口"])
    planned = json.loads(capsys.readouterr().out)
    action_id = planned["leader_action"]["action_id"]

    exit_code = cli.main(["leader", "chat", "--message", "查看队列"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "queue"
    assert payload["message"] == "查看队列"
    assert payload["plan_id"] == planned["plan_id"]
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["runtime_card"] is None
    assert payload["next_command"] == f"agentdeck leader apply-action --action-id {action_id}"
    assert payload["queue_card"]["active_queue_source"] == "leader_action"
    assert payload["queue_card"]["next_command"] == payload["next_command"]
    assert payload["queue_card"]["leader_actions"]["count"] == 1
    assert payload["queue_card"]["leader_actions"]["pending"] == 1
    assert payload["queue_card"]["leader_actions"]["recommended_action_id"] == action_id
    assert payload["operator_card"]["source"] == "leader_action"
    assert payload["operator_card"]["target_id"] == action_id
    assert payload["operator_card"]["preview_command"] == f"agentdeck leader action --action-id {action_id}"
    assert payload["operator_card"]["apply_command"] == payload["next_command"]
    assert payload["operator_card"]["can_apply"] is True
    assert payload["operator_card"]["controls"][0]["command"] == payload["operator_card"]["preview_command"]
    assert payload["operator_card"]["controls"][1]["command"] == payload["next_command"]
    assert payload["leader_explanation"]["mode"] == "queue"
    assert payload["leader_explanation"]["recommended_action_id"] == action_id
    assert payload["leader_explanation"]["action_kind"] == "leader_action"
    assert payload["leader_explanation"]["action_status"] == "action_required"
    assert payload["leader_explanation"]["safety"] == "safe_apply"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["project_view"]["chat_turns"]["items"][-1]["mode"] == "queue"

    state_after = StateStore(root).load()
    assert [turn["mode"] for turn in state_after["chat_turns"]] == ["plan", "queue"]
    assert state_after["chat_turns"][1]["next_command"] == payload["next_command"]
    assert state_after["chat_turns"][1]["action_kind"] == "queue"
    assert len(state_after["leader_actions"]) == 1
    assert state_after["leader_actions"][0]["action_id"] == action_id
    assert state_after["leader_actions"][0]["status"] == "pending"
    assert state_after["approvals"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_queue_surfaces_dispatch_ready_operator_without_dispatching(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%77")
    store = StateStore(root)
    state = store.load()
    state["approvals"] = [
        {
            "approval_id": "apv_planner",
            "plan_id": "pln_ready",
            "step": 1,
            "agent_id": "planner",
            "role": "planning",
            "task": "规划批量派发",
            "risk": "low",
            "status": "approved",
            "created_at": "2026-07-05T00:00:00+00:00",
        },
        {
            "approval_id": "apv_coder",
            "plan_id": "pln_ready",
            "step": 2,
            "agent_id": "coder",
            "role": "implementation",
            "task": "实现批量派发",
            "risk": "low",
            "status": "approved",
            "created_at": "2026-07-05T00:00:01+00:00",
        },
    ]
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "查看控制面"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "queue"
    assert payload["next_command"] == "agentdeck approval dispatch-ready --confirm"
    assert payload["queue_card"]["next_command"] == payload["next_command"]
    assert payload["operator_card"]["action_kind"] == "approval_dispatch_ready"
    assert payload["operator_card"]["command"] == payload["next_command"]
    assert payload["operator_card"]["controls"][-1]["kind"] == "dispatch_ready"
    assert payload["operator_card"]["controls"][-1]["label"] == "Dispatch ready approvals"
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Dispatch ready approvals",
        "command": "agentdeck approval dispatch-ready --confirm",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    state_after = StateStore(root).load()
    assert state_after["approvals"][0]["status"] == "approved"
    assert state_after["approvals"][1]["status"] == "approved"
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert state_after.get("inbox", {}) == {}


def test_leader_chat_inspects_roles_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "chat", "--message", "查看角色"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "role"
    assert payload["message"] == "查看角色"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["runtime_card"] is None
    assert payload["queue_card"] is None
    assert payload["operator_card"] is None
    assert payload["next_command"] == "agentdeck workbench"
    assert payload["role_card"]["count"] == 3
    assert payload["role_card"]["assign_command_template"] == (
        "agentdeck agent assign-role --agent <agent_id> --role <role> --role-prompt <role_prompt>"
    )
    assert payload["role_card"]["agents"][0]["agent_id"] == "planner"
    assert payload["role_card"]["agents"][0]["role"] == "planning"
    assert payload["role_card"]["agents"][0]["assign_command"].startswith(
        "agentdeck agent assign-role --agent planner --role planning"
    )
    assert payload["leader_explanation"]["mode"] == "role"
    assert payload["leader_explanation"]["action_kind"] == "role"
    assert payload["leader_explanation"]["action_status"] == "configured"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["project_view"]["chat_turns"]["items"][0]["mode"] == "role"

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "role"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck workbench"
    assert state_after["chat_turns"][0]["action_kind"] == "role"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["approvals"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_role_assignment_intent_suggests_explicit_command_without_mutating_config(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_before = (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8")

    exit_code = cli.main(["leader", "chat", "--message", "把 planner 设为 架构师"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "role"
    assert payload["message"] == "把 planner 设为 架构师"
    assert payload["next_command"] == (
        "agentdeck agent assign-role --agent planner --role 架构师 --role-prompt '你负责架构师。'"
    )
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["runtime_card"] is None
    assert payload["queue_card"] is None
    assert payload["operator_card"] is None
    assert payload["role_card"]["agents"][0]["agent_id"] == "planner"
    assert payload["role_card"]["agents"][0]["role"] == "planning"
    assert payload["leader_explanation"] == {
        "mode": "role",
        "summary": "Leader recommends an explicit role assignment command without mutating role config.",
        "reason": "human asked to assign an agent role",
        "next_command": "agentdeck agent assign-role --agent planner --role 架构师 --role-prompt '你负责架构师。'",
        "recommended_action_id": "planner",
        "action_kind": "role_assign",
        "action_status": "suggested",
        "safety": "explicit_user",
        "requires_explicit_user": True,
    }
    assert payload["intent_card"] == {
        "mode": "role",
        "matched_intent": "role",
        "route_source": "local_rule",
        "embedded_card": "role_card",
        "secondary_embedded_cards": [],
        "read_only": True,
        "next_command": "agentdeck agent assign-role --agent planner --role 架构师 --role-prompt '你负责架构师。'",
        "requires_explicit_user": True,
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect role_card",
                "command": "agentdeck workbench",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "next",
                "label": "Assign role",
                "command": "agentdeck agent assign-role --agent planner --role 架构师 --role-prompt '你负责架构师。'",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    assert cli.validate_leader_chat_contract(payload) == {"ok": True, "errors": []}
    assert (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8") == config_before

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "role"
    assert state_after["chat_turns"][0]["next_command"] == (
        "agentdeck agent assign-role --agent planner --role 架构师 --role-prompt '你负责架构师。'"
    )
    assert state_after["chat_turns"][0]["action_kind"] == "role_assign"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["approvals"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_task_assignment_intent_creates_pending_approval_without_dispatching(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_before = (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8")

    exit_code = cli.main(["leader", "chat", "--message", "让 planner 规划 README 更新"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    approval = payload["approval_card"]["approvals"][0]
    approval_id = approval["approval_id"]
    assert payload["mode"] == "approval"
    assert payload["message"] == "让 planner 规划 README 更新"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["runtime_card"] is None
    assert payload["queue_card"] is None
    assert payload["operator_card"] is None
    assert payload["role_card"] is None
    assert payload["next_command"] == f"agentdeck approval approve --approval-id {approval_id}"
    assert payload["approval_card"]["count"] == 1
    assert approval["plan_id"] is None
    assert approval["step"] == 1
    assert approval["agent_id"] == "planner"
    assert approval["role"] == "planning"
    assert approval["task"] == "规划 README 更新"
    assert approval["risk"] == "human_requested"
    assert approval["status"] == "pending"
    assert approval["source"] == "leader_chat_task_assignment"
    assert approval["approve_command"] == payload["next_command"]
    assert approval["can_dispatch"] is False
    assert approval["dispatch_blocker"] == "approval is not approved"
    assert payload["leader_explanation"] == {
        "mode": "approval",
        "summary": "Leader created a pending approval from explicit task assignment without dispatching runtime work.",
        "reason": "human asked to assign a task to an agent",
        "next_command": f"agentdeck approval approve --approval-id {approval_id}",
        "recommended_action_id": approval_id,
        "action_kind": "approval_create",
        "action_status": "pending",
        "safety": "explicit_runtime",
        "requires_explicit_user": True,
    }
    assert payload["intent_card"] == {
        "mode": "approval",
        "matched_intent": "approval",
        "route_source": "local_rule",
        "embedded_card": "approval_card",
        "secondary_embedded_cards": [],
        "read_only": False,
        "next_command": f"agentdeck approval approve --approval-id {approval_id}",
        "requires_explicit_user": True,
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect approval_card",
                "command": "agentdeck approval list",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "next",
                "label": "Approve approval",
                "command": f"agentdeck approval approve --approval-id {approval_id}",
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    assert cli.validate_leader_chat_contract(payload) == {"ok": True, "errors": []}
    assert (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8") == config_before

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "approval"
    assert state_after["chat_turns"][0]["next_command"] == f"agentdeck approval approve --approval-id {approval_id}"
    assert state_after["chat_turns"][0]["action_kind"] == "approval_create"
    assert state_after["approvals"][0]["approval_id"] == approval_id
    assert state_after["approvals"][0]["agent_id"] == "planner"
    assert state_after["approvals"][0]["task"] == "规划 README 更新"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert state_after.get("inbox", {}) == {}

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "approval_created_from_chat"' in events
    assert f'"approval_id": "{approval_id}"' in events


def test_leader_chat_capture_reply_intent_suggests_explicit_command_without_capturing(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%77")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--task", "需要 worker 回复"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    cli.main(["approval", "dispatch", "--approval-id", approval_id])
    message_id = json.loads(capsys.readouterr().out)["message_id"]

    exit_code = cli.main(["leader", "chat", "--message", f"捕获 planner 对 {message_id} 的回复"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected_command = f"agentdeck capture-reply --agent planner --message-id {message_id}"
    assert payload["mode"] == "capture"
    assert payload["message"] == f"捕获 planner 对 {message_id} 的回复"
    assert payload["next_command"] == expected_command
    assert payload["capture_card"] is None
    assert payload["trace_card"]["query_id"] == message_id
    assert payload["trace_card"]["message"]["message_id"] == message_id
    assert payload["leader_explanation"] == {
        "mode": "capture",
        "summary": "Leader recommends explicitly capturing a structured reply without reading the pane in chat.",
        "reason": "human asked to capture an agent reply for a message",
        "next_command": expected_command,
        "recommended_action_id": message_id,
        "action_kind": "capture_reply",
        "action_status": "suggested",
        "safety": "explicit_runtime",
        "requires_explicit_user": True,
    }
    assert payload["intent_card"] == {
        "mode": "capture",
        "matched_intent": "capture",
        "route_source": "local_rule",
        "embedded_card": "trace_card",
        "secondary_embedded_cards": [],
        "read_only": True,
        "next_command": expected_command,
        "requires_explicit_user": True,
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect trace_card",
                "command": f"agentdeck trace --id {message_id}",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "next",
                "label": "Capture reply",
                "command": expected_command,
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    assert cli.validate_leader_chat_contract(payload) == {"ok": True, "errors": []}
    assert fake.captured == []

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "capture"
    assert state_after["chat_turns"][0]["next_command"] == expected_command
    assert state_after["chat_turns"][0]["action_kind"] == "capture_reply"
    assert state_after["replies"] == []
    assert state_after["messages"][0]["message_id"] == message_id
    assert state_after["jobs"][0]["message_id"] == message_id

    exit_code = cli.main(["leader", "chat", "--message", f"回收 planner 对 {message_id} 的结果"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "capture"
    assert payload["next_command"] == expected_command
    assert payload["intent_card"]["controls"][-1]["label"] == "Capture reply"
    assert fake.captured == []


def test_leader_chat_capture_current_reply_uses_latest_waiting_review_without_capturing(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%78")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--task", "需要当前回复"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    cli.main(["approval", "dispatch", "--approval-id", approval_id])
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "chat", "--message", "捕获当前回复"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected_command = f"agentdeck capture-reply --agent planner --message-id {message_id}"
    assert payload["mode"] == "capture"
    assert payload["message"] == "捕获当前回复"
    assert payload["plan_id"] == plan_id
    assert payload["review"]["next_action"] == "wait_for_reply"
    assert payload["review"]["message_id"] == message_id
    assert payload["next_command"] == expected_command
    assert payload["capture_card"] is None
    assert payload["trace_card"]["query_id"] == message_id
    assert payload["leader_explanation"]["action_kind"] == "capture_reply"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["intent_card"]["embedded_card"] == "trace_card"
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Capture reply",
        "command": expected_command,
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    assert cli.validate_leader_chat_contract(payload) == {"ok": True, "errors": []}
    assert fake.captured == []

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][-1]["mode"] == "capture"
    assert state_after["chat_turns"][-1]["plan_id"] == plan_id
    assert state_after["chat_turns"][-1]["next_command"] == expected_command
    assert state_after["chat_turns"][-1]["action_kind"] == "capture_reply"
    assert state_after["messages"] == state_before["messages"]
    assert state_after["jobs"] == state_before["jobs"]
    assert state_after["replies"] == state_before["replies"]
    assert state_after["inbox"] == state_before.get("inbox", {})


def test_leader_chat_inspects_ledger_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--agent", "planner", "--task", "设计消息账本"])
    dispatch_payload = json.loads(capsys.readouterr().out)

    exit_code = cli.main(["leader", "chat", "--message", "查看账本"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "ledger"
    assert payload["message"] == "查看账本"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["runtime_card"] is None
    assert payload["queue_card"] is None
    assert payload["operator_card"] is None
    assert payload["role_card"] is None
    assert payload["next_command"] == f"agentdeck trace --id {dispatch_payload['message_id']}"
    assert payload["ledger_card"]["messages"]["count"] == 1
    assert payload["ledger_card"]["messages"]["items"][0]["message_id"] == dispatch_payload["message_id"]
    assert payload["ledger_card"]["jobs"]["count"] == 1
    assert payload["ledger_card"]["jobs"]["items"][0]["message_id"] == dispatch_payload["message_id"]
    assert payload["ledger_card"]["jobs"]["items"][0]["job_id"].startswith("job_")
    assert payload["ledger_card"]["inbox"]["total"] == 1
    assert payload["ledger_card"]["trace_commands"][0] == payload["next_command"]
    assert payload["lineage_card"]["message_count"] == 1
    assert payload["lineage_card"]["job_count"] == 1
    assert payload["lineage_card"]["reply_count"] == 0
    assert payload["lineage_card"]["inbox_count"] == 1
    assert payload["lineage_card"]["recent_paths"] == [
        {
            "message_id": dispatch_payload["message_id"],
            "job_id": payload["ledger_card"]["jobs"]["items"][0]["job_id"],
            "reply_id": None,
            "inbox_id": payload["ledger_card"]["inbox"]["heads"]["planner"]["inbox_id"],
            "from_actor": "user",
            "to_agent": "planner",
            "from_agent": None,
            "to_actor": None,
            "task": "设计消息账本",
            "status": "inbox_pending",
            "trace_command": payload["next_command"],
        }
    ]
    assert payload["leader_explanation"]["mode"] == "ledger"
    assert payload["leader_explanation"]["recommended_action_id"] == dispatch_payload["message_id"]
    assert payload["leader_explanation"]["action_kind"] == "ledger"
    assert payload["leader_explanation"]["action_status"] == "has_traces"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["project_view"]["chat_turns"]["items"][0]["mode"] == "ledger"

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "ledger"
    assert state_after["chat_turns"][0]["next_command"] == payload["next_command"]
    assert state_after["chat_turns"][0]["action_kind"] == "ledger"
    assert state_after["messages"][0]["message_id"] == dispatch_payload["message_id"]
    assert state_after["jobs"][0]["message_id"] == dispatch_payload["message_id"]
    assert state_after["inbox"]["planner"][0]["status"] == "pending"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []


def test_leader_chat_inspects_audit_events_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    initial_event = cli.EventRecord.create("manual_checkpoint", {"note": "before audit chat"})
    store.append_event(initial_event)
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "chat", "--message", "查看审计"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "audit"
    assert payload["message"] == "查看审计"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["leader_action_card"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["runtime_card"] is None
    assert payload["queue_card"] is None
    assert payload["operator_card"] is None
    assert payload["role_card"] is None
    assert payload["ledger_card"] is None
    assert payload["workbench_card"] is None
    assert payload["next_command"] == "agentdeck events --limit 20"
    assert payload["audit_card"]["events_command"] == payload["next_command"]
    assert payload["audit_card"]["latest_event"]["event_type"] == "leader_chat_turn"
    assert payload["audit_card"]["recent_events"][0]["event_type"] == "manual_checkpoint"
    assert payload["audit_card"]["event_count"] == len(payload["audit_card"]["recent_events"])
    assert payload["leader_explanation"]["mode"] == "audit"
    assert payload["leader_explanation"]["action_kind"] == "audit"
    assert payload["leader_explanation"]["action_status"] == "has_events"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["intent_card"]["embedded_card"] == "audit_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect audit_card",
        "command": "agentdeck events --limit 20",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["plans"] == state_before["plans"]
    assert state_after["approvals"] == state_before["approvals"]
    assert state_after["leader_actions"] == state_before["leader_actions"]
    assert state_after["messages"] == state_before["messages"]
    assert state_after["jobs"] == state_before["jobs"]
    assert state_after["replies"] == state_before["replies"]
    assert state_after.get("inbox", {}) == state_before.get("inbox", {})
    assert len(state_after["chat_turns"]) == len(state_before["chat_turns"]) + 1
    assert state_after["chat_turns"][-1]["mode"] == "audit"
    assert state_after["chat_turns"][-1]["next_command"] == "agentdeck events --limit 20"
    assert state_after["chat_turns"][-1]["action_kind"] == "audit"


def test_leader_chat_inspects_artifacts_without_reading_files_or_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["artifacts"] = [
        {
            "artifact_id": "art_chat",
            "message_id": "msg_chat_artifact",
            "job_id": "job_chat_artifact",
            "reply_id": "rep_chat_artifact",
            "from_agent": "planner",
            "path": "docs/architecture/chat-artifact.md",
            "kind": "markdown",
            "status": "created",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    store.save(state)
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "chat", "--message", "查看产物"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "artifacts"
    assert payload["message"] == "查看产物"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["leader_action_card"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["runtime_card"] is None
    assert payload["queue_card"] is None
    assert payload["operator_card"] is None
    assert payload["role_card"] is None
    assert payload["ledger_card"] is None
    assert payload["workbench_card"] is None
    assert payload["next_command"] == "agentdeck artifacts"
    assert payload["artifacts_card"]["artifacts_command"] == "agentdeck artifacts"
    assert payload["artifacts_card"]["trace_command_template"] == "agentdeck trace --id <id>"
    assert payload["artifacts_card"]["artifacts"]["count"] == 1
    assert payload["artifacts_card"]["artifacts"]["items"][0] == {
        "artifact_id": "art_chat",
        "message_id": "msg_chat_artifact",
        "job_id": "job_chat_artifact",
        "reply_id": "rep_chat_artifact",
        "from_agent": "planner",
        "path": "docs/architecture/chat-artifact.md",
        "kind": "markdown",
        "status": "created",
        "created_at": "2026-07-04T00:00:00+00:00",
        "trace_command": "agentdeck trace --id msg_chat_artifact",
    }
    assert payload["leader_explanation"]["mode"] == "artifacts"
    assert payload["leader_explanation"]["action_kind"] == "artifacts"
    assert payload["leader_explanation"]["action_status"] == "has_artifacts"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["intent_card"]["embedded_card"] == "artifacts_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect artifacts_card",
        "command": "agentdeck artifacts",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["plans"] == state_before["plans"]
    assert state_after["approvals"] == state_before["approvals"]
    assert state_after["leader_actions"] == state_before["leader_actions"]
    assert state_after["messages"] == state_before["messages"]
    assert state_after["jobs"] == state_before["jobs"]
    assert state_after["replies"] == state_before["replies"]
    assert state_after["artifacts"] == state_before["artifacts"]
    assert state_after.get("inbox", {}) == state_before.get("inbox", {})
    assert len(state_after["chat_turns"]) == len(state_before["chat_turns"]) + 1
    assert state_after["chat_turns"][-1]["mode"] == "artifacts"
    assert state_after["chat_turns"][-1]["next_command"] == "agentdeck artifacts"
    assert state_after["chat_turns"][-1]["action_kind"] == "artifacts"


def test_leader_chat_opens_workbench_snapshot_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")

    exit_code = cli.main(["leader", "chat", "--message", "打开工作台"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "workbench"
    assert payload["message"] == "打开工作台"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["runtime_card"] is None
    assert payload["queue_card"] is None
    assert payload["operator_card"] is None
    assert payload["role_card"] is None
    assert payload["ledger_card"] is None
    assert payload["next_command"] == payload["workbench_card"]["next_command"]
    assert payload["workbench_card"]["mode"] == "workbench"
    assert payload["workbench_card"]["project_view"]["chat_turns"]["items"][0]["mode"] == "workbench"
    assert payload["workbench_card"]["runtime_card"]["by_status"]["running"] == 1
    assert payload["workbench_card"]["runtime_card"]["agents"][0]["pane_id"] == "%42"
    assert payload["workbench_card"]["role_card"]["count"] == 3
    assert payload["workbench_card"]["ledger_card"]["messages"]["count"] == 0
    assert payload["workbench_card"]["queue_card"]["active_queue_source"] == "none"
    assert payload["workbench_card"]["operator_card"]["preview_command"] == "agentdeck status"
    assert payload["workbench_card"]["operator_card"]["controls"][1] == {
        "kind": "explicit",
        "label": "Run explicit command",
        "command": None,
        "safety": None,
        "enabled": False,
        "blocker": "no explicit command available",
    }
    assert not [
        item
        for item in payload["workbench_card"]["control_registry"]
        if item["enabled"] is False and not item["blocker"]
    ]
    assert payload["workbench_card"]["contracts_card"]["workbench_contract"] == "agentdeck contract workbench"
    assert payload["leader_explanation"]["mode"] == "workbench"
    assert payload["leader_explanation"]["action_kind"] == "workbench"
    assert payload["leader_explanation"]["action_status"] == "ready"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert payload["intent_card"] == {
        "mode": "workbench",
        "matched_intent": "workbench",
        "route_source": "local_rule",
        "embedded_card": "workbench_card",
        "secondary_embedded_cards": [],
        "read_only": True,
        "next_command": payload["next_command"],
        "requires_explicit_user": False,
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect workbench_card",
                "command": "agentdeck workbench",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "next",
                "label": "Next command",
                "command": payload["next_command"],
                "safety": "inspect",
                "enabled": False,
                "blocker": "next command unavailable",
            }
        ],
    }
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["project_view"]["chat_turns"]["items"][0]["mode"] == "workbench"

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "workbench"
    assert state_after["chat_turns"][0]["next_command"] == payload["next_command"]
    assert state_after["chat_turns"][0]["action_kind"] == "workbench"
    assert state_after["agents"]["planner"]["status"] == "running"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["approvals"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_suggests_policy_mode_change_without_mutating_config(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_path = root / ".agentdeck" / "config.toml"
    config_before = config_path.read_text(encoding="utf-8")

    exit_code = cli.main(["leader", "chat", "--message", "切换到审批模式"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "policy"
    assert payload["message"] == "切换到审批模式"
    assert payload["next_command"] == "agentdeck policy set-mode --mode approve"
    assert payload["control_mode_card"]["mode"] == "control_mode"
    assert payload["control_mode_card"]["current_mode"] == "ask"
    assert payload["control_mode_card"]["approval_mode"] == "confirm"
    assert payload["leader_explanation"] == {
        "mode": "policy",
        "summary": "Leader recommends an explicit control mode command without mutating policy.",
        "reason": "human asked to change control mode",
        "next_command": "agentdeck policy set-mode --mode approve",
        "recommended_action_id": "approve",
        "action_kind": "policy_mode",
        "action_status": "suggested",
        "safety": "explicit_user",
        "requires_explicit_user": True,
    }
    assert payload["intent_card"]["embedded_card"] == "control_mode_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect control_mode_card",
        "command": "agentdeck workbench",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["intent_card"]["controls"][1] == {
        "kind": "next",
        "label": "Switch to approval mode",
        "command": "agentdeck policy set-mode --mode approve",
        "safety": "explicit_user",
        "enabled": True,
        "blocker": None,
    }
    assert config_path.read_text(encoding="utf-8") == config_before

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "policy"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck policy set-mode --mode approve"
    assert state_after["chat_turns"][0]["action_kind"] == "policy_mode"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["approvals"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_suggests_autonomous_policy_command_but_keeps_it_blocked(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_path = root / ".agentdeck" / "config.toml"
    config_before = config_path.read_text(encoding="utf-8")

    exit_code = cli.main(["leader", "chat", "--message", "开启 autonomous 完全放权"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "policy"
    assert payload["next_command"] == "agentdeck policy set-mode --mode autonomous"
    assert payload["control_mode_card"]["available_modes"][2]["mode"] == "autonomous"
    assert payload["control_mode_card"]["available_modes"][2]["enabled"] is False
    assert payload["control_mode_card"]["available_modes"][2]["blocker"] == (
        "autonomous execution policy is not implemented"
    )
    assert payload["leader_explanation"]["recommended_action_id"] == "autonomous"
    assert payload["leader_explanation"]["action_status"] == "blocked"
    assert payload["leader_explanation"]["safety"] == "explicit_user"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["intent_card"]["controls"][1] == {
        "kind": "next",
        "label": "Request autonomous mode",
        "command": "agentdeck policy set-mode --mode autonomous",
        "safety": "explicit_user",
        "enabled": True,
        "blocker": None,
    }
    assert config_path.read_text(encoding="utf-8") == config_before

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "policy"
    assert state_after["chat_turns"][0]["action_kind"] == "policy_mode"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []


def test_leader_chat_help_returns_capability_card_without_planning(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "chat", "--message", "帮助"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "help"
    assert payload["message"] == "帮助"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["approval_card"] is None
    assert payload["runtime_card"] is None
    assert payload["queue_card"] is None
    assert payload["operator_card"] is None
    assert payload["role_card"] is None
    assert payload["ledger_card"] is None
    assert payload["workbench_card"] is None
    assert payload["capability_card"]["mode"] == "help"
    assert payload["control_registry_card"]["mode"] == "control_registry"
    assert payload["control_registry_card"]["title"] == "Command palette"
    assert payload["control_registry_card"]["source_command"] == "agentdeck workbench"
    assert payload["control_registry_card"]["default_command"] == "agentdeck controls"
    assert payload["control_registry_card"]["item_count"] == len(payload["control_registry_card"]["items"])
    assert payload["control_registry_card"]["items"][0] == {
        "scope": "leader",
        "card": "leader_card",
        "kind": "chat",
        "label": "Ask Leader",
        "command": "agentdeck leader chat --message <text>",
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "requires message text",
        "agent_id": "leader",
        "control_id": payload["control_registry_card"]["items"][0]["control_id"],
    }
    assert payload["control_registry_card"]["items"][0]["control_id"].startswith(
        "leader:leader_card:chat:leader:"
    )
    assert {
        (item["scope"], item["card"], item["kind"], item["agent_id"])
        for item in payload["control_registry_card"]["items"]
    } >= {
        ("leader", "leader_card", "continue", "leader"),
        ("runtime", "runtime_card", "spawn", "planner"),
        ("operator", "operator_card", "preview", None),
    }
    assert payload["capability_card"]["default_command"] == "agentdeck workbench"
    assert payload["capability_card"]["capability_count"] == len(payload["capability_card"]["capabilities"])
    capability_modes = {item["mode"] for item in payload["capability_card"]["capabilities"]}
    assert {
        "plan",
        "review",
        "apply_action",
        "continue",
        "workbench",
        "runtime",
        "role",
        "ledger",
        "queue",
        "approval",
        "inbox",
        "policy",
        "provider_switch",
    } <= capability_modes
    capabilities = {item["mode"]: item for item in payload["capability_card"]["capabilities"]}
    assert capabilities["plan"]["safety"] == "plan_only"
    assert capabilities["plan"]["requires_explicit_user"] is False
    assert capabilities["plan"]["controls"] == [
        {
            "kind": "plan",
            "label": "Create Leader plan",
            "command": "agentdeck leader plan --task <goal>",
            "safety": "plan_only",
            "enabled": False,
            "blocker": "requires goal text",
        }
    ]
    assert capabilities["review"]["command"] == "agentdeck leader review --plan-id <plan_id>"
    assert capabilities["review"]["safety"] == "safe_apply"
    assert capabilities["review"]["controls"][0] == {
        "kind": "review",
        "label": "Review current plan",
        "command": "agentdeck leader review --plan-id <plan_id>",
        "safety": "safe_apply",
        "enabled": False,
        "blocker": "requires plan_id",
    }
    assert capabilities["apply_action"]["command"] == "agentdeck leader apply-action --action-id <action_id>"
    assert capabilities["apply_action"]["safety"] == "safe_apply"
    assert capabilities["apply_action"]["controls"][0] == {
        "kind": "apply",
        "label": "Apply safe Leader action",
        "command": "agentdeck leader apply-action --action-id <action_id>",
        "safety": "safe_apply",
        "enabled": False,
        "blocker": "requires action_id",
    }
    assert capabilities["inbox"]["controls"][0]["enabled"] is False
    assert capabilities["inbox"]["controls"][0]["blocker"] == "requires agent_id"
    assert capabilities["policy"]["command"] == "agentdeck policy set-mode --mode <mode>"
    assert capabilities["policy"]["safety"] == "explicit_user"
    assert capabilities["policy"]["requires_explicit_user"] is True
    assert capabilities["policy"]["controls"][0] == {
        "kind": "set",
        "label": "Set control mode",
        "command": "agentdeck policy set-mode --mode <mode>",
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "requires control mode",
    }
    assert capabilities["provider_switch"]["command"] == (
        "agentdeck leader set-provider --provider <provider> --model <model>"
    )
    assert capabilities["provider_switch"]["safety"] == "explicit_user"
    assert capabilities["provider_switch"]["requires_explicit_user"] is True
    assert capabilities["provider_switch"]["controls"][0] == {
        "kind": "set_provider",
        "label": "Switch Leader provider",
        "command": "agentdeck leader set-provider --provider <provider> --model <model>",
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "requires leader provider",
    }
    assert capabilities["workbench"]["controls"][0] == {
        "kind": "inspect",
        "label": "Open workbench",
        "command": "agentdeck workbench",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["capability_card"]["capabilities"][0]["safety"] == "inspect"
    assert payload["next_command"] == "agentdeck workbench"
    assert payload["leader_explanation"]["mode"] == "help"
    assert payload["leader_explanation"]["action_kind"] == "help"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["intent_card"]["embedded_card"] == "capability_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect capability_card",
        "command": "agentdeck workbench",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "help"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck workbench"
    assert state_after["chat_turns"][0]["action_kind"] == "help"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["approvals"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_help_filters_command_palette_without_planning(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["leader", "chat", "--message", "命令面板 runtime enabled only"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "help"
    registry = payload["control_registry_card"]
    assert registry["filters"] == {
        "scope": "runtime",
        "card": None,
        "query": None,
        "control_id": None,
        "enabled_only": True,
        "active_filter_keys": ["scope", "enabled_only"],
        "item_count_before_filter": 48,
    }
    assert registry["item_count"] == len(registry["items"])
    assert registry["group_count"] == len(registry["groups"])
    assert {item["scope"] for item in registry["items"]} == {"runtime"}
    assert all(item["enabled"] is True for item in registry["items"])
    assert [group["group_id"] for group in registry["groups"]] == ["runtime:runtime_card"]
    assert registry["groups"][0]["items"] == registry["items"]
    assert payload["next_command"] == "agentdeck workbench"
    assert payload["leader_explanation"]["action_kind"] == "help"

    state_after = StateStore(root).load()
    assert len(state_after["chat_turns"]) == len(before["chat_turns"]) + 1
    assert state_after["chat_turns"][0]["mode"] == "help"
    assert state_after["plans"] == before["plans"]
    assert state_after["leader_actions"] == before["leader_actions"]
    assert state_after["approvals"] == before["approvals"]
    assert state_after["messages"] == before["messages"]
    assert state_after["jobs"] == before["jobs"]


def test_leader_chat_help_filters_command_palette_by_query(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "chat", "--message", "命令面板 搜索 terminal"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "help"
    registry = payload["control_registry_card"]
    assert registry["filters"] == {
        "scope": None,
        "card": None,
        "query": "terminal",
        "control_id": None,
        "enabled_only": False,
        "active_filter_keys": ["query"],
        "item_count_before_filter": 48,
    }
    assert registry["items"]
    assert all(
        "terminal"
        in " ".join(
            str(item.get(field, ""))
            for field in ["scope", "card", "kind", "label", "command", "agent_id"]
        ).lower()
        for item in registry["items"]
    )
    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "help"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []


def test_leader_chat_help_filters_command_palette_by_control_id(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["controls"])

    assert exit_code == 0
    controls_payload = json.loads(capsys.readouterr().out)
    selected_item = next(item for item in controls_payload["items"] if item["enabled"] is True)
    control_id = selected_item["control_id"]

    exit_code = cli.main(["leader", "chat", "--message", f"命令面板 control_id {control_id}"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "help"
    registry = payload["control_registry_card"]
    assert registry["filters"] == {
        "scope": None,
        "card": None,
        "query": None,
        "control_id": control_id,
        "enabled_only": False,
        "active_filter_keys": ["control_id"],
        "item_count_before_filter": 48,
    }
    assert registry["items"] == [selected_item]
    assert registry["selection"] == {
        "requested_control_id": control_id,
        "matched": True,
        "matched_count": 1,
        "selected_control": selected_item,
        "blocker": None,
        "next_command": selected_item["command"],
    }
    assert registry["groups"][0]["items"] == registry["items"]

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "help"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []


def test_leader_chat_help_reports_unmatched_control_id_selection(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["leader", "chat", "--message", "命令面板 control_id missing:control"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "help"
    registry = payload["control_registry_card"]
    assert registry["filters"] == {
        "scope": None,
        "card": None,
        "query": None,
        "control_id": "missing:control",
        "enabled_only": False,
        "active_filter_keys": ["control_id"],
        "item_count_before_filter": 48,
    }
    assert registry["items"] == []
    assert registry["groups"] == []
    assert registry["selection"] == {
        "requested_control_id": "missing:control",
        "matched": False,
        "matched_count": 0,
        "selected_control": None,
        "blocker": "control_id not found",
        "next_command": None,
    }
    assert payload["next_command"] == "agentdeck workbench"
    assert payload["leader_explanation"]["action_kind"] == "help"

    state_after = StateStore(root).load()
    assert len(state_after["chat_turns"]) == len(before["chat_turns"]) + 1
    assert state_after["chat_turns"][0]["mode"] == "help"
    assert state_after["plans"] == before["plans"]
    assert state_after["leader_actions"] == before["leader_actions"]
    assert state_after["approvals"] == before["approvals"]
    assert state_after["messages"] == before["messages"]
    assert state_after["jobs"] == before["jobs"]


def test_leader_chat_help_reports_filtered_out_control_id_selection(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["controls"])

    assert exit_code == 0
    controls_payload = json.loads(capsys.readouterr().out)
    disabled_item = next(item for item in controls_payload["items"] if item["enabled"] is False)

    exit_code = cli.main(
        ["leader", "chat", "--message", f"命令面板 control_id {disabled_item['control_id']} enabled only"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "help"
    registry = payload["control_registry_card"]
    assert registry["filters"] == {
        "scope": None,
        "card": None,
        "query": None,
        "control_id": disabled_item["control_id"],
        "enabled_only": True,
        "active_filter_keys": ["control_id", "enabled_only"],
        "item_count_before_filter": 48,
    }
    assert registry["items"] == []
    assert registry["groups"] == []
    assert registry["selection"] == {
        "requested_control_id": disabled_item["control_id"],
        "matched": False,
        "matched_count": 0,
        "selected_control": None,
        "blocker": "control_id filtered out",
        "next_command": None,
    }

    state_after = StateStore(root).load()
    assert len(state_after["chat_turns"]) == len(before["chat_turns"]) + 1
    assert state_after["chat_turns"][0]["mode"] == "help"
    assert state_after["plans"] == before["plans"]
    assert state_after["leader_actions"] == before["leader_actions"]
    assert state_after["approvals"] == before["approvals"]
    assert state_after["messages"] == before["messages"]
    assert state_after["jobs"] == before["jobs"]


def test_leader_chat_inspects_agent_inbox_without_mutating_runtime(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["inbox"] = {
        "planner": [
            {
                "inbox_id": "inb_planner_head",
                "event_type": "task_request",
                "message_id": "msg_planner",
                "attempt_id": "att_planner",
                "job_id": "job_planner",
                "reply_id": None,
                "from_actor": "leader",
                "to_agent": "planner",
                "task": "拆解自然语言 inbox",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ]
    }
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "查看 planner inbox"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "inbox"
    assert payload["message"] == "查看 planner inbox"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["next_command"] == "agentdeck inbox --agent planner"
    assert payload["inbox_card"]["agent_id"] == "planner"
    assert payload["inbox_card"]["head_inbox_id"] == "inb_planner_head"
    assert payload["inbox_card"]["items"][0]["trace_command"] == "agentdeck trace --id inb_planner_head"
    assert payload["inbox_card"]["items"][0]["ack_command"] == "agentdeck ack --agent planner --inbox-id inb_planner_head"
    assert payload["inbox_card"]["items"][0]["can_ack"] is True
    assert payload["leader_explanation"]["mode"] == "inbox"
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert payload["leader_explanation"]["recommended_action_id"] == "inb_planner_head"
    assert payload["leader_explanation"]["action_kind"] == "inbox"
    assert payload["leader_explanation"]["action_status"] == "pending"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Open inbox",
        "command": payload["next_command"],
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["project_view"]["chat_turns"]["items"][0]["mode"] == "inbox"

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "inbox"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck inbox --agent planner"
    assert state_after["inbox"]["planner"][0]["status"] == "pending"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_resolves_current_inbox_from_recovery_without_agent_mention(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["inbox"] = {
        "planner": [
            {
                "inbox_id": "inb_current_head",
                "event_type": "task_request",
                "message_id": "msg_current",
                "attempt_id": "att_current",
                "job_id": "job_current",
                "reply_id": None,
                "from_actor": "leader",
                "to_agent": "planner",
                "task": "当前 recovery inbox",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ]
    }
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "查看当前 inbox"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert cli.validate_leader_chat_contract(payload) == {"ok": True, "errors": []}
    assert payload["mode"] == "inbox"
    assert payload["next_command"] == "agentdeck inbox --agent planner"
    assert payload["inbox_card"]["agent_id"] == "planner"
    assert payload["inbox_card"]["head_inbox_id"] == "inb_current_head"
    assert payload["leader_explanation"]["recommended_action_id"] == "inb_current_head"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["intent_card"]["controls"][-1]["label"] == "Open inbox"

    exit_code = cli.main(["leader", "chat", "--message", "确认当前 inbox"])

    assert exit_code == 0
    ack_payload = json.loads(capsys.readouterr().out)
    assert cli.validate_leader_chat_contract(ack_payload) == {"ok": True, "errors": []}
    assert ack_payload["mode"] == "inbox"
    assert ack_payload["next_command"] == "agentdeck ack --agent planner --inbox-id inb_current_head"
    assert ack_payload["leader_explanation"]["action_kind"] == "inbox_ack"
    assert ack_payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert ack_payload["leader_explanation"]["requires_explicit_user"] is True
    assert ack_payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Acknowledge inbox item",
        "command": "agentdeck ack --agent planner --inbox-id inb_current_head",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["inbox"]["planner"][0]["status"] == "pending"
    assert [turn["next_command"] for turn in state_after["chat_turns"]] == [
        "agentdeck inbox --agent planner",
        "agentdeck ack --agent planner --inbox-id inb_current_head",
    ]
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_suggests_trace_for_current_inbox_head(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["messages"] = [
        {
            "message_id": "msg_trace",
            "from_actor": "leader",
            "to_agent": "planner",
            "task": "代码实现完成",
            "prompt": "# AgentDeck dispatch\n\nAgent: planner\n\n当前任务:\n代码实现完成",
            "status": "replied",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["attempts"] = [
        {
            "attempt_id": "att_trace",
            "message_id": "msg_trace",
            "agent_id": "planner",
            "status": "completed",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["jobs"] = [
        {
            "job_id": "job_trace",
            "message_id": "msg_trace",
            "attempt_id": "att_trace",
            "agent_id": "planner",
            "pane_id": "%42",
            "status": "completed",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["replies"] = [
        {
            "reply_id": "rep_trace",
            "message_id": "msg_trace",
            "attempt_id": "att_trace",
            "job_id": "job_trace",
            "from_agent": "coder",
            "to_actor": "leader",
            "text": "status: completed\nsummary: 已完成实现。",
            "created_at": "2026-07-04T00:00:01+00:00",
        }
    ]
    state["inbox"] = {
        "planner": [
            {
                "inbox_id": "inb_trace_me",
                "event_type": "task_reply",
                "message_id": "msg_trace",
                "attempt_id": "att_trace",
                "job_id": "job_trace",
                "reply_id": "rep_trace",
                "from_agent": "coder",
                "to_agent": "planner",
                "task": "代码实现完成",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ]
    }
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "追踪 planner 当前 inbox"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "inbox"
    assert payload["next_command"] == "agentdeck trace --id inb_trace_me"
    assert payload["inbox_card"]["agent_id"] == "planner"
    assert payload["inbox_card"]["items"][0]["event_type"] == "task_reply"
    assert payload["inbox_card"]["items"][0]["trace_command"] == payload["next_command"]
    assert payload["trace_card"]["query_id"] == "inb_trace_me"
    assert payload["trace_card"]["message"]["message_id"] == "msg_trace"
    assert payload["trace_card"]["jobs"][0]["job_id"] == "job_trace"
    assert payload["trace_card"]["replies"][0]["reply_id"] == "rep_trace"
    assert payload["trace_card"]["inbox_items"][0]["inbox_id"] == "inb_trace_me"
    assert payload["leader_explanation"]["action_kind"] == "inbox_trace"
    assert payload["leader_explanation"]["recommended_action_id"] == "inb_trace_me"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["intent_card"]["embedded_card"] == "trace_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect trace_card",
        "command": "agentdeck trace --id inb_trace_me",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Inspect trace",
        "command": payload["next_command"],
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert StateStore(root).load()["inbox"]["planner"][0]["status"] == "pending"


def test_leader_chat_traces_specific_communication_id_without_mutating_runtime(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["messages"] = [
        {
            "message_id": "msg_trace_direct",
            "from_actor": "leader",
            "to_agent": "planner",
            "task": "直接追踪消息",
            "prompt": "# AgentDeck dispatch\n\nAgent: planner\n\n当前任务:\n直接追踪消息",
            "status": "replied",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["attempts"] = [
        {
            "attempt_id": "att_trace_direct",
            "message_id": "msg_trace_direct",
            "agent_id": "planner",
            "status": "completed",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["jobs"] = [
        {
            "job_id": "job_trace_direct",
            "message_id": "msg_trace_direct",
            "attempt_id": "att_trace_direct",
            "agent_id": "planner",
            "pane_id": "%42",
            "status": "completed",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["replies"] = [
        {
            "reply_id": "rep_trace_direct",
            "message_id": "msg_trace_direct",
            "attempt_id": "att_trace_direct",
            "job_id": "job_trace_direct",
            "from_agent": "planner",
            "to_actor": "leader",
            "text": "status: completed\nsummary: direct trace ok.",
            "created_at": "2026-07-04T00:00:01+00:00",
        }
    ]
    state["inbox"] = {
        "planner": [
            {
                "inbox_id": "inb_trace_direct",
                "event_type": "task_reply",
                "message_id": "msg_trace_direct",
                "attempt_id": "att_trace_direct",
                "job_id": "job_trace_direct",
                "reply_id": "rep_trace_direct",
                "from_agent": "planner",
                "to_agent": "planner",
                "task": "直接追踪消息",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ]
    }
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "追踪 msg_trace_direct"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "trace"
    assert payload["message"] == "追踪 msg_trace_direct"
    assert payload["next_command"] == "agentdeck trace --id msg_trace_direct"
    assert payload["inbox_card"] is None
    assert payload["ledger_card"] is None
    assert payload["lineage_card"] is None
    assert payload["trace_card"]["query_id"] == "msg_trace_direct"
    assert payload["trace_card"]["message"]["message_id"] == "msg_trace_direct"
    assert payload["trace_card"]["jobs"][0]["job_id"] == "job_trace_direct"
    assert payload["trace_card"]["replies"][0]["reply_id"] == "rep_trace_direct"
    assert payload["trace_card"]["inbox_items"][0]["inbox_id"] == "inb_trace_direct"
    assert payload["leader_explanation"] == {
        "mode": "trace",
        "summary": "Leader recommends inspecting a specific communication trace without mutating messages or runtime state.",
        "reason": "human asked to inspect one communication lineage by id",
        "next_command": "agentdeck trace --id msg_trace_direct",
        "recommended_action_id": "msg_trace_direct",
        "action_kind": "trace",
        "action_status": "found",
        "safety": "inspect",
        "requires_explicit_user": False,
    }
    assert payload["intent_card"]["embedded_card"] == "trace_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect trace_card",
        "command": "agentdeck trace --id msg_trace_direct",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Inspect trace",
        "command": payload["next_command"],
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "trace"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck trace --id msg_trace_direct"
    assert state_after["chat_turns"][0]["action_kind"] == "trace"
    assert state_after["inbox"]["planner"][0]["status"] == "pending"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []


def test_leader_chat_traces_specific_artifact_id_without_mutating_runtime(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["messages"] = [
        {
            "message_id": "msg_trace_artifact_direct",
            "from_actor": "leader",
            "to_agent": "planner",
            "task": "直接追踪产物",
            "prompt": "# AgentDeck dispatch\n\nAgent: planner\n\n当前任务:\n直接追踪产物",
            "status": "replied",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["attempts"] = [
        {
            "attempt_id": "att_trace_artifact_direct",
            "message_id": "msg_trace_artifact_direct",
            "agent_id": "planner",
            "status": "completed",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["jobs"] = [
        {
            "job_id": "job_trace_artifact_direct",
            "message_id": "msg_trace_artifact_direct",
            "attempt_id": "att_trace_artifact_direct",
            "agent_id": "planner",
            "pane_id": "%42",
            "status": "completed",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["replies"] = [
        {
            "reply_id": "rep_trace_artifact_direct",
            "message_id": "msg_trace_artifact_direct",
            "attempt_id": "att_trace_artifact_direct",
            "job_id": "job_trace_artifact_direct",
            "from_agent": "planner",
            "to_actor": "leader",
            "text": "status: completed\nfull_output_path: docs/architecture/artifact-trace.md",
            "created_at": "2026-07-04T00:00:01+00:00",
        }
    ]
    state["artifacts"] = [
        {
            "artifact_id": "art_trace_direct",
            "message_id": "msg_trace_artifact_direct",
            "attempt_id": "att_trace_artifact_direct",
            "job_id": "job_trace_artifact_direct",
            "reply_id": "rep_trace_artifact_direct",
            "from_agent": "planner",
            "path": "docs/architecture/artifact-trace.md",
            "kind": "markdown",
            "status": "created",
            "created_at": "2026-07-04T00:00:02+00:00",
        }
    ]
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "追踪 art_trace_direct"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "trace"
    assert payload["message"] == "追踪 art_trace_direct"
    assert payload["next_command"] == "agentdeck trace --id art_trace_direct"
    assert payload["trace_card"]["query_id"] == "art_trace_direct"
    assert payload["trace_card"]["message"]["message_id"] == "msg_trace_artifact_direct"
    assert payload["trace_card"]["artifacts"][0]["artifact_id"] == "art_trace_direct"
    assert payload["trace_card"]["artifacts"][0]["path"] == "docs/architecture/artifact-trace.md"
    assert payload["leader_explanation"]["recommended_action_id"] == "art_trace_direct"
    assert payload["intent_card"]["embedded_card"] == "trace_card"
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Inspect trace",
        "command": payload["next_command"],
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["chat_turns"][0]["mode"] == "trace"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck trace --id art_trace_direct"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == state["messages"]
    assert state_after["artifacts"] == state["artifacts"]


def test_leader_chat_rejects_unknown_trace_id_without_planning(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "chat", "--message", "追踪 msg_missing"])

    assert exit_code == 1
    assert capsys.readouterr().err == "unknown trace id: msg_missing\n"
    state_after = StateStore(root).load()
    assert state_after["chat_turns"] == []
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert state_after.get("inbox", {}) == {}


def test_leader_chat_suggests_ack_for_current_inbox_head_without_acknowledging(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["inbox"] = {
        "planner": [
            {
                "inbox_id": "inb_ack_me",
                "event_type": "task_request",
                "message_id": "msg_ack",
                "attempt_id": "att_ack",
                "job_id": "job_ack",
                "reply_id": None,
                "from_actor": "leader",
                "to_agent": "planner",
                "task": "确认后继续",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ]
    }
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "确认 planner 当前 inbox"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "inbox"
    assert payload["next_command"] == "agentdeck ack --agent planner --inbox-id inb_ack_me"
    assert payload["inbox_card"]["items"][0]["ack_command"] == payload["next_command"]
    assert payload["inbox_card"]["items"][0]["preview_command"] == "agentdeck trace --id inb_ack_me"
    assert payload["inbox_card"]["items"][0]["controls"][0]["command"] == "agentdeck trace --id inb_ack_me"
    assert payload["inbox_card"]["items"][0]["controls"][1]["command"] == payload["next_command"]
    assert payload["inbox_card"]["items"][0]["can_ack"] is True
    assert payload["leader_explanation"]["action_kind"] == "inbox_ack"
    assert payload["leader_explanation"]["recommended_action_id"] == "inb_ack_me"
    assert payload["leader_explanation"]["action_status"] == "pending"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Acknowledge inbox item",
        "command": payload["next_command"],
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["inbox"]["planner"][0]["status"] == "pending"
    assert state_after["chat_turns"][0]["mode"] == "inbox"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck ack --agent planner --inbox-id inb_ack_me"
    assert state_after["chat_turns"][0]["action_kind"] == "inbox_ack"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_inspects_and_acknowledges_leader_inbox_without_provider_or_runtime(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["inbox"] = {
        "leader": [
            {
                "inbox_id": "inb_leader_reply",
                "event_type": "task_reply",
                "message_id": "msg_leader_reply",
                "attempt_id": "att_leader_reply",
                "job_id": "job_leader_reply",
                "reply_id": "rep_leader_reply",
                "from_actor": None,
                "from_agent": "planner",
                "to_agent": "leader",
                "task": "planner 完成摘要",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ]
    }
    store.save(state)

    exit_code = cli.main(["leader", "chat", "--message", "查看 leader inbox"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert cli.validate_leader_chat_contract(payload) == {"ok": True, "errors": []}
    assert payload["mode"] == "inbox"
    assert payload["next_command"] == "agentdeck inbox --agent leader"
    assert payload["inbox_card"]["agent_id"] == "leader"
    assert payload["inbox_card"]["count"] == 1
    assert payload["inbox_card"]["head_inbox_id"] == "inb_leader_reply"
    assert payload["inbox_card"]["items"][0]["ack_command"] == (
        "agentdeck ack --agent leader --inbox-id inb_leader_reply"
    )
    assert payload["leader_explanation"]["mode"] == "inbox"
    assert payload["leader_explanation"]["summary"] == (
        "Leader recommends inspecting leader inbox without mutating runtime state."
    )
    assert payload["leader_explanation"]["reason"] == "human asked to inspect an agent inbox"
    assert payload["leader_explanation"]["next_command"] == "agentdeck inbox --agent leader"
    assert payload["leader_explanation"]["recommended_action_id"] == "inb_leader_reply"
    assert payload["leader_explanation"]["action_kind"] == "inbox"
    assert payload["leader_explanation"]["action_status"] == "pending"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["intent_card"]["embedded_card"] == "inbox_card"
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Open inbox",
        "command": "agentdeck inbox --agent leader",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }

    exit_code = cli.main(["leader", "chat", "--message", "确认 leader 当前 inbox"])

    assert exit_code == 0
    ack_payload = json.loads(capsys.readouterr().out)
    assert cli.validate_leader_chat_contract(ack_payload) == {"ok": True, "errors": []}
    assert ack_payload["mode"] == "inbox"
    assert ack_payload["next_command"] == "agentdeck ack --agent leader --inbox-id inb_leader_reply"
    assert ack_payload["leader_explanation"]["action_kind"] == "inbox_ack"
    assert ack_payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert ack_payload["leader_explanation"]["requires_explicit_user"] is True
    assert ack_payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Acknowledge inbox item",
        "command": "agentdeck ack --agent leader --inbox-id inb_leader_reply",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }

    state_after = StateStore(root).load()
    assert state_after["inbox"]["leader"][0]["status"] == "pending"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck inbox --agent leader"
    assert state_after["chat_turns"][1]["next_command"] == "agentdeck ack --agent leader --inbox-id inb_leader_reply"
    assert state_after["plans"] == []
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_inspects_approval_queue_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "审批自然语言入口"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    capsys.readouterr()

    exit_code = cli.main(["leader", "chat", "--message", "查看审批"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "approval"
    assert payload["message"] == "查看审批"
    assert payload["plan_id"] is None
    assert payload["review"] is None
    assert payload["leader_action"] is None
    assert payload["continue_card"] is None
    assert payload["inbox_card"] is None
    assert payload["next_command"] == "agentdeck approval list"
    assert payload["approval_card"]["count"] == 3
    assert payload["approval_card"]["approvals"][0]["approve_command"].startswith(
        "agentdeck approval approve --approval-id apv_"
    )
    assert payload["approval_card"]["approvals"][0]["preview_command"] == "agentdeck approval list"
    assert payload["approval_card"]["approvals"][0]["controls"][0]["command"] == "agentdeck approval list"
    assert payload["approval_card"]["approvals"][0]["controls"][1]["command"] == (
        payload["approval_card"]["approvals"][0]["approve_command"]
    )
    assert payload["leader_explanation"]["mode"] == "approval"
    assert payload["leader_explanation"]["action_kind"] == "approval"
    assert payload["leader_explanation"]["action_status"] == "pending"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["project_view"]["chat_turns"]["items"][0]["mode"] == "approval"

    state_after = StateStore(root).load()
    assert [item["status"] for item in state_after["approvals"]] == ["pending", "pending", "pending"]
    assert state_after["chat_turns"][0]["mode"] == "approval"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck approval list"
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_suggests_approve_for_pending_approval_without_approving(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "审批建议"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approvals = json.loads(capsys.readouterr().out)["approvals"]
    approval_id = approvals[0]["approval_id"]

    exit_code = cli.main(["leader", "chat", "--message", "批准当前审批"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "approval"
    assert payload["next_command"] == f"agentdeck approval approve --approval-id {approval_id}"
    assert payload["approval_card"]["approvals"][0]["approval_id"] == approval_id
    assert payload["approval_card"]["approvals"][0]["approve_command"] == payload["next_command"]
    assert payload["approval_card"]["approvals"][0]["preview_command"] == "agentdeck approval list"
    assert payload["leader_explanation"]["action_kind"] == "approval_approve"
    assert payload["leader_explanation"]["recommended_action_id"] == approval_id
    assert payload["leader_explanation"]["action_status"] == "pending"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True

    state_after = StateStore(root).load()
    assert state_after["approvals"][0]["status"] == "pending"
    assert state_after["chat_turns"][0]["mode"] == "approval"
    assert state_after["chat_turns"][0]["next_command"] == f"agentdeck approval approve --approval-id {approval_id}"
    assert state_after["chat_turns"][0]["action_kind"] == "approval_approve"
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_suggests_reject_for_pending_approval_without_rejecting(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "拒绝审批建议"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approvals = json.loads(capsys.readouterr().out)["approvals"]
    approval_id = approvals[0]["approval_id"]

    exit_code = cli.main(["leader", "chat", "--message", "拒绝当前审批"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "approval"
    assert payload["next_command"] == f"agentdeck approval reject --approval-id {approval_id} --reason <reason>"
    assert payload["approval_card"]["approvals"][0]["approval_id"] == approval_id
    assert payload["approval_card"]["approvals"][0]["reject_command"] == payload["next_command"]
    assert payload["approval_card"]["approvals"][0]["preview_command"] == "agentdeck approval list"
    assert payload["leader_explanation"]["action_kind"] == "approval_reject"
    assert payload["leader_explanation"]["recommended_action_id"] == approval_id
    assert payload["leader_explanation"]["action_status"] == "pending"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Reject approval",
        "command": payload["next_command"],
        "safety": "explicit_runtime",
        "enabled": False,
        "blocker": "requires reason",
    }

    state_after = StateStore(root).load()
    assert state_after["approvals"][0]["status"] == "pending"
    assert state_after["chat_turns"][0]["mode"] == "approval"
    assert state_after["chat_turns"][0]["next_command"] == (
        f"agentdeck approval reject --approval-id {approval_id} --reason <reason>"
    )
    assert state_after["chat_turns"][0]["action_kind"] == "approval_reject"
    assert state_after["leader_actions"] == []
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


def test_leader_chat_suggests_dispatch_for_approved_approval_without_dispatching(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "派发建议"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approvals = json.loads(capsys.readouterr().out)["approvals"]
    approval_id = approvals[0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    bind_agent(root, "planner", "%42")

    exit_code = cli.main(["leader", "chat", "--message", "派发当前审批"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "approval"
    assert payload["next_command"] == f"agentdeck approval dispatch --approval-id {approval_id}"
    assert payload["approval_card"]["approvals"][0]["approval_id"] == approval_id
    assert payload["approval_card"]["approvals"][0]["status"] == "approved"
    assert payload["approval_card"]["approvals"][0]["dispatch_command"] == payload["next_command"]
    assert payload["approval_card"]["approvals"][0]["can_dispatch"] is True
    assert payload["approval_card"]["approvals"][0]["preview_command"] == "agentdeck approval list"
    assert payload["dispatch_preview_card"] == {
        "approval_id": approval_id,
        "agent_id": "planner",
        "agent_role": "planning",
        "pane_id": "%42",
        "runtime_status": "running",
        "task": "Break down the goal and identify risks: 派发建议",
        "dispatch_command": f"agentdeck approval dispatch --approval-id {approval_id}",
        "approval_command": "agentdeck approval list",
        "inbox_command": "agentdeck inbox --agent planner",
            "requires_explicit_user": True,
            "safety": "explicit_runtime",
            "blocker": None,
            "controls": [
                {
                    "kind": "inspect",
                    "label": "Inspect approval",
                    "command": "agentdeck approval list",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "dispatch",
                    "label": "Dispatch approval",
                    "command": f"agentdeck approval dispatch --approval-id {approval_id}",
                    "safety": "explicit_runtime",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        }
    assert payload["intent_card"]["embedded_card"] == "dispatch_preview_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect dispatch_preview_card",
        "command": "agentdeck approval list",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Dispatch approval",
        "command": payload["next_command"],
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    assert payload["leader_explanation"]["action_kind"] == "approval_dispatch"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True

    state_after = StateStore(root).load()
    assert state_after["approvals"][0]["status"] == "approved"
    assert state_after["chat_turns"][0]["action_kind"] == "approval_dispatch"
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert state_after.get("inbox", {}) == {}
    assert payload["leader_explanation"]["recommended_action_id"] == approval_id
    assert payload["leader_explanation"]["action_status"] == "approved"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True

    state_after = StateStore(root).load()
    assert state_after["approvals"][0]["status"] == "approved"
    assert state_after["chat_turns"][0]["mode"] == "approval"
    assert state_after["chat_turns"][0]["next_command"] == f"agentdeck approval dispatch --approval-id {approval_id}"
    assert state_after["chat_turns"][0]["action_kind"] == "approval_dispatch"
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert state_after.get("inbox", {}) == {}
    assert state_after["agents"]["planner"]["status"] == "running"


def test_leader_chat_blocks_dispatch_preview_when_agent_is_not_spawned(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "派发前检查 runtime"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approvals = json.loads(capsys.readouterr().out)["approvals"]
    approval_id = approvals[0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()

    exit_code = cli.main(["leader", "chat", "--message", "派发当前审批"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "approval"
    assert payload["next_command"] == f"agentdeck approval dispatch --approval-id {approval_id}"
    assert payload["dispatch_preview_card"]["approval_id"] == approval_id
    assert payload["dispatch_preview_card"]["agent_id"] == "planner"
    assert payload["dispatch_preview_card"]["pane_id"] is None
    assert payload["dispatch_preview_card"]["runtime_status"] == "configured"
    assert payload["dispatch_preview_card"]["blocker"] == "agent is not spawned: planner"
    assert payload["dispatch_preview_card"]["controls"][-1] == {
        "kind": "dispatch",
        "label": "Dispatch approval",
        "command": f"agentdeck approval dispatch --approval-id {approval_id}",
        "safety": "explicit_runtime",
        "enabled": False,
        "blocker": "agent is not spawned: planner",
    }
    assert payload["intent_card"]["embedded_card"] == "dispatch_preview_card"
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Dispatch approval",
        "command": payload["next_command"],
        "safety": "explicit_runtime",
        "enabled": False,
        "blocker": "agent is not spawned: planner",
    }
    assert payload["leader_explanation"]["action_kind"] == "approval_dispatch"


def test_leader_chat_previews_all_approved_dispatches_without_dispatching(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "批量派发建议"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approvals = json.loads(capsys.readouterr().out)["approvals"]
    planner_approval_id = approvals[0]["approval_id"]
    coder_approval_id = approvals[1]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", planner_approval_id])
    capsys.readouterr()
    cli.main(["approval", "approve", "--approval-id", coder_approval_id])
    capsys.readouterr()
    bind_agent(root, "planner", "%42")

    exit_code = cli.main(["leader", "chat", "--message", "派发所有已审批"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "approval"
    assert payload["next_command"] == "agentdeck approval dispatch-ready --confirm"
    assert payload["dispatch_preview_card"] is None
    batch_card = payload["dispatch_batch_preview_card"]
    assert batch_card["mode"] == "dispatch_batch_preview"
    assert batch_card["approval_command"] == "agentdeck approval list"
    assert batch_card["count"] == 2
    assert batch_card["ready_count"] == 1
    assert batch_card["blocked_count"] == 1
    assert batch_card["requires_explicit_user"] is True
    assert batch_card["safety"] == "explicit_runtime"
    assert batch_card["blocker"] == "some dispatch targets are blocked"
    assert batch_card["dispatch_ready_command"] == "agentdeck approval dispatch-ready --confirm"
    assert batch_card["controls"] == [
        {
            "kind": "inspect",
            "label": "Inspect approvals",
            "command": "agentdeck approval list",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "dispatch_ready",
            "label": "Dispatch ready approvals",
            "command": "agentdeck approval dispatch-ready --confirm",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
    ]
    assert batch_card["items"][0]["approval_id"] == planner_approval_id
    assert batch_card["items"][0]["agent_id"] == "planner"
    assert batch_card["items"][0]["pane_id"] == "%42"
    assert batch_card["items"][0]["blocker"] is None
    assert batch_card["items"][0]["controls"] == [
        {
            "kind": "inspect",
            "label": "Inspect approval",
            "command": "agentdeck approval list",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "dispatch",
            "label": "Dispatch approval",
            "command": f"agentdeck approval dispatch --approval-id {planner_approval_id}",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
    ]
    assert batch_card["items"][1]["approval_id"] == coder_approval_id
    assert batch_card["items"][1]["agent_id"] == "coder"
    assert batch_card["items"][1]["pane_id"] is None
    assert batch_card["items"][1]["blocker"] == "agent is not spawned: coder"
    assert batch_card["items"][1]["controls"][-1] == {
        "kind": "dispatch",
        "label": "Dispatch approval",
        "command": f"agentdeck approval dispatch --approval-id {coder_approval_id}",
        "safety": "explicit_runtime",
        "enabled": False,
        "blocker": "agent is not spawned: coder",
    }
    assert payload["intent_card"]["embedded_card"] == "dispatch_batch_preview_card"
    assert payload["intent_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect dispatch_batch_preview_card",
        "command": "agentdeck approval list",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Dispatch ready approvals",
        "command": "agentdeck approval dispatch-ready --confirm",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    assert payload["leader_explanation"]["action_kind"] == "approval_dispatch_batch"
    assert payload["leader_explanation"]["recommended_action_id"] == "2 approvals"
    assert payload["leader_explanation"]["action_status"] == "partially_blocked"
    assert payload["leader_explanation"]["safety"] == "explicit_runtime"
    assert payload["leader_explanation"]["requires_explicit_user"] is True

    state_after = StateStore(root).load()
    assert state_after["approvals"][0]["status"] == "approved"
    assert state_after["approvals"][1]["status"] == "approved"
    assert state_after["chat_turns"][0]["mode"] == "approval"
    assert state_after["chat_turns"][0]["next_command"] == "agentdeck approval dispatch-ready --confirm"
    assert state_after["chat_turns"][0]["action_kind"] == "approval_dispatch_batch"
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert state_after.get("inbox", {}) == {}


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
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["leader_actions"]["recommended_action_id"] == payload["leader_action"]["action_id"]
    assert payload["leader_actions"]["items"][0]["action_id"] == payload["leader_action"]["action_id"]
    assert payload["leader_actions"]["items"][0]["is_recommended"] is True
    assert payload["leader_explanation"]["mode"] == "review"
    assert payload["leader_explanation"]["recommended_action_id"] == payload["leader_action"]["action_id"]
    assert payload["leader_explanation"]["action_kind"] == "create_approvals"
    assert payload["leader_explanation"]["action_status"] == "pending"
    assert payload["leader_explanation"]["safety"] == "safe_apply"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert payload["leader_explanation"]["reason"] == payload["review"]["reason"]

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
    assert [item["mode"] for item in payload["turns"]] == ["plan", "continue"]
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
    assert payload["recommended_action_id"] == first["action_id"]
    assert payload["actions"][0]["action_id"] == first["action_id"]
    assert payload["actions"][0]["kind"] == "create_approvals"
    assert payload["actions"][0]["status"] == "pending"
    assert payload["actions"][0]["can_apply"] is True
    assert payload["actions"][0]["preview_command"] == f"agentdeck leader action --action-id {first['action_id']}"
    assert payload["actions"][0]["controls"][0]["command"] == payload["actions"][0]["preview_command"]
    assert payload["actions"][0]["controls"][1]["command"] == payload["actions"][0]["apply_command"]
    assert payload["actions"][0]["apply_command"] == f"agentdeck leader apply-action --action-id {first['action_id']}"
    assert payload["actions"][0]["explicit_command"] == first["command"]
    assert payload["actions"][0]["apply_blocker"] is None
    assert payload["actions"][0]["is_recommended"] is True


def test_leader_actions_refuses_contract_violation(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "坏 actions 队列不能输出"])
    capsys.readouterr()
    cli.main(["leader", "next"])
    capsys.readouterr()

    def broken_validation(_payload):
        return {"ok": False, "errors": ["missing leader action item field: apply_blocker"]}

    monkeypatch.setattr(cli, "validate_leader_actions_contract", broken_validation)

    exit_code = cli.main(["leader", "actions"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Leader actions contract validation failed" in captured.err
    assert "missing leader action item field: apply_blocker" in captured.err
    state = StateStore(root).load()
    assert state["leader_actions"][0]["status"] == "pending"


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
    assert payload["preview_command"] == f"agentdeck leader action --action-id {action_id}"
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


def test_leader_action_show_refuses_contract_violation(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "坏 action 详情不能输出"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["leader", "next", "--plan-id", plan_id])
    action_id = json.loads(capsys.readouterr().out)["action_id"]

    def broken_validation(_payload):
        return {"ok": False, "errors": ["missing leader_action field: recovery"]}

    monkeypatch.setattr(cli, "validate_leader_action_contract", broken_validation)

    exit_code = cli.main(["leader", "action", "--action-id", action_id])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Leader action contract validation failed" in captured.err
    assert "missing leader_action field: recovery" in captured.err
    state = StateStore(root).load()
    assert state["leader_actions"][0]["status"] == "pending"


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
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["leader_actions"]["recommended_action_id"] is None
    assert payload["leader_actions"]["items"][0]["action_id"] == action_id
    assert payload["leader_actions"]["items"][0]["status"] == "applied"
    assert payload["leader_actions"]["items"][0]["is_recommended"] is False
    assert payload["recovery"]["status"] == "approval_required"
    assert payload["recovery"]["recommended_action"]["command"] == "agentdeck approval list"
    assert payload["next_command"] == payload["recovery"]["next_command"]
    assert payload["next_command"] == "agentdeck approval list"
    assert payload["approval_card"]["count"] == 3
    assert [item["agent_id"] for item in payload["approval_card"]["approvals"]] == [
        "planner",
        "coder",
        "reviewer",
    ]
    assert [item["status"] for item in payload["approval_card"]["approvals"]] == [
        "pending",
        "pending",
        "pending",
    ]
    assert payload["approval_card"]["approvals"][0]["preview_command"] == "agentdeck approval list"
    assert payload["approval_card"]["approvals"][0]["approve_command"].startswith("agentdeck approval approve")
    assert payload["approval_card"]["approvals"][0]["can_dispatch"] is False
    assert payload["approval_card"]["approvals"][0]["dispatch_blocker"] == "approval is not approved"
    assert payload["leader_explanation"]["mode"] == "apply_action"
    assert payload["leader_explanation"]["recommended_action_id"] is None
    assert payload["leader_explanation"]["action_kind"] == "create_approvals"
    assert payload["leader_explanation"]["action_status"] == "applied"
    assert payload["leader_explanation"]["safety"] == "safe_apply_completed"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert payload["leader_explanation"]["next_command"] == payload["next_command"]
    assert payload["leader_explanation"]["result_count"] == 3
    assert payload["result"]["count"] == 3

    state = StateStore(root).load()
    assert len(state["approvals"]) == 3
    assert state["leader_actions"][0]["status"] == "applied"
    assert state["chat_turns"][0]["mode"] == "apply_action"
    assert state["chat_turns"][0]["next_command"] == payload["next_command"]
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
    assert payload["plans"][0]["provider_backend"] == "local"
    assert payload["plans"][0]["provider_transport"] == "local"
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
    assert payload["leader_backend"] == {
        "agent_id": "leader",
        "provider": "fake",
        "model": "fake-plan",
        "provider_backend": "local",
        "provider_transport": "local",
        "reasoning_backend": "local-fake",
        "runtime_kind": "logical_leader",
        "pane_backed": False,
        "pane_id": None,
        "approval_required": True,
        "dispatch_ready": False,
    }
    assert payload["approval_id"] == approval_id
    assert payload["agent_id"] == "planner"
    assert payload["reason"] == "approved step is waiting for dispatch"
    assert payload["counts"]["approved"] == 1


def test_leader_review_refuses_contract_violation(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "坏 review 不能输出"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]

    def broken_validation(_payload):
        return {"ok": False, "errors": ["missing leader_review field: next_command"]}

    monkeypatch.setattr(cli, "validate_leader_review_contract", broken_validation)

    exit_code = cli.main(["leader", "review", "--plan-id", plan_id])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Leader review contract validation failed" in captured.err
    assert "missing leader_review field: next_command" in captured.err
    state = StateStore(root).load()
    assert state["approvals"] == []
    assert state["messages"] == []
    assert state["jobs"] == []
    assert state["replies"] == []


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
    assert payload["next_command"] == f"agentdeck capture-reply --agent planner --message-id {message_id}"
    assert payload["controls"] == [
        {
            "kind": "preview",
            "label": "Preview message lineage",
            "command": f"agentdeck trace --id {message_id}",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "capture_reply",
            "label": "Capture reply",
            "command": f"agentdeck capture-reply --agent planner --message-id {message_id}",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
    ]

    state_after = StateStore(root).load()
    assert state_after["replies"] == []
    assert state_after["leader_actions"] == []


def test_leader_review_summarizes_when_all_dispatched_steps_have_replies(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%77")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--task", "review completed"])
    planned = json.loads(capsys.readouterr().out)
    plan_id = planned["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    cli.main(["approval", "dispatch", "--approval-id", approval_id])
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    cli.main(["reply", "--agent", "planner", "--message-id", message_id, "--text", "status: completed\nsummary: done"])
    reply_payload = json.loads(capsys.readouterr().out)
    reply_id = reply_payload["reply_id"]
    assert reply_payload["inbox_card"]["agent_id"] == "leader"
    assert reply_payload["inbox_card"]["count"] == 1
    assert reply_payload["inbox_card"]["items"][0]["event_type"] == "task_reply"
    assert reply_payload["inbox_card"]["items"][0]["reply_id"] == reply_id
    assert reply_payload["inbox_card"]["items"][0]["message_id"] == message_id
    assert reply_payload["inbox_card"]["items"][0]["trace_command"].startswith("agentdeck trace --id inb_")
    assert reply_payload["inbox_card"]["items"][0]["can_ack"] is True

    exit_code = cli.main(["leader", "review", "--plan-id", plan_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["next_action"] == "summarize"
    assert payload["reason"] == "all dispatched steps have replies"
    assert payload["replies"] == [{"agent_id": "planner", "message_id": message_id, "reply_id": reply_id}]
    assert payload["next_command"] == f"agentdeck leader summary --plan-id {plan_id}"
    assert payload["controls"] == [
        {
            "kind": "next",
            "label": "Next command",
            "command": f"agentdeck leader summary --plan-id {plan_id}",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
    ]


def test_leader_summary_returns_replies_and_artifacts_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["plans"] = [
        {
            "plan_id": "pln_summary",
            "task": "总结多 agent 结果",
            "provider": "fake",
            "model": "fake-plan",
            "status": "planned",
            "dispatch_ready": False,
            "created_at": "2026-07-04T00:00:00+00:00",
            "plan": {
                "goal": "总结多 agent 结果",
                "summary": "Need planner output.",
                "steps": [
                    {
                        "step": 1,
                        "agent_id": "planner",
                        "role": "planning",
                        "task": "整理方案",
                        "requires_approval": True,
                    }
                ],
            },
        }
    ]
    state["approvals"] = [
        {
            "approval_id": "apv_summary",
            "plan_id": "pln_summary",
            "step": 1,
            "step_index": 0,
            "agent_id": "planner",
            "role": "planning",
            "task": "整理方案",
            "status": "dispatched",
            "message_id": "msg_summary",
            "attempt_id": "att_summary",
            "job_id": "job_summary",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["messages"] = [
        {
            "message_id": "msg_summary",
            "from_actor": "leader",
            "to_agent": "planner",
            "task": "整理方案",
            "prompt": "prompt",
            "status": "replied",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["attempts"] = [
        {
            "attempt_id": "att_summary",
            "message_id": "msg_summary",
            "agent_id": "planner",
            "status": "completed",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["jobs"] = [
        {
            "job_id": "job_summary",
            "message_id": "msg_summary",
            "attempt_id": "att_summary",
            "agent_id": "planner",
            "pane_id": "%42",
            "status": "completed",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["replies"] = [
        {
            "reply_id": "rep_summary",
            "message_id": "msg_summary",
            "attempt_id": "att_summary",
            "job_id": "job_summary",
            "from_agent": "planner",
            "to_actor": "leader",
            "text": "status: completed\nsummary: planner delivered.\nfull_output_path: docs/summary.md",
            "created_at": "2026-07-04T00:00:01+00:00",
        }
    ]
    state["artifacts"] = [
        {
            "artifact_id": "art_summary",
            "message_id": "msg_summary",
            "attempt_id": "att_summary",
            "job_id": "job_summary",
            "reply_id": "rep_summary",
            "from_agent": "planner",
            "path": "docs/summary.md",
            "kind": "markdown",
            "status": "created",
            "created_at": "2026-07-04T00:00:02+00:00",
        }
    ]
    store.save(state)

    exit_code = cli.main(["leader", "summary", "--plan-id", "pln_summary"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["plan_id"] == "pln_summary"
    assert payload["status"] == "ready"
    assert payload["reply_count"] == 1
    assert payload["artifact_count"] == 1
    assert payload["leader_backend"] == {
        "agent_id": "leader",
        "provider": "fake",
        "model": "fake-plan",
        "provider_backend": "local",
        "provider_transport": "local",
        "reasoning_backend": "local-fake",
        "runtime_kind": "logical_leader",
        "pane_backed": False,
        "pane_id": None,
        "approval_required": True,
        "dispatch_ready": False,
    }
    assert payload["summary"] == "1 dispatched step has replies; 1 artifact recorded."
    assert payload["plan_status_command"] == "agentdeck plan status --plan-id pln_summary"
    assert payload["review_command"] == "agentdeck leader review --plan-id pln_summary"
    assert payload["steps"] == [
        {
            "step": 1,
            "agent_id": "planner",
            "role": "planning",
            "task": "整理方案",
            "approval_id": "apv_summary",
            "message_id": "msg_summary",
            "attempt_id": "att_summary",
            "job_id": "job_summary",
            "reply_id": "rep_summary",
            "reply_text": "status: completed\nsummary: planner delivered.\nfull_output_path: docs/summary.md",
            "artifact_count": 1,
            "artifacts": [
                {
                    "artifact_id": "art_summary",
                    "path": "docs/summary.md",
                    "kind": "markdown",
                    "status": "created",
                    "trace_command": "agentdeck trace --id art_summary",
                }
            ],
            "trace_command": "agentdeck trace --id msg_summary",
        }
    ]
    assert payload["controls"] == [
        {
            "kind": "plan_status",
            "label": "Plan status",
            "command": "agentdeck plan status --plan-id pln_summary",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "review",
            "label": "Review plan",
            "command": "agentdeck leader review --plan-id pln_summary",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "trace",
            "label": "Trace step",
            "command": "agentdeck trace --id msg_summary",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
    ]
    assert StateStore(root).load() == state


def test_leader_chat_summary_intent_embeds_summary_card_without_creating_actions(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%77")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--task", "review completed"])
    planned = json.loads(capsys.readouterr().out)
    plan_id = planned["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_id = json.loads(capsys.readouterr().out)["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    cli.main(["approval", "dispatch", "--approval-id", approval_id])
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    cli.main(
        [
            "reply",
            "--agent",
            "planner",
            "--message-id",
            message_id,
            "--text",
            "status: completed\nsummary: done\nfull_output_path: docs/done.md",
        ]
    )
    reply_payload = json.loads(capsys.readouterr().out)
    inbox_id = reply_payload["inbox_card"]["items"][0]["inbox_id"]
    cli.main(["ack", "--agent", "leader", "--inbox-id", inbox_id])
    capsys.readouterr()
    state_before = StateStore(root).load()
    sent_before = list(fake.sent)
    captured_before = list(fake.captured)

    exit_code = cli.main(["leader", "chat", "--message", "总结当前计划"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "summary"
    assert payload["plan_id"] == plan_id
    assert payload["next_command"] == f"agentdeck leader summary --plan-id {plan_id}"
    assert payload["review"]["next_action"] == "summarize"
    assert payload["leader_summary_card"]["plan_id"] == plan_id
    assert payload["leader_summary_card"]["leader_backend"] == planned["leader_backend"]
    assert payload["leader_summary_card"]["reply_count"] == 1
    assert payload["leader_summary_card"]["artifact_count"] == 1
    assert payload["leader_summary_card"]["steps"][0]["message_id"] == message_id
    assert payload["leader_summary_card"]["steps"][0]["reply_text"].startswith("status: completed")
    assert payload["leader_summary_card"]["steps"][0]["artifacts"][0]["path"] == "docs/done.md"
    assert payload["intent_card"]["embedded_card"] == "leader_summary_card"
    assert payload["intent_card"]["read_only"] is True
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Summarize plan",
        "command": f"agentdeck leader summary --plan-id {plan_id}",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["leader_explanation"]["action_kind"] == "leader_summary"
    assert payload["leader_explanation"]["safety"] == "inspect"
    assert payload["leader_explanation"]["requires_explicit_user"] is False
    assert cli.validate_leader_chat_contract(payload) == {"ok": True, "errors": []}

    state_after = StateStore(root).load()
    assert state_after["leader_actions"] == state_before["leader_actions"]
    assert state_after["approvals"] == state_before["approvals"]
    assert state_after["messages"] == state_before["messages"]
    assert state_after["jobs"] == state_before["jobs"]
    assert state_after["replies"] == state_before["replies"]
    assert state_after["artifacts"] == state_before["artifacts"]
    assert len(state_after["chat_turns"]) == len(state_before["chat_turns"]) + 1
    assert state_after["chat_turns"][-1]["mode"] == "summary"
    assert state_after["chat_turns"][-1]["next_command"] == f"agentdeck leader summary --plan-id {plan_id}"
    assert fake.sent == sent_before
    assert fake.captured == captured_before


def test_leader_summary_refuses_contract_violation(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "坏 summary 不能输出"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    state_before = StateStore(root).load()

    def broken_validation(_payload):
        return {"ok": False, "errors": ["missing leader_summary field: summary"]}

    monkeypatch.setattr(cli, "validate_leader_summary_contract", broken_validation)

    exit_code = cli.main(["leader", "summary", "--plan-id", plan_id])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Leader summary contract validation failed" in captured.err
    assert "missing leader_summary field: summary" in captured.err
    assert StateStore(root).load() == state_before


def test_leader_summary_rejects_unknown_plan_id(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "summary", "--plan-id", "pln_missing"])

    assert exit_code == 1
    assert "unknown plan: pln_missing" in capsys.readouterr().err


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
    assert payload["approvals"][0]["can_dispatch"] is True
    assert payload["approvals"][0]["dispatch_command"] == f"agentdeck approval dispatch --approval-id {first_id}"
    assert payload["approvals"][0]["dispatch_blocker"] is None
    assert payload["approvals"][1]["can_dispatch"] is False
    assert payload["approvals"][1]["dispatch_blocker"] == "approval is not approved"
    assert payload["approvals"][2]["approve_command"].startswith("agentdeck approval approve --approval-id apv_")
    assert payload["approvals"][2]["reject_command"].startswith("agentdeck approval reject --approval-id apv_")

    state = StateStore(root).load()
    assert state["approvals"][0]["status"] == "approved"
    assert state["approvals"][1]["status"] == "rejected"
    assert state["approvals"][1]["reason"] == "范围过大"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "approval_decided"' in events


def test_approval_list_refuses_contract_violation(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "plan", "--task", "坏 approval 队列不能输出"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    capsys.readouterr()

    def broken_validation(_payload):
        return {"ok": False, "errors": ["missing approval item field: dispatch_blocker"]}

    monkeypatch.setattr(cli, "validate_approval_contract", broken_validation)

    exit_code = cli.main(["approval", "list"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Approval queue contract validation failed" in captured.err
    assert "missing approval item field: dispatch_blocker" in captured.err
    state = StateStore(root).load()
    assert len(state["approvals"]) == 3
    assert state["approvals"][0]["status"] == "pending"


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
    assert payload["trace_command"] == f"agentdeck trace --id {payload['message_id']}"
    assert payload["inbox_card"]["agent_id"] == "planner"
    assert payload["inbox_card"]["count"] == 1
    assert payload["inbox_card"]["items"][0]["event_type"] == "task_request"
    assert payload["inbox_card"]["items"][0]["message_id"] == payload["message_id"]
    assert payload["inbox_card"]["items"][0]["trace_command"].startswith("agentdeck trace --id inb_")
    assert payload["inbox_card"]["items"][0]["ack_command"].startswith("agentdeck ack --agent planner")
    assert payload["inbox_card"]["items"][0]["can_ack"] is True
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


def test_approval_dispatch_ready_requires_confirm_and_dispatches_only_ready_items(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%77")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--task", "批量显式派发"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approvals = json.loads(capsys.readouterr().out)["approvals"]
    planner_approval_id = approvals[0]["approval_id"]
    coder_approval_id = approvals[1]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", planner_approval_id])
    capsys.readouterr()
    cli.main(["approval", "approve", "--approval-id", coder_approval_id])
    capsys.readouterr()

    exit_code = cli.main(["approval", "dispatch-ready"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "requires --confirm" in captured.err
    state = StateStore(root).load()
    assert state["approvals"][0]["status"] == "approved"
    assert state["approvals"][1]["status"] == "approved"
    assert state["messages"] == []
    assert state["jobs"] == []
    assert fake.sent == []

    exit_code = cli.main(["approval", "dispatch-ready", "--confirm"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "dispatch_ready"
    assert payload["requires_explicit_user"] is True
    assert payload["safety"] == "explicit_runtime"
    assert payload["dispatched_count"] == 1
    assert payload["blocked_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["results"][0]["approval_id"] == planner_approval_id
    assert payload["results"][0]["status"] == "dispatched"
    assert payload["results"][0]["agent_id"] == "planner"
    assert payload["results"][0]["pane_id"] == "%77"
    assert payload["results"][0]["message_id"].startswith("msg_")
    assert payload["results"][0]["trace_command"] == f"agentdeck trace --id {payload['results'][0]['message_id']}"
    assert payload["results"][0]["blocker"] is None
    assert (
        payload["results"][0]["dispatch_command"]
        == f"agentdeck approval dispatch --approval-id {planner_approval_id}"
    )
    assert payload["results"][1] == {
        "approval_id": coder_approval_id,
        "status": "blocked",
        "agent_id": "coder",
        "pane_id": None,
        "message_id": None,
        "trace_command": None,
        "blocker": "agent is not spawned: coder",
        "dispatch_command": f"agentdeck approval dispatch --approval-id {coder_approval_id}",
    }
    assert fake.sent and fake.sent[0][0] == "%77"

    state = StateStore(root).load()
    assert state["approvals"][0]["status"] == "dispatched"
    assert state["approvals"][1]["status"] == "approved"
    assert state["approvals"][0]["message_id"] == payload["results"][0]["message_id"]
    assert len(state["messages"]) == 1
    assert len(state["jobs"]) == 1
    assert state["messages"][0]["to_agent"] == "planner"
    assert state["inbox"]["planner"][0]["event_type"] == "task_request"
