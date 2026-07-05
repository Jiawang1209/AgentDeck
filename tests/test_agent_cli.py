from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.contracts import (
    AGENT_RUNTIME_AGENT_ITEM_FIELDS,
    AGENT_RUNTIME_CAPTURE_RESPONSE_FIELDS,
    AGENT_RUNTIME_REFRESH_AGENT_FIELDS,
    AGENT_RUNTIME_REFRESH_RESPONSE_FIELDS,
    approval_contract_payload,
    approval_contract_response,
    agent_runtime_contract_payload,
    agent_runtime_contract_response,
    contract_index_response,
    continue_contract_payload,
    continue_contract_response,
    doctor_contract_payload,
    doctor_contract_response,
    events_contract_payload,
    events_contract_response,
    inbox_contract_payload,
    inbox_contract_response,
    leader_actions_contract_payload,
    leader_actions_contract_response,
    leader_action_contract_payload,
    leader_action_contract_response,
    leader_chat_contract_payload,
    leader_chat_contract_response,
    leader_review_contract_payload,
    leader_review_contract_response,
    project_view_contract_payload,
    project_view_contract_response,
    trace_contract_payload,
    trace_contract_response,
    validate_trace_contract,
    validate_project_view_contract,
)
from agentdeck.state import StateStore


class FakeTmuxBackend:
    def __init__(self) -> None:
        self.created_sessions = 0
        self.spawned: list[tuple[str, str]] = []
        self.captured: list[tuple[str, int]] = []
        self.sent: list[tuple[str, str]] = []
        self.killed: list[str] = []
        self.existing_panes: set[str] = set()
        self.checked_panes: list[str] = []

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

    def pane_exists(self, _config, pane_id: str) -> bool:
        self.checked_panes.append(pane_id)
        return pane_id in self.existing_panes


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
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AGENTDECK_LEADER_API_KEY", raising=False)

    exit_code = cli.main(["doctor"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["configured_leader"] == {
        "agent_id": "leader",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "approval_mode": "confirm",
        "ready": False,
        "supported": True,
        "missing_env": ["DEEPSEEK_API_KEY"],
        "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
        "setup_commands": [
            'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
            'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
            'export DEEPSEEK_MODEL="deepseek-chat"',
        ],
    }
    assert payload["deepseek"] == {
        "ok": False,
        "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
    }
    assert payload["openai_compatible"] == {
        "ok": False,
        "detail": "AGENTDECK_LEADER_API_KEY is not set; provider calls are disabled",
    }


def test_doctor_reports_configured_leader_ready_when_env_is_set(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    exit_code = cli.main(["doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["configured_leader"] == {
        "agent_id": "leader",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "approval_mode": "confirm",
        "ready": True,
        "supported": True,
        "missing_env": [],
        "detail": "DEEPSEEK_API_KEY is set",
        "setup_commands": [
            'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
            'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
            'export DEEPSEEK_MODEL="deepseek-chat"',
        ],
    }
    assert payload["deepseek"]["ok"] is True
    assert exit_code == (0 if payload["tmux"]["ok"] else 1)


def test_doctor_configured_leader_never_exposes_real_provider_key(
    tmp_path, monkeypatch, capsys
) -> None:
    prepare_project(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "real-secret-key")

    cli.main(["doctor"])

    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert payload["configured_leader"]["ready"] is True
    assert payload["configured_leader"]["setup_commands"] == [
        'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
        'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
        'export DEEPSEEK_MODEL="deepseek-chat"',
    ]
    assert "real-secret-key" not in rendered


def test_events_lists_recent_event_tail(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    store.append_event(cli.EventRecord.create("first_event", {"index": 1}))
    store.append_event(cli.EventRecord.create("second_event", {"index": 2}))
    store.append_event(cli.EventRecord.create("third_event", {"index": 3}))

    exit_code = cli.main(["events", "--limit", "2"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert payload["limit"] == 2
    assert [item["event_type"] for item in payload["events"]] == ["second_event", "third_event"]
    assert [item["payload"]["index"] for item in payload["events"]] == [2, 3]


def test_events_since_returns_events_after_cursor_with_metadata(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    first = cli.EventRecord.create("first_event", {"index": 1})
    second = cli.EventRecord.create("second_event", {"index": 2})
    third = cli.EventRecord.create("third_event", {"index": 3})
    store.append_event(first)
    store.append_event(second)
    store.append_event(third)

    exit_code = cli.main(["events", "--since", first.event_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert payload["limit"] == 20
    assert payload["since_event_id"] == first.event_id
    assert payload["latest_event_id"] == third.event_id
    assert payload["cursor_found"] is True
    assert [item["event_id"] for item in payload["events"]] == [second.event_id, third.event_id]
    assert [item["payload"]["index"] for item in payload["events"]] == [2, 3]


def test_events_since_missing_cursor_returns_limited_tail_and_marks_cursor_missing(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    store.append_event(cli.EventRecord.create("first_event", {"index": 1}))
    second = cli.EventRecord.create("second_event", {"index": 2})
    store.append_event(second)

    exit_code = cli.main(["events", "--since", "evt_missing", "--limit", "1"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["limit"] == 1
    assert payload["since_event_id"] == "evt_missing"
    assert payload["latest_event_id"] == second.event_id
    assert payload["cursor_found"] is False
    assert [item["event_id"] for item in payload["events"]] == [second.event_id]


def test_events_returns_empty_list_when_log_is_missing(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["events"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"count": 0, "limit": 20, "events": []}


def test_continue_returns_recovery_card_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["leader", "chat", "--provider", "fake", "--model", "fake-plan", "--message", "帮我规划下一步"])
    capsys.readouterr()
    state_before = StateStore(root).load()
    status_before = cli.asdict(StateStore(root).project_view(cli.load_config(root)))

    exit_code = cli.main(["continue"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    recovery = status_before["recovery"]
    recommended_action = recovery["recommended_action"]
    assert payload["ok"] is True
    assert payload["mode"] == "continue"
    assert payload["project_view_schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["project_view_command"] == "agentdeck status"
    assert payload["status"] == recovery["status"]
    assert payload["reason"] == recovery["reason"]
    assert payload["next_command"] == recovery["next_command"]
    assert payload["recommended_action"] == recommended_action
    assert payload["pending"] == recovery["pending"]
    assert payload["leader_action"]["action_id"] == recommended_action["target_id"]
    assert payload["leader_action"]["can_apply"] is True
    assert payload["leader_action"]["apply_command"] == recovery["next_command"]
    assert payload["action_detail_command"] == f"agentdeck leader action --action-id {recommended_action['target_id']}"
    assert StateStore(root).load() == state_before


def test_continue_surfaces_provider_setup_when_configured_leader_is_not_ready(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    state_before = StateStore(root).load()

    exit_code = cli.main(["continue"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "provider_setup_required"
    assert payload["reason"] == "configured Leader provider is not ready: deepseek"
    assert payload["next_command"] == "agentdeck doctor"
    assert payload["recommended_action"] == {
        "label": "Inspect Leader provider setup",
        "command": "agentdeck doctor",
        "safety": "inspect",
        "requires_explicit_user": False,
        "source": "provider_health",
        "target_id": "deepseek",
    }
    assert payload["leader_action"] is None
    assert StateStore(root).load() == state_before


def test_continue_refuses_invalid_project_view_before_printing(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    original_asdict = cli.asdict

    def broken_project_view_asdict(obj):
        payload = original_asdict(obj)
        if obj.__class__.__name__ == "ProjectView":
            payload.pop("recovery", None)
        return payload

    monkeypatch.setattr(cli, "asdict", broken_project_view_asdict)

    exit_code = cli.main(["continue"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "ProjectView contract validation failed" in captured.err
    assert "missing top-level field: recovery" in captured.err


def test_continue_refuses_invalid_continue_card_before_printing(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    original_continue_card = cli._continue_card_payload

    def broken_continue_card(project_view, store):
        payload = original_continue_card(project_view, store)
        payload.pop("next_command", None)
        return payload

    monkeypatch.setattr(cli, "_continue_card_payload", broken_continue_card)

    exit_code = cli.main(["continue"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Continue card contract validation failed" in captured.err
    assert "missing continue_card field: next_command" in captured.err


def test_contract_list_discovers_all_gui_contracts(capsys) -> None:
    exit_code = cli.main(["contract", "list"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = contract_index_response(Path(payload["contract_docs_dir"]))
    assert payload == expected
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["contracts_command"] == "agentdeck contract list"
    assert payload["contract_docs_dir"].endswith("docs/contracts")
    assert payload["count"] == len(payload["contracts"])
    assert [item["name"] for item in payload["contracts"]] == [
        "project-view",
        "continue",
        "doctor",
        "events",
        "workbench",
        "agent-runtime",
        "leader-chat",
        "leader-actions",
        "leader-review",
        "leader-action",
        "approvals",
        "inbox",
        "trace",
    ]
    assert all(item["contract_exists"] for item in payload["contracts"])
    assert payload["contracts"][0]["command"] == "agentdeck contract project-view"
    assert payload["contracts"][0]["example_command"] == "agentdeck contract project-view --example"
    assert payload["contracts"][0]["contract_path"].endswith("docs/contracts/project-view-schema.md")


def test_contract_agent_runtime_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "agent-runtime"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = agent_runtime_contract_payload(Path(payload["contract_path"]))
    assert payload == expected
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["list_command"] == "agentdeck agent list"
    assert payload["spawn_command_template"] == "agentdeck agent spawn --agent <id>"
    assert payload["capture_command_template"] == "agentdeck agent capture --agent <id> --lines 200"
    assert payload["send_command_template"] == "agentdeck agent send --agent <id> --text <text>"
    assert payload["stop_command_template"] == "agentdeck agent stop --agent <id>"
    assert payload["refresh_command"] == "agentdeck agent refresh"
    assert payload["contract_path"].endswith("docs/contracts/agent-runtime-schema.md")
    assert payload["contract_exists"] is True
    assert payload["agent_item_fields"] == list(AGENT_RUNTIME_AGENT_ITEM_FIELDS)
    assert payload["capture_response_fields"] == list(AGENT_RUNTIME_CAPTURE_RESPONSE_FIELDS)
    assert payload["refresh_response_fields"] == list(AGENT_RUNTIME_REFRESH_RESPONSE_FIELDS)
    assert payload["refresh_agent_fields"] == list(AGENT_RUNTIME_REFRESH_AGENT_FIELDS)
    assert payload["workbench_contract"] == "agentdeck contract workbench"


def test_contract_agent_runtime_example_exports_gui_ready_runtime_contract(capsys) -> None:
    exit_code = cli.main(["contract", "agent-runtime", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = agent_runtime_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    assert payload["example"] is True
    example = payload["example_agent_runtime"]
    assert payload["example_agent_item_fields"] == payload["agent_item_fields"]
    assert payload["example_capture_response_fields"] == payload["capture_response_fields"]
    assert payload["example_refresh_response_fields"] == payload["refresh_response_fields"]
    assert payload["example_refresh_agent_fields"] == payload["refresh_agent_fields"]
    assert payload["example_control_fields"] == payload["runtime_control_fields"]
    assert set(example["agents"][0]) == set(payload["agent_item_fields"])
    assert set(example["capture"]) == set(payload["capture_response_fields"])
    assert set(example["refresh"]) == set(payload["refresh_response_fields"])
    assert set(example["refresh"]["agents"][0]) == set(payload["refresh_agent_fields"])
    assert set(example["controls"][0]) == set(payload["runtime_control_fields"])
    assert example["agents"][0]["runtime"]["pane_id"] == "%42"
    assert example["capture"]["output"] == "status: completed\n"


def test_contract_project_view_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "project-view"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = project_view_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["status_command"] == "agentdeck status"
    assert payload["contract_path"].endswith("docs/contracts/project-view-schema.md")
    assert payload["contract_exists"] is True
    assert payload["top_level_fields"] == expected["top_level_fields"]
    assert payload["recovery_fields"] == expected["recovery_fields"]
    assert payload["recovery_pending_fields"] == expected["recovery_pending_fields"]
    assert payload["recommended_action_fields"] == expected["recommended_action_fields"]
    assert payload["leader_actions_fields"] == expected["leader_actions_fields"]
    assert payload["leader_action_item_fields"] == expected["leader_action_item_fields"]
    assert payload["message_item_fields"] == expected["message_item_fields"]
    assert payload["job_item_fields"] == expected["job_item_fields"]
    assert payload["reply_item_fields"] == expected["reply_item_fields"]


def test_contract_project_view_example_exports_gui_ready_status(capsys) -> None:
    exit_code = cli.main(["contract", "project-view", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["example"] is True
    example = payload["example_project_view"]
    assert payload["example_top_level_fields"] == payload["top_level_fields"]
    assert set(payload["example_top_level_fields"]) == set(example)
    assert payload["example_recovery_fields"] == payload["recovery_fields"]
    assert set(payload["example_recovery_fields"]) == set(example["recovery"])
    assert payload["example_recovery_pending_fields"] == payload["recovery_pending_fields"]
    assert set(payload["example_recovery_pending_fields"]) == set(example["recovery"]["pending"])
    assert payload["example_recommended_action_fields"] == payload["recommended_action_fields"]
    assert set(payload["example_recommended_action_fields"]) == set(example["recovery"]["recommended_action"])
    assert example["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert validate_project_view_contract(example) == {"ok": True, "errors": []}
    assert example["runtime_backend"] == "tmux"
    assert example["agents"][0]["runtime"]["pane_id"] == "%1"
    assert example["leader_actions"]["recommended_action_id"] == "act_example"
    assert example["leader_actions"]["items"][0]["can_apply"] is True
    assert example["leader_actions"]["items"][0]["is_recommended"] is True
    assert example["chat_turns"]["items"][0]["action_id"] == "act_example"
    assert example["recovery"]["status"] == "action_required"
    assert example["recovery"]["recommended_action"] == {
        "label": "Apply safe Leader action",
        "command": "agentdeck leader apply-action --action-id act_example",
        "safety": "safe_apply",
        "requires_explicit_user": False,
        "source": "leader_action",
        "target_id": "act_example",
    }


def test_contract_project_view_cli_matches_contract_module(capsys) -> None:
    cli.main(["contract", "project-view", "--example"])

    payload = json.loads(capsys.readouterr().out)
    expected = project_view_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected


def test_contract_leader_chat_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "leader-chat"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = leader_chat_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["chat_command"] == "agentdeck leader chat --message <text>"
    assert payload["contract_path"].endswith("docs/contracts/leader-chat-schema.md")
    assert payload["contract_exists"] is True
    assert payload["response_fields"] == expected["response_fields"]
    assert payload["explanation_fields"] == expected["explanation_fields"]


def test_contract_continue_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "continue"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = continue_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["continue_command"] == "agentdeck continue"
    assert payload["contract_path"].endswith("docs/contracts/continue-card-schema.md")
    assert payload["contract_exists"] is True
    assert payload["continue_card_fields"] == expected["continue_card_fields"]
    assert payload["project_view_contract"] == "agentdeck contract project-view"


def test_contract_continue_example_exports_gui_ready_card(capsys) -> None:
    exit_code = cli.main(["contract", "continue", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = continue_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    example = payload["example_continue_card"]
    assert payload["example_continue_card_fields"] == payload["continue_card_fields"]
    assert set(payload["example_continue_card_fields"]) == set(example)
    assert example["mode"] == "continue"
    assert example["next_command"] == "agentdeck leader apply-action --action-id act_example"


def test_contract_doctor_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "doctor"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = doctor_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["doctor_command"] == "agentdeck doctor"
    assert payload["contract_path"].endswith("docs/contracts/doctor-schema.md")
    assert payload["contract_exists"] is True
    assert payload["response_fields"] == expected["response_fields"]
    assert payload["configured_leader_fields"] == expected["configured_leader_fields"]
    assert payload["provider_check_fields"] == expected["provider_check_fields"]


def test_contract_doctor_example_exports_gui_ready_diagnostics(capsys) -> None:
    exit_code = cli.main(["contract", "doctor", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = doctor_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    example = payload["example_doctor"]
    assert payload["example_response_fields"] == payload["response_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert payload["example_configured_leader_fields"] == payload["configured_leader_fields"]
    assert set(payload["example_configured_leader_fields"]) == set(example["configured_leader"])
    assert payload["example_provider_check_fields"] == payload["provider_check_fields"]
    assert set(payload["example_provider_check_fields"]) == set(example["deepseek"])
    assert example["doctor_command"] == "agentdeck doctor"
    assert example["configured_leader"]["setup_commands"][0].startswith("export DEEPSEEK_API_KEY=")


def test_contract_events_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "events"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = events_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["events_command"] == "agentdeck events"
    assert payload["contract_path"].endswith("docs/contracts/events-schema.md")
    assert payload["contract_exists"] is True
    assert payload["response_fields"] == expected["response_fields"]
    assert payload["cursor_fields"] == expected["cursor_fields"]
    assert payload["event_item_fields"] == expected["event_item_fields"]
    assert payload["project_view_contract"] == "agentdeck contract project-view"
    assert payload["workbench_contract"] == "agentdeck contract workbench"


def test_contract_events_example_exports_gui_ready_timeline(capsys) -> None:
    exit_code = cli.main(["contract", "events", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = events_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    example = payload["example_events"]
    assert payload["example_response_fields"] == payload["response_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert payload["example_event_item_fields"] == payload["event_item_fields"]
    assert set(payload["example_event_item_fields"]) == set(example["events"][0])
    assert example["since_event_id"] == "evt_old"
    assert example["cursor_found"] is True


def test_contract_workbench_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["workbench_command"] == "agentdeck workbench"
    assert payload["contract_path"].endswith("docs/contracts/workbench-schema.md")
    assert payload["contract_exists"] is True
    assert payload["snapshot_fields"] == [
        "ok",
        "mode",
        "schema_version",
        "project_view",
        "leader_actions",
        "leader_card",
        "provider_health",
        "runtime_card",
        "role_card",
        "ledger_card",
        "queue_card",
        "operator_card",
        "audit_card",
        "contracts_card",
        "recovery",
        "next_command",
        "continue_card",
        "active_queue_source",
        "inbox_card",
        "approval_card",
        "leader_action",
        "change_summary",
    ]
    assert payload["leader_card_fields"] == [
        "agent_id",
        "provider",
        "model",
        "approval_mode",
        "api_backed",
        "chat_command",
        "continue_command",
        "actions_command",
        "status_command",
    ]
    assert payload["provider_health_fields"] == [
        "agent_id",
        "provider",
        "model",
        "approval_mode",
        "api_backed",
        "supported",
        "ready",
        "missing_env",
        "detail",
        "doctor_command",
        "doctor_contract",
        "setup_commands",
    ]
    assert payload["runtime_card_fields"] == ["backend", "count", "by_status", "refresh_command", "agents"]
    assert payload["runtime_agent_fields"] == [
        "agent_id",
        "role",
        "provider",
        "workspace_mode",
        "status",
        "pane_id",
        "session_name",
        "cwd",
        "spawn_command",
        "stop_command",
        "capture_command",
        "send_command_template",
        "inbox_command",
        "controls",
    ]
    assert payload["runtime_control_fields"] == [
        "kind",
        "label",
        "command",
        "safety",
        "enabled",
        "blocker",
    ]
    assert payload["role_card_fields"] == ["count", "agents", "assign_command_template"]
    assert payload["role_agent_fields"] == [
        "agent_id",
        "role",
        "provider",
        "workspace_mode",
        "role_prompt",
        "assign_command",
    ]
    assert payload["ledger_card_fields"] == [
        "messages",
        "jobs",
        "replies",
        "inbox",
        "trace_commands",
    ]
    assert payload["queue_card_fields"] == [
        "active_queue_source",
        "next_command",
        "leader_actions",
        "approvals",
        "inbox",
        "refresh_command",
    ]
    assert payload["operator_card_fields"] == [
        "status",
        "reason",
        "label",
        "command",
        "next_command",
        "safety",
        "requires_explicit_user",
        "source",
        "target_id",
        "preview_command",
        "controls",
        "active_queue_source",
        "action_kind",
        "can_apply",
        "apply_command",
        "explicit_command",
        "blocker",
    ]
    assert payload["audit_card_fields"] == [
        "latest_event",
        "recent_events",
        "event_count",
        "events_command",
    ]
    assert payload["contracts_card_fields"] == [
        "contracts_command",
        "contract_index_contract",
        "workbench_contract",
        "agent_runtime_contract",
        "leader_chat_contract",
        "leader_review_contract",
        "project_view_contract",
        "events_contract",
        "doctor_contract",
    ]
    assert payload["change_summary_fields"] == [
        "since_event_id",
        "latest_event_id",
        "has_new_events",
        "new_event_count",
        "new_events",
    ]
    assert payload["project_view_contract"] == "agentdeck contract project-view"
    assert payload["continue_contract"] == "agentdeck contract continue"


def test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state(
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
        }
    }
    state["inbox"] = {
        "planner": [
            {
                "inbox_id": "inb_workbench_head",
                "event_type": "task_request",
                "message_id": "msg_workbench",
                "attempt_id": "att_workbench",
                "job_id": "job_workbench",
                "reply_id": None,
                "from_actor": "leader",
                "to_agent": "planner",
                "task": "展示工作台 inbox",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ]
    }
    state["messages"] = [
        {
            "message_id": "msg_workbench",
            "from_actor": "leader",
            "to_agent": "planner",
            "task": "展示工作台 inbox",
            "prompt": "prompt body",
            "status": "dispatched",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["jobs"] = [
        {
            "job_id": "job_workbench",
            "message_id": "msg_workbench",
            "agent_id": "planner",
            "pane_id": "%42",
            "status": "running",
            "created_at": "2026-07-04T00:00:01+00:00",
        }
    ]
    state["replies"] = [
        {
            "reply_id": "rep_workbench",
            "message_id": "msg_workbench",
            "job_id": "job_workbench",
            "from_agent": "planner",
            "to_actor": "leader",
            "text": "status: completed",
            "created_at": "2026-07-04T00:00:02+00:00",
        }
    ]
    store.save(state)
    store.append_event(cli.EventRecord.create("workbench_first_event", {"index": 1}))
    store.append_event(cli.EventRecord.create("workbench_second_event", {"index": 2}))

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "workbench"
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["project_view"]["recovery"]["status"] == "inbox_pending"
    assert payload["leader_actions"] == payload["project_view"]["leader_actions"]
    assert payload["recovery"] == payload["project_view"]["recovery"]
    assert payload["leader_card"] == {
        "agent_id": "leader",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "approval_mode": "confirm",
        "api_backed": True,
        "chat_command": "agentdeck leader chat --message <text>",
        "continue_command": "agentdeck continue",
        "actions_command": "agentdeck leader actions",
        "status_command": "agentdeck status",
    }
    assert payload["provider_health"] == {
        "agent_id": "leader",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "approval_mode": "confirm",
        "api_backed": True,
        "supported": True,
        "ready": False,
        "missing_env": ["DEEPSEEK_API_KEY"],
        "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
        "doctor_command": "agentdeck doctor",
        "doctor_contract": "agentdeck contract doctor",
        "setup_commands": [
            'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
            'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
            'export DEEPSEEK_MODEL="deepseek-chat"',
        ],
    }
    assert payload["runtime_card"]["backend"] == "tmux"
    assert payload["runtime_card"]["count"] == 3
    assert payload["runtime_card"]["by_status"] == {"configured": 2, "running": 1}
    assert payload["runtime_card"]["refresh_command"] == "agentdeck agent refresh"
    assert payload["role_card"]["count"] == 3
    assert payload["role_card"]["assign_command_template"] == (
        "agentdeck agent assign-role --agent <agent_id> --role <role> --role-prompt <role_prompt>"
    )
    planner_role = payload["role_card"]["agents"][0]
    assert planner_role["agent_id"] == "planner"
    assert planner_role["role"] == "planning"
    assert planner_role["provider"] == "codex"
    assert planner_role["workspace_mode"] == "shared"
    assert "任务拆解" in planner_role["role_prompt"]
    assert planner_role["assign_command"].startswith("agentdeck agent assign-role --agent planner")
    assert "--role planning" in planner_role["assign_command"]
    assert "--role-prompt" in planner_role["assign_command"]
    planner_runtime = payload["runtime_card"]["agents"][0]
    assert planner_runtime["agent_id"] == "planner"
    assert planner_runtime["role"] == "planning"
    assert planner_runtime["provider"] == "codex"
    assert planner_runtime["workspace_mode"] == "shared"
    assert planner_runtime["status"] == "running"
    assert planner_runtime["pane_id"] == "%42"
    assert planner_runtime["session_name"] == "agentdeck"
    assert planner_runtime["cwd"] == str(root)
    assert planner_runtime["spawn_command"] == "agentdeck agent spawn --agent planner"
    assert planner_runtime["stop_command"] == "agentdeck agent stop --agent planner"
    assert planner_runtime["capture_command"] == "agentdeck agent capture --agent planner --lines 200"
    assert planner_runtime["send_command_template"] == "agentdeck agent send --agent planner --text <text>"
    assert planner_runtime["inbox_command"] == "agentdeck inbox --agent planner"
    assert planner_runtime["controls"] == [
        {
            "kind": "capture",
            "label": "Capture pane output",
            "command": "agentdeck agent capture --agent planner --lines 200",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "send",
            "label": "Send input",
            "command": "agentdeck agent send --agent planner --text <text>",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "stop",
            "label": "Stop pane",
            "command": "agentdeck agent stop --agent planner",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "inbox",
            "label": "Open inbox",
            "command": "agentdeck inbox --agent planner",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
    ]
    coder_runtime = payload["runtime_card"]["agents"][1]
    assert coder_runtime["status"] == "configured"
    assert coder_runtime["controls"][0] == {
        "kind": "spawn",
        "label": "Spawn pane",
        "command": "agentdeck agent spawn --agent coder",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    assert coder_runtime["controls"][1]["kind"] == "capture"
    assert coder_runtime["controls"][1]["enabled"] is False
    assert coder_runtime["controls"][1]["blocker"] == "agent is not running"
    assert payload["ledger_card"]["messages"]["count"] == 1
    assert payload["ledger_card"]["messages"]["items"][0]["trace_command"] == "agentdeck trace --id msg_workbench"
    assert payload["ledger_card"]["jobs"]["count"] == 1
    assert payload["ledger_card"]["jobs"]["items"][0]["trace_command"] == "agentdeck trace --id job_workbench"
    assert payload["ledger_card"]["replies"]["count"] == 1
    assert payload["ledger_card"]["replies"]["items"][0]["trace_command"] == "agentdeck trace --id rep_workbench"
    assert payload["ledger_card"]["inbox"]["total"] == 1
    assert payload["ledger_card"]["inbox"]["heads"]["planner"]["inbox_id"] == "inb_workbench_head"
    assert payload["ledger_card"]["trace_commands"] == [
        "agentdeck trace --id msg_workbench",
        "agentdeck trace --id job_workbench",
        "agentdeck trace --id rep_workbench",
        "agentdeck trace --id inb_workbench_head",
    ]
    assert payload["queue_card"] == {
        "active_queue_source": "inbox",
        "next_command": "agentdeck inbox --agent planner",
        "leader_actions": {
            "count": 0,
            "pending": 0,
            "recommended_action_id": None,
            "command": "agentdeck leader actions",
        },
        "approvals": {
            "count": 0,
            "pending": 0,
            "approved": 0,
            "command": "agentdeck approval list",
        },
        "inbox": {
            "total": 1,
            "by_agent": {"planner": 1},
            "command_template": "agentdeck inbox --agent <agent_id>",
        },
        "refresh_command": "agentdeck workbench",
    }
    assert payload["next_command"] == "agentdeck inbox --agent planner"
    assert payload["operator_card"] == {
        "status": "inbox_pending",
        "reason": payload["recovery"]["reason"],
        "label": payload["recovery"]["recommended_action"]["label"],
        "command": "agentdeck inbox --agent planner",
        "next_command": "agentdeck inbox --agent planner",
        "safety": "inspect",
        "requires_explicit_user": False,
        "source": "inbox",
        "target_id": "inb_workbench_head",
        "preview_command": "agentdeck trace --id inb_workbench_head",
        "controls": [
            {
                "kind": "preview",
                "label": "Preview",
                "command": "agentdeck trace --id inb_workbench_head",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "explicit",
                "label": "Run explicit command",
                "command": "agentdeck inbox --agent planner",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
        "active_queue_source": "inbox",
        "action_kind": "inbox",
        "can_apply": False,
        "apply_command": None,
        "explicit_command": "agentdeck inbox --agent planner",
        "blocker": None,
    }
    assert payload["audit_card"]["latest_event"] == payload["recovery"]["latest_event"]
    assert payload["audit_card"]["latest_event"]["event_type"] == "workbench_second_event"
    assert payload["audit_card"]["recent_events"] == payload["recovery"]["recent_events"]
    assert payload["audit_card"]["event_count"] == len(payload["recovery"]["recent_events"])
    assert payload["audit_card"]["events_command"] == "agentdeck events --limit 20"
    assert payload["contracts_card"] == {
        "contracts_command": "agentdeck contract list",
        "contract_index_contract": "docs/contracts/contract-index-schema.md",
        "workbench_contract": "agentdeck contract workbench",
        "agent_runtime_contract": "agentdeck contract agent-runtime",
        "leader_chat_contract": "agentdeck contract leader-chat",
        "leader_review_contract": "agentdeck contract leader-review",
        "project_view_contract": "agentdeck contract project-view",
        "events_contract": "agentdeck contract events",
        "doctor_contract": "agentdeck contract doctor",
    }
    assert payload["continue_card"]["status"] == "inbox_pending"
    assert payload["active_queue_source"] == "inbox"
    assert payload["inbox_card"]["agent_id"] == "planner"
    assert payload["inbox_card"]["head_inbox_id"] == "inb_workbench_head"
    assert payload["approval_card"] is None
    assert payload["leader_action"] is None
    assert payload["change_summary"] == {
        "since_event_id": None,
        "latest_event_id": payload["audit_card"]["latest_event"]["event_id"],
        "has_new_events": False,
        "new_event_count": 0,
        "new_events": [],
    }

    state_after = StateStore(root).load()
    assert state_after["agents"]["planner"]["status"] == "running"
    assert state_after["inbox"]["planner"][0]["status"] == "pending"
    assert state_after["messages"][0]["status"] == "dispatched"
    assert state_after["jobs"][0]["status"] == "running"
    assert state_after["replies"][0]["text"] == "status: completed"
    assert state_after["chat_turns"] == []
    assert state_after["leader_actions"] == []


def test_workbench_surfaces_provider_setup_as_active_operator_source(
    tmp_path, monkeypatch, capsys
) -> None:
    prepare_project(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["recovery"]["status"] == "provider_setup_required"
    assert payload["active_queue_source"] == "provider_health"
    assert payload["queue_card"]["active_queue_source"] == "provider_health"
    assert payload["queue_card"]["next_command"] == "agentdeck doctor"
    assert payload["operator_card"] == {
        "status": "provider_setup_required",
        "reason": "configured Leader provider is not ready: deepseek",
        "label": "Inspect Leader provider setup",
        "command": "agentdeck doctor",
        "next_command": "agentdeck doctor",
        "safety": "inspect",
        "requires_explicit_user": False,
        "source": "provider_health",
        "target_id": "deepseek",
        "preview_command": "agentdeck doctor",
        "controls": [
            {
                "kind": "preview",
                "label": "Preview",
                "command": "agentdeck doctor",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "explicit",
                "label": "Run explicit command",
                "command": "agentdeck doctor",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
        "active_queue_source": "provider_health",
        "action_kind": "provider_health",
        "can_apply": False,
        "apply_command": None,
        "explicit_command": "agentdeck doctor",
        "blocker": None,
    }


def test_workbench_surfaces_stale_runtime_as_active_operator_source(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
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

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["recovery"]["status"] == "runtime_stale"
    assert payload["active_queue_source"] == "runtime"
    assert payload["queue_card"]["active_queue_source"] == "runtime"
    assert payload["operator_card"]["action_kind"] == "runtime"
    assert payload["operator_card"]["command"] == "agentdeck agent refresh"
    assert payload["operator_card"]["preview_command"] == "agentdeck agent refresh"
    assert payload["operator_card"]["controls"][0]["command"] == "agentdeck agent refresh"
    assert payload["operator_card"]["explicit_command"] == "agentdeck agent refresh"


def test_workbench_watch_outputs_jsonl_snapshots_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    before = StateStore(root).load()

    exit_code = cli.main(["workbench", "--watch", "--iterations", "2", "--interval", "0"])

    assert exit_code == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    snapshots = [json.loads(line) for line in lines]
    assert [snapshot["mode"] for snapshot in snapshots] == ["workbench", "workbench"]
    assert [snapshot["ok"] for snapshot in snapshots] == [True, True]
    assert [snapshot["active_queue_source"] for snapshot in snapshots] == ["provider_health", "provider_health"]
    assert snapshots[0]["operator_card"]["controls"][0]["command"] == "agentdeck doctor"
    assert snapshots[1]["operator_card"]["controls"][0]["command"] == "agentdeck doctor"
    assert StateStore(root).load() == before


def test_workbench_since_event_summarizes_new_audit_events_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    first = cli.EventRecord.create("workbench_cursor_first", {"index": 1})
    second = cli.EventRecord.create("workbench_cursor_second", {"index": 2})
    store.append_event(first)
    store.append_event(second)
    before = StateStore(root).load()

    exit_code = cli.main(["workbench", "--since-event", first.event_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["change_summary"] == {
        "since_event_id": first.event_id,
        "latest_event_id": second.event_id,
        "has_new_events": True,
        "new_event_count": 1,
        "new_events": [
            {
                "event_id": second.event_id,
                "event_type": "workbench_cursor_second",
                "created_at": second.created_at,
            }
        ],
    }
    assert StateStore(root).load() == before


def test_workbench_since_latest_event_reports_no_new_events(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    event = cli.EventRecord.create("workbench_cursor_latest", {"index": 1})
    store.append_event(event)

    exit_code = cli.main(["workbench", "--since-event", event.event_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["change_summary"] == {
        "since_event_id": event.event_id,
        "latest_event_id": event.event_id,
        "has_new_events": False,
        "new_event_count": 0,
        "new_events": [],
    }


def test_contract_approvals_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "approvals"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = approval_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["approvals_command"] == "agentdeck approval list"
    assert payload["contract_path"].endswith("docs/contracts/approvals-schema.md")
    assert payload["contract_exists"] is True
    assert payload["queue_fields"] == expected["queue_fields"]
    assert payload["approval_item_fields"] == expected["approval_item_fields"]
    assert payload["project_view_contract"] == "agentdeck contract project-view"


def test_contract_approvals_example_exports_gui_ready_queue(capsys) -> None:
    exit_code = cli.main(["contract", "approvals", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = approval_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    example = payload["example_approval_queue"]
    assert payload["example_queue_fields"] == payload["queue_fields"]
    assert set(payload["example_queue_fields"]) == set(example)
    assert payload["example_approval_item_fields"] == payload["approval_item_fields"]
    assert set(payload["example_approval_item_fields"]) == set(example["approvals"][0])
    assert "preview_command" in payload["approval_item_fields"]
    assert "controls" in payload["approval_item_fields"]
    assert example["approvals"][0]["approve_command"] == "agentdeck approval approve --approval-id apv_pending"
    assert example["approvals"][0]["preview_command"] == "agentdeck approval list"
    assert example["approvals"][0]["controls"][0]["command"] == example["approvals"][0]["preview_command"]
    assert example["approvals"][0]["controls"][1]["command"] == example["approvals"][0]["approve_command"]
    assert example["approvals"][1]["dispatch_command"] == "agentdeck approval dispatch --approval-id apv_approved"


def test_contract_inbox_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "inbox"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = inbox_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["inbox_command"] == "agentdeck inbox --agent <id>"
    assert payload["contract_path"].endswith("docs/contracts/inbox-schema.md")
    assert payload["contract_exists"] is True
    assert payload["queue_fields"] == expected["queue_fields"]
    assert payload["inbox_item_fields"] == expected["inbox_item_fields"]
    assert payload["trace_contract"] == "agentdeck contract trace"


def test_contract_inbox_example_exports_gui_ready_queue(capsys) -> None:
    exit_code = cli.main(["contract", "inbox", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = inbox_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    example = payload["example_inbox"]
    assert payload["example_queue_fields"] == payload["queue_fields"]
    assert set(payload["example_queue_fields"]) == set(example)
    assert payload["example_inbox_item_fields"] == payload["inbox_item_fields"]
    assert set(payload["example_inbox_item_fields"]) == set(example["items"][0])
    assert "preview_command" in payload["inbox_item_fields"]
    assert "controls" in payload["inbox_item_fields"]
    assert example["items"][0]["ack_command"] == "agentdeck ack --agent planner --inbox-id inb_task"
    assert example["items"][0]["trace_command"] == "agentdeck trace --id inb_task"
    assert example["items"][0]["preview_command"] == "agentdeck trace --id inb_task"
    assert example["items"][0]["controls"][0]["command"] == example["items"][0]["preview_command"]
    assert example["items"][0]["controls"][1]["command"] == example["items"][0]["ack_command"]


def test_contract_leader_action_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "leader-action"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = leader_action_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["action_command"] == "agentdeck leader action --action-id <id>"
    assert payload["contract_path"].endswith("docs/contracts/leader-action-schema.md")
    assert payload["contract_exists"] is True
    assert payload["action_fields"] == expected["action_fields"]
    assert payload["project_view_contract"] == "agentdeck contract project-view"


def test_contract_leader_action_example_exports_gui_ready_detail(capsys) -> None:
    exit_code = cli.main(["contract", "leader-action", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = leader_action_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    example = payload["example_leader_action"]
    assert payload["example_action_fields"] == payload["action_fields"]
    assert set(payload["example_action_fields"]) == set(example)
    assert "preview_command" in payload["action_fields"]
    assert example["matches_recommended_action"] is True
    assert example["preview_command"] == "agentdeck leader action --action-id act_example"
    assert example["recovery"]["recommended_action"]["target_id"] == "act_example"


def test_contract_leader_actions_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "leader-actions"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = leader_actions_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["actions_command"] == "agentdeck leader actions"
    assert payload["contract_path"].endswith("docs/contracts/leader-actions-schema.md")
    assert payload["contract_exists"] is True
    assert payload["list_fields"] == expected["list_fields"]
    assert payload["action_item_fields"] == expected["action_item_fields"]
    assert payload["project_view_contract"] == "agentdeck contract project-view"


def test_contract_leader_actions_example_exports_gui_ready_queue(capsys) -> None:
    exit_code = cli.main(["contract", "leader-actions", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = leader_actions_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    example = payload["example_leader_actions"]
    assert payload["example_list_fields"] == payload["list_fields"]
    assert set(payload["example_list_fields"]) == set(example)
    assert payload["example_action_item_fields"] == payload["action_item_fields"]
    assert set(payload["example_action_item_fields"]) == set(example["actions"][0])
    assert "preview_command" in payload["action_item_fields"]
    assert "controls" in payload["action_item_fields"]
    assert example["actions"][0]["preview_command"] == "agentdeck leader action --action-id act_example"
    assert example["actions"][0]["controls"][0]["command"] == example["actions"][0]["preview_command"]
    assert example["actions"][0]["controls"][1]["command"] == example["actions"][0]["apply_command"]
    assert example["actions"][0]["apply_command"] == "agentdeck leader apply-action --action-id act_example"


def test_contract_leader_review_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "leader-review"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = leader_review_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["review_command"] == "agentdeck leader review --plan-id <id>"
    assert payload["contract_path"].endswith("docs/contracts/leader-review-schema.md")
    assert payload["contract_exists"] is True
    assert payload["response_fields"] == expected["response_fields"]
    assert payload["control_fields"] == expected["control_fields"]
    assert payload["project_view_contract"] == "agentdeck contract project-view"


def test_contract_leader_review_example_exports_gui_ready_response(capsys) -> None:
    exit_code = cli.main(["contract", "leader-review", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = leader_review_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    example = payload["example_leader_review"]
    assert payload["example_response_fields"] == payload["response_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert payload["example_control_fields"] == payload["control_fields"]
    assert set(payload["example_control_fields"]) == set(example["controls"][0])
    assert example["next_command"] == "agentdeck capture-reply --agent planner --message-id msg_example"
    assert example["controls"][1]["command"] == example["next_command"]


def test_contract_leader_chat_example_exports_gui_ready_response(capsys) -> None:
    exit_code = cli.main(["contract", "leader-chat", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["example"] is True
    example = payload["example_leader_chat"]
    assert payload["example_response_fields"] == payload["response_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert payload["example_explanation_fields"] == payload["explanation_fields"]
    assert set(payload["example_explanation_fields"]) == set(example["leader_explanation"])
    assert example["leader_explanation"]["safety"] == "safe_apply"
    assert example["leader_explanation"]["recommended_action_id"] == example["leader_action"]["action_id"]
    assert example["leader_actions"] == example["project_view"]["leader_actions"]


def test_contract_leader_chat_cli_matches_contract_module(capsys) -> None:
    cli.main(["contract", "leader-chat", "--example"])

    payload = json.loads(capsys.readouterr().out)
    expected = leader_chat_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected


def test_contract_trace_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "trace"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = trace_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["trace_command"] == "agentdeck trace --id <id>"
    assert payload["contract_path"].endswith("docs/contracts/trace-schema.md")
    assert payload["contract_exists"] is True
    assert payload["top_level_fields"] == expected["top_level_fields"]
    assert payload["message_fields"] == expected["message_fields"]
    assert payload["attempt_fields"] == expected["attempt_fields"]
    assert payload["job_fields"] == expected["job_fields"]
    assert payload["reply_fields"] == expected["reply_fields"]
    assert payload["inbox_item_fields"] == expected["inbox_item_fields"]


def test_contract_trace_example_exports_gui_ready_lineage(capsys) -> None:
    exit_code = cli.main(["contract", "trace", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["example"] is True
    example = payload["example_trace"]
    assert payload["example_top_level_fields"] == payload["top_level_fields"]
    assert set(payload["example_top_level_fields"]) == set(example)
    assert payload["example_message_fields"] == payload["message_fields"]
    assert set(payload["example_message_fields"]) == set(example["message"])
    assert payload["example_attempt_fields"] == payload["attempt_fields"]
    assert set(payload["example_attempt_fields"]) == set(example["attempts"][0])
    assert payload["example_job_fields"] == payload["job_fields"]
    assert set(payload["example_job_fields"]) == set(example["jobs"][0])
    assert payload["example_reply_fields"] == payload["reply_fields"]
    assert set(payload["example_reply_fields"]) == set(example["replies"][0])
    assert payload["example_inbox_item_fields"] == payload["inbox_item_fields"]
    assert set(payload["example_inbox_item_fields"]) == set(example["inbox_items"][0])
    assert validate_trace_contract(example) == {"ok": True, "errors": []}
    assert example["message"]["message_id"] == "msg_example"
    assert example["attempts"][0]["attempt_id"] == "att_example"
    assert example["jobs"][0]["job_id"] == "job_example"
    assert example["replies"][0]["reply_id"] == "rep_example"
    assert {item["event_type"] for item in example["inbox_items"]} == {"task_request", "task_reply"}


def test_contract_trace_cli_matches_contract_module(capsys) -> None:
    cli.main(["contract", "trace", "--example"])

    payload = json.loads(capsys.readouterr().out)
    expected = trace_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected


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
    assert payload["messages"]["items"][0]["trace_command"] == "agentdeck trace --id msg_demo"
    assert payload["jobs"]["by_status"] == {"completed": 1}
    assert payload["jobs"]["items"][0]["trace_command"] == "agentdeck trace --id job_demo"
    assert payload["replies"]["items"][0]["reply_id"] == "rep_demo"
    assert payload["replies"]["items"][0]["trace_command"] == "agentdeck trace --id rep_demo"
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
        "recommended_action_id": "act_demo",
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
                "preview_command": "agentdeck leader action --action-id act_demo",
                "controls": [
                    {
                        "kind": "preview",
                        "label": "Preview Leader action",
                        "command": "agentdeck leader action --action-id act_demo",
                        "safety": "inspect",
                        "enabled": True,
                        "blocker": None,
                    },
                    {
                        "kind": "apply",
                        "label": "Apply safe Leader action",
                        "command": "agentdeck leader apply-action --action-id act_demo",
                        "safety": "safe_apply",
                        "enabled": True,
                        "blocker": None,
                    },
                    {
                        "kind": "explicit",
                        "label": "Run explicit command",
                        "command": "agentdeck approval create-from-plan --plan-id pln_demo",
                        "safety": "explicit_runtime",
                        "enabled": True,
                        "blocker": None,
                    },
                ],
                "can_apply": True,
                "apply_command": "agentdeck leader apply-action --action-id act_demo",
                "explicit_command": "agentdeck approval create-from-plan --plan-id pln_demo",
                "apply_blocker": None,
                "is_recommended": True,
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


def test_status_matches_project_view_contract_for_gui_clients(tmp_path, monkeypatch, capsys) -> None:
    contract_path = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "project-view-schema.md"
    assert contract_path.exists()
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["leader_actions"] = [
        {
            "action_id": "act_contract",
            "kind": "create_approvals",
            "status": "pending",
            "requires_confirmation": True,
            "plan_id": "pln_contract",
            "approval_id": None,
            "agent_id": None,
            "message_id": None,
            "command": "agentdeck approval create-from-plan --plan-id pln_contract",
            "reason": "contract smoke",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    store.save(state)
    store.append_event(cli.EventRecord.create("leader_chat_turn", {"turn_id": "cht_contract"}))

    exit_code = cli.main(["status"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected_top_level = {
        *project_view_contract_payload(contract_path)["top_level_fields"],
    }
    assert expected_top_level <= set(payload)
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    expected_recovery = {
        *project_view_contract_payload(contract_path)["recovery_fields"],
    }
    assert expected_recovery <= set(payload["recovery"])
    expected_action = {*project_view_contract_payload(contract_path)["recommended_action_fields"]}
    assert expected_action <= set(payload["recovery"]["recommended_action"])
    assert payload["recovery"]["recommended_action"]["target_id"] == "act_contract"
    assert validate_project_view_contract(payload) == {"ok": True, "errors": []}


def test_status_recovery_surfaces_leader_errors_when_no_work_is_pending(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["leader_errors"] = [
        {
            "error_id": "err_contract",
            "mode": "plan",
            "provider": "agentdeck-contract",
            "model": None,
            "task": "坏响应",
            "error": "missing leader_explanation field: safety",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    store.save(state)

    exit_code = cli.main(["status"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["leader_errors"]["count"] == 1
    assert payload["recovery"]["status"] == "leader_error"
    assert payload["recovery"]["reason"] == "leader error requires inspection"
    assert payload["recovery"]["next_command"] == "agentdeck status"
    assert payload["recovery"]["recommended_action"] == {
        "label": "Inspect Leader error",
        "command": "agentdeck status",
        "safety": "inspect",
        "requires_explicit_user": False,
        "source": "leader_error",
        "target_id": "err_contract",
    }
    assert payload["recovery"]["pending"]["leader_errors"] == 1
    assert validate_project_view_contract(payload) == {"ok": True, "errors": []}


def test_status_refuses_project_view_contract_violation(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    original_asdict = cli.asdict

    def broken_project_view_asdict(obj):
        payload = original_asdict(obj)
        if obj.__class__.__name__ == "ProjectView":
            payload.pop("recovery", None)
        return payload

    monkeypatch.setattr(cli, "asdict", broken_project_view_asdict)

    exit_code = cli.main(["status"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "ProjectView contract validation failed" in captured.err
    assert "missing top-level field: recovery" in captured.err


def test_status_includes_recovery_summary(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["approvals"] = [
        {"approval_id": "apv_pending", "status": "pending"},
        {"approval_id": "apv_approved", "status": "approved"},
    ]
    state["inbox"] = {
        "planner": [{"inbox_id": "inb_pending", "status": "pending"}],
        "coder": [{"inbox_id": "inb_acked", "status": "acked"}],
    }
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
    store.save(state)
    store.append_event(cli.EventRecord.create("first_event", {"index": 1}))
    store.append_event(cli.EventRecord.create("second_event", {"index": 2}))

    exit_code = cli.main(["status"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["recovery"] == {
        "status": "action_required",
        "reason": "pending leader action: create_approvals",
        "next_command": "agentdeck leader apply-action --action-id act_demo",
        "recommended_action": {
            "label": "Apply safe Leader action",
            "command": "agentdeck leader apply-action --action-id act_demo",
            "safety": "safe_apply",
            "requires_explicit_user": False,
            "source": "leader_action",
            "target_id": "act_demo",
        },
        "pending": {
            "leader_actions": 1,
            "approvals": 1,
            "approved_approvals": 1,
            "inbox_items": 1,
            "leader_errors": 0,
            "runtime_stale": 0,
        },
        "leader_action": {
            "action_id": "act_demo",
            "kind": "create_approvals",
            "command": "agentdeck approval create-from-plan --plan-id pln_demo",
            "can_apply": True,
            "apply_command": "agentdeck leader apply-action --action-id act_demo",
            "apply_blocker": None,
        },
        "latest_event": {
            "event_id": payload["recovery"]["latest_event"]["event_id"],
            "event_type": "second_event",
            "created_at": payload["recovery"]["latest_event"]["created_at"],
        },
        "recent_events": [
            {
                "event_id": payload["recovery"]["recent_events"][0]["event_id"],
                "event_type": "first_event",
                "created_at": payload["recovery"]["recent_events"][0]["created_at"],
            },
            {
                "event_id": payload["recovery"]["recent_events"][1]["event_id"],
                "event_type": "second_event",
                "created_at": payload["recovery"]["recent_events"][1]["created_at"],
            },
        ],
    }


def recovery_for_state(root: Path, state_patch: dict[str, object], capsys) -> dict[str, object]:
    store = StateStore(root)
    state = store.load()
    state.update(state_patch)
    store.save(state)

    exit_code = cli.main(["status"])

    assert exit_code == 0
    return json.loads(capsys.readouterr().out)["recovery"]


def test_status_recovery_matrix_for_gui_actions(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    dispatch_recovery = recovery_for_state(
        root,
        {"approvals": [{"approval_id": "apv_ready", "status": "approved"}]},
        capsys,
    )
    assert dispatch_recovery["status"] == "dispatch_ready"
    assert dispatch_recovery["recommended_action"] == {
        "label": "Dispatch approved task",
        "command": "agentdeck approval dispatch --approval-id apv_ready",
        "safety": "explicit_runtime",
        "requires_explicit_user": True,
        "source": "approval",
        "target_id": "apv_ready",
    }

    approval_recovery = recovery_for_state(
        root,
        {"approvals": [{"approval_id": "apv_review", "status": "pending"}]},
        capsys,
    )
    assert approval_recovery["status"] == "approval_required"
    assert approval_recovery["recommended_action"] == {
        "label": "Review approvals",
        "command": "agentdeck approval list",
        "safety": "inspect",
        "requires_explicit_user": False,
        "source": "approval",
        "target_id": "apv_review",
    }

    inbox_recovery = recovery_for_state(
        root,
        {
            "approvals": [],
            "inbox": {"planner": [{"inbox_id": "inb_head", "to_agent": "planner", "status": "pending"}]},
        },
        capsys,
    )
    assert inbox_recovery["status"] == "inbox_pending"
    assert inbox_recovery["recommended_action"] == {
        "label": "Inspect pending inbox",
        "command": "agentdeck inbox --agent planner",
        "safety": "inspect",
        "requires_explicit_user": False,
        "source": "inbox",
        "target_id": "inb_head",
    }

    stale_recovery = recovery_for_state(
        root,
        {
            "approvals": [],
            "inbox": {},
            "agents": {
                "planner": {
                    "agent_id": "planner",
                    "pane_id": None,
                    "session_name": "agentdeck",
                    "cwd": str(root),
                    "status": "stale",
                }
            },
        },
        capsys,
    )
    assert stale_recovery["status"] == "runtime_stale"
    assert stale_recovery["pending"]["runtime_stale"] == 1
    assert stale_recovery["next_command"] == "agentdeck agent refresh"
    assert stale_recovery["recommended_action"] == {
        "label": "Refresh stale runtime",
        "command": "agentdeck agent refresh",
        "safety": "inspect",
        "requires_explicit_user": False,
        "source": "runtime",
        "target_id": "planner",
    }

    idle_recovery = recovery_for_state(root, {"agents": {}, "inbox": {}}, capsys)
    assert idle_recovery["status"] == "idle"
    assert idle_recovery["recommended_action"] is None


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


def test_agent_refresh_marks_missing_running_pane_as_stale(tmp_path, monkeypatch, capsys) -> None:
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

    exit_code = cli.main(["agent", "refresh"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["stale_count"] == 1
    assert payload["running_count"] == 0
    assert payload["agents"][0] == {
        "agent_id": "planner",
        "previous_status": "running",
        "status": "stale",
        "pane_id": "%42",
        "pane_exists": False,
        "changed": True,
    }
    assert fake.checked_panes == ["%42"]

    state = StateStore(root).load()
    assert state["agents"]["planner"]["pane_id"] is None
    assert state["agents"]["planner"]["status"] == "stale"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "agent_runtime_stale"' in events


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
