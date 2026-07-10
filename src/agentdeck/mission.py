from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .models import AgentRuntimeBinding, AgentSpec, LeaderConfig, ProjectConfig


MISSION_SCHEMA_VERSION = "mission/v1"
MISSION_STATUSES = (
    "pending_confirmation",
    "preparing",
    "running",
    "completed",
    "stopped",
    "interrupted",
)


def provider_family(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"codex", "codex-cli"}:
        return "codex"
    if normalized in {"claude", "claude-cli"}:
        return "claude"
    return normalized


def _token_positions(message: str, values: Sequence[str]) -> list[tuple[int, str]]:
    positions: list[tuple[int, str]] = []
    for value in values:
        pattern = rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-])"
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            positions.append((match.start(), value))
    return sorted(positions)


def _provider_positions(message: str) -> list[tuple[int, str]]:
    positions: list[tuple[int, str]] = []
    for family in ("codex", "claude"):
        match = re.search(
            rf"(?<![A-Za-z0-9_-]){family}(?:-cli)?(?![A-Za-z0-9_-])",
            message,
            flags=re.IGNORECASE,
        )
        if match:
            positions.append((match.start(), family))
    return sorted(positions)


def _is_inspection_route(message: str) -> bool:
    if "怎么" in message or "如何" in message:
        return True
    if re.match(r"^(?:请(?:帮我)?|帮我)?\s*(?:查看|检查|显示|列出|追踪)", message):
        return True
    return bool(
        re.match(
            r"^(?:please\s+)?(?:help\b|status\b|trace\b|"
            r"skill\s+(?:list|show|suggestions)\b|"
            r"memory\s+(?:list|show|suggestions)\b)",
            message,
        )
    )


def mission_intent(message: str, config: ProjectConfig) -> dict[str, Any] | None:
    normalized = message.strip().lower()
    if not normalized:
        return None

    if _is_inspection_route(normalized):
        return None

    execution_requested = any(
        signal in normalized for signal in ("让", "执行", "开始", "协作", "完成")
    ) or bool(re.search(r"(?<![A-Za-z0-9_-])run(?![A-Za-z0-9_-])", normalized))
    if not execution_requested:
        return None

    requested_ids = [
        agent_id
        for _, agent_id in _token_positions(
            message,
            [agent.agent_id for agent in config.agents if agent.agent_id != config.leader.agent_id],
        )
    ]
    requested_providers = [family for _, family in _provider_positions(message)]
    generic_signals = ("多个智能体", "两个 agent", "协作", "交替", "接龙", "依次")
    multi_agent = (
        len(requested_ids) >= 2
        or len(requested_providers) >= 2
        or any(signal in normalized for signal in generic_signals)
    )
    if not multi_agent:
        return None

    return {
        "execution_requested": True,
        "requested_agent_ids": requested_ids,
        "requested_providers": requested_providers,
        "multi_agent": True,
    }


@dataclass(frozen=True)
class MissionSelection:
    agents: tuple[AgentSpec, ...]
    blockers: tuple[str, ...]


def _binding_field(
    bindings: Mapping[str, AgentRuntimeBinding | Mapping[str, Any]],
    agent_id: str,
    field: str,
    default: Any = None,
) -> Any:
    binding = bindings.get(agent_id)
    if binding is None:
        return default
    if isinstance(binding, Mapping):
        return binding.get(field, default)
    return getattr(binding, field, default)


def _ranked_provider_candidate(
    candidates: Sequence[tuple[int, AgentSpec]],
    bindings: Mapping[str, AgentRuntimeBinding | Mapping[str, Any]],
) -> AgentSpec:
    _, selected = min(
        candidates,
        key=lambda item: (
            _binding_field(bindings, item[1].agent_id, "status", "configured") != "running",
            item[1].workspace_mode != "shared",
            item[0],
        ),
    )
    return selected


def select_mission_agents(
    config: ProjectConfig,
    requested_agent_ids: Sequence[str],
    requested_providers: Sequence[str],
    bindings: Mapping[str, AgentRuntimeBinding | Mapping[str, Any]],
) -> MissionSelection:
    workers = [agent for agent in config.agents if agent.agent_id != config.leader.agent_id]
    worker_by_id = {agent.agent_id: agent for agent in workers}

    if requested_agent_ids:
        selected: list[AgentSpec] = []
        blockers: list[str] = []
        seen: set[str] = set()
        for agent_id in requested_agent_ids:
            if agent_id in seen:
                continue
            seen.add(agent_id)
            if agent_id == config.leader.agent_id:
                blockers.append(f"requested agent is not a worker: {agent_id}")
            elif agent_id not in worker_by_id:
                blockers.append(f"requested agent not configured: {agent_id}")
            else:
                selected.append(worker_by_id[agent_id])
        if blockers:
            return MissionSelection(agents=(), blockers=tuple(blockers))
        if len(selected) < 2:
            return MissionSelection(agents=(), blockers=("mission requires at least two workers",))
        return MissionSelection(agents=tuple(selected), blockers=())

    indexed_by_family: dict[str, list[tuple[int, AgentSpec]]] = {}
    for index, agent in enumerate(workers):
        indexed_by_family.setdefault(provider_family(agent.provider), []).append((index, agent))

    families: list[str] = []
    if requested_providers:
        for requested in requested_providers:
            family = provider_family(requested)
            if family not in families:
                families.append(family)
    else:
        families = list(indexed_by_family)[:2]

    blockers = [
        f"requested provider not configured: {family}"
        for family in families
        if family not in indexed_by_family
    ]
    if blockers:
        return MissionSelection(agents=(), blockers=tuple(blockers))

    selected = [
        _ranked_provider_candidate(indexed_by_family[family], bindings) for family in families
    ]
    if len(selected) < 2:
        return MissionSelection(agents=(), blockers=("mission requires at least two workers",))
    return MissionSelection(agents=tuple(selected), blockers=())


@dataclass(frozen=True)
class EffectiveMissionAgent:
    agent: AgentSpec
    model: str | None
    model_source: str


def _configured_model(command: str) -> str | None:
    tokens = shlex.split(command)
    for index, token in enumerate(tokens):
        if token in {"--model", "-m"} and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--model="):
            return token.partition("=")[2]
    return None


def effective_mission_agent(agent: AgentSpec, leader: LeaderConfig) -> EffectiveMissionAgent:
    configured_model = _configured_model(agent.command)
    if configured_model is not None:
        return EffectiveMissionAgent(
            agent=agent,
            model=configured_model,
            model_source="configured_command",
        )

    family = provider_family(agent.provider)
    leader_family = provider_family(leader.provider)
    if (
        leader.provider in {"codex-cli", "claude-cli"}
        and family == leader_family
        and leader.model
    ):
        command = shlex.join([*shlex.split(agent.command), "--model", leader.model])
        return EffectiveMissionAgent(
            agent=replace(agent, command=command),
            model=leader.model,
            model_source="leader_inherited",
        )

    return EffectiveMissionAgent(agent=agent, model=None, model_source="provider_default")


def validate_mission_plan(
    plan: dict[str, Any],
    selected_agent_ids: Sequence[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("mission plan must be a dict")
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("mission timeout must be positive")

    forbidden_metadata = ("parallel", "dag", "cycle", "dynamic_steps")
    if any(plan.get(key) for key in forbidden_metadata):
        raise ValueError("mission plan cannot contain dynamic or parallel metadata")

    steps = plan.get("steps")
    if not isinstance(steps, list):
        raise ValueError("mission plan steps must be a list")
    if len(steps) < 2:
        raise ValueError("mission plan requires at least two steps")

    selected = frozenset(selected_agent_ids)
    represented: set[str] = set()
    for expected_step, step in enumerate(steps, start=1):
        if (
            not isinstance(step, dict)
            or type(step.get("step")) is not int
            or step.get("step") != expected_step
        ):
            raise ValueError("mission plan steps must be numbered 1 through n")
        agent_id = step.get("agent_id")
        if agent_id not in selected:
            raise ValueError(f"mission plan agent outside frozen selection: {agent_id}")
        represented.add(agent_id)
    if len(represented) < 2:
        raise ValueError("mission plan must represent at least two selected agents")
    return plan


def selected_agent_summaries(
    effective_agents: Sequence[EffectiveMissionAgent],
    bindings: Mapping[str, AgentRuntimeBinding | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "agent_id": item.agent.agent_id,
            "provider": item.agent.provider,
            "role": item.agent.role,
            "workspace_mode": item.agent.workspace_mode,
            "runtime_status": _binding_field(
                bindings, item.agent.agent_id, "status", "configured"
            ),
            "effective_model": item.model,
            "model_source": item.model_source,
        }
        for item in effective_agents
    ]


def startup_action_summaries(
    effective_agents: Sequence[EffectiveMissionAgent],
    bindings: Mapping[str, AgentRuntimeBinding | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in effective_agents:
        status = _binding_field(bindings, item.agent.agent_id, "status", "configured")
        pane_id = _binding_field(bindings, item.agent.agent_id, "pane_id")
        summaries.append(
            {
                "agent_id": item.agent.agent_id,
                "action": "reuse" if status == "running" and pane_id else "spawn",
                "runtime_status": status,
                "effective_model": item.model,
                "model_source": item.model_source,
            }
        )
    return summaries
