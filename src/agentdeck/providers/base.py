from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentdeck.models import ProjectConfig

PROVIDER_PLAN_REQUIRED_FIELDS = ("goal", "summary", "steps")
PROVIDER_PLAN_STRING_FIELDS = ("goal", "summary")
PROVIDER_PLAN_STEP_REQUIRED_FIELDS = ("step", "agent_id", "role", "task", "risk", "requires_approval")
PROVIDER_PLAN_STEP_STRING_FIELDS = ("agent_id", "role", "task", "risk")


@dataclass(frozen=True)
class LeaderPlanRequest:
    task: str
    config: ProjectConfig
    model: str | None = None


class LeaderProvider(Protocol):
    name: str

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        """Return a structured plan without dispatching work."""


def validate_provider_plan_schema(plan: object, config: ProjectConfig | None = None) -> dict[str, object]:
    if not isinstance(plan, dict):
        raise RuntimeError("provider plan content must be a JSON object")
    for field in PROVIDER_PLAN_REQUIRED_FIELDS:
        if field not in plan:
            raise RuntimeError(f"provider plan missing required field: {field}")
    for field in PROVIDER_PLAN_STRING_FIELDS:
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            raise RuntimeError(f"provider plan field {field} must be a non-empty string")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RuntimeError("provider plan must include non-empty steps")
    configured_agent_ids = {agent.agent_id for agent in config.agents} if config is not None else None
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise RuntimeError(f"provider plan step {index} must be an object")
        for field in PROVIDER_PLAN_STEP_REQUIRED_FIELDS:
            if field not in step:
                raise RuntimeError(f"provider plan step {index} missing required field: {field}")
        if not isinstance(step.get("step"), int) or step["step"] <= 0:
            raise RuntimeError(f"provider plan step {index} field step must be a positive integer")
        for field in PROVIDER_PLAN_STEP_STRING_FIELDS:
            if not isinstance(step.get(field), str) or not step[field].strip():
                raise RuntimeError(f"provider plan step {index} field {field} must be a non-empty string")
        if configured_agent_ids is not None and step["agent_id"] not in configured_agent_ids:
            raise RuntimeError(f"provider plan step {index} agent_id is not configured: {step['agent_id']}")
        if step.get("requires_approval") is not True:
            raise RuntimeError(f"provider plan step {index} must require approval")
    plan["approval_required"] = True
    plan["dispatch_ready"] = False
    return plan
