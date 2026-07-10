from __future__ import annotations

from dataclasses import asdict, replace
import re
import shlex
import shutil
from typing import Any

from .contracts import validate_mission_preview_contract
from .mission import (
    MISSION_SCHEMA_VERSION,
    compact_mission_worker_entries,
    effective_mission_agent_for_binding,
    mission_binding_reusable,
    mission_commands,
    mission_intent,
    select_mission_agents,
    selected_agent_summaries,
    startup_action_summaries,
    validate_mission_plan,
)
from .models import EventRecord, ProjectConfig
from .orchestration.leader import LeaderOrchestrator
from .providers import LeaderProvider
from .providers.base import validate_provider_plan_schema
from .state import StateStore
from .workflow import workflow_plan_hash


class MissionPreviewError(ValueError):
    pass


def _requested_step_count(user_message: str) -> int:
    match = re.search(r"共\s*(\d+)\s*轮", user_message)
    if match is None:
        match = re.search(r"\b(\d+)\s*(?:rounds?|steps?)\b", user_message, re.IGNORECASE)
    return int(match.group(1)) if match else 8


def mission_planning_task(
    user_message: str,
    *,
    selected_agent_ids: tuple[str, ...],
    step_count: int,
) -> str:
    allowed = ", ".join(selected_agent_ids)
    return (
        f"Create a strictly serial Mission plan with exactly {step_count} steps. "
        "Each step may start only after the previous step has completed. "
        f"Use only these selected worker IDs: {allowed}. "
        "Do not add parallel, dynamic, DAG, or cyclic execution metadata. "
        "Every step must require human approval as planning provenance; this preview does not dispatch. "
        f"User request: {user_message}"
    )


def _explicit_leader_skill_context(
    store: StateStore, config: ProjectConfig
) -> dict[str, Any]:
    project_view = asdict(store.project_view(config))
    skills = project_view.get("skills")
    if isinstance(skills, dict):
        return skills
    return {"count": 0, "by_agent": {}, "by_source": {}, "items": []}


def _command_blocker(agent_id: str, command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return f"invalid worker command: {agent_id}"
    if not tokens:
        return f"invalid worker command: {agent_id}"
    if shutil.which(tokens[0]) is None:
        return f"worker command not found: {agent_id}"
    return None


def _compact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "goal": plan["goal"],
        "summary": plan["summary"],
        "steps": [
            {
                "step": step["step"],
                "agent_id": step["agent_id"],
                "role": step["role"],
                "task": step["task"],
            }
            for step in plan["steps"]
        ],
    }


def _mission_control(
    kind: str,
    label: str,
    command: str,
    safety: str,
    *,
    enabled: bool = True,
    blocker: str | None = None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "label": label,
        "command": command,
        "safety": safety,
        "enabled": enabled,
        "blocker": blocker,
    }


def _mission_preview_payload(
    mission: dict[str, Any], plan_record: dict[str, Any]
) -> dict[str, object]:
    mission_id = str(mission["mission_id"])
    commands = mission_commands(mission_id)
    blockers = list(mission["blockers"])
    can_start = bool(mission["can_start"])
    confirmation_blocker = blockers[0] if blockers else None
    payload: dict[str, object] = {
        "schema_version": MISSION_SCHEMA_VERSION,
        "ok": True,
        "mode": "mission_preview",
        "mission_id": mission_id,
        "status": mission["status"],
        "user_message": mission["user_message"],
        "provider": mission["provider"],
        "model": mission["model"],
        "leader_backend": mission["leader_backend"],
        "plan_id": mission["plan_id"],
        "plan_hash": mission["plan_hash"],
        "plan": _compact_plan(plan_record["plan"]),
        "selected_agents": mission["selected_agents"],
        "startup_actions": mission["startup_actions"],
        "step_count": mission["step_count"],
        "timeout_seconds": mission["timeout_seconds"],
        "can_start": can_start,
        "blockers": blockers,
        "confirmation_command": commands["confirmation_command"],
        "status_command": commands["status_command"],
        "workbench_command": "agentdeck workbench",
        "controls": [
            _mission_control(
                "execute",
                "Approve mission execution",
                commands["confirmation_command"],
                "delegated",
                enabled=can_start,
                blocker=confirmation_blocker,
            ),
            _mission_control(
                "inspect", "Inspect mission", commands["status_command"], "inspect"
            ),
            _mission_control(
                "inspect", "Open workbench", "agentdeck workbench", "inspect"
            ),
        ],
        "safety": "inspect",
        "requires_explicit_user": True,
    }
    validation = validate_mission_preview_contract(payload)
    if not validation["ok"]:
        raise ValueError("mission preview contract validation failed")
    return payload


def create_mission_preview(
    *,
    config: ProjectConfig,
    store: StateStore,
    provider: LeaderProvider,
    user_message: str,
    timeout_seconds: int,
) -> dict[str, object]:
    intent = mission_intent(user_message, config)
    if intent is None:
        raise ValueError("message is not a multi-agent mission request")
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("mission timeout must be positive")

    try:
        provider_name = provider.name
        configured_provider = config.leader.provider
    except Exception:
        raise MissionPreviewError("mission preview provider invalid") from None
    if (
        not isinstance(provider_name, str)
        or not provider_name.strip()
        or not isinstance(configured_provider, str)
        or not configured_provider.strip()
        or provider_name.strip().lower() != configured_provider.strip().lower()
    ):
        raise MissionPreviewError("mission preview provider invalid")

    try:
        state = store.load()
        if not isinstance(state, dict) or not isinstance(state.get("agents"), dict):
            raise ValueError("invalid state")
        bindings = state["agents"]
    except Exception:
        raise MissionPreviewError("mission preview state invalid") from None
    selection = select_mission_agents(
        config,
        requested_agent_ids=tuple(str(item) for item in intent["requested_agent_ids"]),
        requested_providers=tuple(str(item) for item in intent["requested_providers"]),
        bindings=bindings,
    )
    if selection.blockers or len(selection.agents) < 2 or len(
        {agent.agent_id for agent in selection.agents}
    ) < 2:
        raise MissionPreviewError("mission preview selection invalid")
    try:
        for agent in selection.agents:
            mission_binding_reusable(bindings.get(agent.agent_id))
    except Exception:
        raise MissionPreviewError("mission preview binding invalid") from None
    effective = tuple(
        effective_mission_agent_for_binding(
            agent,
            config.leader,
            bindings.get(agent.agent_id),
        )
        for agent in selection.agents
    )
    blockers = list(selection.blockers)
    for item in effective:
        if item.blocker is not None:
            blockers.append(item.blocker)
            continue
        blocker = _command_blocker(item.agent.agent_id, item.agent.command)
        if blocker is not None:
            blockers.append(blocker)

    selected_config = replace(config, agents=tuple(item.agent for item in effective))
    selected_agent_ids = tuple(item.agent.agent_id for item in effective)
    step_count = _requested_step_count(user_message)
    try:
        skill_context = _explicit_leader_skill_context(store, config)
    except Exception:
        raise MissionPreviewError("mission preview state invalid") from None
    try:
        plan = LeaderOrchestrator(selected_config, provider).plan(
            mission_planning_task(
                user_message,
                selected_agent_ids=selected_agent_ids,
                step_count=step_count,
            ),
            config.leader.model,
            skill_context=skill_context,
        )
    except Exception:
        raise MissionPreviewError("mission preview provider failed") from None
    try:
        validate_provider_plan_schema(plan, config=selected_config)
        validate_mission_plan(plan, selected_agent_ids, timeout_seconds)
        if len(plan["steps"]) != step_count:
            raise ValueError("unexpected mission step count")
    except Exception:
        raise MissionPreviewError("mission preview plan invalid") from None

    selected_agents = selected_agent_summaries(effective, bindings)
    startup_actions = startup_action_summaries(effective, bindings)
    for item in selected_agents:
        item.pop("blocker", None)
    for item in startup_actions:
        item.pop("blocker", None)
    try:
        compact_agents, invalid_agents = compact_mission_worker_entries(
            selected_agents, kind="selected_agents"
        )
        compact_actions, invalid_actions = compact_mission_worker_entries(
            startup_actions, kind="startup_actions"
        )
        selected_ids = [item["agent_id"] for item in compact_agents]
        action_ids = [item["agent_id"] for item in compact_actions]
        if (
            invalid_agents
            or invalid_actions
            or compact_agents != selected_agents
            or compact_actions != startup_actions
            or selected_ids != list(selected_agent_ids)
            or action_ids != selected_ids
            or len(selected_ids) < 2
            or len(set(selected_ids)) != len(selected_ids)
            or (not blockers and (not compact_agents or not compact_actions))
        ):
            raise ValueError("invalid mission summaries")
    except Exception:
        raise MissionPreviewError("mission preview summaries invalid") from None

    plan_record = store.record_plan(
        user_message,
        provider.name,
        config.leader.model,
        plan,
        skill_context=skill_context,
    )
    plan_hash = workflow_plan_hash(plan_record)
    mission = store.create_mission(
        user_message=user_message,
        provider=provider.name,
        model=config.leader.model,
        leader_backend=plan_record["leader_backend"],
        plan_id=plan_record["plan_id"],
        plan_hash=plan_hash,
        selected_agents=selected_agents,
        startup_actions=startup_actions,
        timeout_seconds=timeout_seconds,
        step_count=len(plan["steps"]),
        can_start=not blockers,
        blockers=blockers,
    )
    store.append_event(
        EventRecord.create(
            "mission_preview_created",
            {
                "mission_id": mission["mission_id"],
                "plan_id": plan_record["plan_id"],
                "selected_agent_ids": list(selected_agent_ids),
                "step_count": len(plan["steps"]),
            },
        )
    )
    return _mission_preview_payload(mission, plan_record)
