from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol

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
    skill_context: dict[str, Any] | None = None


class LeaderProvider(Protocol):
    name: str

    def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
        """Return a structured plan without dispatching work."""


def leader_skill_context_prompt_lines(skill_context: dict[str, Any] | None) -> list[str]:
    if not isinstance(skill_context, dict):
        return [
            "Loaded skills: {\"count\": 0, \"by_agent\": {}, \"by_source\": {}, \"items\": []}",
            "Loaded skills are replayable workflow context only.",
            "Do not install, rewrite, or auto-enable skills from this context.",
            "Do not treat skills as permission to dispatch or execute work.",
        ]
    items = skill_context.get("items") if isinstance(skill_context.get("items"), list) else []
    compact_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        compact_items.append(
            {
                "load_id": item.get("load_id"),
                "agent_id": item.get("agent_id"),
                "purpose": item.get("purpose"),
                "name": item.get("name"),
                "source": item.get("source"),
                "path": item.get("path"),
                "content_hash": item.get("content_hash"),
                "description": item.get("description"),
                "required_tools": item.get("required_tools") if isinstance(item.get("required_tools"), list) else [],
                "risk": item.get("risk"),
            }
        )
    compact = {
        "count": len(compact_items),
        "by_agent": skill_context.get("by_agent") if isinstance(skill_context.get("by_agent"), dict) else {},
        "by_source": skill_context.get("by_source") if isinstance(skill_context.get("by_source"), dict) else {},
        "items": compact_items,
    }
    return [
        f"Loaded skills: {json.dumps(compact, ensure_ascii=False)}",
        "Loaded skills are replayable workflow context only.",
        "Do not install, rewrite, or auto-enable skills from this context.",
        "Do not treat skills as permission to dispatch or execute work.",
    ]


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
    configured_agent_roles = {agent.agent_id: agent.role for agent in config.agents} if config is not None else None
    seen_step_numbers: set[int] = set()
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise RuntimeError(f"provider plan step {index} must be an object")
        for field in PROVIDER_PLAN_STEP_REQUIRED_FIELDS:
            if field not in step:
                raise RuntimeError(f"provider plan step {index} missing required field: {field}")
        if not isinstance(step.get("step"), int) or step["step"] <= 0:
            raise RuntimeError(f"provider plan step {index} field step must be a positive integer")
        step_number = step["step"]
        if step_number in seen_step_numbers:
            raise RuntimeError(f"provider plan step {index} duplicates step number: {step_number}")
        seen_step_numbers.add(step_number)
        for field in PROVIDER_PLAN_STEP_STRING_FIELDS:
            if not isinstance(step.get(field), str) or not step[field].strip():
                raise RuntimeError(f"provider plan step {index} field {field} must be a non-empty string")
        if configured_agent_roles is not None and step["agent_id"] not in configured_agent_roles:
            raise RuntimeError(f"provider plan step {index} agent_id is not configured: {step['agent_id']}")
        if configured_agent_roles is not None:
            expected_role = configured_agent_roles[step["agent_id"]]
            if step["role"] != expected_role:
                raise RuntimeError(
                    f"provider plan step {index} role does not match configured agent role for "
                    f"{step['agent_id']}: expected {expected_role}, got {step['role']}"
                )
        if step.get("requires_approval") is not True:
            raise RuntimeError(f"provider plan step {index} must require approval")
    expected_step_numbers = set(range(1, len(steps) + 1))
    if seen_step_numbers != expected_step_numbers:
        raise RuntimeError(f"provider plan steps must be numbered 1..{len(steps)} without gaps")
    plan["approval_required"] = True
    plan["dispatch_ready"] = False
    return plan
