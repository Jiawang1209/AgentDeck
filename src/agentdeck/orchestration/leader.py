from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agentdeck.models import ProjectConfig
from agentdeck.providers import LeaderPlanRequest, LeaderPlanResult, LeaderProvider
from agentdeck.providers.plan_schema import (
    build_leader_generation_provenance,
    validate_provider_plan_schema,
)


class LeaderOrchestrator:
    """Plan-only skeleton for the Leader Agent.

    The first implementation returns a deterministic project plan so the CLI,
    state, runtime, and approval boundaries can stabilize before LLM calls are
    introduced.
    """

    def __init__(self, config: ProjectConfig, provider: LeaderProvider | None = None) -> None:
        self.config = config
        self.provider = provider

    def describe_team(self) -> dict[str, object]:
        return {
            "leader": asdict(self.config.leader),
            "workers": [asdict(agent) for agent in self.config.agents],
        }

    def plan(
        self,
        task: str,
        model: str | None = None,
        *,
        skill_context: dict[str, Any] | None = None,
        selected_agent_ids: tuple[str, ...] | None = None,
        step_count: int | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        return self.plan_result(
            task,
            model,
            skill_context=skill_context,
            selected_agent_ids=selected_agent_ids,
            step_count=step_count,
            timeout_seconds=timeout_seconds,
        ).plan

    def plan_result(
        self,
        task: str,
        model: str | None = None,
        *,
        skill_context: dict[str, Any] | None = None,
        selected_agent_ids: tuple[str, ...] | None = None,
        step_count: int | None = None,
        timeout_seconds: int | None = None,
    ) -> LeaderPlanResult:
        if self.provider is None:
            raise RuntimeError("leader provider is not configured")
        request = LeaderPlanRequest(
            task=task,
            config=self.config,
            model=model,
            skill_context=skill_context,
            selected_agent_ids=selected_agent_ids,
            step_count=step_count,
            timeout_seconds=timeout_seconds,
        )
        native_plan_result = getattr(self.provider, "plan_result", None)
        if callable(native_plan_result):
            result = native_plan_result(request)
            if not isinstance(result, LeaderPlanResult):
                raise TypeError("leader provider plan_result must return LeaderPlanResult")
            return result
        plan = validate_provider_plan_schema(
            self.provider.plan(request),
            config=self.config,
            selected_agent_ids=selected_agent_ids,
            step_count=step_count,
        )
        return LeaderPlanResult(
            plan=plan,
            leader_generation=build_leader_generation_provenance(
                request=request,
                provider=self.provider.name,
                constraint_mode=getattr(self.provider, "constraint_mode", "local"),
            ),
        )
