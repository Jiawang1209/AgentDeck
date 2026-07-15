from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
import multiprocessing
from pathlib import Path

import pytest

from agentdeck.mission import (
    MAX_MISSION_STEPS,
    MISSION_SCHEMA_VERSION,
    MISSION_STATUSES,
    EffectiveMissionAgent,
    MissionSelection,
    daemon_mission_authority_state,
    effective_mission_agent,
    mission_intent,
    provider_family,
    select_mission_agents,
    selected_agent_summaries,
    startup_action_summaries,
    validate_mission_plan,
    normalize_mission_plan_metadata,
)
from agentdeck.models import (
    AgentRuntimeBinding,
    AgentSpec,
    LeaderConfig,
    ProjectConfig,
    RuntimeConfig,
)
from agentdeck.state import StateStore, leader_backend_identity
from agentdeck.mission import workbench_mission_card
from agentdeck.mission_authority import canonical_workflow_plan_hash
from agentdeck.orchestration.leader import LeaderOrchestrator
from agentdeck.providers.fake import FakeLeaderProvider


@pytest.mark.parametrize(
    ("mission", "expected"),
    [
        ({}, "legacy"),
        (
            {
                "snapshot_hash": "sha256:" + "a" * 64,
                "execution_snapshot": {"execution_hash": "sha256:" + "a" * 64},
                "daemon_admission": {
                    "state": "admitted",
                    "snapshot_hash": "sha256:" + "a" * 64,
                    "blocker": None,
                    "recovery_command": None,
                    "updated_at": "2026-07-14T00:00:00+00:00",
                },
            },
            "admitted",
        ),
        (
            {
                "snapshot_hash": "sha256:" + "a" * 64,
                "execution_snapshot": {"execution_hash": "sha256:" + "a" * 64},
                "daemon_admission": {
                    "state": "admitted",
                    "snapshot_hash": "sha256:" + "b" * 64,
                    "blocker": None,
                    "recovery_command": None,
                    "updated_at": "2026-07-14T00:00:00+00:00",
                },
            },
            "incomplete",
        ),
        (
            {
                "snapshot_hash": "sha256:" + "a" * 64,
                "execution_snapshot": {"execution_hash": "sha256:" + "b" * 64},
                "daemon_admission": {
                    "state": "admitted",
                    "snapshot_hash": "sha256:" + "a" * 64,
                    "blocker": None,
                    "recovery_command": None,
                    "updated_at": "2026-07-14T00:00:00+00:00",
                },
            },
            "incomplete",
        ),
        (
            {
                "snapshot_hash": "not-a-hash",
                "execution_snapshot": {"execution_hash": "not-a-hash"},
                "daemon_admission": {
                    "state": "admitted",
                    "snapshot_hash": "not-a-hash",
                    "blocker": None,
                    "recovery_command": None,
                    "updated_at": "2026-07-14T00:00:00+00:00",
                },
            },
            "incomplete",
        ),
        (
            {
                "snapshot_hash": "sha256:" + "a" * 64,
                "execution_snapshot": {"execution_hash": "sha256:" + "a" * 64},
                "daemon_admission": {
                    "state": "admitted",
                    "snapshot_hash": "sha256:" + "a" * 64,
                    "blocker": None,
                    "recovery_command": None,
                    "updated_at": "2026-07-14T00:00:00+00:00",
                    "extra": "forbidden",
                },
            },
            "incomplete",
        ),
        (
            {
                "snapshot_hash": "sha256:" + "a" * 64,
                "execution_snapshot": {"execution_hash": "sha256:" + "a" * 64},
                "daemon_admission": {
                    "state": "admitted",
                    "snapshot_hash": "sha256:" + "a" * 64,
                    "blocker": None,
                    "recovery_command": None,
                },
            },
            "incomplete",
        ),
        (
            {
                "snapshot_hash": "sha256:" + "a" * 64,
                "execution_snapshot": {"execution_hash": "sha256:" + "a" * 64},
                "daemon_admission": {
                    "state": "admitted",
                    "snapshot_hash": "sha256:" + "a" * 64,
                    "blocker": "unexpected",
                    "recovery_command": None,
                    "updated_at": "2026-07-14T00:00:00+00:00",
                },
            },
            "incomplete",
        ),
        (
            {
                "snapshot_hash": "sha256:" + "a" * 64,
                "execution_snapshot": {"execution_hash": "sha256:" + "a" * 64},
            },
            "incomplete",
        ),
    ],
)
def test_daemon_mission_authority_requires_exact_three_way_hash_binding(
    mission: dict[str, object], expected: str
) -> None:
    assert daemon_mission_authority_state(mission) == expected


def agent(
    agent_id: str,
    provider: str,
    *,
    command: str | None = None,
    workspace_mode: str = "shared",
) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        role=f"{agent_id} role",
        provider=provider,
        command=command or provider,
        workspace_mode=workspace_mode,
    )


def config(*agents: AgentSpec, leader: LeaderConfig | None = None) -> ProjectConfig:
    return ProjectConfig(
        name="mission-test",
        root="/tmp/mission-test",
        leader=leader or LeaderConfig(),
        agents=agents,
        runtime=RuntimeConfig(),
    )


def binding(
    agent_id: str,
    *,
    status: str = "configured",
    pane_id: str | None = None,
) -> AgentRuntimeBinding:
    return AgentRuntimeBinding(agent_id=agent_id, status=status, pane_id=pane_id)


def effective(spec: AgentSpec, model: str | None, source: str) -> EffectiveMissionAgent:
    return EffectiveMissionAgent(agent=spec, model=model, model_source=source)


def _claim_in_process(root: str, mission_id: str, start, queue) -> None:
    start.wait()
    result = StateStore(Path(root)).claim_mission_execution(
        mission_id,
        resuming=False,
        confirmed_at="2026-07-11T00:00:00+00:00",
    )
    queue.put(result["claimed"])


def mission_values() -> dict[str, object]:
    return {
        "user_message": "让 Codex 和 Claude 接龙",
        "can_start": True,
        "blockers": [],
        "provider": "fake",
        "model": "fake-plan",
        "leader_backend": leader_backend_identity("fake", "fake-plan"),
        "plan_id": "pln_demo",
        "plan_hash": "sha256:plan",
        "selected_agents": [
            {
                "agent_id": "planner",
                "provider": "codex-cli",
                "role": "planning",
                "workspace_mode": "shared",
                "runtime_status": "configured",
                "effective_model": "gpt-5.5",
                "model_source": "configured",
            },
            {
                "agent_id": "reviewer",
                "provider": "claude-cli",
                "role": "review",
                "workspace_mode": "shared",
                "runtime_status": "configured",
                "effective_model": "opus-4.8",
                "model_source": "configured",
            },
        ],
        "startup_actions": [
            {
                "agent_id": "planner",
                "action": "spawn",
                "runtime_status": "configured",
                "effective_model": "gpt-5.5",
                "model_source": "configured",
            },
            {
                "agent_id": "reviewer",
                "action": "spawn",
                "runtime_status": "configured",
                "effective_model": "opus-4.8",
                "model_source": "configured",
            },
        ],
        "step_count": 2,
        "timeout_seconds": 180,
    }


def _project_view_semantic_authority() -> dict[str, object]:
    return {
        "schema_version": "mission-semantic-authority/v1",
        "source_message_hash": "sha256:" + "a" * 64,
        "requirements": [
            {
                "requirement_id": "req_111111111111",
                "kind": "create",
                "target": "artifact.txt",
                "operation": "create",
                "literal": "draft-v1\n",
                "phase": "implementation",
                "agent_id": "planner",
                "sensitivity": "ordinary",
            },
            {
                "requirement_id": "req_222222222222",
                "kind": "review",
                "target": "artifact.txt",
                "operation": "review",
                "literal": "accepted-v2\n",
                "phase": "review",
                "agent_id": "reviewer",
                "sensitivity": "ordinary",
            },
            {
                "requirement_id": "req_333333333333",
                "kind": "state_transition",
                "target": "artifact.txt",
                "operation": "update",
                "before": {"content_equals": "draft-v1\n"},
                "after": {"content_equals": "accepted-v2\n"},
                "phase": "revision",
                "agent_id": "planner",
                "sensitivity": "ordinary",
            },
            {
                "requirement_id": "req_444444444444",
                "kind": "verify",
                "target": "artifact.txt",
                "operation": "verify",
                "literal": "accepted-v2\n",
                "phase": "acceptance",
                "agent_id": "reviewer",
                "sensitivity": "ordinary",
            },
        ],
        "proposed_effects": [],
        "unresolved": [],
    }


def test_project_view_and_workbench_share_compact_semantic_authority(
    tmp_path: Path,
) -> None:
    project_config = config(
        agent("planner", "codex-cli"),
        agent("reviewer", "claude-cli"),
        leader=LeaderConfig(provider="fake", model="fake-plan"),
    )
    store = StateStore(tmp_path)
    semantic_plan = LeaderOrchestrator(
        project_config, FakeLeaderProvider()
    ).plan_result(
        "raw context only",
        project_config.leader.model,
        selected_agent_ids=("planner", "reviewer"),
        step_count=4,
        semantic_authority=_project_view_semantic_authority(),
    ).plan
    plan = store.build_plan_record(
        "semantic task", "fake", "fake-plan", semantic_plan
    )
    values = mission_values()
    values.update(
        plan_id=plan["plan_id"],
        plan_hash=canonical_workflow_plan_hash(plan),
        step_count=4,
    )
    mission = store.build_mission_record(**values, semantic_plan=semantic_plan)
    state = store.load()
    state["plans"] = [plan]
    state["missions"] = [mission]
    store.save(state)

    raw_preview = store.project_view(project_config)
    raw_plan_card = raw_preview.plans["items"][0]["semantic_authority"]
    raw_mission_item = raw_preview.missions["items"][0]
    raw_mission_card = raw_mission_item["semantic_authority"]
    raw_workbench_card = workbench_mission_card(raw_mission_item, "agentdeck")
    raw_workbench_semantic = raw_workbench_card["semantic_authority"]
    assert raw_plan_card == raw_mission_card == raw_workbench_semantic
    assert raw_plan_card is not raw_mission_card
    assert raw_workbench_semantic is not raw_mission_card
    raw_plan_card["blockers"].append("projection-only mutation")
    assert raw_mission_card["blockers"] == []
    assert raw_workbench_semantic["blockers"] == []
    raw_workbench_semantic["blockers"].append("workbench-only mutation")
    assert raw_mission_card["blockers"] == []

    preview = asdict(raw_preview)
    plan_card = preview["plans"]["items"][0]["semantic_authority"]
    mission_card = preview["missions"]["items"][0]["semantic_authority"]
    plan_card["blockers"] = []
    assert plan_card == mission_card
    assert set(plan_card) == {
        "schema_version", "state", "authority_hash", "requirement_count",
        "proposed_effect_count", "unresolved_count", "compiled_step_count",
        "blockers",
    }
    assert plan_card["state"] == "preview"
    assert plan_card["compiled_step_count"] == 4
    assert plan_card["blockers"] == []
    rendered = repr(plan_card)
    for forbidden in (
        "artifact.txt", "draft-v1", "accepted-v2", "before", "after",
        "semantic_steps", "semantic_authority", "prompt", "secret_ref",
    ):
        assert forbidden not in rendered
    assert workbench_mission_card(
        preview["missions"]["items"][0], "agentdeck"
    )["semantic_authority"] == mission_card

    state = store.load()
    state["missions"][0]["preview_generation"] = True
    store.save(state)
    with pytest.raises(ValueError, match="semantic ProjectView provenance invalid"):
        store.project_view(project_config)

    state = store.load()
    state["missions"][0]["preview_generation"] = 1
    state["missions"][0]["confirmed_at"] = "not-a-timestamp"
    store.save(state)
    with pytest.raises(ValueError, match="semantic ProjectView provenance invalid"):
        store.project_view(project_config)

    state = store.load()
    state["missions"][0].update(
        status="preparing",
        confirmed_at="2026-07-15T00:00:00+00:00",
        blockers=["project daemon is not running"],
    )
    store.save(state)
    frozen = asdict(store.project_view(project_config))
    frozen_plan = frozen["plans"]["items"][0]["semantic_authority"]
    frozen_mission = frozen["missions"]["items"][0]["semantic_authority"]
    assert frozen_plan == frozen_mission
    assert frozen_plan["state"] == "frozen"
    assert frozen_plan["blockers"] == []

    state = store.load()
    state["plans"].append(deepcopy(state["plans"][0]))
    store.save(state)
    from agentdeck.contracts import validate_project_view_contract

    duplicate_view = asdict(store.project_view(project_config))
    result = validate_project_view_contract(duplicate_view)
    assert result["ok"] is False
    assert "plans.items plan_id must be unique" in result["errors"]


@pytest.mark.parametrize(
    "drift",
    ["semantic_authority_hash", "compiled_task_hashes", "preview_generation"],
)
def test_project_view_rejects_second_semantic_mission_drift(
    tmp_path: Path, drift: str
) -> None:
    project_config = config(
        agent("planner", "codex-cli"),
        agent("reviewer", "claude-cli"),
        leader=LeaderConfig(provider="fake", model="fake-plan"),
    )
    store = StateStore(tmp_path)
    semantic_plan = LeaderOrchestrator(
        project_config, FakeLeaderProvider()
    ).plan_result(
        "raw context only",
        project_config.leader.model,
        selected_agent_ids=("planner", "reviewer"),
        step_count=4,
        semantic_authority=_project_view_semantic_authority(),
    ).plan
    plan = store.build_plan_record(
        "semantic task", "fake", "fake-plan", semantic_plan
    )
    values = mission_values()
    values.update(
        plan_id=plan["plan_id"],
        plan_hash=canonical_workflow_plan_hash(plan),
        step_count=4,
    )
    mission = store.build_mission_record(**values, semantic_plan=semantic_plan)
    second = deepcopy(mission)
    second["mission_id"] = "mis_deadbeefcafe"
    if drift == "semantic_authority_hash":
        second[drift] = "sha256:" + "f" * 64
    elif drift == "compiled_task_hashes":
        second[drift] = ["sha256:" + "f" * 64] * 4
    else:
        second[drift] = 2
    state = store.load()
    state["plans"] = [plan]
    state["missions"] = [mission, second]
    store.save(state)

    with pytest.raises(ValueError, match="semantic ProjectView provenance invalid"):
        store.project_view(project_config)


def test_project_view_legacy_plan_and_mission_project_null_semantic_authority(
    tmp_path: Path,
) -> None:
    project_config = config(
        agent("planner", "codex-cli"),
        agent("reviewer", "claude-cli"),
        leader=LeaderConfig(provider="fake", model="fake-plan"),
    )
    store = StateStore(tmp_path)
    plan_body = {
        "goal": "legacy",
        "summary": "legacy",
        "steps": [
            {"step": 1, "agent_id": "planner", "role": "planning", "task": "one"},
            {"step": 2, "agent_id": "reviewer", "role": "review", "task": "two"},
        ],
    }
    plan = store.build_plan_record("legacy", "fake", "fake-plan", plan_body)
    values = mission_values()
    values.update(plan_id=plan["plan_id"], plan_hash=canonical_workflow_plan_hash(plan))
    mission = store.build_mission_record(**values)
    state = store.load()
    state["plans"] = [plan]
    state["missions"] = [mission]
    store.save(state)

    view = asdict(store.project_view(project_config))
    assert view["plans"]["items"][0]["semantic_authority"] is None
    assert view["missions"]["items"][0]["semantic_authority"] is None

    state = store.load()
    state["missions"] = {"credentials": "RAW_CONTAINER_SECRET"}
    store.save(state)
    malformed = asdict(store.project_view(project_config))
    assert malformed["missions"] == {
        "count": -1,
        "by_status": {},
        "latest_id": None,
        "items": [],
    }
    assert "RAW_CONTAINER_SECRET" not in repr(malformed)


@pytest.mark.parametrize("bad_plan_id", [[], {"nested": "value"}, True])
def test_project_view_bad_plan_id_is_bounded_contract_failure(
    tmp_path: Path, bad_plan_id: object
) -> None:
    from agentdeck.contracts import validate_project_view_contract

    project_config = config(
        agent("planner", "codex-cli"),
        agent("reviewer", "claude-cli"),
        leader=LeaderConfig(provider="fake", model="fake-plan"),
    )
    store = StateStore(tmp_path)
    plan_body = {
        "goal": "legacy",
        "summary": "legacy",
        "steps": [
            {"step": 1, "agent_id": "planner", "role": "planning", "task": "one"},
            {"step": 2, "agent_id": "reviewer", "role": "review", "task": "two"},
        ],
    }
    plan = store.build_plan_record("legacy", "fake", "fake-plan", plan_body)
    values = mission_values()
    values.update(plan_id=plan["plan_id"], plan_hash=canonical_workflow_plan_hash(plan))
    mission = store.build_mission_record(**values)
    plan["plan_id"] = deepcopy(bad_plan_id)
    mission["plan_id"] = deepcopy(bad_plan_id)
    state = store.load()
    state["plans"] = [plan]
    state["missions"] = [mission]
    store.save(state)

    payload = asdict(store.project_view(project_config))
    result = validate_project_view_contract(payload)

    assert result["ok"] is False
    assert any("plan_id" in error for error in result["errors"])


def test_state_store_default_and_legacy_state_are_mission_compatible(tmp_path) -> None:
    store = StateStore(tmp_path)

    assert store.load()["missions"] == []
    store.save({"agents": {}, "plans": []})
    assert store.list_missions() == []


def test_state_store_creates_updates_gets_and_lists_mission_without_events(tmp_path) -> None:
    store = StateStore(tmp_path)

    created = store.create_mission(**mission_values())

    assert created["mission_id"].startswith("mis_")
    assert created["schema_version"] == MISSION_SCHEMA_VERSION
    assert created["status"] == "pending_confirmation"
    assert created["stop_reason"] is None
    assert created["workflow_run_id"] is None
    assert created["current_step"] == 0
    assert created["confirmed_at"] is None
    assert created["completed_at"] is None
    assert created["created_at"] == created["updated_at"]
    assert store.mission_by_id(created["mission_id"]) == created
    assert store.list_missions() == [created]
    assert store.list_events(limit=10) == []

    preparing = store.update_mission(
        created["mission_id"],
        status="preparing",
        confirmed_at="2026-07-11T00:00:00+00:00",
    )
    updated = store.update_mission(
        created["mission_id"],
        status="running",
        workflow_run_id="wfr_demo",
        current_step=1,
    )

    assert updated["status"] == "running"
    assert updated["workflow_run_id"] == "wfr_demo"
    assert updated["current_step"] == 1
    assert updated["updated_at"] >= preparing["updated_at"]
    assert store.mission_by_id(created["mission_id"]) == updated
    assert store.list_events(limit=10) == []


def test_state_store_mission_unknown_ids_raise_key_error(tmp_path) -> None:
    store = StateStore(tmp_path)

    with pytest.raises(KeyError, match="mis_missing"):
        store.mission_by_id("mis_missing")
    with pytest.raises(KeyError, match="mis_missing"):
        store.update_mission("mis_missing", status="running")


def test_state_store_sets_mission_completion_timestamp_only_once(tmp_path) -> None:
    store = StateStore(tmp_path)
    created = store.create_mission(**mission_values())

    store.update_mission(created["mission_id"], status="preparing")
    store.update_mission(created["mission_id"], status="running")
    completed = store.update_mission(created["mission_id"], status="completed")
    completed_at = completed["completed_at"]
    completed_again = store.update_mission(created["mission_id"], status="completed")

    assert completed_at is not None
    assert completed_again["completed_at"] == completed_at


@pytest.mark.parametrize(
    "changes",
    [
        {"mission_id": "mis_000000000000"},
        {"schema_version": "mission/v0"},
        {"created_at": "2020-01-01T00:00:00+00:00"},
        {"provider": "codex-cli"},
        {"selected_agents": []},
        {"status": "unknown"},
        {"status": "failed"},
        {"current_step": -1},
        {"current_step": 3},
        {"current_step": True},
        {"workflow_run_id": {"command": "unsafe"}},
        {"stop_reason": {"full_prompt": "unsafe"}},
        {"confirmed_at": []},
        {"blockers": ["safe", {"credentials": "unsafe"}]},
        {"can_start": "yes"},
        {"completed_at": "2020-01-01T00:00:00+00:00"},
        {"unknown_field": "value"},
    ],
)
def test_update_mission_rejects_invalid_or_immutable_changes_without_writes(
    tmp_path, changes
) -> None:
    store = StateStore(tmp_path)
    mission = store.create_mission(**mission_values())
    state_before = store.state_path.read_bytes()

    with pytest.raises(ValueError):
        store.update_mission(mission["mission_id"], **changes)

    assert store.state_path.read_bytes() == state_before


@pytest.mark.parametrize("stopped_status", ["stopped", "interrupted"])
def test_mission_state_transitions_support_bounded_resume(tmp_path, stopped_status) -> None:
    store = StateStore(tmp_path)
    mission = store.create_mission(**mission_values())

    store.update_mission(mission["mission_id"], status="preparing")
    if stopped_status == "interrupted":
        store.update_mission(mission["mission_id"], status="running")
    stopped = store.update_mission(mission["mission_id"], status=stopped_status)
    resumed = store.update_mission(mission["mission_id"], status="running")

    assert stopped["status"] == stopped_status
    assert resumed["status"] == "running"


def test_completed_mission_is_terminal_and_cannot_reopen(tmp_path) -> None:
    store = StateStore(tmp_path)
    mission = store.create_mission(**mission_values())
    store.update_mission(mission["mission_id"], status="preparing")
    store.update_mission(mission["mission_id"], status="running")
    store.update_mission(mission["mission_id"], status="completed")
    state_before = store.state_path.read_bytes()

    with pytest.raises(ValueError, match="completed mission is terminal"):
        store.update_mission(mission["mission_id"], status="running")

    assert store.state_path.read_bytes() == state_before


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        ("pending_confirmation", "running"),
        ("pending_confirmation", "completed"),
        ("preparing", "completed"),
        ("preparing", "interrupted"),
        ("running", "preparing"),
        ("stopped", "completed"),
        ("interrupted", "completed"),
    ],
)
def test_mission_state_rejects_out_of_order_transitions_without_writes(
    tmp_path, current_status, target_status
) -> None:
    store = StateStore(tmp_path)
    mission = store.create_mission(**mission_values())
    if current_status in {"preparing", "running", "interrupted"}:
        store.update_mission(mission["mission_id"], status="preparing")
    if current_status in {"running", "interrupted"}:
        store.update_mission(mission["mission_id"], status="running")
    if current_status == "stopped":
        store.update_mission(mission["mission_id"], status="stopped")
    if current_status == "interrupted":
        store.update_mission(mission["mission_id"], status="interrupted")
    state_before = store.state_path.read_bytes()

    with pytest.raises(ValueError, match="invalid mission status transition"):
        store.update_mission(mission["mission_id"], status=target_status)

    assert store.state_path.read_bytes() == state_before


@pytest.mark.parametrize(
    ("provider", "model"),
    [("codex-cli", "gpt-5.5"), ("openai-compatible", "gpt-5.5-mini")],
)
def test_create_mission_accepts_coherent_cli_and_api_leader_backends(
    tmp_path, provider, model
) -> None:
    store = StateStore(tmp_path)
    values = mission_values()
    values.update(
        {
            "provider": provider,
            "model": model,
            "leader_backend": leader_backend_identity(provider, model),
        }
    )

    mission = store.create_mission(**values)

    assert mission["leader_backend"] == leader_backend_identity(provider, model)


def test_create_mission_rejects_incoherent_leader_backend_without_writes(tmp_path) -> None:
    store = StateStore(tmp_path)
    values = mission_values()
    values["leader_backend"] = leader_backend_identity("codex-cli", "gpt-5.5")

    with pytest.raises(ValueError, match="leader_backend must match provider and model"):
        store.create_mission(**values)

    assert store.load()["missions"] == []


def test_create_mission_rejects_startable_state_with_blockers_without_writes(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    values = mission_values()
    values["blockers"] = ["worker unavailable"]

    with pytest.raises(ValueError, match="can_start requires empty blockers"):
        store.create_mission(**values)

    assert store.load()["missions"] == []


@pytest.mark.parametrize("change_direction", ["add_blocker", "enable_start"])
def test_update_mission_rejects_startable_blocker_conflicts_without_writes(
    tmp_path, change_direction
) -> None:
    store = StateStore(tmp_path)
    values = mission_values()
    if change_direction == "enable_start":
        values.update({"can_start": False, "blockers": ["worker unavailable"]})
    mission = store.create_mission(**values)
    state_before = store.state_path.read_bytes()
    changes = (
        {"blockers": ["worker unavailable"]}
        if change_direction == "add_blocker"
        else {"can_start": True}
    )

    with pytest.raises(ValueError, match="can_start requires empty blockers"):
        store.update_mission(mission["mission_id"], **changes)

    assert store.state_path.read_bytes() == state_before


def test_mission_constants_and_provider_family_are_stable() -> None:
    assert MISSION_SCHEMA_VERSION == "mission/v1"
    assert MISSION_STATUSES == (
        "pending_confirmation",
        "preparing",
        "running",
        "completed",
        "stopped",
        "interrupted",
    )
    assert provider_family(" Codex-CLI ") == "codex"
    assert provider_family("CLAUDE") == "claude"
    assert provider_family("DeepSeek") == "deepseek"


def test_chinese_codex_claude_handoff_request_has_exact_intent() -> None:
    project = config(agent("planner", "codex"), agent("reviewer", "claude"))

    assert mission_intent("让 Codex 和 Claude 一人一句接龙百家姓，共8轮", project) == {
        "execution_requested": True,
        "requested_agent_ids": [],
        "requested_providers": ["codex", "claude"],
        "multi_agent": True,
    }


def test_english_run_and_provider_mentions_require_standalone_tokens() -> None:
    project = config(agent("planner", "codex"), agent("reviewer", "claude"))

    assert mission_intent("brunch codex claude", project) is None
    assert mission_intent("run codex-coder and claude", project) is None


@pytest.mark.parametrize(
    "message",
    [
        "怎么让多个智能体协作？",
        "查看多个智能体状态",
        "查看 skill 建议",
        "查看长期 memory",
        "trace planner 当前消息",
    ],
)
def test_non_mission_help_and_inspection_sentences_are_not_hijacked(message: str) -> None:
    project = config(agent("planner", "codex"), agent("reviewer", "claude"))

    assert mission_intent(message, project) is None


@pytest.mark.parametrize(
    "message",
    ["查看 memory", "skill list", "trace msg_x", "status"],
)
def test_explicit_inspection_route_shapes_are_not_missions(message: str) -> None:
    project = config(agent("planner", "codex"), agent("reviewer", "claude"))

    assert mission_intent(message, project) is None


@pytest.mark.parametrize(
    "message",
    [
        "状态：Codex 和 Claude 协作情况",
        "请帮助我查看 Codex 和 Claude 协作状态",
        "帮助我查看 Codex 和 Claude 协作状态",
    ],
)
def test_chinese_status_and_help_route_shapes_are_not_missions(message: str) -> None:
    project = config(agent("planner", "codex"), agent("reviewer", "claude"))

    assert mission_intent(message, project) is None


def test_execution_request_may_name_memory_as_its_task_subject() -> None:
    project = config(agent("planner", "codex"), agent("reviewer", "claude"))

    assert mission_intent("让 Codex 和 Claude 协作完成 memory 模块", project) == {
        "execution_requested": True,
        "requested_agent_ids": [],
        "requested_providers": ["codex", "claude"],
        "multi_agent": True,
    }


def test_mission_intent_detects_explicit_ids_in_message_order_with_safe_boundaries() -> None:
    project = config(
        agent("plan", "codex"),
        agent("planner", "codex"),
        agent("reviewer", "claude"),
    )

    intent = mission_intent("让 reviewer 和 planner 协作完成审阅，不要选择 plan-b", project)

    assert intent == {
        "execution_requested": True,
        "requested_agent_ids": ["reviewer", "planner"],
        "requested_providers": [],
        "multi_agent": True,
    }


def test_provider_selection_follows_explicit_provider_order() -> None:
    codex = agent("planner", "codex-cli")
    claude = agent("reviewer", "claude")

    selection = select_mission_agents(
        config(codex, claude),
        requested_agent_ids=[],
        requested_providers=["claude", "codex"],
        bindings={},
    )

    assert selection == MissionSelection(agents=(claude, codex), blockers=())


def test_missing_requested_provider_blocks_without_partial_selection() -> None:
    selection = select_mission_agents(
        config(agent("planner", "codex")),
        requested_agent_ids=[],
        requested_providers=["codex", "claude"],
        bindings={},
    )

    assert selection.agents == ()
    assert selection.blockers == ("requested provider not configured: claude",)


def test_generic_selection_chooses_first_two_distinct_provider_families() -> None:
    codex_first = agent("codex-first", "codex")
    codex_second = agent("codex-second", "codex-cli")
    claude = agent("claude-first", "claude-cli")

    selection = select_mission_agents(
        config(codex_first, codex_second, claude),
        requested_agent_ids=[],
        requested_providers=[],
        bindings={},
    )

    assert selection.agents == (codex_first, claude)
    assert selection.blockers == ()


def test_provider_candidate_ranking_prefers_running_then_shared_then_config_order() -> None:
    stopped_shared = agent("stopped-shared", "codex", workspace_mode="shared")
    running_worktree = agent("running-worktree", "codex", workspace_mode="worktree")
    running_shared_first = agent("running-shared-first", "codex", workspace_mode="shared")
    running_shared_second = agent("running-shared-second", "codex", workspace_mode="shared")
    claude = agent("claude", "claude")
    project = config(
        stopped_shared,
        running_worktree,
        running_shared_first,
        running_shared_second,
        claude,
    )
    bindings = {
        item.agent_id: binding(item.agent_id, status="running", pane_id=f"%{index}")
        for index, item in enumerate(
            (running_worktree, running_shared_first, running_shared_second), start=1
        )
    }

    selection = select_mission_agents(
        project,
        requested_agent_ids=[],
        requested_providers=["codex", "claude"],
        bindings=bindings,
    )

    assert selection.agents == (running_shared_first, claude)


def test_explicit_ids_win_over_provider_mentions_and_never_select_logical_leader() -> None:
    planner = agent("planner", "codex")
    reviewer = agent("reviewer", "claude")
    project = config(
        planner,
        reviewer,
        leader=LeaderConfig(agent_id="leader", provider="codex-cli", model="gpt-5.5"),
    )

    selection = select_mission_agents(
        project,
        requested_agent_ids=["reviewer", "planner"],
        requested_providers=["codex"],
        bindings={},
    )
    leader_selection = select_mission_agents(
        project,
        requested_agent_ids=["leader", "planner"],
        requested_providers=[],
        bindings={},
    )

    assert selection.agents == (reviewer, planner)
    assert selection.blockers == ()
    assert leader_selection.agents == ()
    assert leader_selection.blockers == ("requested agent is not a worker: leader",)


def test_unknown_explicit_id_and_single_worker_both_return_no_agents() -> None:
    planner = agent("planner", "codex")
    project = config(planner, agent("reviewer", "claude"))

    unknown = select_mission_agents(
        project,
        requested_agent_ids=["planner", "missing"],
        requested_providers=[],
        bindings={},
    )
    too_few = select_mission_agents(
        config(planner),
        requested_agent_ids=["planner"],
        requested_providers=[],
        bindings={},
    )

    assert unknown.agents == ()
    assert unknown.blockers == ("requested agent not configured: missing",)
    assert too_few.agents == ()
    assert too_few.blockers == ("mission requires at least two workers",)


@pytest.mark.parametrize(
    "project",
    [
        config(agent("worker", "codex"), agent("worker", "claude")),
        config(
            agent("worker", "codex", workspace_mode="shared"),
            agent("worker", "codex", workspace_mode="worktree"),
            agent("reviewer", "claude"),
        ),
    ],
    ids=["across-provider-families", "within-provider-family"],
)
def test_duplicate_configured_agent_ids_fail_closed(project: ProjectConfig) -> None:
    selection = select_mission_agents(
        project,
        requested_agent_ids=[],
        requested_providers=[],
        bindings={},
    )

    assert selection.agents == ()
    assert selection.blockers == ("duplicate configured agent_id: worker",)


def test_mission_selection_is_frozen() -> None:
    selection = MissionSelection(agents=(), blockers=())

    with pytest.raises(FrozenInstanceError):
        selection.blockers = ("changed",)  # type: ignore[misc]


def test_codex_worker_inherits_matching_cli_leader_model_without_mutating_original() -> None:
    worker = agent("planner", "codex", command="codex exec --full-auto")
    leader = LeaderConfig(provider="codex-cli", model="gpt-5.5")

    result = effective_mission_agent(worker, leader)

    assert result.agent.command == "codex exec --full-auto --model gpt-5.5"
    assert result.model == "gpt-5.5"
    assert result.model_source == "leader_inherited"
    assert result.blocker is None
    assert worker.command == "codex exec --full-auto"


@pytest.mark.parametrize(
    ("command", "expected_model"),
    [
        ("codex exec --model gpt-worker --full-auto", "gpt-worker"),
        ("codex exec -m 'gpt worker'", "gpt worker"),
        ("codex exec --model=gpt-worker", "gpt-worker"),
    ],
)
def test_explicit_worker_model_is_preserved(command: str, expected_model: str) -> None:
    worker = agent("planner", "codex", command=command)

    result = effective_mission_agent(
        worker,
        LeaderConfig(provider="codex-cli", model="gpt-5.5"),
    )

    assert result.agent is worker
    assert result.agent.command == command
    assert result.model == expected_model
    assert result.model_source == "configured_command"
    assert result.blocker is None


def test_worker_uses_provider_default_when_family_differs_from_leader() -> None:
    worker = agent("reviewer", "claude", command="claude --permission-mode plan")

    result = effective_mission_agent(
        worker,
        LeaderConfig(provider="codex-cli", model="gpt-5.5"),
    )

    assert result.agent is worker
    assert result.model is None
    assert result.model_source == "provider_default"
    assert result.blocker is None


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_non_cli_leader_provider_does_not_supply_worker_model(provider: str) -> None:
    worker = agent("worker", provider, command=f"{provider} run")

    result = effective_mission_agent(
        worker,
        LeaderConfig(provider=provider, model="leader-model"),
    )

    assert result.agent is worker
    assert result.agent.command == f"{provider} run"
    assert result.model is None
    assert result.model_source == "provider_default"
    assert result.blocker is None


def test_matching_cli_leader_provider_is_normalized_before_model_inheritance() -> None:
    worker = agent("planner", "codex", command="codex exec")

    result = effective_mission_agent(
        worker,
        LeaderConfig(provider=" Codex-CLI ", model="gpt-5.5"),
    )

    assert result.agent.command == "codex exec --model gpt-5.5"
    assert result.model == "gpt-5.5"
    assert result.model_source == "leader_inherited"
    assert result.blocker is None


def test_invalid_shell_quoting_blocks_model_resolution_without_mutating_command() -> None:
    worker = agent("planner", "codex", command="codex exec 'unterminated")

    result = effective_mission_agent(
        worker,
        LeaderConfig(provider="codex-cli", model="gpt-5.5"),
    )

    assert result.agent is worker
    assert result.model is None
    assert result.model_source == "provider_default"
    assert result.blocker == "invalid worker command: planner"


@pytest.mark.parametrize(
    "command",
    [
        "codex exec\necho unsafe",
        "codex exec && echo unsafe",
        "codex exec || echo unsafe",
        "codex exec; echo unsafe",
        "codex exec | tee output",
        "codex exec < input",
        "codex exec > output",
        "codex exec `whoami`",
        "codex exec $(whoami)",
        "codex exec $HOME",
        "MODEL=gpt-5.5 codex exec",
        "codex exec & echo unsafe",
        "codex exec # comment",
        "codex exec *.md",
        "codex exec ~/project",
        "codex exec 'path with spaces'",
    ],
)
def test_shell_sensitive_worker_commands_are_blocked_unchanged(command: str) -> None:
    worker = agent("planner", "codex", command=command)

    result = effective_mission_agent(
        worker,
        LeaderConfig(provider="codex-cli", model="gpt-5.5"),
    )

    assert result.agent is worker
    assert result.agent.command == command
    assert result.model is None
    assert result.model_source == "provider_default"
    assert result.blocker == "unsupported worker command: planner"


def test_worker_executable_must_match_configured_provider_family() -> None:
    worker = agent("planner", "codex", command="python worker.py")

    result = effective_mission_agent(
        worker,
        LeaderConfig(provider="codex-cli", model="gpt-5.5"),
    )

    assert result.agent is worker
    assert result.model is None
    assert result.model_source == "provider_default"
    assert result.blocker == "worker executable does not match provider: planner"


def test_duplicate_explicit_model_flags_are_blocked() -> None:
    worker = agent(
        "planner",
        "codex",
        command="codex exec --model first --model second",
    )

    result = effective_mission_agent(
        worker,
        LeaderConfig(provider="codex-cli", model="gpt-5.5"),
    )

    assert result.agent is worker
    assert result.model is None
    assert result.model_source == "provider_default"
    assert result.blocker == "duplicate worker model flag: planner"


@pytest.mark.parametrize(
    ("command", "expected_command"),
    [
        ("codex.exe exec", "codex.exe exec --model gpt-5.5"),
        ("/usr/local/bin/codex exec", "/usr/local/bin/codex exec --model gpt-5.5"),
    ],
)
def test_codex_executable_basename_allows_exe_and_absolute_path(
    command: str,
    expected_command: str,
) -> None:
    worker = agent("planner", "codex", command=command)

    result = effective_mission_agent(
        worker,
        LeaderConfig(provider="codex-cli", model="gpt-5.5"),
    )

    assert result.agent.command == expected_command
    assert result.model == "gpt-5.5"
    assert result.model_source == "leader_inherited"
    assert result.blocker is None


@pytest.mark.parametrize(
    "command",
    [
        "codex exec --model",
        "codex exec -m --full-auto",
        "codex exec --model=",
        "codex exec --model ''",
        "codex exec --model '   '",
    ],
)
def test_missing_or_empty_explicit_model_value_blocks_without_appending(command: str) -> None:
    worker = agent("planner", "codex", command=command)

    result = effective_mission_agent(
        worker,
        LeaderConfig(provider="codex-cli", model="gpt-5.5"),
    )

    assert result.agent is worker
    assert result.agent.command == command
    assert result.model is None
    assert result.model_source == "provider_default"
    assert result.blocker == "invalid worker model flag: planner"


def test_effective_mission_agent_is_frozen() -> None:
    result = effective(
        agent("planner", "codex"),
        model="gpt-5.5",
        source="leader_inherited",
    )

    with pytest.raises(FrozenInstanceError):
        result.model_source = "changed"  # type: ignore[misc]


def valid_plan() -> dict:
    return {
        "goal": "fixed handoff",
        "steps": [
            {"step": 1, "agent_id": "planner", "task": "first"},
            {"step": 2, "agent_id": "reviewer", "task": "second"},
        ],
    }


def test_validate_mission_plan_returns_valid_plan_unchanged() -> None:
    plan = valid_plan()

    assert validate_mission_plan(plan, ("planner", "reviewer"), 30) is plan


def test_validate_mission_plan_rejects_steps_above_shared_maximum() -> None:
    plan = {
        "steps": [
            {
                "step": step,
                "agent_id": "planner" if step % 2 else "reviewer",
            }
            for step in range(1, MAX_MISSION_STEPS + 2)
        ]
    }

    with pytest.raises(ValueError, match="maximum step count"):
        validate_mission_plan(plan, ("planner", "reviewer"), 30)


@pytest.mark.parametrize(
    ("plan", "selected", "timeout", "match"),
    [
        (None, ("planner", "reviewer"), 30, "plan must be a dict"),
        ({"steps": "bad"}, ("planner", "reviewer"), 30, "steps must be a list"),
        ({"steps": [{"step": 1, "agent_id": "planner"}]}, ("planner", "reviewer"), 30, "at least two steps"),
        (
            {"steps": [{"step": 1, "agent_id": "planner"}, {"step": 2, "agent_id": "planner"}]},
            ("planner", "reviewer"),
            30,
            "at least two selected agents",
        ),
        (
            {"steps": [{"step": 0, "agent_id": "planner"}, {"step": 2, "agent_id": "reviewer"}]},
            ("planner", "reviewer"),
            30,
            "numbered 1 through n",
        ),
        (
            {"steps": [{"step": 1, "agent_id": "planner"}, {"step": 2, "agent_id": "outsider"}]},
            ("planner", "reviewer"),
            30,
            "outside frozen selection",
        ),
        (valid_plan(), ("planner", "reviewer"), 0, "timeout must be positive"),
        ({**valid_plan(), "parallel": True}, ("planner", "reviewer"), 30, "dynamic or parallel"),
        ({**valid_plan(), "dag": {"edges": []}}, ("planner", "reviewer"), 30, "dynamic or parallel"),
        ({**valid_plan(), "cycle": 2}, ("planner", "reviewer"), 30, "dynamic or parallel"),
        ({**valid_plan(), "dynamic_steps": ["later"]}, ("planner", "reviewer"), 30, "dynamic or parallel"),
    ],
)
def test_validate_mission_plan_rejects_invalid_or_non_sequential_plans(
    plan: object,
    selected: tuple[str, ...],
    timeout: int,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        validate_mission_plan(plan, selected, timeout)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [None, "30", True])
def test_validate_mission_plan_rejects_non_numeric_timeouts(timeout: object) -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        validate_mission_plan(valid_plan(), ("planner", "reviewer"), timeout)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [1.5, float("inf"), float("nan")])
def test_validate_mission_plan_requires_a_finite_integer_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        validate_mission_plan(valid_plan(), ("planner", "reviewer"), timeout)


def test_validate_mission_plan_rejects_bool_step_numbers() -> None:
    plan = valid_plan()
    plan["steps"][0]["step"] = True

    with pytest.raises(ValueError, match="numbered 1 through n"):
        validate_mission_plan(plan, ("planner", "reviewer"), 30)


@pytest.mark.parametrize("key", ["parallel", "dag", "cycle", "dynamic_steps"])
def test_validate_mission_plan_rejects_forbidden_metadata_even_when_false(key: str) -> None:
    plan = {**valid_plan(), key: False}

    with pytest.raises(ValueError, match="dynamic or parallel metadata"):
        validate_mission_plan(plan, ("planner", "reviewer"), 30)


def test_validate_mission_plan_allows_unrelated_parallelism_metadata() -> None:
    plan = {**valid_plan(), "parallelism": "disabled"}

    assert validate_mission_plan(plan, ("planner", "reviewer"), 30) is plan


def test_normalize_mission_plan_metadata_is_canonical_and_does_not_mutate_input() -> None:
    plan = {
        "goal": "SECRET provider goal",
        "summary": "Every step requires human approval",
        "steps": [
            {"step": 1, "agent_id": "planner", "role": "planning", "task": "approve invoice"},
            {"step": 2, "agent_id": "reviewer", "role": "review", "task": "record result"},
        ],
        "approval_required": True,
        "dispatch_ready": False,
    }
    original = deepcopy(plan)

    normalized = normalize_mission_plan_metadata(plan, 2)

    assert normalized == {
        "goal": "Fixed sequential 2-step Mission.",
        "summary": "One overall Mission confirmation authorizes all 2 steps; no per-step approval.",
        "steps": original["steps"],
    }
    assert normalized["steps"] is not plan["steps"]
    assert normalized["steps"][0] is not plan["steps"][0]
    assert plan == original


def test_summaries_are_compact_and_startup_actions_distinguish_reuse_from_spawn() -> None:
    planner = agent("planner", "codex", command="codex --model secret-model")
    reviewer = agent("reviewer", "claude", command="claude --dangerously-skip-permissions")
    effective_agents = (
        effective(planner, "gpt-5.5", "leader_inherited"),
        effective(reviewer, None, "provider_default"),
    )
    bindings = {
        "planner": binding("planner", status="running", pane_id="%1"),
        "reviewer": binding("reviewer", status="stopped"),
    }

    selected = selected_agent_summaries(effective_agents, bindings)
    startup = startup_action_summaries(effective_agents, bindings)

    assert selected == [
        {
            "agent_id": "planner",
            "provider": "codex",
            "role": "planner role",
            "workspace_mode": "shared",
            "runtime_status": "running",
            "effective_model": "gpt-5.5",
            "model_source": "leader_inherited",
        },
        {
            "agent_id": "reviewer",
            "provider": "claude",
            "role": "reviewer role",
            "workspace_mode": "shared",
            "runtime_status": "stopped",
            "effective_model": None,
            "model_source": "provider_default",
        },
    ]
    assert startup == [
        {
            "agent_id": "planner",
            "action": "reuse",
            "runtime_status": "running",
            "effective_model": "gpt-5.5",
            "model_source": "leader_inherited",
        },
        {
            "agent_id": "reviewer",
            "action": "spawn",
            "runtime_status": "stopped",
            "effective_model": None,
            "model_source": "provider_default",
        },
    ]
    assert all("command" not in item and "env" not in item for item in selected + startup)


def test_blocked_effective_agent_projects_blocker_without_raw_command() -> None:
    planner = agent("planner", "codex", command="TOKEN=secret codex exec")
    blocked = EffectiveMissionAgent(
        agent=planner,
        model=None,
        model_source="provider_default",
        blocker="unsupported worker command: planner",
    )

    selected = selected_agent_summaries((blocked,), {})
    startup = startup_action_summaries((blocked,), {})

    assert selected[0]["blocker"] == "unsupported worker command: planner"
    assert startup[0]["blocker"] == "unsupported worker command: planner"
    assert "command" not in selected[0]
    assert "command" not in startup[0]


def test_claim_mission_execution_is_atomic_and_idempotent(tmp_path) -> None:
    store = StateStore(tmp_path)
    mission = store.create_mission(**mission_values())

    first = store.claim_mission_execution(
        mission["mission_id"], resuming=False, confirmed_at="2026-07-11T00:00:00+00:00"
    )
    second = store.claim_mission_execution(
        mission["mission_id"], resuming=False, confirmed_at="2026-07-11T00:00:01+00:00"
    )

    assert first["claimed"] is True
    assert second["claimed"] is False
    assert first["mission"]["status"] == second["mission"]["status"] == "preparing"
    assert second["mission"]["confirmed_at"] == "2026-07-11T00:00:00+00:00"


def test_claim_mission_execution_is_exclusive_across_processes(tmp_path) -> None:
    store = StateStore(tmp_path)
    mission = store.create_mission(**mission_values())
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    queue = context.Queue()
    processes = [
        context.Process(
            target=_claim_in_process,
            args=(str(tmp_path), mission["mission_id"], start, queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    results = [queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert sorted(results) == [False, True]
