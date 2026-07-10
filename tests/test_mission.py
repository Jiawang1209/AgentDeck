from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentdeck.mission import (
    MISSION_SCHEMA_VERSION,
    MISSION_STATUSES,
    EffectiveMissionAgent,
    MissionSelection,
    effective_mission_agent,
    mission_intent,
    provider_family,
    select_mission_agents,
    selected_agent_summaries,
    startup_action_summaries,
    validate_mission_plan,
)
from agentdeck.models import (
    AgentRuntimeBinding,
    AgentSpec,
    LeaderConfig,
    ProjectConfig,
    RuntimeConfig,
)


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
def test_validate_mission_plan_allows_false_fixed_sequence_metadata(key: str) -> None:
    plan = {**valid_plan(), key: False}

    assert validate_mission_plan(plan, ("planner", "reviewer"), 30) is plan


def test_validate_mission_plan_allows_unrelated_parallelism_metadata() -> None:
    plan = {**valid_plan(), "parallelism": "disabled"}

    assert validate_mission_plan(plan, ("planner", "reviewer"), 30) is plan


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
