from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
from urllib.error import URLError

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.contracts import validate_mission_preview_contract
from agentdeck.mission_orchestration import create_mission_preview
from agentdeck.providers import LeaderPlanRequest
from agentdeck.state import StateStore


MESSAGE = "让 Codex 和 Claude 一人一句接龙百家姓，共8轮"


def eight_step_plan() -> dict[str, object]:
    steps = []
    for step in range(1, 9):
        agent_id, role = ("planner", "planning") if step % 2 else ("reviewer", "review")
        steps.append(
            {
                "step": step,
                "agent_id": agent_id,
                "role": role,
                "task": f"完成接龙第 {step} 轮",
                "risk": "requires human review before dispatch",
                "requires_approval": True,
            }
        )
    return {
        "goal": "完成八轮接龙",
        "summary": "Codex 与 Claude 严格串行交替执行。",
        "steps": steps,
        "approval_required": True,
        "dispatch_ready": False,
    }


class RecordingProvider:
    name = "fake"

    def __init__(self, plan: object | None = None) -> None:
        self.requests: list[LeaderPlanRequest] = []
        self.plan_result = eight_step_plan() if plan is None else plan

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        self.requests.append(request)
        return self.plan_result  # type: ignore[return-value]


class ExplodingProvider:
    name = "fake"

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        self.calls += 1
        raise self.error


def project(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    config_path.write_text(text, encoding="utf-8")
    return root, load_config(root), StateStore(root), config_path


def test_create_preview_selects_workers_freezes_serial_plan_and_never_touches_runtime(
    tmp_path, monkeypatch
) -> None:
    root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    provider = RecordingProvider()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config,
        store=store,
        provider=provider,
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    assert validate_mission_preview_contract(result) == {"ok": True, "errors": []}
    assert [item["provider"] for item in result["selected_agents"]] == ["codex", "claude"]
    assert [item["agent_id"] for item in result["selected_agents"]] == ["planner", "reviewer"]
    assert all(set(item) == {
        "agent_id", "provider", "role", "workspace_mode", "runtime_status",
        "effective_model", "model_source",
    } for item in result["selected_agents"])
    assert result["step_count"] == 8
    assert result["can_start"] is True
    assert result["confirmation_command"].endswith(f'批准执行 {result["mission_id"]}"')
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert [agent.agent_id for agent in request.config.agents] == ["planner", "reviewer"]
    assert "strictly serial" in request.task
    assert "exactly 8 steps" in request.task
    assert "planner, reviewer" in request.task
    assert "only after the previous step has completed" in request.task
    assert config_path.read_bytes() == config_before
    state = store.load()
    assert state.get("workflow_runs", []) == []
    assert state["jobs"] == []
    assert state["messages"] == []
    assert state["approvals"] == []
    assert state.get("inbox", {}) == {}
    assert state["skill_loads"] == []
    assert len(state["plans"]) == 1
    assert len(state["missions"]) == 1
    assert [event["event_type"] for event in store.list_events(limit=10)] == [
        "mission_preview_created"
    ]
    assert root.exists()


def test_create_preview_preserves_compact_loaded_leader_skill_context(tmp_path, monkeypatch) -> None:
    _root, config, store, _config_path = project(tmp_path)
    state = store.load()
    state["skill_loads"] = [
        {
            "load_id": "sld_demo",
            "agent_id": "leader",
            "purpose": "plan serial work",
            "name": "sequential-handoff",
            "source": "project",
            "path": ".agentdeck/skills/sequential-handoff/SKILL.md",
            "content_hash": "sha256:" + "a" * 64,
            "content_snapshot": "SECRET FULL SKILL CONTENT",
            "description": "Plan fixed handoffs",
            "required_tools": [],
            "risk": "low",
            "loaded_at": "2026-07-11T00:00:00+00:00",
        }
    ]
    store.save(state)
    provider = RecordingProvider()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config,
        store=store,
        provider=provider,
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    skill_context = provider.requests[0].skill_context
    assert skill_context is not None
    assert skill_context["count"] == 1
    assert "content_snapshot" not in repr(skill_context)
    plan_record = store.plan_by_id(result["plan_id"])
    assert plan_record["skill_context"] == skill_context
    assert "content_snapshot" not in repr(plan_record["skill_context"])
    assert len(store.load()["skill_loads"]) == 1


def test_create_preview_passes_selected_effective_models_without_rewriting_config(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="codex-cli", model="gpt-5.5"),
        agents=tuple(
            replace(item, command="claude --model opus-4.8")
            if item.agent_id == "reviewer"
            else item
            for item in config.agents
        ),
    )
    config_before = config_path.read_bytes()
    provider = RecordingProvider()
    provider.name = "codex-cli"
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config,
        store=store,
        provider=provider,
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    assert [
        (item["effective_model"], item["model_source"])
        for item in result["selected_agents"]
    ] == [
        ("gpt-5.5", "leader_inherited"),
        ("opus-4.8", "configured_command"),
    ]
    assert provider.requests[0].config.agents[0].command == "codex --model gpt-5.5"
    assert provider.requests[0].config.agents[1].command == "claude --model opus-4.8"
    assert config_path.read_bytes() == config_before


def test_create_preview_reuses_running_bindings_without_claiming_derived_models(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="codex-cli", model="gpt-5.5"),
    )
    state = store.load()
    state["agents"] = {
        agent_id: {
            "agent_id": agent_id,
            "status": "running",
            "pane_id": pane_id,
        }
        for agent_id, pane_id in (("planner", "%1"), ("reviewer", "%2"))
    }
    store.save(state)
    provider = RecordingProvider()
    provider.name = "codex-cli"
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config,
        store=store,
        provider=provider,
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    assert [
        (item["runtime_status"], item["effective_model"], item["model_source"])
        for item in result["selected_agents"]
    ] == [
        ("running", None, "running_binding"),
        ("running", None, "running_binding"),
    ]
    assert [
        (item["action"], item["effective_model"], item["model_source"])
        for item in result["startup_actions"]
    ] == [
        ("reuse", None, "running_binding"),
        ("reuse", None, "running_binding"),
    ]
    assert [item.command for item in provider.requests[0].config.agents] == [
        "codex",
        "claude",
    ]


def test_running_binding_without_pane_uses_spawn_derivation(tmp_path, monkeypatch) -> None:
    _root, config, store, _config_path = project(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="codex-cli", model="gpt-5.5"),
    )
    state = store.load()
    state["agents"] = {
        "planner": {"agent_id": "planner", "status": "running", "pane_id": None},
        "reviewer": {"agent_id": "reviewer", "status": "configured", "pane_id": None},
    }
    store.save(state)
    provider = RecordingProvider()
    provider.name = "codex-cli"
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
    )

    planner = result["selected_agents"][0]
    startup = result["startup_actions"][0]
    assert (planner["effective_model"], planner["model_source"]) == (
        "gpt-5.5",
        "leader_inherited",
    )
    assert startup["action"] == "spawn"
    assert provider.requests[0].config.agents[0].command == "codex --model gpt-5.5"


@pytest.mark.parametrize(
    "binding",
    [
        {"agent_id": "planner", "status": "corrupt", "pane_id": None},
        {"agent_id": "planner", "status": "running", "pane_id": ""},
        {"agent_id": "planner", "status": "running", "pane_id": {"secret": "BINDING_MARKER"}},
    ],
)
def test_malformed_selected_binding_fails_before_provider_and_any_write(
    tmp_path, monkeypatch, binding
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    state = store.load()
    state["agents"] = {"planner": binding}
    store.save(state)
    state_before = store.state_path.read_bytes()
    events_before = store.events_path.read_bytes()
    provider = RecordingProvider()

    with pytest.raises(ValueError, match="^mission preview binding invalid$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert "BINDING_MARKER" not in str(exc_info.value)
    assert provider.requests == []
    assert store.state_path.read_bytes() == state_before
    assert store.events_path.read_bytes() == events_before
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert config_path.read_bytes() == config_before


@pytest.mark.parametrize("case", ["duplicate", "fewer_than_two"])
def test_selection_blockers_fail_before_provider_and_any_write(
    tmp_path, monkeypatch, case
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    if case == "duplicate":
        config = replace(config, agents=(config.agents[0], config.agents[0], config.agents[2]))
    else:
        config = replace(config, agents=(config.agents[0],))
    provider = RecordingProvider()
    events_before = store.events_path.read_bytes()

    with pytest.raises(ValueError, match="^mission preview selection invalid$"):
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert provider.requests == []
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before
    assert config_path.read_bytes() == config_before


@pytest.mark.parametrize("provider_name", ["", "codex-cli"])
def test_provider_identity_invalid_fails_before_provider_and_any_write(
    tmp_path, monkeypatch, provider_name
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    provider = RecordingProvider()
    provider.name = provider_name
    events_before = store.events_path.read_bytes()

    with pytest.raises(ValueError, match="^mission preview provider invalid$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    if provider_name:
        assert provider_name not in str(exc_info.value)
    assert provider.requests == []
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before
    assert config_path.read_bytes() == config_before


@pytest.mark.parametrize(
    "configured_provider",
    ["fake", "codex-cli", "claude-cli", "deepseek"],
)
def test_provider_identity_is_canonicalized_for_payload_and_state(
    tmp_path, monkeypatch, configured_provider
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider=configured_provider),
    )
    provider = RecordingProvider()
    provider.name = f" {configured_provider.upper()} "
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
    )

    assert validate_mission_preview_contract(result) == {"ok": True, "errors": []}
    assert result["provider"] == configured_provider
    assert result["leader_backend"]["provider"] == configured_provider
    plan = store.plan_by_id(result["plan_id"])
    mission = store.mission_by_id(result["mission_id"])
    assert plan["provider"] == configured_provider
    assert plan["leader_backend"]["provider"] == configured_provider
    assert mission["provider"] == configured_provider
    assert mission["leader_backend"]["provider"] == configured_provider


def test_non_object_agents_state_fails_before_provider_and_any_business_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    state = store.load()
    state["agents"] = ["STATE_MARKER"]
    store.save(state)
    state_before = store.state_path.read_bytes()
    events_before = store.events_path.read_bytes()
    provider = RecordingProvider()

    with pytest.raises(ValueError, match="^mission preview state invalid$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert "STATE_MARKER" not in str(exc_info.value)
    assert provider.requests == []
    assert store.state_path.read_bytes() == state_before
    assert store.events_path.read_bytes() == events_before
    assert config_path.read_bytes() == config_before


def test_project_view_state_failure_is_sanitized_before_provider_and_any_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, config_path = project(tmp_path)
    config_before = config_path.read_bytes()
    events_before = store.events_path.read_bytes()
    provider = RecordingProvider()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")
    monkeypatch.setattr(
        "agentdeck.mission_orchestration._explicit_leader_skill_context",
        lambda *_args: (_ for _ in ()).throw(ValueError("PROJECT_VIEW_MARKER")),
    )

    with pytest.raises(ValueError, match="^mission preview state invalid$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert "PROJECT_VIEW_MARKER" not in str(exc_info.value)
    assert provider.requests == []
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before
    assert config_path.read_bytes() == config_before


def test_create_preview_reports_missing_command_without_echoing_command(tmp_path, monkeypatch) -> None:
    _root, config, store, _config_path = project(tmp_path)
    provider = RecordingProvider()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which",
        lambda command: "/bin/codex" if command == "codex" else None,
    )

    result = create_mission_preview(
        config=config,
        store=store,
        provider=provider,
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    assert validate_mission_preview_contract(result) == {"ok": True, "errors": []}
    assert result["can_start"] is False
    assert result["blockers"] == ["worker command not found: reviewer"]
    assert "claude" not in repr(result["blockers"])
    confirm = next(item for item in result["controls"] if item["kind"] == "execute")
    assert confirm["enabled"] is False
    assert confirm["blocker"] == result["blockers"][0]
    assert len(store.load()["plans"]) == 1
    assert len(store.load()["missions"]) == 1


def test_create_preview_compacts_malformed_command_blocker_without_echoing_command(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    agents = tuple(
        replace(item, command='claude "') if item.agent_id == "reviewer" else item
        for item in config.agents
    )
    config = replace(config, agents=agents)
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    result = create_mission_preview(
        config=config,
        store=store,
        provider=RecordingProvider(),
        user_message=MESSAGE,
        timeout_seconds=180,
    )

    assert result["can_start"] is False
    assert result["blockers"] == ["invalid worker command: reviewer"]
    assert 'claude "' not in repr(result["blockers"])


def test_invalid_provider_plan_fails_closed_before_any_state_or_event_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    provider = RecordingProvider({"goal": "bad", "summary": "bad", "steps": []})
    state_before = store.state_path.read_bytes() if store.state_path.exists() else None
    events_before = store.events_path.read_bytes()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    with pytest.raises(ValueError, match="^mission preview plan invalid$"):
        create_mission_preview(
            config=config,
            store=store,
            provider=provider,
            user_message=MESSAGE,
            timeout_seconds=180,
        )

    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before
    if state_before is not None:
        assert store.state_path.read_bytes() == state_before


def test_invalid_compact_summaries_fail_before_plan_record_write(
    tmp_path, monkeypatch
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    provider = RecordingProvider()
    events_before = store.events_path.read_bytes()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.selected_agent_summaries",
        lambda *_args: [{"agent_id": {"marker": "SUMMARY_MARKER"}}],
    )

    with pytest.raises(ValueError, match="^mission preview summaries invalid$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert "SUMMARY_MARKER" not in str(exc_info.value)
    assert len(provider.requests) == 1
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before


@pytest.mark.parametrize(
    "error",
    [
        URLError("URL_MARKER"),
        subprocess.TimeoutExpired("TIMEOUT_MARKER", 1),
        RuntimeError("RUNTIME_MARKER"),
        ValueError("VALUE_MARKER"),
    ],
)
def test_provider_exceptions_are_sanitized_and_write_nothing(
    tmp_path, monkeypatch, error
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    provider = ExplodingProvider(error)
    events_before = store.events_path.read_bytes()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    with pytest.raises(ValueError, match="^mission preview provider failed$") as exc_info:
        create_mission_preview(
            config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
        )

    assert "MARKER" not in str(exc_info.value)
    assert provider.calls == 1
    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parallel", False),
        ("dynamic_steps", []),
        ("dag", None),
        ("cycle", False),
    ],
)
def test_forbidden_plan_metadata_presence_fails_closed_before_any_write(
    tmp_path, monkeypatch, field, value
) -> None:
    _root, config, store, _config_path = project(tmp_path)
    plan = eight_step_plan()
    plan[field] = value
    provider = RecordingProvider(plan)
    state_before = store.state_path.read_bytes() if store.state_path.exists() else None
    events_before = store.events_path.read_bytes()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    with pytest.raises(ValueError, match="^mission preview plan invalid$"):
        create_mission_preview(
            config=config,
            store=store,
            provider=provider,
            user_message=MESSAGE,
            timeout_seconds=180,
        )

    assert store.list_plans() == []
    assert store.list_missions() == []
    assert store.events_path.read_bytes() == events_before
    if state_before is not None:
        assert store.state_path.read_bytes() == state_before


def test_duplicate_request_creates_distinct_audited_previews(tmp_path, monkeypatch) -> None:
    _root, config, store, _config_path = project(tmp_path)
    provider = RecordingProvider()
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")

    first = create_mission_preview(
        config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
    )
    second = create_mission_preview(
        config=config, store=store, provider=provider, user_message=MESSAGE, timeout_seconds=180
    )

    assert first["mission_id"] != second["mission_id"]
    assert first["plan_id"] != second["plan_id"]
    assert len(store.list_missions()) == 2
    assert len(store.list_plans()) == 2
    assert [event["event_type"] for event in store.list_events(limit=10)] == [
        "mission_preview_created",
        "mission_preview_created",
    ]
