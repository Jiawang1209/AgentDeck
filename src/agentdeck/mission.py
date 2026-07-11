from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, replace
from pathlib import PurePath
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
MISSION_ID_PATTERN = re.compile(r"^mis_[0-9a-f]{12}$")
MISSION_STATUS_TRANSITIONS = {
    "pending_confirmation": frozenset({"preparing", "stopped"}),
    "preparing": frozenset({"running", "stopped"}),
    "running": frozenset({"completed", "stopped", "interrupted"}),
    "completed": frozenset(),
    "stopped": frozenset({"preparing", "running"}),
    "interrupted": frozenset({"preparing", "running"}),
}
MISSION_SELECTED_AGENT_FIELDS = (
    "agent_id",
    "provider",
    "role",
    "workspace_mode",
    "runtime_status",
    "effective_model",
    "model_source",
    "blocker",
)
MISSION_SELECTED_AGENT_REQUIRED_FIELDS = MISSION_SELECTED_AGENT_FIELDS[:-1]
MISSION_STARTUP_ACTION_FIELDS = (
    "agent_id",
    "action",
    "runtime_status",
    "effective_model",
    "model_source",
    "blocker",
)
MISSION_STARTUP_ACTION_REQUIRED_FIELDS = MISSION_STARTUP_ACTION_FIELDS[:-1]
MISSION_WORKER_NULLABLE_FIELDS = frozenset({"effective_model", "blocker"})
MISSION_INVALID_BLOCKERS_BLOCKER = "invalid mission blockers"
MISSION_BINDING_STATUSES = frozenset({"configured", "running", "stopped", "unknown"})


def is_canonical_mission_id(value: object) -> bool:
    return isinstance(value, str) and MISSION_ID_PATTERN.fullmatch(value) is not None


def mission_commands(mission_id: str) -> dict[str, str]:
    if not is_canonical_mission_id(mission_id):
        raise ValueError("mission_id must match canonical mission id grammar")
    return {
        "status_command": f"agentdeck mission status --mission-id {mission_id}",
        "confirmation_command": (
            f'agentdeck leader chat --message "批准执行 {mission_id}"'
        ),
        "resume_command": (
            f"agentdeck mission resume --mission-id {mission_id} --confirm"
        ),
    }


def workbench_mission_card(item: Mapping[str, object], session_name: str) -> dict[str, object]:
    """Purely project a compact ProjectView Mission item into its workbench card."""
    mission_id = str(item.get("mission_id") or "")
    commands = mission_commands(mission_id)
    if re.fullmatch(r"[A-Za-z0-9_.:-]+", session_name) is None:
        raise ValueError("invalid tmux session name")
    blockers = [value for value in item.get("blockers", []) if isinstance(value, str)] if isinstance(item.get("blockers"), list) else []
    can_resume = item.get("status") in {"stopped", "interrupted"} and not blockers
    can_confirm = item.get("status") == "pending_confirmation" and not blockers
    resume_blocker = None if can_resume else blockers[0] if blockers else f"mission status is {item.get('status')}"
    confirm_blocker = None if can_confirm else blockers[0] if blockers else f"mission status is {item.get('status')}"
    attach_command = f"tmux attach -t {session_name}"
    def control(kind: str, label: str, command: str, safety: str, enabled: bool = True, blocker: str | None = None) -> dict[str, object]:
        return {"kind": kind, "label": label, "command": command, "safety": safety, "enabled": enabled, "blocker": blocker}
    return {
        "schema_version": MISSION_SCHEMA_VERSION,
        "ok": True,
        "mode": "mission_status",
        "mission_id": mission_id,
        "status": item.get("status"),
        "user_message": item.get("user_message"),
        "plan_id": item.get("plan_id"),
        "plan_hash": item.get("plan_hash"),
        "workflow_run_id": item.get("workflow_run_id"),
        "current_step": item.get("current_step", 0),
        "step_count": item.get("step_count"),
        "timeout_seconds": item.get("timeout_seconds"),
        "selected_agents": item.get("selected_agents"),
        "blockers": blockers,
        "stop_reason": item.get("stop_reason"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "confirmed_at": item.get("confirmed_at"),
        "completed_at": item.get("completed_at"),
        "can_resume": can_resume,
        "status_command": commands["status_command"],
        "resume_command": commands["resume_command"],
        "attach_command": attach_command,
        "workbench_command": "agentdeck workbench",
        "controls": [
            control("execute", "Confirm mission", commands["confirmation_command"], "delegated", can_confirm, confirm_blocker),
            control("execute", "Resume mission", commands["resume_command"], "delegated", can_resume, resume_blocker),
            control("inspect", "Inspect mission", commands["status_command"], "inspect"),
            control("inspect", "Attach terminal", attach_command, "inspect"),
            control("inspect", "Open workbench", "agentdeck workbench", "inspect"),
        ],
        "safety": "inspect",
        "requires_explicit_user": True,
        "confirmation_command": commands["confirmation_command"],
    }


def mission_status_transition_allowed(current: object, target: object) -> bool:
    if current not in MISSION_STATUSES or target not in MISSION_STATUSES:
        return False
    return current == target or target in MISSION_STATUS_TRANSITIONS[current]


def compact_mission_blockers(value: object) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], True
    blockers = [item for item in value if isinstance(item, str)]
    return blockers, len(blockers) != len(value)


def compact_mission_worker_entries(
    value: object,
    *,
    kind: str,
) -> tuple[list[dict[str, Any]], int]:
    if kind == "selected_agents":
        fields = MISSION_SELECTED_AGENT_FIELDS
        required = MISSION_SELECTED_AGENT_REQUIRED_FIELDS
    elif kind == "startup_actions":
        fields = MISSION_STARTUP_ACTION_FIELDS
        required = MISSION_STARTUP_ACTION_REQUIRED_FIELDS
    else:
        raise ValueError(f"unknown mission worker summary kind: {kind}")
    if not isinstance(value, list):
        return [], 1

    compact_items: list[dict[str, Any]] = []
    invalid_count = 0
    for raw_item in value:
        if not isinstance(raw_item, Mapping):
            invalid_count += 1
            continue
        item: dict[str, Any] = {}
        valid = True
        for field in fields:
            if field not in raw_item:
                continue
            field_value = raw_item[field]
            if isinstance(field_value, str) or (
                field in MISSION_WORKER_NULLABLE_FIELDS and field_value is None
            ):
                item[field] = field_value
            else:
                valid = False
        has_required_fields = all(field in item for field in required)
        if kind == "startup_actions" and item.get("action") not in {"reuse", "spawn"}:
            has_required_fields = False
        if has_required_fields:
            compact_items.append(item)
        if not valid or not has_required_fields:
            invalid_count += 1
    return compact_items, invalid_count


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
    if re.match(r"^状态(?:\s|[:：]|$)", message):
        return True
    if re.match(
        r"^(?:(?:请)?(?:帮我|帮助我)|请)?\s*(?:查看|检查|显示|列出|追踪)",
        message,
    ):
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


def _duplicate_agent_id(agents: Sequence[AgentSpec]) -> str | None:
    seen: set[str] = set()
    for agent in agents:
        if agent.agent_id in seen:
            return agent.agent_id
        seen.add(agent.agent_id)
    return None


def select_mission_agents(
    config: ProjectConfig,
    requested_agent_ids: Sequence[str],
    requested_providers: Sequence[str],
    bindings: Mapping[str, AgentRuntimeBinding | Mapping[str, Any]],
) -> MissionSelection:
    if duplicate_id := _duplicate_agent_id(config.agents):
        return MissionSelection(
            agents=(),
            blockers=(f"duplicate configured agent_id: {duplicate_id}",),
        )

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
        if duplicate_id := _duplicate_agent_id(selected):
            return MissionSelection(
                agents=(),
                blockers=(f"duplicate configured agent_id: {duplicate_id}",),
            )
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
    if duplicate_id := _duplicate_agent_id(selected):
        return MissionSelection(
            agents=(),
            blockers=(f"duplicate configured agent_id: {duplicate_id}",),
        )
    return MissionSelection(agents=tuple(selected), blockers=())


@dataclass(frozen=True)
class EffectiveMissionAgent:
    agent: AgentSpec
    model: str | None
    model_source: str
    blocker: str | None = None


def _configured_model(tokens: Sequence[str]) -> tuple[str | None, str | None]:
    model_flag_count = sum(
        token in {"--model", "-m"} or token.startswith("--model=") for token in tokens
    )
    if model_flag_count > 1:
        return None, "duplicate"

    for index, token in enumerate(tokens):
        if token in {"--model", "-m"}:
            value = tokens[index + 1] if index + 1 < len(tokens) else ""
            if (
                index + 1 >= len(tokens)
                or not value.strip()
                or value.lstrip().startswith("-")
            ):
                return None, "invalid"
            return value, None
        if token.startswith("--model="):
            value = token.partition("=")[2]
            if not value.strip():
                return None, "invalid"
            return value, None
    return None, None


def _unsupported_worker_command(command: str, tokens: Sequence[str]) -> bool:
    if "\n" in command or "\r" in command:
        return True
    shell_sensitive = (
        "&&",
        "||",
        ";",
        "|",
        "<",
        ">",
        "`",
        "$",
        "&",
        "#",
        "*",
        "?",
        "[",
        "]",
    )
    if any(operator in command for operator in shell_sensitive):
        return True
    if tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        return True
    return any(token.startswith("~") for token in tokens)


def _worker_executable_matches_provider(agent: AgentSpec, executable: str) -> bool:
    family = provider_family(agent.provider)
    allowed_executables = {
        "codex": {"codex", "codex.exe"},
        "claude": {"claude", "claude.exe"},
    }
    return PurePath(executable).name.lower() in allowed_executables.get(family, set())


def _literal_worker_argv(tokens: Sequence[str]) -> bool:
    literal = re.compile(r"^[A-Za-z0-9_./:=,+@%-]+$")
    model_literal = re.compile(r"^[A-Za-z0-9_./:=,+@% -]+$")
    for index, token in enumerate(tokens[1:], start=1):
        if literal.fullmatch(token):
            continue
        if index > 0 and tokens[index - 1] in {"--model", "-m"}:
            if model_literal.fullmatch(token):
                continue
        if token.startswith("--model=") and model_literal.fullmatch(token.partition("=")[2]):
            continue
        return False
    return True


def effective_mission_agent(agent: AgentSpec, leader: LeaderConfig) -> EffectiveMissionAgent:
    try:
        tokens = shlex.split(agent.command)
    except ValueError:
        return EffectiveMissionAgent(
            agent=agent,
            model=None,
            model_source="provider_default",
            blocker=f"invalid worker command: {agent.agent_id}",
        )

    if not tokens or _unsupported_worker_command(agent.command, tokens):
        return EffectiveMissionAgent(
            agent=agent,
            model=None,
            model_source="provider_default",
            blocker=f"unsupported worker command: {agent.agent_id}",
        )

    if not _worker_executable_matches_provider(agent, tokens[0]):
        return EffectiveMissionAgent(
            agent=agent,
            model=None,
            model_source="provider_default",
            blocker=f"worker executable does not match provider: {agent.agent_id}",
        )

    configured_model, model_flag_error = _configured_model(tokens)
    if model_flag_error == "duplicate":
        return EffectiveMissionAgent(
            agent=agent,
            model=None,
            model_source="provider_default",
            blocker=f"duplicate worker model flag: {agent.agent_id}",
        )
    if model_flag_error == "invalid":
        return EffectiveMissionAgent(
            agent=agent,
            model=None,
            model_source="provider_default",
            blocker=f"invalid worker model flag: {agent.agent_id}",
        )

    if not _literal_worker_argv(tokens):
        return EffectiveMissionAgent(
            agent=agent,
            model=None,
            model_source="provider_default",
            blocker=f"unsupported worker command: {agent.agent_id}",
        )
    if configured_model is not None:
        return EffectiveMissionAgent(
            agent=agent,
            model=configured_model,
            model_source="configured_command",
        )

    family = provider_family(agent.provider)
    normalized_leader_provider = leader.provider.strip().lower()
    leader_family = provider_family(normalized_leader_provider)
    if (
        normalized_leader_provider in {"codex-cli", "claude-cli"}
        and family == leader_family
        and leader.model
    ):
        command = shlex.join([*tokens, "--model", leader.model])
        return EffectiveMissionAgent(
            agent=replace(agent, command=command),
            model=leader.model,
            model_source="leader_inherited",
        )

    return EffectiveMissionAgent(agent=agent, model=None, model_source="provider_default")


def effective_mission_agent_for_binding(
    agent: AgentSpec,
    leader: LeaderConfig,
    binding: AgentRuntimeBinding | Mapping[str, Any] | None,
) -> EffectiveMissionAgent:
    if mission_binding_reusable(binding):
        return EffectiveMissionAgent(
            agent=agent,
            model=None,
            model_source="running_binding",
        )
    return effective_mission_agent(agent, leader)


def mission_binding_reusable(
    binding: AgentRuntimeBinding | Mapping[str, Any] | None,
) -> bool:
    if binding is None:
        return False
    status = binding.get("status") if isinstance(binding, Mapping) else binding.status
    pane_id = binding.get("pane_id") if isinstance(binding, Mapping) else binding.pane_id
    if not isinstance(status, str) or status not in MISSION_BINDING_STATUSES:
        raise ValueError("invalid mission binding")
    if pane_id is not None and (
        not isinstance(pane_id, str) or not pane_id.strip()
    ):
        raise ValueError("invalid mission binding")
    return status == "running" and pane_id is not None


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
    if any(key in plan for key in forbidden_metadata):
        raise ValueError("mission plan cannot contain dynamic or parallel metadata")

    per_step_approval_patterns = (
        r"\bper[- ]step\s+(?:human\s+)?approval\b",
        r"\b(?:every|each)\s+step\b.{0,48}\b(?:human\s+)?approv(?:al|e|ed|es)\b",
        r"每一步.{0,24}(?:人工)?批准",
        r"逐步批准",
    )
    for field in ("goal", "summary"):
        value = plan.get(field)
        if isinstance(value, str) and any(
            re.search(pattern, value, re.IGNORECASE)
            for pattern in per_step_approval_patterns
        ):
            raise ValueError("mission plan cannot require per-step approval")

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
    summaries: list[dict[str, Any]] = []
    for item in effective_agents:
        summary = {
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
        if item.blocker is not None:
            summary["blocker"] = item.blocker
        summaries.append(summary)
    return summaries


def startup_action_summaries(
    effective_agents: Sequence[EffectiveMissionAgent],
    bindings: Mapping[str, AgentRuntimeBinding | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in effective_agents:
        binding = bindings.get(item.agent.agent_id)
        status = _binding_field(bindings, item.agent.agent_id, "status", "configured")
        summary = {
            "agent_id": item.agent.agent_id,
            "action": "reuse" if mission_binding_reusable(binding) else "spawn",
            "runtime_status": status,
            "effective_model": item.model,
            "model_source": item.model_source,
        }
        if item.blocker is not None:
            summary["blocker"] = item.blocker
        summaries.append(summary)
    return summaries
