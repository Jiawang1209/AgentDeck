from __future__ import annotations

from typing import Any

import pytest

from agentdeck.models import AgentSpec, LeaderConfig, ProjectConfig, RuntimeConfig
from agentdeck.orchestration.split_planning import (
    SplitPlanningError,
    run_split_planning,
)
from agentdeck.providers.base import LeaderPlanRequest
from agentdeck.providers.fake import FakeLeaderProvider
from agentdeck.providers.planner_brief import PLANNER_BRIEF_SCHEMA_VERSION


def _config() -> ProjectConfig:
    return ProjectConfig(
        name="demo",
        root="/tmp/demo",
        leader=LeaderConfig(),
        agents=(
            AgentSpec(agent_id="planner", role="planning", provider="codex", command="codex"),
            AgentSpec(agent_id="coder", role="coding", provider="claude", command="claude"),
        ),
        runtime=RuntimeConfig(),
    )


class _SpyOrchestratorProvider(FakeLeaderProvider):
    def __init__(self) -> None:
        self.requests: list[LeaderPlanRequest] = []

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        self.requests.append(request)
        return super().plan(request)


class _NoBriefProvider(FakeLeaderProvider):
    plan_brief = None


class _BadBriefProvider(FakeLeaderProvider):
    def plan_brief(self, **_kwargs: Any) -> dict[str, object]:
        return {"goal": "", "acceptance_criteria": [], "risks": [], "macro_steps": []}


class _ExplodingProvider(FakeLeaderProvider):
    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        raise RuntimeError("orchestrator backend unavailable")


def test_split_planning_happy_path_returns_single_plan_with_brief() -> None:
    orchestrator_provider = _SpyOrchestratorProvider()
    result = run_split_planning(
        _config(),
        "自动化 README 更新",
        planner_provider=FakeLeaderProvider(),
        orchestrator_provider=orchestrator_provider,
        planner_model="fake-planner",
        orchestrator_model="fake-orchestrator",
    )
    plan = result.plan
    assert plan["goal"]
    assert plan["steps"]
    for step in plan["steps"]:
        assert step["requires_approval"] is True
    brief = result.planner_brief
    assert brief["schema_version"] == PLANNER_BRIEF_SCHEMA_VERSION
    assert isinstance(brief["content_hash"], str) and len(brief["content_hash"]) == 64
    assert brief["acceptance_criteria"]
    assert result.planner_backend == ("fake", "fake-planner")
    assert result.orchestrator_backend == ("fake", "fake-orchestrator")
    request = orchestrator_provider.requests[0]
    assert request.planner_brief is not None
    assert request.planner_brief["goal"] == brief["goal"]
    assert "content_hash" not in request.planner_brief


def test_planner_without_brief_capability_fails_before_orchestrator() -> None:
    orchestrator_provider = _SpyOrchestratorProvider()
    with pytest.raises(SplitPlanningError) as error:
        run_split_planning(
            _config(),
            "目标",
            planner_provider=_NoBriefProvider(),
            orchestrator_provider=orchestrator_provider,
            planner_model="fake-planner",
            orchestrator_model="fake-orchestrator",
        )
    assert error.value.stage == "planner"
    assert orchestrator_provider.requests == []


def test_invalid_brief_fails_planner_stage() -> None:
    orchestrator_provider = _SpyOrchestratorProvider()
    with pytest.raises(SplitPlanningError) as error:
        run_split_planning(
            _config(),
            "目标",
            planner_provider=_BadBriefProvider(),
            orchestrator_provider=orchestrator_provider,
            planner_model="fake-planner",
            orchestrator_model="fake-orchestrator",
        )
    assert error.value.stage == "planner"
    assert orchestrator_provider.requests == []


def test_orchestrator_failure_reports_orchestrator_stage() -> None:
    with pytest.raises(SplitPlanningError) as error:
        run_split_planning(
            _config(),
            "目标",
            planner_provider=FakeLeaderProvider(),
            orchestrator_provider=_ExplodingProvider(),
            planner_model="fake-planner",
            orchestrator_model="fake-orchestrator",
        )
    assert error.value.stage == "orchestrator"


def test_leader_plan_request_default_has_no_planner_brief() -> None:
    request = LeaderPlanRequest(task="t", config=_config())
    assert request.planner_brief is None


def test_fake_provider_plan_brief_is_valid_and_deterministic() -> None:
    provider = FakeLeaderProvider()
    first = provider.plan_brief(task="目标", model="fake-planner", skill_context=None)
    second = provider.plan_brief(task="目标", model="fake-planner", skill_context=None)
    assert first == second
    assert first["goal"]
    assert first["acceptance_criteria"]
    assert first["macro_steps"]
