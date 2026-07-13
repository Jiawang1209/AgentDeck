from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import os
import re
import shlex
import shutil
import stat
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import (
    validate_mission_preview_contract,
    validate_mission_run_contract,
    validate_mission_status_contract,
)
from .mission import (
    MISSION_SCHEMA_VERSION,
    compact_mission_worker_entries,
    effective_mission_agent_for_binding,
    mission_binding_reusable,
    mission_commands,
    mission_intent,
    normalize_mission_plan_metadata,
    is_canonical_mission_id,
    select_mission_agents,
    selected_agent_summaries,
    startup_action_summaries,
    validate_mission_plan,
)
from .models import (
    AgentRuntimeBinding,
    AgentSpec,
    EventRecord,
    ProjectConfig,
    utc_now,
)
from .conversation.models import ConversationMutation
from .orchestration.leader import LeaderOrchestrator
from .providers import LeaderProvider
from .providers.base import validate_provider_plan_schema
from .state import StateStore
from .state import canonical_snapshot_hash, leader_backend_identity, validate_execution_snapshot
from .runtime.base import RuntimeBackend
from .runtime.readiness import WorkerReadinessBatch, wait_for_worker_readiness
from .workflow import authorized_steps, run_sequential_workflow, workflow_plan_hash


class MissionPreviewError(ValueError):
    pass


class MissionRunError(ValueError):
    pass


def canonical_hash(value: object) -> str:
    """Return the bounded canonical hash used by frozen daemon execution facts."""
    try:
        return canonical_snapshot_hash(value)
    except (TypeError, ValueError):
        raise MissionRunError("execution snapshot invalid") from None


def _compact_snapshot_provenance(plan: Mapping[str, object]) -> list[dict[str, object]]:
    raw_context = plan.get("skill_context")
    if not isinstance(raw_context, Mapping):
        return []
    raw_items = raw_context.get("items")
    if not isinstance(raw_items, list):
        return []
    compact: list[dict[str, object]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise MissionRunError("execution snapshot invalid")
        item: dict[str, object] = {}
        name = raw.get("name")
        content_hash = raw.get("content_hash")
        agent_id = raw.get("agent_id")
        source = raw.get("source")
        if (
            type(name) is not str
            or not name
            or type(content_hash) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash) is None
            or type(agent_id) is not str
            or not agent_id
            or type(source) is not str
            or not source
        ):
            raise MissionRunError("execution snapshot invalid")
        source_kind = (
            "builtin"
            if source == "builtin"
            else "project"
            if source == "project"
            else "external"
        )
        item = {
            "agent_id": agent_id,
            "name_hash": canonical_hash({"name": name}),
            "content_hash": content_hash,
            "source_kind": source_kind,
        }
        compact.append(item)
    return compact


def _optional_plan_fact_hash(plan: Mapping[str, object], field: str) -> str | None:
    value = plan.get(field)
    if value is None:
        return None
    return canonical_hash({field: value})


def _compact_memory_provenance(root: Path) -> list[dict[str, object]]:
    canonical_root = root.resolve(strict=False)
    records: list[dict[str, object]] = []
    for scope, relative in (
        ("project", Path(".agentdeck/memory/project.md")),
        ("global", Path(".agentdeck/memory/global.md")),
    ):
        path = canonical_root / relative
        if not path.exists():
            continue
        try:
            if not path.resolve(strict=True).is_relative_to(canonical_root):
                raise MissionRunError("execution snapshot invalid")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
                    raise MissionRunError("execution snapshot invalid")
                data = os.read(descriptor, 256 * 1024 + 1)
                if len(data) != info.st_size or len(data) > 256 * 1024:
                    raise MissionRunError("execution snapshot invalid")
            finally:
                os.close(descriptor)
            text = data.decode("utf-8")
        except MissionRunError:
            raise
        except (OSError, UnicodeError):
            raise MissionRunError("execution snapshot invalid") from None
        records.append(
            {
                "scope": scope,
                "content_hash": f"sha256:{hashlib.sha256(data).hexdigest()}",
                "line_count": len(text.splitlines()),
                "byte_count": len(data),
            }
        )
    return records


def _execution_policy(config: ProjectConfig) -> dict[str, object]:
    approval_mode = config.leader.approval_mode
    allowed_modes = {"confirm", "approve", "auto_approve", "autonomous"}
    if type(approval_mode) is not str or approval_mode not in allowed_modes:
        raise MissionRunError("execution policy invalid")
    if any(type(item) is not str or not item for item in config.autonomous.allowed_agents):
        raise MissionRunError("execution policy invalid")
    if type(config.autonomous.max_approvals) is not int or config.autonomous.max_approvals < 0:
        raise MissionRunError("execution policy invalid")
    return {
        "approval_mode": approval_mode,
        "autonomous_allowed_agents": list(config.autonomous.allowed_agents),
        "autonomous_max_approvals": config.autonomous.max_approvals,
        "policy_source": "project_config",
    }


def build_execution_snapshot(
    config: ProjectConfig,
    mission: Mapping[str, object],
    plan: Mapping[str, object],
    policy: Mapping[str, object],
) -> dict[str, object]:
    """Build compact authority facts without carrying prompts or runtime identifiers."""
    try:
        mission_id = mission.get("mission_id")
        if not is_canonical_mission_id(mission_id):
            raise ValueError("invalid mission")
        plan_id = mission.get("plan_id")
        if type(plan_id) is not str or plan.get("plan_id") != plan_id:
            raise ValueError("invalid plan")
        if mission.get("schema_version") != MISSION_SCHEMA_VERSION:
            raise ValueError("invalid mission schema")
        raw_plan = plan.get("plan")
        if type(raw_plan) is not dict:
            raise ValueError("invalid plan")
        raw_steps = raw_plan.get("steps")
        if type(raw_steps) is not list or not raw_steps:
            raise ValueError("invalid steps")
        selected = mission.get("selected_agents")
        if type(selected) is not list:
            raise ValueError("invalid workers")
        selected_ids = [
            item.get("agent_id") if type(item) is dict else None for item in selected
        ]
        if len(selected_ids) < 2 or len(set(selected_ids)) != len(selected_ids):
            raise ValueError("invalid workers")
        agents_by_id = {agent.agent_id: agent for agent in config.agents}
        if len(agents_by_id) != len(config.agents):
            raise ValueError("invalid workers")
        workers: list[dict[str, object]] = []
        for selected_row in selected:
            assert type(selected_row) is dict
            agent_id = selected_row["agent_id"]
            agent = agents_by_id.get(agent_id)
            if agent is None or agent.transport not in {"acp", "tmux"}:
                raise ValueError("invalid worker")
            if agent.transport == "acp" and not agent.transport_command:
                raise ValueError("invalid worker")
            if any(type(part) is not str or not part for part in agent.transport_command):
                raise ValueError("invalid worker")
            if any(
                selected_row.get(field) != getattr(agent, field)
                for field in ("provider", "role", "workspace_mode")
            ):
                raise ValueError("worker drift")
            workers.append(
                {
                    "agent_id": agent.agent_id,
                    "role": agent.role,
                    "provider": agent.provider,
                    "workspace_mode": agent.workspace_mode,
                    "configured_transport": agent.transport,
                    "capability_provenance": {
                        "source": "project_config",
                        "transport": agent.transport,
                        "adapter_configuration": (
                            "present" if agent.transport_command else "not_applicable"
                        ),
                    },
                }
            )
        compact_steps: list[dict[str, object]] = []
        for position, raw_step in enumerate(raw_steps, start=1):
            if type(raw_step) is not dict:
                raise ValueError("invalid step")
            step_number = raw_step.get("step")
            agent_id = raw_step.get("agent_id")
            role = raw_step.get("role")
            task = raw_step.get("task")
            if (
                type(step_number) is not int
                or step_number != position
                or type(agent_id) is not str
                or agent_id not in selected_ids
                or type(role) is not str
                or not role
                or type(task) is not str
                or not task
            ):
                raise ValueError("invalid step")
            compact_steps.append(
                {
                    "step_id": f"step_{position}",
                    "position": position,
                    "agent_id": agent_id,
                    "role": role,
                    "task_hash": canonical_hash({"task": task}),
                }
            )
        if len(compact_steps) != mission.get("step_count"):
            raise ValueError("invalid step count")
        root = Path(config.root).resolve(strict=False)
        plan_hash = mission.get("plan_hash")
        if type(plan_hash) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", plan_hash) is None:
            raise ValueError("invalid plan hash")
        policy_body = deepcopy(dict(policy))
        if set(policy_body) != {
            "approval_mode",
            "autonomous_allowed_agents",
            "autonomous_max_approvals",
            "policy_source",
        }:
            raise ValueError("invalid policy")
        if (
            policy_body.get("approval_mode")
            not in {"confirm", "approve", "auto_approve", "autonomous"}
            or type(policy_body.get("autonomous_allowed_agents")) is not list
            or any(
                type(item) is not str or not item
                for item in policy_body["autonomous_allowed_agents"]
            )
            or type(policy_body.get("autonomous_max_approvals")) is not int
            or policy_body["autonomous_max_approvals"] < 0
            or policy_body.get("policy_source") != "project_config"
        ):
            raise ValueError("invalid policy")
        mission_body: dict[str, object] = {
            "mission_id": mission_id,
            "schema_version": mission.get("schema_version"),
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "goal_hash": canonical_hash({"goal": raw_plan.get("goal")}),
            "summary_hash": canonical_hash({"summary": raw_plan.get("summary")}),
            "steps": compact_steps,
            "project_scope_hash": canonical_hash({"project_root": str(root)}),
            "action_classes": ["worker_task", "declared_local_verification"],
            "skill_provenance": _compact_snapshot_provenance(plan),
            "memory_provenance": _compact_memory_provenance(root),
            "declared_tests_hash": _optional_plan_fact_hash(raw_plan, "declared_tests"),
            "acceptance_criteria_hash": _optional_plan_fact_hash(
                raw_plan, "acceptance_criteria"
            ),
        }
        limits: dict[str, object] = {
            "step_count": mission.get("step_count"),
            "timeout_seconds": mission.get("timeout_seconds"),
            "retry_limit": 0,
            "worker_budget": mission.get("step_count"),
        }
        mission_hash = canonical_hash(mission_body)
        policy_hash = canonical_hash(policy_body)
        execution_body: dict[str, object] = {
            "mission": mission_body,
            "workers": workers,
            "policy": policy_body,
            "limits": limits,
            "mission_hash": mission_hash,
            "policy_hash": policy_hash,
        }
        snapshot: dict[str, object] = {
            **execution_body,
            "execution_hash": canonical_hash(execution_body),
        }
        return validate_execution_snapshot(snapshot)
    except MissionRunError:
        raise
    except (AssertionError, KeyError, TypeError, ValueError):
        raise MissionRunError("execution snapshot invalid") from None


def confirm_mission_for_daemon(
    *, config: ProjectConfig, store: StateStore, mission_id: str
) -> dict[str, Any]:
    try:
        mission = store.mission_by_id(mission_id)
        if mission.get("status") != "pending_confirmation":
            raise MissionRunError("mission is not pending confirmation")
        plan, _agents = _frozen_preflight(config, store, mission)
        policy = _execution_policy(config)
        snapshot = build_execution_snapshot(config, mission, plan, policy)
        return store.freeze_mission_execution(
            mission_id,
            expected_mission=mission,
            expected_plan=plan,
            execution_snapshot=snapshot,
            confirmed_at=utc_now(),
        )
    except MissionRunError:
        raise
    except KeyError:
        raise MissionRunError("mission identity invalid") from None
    except (TypeError, ValueError):
        raise MissionRunError("mission confirmation drift") from None


def attempt_dispatch_key(
    mission_id: str,
    step_id: str,
    agent_id: str,
    configured_transport: str,
    snapshot_hash: str,
) -> str:
    if (
        not is_canonical_mission_id(mission_id)
        or type(step_id) is not str
        or re.fullmatch(r"step_[1-9][0-9]*", step_id) is None
        or type(agent_id) is not str
        or not agent_id
        or configured_transport not in {"acp", "tmux"}
        or type(snapshot_hash) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot_hash) is None
    ):
        raise MissionRunError("attempt identity invalid")
    digest = canonical_hash(
        {
            "mission_id": mission_id,
            "step_id": step_id,
            "agent_id": agent_id,
            "configured_transport": configured_transport,
            "snapshot_hash": snapshot_hash,
        }
    ).removeprefix("sha256:")
    return f"dsp_{digest[:32]}"


def prepare_attempt(
    *,
    config: ProjectConfig,
    store: StateStore,
    mission_id: str,
    step_id: str,
    agent_id: str,
    configured_transport: str,
) -> dict[str, Any]:
    if type(step_id) is not str:
        raise MissionRunError("mission step identity invalid")
    try:
        mission = store.mission_by_id(mission_id)
    except KeyError:
        raise MissionRunError("mission identity invalid") from None
    if mission.get("status") in {"completed", "stopped", "interrupted"}:
        raise MissionRunError("terminal mission step")
    if (
        mission.get("status") not in {"preparing", "running"}
        or not mission.get("confirmed_at")
        or mission.get("execution_snapshot") is None
    ):
        raise MissionRunError("mission execution is not confirmed")
    try:
        persisted_snapshot = validate_execution_snapshot(
            mission["execution_snapshot"]
        )
    except (TypeError, ValueError):
        raise MissionRunError("frozen execution snapshot invalid") from None
    try:
        plan = store.plan_by_id(str(mission.get("plan_id") or ""))
        expected_snapshot = build_execution_snapshot(
            config, mission, plan, _execution_policy(config)
        )
    except (KeyError, MissionRunError):
        raise MissionRunError("frozen execution drift") from None
    if persisted_snapshot != expected_snapshot:
        raise MissionRunError("frozen execution drift")
    dispatch_key = attempt_dispatch_key(
        mission_id,
        step_id,
        agent_id,
        configured_transport,
        str(persisted_snapshot["execution_hash"]),
    )
    try:
        return store.prepare_mission_attempt(
            mission_id=mission_id,
            step_id=step_id,
            agent_id=agent_id,
            configured_transport=configured_transport,
            expected_snapshot=expected_snapshot,
            expected_plan=plan,
            dispatch_key=dispatch_key,
        )
    except KeyError:
        raise MissionRunError("frozen execution drift") from None
    except (TypeError, ValueError) as exc:
        allowed = {
            "active attempt already exists",
            "attempt state invalid",
            "frozen execution drift",
            "frozen execution snapshot invalid",
            "mission confirmation state invalid",
            "mission execution is not confirmed",
            "mission step agent drift",
            "mission step identity invalid",
            "mission step lineage drift",
            "mission step transport drift",
            "terminal mission step",
            "unknown mission step",
        }
        message = str(exc)
        raise MissionRunError(message if message in allowed else "attempt preparation invalid") from None


@dataclass(frozen=True)
class LeaderMissionCandidate:
    provider: str
    model: str
    user_message: str
    plan: dict[str, Any]
    timeout_seconds: int


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
        "The user gives one overall Mission confirmation that authorizes the entire fixed sequence; "
        "the plan must not request per-step approval. This preview does not dispatch. "
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


def _mission_candidate_context(
    config: ProjectConfig,
    store: StateStore,
    *,
    user_message: str,
    provider: str,
) -> tuple[dict[str, Any], tuple[Any, ...], tuple[str, ...], list[str], ProjectConfig, dict[str, Any]]:
    intent = mission_intent(user_message, config)
    if intent is None:
        raise ValueError("message is not a multi-agent mission request")
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
            agent, config.leader, bindings.get(agent.agent_id)
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
    selected_config = replace(
        config,
        leader=replace(config.leader, provider=provider),
        agents=tuple(item.agent for item in effective),
    )
    selected_agent_ids = tuple(item.agent.agent_id for item in effective)
    try:
        skill_context = _explicit_leader_skill_context(store, config)
    except Exception:
        raise MissionPreviewError("mission preview state invalid") from None
    return (
        bindings,
        effective,
        selected_agent_ids,
        blockers,
        selected_config,
        skill_context,
    )


def create_mission_preview_from_candidate(
    *,
    config: ProjectConfig,
    store: StateStore,
    candidate: LeaderMissionCandidate,
    conversation_mutation: ConversationMutation | None = None,
    conversation_mutation_factory: Callable[[dict[str, object]], ConversationMutation] | None = None,
) -> dict[str, object]:
    if not isinstance(candidate, LeaderMissionCandidate):
        raise MissionPreviewError("mission preview candidate invalid")
    provider = candidate.provider.strip().lower() if isinstance(candidate.provider, str) else ""
    configured_provider = config.leader.provider.strip().lower()
    if not provider or provider != configured_provider or candidate.model != config.leader.model:
        raise MissionPreviewError("mission preview provider invalid")
    if type(candidate.timeout_seconds) is not int or candidate.timeout_seconds <= 0:
        raise ValueError("mission timeout must be positive")
    (
        bindings,
        effective,
        selected_agent_ids,
        blockers,
        selected_config,
        skill_context,
    ) = _mission_candidate_context(
        config, store, user_message=candidate.user_message, provider=provider
    )
    step_count = _requested_step_count(candidate.user_message)
    try:
        raw_plan = deepcopy(candidate.plan)
        validate_provider_plan_schema(raw_plan, config=selected_config)
        validate_mission_plan(raw_plan, selected_agent_ids, candidate.timeout_seconds)
        if len(raw_plan["steps"]) != step_count:
            raise ValueError("unexpected mission step count")
        plan = normalize_mission_plan_metadata(raw_plan, step_count)
        validate_mission_plan(plan, selected_agent_ids, candidate.timeout_seconds)
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

    plan_record = store.build_plan_record(
        candidate.user_message,
        provider,
        candidate.model,
        plan,
        skill_context=skill_context,
    )
    plan_hash = workflow_plan_hash(plan_record)
    mission = store.build_mission_record(
        user_message=candidate.user_message,
        provider=provider,
        model=candidate.model,
        leader_backend=plan_record["leader_backend"],
        plan_id=plan_record["plan_id"],
        plan_hash=plan_hash,
        selected_agents=selected_agents,
        startup_actions=startup_actions,
        timeout_seconds=candidate.timeout_seconds,
        step_count=len(plan["steps"]),
        can_start=not blockers,
        blockers=blockers,
    )
    payload = _mission_preview_payload(mission, plan_record)
    event = EventRecord.create(
        "mission_preview_created",
        {
            "mission_id": mission["mission_id"],
            "plan_id": plan_record["plan_id"],
            "selected_agent_ids": list(selected_agent_ids),
            "step_count": len(plan["steps"]),
        },
    )
    if conversation_mutation is not None and conversation_mutation_factory is not None:
        raise MissionPreviewError("conversation mutation source is ambiguous")
    if conversation_mutation_factory is not None:
        try:
            conversation_mutation = conversation_mutation_factory(deepcopy(payload))
        except Exception:
            raise MissionPreviewError("conversation mutation invalid") from None
    conversation_records: dict[str, tuple[Any, ...]] = {}
    conversation_events: tuple[EventRecord, ...] = ()
    if conversation_mutation is not None:
        if not isinstance(conversation_mutation, ConversationMutation):
            raise MissionPreviewError("conversation mutation invalid")
        allowed = {
            "conversation_sessions",
            "conversation_turns",
            "conversation_preview_bindings",
            "conversation_state_transitions",
        }
        if set(conversation_mutation.append_records) - allowed:
            raise MissionPreviewError("conversation mutation contains domain records")
        conversation_records = {
            key: tuple(records)
            for key, records in conversation_mutation.append_records.items()
        }
        conversation_events = conversation_mutation.events
    result = store.commit_conversation_mutation(
        ConversationMutation(
            append_records={
                **conversation_records,
                "plans": (plan_record,),
                "missions": (mission,),
            },
            events=(*conversation_events, event),
        )
    )
    if result["outbox_blocked"]:
        raise MissionPreviewError("mission preview audit blocked")
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
    if not isinstance(provider_name, str) or not isinstance(configured_provider, str):
        raise MissionPreviewError("mission preview provider invalid")
    candidate_provider = provider_name.strip().lower()
    canonical_provider = configured_provider.strip().lower()
    if (
        not candidate_provider
        or not canonical_provider
        or candidate_provider != canonical_provider
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

    selected_config = replace(
        config,
        leader=replace(config.leader, provider=canonical_provider),
        agents=tuple(item.agent for item in effective),
    )
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
    return create_mission_preview_from_candidate(
        config=config,
        store=store,
        candidate=LeaderMissionCandidate(
            provider=canonical_provider,
            model=config.leader.model,
            user_message=user_message,
            plan=plan,
            timeout_seconds=timeout_seconds,
        ),
    )


def _safe_attach_command(config: ProjectConfig) -> str:
    session = config.runtime.session_name
    if (
        not isinstance(session, str)
        or re.fullmatch(r"[A-Za-z0-9_.:-]+", session) is None
    ):
        raise MissionRunError("mission runtime configuration invalid")
    return f"tmux attach -t {session}"


def mission_status_payload(
    config: ProjectConfig,
    store: StateStore,
    mission: dict[str, Any],
    *,
    mode: str = "mission_status",
    confirmed: bool | None = None,
) -> dict[str, object]:
    mission_id = str(mission.get("mission_id") or "")
    commands = mission_commands(mission_id)
    blockers = list(mission.get("blockers") or [])
    can_resume = mission.get("status") in {"stopped", "interrupted"} and not blockers
    resume_blocker = (
        None
        if can_resume
        else blockers[0]
        if blockers
        else f"mission status is {mission.get('status')}"
    )
    attach = _safe_attach_command(config)
    payload: dict[str, object] = {
        "schema_version": MISSION_SCHEMA_VERSION,
        "ok": True,
        "mode": mode,
        "mission_id": mission_id,
        "status": mission.get("status"),
        "user_message": mission.get("user_message"),
        "plan_id": mission.get("plan_id"),
        "plan_hash": mission.get("plan_hash"),
        "workflow_run_id": mission.get("workflow_run_id"),
        "current_step": mission.get("current_step", 0),
        "step_count": mission.get("step_count"),
        "timeout_seconds": mission.get("timeout_seconds"),
        "selected_agents": mission.get("selected_agents"),
        "blockers": blockers,
        "stop_reason": mission.get("stop_reason"),
        "created_at": mission.get("created_at"),
        "updated_at": mission.get("updated_at"),
        "confirmed_at": mission.get("confirmed_at"),
        "completed_at": mission.get("completed_at"),
        "can_resume": can_resume,
        "status_command": commands["status_command"],
        "resume_command": commands["resume_command"],
        "attach_command": attach,
        "workbench_command": "agentdeck workbench",
        "controls": [
            _mission_control(
                "execute",
                "Resume mission",
                commands["resume_command"],
                "delegated",
                enabled=can_resume,
                blocker=resume_blocker,
            ),
            _mission_control(
                "inspect", "Inspect mission", commands["status_command"], "inspect"
            ),
            _mission_control("inspect", "Attach terminal", attach, "inspect"),
            _mission_control("inspect", "Open workbench", "agentdeck workbench", "inspect"),
        ],
        "safety": "inspect" if mode == "mission_status" else "delegated",
        "requires_explicit_user": True,
    }
    if mode != "mission_status":
        payload["confirmed"] = True if confirmed is None else confirmed
        turns: list[dict[str, object]] = []
        workflow_run_id = mission.get("workflow_run_id")
        if isinstance(workflow_run_id, str) and workflow_run_id:
            try:
                workflow = store.workflow_run_by_id(workflow_run_id)
            except KeyError as exc:
                if mission.get("status") not in {"stopped", "interrupted"}:
                    raise MissionRunError("mission workflow state invalid") from exc
                workflow = {"turns": []}
            for raw_turn in workflow.get("turns", []):
                if not isinstance(raw_turn, dict):
                    raise MissionRunError("mission workflow turns invalid")
                raw_handoff = raw_turn.get("handoff")
                handoff = None
                if isinstance(raw_handoff, dict):
                    handoff = {
                        field: raw_handoff.get(field)
                        for field in (
                            "step", "agent_id", "status", "summary", "verification",
                            "risks", "next_steps", "artifact_paths", "trace_command",
                        )
                    }
                turns.append(
                    {
                        "step": raw_turn.get("step"),
                        "agent_id": raw_turn.get("agent_id"),
                        "status": raw_turn.get("status"),
                        "handoff": handoff,
                    }
                )
        payload["turns"] = turns
        validation = validate_mission_run_contract(payload)
    else:
        validation = validate_mission_status_contract(payload)
    if not validation["ok"]:
        raise MissionRunError("mission response contract validation failed")
    return payload


def _audit(store: StateStore, event_type: str, **payload: object) -> None:
    store.append_event(EventRecord.create(event_type, dict(payload)))


def _mission_owns_spawn(
    events: list[dict[str, Any]],
    *,
    mission_id: str,
    agent_id: str,
    pane_id: str,
    session_name: str,
    cwd: str,
) -> bool:
    expected = {
        "mission_id": mission_id,
        "agent_id": agent_id,
        "pane_id": pane_id,
        "session_name": session_name,
        "cwd": cwd,
    }
    return any(
        event.get("event_type") == "agent_spawned"
        and isinstance(event.get("payload"), dict)
        and event["payload"] == expected
        for event in events
    )


def _stop_mission(
    store: StateStore,
    mission: dict[str, Any],
    *,
    reason: str,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    mission_id = str(mission["mission_id"])
    changes: dict[str, Any] = {
        "status": "stopped",
        "stop_reason": reason,
        "blockers": list(blockers or []),
        "can_start": False,
    }
    if mission.get("confirmed_at") is None:
        changes["confirmed_at"] = utc_now()
    stopped = store.update_mission(mission_id, **changes)
    try:
        _audit(store, "mission_stopped", mission_id=mission_id, reason=reason)
    except Exception:
        pass
    return stopped


def _frozen_preflight(
    config: ProjectConfig, store: StateStore, mission: dict[str, Any]
) -> tuple[dict[str, Any], tuple[AgentSpec, ...]]:
    mission_id = mission.get("mission_id")
    if (
        not is_canonical_mission_id(mission_id)
        or mission.get("schema_version") != MISSION_SCHEMA_VERSION
    ):
        raise MissionRunError("mission identity invalid")
    if mission.get("status") == "pending_confirmation" and (
        mission.get("can_start") is not True or mission.get("blockers")
    ):
        raise MissionRunError("mission is blocked")
    try:
        plan = store.plan_by_id(str(mission.get("plan_id") or ""))
    except KeyError:
        raise MissionRunError("plan_drift") from None
    if workflow_plan_hash(plan) != mission.get("plan_hash"):
        raise MissionRunError("plan_drift")
    selected = mission.get("selected_agents")
    if not isinstance(selected, list) or len(selected) < 2:
        raise MissionRunError("mission_config_drift")
    selected_ids = [item.get("agent_id") for item in selected if isinstance(item, dict)]
    if len(selected_ids) != len(selected) or len(set(selected_ids)) != len(selected_ids):
        raise MissionRunError("mission_config_drift")
    config_by_id = {agent.agent_id: agent for agent in config.agents}
    if len(config_by_id) != len(config.agents):
        raise MissionRunError("mission_config_drift")
    agents: list[AgentSpec] = []
    frozen_effective: list[tuple[object, object]] = []
    for row in selected:
        agent = config_by_id.get(str(row["agent_id"]))
        if agent is None or any(
            row.get(field) != getattr(agent, field)
            for field in ("provider", "role", "workspace_mode")
        ):
            raise MissionRunError("mission_config_drift")
        # Recreate the preview-time launch derivation, independent of current runtime state.
        frozen_binding = None
        if row.get("runtime_status") == "running":
            frozen_binding = {"status": "running", "pane_id": "%frozen"}
        effective = effective_mission_agent_for_binding(agent, config.leader, frozen_binding)
        if (
            row.get("effective_model") != effective.model
            or row.get("model_source") != effective.model_source
        ):
            raise MissionRunError("mission_config_drift")
        agents.append(effective.agent)
        frozen_effective.append((effective.model, effective.model_source))
    startup = mission.get("startup_actions")
    if not isinstance(startup, list) or len(startup) != len(selected):
        raise MissionRunError("mission_config_drift")
    for index, row in enumerate(startup):
        if not isinstance(row, dict) or row.get("agent_id") != selected_ids[index]:
            raise MissionRunError("mission_config_drift")
        expected_action = (
            "reuse"
            if selected[index].get("runtime_status") == "running"
            else "spawn"
        )
        if (
            row.get("action") != expected_action
            or row.get("runtime_status") != selected[index].get("runtime_status")
            or (row.get("effective_model"), row.get("model_source")) != frozen_effective[index]
        ):
            raise MissionRunError("mission_config_drift")
    if (
        mission.get("provider") != config.leader.provider.strip().lower()
        or mission.get("model") != config.leader.model
        or mission.get("leader_backend")
        != leader_backend_identity(config.leader.provider, config.leader.model)
        or plan.get("provider") != mission.get("provider")
        or plan.get("model") != mission.get("model")
        or plan.get("leader_backend") != mission.get("leader_backend")
    ):
        raise MissionRunError("mission_config_drift")
    validate_mission_plan(plan.get("plan"), selected_ids, mission.get("timeout_seconds"))
    steps = authorized_steps(plan)
    if len(steps) != mission.get("step_count") or any(
        step["agent_id"] not in selected_ids for step in steps
    ):
        raise MissionRunError("plan_drift")
    return plan, tuple(agents)


def _prepare_selected_workers(
    *,
    config: ProjectConfig,
    store: StateStore,
    backend: RuntimeBackend,
    mission: dict[str, Any],
    agents: tuple[AgentSpec, ...],
    resuming: bool,
) -> tuple[tuple[AgentSpec, str], ...]:
    prepared: list[tuple[AgentSpec, str]] = []
    session_created = False
    actions = mission["startup_actions"]
    for agent, frozen_action in zip(agents, actions):
        binding = store.agent_binding(agent.agent_id)
        reusable = False
        try:
            reusable = mission_binding_reusable(binding) and backend.pane_exists(
                config.runtime, str(binding.get("pane_id"))
            )
        except Exception:
            reusable = False
        requested_action = frozen_action["action"]
        _audit(
            store,
            "mission_worker_preparing",
            mission_id=mission["mission_id"],
            agent_id=agent.agent_id,
            action=requested_action,
        )
        if requested_action == "reuse":
            if not reusable:
                _audit(
                    store,
                    "mission_worker_blocked",
                    mission_id=mission["mission_id"],
                    agent_id=agent.agent_id,
                    reason="worker_runtime_drift",
                )
                raise MissionRunError("worker_runtime_drift")
            prepared.append((agent, str(binding["pane_id"])))
            continue
        if reusable:
            pane_id = str(binding["pane_id"])
            try:
                events = store.all_events()
                if not isinstance(events, list) or any(
                    not isinstance(event, dict) for event in events
                ):
                    raise ValueError("invalid audit")
                owned = _mission_owns_spawn(
                    events,
                    mission_id=str(mission["mission_id"]),
                    agent_id=agent.agent_id,
                    pane_id=pane_id,
                    session_name=config.runtime.session_name,
                    cwd=config.root,
                )
            except Exception:
                raise MissionRunError("mission_audit_invalid") from None
            if resuming and owned:
                prepared.append((agent, pane_id))
                continue
            _audit(
                store,
                "mission_worker_blocked",
                mission_id=mission["mission_id"],
                agent_id=agent.agent_id,
                reason="worker_runtime_drift",
            )
            raise MissionRunError("worker_runtime_drift")
        try:
            if not session_created:
                backend.create_session(config.runtime)
                session_created = True
            pane_id = backend.spawn_agent(config.runtime, agent, config.root)
            if not isinstance(pane_id, str) or not pane_id:
                raise RuntimeError("invalid pane")
            store.bind_agent(
                AgentRuntimeBinding(
                    agent.agent_id,
                    pane_id,
                    config.runtime.session_name,
                    config.root,
                    "running",
                )
            )
            _audit(
                store,
                "agent_spawned",
                mission_id=mission["mission_id"],
                agent_id=agent.agent_id,
                pane_id=pane_id,
                session_name=config.runtime.session_name,
                cwd=config.root,
            )
            prepared.append((agent, pane_id))
        except Exception:
            _audit(
                store,
                "mission_worker_blocked",
                mission_id=mission["mission_id"],
                agent_id=agent.agent_id,
                reason="worker_start_failed",
            )
            raise MissionRunError("worker_start_failed") from None
    return tuple(prepared)


def _readiness_reason(batch: WorkerReadinessBatch) -> str:
    statuses = {item.status for item in batch.results}
    if "setup_required" in statuses:
        return "worker_setup_required"
    if "pane_lost" in statuses:
        return "worker_pane_lost"
    if "timeout" in statuses or batch.timed_out:
        return "worker_readiness_timeout"
    return "worker_readiness_failed"


def _execute_mission(
    *, config: ProjectConfig, store: StateStore, backend: RuntimeBackend,
    mission_id: str, readiness_timeout_seconds: int, resuming: bool,
) -> dict[str, object]:
    mission = store.mission_by_id(mission_id)
    if mission.get("status") in {"preparing", "running", "completed"}:
        return mission_status_payload(config, store, mission, mode="mission_resume" if resuming else "mission_run")
    if resuming and mission.get("status") not in {"stopped", "interrupted"}:
        raise MissionRunError("mission is not resumable")
    if not resuming and mission.get("status") != "pending_confirmation":
        raise MissionRunError("mission is not pending confirmation")
    if resuming and mission.get("blockers"):
        raise MissionRunError("mission is blocked")
    try:
        plan, selected_agents = _frozen_preflight(config, store, mission)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, MissionRunError) else "mission_config_drift"
        if reason in {"plan_drift", "mission_config_drift"}:
            mission = _stop_mission(store, mission, reason=reason, blockers=[reason.replace("_", " ")])
            return mission_status_payload(config, store, mission, mode="mission_resume" if resuming else "mission_run")
        raise MissionRunError("mission validation failed") from None
    now = mission.get("confirmed_at") or utc_now()
    claim = store.claim_mission_execution(
        mission_id, resuming=resuming, confirmed_at=str(now)
    )
    mission = claim["mission"]
    if claim["claimed"] is not True:
        return mission_status_payload(
            config,
            store,
            mission,
            mode="mission_resume" if resuming else "mission_run",
        )
    try:
        _audit(
            store,
            "mission_resumed" if resuming else "mission_confirmed",
            mission_id=mission_id,
            plan_id=mission["plan_id"],
        )
    except Exception:
        mission = _stop_mission(
            store, mission, reason="mission_audit_failed"
        )
        return mission_status_payload(
            config,
            store,
            mission,
            mode="mission_resume" if resuming else "mission_run",
        )
    try:
        prepared = _prepare_selected_workers(
            config=config,
            store=store,
            backend=backend,
            mission=mission,
            agents=selected_agents,
            resuming=resuming,
        )
    except MissionRunError as exc:
        reason = str(exc)
        blockers = (
            [reason.replace("_", " ")]
            if reason in {"worker_runtime_drift", "mission_audit_invalid"}
            else None
        )
        mission = _stop_mission(
            store,
            store.mission_by_id(mission_id),
            reason=reason,
            blockers=blockers,
        )
        return mission_status_payload(config, store, mission, mode="mission_resume" if resuming else "mission_run")
    try:
        batch = wait_for_worker_readiness(
            runtime_config=config.runtime, backend=backend, selected=prepared,
            timeout_seconds=readiness_timeout_seconds, poll_interval=0.25,
        )
    except Exception:
        mission = _stop_mission(store, store.mission_by_id(mission_id), reason="worker_readiness_failed")
        return mission_status_payload(config, store, mission, mode="mission_resume" if resuming else "mission_run")
    expected_ready_ids = [agent.agent_id for agent, _pane_id in prepared]
    if [item.agent_id for item in batch.results] != expected_ready_ids:
        mission = _stop_mission(store, store.mission_by_id(mission_id), reason="worker_readiness_failed")
        return mission_status_payload(config, store, mission, mode="mission_resume" if resuming else "mission_run")
    if not batch.all_ready:
        reason = _readiness_reason(batch)
        for item in batch.results:
            if item.status == "ready":
                _audit(
                    store,
                    "mission_worker_ready",
                    mission_id=mission_id,
                    agent_id=item.agent_id,
                    status="ready",
                )
            else:
                _audit(store, "mission_worker_blocked", mission_id=mission_id, agent_id=item.agent_id, reason=reason)
        mission = _stop_mission(store, store.mission_by_id(mission_id), reason=reason)
        return mission_status_payload(config, store, mission, mode="mission_resume" if resuming else "mission_run")
    for item in batch.results:
        _audit(store, "mission_worker_ready", mission_id=mission_id, agent_id=item.agent_id, status="ready")
    run_id = mission.get("workflow_run_id")
    if run_id:
        try:
            workflow = store.workflow_run_by_id(str(run_id))
            expected_steps = authorized_steps(plan)
            if (
                not isinstance(workflow, dict)
                or workflow.get("run_id") != run_id
                or workflow.get("plan_id") != mission.get("plan_id")
                or workflow.get("plan_hash") != mission.get("plan_hash")
                or workflow.get("authorized_steps") != expected_steps
            ):
                raise ValueError("workflow drift")
        except Exception:
            mission = _stop_mission(
                store,
                mission,
                reason="workflow_state_drift",
                blockers=["workflow state drift"],
            )
            return mission_status_payload(config, store, mission, mode="mission_resume" if resuming else "mission_run")
        store.update_workflow_run(str(run_id), status="running", stop_reason=None)
        _audit(
            store,
            "workflow_resumed",
            run_id=run_id,
            plan_id=mission["plan_id"],
            current_step=workflow.get("current_step"),
        )
    else:
        workflow = store.create_workflow_run(
            plan_id=str(mission["plan_id"]), plan_hash=str(mission["plan_hash"]),
            timeout_seconds=int(mission["timeout_seconds"]), authorized_steps=authorized_steps(plan),
        )
        run_id = workflow["run_id"]
        mission = store.update_mission(mission_id, workflow_run_id=run_id)
        _audit(
            store,
            "workflow_started",
            run_id=run_id,
            plan_id=mission["plan_id"],
            plan_hash=mission["plan_hash"],
            step_count=mission["step_count"],
            timeout_seconds=mission["timeout_seconds"],
        )
    mission = store.update_mission(mission_id, status="running")
    _audit(store, "mission_workflow_started", mission_id=mission_id, workflow_run_id=run_id, plan_id=mission["plan_id"])
    selected_config = replace(config, agents=selected_agents)
    try:
        result = run_sequential_workflow(config=selected_config, store=store, backend=backend, run_id=str(run_id))
    except KeyboardInterrupt:
        raise
    except Exception:
        result = store.update_workflow_run(str(run_id), status="stopped", stop_reason="workflow_failed")
    current_step = min(int(result.get("current_step") or 0), int(mission["step_count"]))
    if result.get("status") == "completed":
        mission = store.update_mission(mission_id, status="completed", current_step=int(mission["step_count"]), stop_reason=None)
        _audit(store, "mission_completed", mission_id=mission_id, workflow_run_id=run_id)
    elif result.get("status") == "interrupted":
        mission = store.update_mission(mission_id, status="interrupted", current_step=current_step, stop_reason=str(result.get("stop_reason") or "interrupted"))
        _audit(store, "mission_stopped", mission_id=mission_id, reason="interrupted")
    else:
        mission = _stop_mission(store, mission, reason=str(result.get("stop_reason") or "workflow_failed"))
        if current_step > int(mission.get("current_step") or 0):
            mission = store.update_mission(mission_id, current_step=current_step)
    return mission_status_payload(config, store, mission, mode="mission_resume" if resuming else "mission_run")


def run_mission(*, config: ProjectConfig, store: StateStore, backend: RuntimeBackend, mission_id: str, readiness_timeout_seconds: int = 60) -> dict[str, object]:
    return _execute_mission(config=config, store=store, backend=backend, mission_id=mission_id, readiness_timeout_seconds=readiness_timeout_seconds, resuming=False)


def resume_mission(*, config: ProjectConfig, store: StateStore, backend: RuntimeBackend, mission_id: str, readiness_timeout_seconds: int = 60) -> dict[str, object]:
    return _execute_mission(config=config, store=store, backend=backend, mission_id=mission_id, readiness_timeout_seconds=readiness_timeout_seconds, resuming=True)


def interrupt_mission(store: StateStore, mission_id: str) -> dict[str, Any]:
    mission = store.mission_by_id(mission_id)
    run_id = mission.get("workflow_run_id")
    if run_id:
        try:
            store.workflow_run_by_id(str(run_id))
            store.update_workflow_run(
                str(run_id), status="interrupted", stop_reason="interrupted"
            )
            try:
                _audit(
                    store,
                    "workflow_stopped",
                    run_id=run_id,
                    reason="interrupted",
                )
            except Exception:
                pass
        except Exception:
            return _stop_mission(
                store,
                mission,
                reason="workflow_state_drift",
                blockers=["workflow state drift"],
            )
    if mission.get("status") == "preparing":
        return _stop_mission(store, mission, reason="interrupted")
    mission = store.update_mission(mission_id, status="interrupted", stop_reason="interrupted")
    try:
        _audit(store, "mission_stopped", mission_id=mission_id, reason="interrupted")
    except Exception:
        pass
    return mission
