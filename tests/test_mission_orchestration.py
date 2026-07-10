from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
from urllib.error import URLError

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.contracts import validate_mission_preview_contract
from agentdeck.mission_orchestration import (
    create_mission_preview,
    mission_status_payload,
    resume_mission,
    run_mission,
)
from agentdeck.runtime.readiness import WorkerReadiness, WorkerReadinessBatch
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


class MissionBackend:
    def __init__(self, *, fail_spawn_for: str | None = None) -> None:
        self.fail_spawn_for = fail_spawn_for
        self.created = 0
        self.spawned: list[str] = []
        self.panes: dict[str, str] = {}

    def create_session(self, config) -> None:
        self.created += 1

    def spawn_agent(self, config, agent, cwd: str) -> str:
        self.spawned.append(agent.agent_id)
        if agent.agent_id == self.fail_spawn_for:
            raise RuntimeError("SECRET spawn detail")
        pane = f"%{len(self.panes) + 1}"
        self.panes[agent.agent_id] = pane
        return pane

    def pane_exists(self, config, pane_id: str) -> bool:
        return pane_id in self.panes.values()


class CorrelatedMissionBackend(MissionBackend):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[str, str]] = []

    def send_input(self, config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def capture_output(self, config, pane_id: str, lines: int = 200) -> str:
        prompts = [text for target, text in self.sent if target == pane_id]
        if not prompts:
            agent_id = next(agent for agent, pane in self.panes.items() if pane == pane_id)
            if agent_id == "planner":
                return "OpenAI Codex\nmodel: fake\n› Ask Codex anything"
            return "Claude Code\n❯ Try a task\n100% context left"
        token = next(
            line.rsplit(":", 1)[1].strip()
            for line in prompts[-1].splitlines()
            if line.startswith("Complete only this task. Use this handoff token exactly:")
        )
        return (
            f"handoff_token: {token}\n"
            "status: completed\n"
            f"summary: completed {pane_id}\n"
            "verification: correlated fake\n"
            "risks: none\n"
            "next_steps: continue"
        )


def seeded_mission(tmp_path: Path, monkeypatch):
    root, config, store, _ = project(tmp_path)
    monkeypatch.setattr("agentdeck.mission_orchestration.shutil.which", lambda command: f"/bin/{command}")
    preview = create_mission_preview(
        config=config,
        store=store,
        provider=RecordingProvider(),
        user_message=MESSAGE,
        timeout_seconds=180,
    )
    return root, config, store, preview


def ready_batch(*agent_ids: str) -> WorkerReadinessBatch:
    return WorkerReadinessBatch(
        True,
        tuple(WorkerReadiness(agent_id, "codex", "ready", None) for agent_id in agent_ids),
    )


def test_run_mission_spawns_only_frozen_workers_and_completes(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.wait_for_worker_readiness",
        lambda **kwargs: ready_batch("planner", "reviewer"),
    )
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.run_sequential_workflow",
        lambda **kwargs: store.update_workflow_run(
            kwargs["run_id"], status="completed", current_step=8, turns=[]
        ),
    )

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["status"] == "completed"
    assert backend.spawned == ["planner", "reviewer"]
    assert "coder" not in backend.spawned
    assert len(store.load()["workflow_runs"]) == 1
    assert result["workflow_run_id"].startswith("wfr_")


def test_plan_drift_stops_before_runtime_and_is_not_resumable(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    state = store.load()
    state["plans"][0]["plan"]["steps"][0]["task"] = "drifted"
    store.save(state)
    backend = MissionBackend()

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["status"] == "stopped"
    assert result["stop_reason"] == "plan_drift"
    assert result["can_resume"] is False
    assert backend.created == 0
    assert backend.spawned == []
    assert store.load().get("workflow_runs", []) == []


def test_partial_spawn_failure_keeps_first_binding_and_dispatches_zero(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend(fail_spawn_for="reviewer")

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["stop_reason"] == "worker_start_failed"
    assert store.agent_binding("planner")["pane_id"] == "%1"
    assert store.load().get("workflow_runs", []) == []
    assert "SECRET" not in repr(result)
    assert "SECRET" not in repr(store.all_events())


def test_setup_required_stops_before_workflow_dispatch(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.wait_for_worker_readiness",
        lambda **kwargs: WorkerReadinessBatch(
            False,
            (
                WorkerReadiness("planner", "codex", "ready", None),
                WorkerReadiness("reviewer", "claude", "setup_required", "SECRET login screen"),
            ),
        ),
    )

    result = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["stop_reason"] == "worker_setup_required"
    assert result["can_resume"] is True
    assert store.load().get("workflow_runs", []) == []
    assert "SECRET" not in repr(result)
    assert "SECRET" not in repr(store.all_events())


def test_resume_reuses_existing_workflow_and_duplicate_completion_is_idempotent(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = MissionBackend()
    for agent_id, pane_id in (("planner", "%1"), ("reviewer", "%2")):
        backend.panes[agent_id] = pane_id
        from agentdeck.models import AgentRuntimeBinding
        store.bind_agent(AgentRuntimeBinding(agent_id, pane_id, config.runtime.session_name, config.root, "running"))
    run = store.create_workflow_run(
        plan_id=preview["plan_id"],
        plan_hash=preview["plan_hash"],
        timeout_seconds=180,
        authorized_steps=__import__("agentdeck.workflow", fromlist=["authorized_steps"]).authorized_steps(store.plan_by_id(preview["plan_id"])),
    )
    store.update_workflow_run(run["run_id"], status="interrupted", stop_reason="interrupted")
    from agentdeck.models import utc_now
    store.update_mission(preview["mission_id"], status="preparing", confirmed_at=utc_now())
    store.update_mission(preview["mission_id"], status="running", workflow_run_id=run["run_id"])
    store.update_mission(preview["mission_id"], status="interrupted", stop_reason="interrupted")
    monkeypatch.setattr("agentdeck.mission_orchestration.wait_for_worker_readiness", lambda **kwargs: ready_batch("planner", "reviewer"))
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.run_sequential_workflow",
        lambda **kwargs: store.update_workflow_run(kwargs["run_id"], status="completed", current_step=8, turns=[]),
    )

    result = resume_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])
    again = run_mission(config=config, store=store, backend=backend, mission_id=preview["mission_id"])

    assert result["workflow_run_id"] == run["run_id"]
    assert again["status"] == "completed"
    assert len(store.load()["workflow_runs"]) == 1
    assert backend.spawned == []


def test_mission_status_payload_is_contract_valid_and_read_only(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    before = store.state_path.read_bytes()

    payload = mission_status_payload(config, store, store.mission_by_id(preview["mission_id"]))

    from agentdeck.contracts import validate_mission_status_contract
    assert validate_mission_status_contract(payload) == {"ok": True, "errors": []}
    assert store.state_path.read_bytes() == before


def test_run_mission_executes_real_eight_turn_correlated_workflow(tmp_path, monkeypatch) -> None:
    _root, config, store, preview = seeded_mission(tmp_path, monkeypatch)
    backend = CorrelatedMissionBackend()

    result = run_mission(
        config=config,
        store=store,
        backend=backend,
        mission_id=preview["mission_id"],
        readiness_timeout_seconds=2,
    )

    workflow = store.workflow_run_by_id(result["workflow_run_id"])
    assert result["status"] == "completed"
    assert len(workflow["turns"]) == 8
    assert len(backend.sent) == 8
    assert [turn["agent_id"] for turn in workflow["turns"]] == [
        "planner", "reviewer", "planner", "reviewer",
        "planner", "reviewer", "planner", "reviewer",
    ]
    assert len(store.load()["messages"]) == 8
    assert len(store.load()["replies"]) == 8


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
