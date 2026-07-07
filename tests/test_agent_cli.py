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
    AGENT_RUNTIME_READY_RESPONSE_FIELDS,
    AGENT_RUNTIME_TERMINAL_RESPONSE_FIELDS,
    ARTIFACTS_RESPONSE_FIELDS,
    ARTIFACTS_SUMMARY_FIELDS,
    approval_contract_payload,
    approval_contract_response,
    agent_runtime_contract_payload,
    agent_runtime_contract_response,
    contract_index_response,
    continue_contract_payload,
    continue_contract_response,
    controls_contract_payload,
    controls_contract_response,
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
    leader_status_contract_payload,
    leader_review_contract_payload,
    leader_review_contract_response,
    leader_summary_contract_payload,
    leader_summary_contract_response,
    LEADER_SUMMARY_RESPONSE_FIELDS,
    project_view_contract_payload,
    project_view_contract_response,
    PROJECT_VIEW_ARTIFACT_ITEM_FIELDS,
    run_start_contract_payload,
    run_start_contract_response,
    skills_contract_payload,
    skills_contract_response,
    trace_contract_payload,
    trace_contract_response,
    validate_trace_contract,
    validate_project_view_contract,
    WORKBENCH_PROVIDER_HEALTH_FIELDS,
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


def test_dispatch_prompt_requests_full_output_path_for_artifact_recovery(tmp_path, monkeypatch) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config = cli.load_config(root)

    prompt = cli.build_dispatch_prompt(config.agents[0], "写设计文档")

    assert "请按以下格式返回:" in prompt
    assert "full_output_path:" in prompt


def test_dispatch_injects_loaded_worker_skill_snapshot_into_prompt(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["skills", "load", "--name", "planning", "--agent", "planner", "--purpose", "decompose worker task"])
    cli.main(["skills", "load", "--name", "debugging", "--agent", "coder", "--purpose", "debug only"])
    capsys.readouterr()

    exit_code = cli.main(["dispatch", "--agent", "planner", "--task", "使用 worker skill"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    prompt = fake.sent[0][1]
    assert "已加载技能:" in prompt
    assert "Skill: planning" in prompt
    assert "Load ID:" in prompt
    assert "Purpose: decompose worker task" in prompt
    assert "Break a user goal into role-aware steps." in prompt
    assert "Skill: debugging" not in prompt
    assert "Reproduce the failure" not in prompt
    state = StateStore(root).load()
    assert state["messages"][0]["message_id"] == payload["message_id"]
    assert state["messages"][0]["prompt"] == prompt
    assert state["messages"][0]["prompt_skill_context"]["count"] == 1
    assert state["messages"][0]["prompt_skill_context"]["items"][0]["name"] == "planning"
    assert "content_snapshot" not in state["messages"][0]["prompt_skill_context"]["items"][0]


def test_agent_list_outputs_configured_agents(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["agent", "list"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [agent["agent_id"] for agent in payload["agents"]] == ["planner", "coder", "reviewer"]
    assert payload["agents"][0]["runtime"]["status"] == "configured"


def test_agent_ready_outputs_startup_card_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
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
    state_before = StateStore(root).load()

    exit_code = cli.main(["agent", "ready"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "agent_runtime_ready"
    assert payload["runtime_backend"] == "tmux"
    assert payload["total_count"] == 3
    assert payload["running_count"] == 1
    assert payload["not_running_count"] == 2
    assert payload["all_running"] is False
    assert payload["next_command"] == "agentdeck agent spawn-ready --confirm"
    assert payload["spawn_ready_command"] == "agentdeck agent spawn-ready --confirm"
    assert payload["spawn_commands"] == [
        "agentdeck agent spawn --agent coder",
        "agentdeck agent spawn --agent reviewer",
    ]
    assert payload["refresh_command"] == "agentdeck agent refresh"
    assert payload["dispatch_ready_command"] == "agentdeck approval dispatch-ready --confirm"
    assert payload["controls"] == [
        {
            "kind": "inspect",
            "label": "Inspect readiness",
            "command": "agentdeck agent ready",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "spawn_ready",
            "label": "Spawn ready agents",
            "command": "agentdeck agent spawn-ready --confirm",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "refresh_runtime",
            "label": "Refresh runtime",
            "command": "agentdeck agent refresh",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
    ]
    assert payload["runtime_card"]["by_status"] == {"running": 1, "configured": 2}
    assert payload["runtime_card"]["agents"][0]["agent_id"] == "planner"
    assert payload["runtime_card"]["agents"][0]["status"] == "running"
    assert payload["runtime_card"]["agents"][1]["agent_id"] == "coder"
    assert payload["runtime_card"]["agents"][1]["controls"][0] == {
        "kind": "spawn",
        "label": "Spawn pane",
        "command": "agentdeck agent spawn --agent coder",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    assert StateStore(root).load() == state_before


def test_skills_list_surfaces_builtin_and_project_skills_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    skill_dir = root / ".agentdeck" / "skills" / "release-check"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: release-check\n"
        "description: Verify release readiness.\n"
        "required_tools: pytest, git\n"
        "risk: inspect\n"
        "---\n"
        "# Release Check\n\n"
        "Run tests and inspect git status.\n",
        encoding="utf-8",
    )
    state_before = StateStore(root).load()

    exit_code = cli.main(["skills", "list"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "skills_list"
    assert payload["skill_count"] == len(payload["skills"])
    assert payload["import_command_template"] == "agentdeck skills import --path <SKILL.md>"
    assert payload["controls"] == [
        {
            "kind": "import",
            "label": "Import skill",
            "command": "agentdeck skills import --path <SKILL.md>",
            "safety": "explicit_user",
            "enabled": False,
            "blocker": "requires SKILL.md path",
        }
    ]
    names = [skill["name"] for skill in payload["skills"]]
    assert "planning" in names
    assert "debugging" in names
    assert "code-review" in names
    assert "verification" in names
    project_skill = next(skill for skill in payload["skills"] if skill["name"] == "release-check")
    assert project_skill["source"] == "project"
    assert project_skill["path"].endswith(".agentdeck/skills/release-check/SKILL.md")
    assert project_skill["description"] == "Verify release readiness."
    assert project_skill["required_tools"] == ["pytest", "git"]
    assert project_skill["risk"] == "inspect"
    assert project_skill["content_hash"].startswith("sha256:")
    assert project_skill["load_command"] == "agentdeck skills load --name release-check"
    assert project_skill["controls"] == [
        {
            "kind": "show",
            "label": "Show skill",
            "command": "agentdeck skills show --name release-check",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "load",
            "label": "Load skill",
            "command": "agentdeck skills load --name release-check",
            "safety": "explicit_user",
            "enabled": True,
            "blocker": None,
        },
    ]
    assert StateStore(root).load() == state_before


def test_skills_show_returns_snapshot_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    skill_dir = root / ".agentdeck" / "skills" / "release-check"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: release-check\ndescription: Verify release readiness.\nrisk: inspect\n---\n"
        "# Release Check\n\n"
        "Run tests and inspect git status.\n",
        encoding="utf-8",
    )
    state_before = StateStore(root).load()

    exit_code = cli.main(["skills", "show", "--name", "release-check"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "skill_detail"
    assert payload["skill"]["name"] == "release-check"
    assert payload["skill"]["source"] == "project"
    assert payload["skill"]["content"].startswith("---\nname: release-check")
    assert payload["skill"]["content_hash"].startswith("sha256:")
    assert payload["skill"]["load_command"] == "agentdeck skills load --name release-check"
    assert StateStore(root).load() == state_before


def test_skills_load_records_replayable_snapshot_and_event(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    skill_dir = root / ".agentdeck" / "skills" / "release-check"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: release-check\ndescription: Verify release readiness.\nrisk: inspect\n---\n"
        "# Release Check\n\n"
        "Run tests and inspect git status.\n",
        encoding="utf-8",
    )

    exit_code = cli.main(["skills", "load", "--name", "release-check", "--agent", "leader", "--purpose", "release gate"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "skill_loaded"
    assert payload["agent_id"] == "leader"
    assert payload["purpose"] == "release gate"
    assert payload["skill"]["name"] == "release-check"
    assert payload["skill"]["content_hash"].startswith("sha256:")
    assert payload["skill"]["content_snapshot"].startswith("---\nname: release-check")
    state = StateStore(root).load()
    assert state["skill_loads"] == [
        {
            "load_id": payload["load_id"],
            "agent_id": "leader",
            "purpose": "release gate",
            "name": "release-check",
            "source": "project",
            "path": payload["skill"]["path"],
            "content_hash": payload["skill"]["content_hash"],
            "content_snapshot": payload["skill"]["content_snapshot"],
            "description": "Verify release readiness.",
            "required_tools": [],
            "risk": "inspect",
            "created_at": payload["created_at"],
        }
    ]
    assert StateStore(root).list_events(limit=1)[0]["event_type"] == "skill_loaded"


def test_skills_load_preview_is_read_only_and_surfaces_explicit_command(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    state_before = StateStore(root).load()

    exit_code = cli.main(
        [
            "skills",
            "load-preview",
            "--name",
            "planning",
            "--agent",
            "planner",
            "--purpose",
            "decompose implementation work",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "skill_load_preview"
    assert payload["agent_id"] == "planner"
    assert payload["purpose"] == "decompose implementation work"
    assert payload["load_command"] == (
        "agentdeck skills load --name planning --agent planner --purpose 'decompose implementation work'"
    )
    assert payload["skill"]["name"] == "planning"
    assert payload["skill"]["source"] == "builtin"
    assert payload["skill"]["content_hash"].startswith("sha256:")
    assert payload["controls"] == [
        {
            "kind": "load",
            "label": "Load skill",
            "command": "agentdeck skills load --name planning --agent planner --purpose 'decompose implementation work'",
            "safety": "explicit_user",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "show",
            "label": "Show skill",
            "command": "agentdeck skills show --name planning",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
    ]
    assert StateStore(root).load() == state_before
    assert StateStore(root).list_events(limit=1) == []


def test_skills_suggest_records_pending_skill_suggestion_without_creating_skill(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(
        [
            "skills",
            "suggest",
            "--name",
            "incident-review",
            "--summary",
            "Review incident response evidence.",
            "--rationale",
            "planner repeatedly asked for the same incident review checklist",
            "--source",
            "leader",
            "--agent",
            "reviewer",
            "--from-trace",
            "msg_incident",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "skill_suggested"
    assert payload["suggestion"]["suggestion_id"].startswith("sgs_")
    assert payload["suggestion"]["status"] == "pending"
    assert payload["suggestion"]["name"] == "incident-review"
    assert payload["suggestion"]["summary"] == "Review incident response evidence."
    assert payload["suggestion"]["rationale"] == "planner repeatedly asked for the same incident review checklist"
    assert payload["suggestion"]["source"] == "leader"
    assert payload["suggestion"]["agent_id"] == "reviewer"
    assert payload["suggestion"]["trace_id"] == "msg_incident"
    assert payload["suggestion"]["draft_path"] == ".agentdeck/skills/incident-review/SKILL.md"
    assert payload["suggestion"]["controls"] == [
        {
            "kind": "inspect",
            "label": "List skill suggestions",
            "command": "agentdeck skills suggestions",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        }
    ]
    assert payload["next_command"] == "agentdeck skills suggestions"
    assert not (root / ".agentdeck" / "skills" / "incident-review" / "SKILL.md").exists()
    state = StateStore(root).load()
    assert state["skill_suggestions"] == [payload["suggestion"]]
    latest_event = StateStore(root).list_events(limit=1)[0]
    assert latest_event["event_type"] == "skill_suggested"
    assert latest_event["payload"]["suggestion_id"] == payload["suggestion"]["suggestion_id"]


def test_skills_suggestions_lists_pending_suggestions_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(
        [
            "skills",
            "suggest",
            "--name",
            "incident-review",
            "--summary",
            "Review incident response evidence.",
            "--rationale",
            "repeatable review checklist",
            "--source",
            "human",
        ]
    )
    capsys.readouterr()
    state_before = StateStore(root).load()
    events_before = StateStore(root).list_events(limit=20)

    exit_code = cli.main(["skills", "suggestions"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "skill_suggestions"
    assert payload["count"] == 1
    assert payload["pending_count"] == 1
    assert payload["items"] == state_before["skill_suggestions"]
    assert payload["controls"] == [
        {
            "kind": "suggest",
            "label": "Suggest skill",
            "command": "agentdeck skills suggest --name <name> --summary <summary> --rationale <rationale> --source human",
            "safety": "explicit_user",
            "enabled": False,
            "blocker": "requires suggestion fields",
        }
    ]
    assert StateStore(root).load() == state_before
    assert StateStore(root).list_events(limit=20) == events_before


def test_memory_suggest_records_pending_memory_suggestion_without_writing_memory(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(
        [
            "memory",
            "suggest",
            "--summary",
            "The project prefers approval-gated worker dispatch.",
            "--rationale",
            "Leader repeatedly reminded agents not to auto-dispatch",
            "--source",
            "reviewer",
            "--scope",
            "project",
            "--agent",
            "leader",
            "--from-trace",
            "plan_approval",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "memory_suggested"
    assert payload["suggestion"]["suggestion_id"].startswith("mem_")
    assert payload["suggestion"]["status"] == "pending"
    assert payload["suggestion"]["scope"] == "project"
    assert payload["suggestion"]["summary"] == "The project prefers approval-gated worker dispatch."
    assert payload["suggestion"]["rationale"] == "Leader repeatedly reminded agents not to auto-dispatch"
    assert payload["suggestion"]["source"] == "reviewer"
    assert payload["suggestion"]["agent_id"] == "leader"
    assert payload["suggestion"]["trace_id"] == "plan_approval"
    assert payload["suggestion"]["target"] == ".agentdeck/memory/project.md"
    assert payload["suggestion"]["controls"] == [
        {
            "kind": "inspect",
            "label": "List memory suggestions",
            "command": "agentdeck memory suggestions",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        }
    ]
    assert payload["next_command"] == "agentdeck memory suggestions"
    assert not (root / ".agentdeck" / "memory" / "project.md").exists()
    state = StateStore(root).load()
    assert state["memory_suggestions"] == [payload["suggestion"]]
    latest_event = StateStore(root).list_events(limit=1)[0]
    assert latest_event["event_type"] == "memory_suggested"
    assert latest_event["payload"]["suggestion_id"] == payload["suggestion"]["suggestion_id"]


def test_memory_suggestions_lists_pending_suggestions_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(
        [
            "memory",
            "suggest",
            "--summary",
            "Keep skill loading explicit.",
            "--rationale",
            "prevents hidden prompt mutation",
            "--source",
            "human",
        ]
    )
    capsys.readouterr()
    state_before = StateStore(root).load()
    events_before = StateStore(root).list_events(limit=20)
    suggestion_id = state_before["memory_suggestions"][0]["suggestion_id"]
    expected_items = [
        {
            **state_before["memory_suggestions"][0],
            "controls": [
                {
                    "kind": "inspect",
                    "label": "List memory suggestions",
                    "command": "agentdeck memory suggestions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "apply_preview",
                    "label": "Preview memory apply",
                    "command": f"agentdeck memory apply-preview --suggestion-id {suggestion_id}",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "apply_memory",
                    "label": "Apply memory suggestion",
                    "command": f"agentdeck memory apply --suggestion-id {suggestion_id} --confirm",
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        }
    ]

    exit_code = cli.main(["memory", "suggestions"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "memory_suggestions"
    assert payload["count"] == 1
    assert payload["pending_count"] == 1
    assert payload["apply_preview_command_template"] == "agentdeck memory apply-preview --suggestion-id <id>"
    assert payload["items"] == expected_items
    assert payload["controls"] == [
        {
            "kind": "suggest",
            "label": "Suggest memory",
            "command": "agentdeck memory suggest --summary <summary> --rationale <rationale> --source human",
            "safety": "explicit_user",
            "enabled": False,
            "blocker": "requires suggestion fields",
        },
        {
            "kind": "apply_preview",
            "label": "Preview memory apply",
            "command": "agentdeck memory apply-preview --suggestion-id <id>",
            "safety": "inspect",
            "enabled": False,
            "blocker": "requires suggestion id",
        }
    ]
    assert StateStore(root).load() == state_before
    assert StateStore(root).list_events(limit=20) == events_before


def test_memory_apply_preview_is_read_only_and_surfaces_explicit_future_apply(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(
        [
            "memory",
            "suggest",
            "--summary",
            "Keep approval-gated worker dispatch.",
            "--rationale",
            "project safety preference",
            "--source",
            "reviewer",
            "--scope",
            "project",
            "--agent",
            "leader",
            "--from-trace",
            "msg_memory",
        ]
    )
    suggestion_payload = json.loads(capsys.readouterr().out)
    suggestion_id = suggestion_payload["suggestion"]["suggestion_id"]
    state_before = StateStore(root).load()
    events_before = StateStore(root).list_events(limit=20)

    exit_code = cli.main(["memory", "apply-preview", "--suggestion-id", suggestion_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "memory_apply_preview"
    assert payload["suggestion_id"] == suggestion_id
    assert payload["target"] == ".agentdeck/memory/project.md"
    assert payload["target_exists"] is False
    assert payload["would_create"] is True
    assert payload["would_update_status"] == "applied"
    assert payload["suggestion"] == state_before["memory_suggestions"][0]
    assert payload["proposed_append"] == (
        "- Keep approval-gated worker dispatch.\n"
        "  - rationale: project safety preference\n"
        "  - source: reviewer\n"
        "  - agent_id: leader\n"
        "  - trace_id: msg_memory\n"
        f"  - suggestion_id: {suggestion_id}\n"
    )
    assert payload["apply_command"] == f"agentdeck memory apply --suggestion-id {suggestion_id} --confirm"
    assert payload["controls"] == [
        {
            "kind": "inspect",
            "label": "List memory suggestions",
            "command": "agentdeck memory suggestions",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "apply_memory",
            "label": "Apply memory suggestion",
            "command": f"agentdeck memory apply --suggestion-id {suggestion_id} --confirm",
            "safety": "explicit_user",
            "enabled": True,
            "blocker": None,
        },
    ]
    assert not (root / ".agentdeck" / "memory" / "project.md").exists()
    assert StateStore(root).load() == state_before
    assert StateStore(root).list_events(limit=20) == events_before


def test_memory_apply_preview_rejects_unknown_suggestion_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    state_before = StateStore(root).load()
    events_before = StateStore(root).list_events(limit=20)

    exit_code = cli.main(["memory", "apply-preview", "--suggestion-id", "mem_missing"])

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "unknown memory suggestion: mem_missing"
    assert StateStore(root).load() == state_before
    assert StateStore(root).list_events(limit=20) == events_before


def test_memory_apply_requires_confirm_and_does_not_mutate_without_it(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(
        [
            "memory",
            "suggest",
            "--summary",
            "Keep approval-gated worker dispatch.",
            "--rationale",
            "project safety preference",
            "--source",
            "reviewer",
        ]
    )
    suggestion_id = json.loads(capsys.readouterr().out)["suggestion"]["suggestion_id"]
    state_before = StateStore(root).load()
    events_before = StateStore(root).list_events(limit=20)

    exit_code = cli.main(["memory", "apply", "--suggestion-id", suggestion_id])

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "memory apply requires --confirm"
    assert not (root / ".agentdeck" / "memory" / "project.md").exists()
    assert StateStore(root).load() == state_before
    assert StateStore(root).list_events(limit=20) == events_before


def test_memory_apply_confirm_writes_memory_and_marks_suggestion_applied(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(
        [
            "memory",
            "suggest",
            "--summary",
            "Keep approval-gated worker dispatch.",
            "--rationale",
            "project safety preference",
            "--source",
            "reviewer",
            "--scope",
            "project",
            "--agent",
            "leader",
            "--from-trace",
            "msg_memory",
        ]
    )
    suggestion_payload = json.loads(capsys.readouterr().out)
    suggestion_id = suggestion_payload["suggestion"]["suggestion_id"]
    expected_append = (
        "- Keep approval-gated worker dispatch.\n"
        "  - rationale: project safety preference\n"
        "  - source: reviewer\n"
        "  - agent_id: leader\n"
        "  - trace_id: msg_memory\n"
        f"  - suggestion_id: {suggestion_id}\n"
    )

    exit_code = cli.main(["memory", "apply", "--suggestion-id", suggestion_id, "--confirm"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "memory_applied"
    assert payload["suggestion_id"] == suggestion_id
    assert payload["target"] == ".agentdeck/memory/project.md"
    assert payload["applied_path"] == ".agentdeck/memory/project.md"
    assert payload["appended"] == expected_append
    assert payload["suggestion"]["status"] == "applied"
    assert payload["suggestion"]["applied_path"] == ".agentdeck/memory/project.md"
    assert isinstance(payload["suggestion"]["applied_at"], str)
    memory_path = root / ".agentdeck" / "memory" / "project.md"
    assert memory_path.read_text(encoding="utf-8") == expected_append
    state = StateStore(root).load()
    assert state["memory_suggestions"][0] == payload["suggestion"]
    latest_event = StateStore(root).list_events(limit=1)[0]
    assert latest_event["event_type"] == "memory_applied"
    assert latest_event["payload"]["suggestion_id"] == suggestion_id
    assert latest_event["payload"]["target"] == ".agentdeck/memory/project.md"


def test_memory_apply_rejects_already_applied_suggestion_without_duplicate_write(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(
        [
            "memory",
            "suggest",
            "--summary",
            "Keep approval-gated worker dispatch.",
            "--rationale",
            "project safety preference",
            "--source",
            "human",
        ]
    )
    suggestion_id = json.loads(capsys.readouterr().out)["suggestion"]["suggestion_id"]
    assert cli.main(["memory", "apply", "--suggestion-id", suggestion_id, "--confirm"]) == 0
    capsys.readouterr()
    memory_path = root / ".agentdeck" / "memory" / "project.md"
    text_before = memory_path.read_text(encoding="utf-8")
    state_before = StateStore(root).load()
    events_before = StateStore(root).list_events(limit=20)

    exit_code = cli.main(["memory", "apply", "--suggestion-id", suggestion_id, "--confirm"])

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == f"memory suggestion is not pending: {suggestion_id}"
    assert memory_path.read_text(encoding="utf-8") == text_before
    assert StateStore(root).load() == state_before
    assert StateStore(root).list_events(limit=20) == events_before


def test_leader_chat_skill_suggestions_is_read_only_and_avoids_provider_calls(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cli.main(
        [
            "skills",
            "suggest",
            "--name",
            "incident-review",
            "--summary",
            "Review incident response evidence.",
            "--rationale",
            "repeatable review checklist",
            "--source",
            "leader",
            "--agent",
            "reviewer",
            "--from-trace",
            "msg_incident",
        ]
    )
    capsys.readouterr()
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "chat", "--message", "查看 skill 建议"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "skill_suggestions"
    assert payload["plan_id"] is None
    assert payload["next_command"] == "agentdeck skills suggestions"
    assert payload["skill_suggestions_card"] == {
        "mode": "skill_suggestions",
        "title": "Skill suggestions",
        "summary": "1 pending skill suggestion is waiting for human review.",
        "suggestions_command": "agentdeck skills suggestions",
        "project_view_command": "agentdeck status",
        "count": 1,
        "pending_count": 1,
        "items": state_before["skill_suggestions"],
        "controls": [
            {
                "kind": "inspect",
                "label": "List skill suggestions",
                "command": "agentdeck skills suggestions",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inspect",
                "label": "Open project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    assert payload["intent_card"]["embedded_card"] == "skill_suggestions_card"
    assert payload["intent_card"]["read_only"] is True
    assert payload["intent_card"]["controls"][0]["label"] == "List skill suggestions"
    assert payload["leader_explanation"]["action_kind"] == "skill_suggestions"
    assert payload["leader_explanation"]["safety"] == "inspect"
    state_after = StateStore(root).load()
    assert state_after["skill_suggestions"] == state_before["skill_suggestions"]
    assert state_after["plans"] == []
    assert state_after["leader_errors"] == []
    assert StateStore(root).list_events(limit=1)[0]["event_type"] == "leader_chat_turn"


def test_leader_chat_memory_suggestions_is_read_only_and_avoids_provider_calls(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cli.main(
        [
            "memory",
            "suggest",
            "--summary",
            "Keep approval-gated worker dispatch.",
            "--rationale",
            "project safety preference",
            "--source",
            "reviewer",
            "--scope",
            "project",
            "--agent",
            "leader",
            "--from-trace",
            "msg_memory",
        ]
    )
    capsys.readouterr()
    state_before = StateStore(root).load()
    suggestion_id = state_before["memory_suggestions"][0]["suggestion_id"]
    expected_items = [
        {
            **state_before["memory_suggestions"][0],
            "controls": [
                {
                    "kind": "inspect",
                    "label": "List memory suggestions",
                    "command": "agentdeck memory suggestions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "apply_preview",
                    "label": "Preview memory apply",
                    "command": f"agentdeck memory apply-preview --suggestion-id {suggestion_id}",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "apply_memory",
                    "label": "Apply memory suggestion",
                    "command": f"agentdeck memory apply --suggestion-id {suggestion_id} --confirm",
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        }
    ]

    exit_code = cli.main(["leader", "chat", "--message", "查看 memory 建议"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "memory_suggestions"
    assert payload["plan_id"] is None
    assert payload["next_command"] == "agentdeck memory suggestions"
    assert payload["memory_suggestions_card"] == {
        "mode": "memory_suggestions",
        "title": "Memory suggestions",
        "summary": "1 pending memory suggestion is waiting for human review.",
        "suggestions_command": "agentdeck memory suggestions",
        "apply_preview_command_template": "agentdeck memory apply-preview --suggestion-id <id>",
        "project_view_command": "agentdeck status",
        "count": 1,
        "pending_count": 1,
        "items": expected_items,
        "controls": [
            {
                "kind": "inspect",
                "label": "List memory suggestions",
                "command": "agentdeck memory suggestions",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "apply_preview",
                "label": "Preview memory apply",
                "command": "agentdeck memory apply-preview --suggestion-id <id>",
                "safety": "inspect",
                "enabled": False,
                "blocker": "requires suggestion id",
            },
            {
                "kind": "inspect",
                "label": "Open project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    assert payload["intent_card"]["embedded_card"] == "memory_suggestions_card"
    assert payload["intent_card"]["read_only"] is True
    assert payload["intent_card"]["controls"][0]["label"] == "List memory suggestions"
    assert payload["leader_explanation"]["action_kind"] == "memory_suggestions"
    assert payload["leader_explanation"]["safety"] == "inspect"
    state_after = StateStore(root).load()
    assert state_after["memory_suggestions"] == state_before["memory_suggestions"]
    assert state_after["plans"] == []
    assert state_after["leader_errors"] == []
    assert not (root / ".agentdeck" / "memory" / "project.md").exists()
    assert StateStore(root).list_events(limit=1)[0]["event_type"] == "leader_chat_turn"


def test_skills_import_copies_external_skill_without_loading_it(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    external = tmp_path / "external" / "SKILL.md"
    external.parent.mkdir()
    external.write_text(
        "---\n"
        "name: architecture-review\n"
        "description: Review architecture tradeoffs.\n"
        "required_tools: rg, pytest\n"
        "risk: inspect\n"
        "---\n"
        "# Architecture Review\n\n"
        "Check boundaries, tradeoffs, and verification evidence.\n",
        encoding="utf-8",
    )
    state_before = StateStore(root).load()

    exit_code = cli.main(["skills", "import", "--path", str(external)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "skill_imported"
    assert payload["skill"]["name"] == "architecture-review"
    assert payload["skill"]["source"] == "project"
    assert payload["skill"]["path"].endswith(".agentdeck/skills/architecture-review/SKILL.md")
    assert payload["skill"]["required_tools"] == ["rg", "pytest"]
    assert payload["skill"]["controls"][0]["command"] == "agentdeck skills show --name architecture-review"
    assert payload["skill"]["controls"][1] == {
        "kind": "load",
        "label": "Load skill",
        "command": "agentdeck skills load --name architecture-review",
        "safety": "explicit_user",
        "enabled": True,
        "blocker": None,
    }
    assert payload["show_command"] == "agentdeck skills show --name architecture-review"
    assert payload["load_command"] == "agentdeck skills load --name architecture-review"
    imported = root / ".agentdeck" / "skills" / "architecture-review" / "SKILL.md"
    assert imported.read_text(encoding="utf-8") == external.read_text(encoding="utf-8")
    state_after = StateStore(root).load()
    assert state_after["skill_loads"] == state_before["skill_loads"]
    latest_event = StateStore(root).list_events(limit=1)[0]
    assert latest_event["event_type"] == "skill_imported"
    assert latest_event["payload"]["name"] == "architecture-review"


def test_skills_import_preview_is_read_only_and_surfaces_controls(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    external = tmp_path / "external" / "SKILL.md"
    external.parent.mkdir()
    external.write_text(
        "---\n"
        "name: architecture-review\n"
        "description: Review architecture tradeoffs.\n"
        "required_tools: rg, pytest\n"
        "risk: inspect\n"
        "---\n"
        "# Architecture Review\n\n"
        "Check boundaries, tradeoffs, and verification evidence.\n",
        encoding="utf-8",
    )
    target = root / ".agentdeck" / "skills" / "architecture-review" / "SKILL.md"
    state_before = StateStore(root).load()

    exit_code = cli.main(["skills", "import-preview", "--path", str(external)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "skill_import_preview"
    assert payload["source_path"] == str(external)
    assert payload["project_path"] == str(target)
    assert payload["would_overwrite"] is False
    assert payload["import_command"] == f"agentdeck skills import --path {external}"
    assert payload["force_import_command"] == f"agentdeck skills import --path {external} --force"
    assert payload["skill"]["name"] == "architecture-review"
    assert payload["skill"]["source"] == "project"
    assert payload["skill"]["path"] == str(target)
    assert payload["skill"]["required_tools"] == ["rg", "pytest"]
    assert payload["controls"] == [
        {
            "kind": "import",
            "label": "Import skill",
            "command": f"agentdeck skills import --path {external}",
            "safety": "explicit_user",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "force_import",
            "label": "Force import skill",
            "command": f"agentdeck skills import --path {external} --force",
            "safety": "explicit_user",
            "enabled": False,
            "blocker": "skill does not exist",
        },
        {
            "kind": "show_after_import",
            "label": "Show skill after import",
            "command": "agentdeck skills show --name architecture-review",
            "safety": "inspect",
            "enabled": False,
            "blocker": "skill is not imported yet",
        },
    ]
    assert not target.exists()
    assert StateStore(root).load() == state_before
    assert StateStore(root).list_events(limit=1) == []


def test_skills_import_preview_marks_existing_skill_and_force_control(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    existing_dir = root / ".agentdeck" / "skills" / "architecture-review"
    existing_dir.mkdir(parents=True)
    existing = existing_dir / "SKILL.md"
    existing.write_text(
        "---\nname: architecture-review\ndescription: Existing.\nrisk: inspect\n---\n# Existing\n",
        encoding="utf-8",
    )
    external = tmp_path / "external" / "SKILL.md"
    external.parent.mkdir()
    external.write_text(
        "---\nname: architecture-review\ndescription: New.\nrisk: inspect\n---\n# New\n",
        encoding="utf-8",
    )
    state_before = StateStore(root).load()

    exit_code = cli.main(["skills", "import-preview", "--path", str(external)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["would_overwrite"] is True
    assert payload["controls"][0] == {
        "kind": "import",
        "label": "Import skill",
        "command": f"agentdeck skills import --path {external}",
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "skill already exists",
    }
    assert payload["controls"][1] == {
        "kind": "force_import",
        "label": "Force import skill",
        "command": f"agentdeck skills import --path {external} --force",
        "safety": "explicit_user",
        "enabled": True,
        "blocker": None,
    }
    assert payload["controls"][2] == {
        "kind": "show_after_import",
        "label": "Show existing skill",
        "command": "agentdeck skills show --name architecture-review",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert existing.read_text(encoding="utf-8").endswith("# Existing\n")
    assert StateStore(root).load() == state_before
    assert StateStore(root).list_events(limit=1) == []


def test_skills_import_refuses_to_overwrite_without_force(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    existing_dir = root / ".agentdeck" / "skills" / "architecture-review"
    existing_dir.mkdir(parents=True)
    existing = existing_dir / "SKILL.md"
    existing.write_text(
        "---\nname: architecture-review\ndescription: Existing.\nrisk: inspect\n---\n# Existing\n",
        encoding="utf-8",
    )
    external = tmp_path / "external" / "SKILL.md"
    external.parent.mkdir()
    external.write_text(
        "---\nname: architecture-review\ndescription: New.\nrisk: inspect\n---\n# New\n",
        encoding="utf-8",
    )
    state_before = StateStore(root).load()

    exit_code = cli.main(["skills", "import", "--path", str(external)])

    assert exit_code == 1
    assert "skill already exists: architecture-review" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8").endswith("# Existing\n")
    assert StateStore(root).load() == state_before


def test_skills_import_force_overwrites_project_skill(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    existing_dir = root / ".agentdeck" / "skills" / "architecture-review"
    existing_dir.mkdir(parents=True)
    existing = existing_dir / "SKILL.md"
    existing.write_text(
        "---\nname: architecture-review\ndescription: Existing.\nrisk: inspect\n---\n# Existing\n",
        encoding="utf-8",
    )
    external = tmp_path / "external" / "SKILL.md"
    external.parent.mkdir()
    external.write_text(
        "---\nname: architecture-review\ndescription: New.\nrisk: inspect\n---\n# New\n",
        encoding="utf-8",
    )

    exit_code = cli.main(["skills", "import", "--path", str(external), "--force"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["overwritten"] is True
    assert existing.read_text(encoding="utf-8").endswith("# New\n")


def test_skills_show_unknown_skill_fails_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    state_before = StateStore(root).load()

    exit_code = cli.main(["skills", "show", "--name", "missing"])

    assert exit_code == 1
    assert "unknown skill: missing" in capsys.readouterr().err
    assert StateStore(root).load() == state_before


def test_status_surfaces_loaded_skill_context_for_project_view(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["skills", "load", "--name", "planning", "--agent", "leader", "--purpose", "decompose task"])
    capsys.readouterr()

    exit_code = cli.main(["status"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skills"]["count"] == 1
    assert payload["skills"]["by_agent"] == {"leader": 1}
    assert payload["skills"]["by_source"] == {"builtin": 1}
    assert payload["skills"]["items"][0]["agent_id"] == "leader"
    assert payload["skills"]["items"][0]["purpose"] == "decompose task"
    assert payload["skills"]["items"][0]["name"] == "planning"
    assert payload["skills"]["items"][0]["source"] == "builtin"
    assert payload["skills"]["items"][0]["content_hash"].startswith("sha256:")
    assert payload["skills"]["items"][0]["show_command"] == "agentdeck skills show --name planning"
    assert payload["skills"]["items"][0]["reload_command"] == (
        "agentdeck skills load --name planning --agent leader --purpose 'decompose task'"
    )


def test_leader_chat_skill_context_is_read_only_and_avoids_provider_calls(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cli.main(["skills", "load", "--name", "planning", "--agent", "leader", "--purpose", "decompose task"])
    capsys.readouterr()
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "chat", "--message", "查看已加载技能"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "skill_context"
    assert payload["plan_id"] is None
    assert payload["next_command"] == "agentdeck skills list"
    assert payload["skill_context_card"] == {
        "mode": "skill_context",
        "title": "Loaded skill context",
        "summary": "1 loaded skill is available as replayable context.",
        "skills_command": "agentdeck skills list",
        "project_view_command": "agentdeck status",
        "count": 1,
        "items": payload["project_view"]["skills"]["items"],
        "controls": [
            {
                "kind": "inspect",
                "label": "List skills",
                "command": "agentdeck skills list",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inspect",
                "label": "Open project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    state_after = StateStore(root).load()
    assert state_after["skill_loads"] == state_before["skill_loads"]
    assert state_after["plans"] == []
    assert state_after["leader_errors"] == []
    assert StateStore(root).list_events(limit=1)[0]["event_type"] == "leader_chat_turn"


def test_workbench_surfaces_pending_skill_suggestions_for_gui_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(
        [
            "skills",
            "suggest",
            "--name",
            "incident-review",
            "--summary",
            "Review incident response evidence.",
            "--rationale",
            "repeatable review checklist",
            "--source",
            "leader",
            "--agent",
            "reviewer",
            "--from-trace",
            "msg_incident",
        ]
    )
    capsys.readouterr()
    state_before = StateStore(root).load()
    events_before = StateStore(root).list_events(limit=20)

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skill_suggestions_card"] == {
        "mode": "skill_suggestions",
        "title": "Skill suggestions",
        "summary": "1 pending skill suggestion is waiting for human review.",
        "suggestions_command": "agentdeck skills suggestions",
        "project_view_command": "agentdeck status",
        "count": 1,
        "pending_count": 1,
        "items": state_before["skill_suggestions"],
        "controls": [
            {
                "kind": "inspect",
                "label": "List skill suggestions",
                "command": "agentdeck skills suggestions",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "inspect",
                "label": "Open project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    assert {
        (item["scope"], item["card"], item["kind"], item["command"], item["safety"])
        for item in payload["control_registry"]
    } >= {
        (
            "skills",
            "skill_suggestions_card",
            "inspect",
            "agentdeck skills suggestions",
            "inspect",
        )
    }
    assert StateStore(root).load() == state_before
    assert StateStore(root).list_events(limit=20) == events_before


def test_workbench_surfaces_pending_memory_suggestions_for_gui_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(
        [
            "memory",
            "suggest",
            "--summary",
            "Keep approval-gated worker dispatch.",
            "--rationale",
            "project safety preference",
            "--source",
            "reviewer",
            "--scope",
            "project",
            "--agent",
            "leader",
            "--from-trace",
            "msg_memory",
        ]
    )
    capsys.readouterr()
    state_before = StateStore(root).load()
    events_before = StateStore(root).list_events(limit=20)
    suggestion_id = state_before["memory_suggestions"][0]["suggestion_id"]
    expected_items = [
        {
            **state_before["memory_suggestions"][0],
            "controls": [
                {
                    "kind": "inspect",
                    "label": "List memory suggestions",
                    "command": "agentdeck memory suggestions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "apply_preview",
                    "label": "Preview memory apply",
                    "command": f"agentdeck memory apply-preview --suggestion-id {suggestion_id}",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                },
                {
                    "kind": "apply_memory",
                    "label": "Apply memory suggestion",
                    "command": f"agentdeck memory apply --suggestion-id {suggestion_id} --confirm",
                    "safety": "explicit_user",
                    "enabled": True,
                    "blocker": None,
                },
            ],
        }
    ]

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["memory_suggestions_card"] == {
        "mode": "memory_suggestions",
        "title": "Memory suggestions",
        "summary": "1 pending memory suggestion is waiting for human review.",
        "suggestions_command": "agentdeck memory suggestions",
        "apply_preview_command_template": "agentdeck memory apply-preview --suggestion-id <id>",
        "project_view_command": "agentdeck status",
        "count": 1,
        "pending_count": 1,
        "items": expected_items,
        "controls": [
            {
                "kind": "inspect",
                "label": "List memory suggestions",
                "command": "agentdeck memory suggestions",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "apply_preview",
                "label": "Preview memory apply",
                "command": "agentdeck memory apply-preview --suggestion-id <id>",
                "safety": "inspect",
                "enabled": False,
                "blocker": "requires suggestion id",
            },
            {
                "kind": "inspect",
                "label": "Open project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    assert {
        (item["scope"], item["card"], item["kind"], item["command"], item["safety"])
        for item in payload["control_registry"]
    } >= {
        (
            "memory",
            "memory_suggestions_card",
            "inspect",
            "agentdeck memory suggestions",
            "inspect",
        ),
        (
            "memory",
            "memory_suggestions_card",
            "apply_preview",
            "agentdeck memory apply-preview --suggestion-id <id>",
            "inspect",
        ),
    }
    assert StateStore(root).load() == state_before
    assert StateStore(root).list_events(limit=20) == events_before
    assert not (root / ".agentdeck" / "memory" / "project.md").exists()


def test_leader_chat_previews_external_skill_import_without_mutating_registry(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    external = tmp_path / "external" / "SKILL.md"
    external.parent.mkdir()
    external.write_text(
        "---\n"
        "name: architecture-review\n"
        "description: Review architecture tradeoffs.\n"
        "required_tools: rg, pytest\n"
        "risk: inspect\n"
        "---\n"
        "# Architecture Review\n\n"
        "Check boundaries, tradeoffs, and verification evidence.\n",
        encoding="utf-8",
    )
    target = root / ".agentdeck" / "skills" / "architecture-review" / "SKILL.md"
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "chat", "--message", f"预览导入 skill {external}"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "skill_import_preview"
    assert payload["plan_id"] is None
    assert payload["next_command"] == f"agentdeck skills import --path {external}"
    assert payload["skill_import_preview_card"] == {
        "ok": True,
        "mode": "skill_import_preview",
        "title": "External skill import preview",
        "summary": "architecture-review can be imported without overwriting an existing project skill.",
        "skill": payload["skill_import_preview_card"]["skill"],
        "source_path": str(external),
        "project_path": str(target),
        "would_overwrite": False,
        "import_command": f"agentdeck skills import --path {external}",
        "force_import_command": f"agentdeck skills import --path {external} --force",
        "controls": [
            {
                "kind": "import",
                "label": "Import skill",
                "command": f"agentdeck skills import --path {external}",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "force_import",
                "label": "Force import skill",
                "command": f"agentdeck skills import --path {external} --force",
                "safety": "explicit_user",
                "enabled": False,
                "blocker": "skill does not exist",
            },
            {
                "kind": "show_after_import",
                "label": "Show skill after import",
                "command": "agentdeck skills show --name architecture-review",
                "safety": "inspect",
                "enabled": False,
                "blocker": "skill is not imported yet",
            },
        ],
    }
    assert payload["skill_import_preview_card"]["skill"]["name"] == "architecture-review"
    assert payload["skill_import_preview_card"]["skill"]["required_tools"] == ["rg", "pytest"]
    assert payload["intent_card"]["embedded_card"] == "skill_import_preview_card"
    assert payload["intent_card"]["requires_explicit_user"] is True
    assert payload["intent_card"]["read_only"] is True
    assert payload["intent_card"]["controls"][-1] == {
        "kind": "next",
        "label": "Import skill",
        "command": f"agentdeck skills import --path {external}",
        "safety": "explicit_user",
        "enabled": True,
        "blocker": None,
    }
    assert payload["leader_explanation"]["action_kind"] == "skill_import_preview"
    assert payload["leader_explanation"]["safety"] == "explicit_user"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    assert not target.exists()
    state_after = StateStore(root).load()
    assert state_after["skill_loads"] == state_before["skill_loads"]
    assert state_after["plans"] == []
    assert state_after["leader_errors"] == []
    assert StateStore(root).list_events(limit=1)[0]["event_type"] == "leader_chat_turn"


def test_leader_chat_previews_skill_load_without_mutating_context(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    state_before = StateStore(root).load()

    exit_code = cli.main(
        [
            "leader",
            "chat",
            "--message",
            "预览加载 skill planning 给 planner 用于 decompose implementation work",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "skill_load_preview"
    assert payload["plan_id"] is None
    assert payload["next_command"] == (
        "agentdeck skills load --name planning --agent planner --purpose 'decompose implementation work'"
    )
    assert payload["skill_load_preview_card"] == {
        "ok": True,
        "mode": "skill_load_preview",
        "title": "Skill load preview",
        "summary": "planning can be loaded for planner as replayable context.",
        "agent_id": "planner",
        "purpose": "decompose implementation work",
        "skill": payload["skill_load_preview_card"]["skill"],
        "load_command": "agentdeck skills load --name planning --agent planner --purpose 'decompose implementation work'",
        "controls": [
            {
                "kind": "load",
                "label": "Load skill",
                "command": "agentdeck skills load --name planning --agent planner --purpose 'decompose implementation work'",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "show",
                "label": "Show skill",
                "command": "agentdeck skills show --name planning",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    assert payload["skill_load_preview_card"]["skill"]["name"] == "planning"
    assert payload["intent_card"]["embedded_card"] == "skill_load_preview_card"
    assert payload["intent_card"]["requires_explicit_user"] is True
    assert payload["intent_card"]["read_only"] is True
    assert payload["leader_explanation"]["action_kind"] == "skill_load_preview"
    assert payload["leader_explanation"]["safety"] == "explicit_user"
    assert payload["leader_explanation"]["requires_explicit_user"] is True
    state_after = StateStore(root).load()
    assert state_after["skill_loads"] == state_before["skill_loads"]
    assert state_after["plans"] == []
    assert state_after["leader_errors"] == []
    assert StateStore(root).list_events(limit=1)[0]["event_type"] == "leader_chat_turn"


def test_doctor_reports_openai_compatible_provider_state(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AGENTDECK_LEADER_API_KEY", raising=False)
    monkeypatch.setattr(
        cli,
        "_command_path",
        lambda command: "/opt/bin/codex" if command == "codex" else None,
    )

    exit_code = cli.main(["doctor"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["configured_leader"] == {
        "agent_id": "leader",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "approval_mode": "confirm",
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
        "ready": False,
        "supported": True,
        "missing_env": ["DEEPSEEK_API_KEY"],
        "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
        "command_path": None,
        "setup_commands": [
            'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
            'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
            'export DEEPSEEK_MODEL="deepseek-chat"',
        ],
    }
    assert payload["deepseek"] == {
        "ok": False,
        "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
        "provider_backend": "api",
        "provider_transport": "http",
        "command_path": None,
        "setup_commands": [
            'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
            'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
            'export DEEPSEEK_MODEL="deepseek-chat"',
        ],
    }
    assert payload["openai_compatible"] == {
        "ok": False,
        "detail": "AGENTDECK_LEADER_API_KEY is not set; provider calls are disabled",
        "provider_backend": "api",
        "provider_transport": "http",
        "command_path": None,
        "setup_commands": [
            'export AGENTDECK_LEADER_API_KEY="<your-provider-api-key>"',
            'export AGENTDECK_LEADER_BASE_URL="https://api.example.com/v1"',
            'export AGENTDECK_LEADER_MODEL="<model-name>"',
        ],
    }
    assert payload["codex_cli"] == {
        "ok": True,
        "detail": "codex is available",
        "provider_backend": "cli",
        "provider_transport": "subprocess",
        "command_path": "/opt/bin/codex",
        "setup_commands": ['codex login', 'codex doctor'],
    }
    assert payload["claude_cli"] == {
        "ok": False,
        "detail": "claude is not found on PATH",
        "provider_backend": "cli",
        "provider_transport": "subprocess",
        "command_path": None,
        "setup_commands": ['claude auth', 'claude doctor'],
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
        "ready": True,
        "supported": True,
        "missing_env": [],
        "detail": "DEEPSEEK_API_KEY is set",
        "command_path": None,
        "setup_commands": [
            'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
            'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
            'export DEEPSEEK_MODEL="deepseek-chat"',
        ],
    }
    assert payload["deepseek"]["ok"] is True
    assert payload["deepseek"]["command_path"] is None
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


def test_doctor_reports_codex_cli_leader_ready_from_local_command(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_path = root / ".agentdeck" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace('provider = "deepseek"', 'provider = "codex-cli"', 1)
    config_text = config_text.replace('model = "deepseek-chat"', 'model = "codex-default"', 1)
    config_path.write_text(config_text, encoding="utf-8")
    monkeypatch.setattr(cli, "_command_path", lambda command: "/opt/bin/codex" if command == "codex" else None)

    exit_code = cli.main(["doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["configured_leader"] == {
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
        "ready": True,
        "supported": True,
        "missing_env": [],
        "detail": "codex is available",
        "command_path": "/opt/bin/codex",
        "setup_commands": ['codex login', 'codex doctor'],
    }
    assert exit_code == (0 if payload["tmux"]["ok"] else 1)


def test_leader_set_provider_updates_default_leader_config_and_records_event(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_command_path", lambda command: "/opt/bin/codex" if command == "codex" else None)

    exit_code = cli.main(
        [
            "leader",
            "set-provider",
            "--provider",
            "codex-cli",
            "--model",
            "codex-default",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "agent_id": "leader",
        "provider": "codex-cli",
        "model": "codex-default",
        "approval_mode": "confirm",
        "ready": True,
        "supported": True,
        "missing_env": [],
        "detail": "codex is available",
        "command_path": "/opt/bin/codex",
        "setup_commands": ['codex login', 'codex doctor'],
        "config_path": str(root / ".agentdeck" / "config.toml"),
        "doctor_command": "agentdeck doctor",
        "workbench_command": "agentdeck workbench",
    }
    config_text = (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8")
    assert 'provider = "codex-cli"' in config_text
    assert 'model = "codex-default"' in config_text
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_provider_updated"' in events
    assert '"provider": "codex-cli"' in events
    assert '"model": "codex-default"' in events


def test_leader_set_provider_require_ready_rejects_missing_cli_without_mutating_config(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_before = (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8")
    monkeypatch.setattr(cli, "_command_path", lambda command: None)

    exit_code = cli.main(
        [
            "leader",
            "set-provider",
            "--provider",
            "claude-cli",
            "--model",
            "claude-default",
            "--require-ready",
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "leader provider is not ready: claude is not found on PATH" in captured.err
    assert (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8") == config_before
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "leader_provider_update_rejected"' in events
    assert '"provider": "claude-cli"' in events
    assert '"reason": "provider_not_ready"' in events


def test_leader_set_provider_rejects_unknown_provider_without_mutating_config(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_before = (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8")

    exit_code = cli.main(["leader", "set-provider", "--provider", "unknown"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unsupported leader provider: unknown" in captured.err
    assert (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8") == config_before


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


def test_continue_surfaces_cli_provider_setup_when_command_is_missing(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_path = root / ".agentdeck" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace('provider = "deepseek"', 'provider = "claude-cli"', 1)
    config_text = config_text.replace('model = "deepseek-chat"', 'model = "claude-default"', 1)
    config_path.write_text(config_text, encoding="utf-8")
    monkeypatch.setattr("agentdeck.state.shutil.which", lambda _command: None)
    state_before = StateStore(root).load()

    exit_code = cli.main(["continue"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "provider_setup_required"
    assert payload["reason"] == "configured Leader provider is not ready: claude-cli"
    assert payload["next_command"] == "agentdeck doctor"
    assert payload["recommended_action"]["source"] == "provider_health"
    assert payload["recommended_action"]["target_id"] == "claude-cli"
    assert payload["leader_action"] is None
    assert StateStore(root).load() == state_before


def test_leader_status_surfaces_provider_and_queue_snapshot_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    store = StateStore(root)
    state = store.load()
    state["plans"].append(
        {
            "plan_id": "pln_status",
            "task": "构建 Leader 状态卡",
            "provider": "fake",
            "model": "fake-plan",
            "status": "planned",
            "dispatch_ready": False,
            "created_at": "2026-07-07T00:00:00+00:00",
            "plan": {
                "steps": [
                    {
                        "step": 1,
                        "agent_id": "planner",
                        "role": "planning",
                        "task": "设计状态卡",
                        "risk": "needs review",
                        "requires_approval": True,
                    }
                ]
            },
        }
    )
    state["approvals"].append(
        {
            "approval_id": "apv_status",
            "plan_id": "pln_status",
            "step": 1,
            "agent_id": "planner",
            "role": "planning",
            "task": "设计状态卡",
            "risk": "needs review",
            "status": "pending",
            "created_at": "2026-07-07T00:00:00+00:00",
        }
    )
    state["inbox"] = {
        "leader": [
            {
                "inbox_id": "inb_leader_status",
                "event_type": "task_reply",
                "message_id": "msg_status",
                "reply_id": "rep_status",
                "from_agent": "planner",
                "to_agent": "leader",
                "task": "状态卡建议",
                "status": "pending",
                "created_at": "2026-07-07T00:00:01+00:00",
            }
        ]
    }
    store.save(state)
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "status"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "leader_status"
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["source_command"] == "agentdeck leader status"
    assert payload["refresh_command"] == "agentdeck leader status"
    assert payload["project_view_command"] == "agentdeck status"
    assert payload["workbench_command"] == "agentdeck workbench"
    assert payload["provider_health"]["provider"] == "deepseek"
    assert payload["provider_health"]["ready"] is False
    assert payload["provider_health"]["missing_env"] == ["DEEPSEEK_API_KEY"]
    assert payload["latest_plan"]["plan_id"] == "pln_status"
    assert payload["latest_plan"]["step_count"] == 1
    assert payload["queues"] == {
        "leader_actions_pending": 0,
        "approvals_pending": 1,
        "approvals_approved": 0,
        "leader_inbox_pending": 1,
        "leader_errors": 0,
    }
    assert payload["recovery"]["status"] == "approval_required"
    assert payload["next_command"] == payload["recovery"]["next_command"]
    assert payload["controls"] == [
        {
            "kind": "refresh",
            "label": "Refresh Leader status",
            "command": payload["refresh_command"],
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "inspect",
            "label": "Open project status",
            "command": "agentdeck status",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "inspect",
            "label": "Open workbench",
            "command": "agentdeck workbench",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "inspect",
            "label": "Inspect provider setup",
            "command": "agentdeck doctor",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "next",
            "label": "Continue",
            "command": payload["next_command"],
            "safety": payload["recovery"]["recommended_action"]["safety"],
            "enabled": True,
            "blocker": None,
        },
    ]
    assert StateStore(root).load() == state_before


def test_leader_status_handles_empty_project_without_provider_or_runtime_calls(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "leader_provider", lambda _name: (_ for _ in ()).throw(AssertionError("no provider call")))
    state_before = StateStore(root).load()

    exit_code = cli.main(["leader", "status"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["latest_plan"] is None
    assert payload["queues"]["approvals_pending"] == 0
    assert payload["next_command"] == "agentdeck doctor"
    assert payload["provider_health"]["provider_backend"] == "api"
    assert payload["provider_health"]["provider_transport"] == "http"
    assert StateStore(root).load() == state_before


def test_continue_promotes_multiple_approved_approvals_to_dispatch_ready(
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
    state_before = StateStore(root).load()

    exit_code = cli.main(["continue"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dispatch_ready"
    assert payload["next_command"] == "agentdeck approval dispatch-ready --confirm"
    assert payload["recommended_action"] == {
        "label": "Dispatch ready approvals",
        "command": "agentdeck approval dispatch-ready --confirm",
        "safety": "explicit_runtime",
        "requires_explicit_user": True,
        "source": "approval",
        "target_id": "dispatch_ready",
    }
    assert payload["pending"]["approved_approvals"] == 2
    assert payload["leader_action"] is None
    assert payload["action_detail_command"] is None
    assert StateStore(root).load() == state_before


def test_continue_surfaces_dispatched_step_waiting_for_reply(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--provider", "fake", "--model", "fake-plan", "--task", "等待 worker 回复"])
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    cli.main(["approval", "create-from-plan", "--plan-id", plan_id])
    approval_payload = json.loads(capsys.readouterr().out)
    approvals = approval_payload["approvals"]
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

    exit_code = cli.main(["continue"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "reply_waiting"
    assert payload["reason"] == "dispatched step has no reply yet"
    assert payload["next_command"] == expected_command
    assert payload["recommended_action"] == {
        "label": "Capture pending reply",
        "command": expected_command,
        "safety": "explicit_runtime",
        "requires_explicit_user": True,
        "source": "reply",
        "target_id": message_id,
    }
    assert payload["leader_action"] is None
    assert payload["action_detail_command"] is None
    assert StateStore(root).load() == state_before
    assert fake.sent
    assert fake.captured == []


def test_workbench_surfaces_capture_reply_operator_for_dispatched_step_waiting_for_reply(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--provider", "fake", "--model", "fake-plan", "--task", "等待 workbench 回复"])
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

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["recovery"]["status"] == "reply_waiting"
    assert payload["active_queue_source"] == "reply"
    assert payload["next_command"] == expected_command
    assert payload["operator_card"]["action_kind"] == "reply"
    assert payload["operator_card"]["status"] == "reply_waiting"
    assert payload["operator_card"]["command"] == expected_command
    assert payload["operator_card"]["preview_command"] == f"agentdeck trace --id {message_id}"
    assert payload["operator_card"]["explicit_command"] == expected_command
    assert payload["operator_card"]["controls"][-1] == {
        "kind": "capture_reply",
        "label": "Capture reply",
        "command": expected_command,
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    registry_item = next(
        item
        for item in payload["control_registry"]
        if item["scope"] == "operator" and item["command"] == expected_command
    )
    assert registry_item["kind"] == "capture_reply"
    assert registry_item["card"] == "operator_card"
    assert StateStore(root).load() == state_before
    assert fake.captured == []


def test_capture_reply_records_full_output_path_as_artifact(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    store = StateStore(root)
    state = store.load()
    state["agents"]["planner"] = {
        "agent_id": "planner",
        "pane_id": "%42",
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    state["messages"] = [
        {
            "message_id": "msg_artifact",
            "from_actor": "leader",
            "to_agent": "planner",
            "task": "写设计文档",
            "prompt": "prompt",
            "status": "dispatched",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["attempts"] = [
        {
            "attempt_id": "att_artifact",
            "message_id": "msg_artifact",
            "agent_id": "planner",
            "status": "dispatched",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["jobs"] = [
        {
            "job_id": "job_artifact",
            "message_id": "msg_artifact",
            "attempt_id": "att_artifact",
            "agent_id": "planner",
            "pane_id": "%42",
            "status": "dispatched",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    store.save(state)

    def capture_output(_config, pane_id: str, lines: int = 200) -> str:
        fake.captured.append((pane_id, lines))
        return "\n".join(
            [
                "older output",
                "status: completed",
                "summary: 设计文档已完成",
                "full_output_path: docs/architecture/agent-plan.md",
                "verification: pytest -q",
            ]
        )

    fake.capture_output = capture_output

    exit_code = cli.main(["capture-reply", "--agent", "planner", "--message-id", "msg_artifact"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["message_id"] == "msg_artifact"
    assert payload["artifacts"]["count"] == 1
    assert payload["artifacts"]["items"][0]["path"] == "docs/architecture/agent-plan.md"
    assert payload["artifacts"]["items"][0]["kind"] == "markdown"
    assert payload["artifacts"]["items"][0]["trace_command"] == "agentdeck trace --id msg_artifact"
    state_after = StateStore(root).load()
    assert state_after["artifacts"][0]["message_id"] == "msg_artifact"
    assert state_after["artifacts"][0]["job_id"] == "job_artifact"
    assert state_after["artifacts"][0]["reply_id"] == payload["reply_id"]
    assert state_after["artifacts"][0]["from_agent"] == "planner"
    assert state_after["artifacts"][0]["path"] == "docs/architecture/agent-plan.md"
    assert state_after["artifacts"][0]["kind"] == "markdown"
    assert state_after["artifacts"][0]["status"] == "created"


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
        "run",
        "workbench",
        "controls",
        "skills",
        "agent-runtime",
        "leader-chat",
        "leader-status",
        "leader-actions",
        "leader-review",
        "leader-summary",
        "leader-action",
        "approvals",
        "inbox",
        "trace",
        "artifacts",
    ]
    assert all(item["contract_exists"] for item in payload["contracts"])
    assert payload["contracts"][0]["command"] == "agentdeck contract project-view"
    assert payload["contracts"][0]["example_command"] == "agentdeck contract project-view --example"
    assert payload["contracts"][0]["contract_path"].endswith("docs/contracts/project-view-schema.md")


def test_contract_skills_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "skills"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = skills_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["skills_list_command"] == "agentdeck skills list"
    assert payload["skills_import_preview_command_template"] == "agentdeck skills import-preview --path <SKILL.md>"
    assert payload["skills_import_command_template"] == "agentdeck skills import --path <SKILL.md>"
    assert payload["skills_load_preview_command_template"] == (
        "agentdeck skills load-preview --name <name> --agent <agent_id> --purpose <purpose>"
    )
    assert payload["skills_suggestions_command"] == "agentdeck skills suggestions"
    assert payload["skills_suggest_command_template"] == (
        "agentdeck skills suggest --name <name> --summary <summary> --rationale <rationale> --source <source>"
    )
    assert payload["contract_path"].endswith("docs/contracts/skills-schema.md")
    assert payload["contract_exists"] is True
    assert payload["list_response_fields"] == expected["list_response_fields"]
    assert payload["skill_item_fields"] == expected["skill_item_fields"]
    assert payload["skill_control_fields"] == expected["skill_control_fields"]
    assert payload["detail_response_fields"] == expected["detail_response_fields"]
    assert payload["import_preview_response_fields"] == expected["import_preview_response_fields"]
    assert payload["import_response_fields"] == expected["import_response_fields"]
    assert payload["load_preview_response_fields"] == expected["load_preview_response_fields"]
    assert payload["load_response_fields"] == expected["load_response_fields"]
    assert payload["suggest_response_fields"] == expected["suggest_response_fields"]
    assert payload["suggestions_response_fields"] == expected["suggestions_response_fields"]
    assert payload["suggestion_item_fields"] == expected["suggestion_item_fields"]


def test_contract_skills_example_exports_gui_ready_skill_registry(capsys) -> None:
    exit_code = cli.main(["contract", "skills", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["example"] is True
    example = payload["example_skills"]
    assert payload["example_list_response_fields"] == payload["list_response_fields"]
    assert set(payload["example_list_response_fields"]) == set(example["list"])
    assert payload["example_skill_item_fields"] == payload["skill_item_fields"]
    assert set(payload["example_skill_item_fields"]) == set(example["list"]["skills"][0])
    assert payload["example_skill_control_fields"] == payload["skill_control_fields"]
    assert set(payload["example_skill_control_fields"]) == set(example["list"]["skills"][0]["controls"][0])
    assert set(payload["example_skill_control_fields"]) == set(example["list"]["controls"][0])
    assert payload["example_detail_response_fields"] == payload["detail_response_fields"]
    assert set(payload["example_detail_response_fields"]) == set(example["detail"])
    assert payload["example_import_preview_response_fields"] == payload["import_preview_response_fields"]
    assert set(payload["example_import_preview_response_fields"]) == set(example["import_preview"])
    assert payload["example_import_response_fields"] == payload["import_response_fields"]
    assert set(payload["example_import_response_fields"]) == set(example["import"])
    assert payload["example_load_preview_response_fields"] == payload["load_preview_response_fields"]
    assert set(payload["example_load_preview_response_fields"]) == set(example["load_preview"])
    assert payload["example_load_response_fields"] == payload["load_response_fields"]
    assert set(payload["example_load_response_fields"]) == set(example["load"])
    assert payload["example_suggest_response_fields"] == payload["suggest_response_fields"]
    assert set(payload["example_suggest_response_fields"]) == set(example["suggest"])
    assert payload["example_suggestions_response_fields"] == payload["suggestions_response_fields"]
    assert set(payload["example_suggestions_response_fields"]) == set(example["suggestions"])
    assert payload["example_suggestion_item_fields"] == payload["suggestion_item_fields"]
    assert set(payload["example_suggestion_item_fields"]) == set(example["suggestions"]["items"][0])
    assert example["list"]["controls"][0]["kind"] == "import"
    assert example["list"]["skills"][0]["controls"][1]["kind"] == "load"
    assert example["import_preview"]["controls"][0]["kind"] == "import"
    assert example["import_preview"]["controls"][1]["kind"] == "force_import"
    assert example["load_preview"]["controls"][0]["kind"] == "load"
    assert "content_snapshot" not in example["load_preview"]["skill"]
    assert example["suggest"]["suggestion"]["status"] == "pending"
    assert example["suggestions"]["items"][0]["draft_path"] == ".agentdeck/skills/incident-review/SKILL.md"
    assert example["import"]["skill"]["controls"][0]["kind"] == "show"


def test_contract_skills_cli_matches_contract_module(capsys) -> None:
    cli.main(["contract", "skills", "--example"])

    payload = json.loads(capsys.readouterr().out)
    expected = skills_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected


def test_contract_run_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "run"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = run_start_contract_payload(Path(payload["contract_path"]))
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["run_command"] == "agentdeck run --task <text>"
    assert payload["contract_path"].endswith("docs/contracts/run-schema.md")
    assert payload["contract_exists"] is True
    assert payload["response_fields"] == expected["response_fields"]
    assert payload["leader_backend_fields"] == expected["leader_backend_fields"]
    assert payload["control_fields"] == expected["control_fields"]
    assert payload["approval_contract"] == "agentdeck contract approvals"
    assert payload["leader_review_contract"] == "agentdeck contract leader-review"


def test_contract_run_example_exports_gui_ready_response(capsys) -> None:
    exit_code = cli.main(["contract", "run", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = run_start_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    example = payload["example_run_start"]
    assert payload["example_response_fields"] == payload["response_fields"]
    assert payload["example_leader_backend_fields"] == payload["leader_backend_fields"]
    assert payload["example_progress_fields"] == payload["progress_response_fields"]
    assert payload["example_control_fields"] == payload["control_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert set(payload["example_leader_backend_fields"]) == set(example["leader_backend"])
    assert set(payload["example_progress_fields"]) == set(payload["example_run_progress"])
    assert set(payload["example_control_fields"]) == set(example["controls"][0])
    assert example["mode"] == "run_start"
    assert payload["example_run_progress"]["mode"] == "run_progress"
    assert example["safety"] == "approval_gated"
    assert example["requires_explicit_user"] is True


def test_contract_artifacts_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "artifacts"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["artifacts_command"] == "agentdeck artifacts"
    assert payload["trace_command_template"] == "agentdeck trace --id <id>"
    assert payload["project_view_contract"] == "agentdeck contract project-view"
    assert payload["trace_contract"] == "agentdeck contract trace"
    assert payload["contract_path"].endswith("docs/contracts/artifacts-schema.md")
    assert payload["contract_exists"] is True
    assert payload["response_fields"] == [
        "schema_version",
        "artifacts_command",
        "project_view_contract",
        "trace_contract",
        "trace_command_template",
        "artifacts",
        "controls",
    ]
    assert payload["control_fields"] == ["kind", "label", "command", "safety", "enabled", "blocker"]
    assert payload["artifact_summary_fields"] == ["count", "by_status", "by_kind", "items"]
    assert payload["artifact_item_fields"] == [
        "artifact_id",
        "message_id",
        "job_id",
        "reply_id",
        "from_agent",
        "path",
        "kind",
        "status",
        "created_at",
        "trace_command",
    ]


def test_contract_artifacts_example_exports_gui_ready_response(capsys) -> None:
    exit_code = cli.main(["contract", "artifacts", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["example"] is True
    example = payload["example_artifacts"]
    assert payload["example_response_fields"] == payload["response_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert payload["example_artifact_summary_fields"] == payload["artifact_summary_fields"]
    assert set(payload["example_artifact_summary_fields"]) == set(example["artifacts"])
    assert payload["example_artifact_item_fields"] == payload["artifact_item_fields"]
    assert set(payload["example_artifact_item_fields"]) == set(example["artifacts"]["items"][0])
    assert example["artifacts"]["items"][0]["artifact_id"] == "art_example"
    assert example["artifacts"]["items"][0]["trace_command"] == "agentdeck trace --id msg_example"


def test_contract_controls_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "controls"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = controls_contract_payload(Path(payload["contract_path"]))
    assert payload == expected
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["controls_command"] == "agentdeck controls"
    assert payload["contract_path"].endswith("docs/contracts/controls-schema.md")
    assert payload["contract_exists"] is True
    assert payload["control_registry_group_fields"] == [
        "group_id",
        "scope",
        "card",
        "label",
        "item_count",
        "enabled_count",
        "disabled_count",
        "items",
    ]
    assert payload["workbench_contract"] == "agentdeck contract workbench"
    assert payload["leader_chat_contract"] == "agentdeck contract leader-chat"


def test_contract_controls_example_exports_gui_ready_response(capsys) -> None:
    exit_code = cli.main(["contract", "controls", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = controls_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    example = payload["example_control_registry_card"]
    assert example["mode"] == "control_registry"
    assert example["item_count"] == len(example["items"])
    assert example["group_count"] == len(example["groups"])
    assert example["groups"][0]["group_id"] == "leader:leader_card"
    assert example["groups"][0]["label"] == "Leader"
    assert example["groups"][0]["items"][0] == example["items"][0]
    assert payload["example_control_registry_card_fields"] == payload["control_registry_card_fields"]
    assert set(payload["example_control_registry_item_fields"]) == set(payload["control_registry_item_fields"])
    assert {
        (item["scope"], item["card"], item["kind"], item["agent_id"])
        for item in example["items"]
    } >= {
        ("leader", "leader_card", "continue", "leader"),
        ("runtime", "runtime_card", "capture", "planner"),
        ("operator", "operator_card", "apply", None),
    }


def test_contract_agent_runtime_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "agent-runtime"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = agent_runtime_contract_payload(Path(payload["contract_path"]))
    assert payload == expected
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["list_command"] == "agentdeck agent list"
    assert payload["ready_command"] == "agentdeck agent ready"
    assert payload["spawn_ready_command"] == "agentdeck agent spawn-ready --confirm"
    assert payload["spawn_command_template"] == "agentdeck agent spawn --agent <id>"
    assert payload["terminal_command_template"] == "agentdeck agent terminal --agent <id>"
    assert payload["capture_command_template"] == "agentdeck agent capture --agent <id> --lines 200"
    assert payload["send_command_template"] == "agentdeck agent send --agent <id> --text <text>"
    assert payload["stop_command_template"] == "agentdeck agent stop --agent <id>"
    assert payload["refresh_command"] == "agentdeck agent refresh"
    assert payload["contract_path"].endswith("docs/contracts/agent-runtime-schema.md")
    assert payload["contract_exists"] is True
    assert payload["agent_item_fields"] == list(AGENT_RUNTIME_AGENT_ITEM_FIELDS)
    assert payload["capture_response_fields"] == list(AGENT_RUNTIME_CAPTURE_RESPONSE_FIELDS)
    assert payload["terminal_response_fields"] == list(AGENT_RUNTIME_TERMINAL_RESPONSE_FIELDS)
    assert payload["refresh_response_fields"] == list(AGENT_RUNTIME_REFRESH_RESPONSE_FIELDS)
    assert payload["refresh_agent_fields"] == list(AGENT_RUNTIME_REFRESH_AGENT_FIELDS)
    assert payload["ready_response_fields"] == [
        "ok",
        "mode",
        "runtime_backend",
        "total_count",
        "running_count",
        "not_running_count",
        "all_running",
        "next_command",
        "spawn_commands",
        "spawn_ready_command",
        "refresh_command",
        "dispatch_ready_command",
        "controls",
        "runtime_card",
    ]
    assert payload["spawn_ready_response_fields"] == [
        "ok",
        "mode",
        "requires_explicit_user",
        "safety",
        "spawned_count",
        "skipped_count",
        "results",
        "ready_command",
    ]
    assert payload["spawn_ready_result_fields"] == [
        "agent_id",
        "status",
        "previous_status",
        "pane_id",
        "spawn_command",
        "blocker",
    ]
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
    assert payload["example_terminal_response_fields"] == payload["terminal_response_fields"]
    assert payload["example_refresh_response_fields"] == payload["refresh_response_fields"]
    assert payload["example_refresh_agent_fields"] == payload["refresh_agent_fields"]
    assert payload["example_ready_response_fields"] == payload["ready_response_fields"]
    assert payload["example_spawn_ready_response_fields"] == payload["spawn_ready_response_fields"]
    assert payload["example_spawn_ready_result_fields"] == payload["spawn_ready_result_fields"]
    assert payload["example_control_fields"] == payload["runtime_control_fields"]
    assert set(example["agents"][0]) == set(payload["agent_item_fields"])
    assert set(example["capture"]) == set(payload["capture_response_fields"])
    assert set(example["terminal"]) == set(payload["terminal_response_fields"])
    assert set(example["refresh"]) == set(payload["refresh_response_fields"])
    assert set(example["refresh"]["agents"][0]) == set(payload["refresh_agent_fields"])
    assert set(example["ready"]) == set(payload["ready_response_fields"])
    assert set(example["spawn_ready"]) == set(payload["spawn_ready_response_fields"])
    assert set(example["spawn_ready"]["results"][0]) == set(payload["spawn_ready_result_fields"])
    assert set(example["controls"][0]) == set(payload["runtime_control_fields"])
    assert example["agents"][0]["runtime"]["pane_id"] == "%42"
    assert example["capture"]["output"] == "status: completed\n"
    assert example["terminal"]["attach_command"] == "tmux -L agentdeck-multi-agent-explore attach -t agentdeck"


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
    assert payload["leader_fields"] == [
        "agent_id",
        "provider",
        "model",
        "approval_mode",
        "leader_backend",
    ]
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
    assert payload["example_leader_fields"] == payload["leader_fields"]
    assert set(payload["example_leader_fields"]) == set(example["leader"])
    assert example["leader"]["leader_backend"] == {
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
    assert payload["run_start_card_fields"] == expected["run_start_card_fields"]
    assert payload["run_progress_card_fields"] == expected["run_progress_card_fields"]
    assert payload["capture_card_fields"] == expected["capture_card_fields"]
    assert payload["terminal_card_fields"] == expected["terminal_card_fields"]
    assert payload["dispatch_preview_card_fields"] == expected["dispatch_preview_card_fields"]
    assert payload["agent_ready_card_fields"] == expected["agent_ready_card_fields"]
    assert payload["skill_import_preview_card_fields"] == expected["skill_import_preview_card_fields"]
    assert payload["skill_load_preview_card_fields"] == expected["skill_load_preview_card_fields"]
    assert payload["skill_suggestions_card_fields"] == expected["skill_suggestions_card_fields"]
    assert payload["memory_suggestions_card_fields"] == expected["memory_suggestions_card_fields"]
    assert payload["artifacts_card_fields"] == expected["artifacts_card_fields"]
    assert payload["artifact_summary_fields"] == expected["artifact_summary_fields"]
    assert payload["artifact_item_fields"] == expected["artifact_item_fields"]
    assert payload["trace_card_fields"] == expected["trace_card_fields"]
    assert payload["trace_message_fields"] == expected["trace_message_fields"]
    assert payload["trace_artifact_fields"] == expected["trace_artifact_fields"]
    assert payload["trace_inbox_item_fields"] == expected["trace_inbox_item_fields"]
    assert payload["workbench_control_registry_item_fields"] == expected["workbench_control_registry_item_fields"]
    assert payload["control_registry_card_fields"] == expected["control_registry_card_fields"]


def test_contract_leader_summary_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "leader-summary"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = leader_summary_contract_payload(Path(payload["contract_path"]))
    assert payload == expected
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["summary_command"] == "agentdeck leader summary --plan-id <id>"
    assert payload["contract_path"].endswith("docs/contracts/leader-summary-schema.md")
    assert payload["contract_exists"] is True
    assert payload["project_view_contract"] == "agentdeck contract project-view"
    assert payload["leader_review_contract"] == "agentdeck contract leader-review"
    assert payload["trace_contract"] == "agentdeck contract trace"
    assert payload["response_fields"] == [
        "schema_version",
        "plan_id",
        "task",
        "status",
        "provider",
        "model",
        "leader_backend",
        "counts",
        "reply_count",
        "artifact_count",
        "summary",
        "plan_status_command",
        "review_command",
        "steps",
        "controls",
    ]
    assert payload["leader_backend_fields"] == [
        "agent_id",
        "provider",
        "model",
        "provider_backend",
        "provider_transport",
        "reasoning_backend",
        "runtime_kind",
        "pane_backed",
        "pane_id",
        "approval_required",
        "dispatch_ready",
    ]
    assert payload["step_fields"] == [
        "step",
        "agent_id",
        "role",
        "task",
        "approval_id",
        "message_id",
        "attempt_id",
        "job_id",
        "reply_id",
        "reply_text",
        "artifact_count",
        "artifacts",
        "trace_command",
    ]
    assert payload["artifact_fields"] == ["artifact_id", "path", "kind", "status", "trace_command"]
    assert payload["control_fields"] == ["kind", "label", "command", "safety", "enabled", "blocker"]


def test_contract_leader_status_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "leader-status"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = leader_status_contract_payload(Path(payload["contract_path"]))
    assert payload == expected
    assert payload["schema_version"] == cli.PROJECT_VIEW_SCHEMA_VERSION
    assert payload["status_command"] == "agentdeck leader status"
    assert payload["contract_path"].endswith("docs/contracts/leader-status-schema.md")
    assert payload["contract_exists"] is True
    assert payload["project_view_contract"] == "agentdeck contract project-view"
    assert payload["workbench_contract"] == "agentdeck contract workbench"
    assert payload["provider_health_fields"] == list(WORKBENCH_PROVIDER_HEALTH_FIELDS)
    assert payload["control_fields"] == ["kind", "label", "command", "safety", "enabled", "blocker"]


def test_contract_leader_summary_example_exports_gui_ready_response(capsys) -> None:
    exit_code = cli.main(["contract", "leader-summary", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    expected = leader_summary_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected
    example = payload["example_leader_summary"]
    assert payload["example_response_fields"] == payload["response_fields"]
    assert payload["example_leader_backend_fields"] == payload["leader_backend_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert set(payload["example_leader_backend_fields"]) == set(example["leader_backend"])
    assert payload["example_step_fields"] == payload["step_fields"]
    assert set(payload["example_step_fields"]) == set(example["steps"][0])
    assert payload["example_artifact_fields"] == payload["artifact_fields"]
    assert set(payload["example_artifact_fields"]) == set(example["steps"][0]["artifacts"][0])
    assert payload["example_control_fields"] == payload["control_fields"]
    assert set(payload["example_control_fields"]) == set(example["controls"][0])
    assert example["controls"][2]["command"] == "agentdeck trace --id msg_example"


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
    assert payload["leader_backend_fields"] == expected["leader_backend_fields"]
    assert payload["provider_check_fields"] == expected["provider_check_fields"]
    assert payload["workbench_contract"] == "agentdeck contract workbench"
    assert payload["leader_chat_contract"] == "agentdeck contract leader-chat"
    assert payload["leader_review_contract"] == "agentdeck contract leader-review"


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
    assert payload["example_leader_backend_fields"] == payload["leader_backend_fields"]
    assert set(payload["example_leader_backend_fields"]) == set(example["configured_leader"]["leader_backend"])
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
        "agent_ready_card",
        "terminal_session_card",
        "role_card",
        "ledger_card",
        "lineage_card",
        "queue_card",
        "operator_card",
        "audit_card",
        "artifacts_card",
        "skill_context_card",
        "skill_suggestions_card",
        "memory_suggestions_card",
        "leader_summary_card",
        "contracts_card",
        "control_mode_card",
        "recovery",
        "next_command",
        "continue_card",
        "active_queue_source",
        "run_progress_card",
        "inbox_card",
        "leader_inbox_card",
        "approval_card",
        "leader_action",
        "control_registry",
        "change_summary",
    ]
    assert payload["leader_card_fields"] == [
        "agent_id",
        "provider",
        "model",
        "approval_mode",
        "api_backed",
        "leader_backend",
        "chat_command",
        "continue_command",
        "review_command_template",
        "actions_command",
        "status_command",
        "controls",
    ]
    assert payload["leader_control_fields"] == [
        "kind",
        "label",
        "command",
        "safety",
        "enabled",
        "blocker",
    ]
    assert payload["control_mode_card_fields"] == [
        "mode",
        "title",
        "current_mode",
        "approval_mode",
        "default_safety",
        "available_modes",
        "active_controls",
        "set_mode_command_template",
        "policy_source",
    ]
    assert payload["provider_health_fields"] == [
        "agent_id",
        "provider",
        "model",
        "approval_mode",
        "api_backed",
        "provider_backend",
        "provider_transport",
        "leader_backend",
        "supported",
        "ready",
        "missing_env",
        "detail",
        "command_path",
        "doctor_command",
        "doctor_contract",
        "setup_commands",
        "controls",
    ]
    assert payload["runtime_card_fields"] == ["backend", "count", "by_status", "refresh_command", "agents"]
    assert payload["agent_ready_card_fields"] == list(AGENT_RUNTIME_READY_RESPONSE_FIELDS)
    assert payload["terminal_session_card_fields"] == [
        "mode",
        "runtime_backend",
        "session_name",
        "attach_command",
        "running_count",
        "agent_count",
        "open_terminals_command",
        "refresh_command",
        "controls",
        "terminals",
    ]
    assert payload["terminal_session_control_fields"] == [
        "kind",
        "label",
        "command",
        "safety",
        "enabled",
        "blocker",
    ]
    assert payload["terminal_session_item_fields"] == [
        "agent_id",
        "role",
        "status",
        "pane_id",
        "terminal_command",
        "select_pane_command",
        "enabled",
        "blocker",
        "controls",
    ]
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
        "terminal_command",
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
        "controls",
    ]
    assert payload["ledger_card_fields"] == [
        "messages",
        "jobs",
        "replies",
        "artifacts",
        "inbox",
        "trace_commands",
        "controls",
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
        "controls",
    ]
    assert payload["artifacts_card_fields"] == list(ARTIFACTS_RESPONSE_FIELDS)
    assert payload["artifact_summary_fields"] == list(ARTIFACTS_SUMMARY_FIELDS)
    assert payload["artifact_item_fields"] == list(PROJECT_VIEW_ARTIFACT_ITEM_FIELDS)
    assert payload["skill_context_card_fields"] == [
        "mode",
        "title",
        "summary",
        "skills_command",
        "project_view_command",
        "count",
        "items",
        "controls",
    ]
    assert payload["skill_suggestions_card_fields"] == [
        "mode",
        "title",
        "summary",
        "suggestions_command",
        "project_view_command",
        "count",
        "pending_count",
        "items",
        "controls",
    ]
    assert payload["memory_suggestions_card_fields"] == [
        "mode",
        "title",
        "summary",
        "suggestions_command",
        "apply_preview_command_template",
        "project_view_command",
        "count",
        "pending_count",
        "items",
        "controls",
    ]
    assert payload["leader_summary_card_fields"] == list(LEADER_SUMMARY_RESPONSE_FIELDS)
    assert payload["contracts_card_fields"] == [
        "contracts_command",
        "contract_index_contract",
        "workbench_contract",
        "controls_contract",
        "skills_contract",
        "agent_runtime_contract",
        "leader_chat_contract",
        "leader_review_contract",
        "leader_summary_contract",
        "project_view_contract",
        "events_contract",
        "doctor_contract",
        "run_contract",
        "artifacts_contract",
    ]
    assert payload["change_summary_fields"] == [
        "since_event_id",
        "latest_event_id",
        "has_new_events",
        "new_event_count",
        "new_events",
    ]
    assert payload["control_registry_item_fields"] == [
        "scope",
        "card",
        "kind",
        "label",
        "command",
        "safety",
        "enabled",
        "blocker",
        "agent_id",
        "control_id",
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
            "prompt_skill_context": {
                "count": 1,
                "by_agent": {"planner": 1},
                "by_source": {"builtin": 1},
                "items": [
                    {
                        "load_id": "skl_workbench",
                        "agent_id": "planner",
                        "purpose": "workbench ledger",
                        "name": "verification",
                        "source": "builtin",
                        "path": None,
                        "content_hash": "sha256:workbench",
                        "description": "Prove claims with fresh command output.",
                        "required_tools": ["pytest"],
                        "risk": "inspect",
                        "created_at": "2026-07-04T00:00:00+00:00",
                        "show_command": "agentdeck skills show --name verification",
                        "reload_command": (
                            "agentdeck skills load --name verification --agent planner "
                            "--purpose 'workbench ledger'"
                        ),
                    }
                ],
            },
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
    state["artifacts"] = [
        {
            "artifact_id": "art_workbench",
            "message_id": "msg_workbench",
            "job_id": "job_workbench",
            "reply_id": "rep_workbench",
            "from_agent": "planner",
            "path": "docs/workbench-plan.md",
            "kind": "markdown",
            "status": "created",
            "created_at": "2026-07-04T00:00:03+00:00",
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
        "chat_command": "agentdeck leader chat --message <text>",
        "continue_command": "agentdeck continue",
        "review_command_template": "agentdeck leader review --plan-id <plan_id>",
        "actions_command": "agentdeck leader actions",
        "status_command": "agentdeck status",
        "controls": [
            {
                "kind": "chat",
                "label": "Ask Leader",
                "command": "agentdeck leader chat --message <text>",
                "safety": "explicit_user",
                "enabled": False,
                "blocker": "requires message text",
            },
            {
                "kind": "continue",
                "label": "Continue",
                "command": "agentdeck continue",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "review",
                "label": "Review plan",
                "command": "agentdeck leader review --plan-id <plan_id>",
                "safety": "inspect",
                "enabled": False,
                "blocker": "requires plan_id",
            },
            {
                "kind": "actions",
                "label": "Leader actions",
                "command": "agentdeck leader actions",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "refresh",
                "label": "Refresh Leader status",
                "command": "agentdeck leader status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "leader_status",
                "label": "Leader status",
                "command": "agentdeck leader status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "status",
                "label": "Project status",
                "command": "agentdeck status",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
        ],
    }
    assert payload["provider_health"] == {
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
        "controls": [
            {
                "kind": "set_provider",
                "label": "Use fake",
                "command": "agentdeck leader set-provider --provider fake --model fake-plan",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "guarded_set_provider",
                "label": "Use fake if ready",
                "command": "agentdeck leader set-provider --provider fake --model fake-plan --require-ready",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "set_provider",
                "label": "Use DeepSeek",
                "command": "agentdeck leader set-provider --provider deepseek --model deepseek-chat",
                "safety": "explicit_user",
                "enabled": False,
                "blocker": "already current provider",
            },
            {
                "kind": "guarded_set_provider",
                "label": "Use DeepSeek if ready",
                "command": "agentdeck leader set-provider --provider deepseek --model deepseek-chat --require-ready",
                "safety": "explicit_user",
                "enabled": False,
                "blocker": "already current provider",
            },
            {
                "kind": "setup_provider",
                "label": "Setup DeepSeek",
                "command": 'export DEEPSEEK_API_KEY="<your-deepseek-api-key>"',
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "setup_provider",
                "label": "Setup DeepSeek",
                "command": 'export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"',
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "setup_provider",
                "label": "Setup DeepSeek",
                "command": 'export DEEPSEEK_MODEL="deepseek-chat"',
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "set_provider",
                "label": "Use OpenAI-compatible",
                "command": "agentdeck leader set-provider --provider openai-compatible --model openai-compatible-default",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "guarded_set_provider",
                "label": "Use OpenAI-compatible if ready",
                "command": (
                    "agentdeck leader set-provider --provider openai-compatible "
                    "--model openai-compatible-default --require-ready"
                ),
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "setup_provider",
                "label": "Setup OpenAI-compatible",
                "command": 'export AGENTDECK_LEADER_API_KEY="<your-provider-api-key>"',
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "setup_provider",
                "label": "Setup OpenAI-compatible",
                "command": 'export AGENTDECK_LEADER_BASE_URL="https://api.example.com/v1"',
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "setup_provider",
                "label": "Setup OpenAI-compatible",
                "command": 'export AGENTDECK_LEADER_MODEL="<model-name>"',
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "set_provider",
                "label": "Use Codex CLI",
                "command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "guarded_set_provider",
                "label": "Use Codex CLI if ready",
                "command": "agentdeck leader set-provider --provider codex-cli --model codex-default --require-ready",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "setup_provider",
                "label": "Setup Codex CLI",
                "command": "codex login",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "setup_provider",
                "label": "Setup Codex CLI",
                "command": "codex doctor",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "set_provider",
                "label": "Use Claude CLI",
                "command": "agentdeck leader set-provider --provider claude-cli --model claude-default",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "guarded_set_provider",
                "label": "Use Claude CLI if ready",
                "command": "agentdeck leader set-provider --provider claude-cli --model claude-default --require-ready",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "setup_provider",
                "label": "Setup Claude CLI",
                "command": "claude auth",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "setup_provider",
                "label": "Setup Claude CLI",
                "command": "claude doctor",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
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
    assert planner_role["controls"] == [
        {
            "kind": "assign_role",
            "label": "Assign role",
            "command": "agentdeck agent assign-role --agent planner --role <role> --role-prompt <role_prompt>",
            "safety": "explicit_user",
            "enabled": False,
            "blocker": "requires role and role_prompt",
        }
    ]
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
    assert planner_runtime["terminal_command"] == "agentdeck agent terminal --agent planner"
    assert planner_runtime["capture_command"] == "agentdeck agent capture --agent planner --lines 200"
    assert planner_runtime["send_command_template"] == "agentdeck agent send --agent planner --text <text>"
    assert planner_runtime["inbox_command"] == "agentdeck inbox --agent planner"
    assert planner_runtime["controls"] == [
        {
            "kind": "terminal",
            "label": "Open terminal",
            "command": "agentdeck agent terminal --agent planner",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
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
    assert coder_runtime["controls"][1] == {
        "kind": "terminal",
        "label": "Open terminal",
        "command": "agentdeck agent terminal --agent coder",
        "safety": "inspect",
        "enabled": False,
        "blocker": "agent is not running",
    }
    assert coder_runtime["controls"][2]["kind"] == "capture"
    assert coder_runtime["controls"][2]["enabled"] is False
    assert coder_runtime["controls"][2]["blocker"] == "agent is not running"
    assert payload["agent_ready_card"]["mode"] == "agent_runtime_ready"
    assert payload["agent_ready_card"]["runtime_backend"] == "tmux"
    assert payload["agent_ready_card"]["total_count"] == 3
    assert payload["agent_ready_card"]["running_count"] == 1
    assert payload["agent_ready_card"]["not_running_count"] == 2
    assert payload["agent_ready_card"]["all_running"] is False
    assert payload["agent_ready_card"]["next_command"] == "agentdeck agent spawn-ready --confirm"
    assert payload["agent_ready_card"]["spawn_commands"] == [
        "agentdeck agent spawn --agent coder",
        "agentdeck agent spawn --agent reviewer",
    ]
    assert payload["agent_ready_card"]["spawn_ready_command"] == "agentdeck agent spawn-ready --confirm"
    assert payload["agent_ready_card"]["refresh_command"] == "agentdeck agent refresh"
    assert payload["agent_ready_card"]["dispatch_ready_command"] == "agentdeck approval dispatch-ready --confirm"
    assert payload["agent_ready_card"]["runtime_card"] == payload["runtime_card"]
    assert payload["terminal_session_card"] == {
        "mode": "terminal_session",
        "runtime_backend": "tmux",
        "session_name": "agentdeck",
        "attach_command": "tmux -L agentdeck-repo attach -t agentdeck",
        "running_count": 1,
        "agent_count": 3,
        "open_terminals_command": "agentdeck controls",
        "refresh_command": "agentdeck agent refresh",
        "controls": [
            {
                "kind": "attach_session",
                "label": "Attach session",
                "command": "tmux -L agentdeck-repo attach -t agentdeck",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "open_controls",
                "label": "Open terminal controls",
                "command": "agentdeck controls",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "refresh_runtime",
                "label": "Refresh runtime",
                "command": "agentdeck agent refresh",
                "safety": "explicit_runtime",
                "enabled": True,
                "blocker": None,
            },
        ],
        "terminals": [
            {
                "agent_id": "planner",
                "role": "planning",
                "status": "running",
                "pane_id": "%42",
                "terminal_command": "agentdeck agent terminal --agent planner",
                "select_pane_command": "tmux -L agentdeck-repo select-pane -t %42",
                "enabled": True,
                "blocker": None,
                "controls": [
                    {
                        "kind": "select_pane",
                        "label": "Select pane",
                        "command": "tmux -L agentdeck-repo select-pane -t %42",
                        "safety": "inspect",
                        "enabled": True,
                        "blocker": None,
                    }
                ],
            },
            {
                "agent_id": "coder",
                "role": "implementation",
                "status": "configured",
                "pane_id": None,
                "terminal_command": "agentdeck agent terminal --agent coder",
                "select_pane_command": None,
                "enabled": False,
                "blocker": "agent is not running",
                "controls": [
                    {
                        "kind": "select_pane",
                        "label": "Select pane",
                        "command": None,
                        "safety": "inspect",
                        "enabled": False,
                        "blocker": "agent is not running",
                    }
                ],
            },
            {
                "agent_id": "reviewer",
                "role": "review",
                "status": "configured",
                "pane_id": None,
                "terminal_command": "agentdeck agent terminal --agent reviewer",
                "select_pane_command": None,
                "enabled": False,
                "blocker": "agent is not running",
                "controls": [
                    {
                        "kind": "select_pane",
                        "label": "Select pane",
                        "command": None,
                        "safety": "inspect",
                        "enabled": False,
                        "blocker": "agent is not running",
                    }
                ],
            },
        ],
    }
    terminal_session_controls = [
        item
        for item in payload["control_registry"]
        if item["scope"] == "terminal_session"
        and item["card"] == "terminal_session_card"
        and item["kind"] in {"attach_session", "open_controls", "refresh_runtime"}
    ]
    assert terminal_session_controls == [
        {
            "scope": "terminal_session",
            "card": "terminal_session_card",
            "kind": "attach_session",
            "label": "Attach session",
            "command": "tmux -L agentdeck-repo attach -t agentdeck",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
            "agent_id": None,
            "control_id": terminal_session_controls[0]["control_id"],
        },
        {
            "scope": "terminal_session",
            "card": "terminal_session_card",
            "kind": "open_controls",
            "label": "Open terminal controls",
            "command": "agentdeck controls",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
            "agent_id": None,
            "control_id": terminal_session_controls[1]["control_id"],
        },
        {
            "scope": "terminal_session",
            "card": "terminal_session_card",
            "kind": "refresh_runtime",
            "label": "Refresh runtime",
            "command": "agentdeck agent refresh",
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
            "agent_id": None,
            "control_id": terminal_session_controls[2]["control_id"],
        },
    ]
    terminal_select_controls = [
        item
        for item in payload["control_registry"]
        if item["scope"] == "terminal_session"
        and item["card"] == "terminal_session_card"
        and item["kind"] == "select_pane"
    ]
    assert terminal_select_controls == [
        {
            "scope": "terminal_session",
            "card": "terminal_session_card",
            "kind": "select_pane",
            "label": "Select pane",
            "command": "tmux -L agentdeck-repo select-pane -t %42",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
            "agent_id": "planner",
            "control_id": terminal_select_controls[0]["control_id"],
        },
        {
            "scope": "terminal_session",
            "card": "terminal_session_card",
            "kind": "select_pane",
            "label": "Select pane",
            "command": None,
            "safety": "inspect",
            "enabled": False,
            "blocker": "agent is not running",
            "agent_id": "coder",
            "control_id": terminal_select_controls[1]["control_id"],
        },
        {
            "scope": "terminal_session",
            "card": "terminal_session_card",
            "kind": "select_pane",
            "label": "Select pane",
            "command": None,
            "safety": "inspect",
            "enabled": False,
            "blocker": "agent is not running",
            "agent_id": "reviewer",
            "control_id": terminal_select_controls[2]["control_id"],
        },
    ]
    assert payload["ledger_card"]["messages"]["count"] == 1
    assert payload["ledger_card"]["messages"]["items"][0]["trace_command"] == "agentdeck trace --id msg_workbench"
    assert payload["ledger_card"]["messages"]["items"][0]["prompt_skill_context"]["items"][0]["name"] == "verification"
    assert "content_snapshot" not in payload["ledger_card"]["messages"]["items"][0]["prompt_skill_context"]["items"][0]
    assert payload["ledger_card"]["jobs"]["count"] == 1
    assert payload["ledger_card"]["jobs"]["items"][0]["trace_command"] == "agentdeck trace --id job_workbench"
    assert payload["ledger_card"]["replies"]["count"] == 1
    assert payload["ledger_card"]["replies"]["items"][0]["trace_command"] == "agentdeck trace --id rep_workbench"
    assert payload["ledger_card"]["artifacts"]["count"] == 1
    assert payload["ledger_card"]["artifacts"]["items"][0]["artifact_id"] == "art_workbench"
    assert payload["ledger_card"]["artifacts"]["items"][0]["path"] == "docs/workbench-plan.md"
    assert payload["ledger_card"]["artifacts"]["items"][0]["trace_command"] == "agentdeck trace --id msg_workbench"
    assert payload["artifacts_card"]["artifacts_command"] == "agentdeck artifacts"
    assert payload["artifacts_card"]["trace_command_template"] == "agentdeck trace --id <id>"
    assert payload["artifacts_card"]["artifacts"]["count"] == 1
    assert payload["artifacts_card"]["artifacts"]["items"][0] == {
        "artifact_id": "art_workbench",
        "message_id": "msg_workbench",
        "job_id": "job_workbench",
        "reply_id": "rep_workbench",
        "from_agent": "planner",
        "path": "docs/workbench-plan.md",
        "kind": "markdown",
        "status": "created",
        "created_at": "2026-07-04T00:00:03+00:00",
        "trace_command": "agentdeck trace --id msg_workbench",
    }
    assert payload["ledger_card"]["inbox"]["total"] == 1
    assert payload["ledger_card"]["inbox"]["heads"]["planner"]["inbox_id"] == "inb_workbench_head"
    assert payload["ledger_card"]["trace_commands"] == [
        "agentdeck trace --id msg_workbench",
        "agentdeck trace --id job_workbench",
        "agentdeck trace --id rep_workbench",
        "agentdeck trace --id inb_workbench_head",
    ]
    assert payload["lineage_card"] == {
        "mode": "lineage",
        "title": "Communication lineage",
        "message_count": 1,
        "job_count": 1,
        "reply_count": 1,
        "inbox_count": 1,
        "trace_command_template": "agentdeck trace --id <id>",
        "recent_paths": [
            {
                "message_id": "msg_workbench",
                "job_id": "job_workbench",
                "reply_id": "rep_workbench",
                "inbox_id": "inb_workbench_head",
                "from_actor": "leader",
                "to_agent": "planner",
                "from_agent": "planner",
                "to_actor": "leader",
                "task": "展示工作台 inbox",
                "status": "reply_pending_ack",
                "trace_command": "agentdeck trace --id msg_workbench",
            }
        ],
    }
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
    assert payload["control_registry"][0] == {
        "scope": "leader",
        "card": "leader_card",
        "kind": "chat",
        "label": "Ask Leader",
        "command": "agentdeck leader chat --message <text>",
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "requires message text",
        "agent_id": "leader",
        "control_id": payload["control_registry"][0]["control_id"],
    }
    provider_controls = [
        item
        for item in payload["control_registry"]
        if item["scope"] == "provider" and item["kind"] == "set_provider"
    ]
    assert provider_controls[0] == {
        "scope": "provider",
        "card": "provider_health",
        "kind": "set_provider",
        "label": "Use fake",
        "command": "agentdeck leader set-provider --provider fake --model fake-plan",
        "safety": "explicit_user",
        "enabled": True,
        "blocker": None,
        "agent_id": "leader",
        "control_id": provider_controls[0]["control_id"],
    }
    assert any(item["command"].endswith("--provider codex-cli --model codex-default") for item in provider_controls)
    guarded_provider_controls = [
        item
        for item in payload["control_registry"]
        if item["scope"] == "provider" and item["kind"] == "guarded_set_provider"
    ]
    assert any(
        item["command"] == (
            "agentdeck leader set-provider --provider codex-cli --model codex-default --require-ready"
        )
        for item in guarded_provider_controls
    )
    role_controls = [
        item
        for item in payload["control_registry"]
        if item["scope"] == "role" and item["kind"] == "assign_role"
    ]
    assert role_controls[0] == {
        "scope": "role",
        "card": "role_card",
        "kind": "assign_role",
        "label": "Assign role",
        "command": "agentdeck agent assign-role --agent planner --role <role> --role-prompt <role_prompt>",
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "requires role and role_prompt",
        "agent_id": "planner",
        "control_id": role_controls[0]["control_id"],
    }
    assert {
        (item["scope"], item["card"], item["kind"], item["agent_id"])
        for item in payload["control_registry"]
    } >= {
        ("leader", "leader_card", "continue", "leader"),
        ("leader", "leader_card", "leader_status", "leader"),
        ("inbox", "inbox_card", "preview", "planner"),
        ("inbox", "inbox_card", "ack", "planner"),
        ("role", "role_card", "assign_role", "planner"),
        ("runtime", "runtime_card", "capture", "planner"),
        ("audit", "audit_card", "inspect", None),
        ("operator", "operator_card", "explicit", None),
    }
    audit_control = next(
        item
        for item in payload["control_registry"]
        if item["scope"] == "audit" and item["card"] == "audit_card" and item["kind"] == "inspect"
    )
    assert audit_control == {
        "scope": "audit",
        "card": "audit_card",
        "kind": "inspect",
        "label": "Inspect audit events",
        "command": "agentdeck events --limit 20",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
        "agent_id": None,
        "control_id": audit_control["control_id"],
    }
    inbox_ack_control = next(
        item
        for item in payload["control_registry"]
        if item["scope"] == "inbox" and item["card"] == "inbox_card" and item["kind"] == "ack"
    )
    assert inbox_ack_control == {
        "scope": "inbox",
        "card": "inbox_card",
        "kind": "ack",
        "label": "Acknowledge inbox head",
        "command": "agentdeck ack --agent planner --inbox-id inb_workbench_head",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
        "agent_id": "planner",
        "control_id": inbox_ack_control["control_id"],
    }
    assert payload["audit_card"]["latest_event"] == payload["recovery"]["latest_event"]
    assert payload["audit_card"]["latest_event"]["event_type"] == "workbench_second_event"
    assert payload["audit_card"]["recent_events"] == payload["recovery"]["recent_events"]
    assert payload["audit_card"]["event_count"] == len(payload["recovery"]["recent_events"])
    assert payload["audit_card"]["events_command"] == "agentdeck events --limit 20"
    assert payload["audit_card"]["controls"][0] == {
        "kind": "inspect",
        "label": "Inspect audit events",
        "command": "agentdeck events --limit 20",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
    }
    assert payload["contracts_card"] == {
        "contracts_command": "agentdeck contract list",
        "contract_index_contract": "docs/contracts/contract-index-schema.md",
        "workbench_contract": "agentdeck contract workbench",
        "agent_runtime_contract": "agentdeck contract agent-runtime",
        "controls_contract": "agentdeck contract controls",
        "skills_contract": "agentdeck contract skills",
        "leader_chat_contract": "agentdeck contract leader-chat",
        "leader_review_contract": "agentdeck contract leader-review",
        "leader_summary_contract": "agentdeck contract leader-summary",
        "project_view_contract": "agentdeck contract project-view",
        "events_contract": "agentdeck contract events",
        "doctor_contract": "agentdeck contract doctor",
        "run_contract": "agentdeck contract run",
        "artifacts_contract": "agentdeck contract artifacts",
    }
    assert payload["control_mode_card"] == {
        "mode": "control_mode",
        "title": "Control mode",
        "current_mode": "ask",
        "approval_mode": "confirm",
        "default_safety": "inspect",
        "available_modes": [
            {
                "mode": "ask",
                "label": "Ask / inspect",
                "description": "Plan, inspect, and suggest commands without mutating runtime state.",
                "enabled": True,
                "requires_explicit_user": False,
                "safety": "inspect",
                "blocker": None,
            },
            {
                "mode": "approve",
                "label": "Approval gated",
                "description": "Allow safe apply after explicit human approval while runtime actions remain explicit.",
                "enabled": True,
                "requires_explicit_user": True,
                "safety": "safe_apply",
                "blocker": None,
            },
            {
                "mode": "autonomous",
                "label": "Autonomous bounded",
                "description": "Reserved for future scoped delegation with budgets, allowlists, and audit gates.",
                "enabled": False,
                "requires_explicit_user": True,
                "safety": "delegated",
                "blocker": "autonomous execution policy is not implemented",
            },
        ],
        "active_controls": [
            {
                "kind": "inspect",
                "label": "Inspect policy",
                "command": "agentdeck workbench",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "set_mode",
                "label": "Ask / inspect",
                "command": "agentdeck policy set-mode --mode ask",
                "safety": "inspect",
                "enabled": False,
                "blocker": "already current mode",
            },
            {
                "kind": "set_mode",
                "label": "Approval gated",
                "command": "agentdeck policy set-mode --mode approve",
                "safety": "explicit_user",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "set_mode",
                "label": "Autonomous bounded",
                "command": "agentdeck policy set-mode --mode autonomous",
                "safety": "delegated",
                "enabled": False,
                "blocker": "autonomous execution policy is not implemented",
            },
        ],
        "set_mode_command_template": "agentdeck policy set-mode --mode <mode>",
        "policy_source": ".agentdeck/config.toml:leader.approval_mode",
    }
    assert payload["continue_card"]["status"] == "inbox_pending"
    assert payload["active_queue_source"] == "inbox"
    assert payload["inbox_card"]["agent_id"] in {"planner", "leader"}
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


def test_workbench_embeds_latest_run_progress_card_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_path = root / ".agentdeck" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    config_text = config_text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    config_path.write_text(config_text, encoding="utf-8")
    cli.main(["run", "--task", "实现 workbench run progress"])
    started = json.loads(capsys.readouterr().out)
    plan_id = started["plan_id"]
    approval_id = started["approval_card"]["approvals"][0]["approval_id"]
    cli.main(["approval", "approve", "--approval-id", approval_id])
    capsys.readouterr()
    state_before = StateStore(root).load()

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    run_progress_card = payload["run_progress_card"]
    assert run_progress_card["mode"] == "run_progress"
    assert run_progress_card["plan_id"] == plan_id
    assert run_progress_card["leader_backend"] == started["leader_backend"]
    assert run_progress_card["counts"]["approved"] == 1
    assert run_progress_card["counts"]["pending"] == 2
    assert run_progress_card["review"]["next_action"] == "dispatch_approved"
    assert run_progress_card["next_command"] == f"agentdeck approval dispatch --approval-id {approval_id}"
    run_progress_registry_items = [
        item
        for item in payload["control_registry"]
        if item["scope"] == "run_progress" and item["card"] == "run_progress_card"
    ]
    assert [item["kind"] for item in run_progress_registry_items] == [
        "plan_status",
        "review",
        "approval_queue",
        "next",
        "continue",
        "workbench",
    ]
    assert run_progress_registry_items[0]["command"] == f"agentdeck plan status --plan-id {plan_id}"
    assert run_progress_registry_items[3]["command"] == run_progress_card["next_command"]
    assert run_progress_registry_items[3]["safety"] == "explicit_runtime"

    assert StateStore(root).load() == state_before


def test_workbench_embeds_summary_card_when_latest_plan_is_ready_to_summarize(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%77")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["leader", "plan", "--provider", "fake", "--model", "fake-plan", "--task", "总结 workbench"])
    started = json.loads(capsys.readouterr().out)
    plan_id = started["plan_id"]
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
            "status: completed\nsummary: done\nfull_output_path: docs/workbench-summary.md",
        ]
    )
    capsys.readouterr()
    state_before = StateStore(root).load()

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    summary_card = payload["leader_summary_card"]
    assert summary_card["plan_id"] == plan_id
    assert summary_card["status"] == "ready"
    assert summary_card["leader_backend"] == started["leader_backend"]
    assert summary_card["reply_count"] == 1
    assert summary_card["artifact_count"] == 1
    assert summary_card["summary"] == "1 dispatched step has replies; 1 artifact recorded."
    assert summary_card["review_command"] == f"agentdeck leader review --plan-id {plan_id}"
    assert summary_card["steps"][0]["message_id"] == message_id
    assert summary_card["steps"][0]["reply_text"].startswith("status: completed")
    assert summary_card["steps"][0]["artifacts"][0]["path"] == "docs/workbench-summary.md"
    assert summary_card["controls"][-1]["command"] == f"agentdeck trace --id {message_id}"
    summary_registry_items = [
        item
        for item in payload["control_registry"]
        if item["scope"] == "leader_summary" and item["card"] == "leader_summary_card"
    ]
    assert [item["kind"] for item in summary_registry_items] == ["summary", "plan_status", "review", "trace"]
    assert summary_registry_items[0]["command"] == f"agentdeck leader summary --plan-id {plan_id}"
    assert summary_registry_items[0]["safety"] == "inspect"

    assert StateStore(root).load() == state_before


def test_workbench_marks_codex_cli_leader_as_local_cli_backed(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_path = root / ".agentdeck" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace('provider = "deepseek"', 'provider = "codex-cli"', 1)
    config_text = config_text.replace('model = "deepseek-chat"', 'model = "codex-default"', 1)
    config_path.write_text(config_text, encoding="utf-8")
    monkeypatch.setattr(cli, "_command_path", lambda command: "/opt/bin/codex" if command == "codex" else None)

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["leader_card"]["api_backed"] is False
    assert payload["leader_card"]["leader_backend"] == {
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
    }
    provider_health = payload["provider_health"]
    assert {key: value for key, value in provider_health.items() if key != "controls"} == {
        "agent_id": "leader",
        "provider": "codex-cli",
        "model": "codex-default",
        "approval_mode": "confirm",
        "api_backed": False,
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
        "supported": True,
        "ready": True,
        "missing_env": [],
        "detail": "codex is available",
        "command_path": "/opt/bin/codex",
        "doctor_command": "agentdeck doctor",
        "doctor_contract": "agentdeck contract doctor",
        "setup_commands": ['codex login', 'codex doctor'],
    }
    codex_control = next(
        item
        for item in provider_health["controls"]
        if item["command"] == "agentdeck leader set-provider --provider codex-cli --model codex-default"
    )
    assert codex_control["enabled"] is False
    assert codex_control["blocker"] == "already current provider"
    assert any(
        item["command"] == "agentdeck leader set-provider --provider claude-cli --model claude-default"
        for item in provider_health["controls"]
    )


def test_policy_set_mode_updates_config_and_workbench_control_mode(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["policy", "set-mode", "--mode", "approve"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "mode": "approve",
        "approval_mode": "approve",
        "policy_source": ".agentdeck/config.toml:leader.approval_mode",
        "workbench_command": "agentdeck workbench",
    }
    assert 'approval_mode = "approve"' in (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8")
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "policy_mode_updated"' in events
    assert '"mode": "approve"' in events
    assert '"approval_mode": "approve"' in events

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    workbench = json.loads(capsys.readouterr().out)
    assert workbench["control_mode_card"]["current_mode"] == "approve"
    assert workbench["control_mode_card"]["approval_mode"] == "approve"
    assert workbench["control_mode_card"]["default_safety"] == "safe_apply"
    approve_controls = {
        item["command"]: item for item in workbench["control_mode_card"]["active_controls"] if item["kind"] == "set_mode"
    }
    assert approve_controls["agentdeck policy set-mode --mode ask"]["enabled"] is True
    assert approve_controls["agentdeck policy set-mode --mode ask"]["safety"] == "inspect"
    assert approve_controls["agentdeck policy set-mode --mode approve"]["enabled"] is False
    assert approve_controls["agentdeck policy set-mode --mode approve"]["blocker"] == "already current mode"

    exit_code = cli.main(["policy", "set-mode", "--mode", "ask"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "ask"
    assert payload["approval_mode"] == "confirm"
    assert 'approval_mode = "confirm"' in (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8")


def test_policy_set_mode_rejects_autonomous_without_mutating_config(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_before = (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8")

    exit_code = cli.main(["policy", "set-mode", "--mode", "autonomous"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "autonomous control mode is not implemented" in captured.err
    assert (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8") == config_before
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "policy_mode_rejected"' in events
    assert '"mode": "autonomous"' in events

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    workbench = json.loads(capsys.readouterr().out)
    assert workbench["control_mode_card"]["current_mode"] == "ask"
    assert workbench["control_mode_card"]["approval_mode"] == "confirm"


def test_workbench_blocks_dispatch_operator_when_approved_agent_is_not_spawned(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["approvals"] = [
        {
            "approval_id": "apv_ready",
            "plan_id": "pln_ready",
            "step_id": "step_1",
            "step": 1,
            "agent_id": "planner",
            "role": "planning",
            "task": "派发前检查 runtime",
            "risk": "requires visible runtime before dispatch",
            "status": "approved",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    store.save(state)

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["active_queue_source"] == "approval"
    assert payload["queue_card"]["active_queue_source"] == "approval"
    assert payload["operator_card"]["status"] == "dispatch_ready"
    assert payload["operator_card"]["command"] == "agentdeck approval dispatch --approval-id apv_ready"
    assert payload["operator_card"]["explicit_command"] == "agentdeck approval dispatch --approval-id apv_ready"
    assert payload["operator_card"]["blocker"] == "agent is not spawned: planner"
    assert payload["operator_card"]["controls"][-1] == {
        "kind": "explicit",
        "label": "Run explicit command",
        "command": "agentdeck approval dispatch --approval-id apv_ready",
        "safety": "explicit_runtime",
        "enabled": False,
        "blocker": "agent is not spawned: planner",
    }
    state_after = StateStore(root).load()
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert state_after.get("inbox", {}) == {}


def test_workbench_surfaces_dispatch_ready_operator_for_multiple_approved_items(
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
    state["approvals"] = [
        {
            "approval_id": "apv_planner",
            "plan_id": "pln_ready",
            "step_id": "step_1",
            "step": 1,
            "agent_id": "planner",
            "role": "planning",
            "task": "规划批量派发",
            "risk": "low",
            "status": "approved",
            "created_at": "2026-07-04T00:00:00+00:00",
        },
        {
            "approval_id": "apv_coder",
            "plan_id": "pln_ready",
            "step_id": "step_2",
            "step": 2,
            "agent_id": "coder",
            "role": "implementation",
            "task": "实现批量派发",
            "risk": "low",
            "status": "approved",
            "created_at": "2026-07-04T00:00:01+00:00",
        },
    ]
    store.save(state)

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["active_queue_source"] == "approval"
    assert payload["queue_card"]["approvals"]["approved"] == 2
    assert payload["operator_card"]["action_kind"] == "approval_dispatch_ready"
    assert payload["operator_card"]["command"] == "agentdeck approval dispatch-ready --confirm"
    assert payload["operator_card"]["explicit_command"] == "agentdeck approval dispatch-ready --confirm"
    assert payload["operator_card"]["blocker"] is None
    assert payload["operator_card"]["controls"][-1] == {
        "kind": "dispatch_ready",
        "label": "Dispatch ready approvals",
        "command": "agentdeck approval dispatch-ready --confirm",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
    }
    state_after = StateStore(root).load()
    assert state_after["messages"] == []
    assert state_after["jobs"] == []
    assert state_after.get("inbox", {}) == {}


def test_workbench_embeds_leader_inbox_card_when_worker_reply_returns_to_leader(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["inbox"] = {
        "planner": [
            {
                "inbox_id": "inb_planner_first",
                "event_type": "task_request",
                "message_id": "msg_planner",
                "attempt_id": "att_planner",
                "job_id": "job_planner",
                "reply_id": None,
                "from_actor": "leader",
                "to_agent": "planner",
                "task": "planner still has work",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ],
        "leader": [
            {
                "inbox_id": "inb_leader_reply",
                "event_type": "task_reply",
                "message_id": "msg_planner_done",
                "attempt_id": "att_planner_done",
                "job_id": "job_planner_done",
                "reply_id": "rep_planner_done",
                "from_actor": None,
                "from_agent": "planner",
                "to_agent": "leader",
                "task": "planner completed work",
                "status": "pending",
                "created_at": "2026-07-04T00:00:01+00:00",
            }
        ],
    }
    store.save(state)

    exit_code = cli.main(["workbench"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inbox_card"]["agent_id"] in {"planner", "leader"}
    assert payload["leader_inbox_card"]["agent_id"] == "leader"
    assert payload["leader_inbox_card"]["count"] == 1
    item = payload["leader_inbox_card"]["items"][0]
    assert item["event_type"] == "task_reply"
    assert item["reply_id"] == "rep_planner_done"
    assert item["trace_command"] == "agentdeck trace --id inb_leader_reply"
    assert item["ack_command"] == "agentdeck ack --agent leader --inbox-id inb_leader_reply"
    leader_inbox_controls = [
        item
        for item in payload["control_registry"]
        if item["scope"] == "inbox" and item["card"] == "leader_inbox_card" and item["agent_id"] == "leader"
    ]
    assert [item["kind"] for item in leader_inbox_controls] == ["preview", "ack"]
    assert leader_inbox_controls[-1] == {
        "scope": "inbox",
        "card": "leader_inbox_card",
        "kind": "ack",
        "label": "Acknowledge inbox head",
        "command": "agentdeck ack --agent leader --inbox-id inb_leader_reply",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
        "agent_id": "leader",
        "control_id": leader_inbox_controls[-1]["control_id"],
    }


def test_controls_outputs_command_palette_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["controls"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "control_registry"
    assert payload["title"] == "Command palette"
    assert payload["source_command"] == "agentdeck workbench"
    assert payload["default_command"] == "agentdeck controls"
    assert payload["item_count"] == len(payload["items"])
    assert payload["group_count"] == len(payload["groups"])
    assert payload["groups"][0] == {
        "group_id": "leader:leader_card",
        "scope": "leader",
        "card": "leader_card",
        "label": "Leader",
        "item_count": 7,
        "enabled_count": 5,
        "disabled_count": 2,
        "items": payload["items"][:7],
    }
    control_ids = [item["control_id"] for item in payload["items"]]
    assert len(control_ids) == len(set(control_ids))
    assert all(isinstance(control_id, str) and control_id for control_id in control_ids)
    assert payload["items"][0]["control_id"].startswith("leader:leader_card:chat:leader:")
    assert payload["groups"][0]["items"][0]["control_id"] == payload["items"][0]["control_id"]
    runtime_group = next(group for group in payload["groups"] if group["group_id"] == "runtime:runtime_card")
    assert runtime_group["label"] == "Runtime"
    assert len(runtime_group["items"]) == len([item for item in payload["items"] if item["scope"] == "runtime"])
    assert payload["items"][0] == {
        "scope": "leader",
        "card": "leader_card",
        "kind": "chat",
        "label": "Ask Leader",
        "command": "agentdeck leader chat --message <text>",
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "requires message text",
        "agent_id": "leader",
        "control_id": payload["items"][0]["control_id"],
    }
    assert {
        (item["scope"], item["card"], item["kind"], item["agent_id"])
        for item in payload["items"]
    } >= {
        ("leader", "leader_card", "continue", "leader"),
        ("leader", "leader_card", "refresh", "leader"),
        ("leader", "leader_card", "leader_status", "leader"),
        ("policy", "control_mode_card", "set_mode", None),
        ("role", "role_card", "assign_role", "planner"),
        ("runtime", "runtime_card", "spawn", "planner"),
        ("operator", "operator_card", "explicit", None),
    }
    refresh_item = next(
        item for item in payload["items"] if item["scope"] == "leader" and item["card"] == "leader_card" and item["kind"] == "refresh"
    )
    assert refresh_item["label"] == "Refresh Leader status"
    assert refresh_item["command"] == "agentdeck leader status"
    assert refresh_item["safety"] == "inspect"
    policy_item = next(item for item in payload["items"] if item["card"] == "control_mode_card" and item["kind"] == "set_mode")
    assert policy_item == {
        "scope": "policy",
        "card": "control_mode_card",
        "kind": "set_mode",
        "label": "Ask / inspect",
        "command": "agentdeck policy set-mode --mode ask",
        "safety": "inspect",
        "enabled": False,
        "blocker": "already current mode",
        "agent_id": None,
        "control_id": policy_item["control_id"],
    }
    approve_item = next(
        item
        for item in payload["items"]
        if item["card"] == "control_mode_card" and item["command"] == "agentdeck policy set-mode --mode approve"
    )
    assert approve_item["enabled"] is True
    assert approve_item["safety"] == "explicit_user"
    provider_items = [item for item in payload["items"] if item["scope"] == "provider"]
    assert any(item["kind"] == "set_provider" and "codex-cli" in item["command"] for item in provider_items)
    assert any(
        item["kind"] == "guarded_set_provider"
        and item["command"] == (
            "agentdeck leader set-provider --provider codex-cli --model codex-default --require-ready"
        )
        for item in provider_items
    )
    role_items = [item for item in payload["items"] if item["scope"] == "role"]
    assert role_items[0] == {
        "scope": "role",
        "card": "role_card",
        "kind": "assign_role",
        "label": "Assign role",
        "command": "agentdeck agent assign-role --agent planner --role <role> --role-prompt <role_prompt>",
        "safety": "explicit_user",
        "enabled": False,
        "blocker": "requires role and role_prompt",
        "agent_id": "planner",
        "control_id": role_items[0]["control_id"],
    }
    assert StateStore(root).load() == before


def test_controls_filters_by_scope_and_enabled_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["controls", "--scope", "runtime", "--enabled-only"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"] == {
        "scope": "runtime",
        "card": None,
        "query": None,
        "control_id": None,
        "enabled_only": True,
        "active_filter_keys": ["scope", "enabled_only"],
        "item_count_before_filter": 73,
    }
    assert payload["item_count"] == len(payload["items"])
    assert payload["group_count"] == len(payload["groups"])
    assert {item["scope"] for item in payload["items"]} == {"runtime"}
    assert all(item["enabled"] is True for item in payload["items"])
    assert [group["group_id"] for group in payload["groups"]] == ["runtime:runtime_card"]
    assert payload["groups"][0]["items"] == payload["items"]
    assert payload["groups"][0]["enabled_count"] == len(payload["items"])
    assert payload["groups"][0]["disabled_count"] == 0
    assert StateStore(root).load() == before


def test_controls_surfaces_agent_ready_card_controls_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["controls", "--card", "agent_ready_card"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"]["card"] == "agent_ready_card"
    assert payload["item_count"] == 3
    assert [item["kind"] for item in payload["items"]] == [
        "inspect",
        "spawn_ready",
        "refresh_runtime",
    ]
    assert payload["items"][1] == {
        "scope": "agent_ready",
        "card": "agent_ready_card",
        "kind": "spawn_ready",
        "label": "Spawn ready agents",
        "command": "agentdeck agent spawn-ready --confirm",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
        "agent_id": None,
        "control_id": payload["items"][1]["control_id"],
    }
    assert payload["groups"][0]["group_id"] == "agent_ready:agent_ready_card"
    assert StateStore(root).load() == before


def test_controls_surfaces_ledger_card_controls_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["controls", "--scope", "ledger"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"]["scope"] == "ledger"
    assert payload["item_count"] == 1
    assert payload["items"] == [
        {
            "scope": "ledger",
            "card": "ledger_card",
            "kind": "inspect",
            "label": "Inspect communication ledger",
            "command": "agentdeck workbench",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
            "agent_id": None,
            "control_id": payload["items"][0]["control_id"],
        }
    ]
    assert payload["selection"]["next_command"] is None
    assert payload["groups"][0]["group_id"] == "ledger:ledger_card"
    assert payload["groups"][0]["items"] == payload["items"]
    assert StateStore(root).load() == before


def test_controls_surfaces_provider_setup_commands_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["controls", "--scope", "provider", "--query", "codex login"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"]["scope"] == "provider"
    assert payload["filters"]["query"] == "codex login"
    assert payload["items"] == [
        {
            "scope": "provider",
            "card": "provider_health",
            "kind": "setup_provider",
            "label": "Setup Codex CLI",
            "command": "codex login",
            "safety": "explicit_user",
            "enabled": True,
            "blocker": None,
            "agent_id": "leader",
            "control_id": payload["items"][0]["control_id"],
        }
    ]
    assert payload["selection"]["next_command"] is None
    assert StateStore(root).load() == before


def test_controls_surfaces_terminal_session_select_pane_controls_when_filtered(
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
    store.save(state)
    before = store.load()

    exit_code = cli.main(["controls", "--scope", "terminal_session", "--enabled-only"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"] == {
        "scope": "terminal_session",
        "card": None,
        "query": None,
        "control_id": None,
        "enabled_only": True,
        "active_filter_keys": ["scope", "enabled_only"],
        "item_count_before_filter": 72,
    }
    assert [item["kind"] for item in payload["items"]] == [
        "attach_session",
        "open_controls",
        "refresh_runtime",
        "select_pane",
    ]
    select_pane_item = payload["items"][3]
    assert select_pane_item == {
        "scope": "terminal_session",
        "card": "terminal_session_card",
        "kind": "select_pane",
        "label": "Select pane",
        "command": "tmux -L agentdeck-repo select-pane -t %42",
        "safety": "inspect",
        "enabled": True,
        "blocker": None,
        "agent_id": "planner",
        "control_id": select_pane_item["control_id"],
    }
    assert payload["group_count"] == 1
    assert payload["groups"][0]["group_id"] == "terminal_session:terminal_session_card"
    assert payload["groups"][0]["items"] == payload["items"]
    assert StateStore(root).load() == before


def test_controls_filters_by_query_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["controls", "--query", "terminal"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"] == {
        "scope": None,
        "card": None,
        "query": "terminal",
        "control_id": None,
        "enabled_only": False,
        "active_filter_keys": ["query"],
        "item_count_before_filter": 73,
    }
    assert payload["item_count"] == len(payload["items"])
    assert payload["group_count"] == len(payload["groups"])
    assert payload["items"]
    assert all(
        "terminal"
        in " ".join(
            str(item.get(field, ""))
            for field in ["scope", "card", "kind", "label", "command", "agent_id"]
        ).lower()
        for item in payload["items"]
    )
    assert StateStore(root).load() == before


def test_controls_filters_by_control_id_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["controls"])

    assert exit_code == 0
    first_payload = json.loads(capsys.readouterr().out)
    selected_item = next(item for item in first_payload["items"] if item["enabled"] is True)
    control_id = selected_item["control_id"]

    exit_code = cli.main(["controls", "--control-id", control_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"] == {
        "scope": None,
        "card": None,
        "query": None,
        "control_id": control_id,
        "enabled_only": False,
        "active_filter_keys": ["control_id"],
        "item_count_before_filter": 73,
    }
    assert payload["item_count"] == 1
    assert payload["items"] == [selected_item]
    assert payload["selection"] == {
        "requested_control_id": control_id,
        "matched": True,
        "matched_count": 1,
        "selected_control": selected_item,
        "blocker": None,
        "next_command": selected_item["command"],
    }
    assert payload["group_count"] == 1
    assert payload["groups"][0]["items"] == payload["items"]
    assert StateStore(root).load() == before


def test_controls_reports_unmatched_control_id_selection_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["controls", "--control-id", "missing:control"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"] == {
        "scope": None,
        "card": None,
        "query": None,
        "control_id": "missing:control",
        "enabled_only": False,
        "active_filter_keys": ["control_id"],
        "item_count_before_filter": 73,
    }
    assert payload["item_count"] == 0
    assert payload["items"] == []
    assert payload["group_count"] == 0
    assert payload["groups"] == []
    assert payload["selection"] == {
        "requested_control_id": "missing:control",
        "matched": False,
        "matched_count": 0,
        "selected_control": None,
        "blocker": "control_id not found",
        "next_command": None,
    }
    assert StateStore(root).load() == before


def test_controls_reports_filtered_out_control_id_selection_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    before = store.load()

    exit_code = cli.main(["controls"])

    assert exit_code == 0
    first_payload = json.loads(capsys.readouterr().out)
    disabled_item = next(item for item in first_payload["items"] if item["enabled"] is False)

    exit_code = cli.main(["controls", "--control-id", disabled_item["control_id"], "--enabled-only"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"] == {
        "scope": None,
        "card": None,
        "query": None,
        "control_id": disabled_item["control_id"],
        "enabled_only": True,
        "active_filter_keys": ["control_id", "enabled_only"],
        "item_count_before_filter": 73,
    }
    assert payload["items"] == []
    assert payload["groups"] == []
    assert payload["selection"] == {
        "requested_control_id": disabled_item["control_id"],
        "matched": False,
        "matched_count": 0,
        "selected_control": None,
        "blocker": "control_id filtered out",
        "next_command": None,
    }
    assert StateStore(root).load() == before


def test_controls_surfaces_dispatch_ready_operator_kind(tmp_path, monkeypatch, capsys) -> None:
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
            "task": "规划批量派发",
            "risk": "low",
            "status": "approved",
            "created_at": "2026-07-04T00:00:00+00:00",
        },
        {
            "approval_id": "apv_coder",
            "plan_id": "pln_ready",
            "step_id": "step_2",
            "step": 2,
            "agent_id": "coder",
            "role": "implementation",
            "task": "实现批量派发",
            "risk": "low",
            "status": "approved",
            "created_at": "2026-07-04T00:00:01+00:00",
        },
    ]
    store.save(state)

    exit_code = cli.main(["controls"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    dispatch_item = next(
        item
        for item in payload["items"]
        if item["scope"] == "operator" and item["command"] == "agentdeck approval dispatch-ready --confirm"
    )
    assert dispatch_item == {
        "scope": "operator",
        "card": "operator_card",
        "kind": "dispatch_ready",
        "label": "Dispatch ready approvals",
        "command": "agentdeck approval dispatch-ready --confirm",
        "safety": "explicit_runtime",
        "enabled": True,
        "blocker": None,
        "agent_id": None,
        "control_id": dispatch_item["control_id"],
    }
    state_after = StateStore(root).load()
    assert state_after["messages"] == []
    assert state_after["jobs"] == []


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
    assert payload["dispatch_ready_command"] == expected["dispatch_ready_command"]
    assert payload["dispatch_ready_response_fields"] == expected["dispatch_ready_response_fields"]
    assert payload["dispatch_ready_result_fields"] == expected["dispatch_ready_result_fields"]
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
    assert payload["example_dispatch_ready_fields"] == payload["dispatch_ready_response_fields"]
    assert payload["example_dispatch_ready_result_fields"] == payload["dispatch_ready_result_fields"]
    assert set(payload["example_dispatch_ready_fields"]) == set(payload["example_dispatch_ready"])
    assert set(payload["example_dispatch_ready_result_fields"]) == set(payload["example_dispatch_ready"]["results"][0])
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


def test_inbox_and_ack_allow_logical_leader_mailbox(tmp_path, monkeypatch, capsys) -> None:
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
                "task": "planner completed work",
                "status": "pending",
                "created_at": "2026-07-04T00:00:00+00:00",
            }
        ]
    }
    store.save(state)

    exit_code = cli.main(["inbox", "--agent", "leader"])

    assert exit_code == 0
    inbox_payload = json.loads(capsys.readouterr().out)
    assert inbox_payload["agent_id"] == "leader"
    assert inbox_payload["count"] == 1
    assert inbox_payload["head_inbox_id"] == "inb_leader_reply"
    assert inbox_payload["items"][0]["ack_command"] == (
        "agentdeck ack --agent leader --inbox-id inb_leader_reply"
    )

    exit_code = cli.main(["ack", "--agent", "leader", "--inbox-id", "inb_leader_reply"])

    assert exit_code == 0
    ack_payload = json.loads(capsys.readouterr().out)
    assert ack_payload == {
        "ok": True,
        "agent_id": "leader",
        "inbox_id": "inb_leader_reply",
        "status": "acked",
    }
    state_after = StateStore(root).load()
    assert state_after["inbox"]["leader"][0]["status"] == "acked"


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
    assert payload["leader_backend_fields"] == expected["leader_backend_fields"]
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
    assert payload["example_leader_backend_fields"] == payload["leader_backend_fields"]
    assert set(payload["example_response_fields"]) == set(example)
    assert set(payload["example_leader_backend_fields"]) == set(example["leader_backend"])
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
    assert payload["example_agent_ready_card_fields"] == payload["agent_ready_card_fields"]
    assert set(payload["example_agent_ready_card_fields"]) == set(example["agent_ready_card"])
    assert payload["example_terminal_card_fields"] == payload["terminal_card_fields"]
    assert set(payload["example_terminal_card_fields"]) == set(example["terminal_card"])
    assert payload["example_skill_import_preview_card_fields"] == payload["skill_import_preview_card_fields"]
    assert set(payload["example_skill_import_preview_card_fields"]) == set(example["skill_import_preview_card"])
    assert payload["example_skill_load_preview_card_fields"] == payload["skill_load_preview_card_fields"]
    assert set(payload["example_skill_load_preview_card_fields"]) == set(example["skill_load_preview_card"])
    assert payload["example_skill_suggestions_card_fields"] == payload["skill_suggestions_card_fields"]
    assert set(payload["example_skill_suggestions_card_fields"]) == set(example["skill_suggestions_card"])
    assert payload["example_memory_suggestions_card_fields"] == payload["memory_suggestions_card_fields"]
    assert set(payload["example_memory_suggestions_card_fields"]) == set(example["memory_suggestions_card"])
    assert payload["example_workbench_control_registry_item_fields"] == (
        payload["workbench_control_registry_item_fields"]
    )
    assert set(payload["example_workbench_control_registry_item_fields"]) == set(
        example["workbench_card"]["control_registry"][0]
    )
    assert payload["example_control_registry_card_fields"] == payload["control_registry_card_fields"]
    assert set(payload["example_control_registry_card_fields"]) == set(example["control_registry_card"])
    assert example["control_registry_card"]["group_count"] == len(example["control_registry_card"]["groups"])
    assert example["control_registry_card"]["groups"][0]["items"][0] == example["control_registry_card"]["items"][0]
    assert example["trace_card"] is None
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
    assert payload["artifact_fields"] == expected["artifact_fields"]


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
    assert payload["example_artifact_fields"] == payload["artifact_fields"]
    assert set(payload["example_artifact_fields"]) == set(example["artifacts"][0])
    assert validate_trace_contract(example) == {"ok": True, "errors": []}
    assert example["message"]["message_id"] == "msg_example"
    assert example["attempts"][0]["attempt_id"] == "att_example"
    assert example["jobs"][0]["job_id"] == "job_example"
    assert example["replies"][0]["reply_id"] == "rep_example"
    assert example["artifacts"][0]["artifact_id"] == "art_example"
    assert {item["event_type"] for item in example["inbox_items"]} == {"task_request", "task_reply"}


def test_contract_trace_cli_matches_contract_module(capsys) -> None:
    cli.main(["contract", "trace", "--example"])

    payload = json.loads(capsys.readouterr().out)
    expected = trace_contract_response(Path(payload["contract_path"]), include_example=True)
    assert payload == expected


def test_trace_surfaces_plan_skill_provenance_for_dispatched_approval(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config = cli.load_config(root)
    store = StateStore(root)
    cli.main(["skills", "load", "--name", "planning", "--agent", "leader", "--purpose", "decompose task"])
    capsys.readouterr()
    skill_context = cli.asdict(store.project_view(config))["skills"]
    plan = {
        "goal": "skill-aware dispatch trace",
        "summary": "Dispatch one approval and preserve loaded skill provenance.",
        "approval_required": True,
        "dispatch_ready": False,
        "steps": [
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planner",
                "task": "Write a traceable plan",
                "risk": "low",
                "requires_approval": True,
            }
        ],
    }
    record = store.record_plan("skill trace task", "fake", "fake-plan", plan, skill_context=skill_context)
    approval = store.create_approvals_from_plan(str(record["plan_id"]))[0]
    store.decide_approval(str(approval["approval_id"]), "approved")
    records = store.create_dispatch_records(
        "leader",
        "planner",
        str(approval["task"]),
        "dispatch prompt",
        "%42",
    )
    store.mark_approval_dispatched(
        str(approval["approval_id"]),
        str(records["message"]["message_id"]),
        str(records["attempt"]["attempt_id"]),
        str(records["job"]["job_id"]),
    )

    exit_code = cli.main(["trace", "--id", str(records["message"]["message_id"])])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan"]["plan_id"] == record["plan_id"]
    assert payload["plan"]["task"] == "skill trace task"
    assert payload["plan"]["provider"] == "fake"
    assert payload["plan"]["skill_context"] == skill_context
    assert payload["plan"]["skill_context"]["items"][0]["name"] == "planning"
    assert "content_snapshot" not in payload["plan"]["skill_context"]["items"][0]
    assert validate_trace_contract(payload) == {"ok": True, "errors": []}


def test_trace_accepts_artifact_id_and_returns_artifacts(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["messages"] = [
        {
            "message_id": "msg_trace_artifact",
            "from_actor": "leader",
            "to_agent": "planner",
            "task": "写设计文档",
            "prompt": "prompt",
            "status": "replied",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    state["attempts"] = [
        {
            "attempt_id": "att_trace_artifact",
            "message_id": "msg_trace_artifact",
            "agent_id": "planner",
            "status": "completed",
            "created_at": "2026-07-04T00:00:01+00:00",
        }
    ]
    state["jobs"] = [
        {
            "job_id": "job_trace_artifact",
            "message_id": "msg_trace_artifact",
            "attempt_id": "att_trace_artifact",
            "agent_id": "planner",
            "pane_id": "%42",
            "status": "completed",
            "created_at": "2026-07-04T00:00:02+00:00",
        }
    ]
    state["replies"] = [
        {
            "reply_id": "rep_trace_artifact",
            "message_id": "msg_trace_artifact",
            "attempt_id": "att_trace_artifact",
            "job_id": "job_trace_artifact",
            "from_agent": "planner",
            "to_actor": "leader",
            "text": "status: completed\nfull_output_path: docs/architecture/trace.md",
            "created_at": "2026-07-04T00:00:03+00:00",
        }
    ]
    state["artifacts"] = [
        {
            "artifact_id": "art_trace",
            "message_id": "msg_trace_artifact",
            "attempt_id": "att_trace_artifact",
            "job_id": "job_trace_artifact",
            "reply_id": "rep_trace_artifact",
            "from_agent": "planner",
            "path": "docs/architecture/trace.md",
            "kind": "markdown",
            "status": "created",
            "created_at": "2026-07-04T00:00:04+00:00",
        }
    ]
    store.save(state)

    exit_code = cli.main(["trace", "--id", "art_trace"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["query_id"] == "art_trace"
    assert payload["message"]["message_id"] == "msg_trace_artifact"
    assert payload["artifacts"] == [
        {
            "artifact_id": "art_trace",
            "message_id": "msg_trace_artifact",
            "attempt_id": "att_trace_artifact",
            "job_id": "job_trace_artifact",
            "reply_id": "rep_trace_artifact",
            "from_agent": "planner",
            "path": "docs/architecture/trace.md",
            "kind": "markdown",
            "status": "created",
            "created_at": "2026-07-04T00:00:04+00:00",
        }
    ]
    assert validate_trace_contract(payload) == {"ok": True, "errors": []}


def test_artifacts_outputs_project_view_artifact_summary_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["artifacts"] = [
        {
            "artifact_id": "art_index",
            "message_id": "msg_index",
            "job_id": "job_index",
            "reply_id": "rep_index",
            "from_agent": "planner",
            "path": "docs/architecture/index.md",
            "kind": "markdown",
            "status": "created",
            "created_at": "2026-07-04T00:00:00+00:00",
        }
    ]
    store.save(state)

    exit_code = cli.main(["artifacts"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "schema_version": cli.PROJECT_VIEW_SCHEMA_VERSION,
        "artifacts_command": "agentdeck artifacts",
        "project_view_contract": "agentdeck contract project-view",
        "trace_contract": "agentdeck contract trace",
        "trace_command_template": "agentdeck trace --id <id>",
        "controls": [
            {
                "kind": "inspect",
                "label": "Inspect artifacts",
                "command": "agentdeck artifacts",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            }
        ],
        "artifacts": {
            "count": 1,
            "by_status": {"created": 1},
            "by_kind": {"markdown": 1},
            "items": [
                {
                    "artifact_id": "art_index",
                    "message_id": "msg_index",
                    "job_id": "job_index",
                    "reply_id": "rep_index",
                    "from_agent": "planner",
                    "path": "docs/architecture/index.md",
                    "kind": "markdown",
                    "status": "created",
                    "created_at": "2026-07-04T00:00:00+00:00",
                    "trace_command": "agentdeck trace --id msg_index",
                }
            ],
        },
    }
    assert StateStore(root).load() == state


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
            "prompt_skill_context": {
                "count": 1,
                "by_agent": {"planner": 1},
                "by_source": {"builtin": 1},
                "items": [
                    {
                        "load_id": "skl_demo",
                        "agent_id": "planner",
                        "purpose": "decompose worker task",
                        "name": "planning",
                        "source": "builtin",
                        "path": None,
                        "content_hash": "sha256:demo",
                        "description": "Break broad goals into reviewable steps.",
                        "required_tools": [],
                        "risk": "inspect",
                        "created_at": "2026-07-04T00:00:00+00:00",
                        "show_command": "agentdeck skills show --name planning",
                        "reload_command": (
                            "agentdeck skills load --name planning --agent planner "
                            "--purpose 'decompose worker task'"
                        ),
                    }
                ],
            },
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
    state["artifacts"] = [
        {
            "artifact_id": "art_demo",
            "message_id": "msg_demo",
            "job_id": "job_demo",
            "reply_id": "rep_demo",
            "from_agent": "planner",
            "path": "docs/plan.md",
            "kind": "markdown",
            "status": "created",
            "created_at": "2026-07-04T00:00:03+00:00",
        }
    ]
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
    assert payload["leader"]["leader_backend"] == {
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
    }
    assert payload["plans"]["count"] == 1
    assert payload["plans"]["items"][0] == {
        "plan_id": "pln_demo",
        "task": "构建 ProjectView",
        "status": "planned",
        "provider": "fake",
        "provider_backend": "local",
        "provider_transport": "local",
        "leader_backend": {
            "agent_id": "leader",
            "provider": "fake",
            "model": "local-plan",
            "provider_backend": "local",
            "provider_transport": "local",
            "reasoning_backend": "local-fake",
            "runtime_kind": "logical_leader",
            "pane_backed": False,
            "pane_id": None,
            "approval_required": True,
            "dispatch_ready": False,
        },
        "model": "local-plan",
        "dispatch_ready": False,
        "skill_context": {"count": 0, "by_agent": {}, "by_source": {}, "items": []},
        "step_count": 2,
        "created_at": "2026-07-04T00:00:00+00:00",
    }
    assert payload["approvals"]["count"] == 1
    assert payload["approvals"]["pending"] == 1
    assert payload["messages"]["by_status"] == {"replied": 1}
    assert payload["messages"]["items"][0]["trace_command"] == "agentdeck trace --id msg_demo"
    assert payload["messages"]["items"][0]["prompt_skill_context"]["items"][0]["name"] == "planning"
    assert "content_snapshot" not in payload["messages"]["items"][0]["prompt_skill_context"]["items"][0]
    assert payload["jobs"]["by_status"] == {"completed": 1}
    assert payload["jobs"]["items"][0]["trace_command"] == "agentdeck trace --id job_demo"
    assert payload["replies"]["items"][0]["reply_id"] == "rep_demo"
    assert payload["replies"]["items"][0]["trace_command"] == "agentdeck trace --id rep_demo"
    assert payload["artifacts"] == {
        "count": 1,
        "by_status": {"created": 1},
        "by_kind": {"markdown": 1},
        "items": [
            {
                "artifact_id": "art_demo",
                "message_id": "msg_demo",
                "job_id": "job_demo",
                "reply_id": "rep_demo",
                "from_agent": "planner",
                "path": "docs/plan.md",
                "kind": "markdown",
                "status": "created",
                "created_at": "2026-07-04T00:00:03+00:00",
                "trace_command": "agentdeck trace --id msg_demo",
            }
        ],
    }
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
    expected_leader = {*project_view_contract_payload(contract_path)["leader_fields"]}
    assert expected_leader <= set(payload["leader"])
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
            "reply_waiting": 0,
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


def test_agent_spawn_ready_requires_confirm_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    state_before = StateStore(root).load()
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["agent", "spawn-ready"])

    assert exit_code == 1
    assert "agent spawn-ready requires --confirm" in capsys.readouterr().err
    assert StateStore(root).load() == state_before
    assert fake.created_sessions == 0
    assert fake.spawned == []


def test_agent_spawn_ready_spawns_all_not_running_agents(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)
    state = store.load()
    state["agents"]["planner"] = {
        "agent_id": "planner",
        "pane_id": "%99",
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    store.save(state)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["agent", "spawn-ready", "--confirm"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "mode": "agent_spawn_ready",
        "requires_explicit_user": True,
        "safety": "explicit_runtime",
        "spawned_count": 2,
        "skipped_count": 1,
        "results": [
            {
                "agent_id": "planner",
                "status": "skipped",
                "previous_status": "running",
                "pane_id": "%99",
                "spawn_command": "agentdeck agent spawn --agent planner",
                "blocker": "agent already running",
            },
            {
                "agent_id": "coder",
                "status": "spawned",
                "previous_status": "configured",
                "pane_id": "%42",
                "spawn_command": "agentdeck agent spawn --agent coder",
                "blocker": None,
            },
            {
                "agent_id": "reviewer",
                "status": "spawned",
                "previous_status": "configured",
                "pane_id": "%42",
                "spawn_command": "agentdeck agent spawn --agent reviewer",
                "blocker": None,
            },
        ],
        "ready_command": "agentdeck agent ready",
    }
    assert fake.created_sessions == 1
    assert fake.spawned == [("coder", str(root)), ("reviewer", str(root))]

    state_after = StateStore(root).load()
    assert state_after["agents"]["planner"]["pane_id"] == "%99"
    assert state_after["agents"]["coder"]["pane_id"] == "%42"
    assert state_after["agents"]["coder"]["status"] == "running"
    assert state_after["agents"]["reviewer"]["pane_id"] == "%42"
    assert state_after["agents"]["reviewer"]["status"] == "running"

    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert events.count('"event_type": "agent_spawned"') == 2
    assert '"event_type": "agent_spawn_ready_completed"' in events


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


def test_agent_terminal_outputs_visible_pane_card_without_mutating_state(
    tmp_path, monkeypatch, capsys
) -> None:
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
    state_before = store.load()
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    exit_code = cli.main(["agent", "terminal", "--agent", "planner"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "mode": "agent_terminal",
        "agent_id": "planner",
        "role": "planning",
        "provider": "codex",
        "workspace_mode": "shared",
        "status": "running",
        "pane_id": "%42",
        "session_name": "agentdeck",
        "cwd": str(root),
        "attach_command": "tmux -L agentdeck-repo attach -t agentdeck",
        "select_pane_command": "tmux -L agentdeck-repo select-pane -t %42",
        "capture_command": "agentdeck agent capture --agent planner --lines 200",
        "send_command_template": "agentdeck agent send --agent planner --text <text>",
        "stop_command": "agentdeck agent stop --agent planner",
        "inbox_command": "agentdeck inbox --agent planner",
        "refresh_command": "agentdeck agent refresh",
        "controls": [
            {
                "kind": "terminal",
                "label": "Open terminal",
                "command": "tmux -L agentdeck-repo attach -t agentdeck",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "select_pane",
                "label": "Select pane",
                "command": "tmux -L agentdeck-repo select-pane -t %42",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
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
        ],
    }
    assert store.load() == state_before
    assert fake.created_sessions == 0
    assert fake.spawned == []
    assert fake.captured == []
    assert fake.sent == []
    assert fake.killed == []


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
