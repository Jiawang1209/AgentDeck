from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentdeck.models import ProjectConfig

PROVIDER_PLAN_STEP_REQUIRED_FIELDS = ("step", "agent_id", "role", "task", "risk", "requires_approval")


@dataclass(frozen=True)
class LeaderPlanRequest:
    task: str
    config: ProjectConfig
    model: str | None = None


class LeaderProvider(Protocol):
    name: str

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        """Return a structured plan without dispatching work."""


def validate_provider_plan_schema(plan: object) -> dict[str, object]:
    if not isinstance(plan, dict):
        raise RuntimeError("provider plan content must be a JSON object")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RuntimeError("provider plan must include non-empty steps")
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise RuntimeError(f"provider plan step {index} must be an object")
        for field in PROVIDER_PLAN_STEP_REQUIRED_FIELDS:
            if field not in step:
                raise RuntimeError(f"provider plan step {index} missing required field: {field}")
        if step.get("requires_approval") is not True:
            raise RuntimeError(f"provider plan step {index} must require approval")
    plan["approval_required"] = True
    plan["dispatch_ready"] = False
    return plan
