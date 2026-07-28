"""G2 two-stage planner/orchestrator planning engine.

Runs the planner stage (macro brief with acceptance criteria) and the
orchestrator stage (existing leader-plan step schema) as two provider
calls, landing exactly one plan payload. Stage failures are reported
separately so callers can audit `stage=planner` / `stage=orchestrator`
without a half-written plan. This engine never dispatches, never creates
approvals, and never touches tmux; the existing single-stage path stays
byte-identical when the split is not configured. See
docs/superpowers/specs/2026-07-28-g2-planner-orchestrator-split-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentdeck.models import ProjectConfig
from agentdeck.providers.planner_brief import (
    planner_brief_snapshot,
    validate_planner_brief,
)

from .leader import LeaderOrchestrator

SPLIT_PLANNING_STAGES = ("planner", "orchestrator")


class SplitPlanningError(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        if stage not in SPLIT_PLANNING_STAGES:
            raise ValueError("unknown split planning stage")
        self.stage = stage
        super().__init__(f"split planning {stage} stage failed: {message}")


@dataclass(frozen=True)
class SplitPlanResult:
    plan: dict[str, object]
    planner_brief: dict[str, Any]
    planner_backend: tuple[str, str]
    orchestrator_backend: tuple[str, str]


def run_split_planning(
    config: ProjectConfig,
    task: str,
    *,
    planner_provider: Any,
    orchestrator_provider: Any,
    planner_model: str,
    orchestrator_model: str,
    skill_context: dict[str, Any] | None = None,
) -> SplitPlanResult:
    planner_name = getattr(planner_provider, "name", None)
    orchestrator_name = getattr(orchestrator_provider, "name", None)
    if type(planner_name) is not str or not planner_name.strip():
        raise SplitPlanningError("planner", "planner provider has no valid name")
    if type(orchestrator_name) is not str or not orchestrator_name.strip():
        raise SplitPlanningError("orchestrator", "orchestrator provider has no valid name")

    brief_fn = getattr(planner_provider, "plan_brief", None)
    if not callable(brief_fn):
        raise SplitPlanningError(
            "planner", "planner provider does not support brief generation"
        )
    try:
        raw_brief = brief_fn(task=task, model=planner_model, skill_context=skill_context)
    except Exception as exc:
        raise SplitPlanningError("planner", str(exc)) from exc
    try:
        validated_brief = validate_planner_brief(raw_brief)
        brief_snapshot = planner_brief_snapshot(validated_brief)
    except ValueError as exc:
        raise SplitPlanningError("planner", str(exc)) from exc

    orchestrator = LeaderOrchestrator(config, orchestrator_provider)
    try:
        plan = orchestrator.plan(
            task,
            orchestrator_model,
            skill_context=skill_context,
            planner_brief=validated_brief,
        )
    except Exception as exc:
        raise SplitPlanningError("orchestrator", str(exc)) from exc

    return SplitPlanResult(
        plan=plan,
        planner_brief=brief_snapshot,
        planner_backend=(planner_name, planner_model),
        orchestrator_backend=(orchestrator_name, orchestrator_model),
    )
