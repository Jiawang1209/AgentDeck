from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol

from agentdeck.models import ProjectConfig
from .plan_schema import (
    PROVIDER_PLAN_REQUIRED_FIELDS,
    PROVIDER_PLAN_STEP_REQUIRED_FIELDS,
    PROVIDER_PLAN_STEP_STRING_FIELDS,
    PROVIDER_PLAN_STRING_FIELDS,
    validate_provider_plan_schema,
)


@dataclass(frozen=True)
class LeaderPlanRequest:
    task: str
    config: ProjectConfig
    model: str | None = None
    skill_context: dict[str, Any] | None = None
    selected_agent_ids: tuple[str, ...] | None = None
    step_count: int | None = None
    timeout_seconds: int | None = None


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
                "planning_guidance": (
                    item.get("planning_guidance")
                    if item.get("agent_id") == "leader"
                    and isinstance(item.get("planning_guidance"), list)
                    else []
                ),
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
