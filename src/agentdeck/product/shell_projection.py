"""Pure input and presentation projection helpers for ProductShell."""

from __future__ import annotations

from collections.abc import Mapping

from agentdeck.application.mission_service import MissionPreviewView
from agentdeck.product.presenter import MissionPreviewPresentation
from agentdeck.product.renderer import render


def copy_available_leaders(
    value: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    if type(value) is not dict:
        raise TypeError("available_leaders must be a plain mapping")
    copied: dict[str, tuple[str, ...]] = {}
    for leader, models in value.items():
        if (
            type(leader) is not str
            or not leader.strip()
            or type(models) is not tuple
            or not models
            or any(type(model) is not str or not model.strip() for model in models)
        ):
            raise ValueError("available_leaders is invalid")
        copied[leader] = tuple(models)
    return dict(sorted(copied.items()))


def confirmation_authority(text: str) -> tuple[str, str] | None:
    if type(text) is not str:
        return None
    parts = text.strip().split()
    if len(parts) == 3 and parts[0].casefold() == "confirm":
        return parts[1], parts[2]
    return None


def preview_presentation(value: MissionPreviewView) -> MissionPreviewPresentation:
    draft, preview = value.draft, value.preview
    budgets = dict(draft.budgets)
    return MissionPreviewPresentation(
        objective=draft.objective,
        scope=draft.scope,
        leader_backend=draft.leader_backend,
        leader_model=draft.leader_model,
        workers=tuple(
            f"{task.agent_instance_id}: {task.role.value} via {task.backend}"
            for task in draft.tasks
        ),
        tasks=tuple(task.name for task in draft.tasks),
        task_dependencies=tuple(
            f"{task.name}: "
            f"{', '.join(task.dependencies) if task.dependencies else 'none'}"
            for task in draft.tasks
        ),
        acp_routes=tuple(task.acp_route for task in draft.tasks),
        permission=draft.permission_profile.value.replace("_", "-"),
        project_boundary=draft.project_root,
        acceptance_criteria=draft.acceptance_criteria,
        retry_budget=budgets["max_attempts"],
        revision_budget=budgets["max_revision_cycles"],
        non_goals=draft.non_goals,
        risks=draft.risks,
        preview_id=preview.preview_id,
        version=preview.version,
        content_hash=preview.content_hash,
        leader_adapter=draft.leader_adapter,
        leader_version=draft.leader_version,
        additional_budgets=(
            f"Leader schema repairs: {budgets['max_leader_schema_repairs']}",
            f"ACP reconnects: {budgets['max_acp_reconnects']}",
            f"Final acceptance attempts: {budgets['max_final_acceptance_attempts']}",
        ),
    )


def validate_mission_preview(value: MissionPreviewView) -> None:
    if type(value) is not MissionPreviewView:
        raise TypeError("Preview validator requires MissionPreviewView")
    text = render(preview_presentation(value))
    if len(text.encode("utf-8", "strict")) > 65_536:
        raise ValueError("rendered Mission Preview exceeds its human display bound")


__all__ = [
    "confirmation_authority",
    "copy_available_leaders",
    "preview_presentation",
    "validate_mission_preview",
]
