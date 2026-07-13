from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
from typing import Any, Callable, Mapping

from .config import CONFIG_DIR, ensure_project_layout, load_config, project_root
from .conversation.lifecycle import validate_conversation_history
from .conversation.models import ConversationMutation
from .daemon.lifecycle import validate_daemon_record
from .daemon.lease import (
    controller_lease_is_active,
    LeaseTransition,
    LeaseError,
    validate_daemon_event_record,
    validate_daemon_event_outbox,
    validate_lease_transition,
)
from .mission import (
    MISSION_SCHEMA_VERSION,
    MISSION_STATUSES,
    MISSION_INVALID_BLOCKERS_BLOCKER,
    compact_mission_blockers,
    compact_mission_worker_entries,
    is_canonical_mission_id,
    mission_commands,
    mission_status_transition_allowed,
)
from .mission_authority import canonical_workflow_plan_hash
from .models import PROJECT_VIEW_SCHEMA_VERSION, AgentRuntimeBinding, EventRecord, ProjectConfig, ProjectView, new_id, utc_now
from .runtime.protocol import (
    AGENT_SESSION_STATES,
    PERMISSION_STATES,
    TURN_STATES,
    TRANSPORT_KINDS,
    UPDATE_KINDS,
    TransportCapabilities,
    build_agent_session,
    build_permission_request,
    build_protocol_transition,
    build_transport_update,
    build_turn,
    validate_protocol_transition_record,
    validate_transition_edge,
)
from .runtime.acp_mapping import (
    MAX_ACP_TURN_PAYLOAD_BYTES,
    MAX_ACP_UPDATES_PER_TURN,
    MAX_ACP_TERMINAL_UPDATE_BYTES,
)


_EXECUTION_SNAPSHOT_FIELDS = frozenset(
    {
        "mission",
        "workers",
        "policy",
        "limits",
        "mission_hash",
        "policy_hash",
        "execution_hash",
    }
)
_EXECUTION_SNAPSHOT_MAX_BYTES = 256 * 1024
_EXECUTION_SNAPSHOT_MAX_DEPTH = 32
_MISSION_ATTEMPT_FIELDS = frozenset(
    {
        "attempt_id",
        "mission_id",
        "step_id",
        "agent_id",
        "configured_transport",
        "dispatch_key",
        "snapshot_hash",
        "state",
        "created_at",
        "updated_at",
        "receipt_summary",
        "blocker",
        "terminal_reason",
    }
)
_MISSION_ATTEMPT_STATES = frozenset(
    {
        "prepared",
        "submitted",
        "running",
        "completed",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
        "ambiguous",
    }
)
_MISSION_ATTEMPT_ACTIVE_STATES = frozenset({"prepared", "submitted", "running"})
_MISSION_ATTEMPT_RETRYABLE_STATES = frozenset({"failed"})


class MissionStateError(ValueError):
    pass


def _strict_event_journal_ids(
    path: Path,
    *,
    malformed_error: str,
    duplicate_error: str,
) -> set[str]:
    if not path.exists():
        return set()
    event_ids: list[str] = []

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> object:
        raise ValueError("non-finite JSON number")

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(
                    line,
                    object_pairs_hook=reject_duplicate_keys,
                    parse_constant=reject_constant,
                )
                event_ids.append(validate_daemon_event_record(item))
            except (json.JSONDecodeError, LeaseError, ValueError):
                raise ValueError(malformed_error) from None
    if len(event_ids) != len(set(event_ids)):
        raise ValueError(duplicate_error)
    return set(event_ids)


def _validated_protocol_event_outbox_ids(value: object) -> set[str]:
    if type(value) is not list:
        raise ValueError("protocol event outbox is invalid")
    event_ids: list[str] = []
    for item in value:
        try:
            event_ids.append(validate_daemon_event_record(item))
        except LeaseError:
            raise ValueError("protocol event outbox is invalid") from None
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("duplicate protocol event identity")
    return set(event_ids)


def _validate_snapshot_json(value: object, *, depth: int = 0) -> None:
    if depth > _EXECUTION_SNAPSHOT_MAX_DEPTH:
        raise ValueError("execution snapshot nesting is invalid")
    if value is None:
        return
    if type(value) is bool or type(value) is float:
        raise ValueError("execution snapshot value is invalid")
    if type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise ValueError("execution snapshot integer is invalid")
        return
    if type(value) is str:
        if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("execution snapshot string is invalid")
        return
    if type(value) is list:
        for item in value:
            _validate_snapshot_json(item, depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not key:
                raise ValueError("execution snapshot key is invalid")
            _validate_snapshot_json(key, depth=depth + 1)
            _validate_snapshot_json(item, depth=depth + 1)
        return
    raise ValueError("execution snapshot value is invalid")


def _canonical_snapshot_bytes(value: object) -> bytes:
    _validate_snapshot_json(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (UnicodeEncodeError, ValueError, TypeError):
        raise ValueError("execution snapshot encoding is invalid") from None
    if len(encoded) > _EXECUTION_SNAPSHOT_MAX_BYTES:
        raise ValueError("execution snapshot is too large")
    return encoded


def canonical_snapshot_hash(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_snapshot_bytes(value)).hexdigest()}"


def _validate_mission_attempt_record(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _MISSION_ATTEMPT_FIELDS:
        raise ValueError("mission attempt state invalid")
    if (
        type(value.get("attempt_id")) is not str
        or re.fullmatch(r"mat_[0-9a-f]{12}", value["attempt_id"]) is None
        or not is_canonical_mission_id(value.get("mission_id"))
        or type(value.get("step_id")) is not str
        or re.fullmatch(r"step_[1-9][0-9]*", value["step_id"]) is None
        or type(value.get("agent_id")) is not str
        or not value["agent_id"]
        or value.get("configured_transport") not in {"acp", "tmux"}
        or type(value.get("dispatch_key")) is not str
        or re.fullmatch(r"dsp_[0-9a-f]{32}", value["dispatch_key"]) is None
        or type(value.get("snapshot_hash")) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", value["snapshot_hash"]) is None
        or value.get("state") not in _MISSION_ATTEMPT_STATES
    ):
        raise ValueError("mission attempt state invalid")
    timestamps: list[datetime] = []
    for field in ("created_at", "updated_at"):
        raw = value.get(field)
        if type(raw) is not str:
            raise ValueError("mission attempt state invalid")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            raise ValueError("mission attempt state invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("mission attempt state invalid")
        timestamps.append(parsed)
    if timestamps[1] < timestamps[0]:
        raise ValueError("mission attempt state invalid")
    if value["state"] == "prepared" and timestamps[1] != timestamps[0]:
        raise ValueError("mission attempt state invalid")
    for field in ("receipt_summary", "blocker", "terminal_reason"):
        item = value.get(field)
        if item is not None and (type(item) is not str or not item):
            raise ValueError("mission attempt state invalid")
    return copy.deepcopy(value)


def validate_mission_attempt_record(value: object) -> dict[str, Any]:
    """Validate the exact durable Task 7 Mission attempt record."""
    return _validate_mission_attempt_record(value)


def validate_execution_snapshot(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _EXECUTION_SNAPSHOT_FIELDS:
        raise ValueError("execution snapshot fields are invalid")
    _canonical_snapshot_bytes(value)
    snapshot = copy.deepcopy(value)
    for name in ("mission", "policy", "limits"):
        if type(snapshot[name]) is not dict:
            raise ValueError(f"execution snapshot {name} is invalid")
    if type(snapshot["workers"]) is not list or len(snapshot["workers"]) < 2:
        raise ValueError("execution snapshot workers are invalid")
    mission = snapshot["mission"]
    mission_fields = {
        "mission_id",
        "schema_version",
        "plan_id",
        "plan_hash",
        "goal_hash",
        "summary_hash",
        "steps",
        "project_scope_hash",
        "action_classes",
        "skill_provenance",
        "memory_provenance",
        "declared_tests_hash",
        "acceptance_criteria_hash",
    }
    if (
        set(mission) != mission_fields
        or not is_canonical_mission_id(mission.get("mission_id"))
        or mission.get("schema_version") != MISSION_SCHEMA_VERSION
        or type(mission.get("plan_id")) is not str
        or re.fullmatch(r"pln_[0-9a-f]{12}", mission["plan_id"]) is None
    ):
        raise ValueError("execution snapshot mission is invalid")
    hash_fields = (
        "plan_hash",
        "goal_hash",
        "summary_hash",
        "project_scope_hash",
    )
    if any(
        type(mission.get(field)) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", mission[field]) is None
        for field in hash_fields
    ):
        raise ValueError("execution snapshot mission is invalid")
    for optional_hash in ("declared_tests_hash", "acceptance_criteria_hash"):
        value = mission.get(optional_hash)
        if value is not None and (
            type(value) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
        ):
            raise ValueError("execution snapshot mission is invalid")
    if mission.get("action_classes") != [
        "worker_task",
        "declared_local_verification",
    ]:
        raise ValueError("execution snapshot mission is invalid")
    steps = mission.get("steps")
    if type(steps) is not list or not steps:
        raise ValueError("execution snapshot mission is invalid")
    step_agents: list[str] = []
    for position, step in enumerate(steps, start=1):
        if (
            type(step) is not dict
            or set(step) != {"step_id", "position", "agent_id", "role", "task_hash"}
            or step.get("step_id") != f"step_{position}"
            or type(step.get("position")) is not int
            or step["position"] != position
            or type(step.get("agent_id")) is not str
            or not step["agent_id"]
            or type(step.get("role")) is not str
            or not step["role"]
            or type(step.get("task_hash")) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", step["task_hash"]) is None
        ):
            raise ValueError("execution snapshot mission is invalid")
        step_agents.append(step["agent_id"])
    workers = snapshot["workers"]
    worker_ids: list[str] = []
    for worker in workers:
        if (
            type(worker) is not dict
            or set(worker)
            != {
                "agent_id",
                "role",
                "provider",
                "workspace_mode",
                "configured_transport",
                "capability_provenance",
            }
            or any(
                type(worker.get(field)) is not str or not worker[field]
                for field in ("agent_id", "role", "provider", "workspace_mode")
            )
            or worker.get("configured_transport") not in {"acp", "tmux"}
        ):
            raise ValueError("execution snapshot workers are invalid")
        provenance = worker.get("capability_provenance")
        if (
            type(provenance) is not dict
            or set(provenance) != {"source", "transport", "adapter_configuration"}
            or provenance.get("source") != "project_config"
            or provenance.get("transport") != worker["configured_transport"]
            or provenance.get("adapter_configuration")
            not in {"present", "not_applicable"}
            or (
                worker["configured_transport"] == "acp"
                and provenance["adapter_configuration"] != "present"
            )
        ):
            raise ValueError("execution snapshot workers are invalid")
        worker_ids.append(worker["agent_id"])
    if len(worker_ids) != len(set(worker_ids)) or any(
        agent_id not in worker_ids for agent_id in step_agents
    ):
        raise ValueError("execution snapshot workers are invalid")
    policy = snapshot["policy"]
    if (
        set(policy)
        != {
            "approval_mode",
            "autonomous_allowed_agents",
            "autonomous_max_approvals",
            "policy_source",
        }
        or policy.get("approval_mode")
        not in {"confirm", "approve", "auto_approve", "autonomous"}
        or type(policy.get("autonomous_allowed_agents")) is not list
        or any(type(item) is not str or not item for item in policy["autonomous_allowed_agents"])
        or type(policy.get("autonomous_max_approvals")) is not int
        or policy["autonomous_max_approvals"] < 0
        or policy.get("policy_source") != "project_config"
    ):
        raise ValueError("execution snapshot policy is invalid")
    limits = snapshot["limits"]
    if set(limits) != {"step_count", "timeout_seconds", "retry_limit", "worker_budget"}:
        raise ValueError("execution snapshot limits are invalid")
    if any(
        type(limits.get(field)) is not int or limits[field] < minimum
        for field, minimum in (
            ("step_count", 1),
            ("timeout_seconds", 1),
            ("retry_limit", 0),
            ("worker_budget", 1),
        )
    ) or limits["step_count"] != len(steps) or limits["worker_budget"] != len(steps):
        raise ValueError("execution snapshot limits are invalid")
    skill_provenance = mission.get("skill_provenance")
    if type(skill_provenance) is not list:
        raise ValueError("execution snapshot mission is invalid")
    for item in skill_provenance:
        if (
            type(item) is not dict
            or set(item) != {"agent_id", "name_hash", "content_hash", "source_kind"}
            or type(item.get("agent_id")) is not str
            or not item["agent_id"]
            or item.get("source_kind") not in {"builtin", "project", "external"}
            or any(
                type(item.get(field)) is not str
                or re.fullmatch(r"sha256:[0-9a-f]{64}", item[field]) is None
                for field in ("name_hash", "content_hash")
            )
        ):
            raise ValueError("execution snapshot mission is invalid")
    memory_provenance = mission.get("memory_provenance")
    if type(memory_provenance) is not list:
        raise ValueError("execution snapshot mission is invalid")
    seen_scopes: set[str] = set()
    for item in memory_provenance:
        if (
            type(item) is not dict
            or set(item) != {"scope", "content_hash", "line_count", "byte_count"}
            or item.get("scope") not in {"project", "global"}
            or item["scope"] in seen_scopes
            or type(item.get("content_hash")) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item["content_hash"]) is None
            or type(item.get("line_count")) is not int
            or item["line_count"] < 0
            or type(item.get("byte_count")) is not int
            or item["byte_count"] < 0
        ):
            raise ValueError("execution snapshot mission is invalid")
        seen_scopes.add(item["scope"])
    if snapshot["mission_hash"] != canonical_snapshot_hash(snapshot["mission"]):
        raise ValueError("execution snapshot mission hash is invalid")
    if snapshot["policy_hash"] != canonical_snapshot_hash(snapshot["policy"]):
        raise ValueError("execution snapshot policy hash is invalid")
    body = {
        key: snapshot[key]
        for key in (
            "mission",
            "workers",
            "policy",
            "limits",
            "mission_hash",
            "policy_hash",
        )
    }
    if snapshot["execution_hash"] != canonical_snapshot_hash(body):
        raise ValueError("execution snapshot execution hash is invalid")
    return snapshot


def execution_policy_snapshot(config: ProjectConfig) -> dict[str, object]:
    approval_mode = config.leader.approval_mode
    if approval_mode not in {"confirm", "approve", "auto_approve", "autonomous"}:
        raise ValueError("execution policy invalid")
    if any(type(item) is not str or not item for item in config.autonomous.allowed_agents):
        raise ValueError("execution policy invalid")
    if (
        type(config.autonomous.max_approvals) is not int
        or config.autonomous.max_approvals < 0
    ):
        raise ValueError("execution policy invalid")
    return {
        "approval_mode": approval_mode,
        "autonomous_allowed_agents": list(config.autonomous.allowed_agents),
        "autonomous_max_approvals": config.autonomous.max_approvals,
        "policy_source": "project_config",
    }


def _snapshot_skill_provenance(plan: Mapping[str, object]) -> list[dict[str, object]]:
    raw_context = plan.get("skill_context")
    if not isinstance(raw_context, Mapping):
        return []
    raw_items = raw_context.get("items")
    if not isinstance(raw_items, list):
        return []
    compact: list[dict[str, object]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise ValueError("execution snapshot invalid")
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
            raise ValueError("execution snapshot invalid")
        source_kind = (
            "builtin"
            if source == "builtin"
            else "project"
            if source == "project"
            else "external"
        )
        compact.append(
            {
                "agent_id": agent_id,
                "name_hash": canonical_snapshot_hash({"name": name}),
                "content_hash": content_hash,
                "source_kind": source_kind,
            }
        )
    return compact


def _snapshot_optional_fact_hash(
    plan: Mapping[str, object], field: str
) -> str | None:
    value = plan.get(field)
    return None if value is None else canonical_snapshot_hash({field: value})


def collect_execution_memory_provenance(root: Path) -> list[dict[str, object]]:
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
                raise ValueError("execution snapshot invalid")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
                    raise ValueError("execution snapshot invalid")
                chunks: list[bytes] = []
                remaining = info.st_size
                while remaining:
                    chunk = os.read(descriptor, min(remaining, 64 * 1024))
                    if not chunk:
                        raise ValueError("execution snapshot invalid")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if os.read(descriptor, 1):
                    raise ValueError("execution snapshot invalid")
                data = b"".join(chunks)
            finally:
                os.close(descriptor)
            text = data.decode("utf-8")
        except ValueError:
            raise
        except (OSError, UnicodeError):
            raise ValueError("execution snapshot invalid") from None
        records.append(
            {
                "scope": scope,
                "content_hash": f"sha256:{hashlib.sha256(data).hexdigest()}",
                "line_count": len(text.splitlines()),
                "byte_count": len(data),
            }
        )
    return records


def build_execution_snapshot_authority(
    config: ProjectConfig,
    mission: Mapping[str, object],
    plan: Mapping[str, object],
    policy: Mapping[str, object],
    *,
    memory_provenance: list[dict[str, object]],
) -> dict[str, Any]:
    mission_id = mission.get("mission_id")
    if not is_canonical_mission_id(mission_id):
        raise ValueError("execution snapshot invalid")
    plan_id = mission.get("plan_id")
    if type(plan_id) is not str or plan.get("plan_id") != plan_id:
        raise ValueError("execution snapshot invalid")
    try:
        current_plan_hash = canonical_workflow_plan_hash(plan)
    except (TypeError, ValueError, OverflowError):
        raise MissionStateError("plan hash drift") from None
    if current_plan_hash != mission.get("plan_hash"):
        raise MissionStateError("plan hash drift")
    if mission.get("schema_version") != MISSION_SCHEMA_VERSION:
        raise ValueError("execution snapshot invalid")
    raw_plan = plan.get("plan")
    if type(raw_plan) is not dict:
        raise ValueError("execution snapshot invalid")
    raw_steps = raw_plan.get("steps")
    selected = mission.get("selected_agents")
    if type(raw_steps) is not list or not raw_steps or type(selected) is not list:
        raise ValueError("execution snapshot invalid")
    selected_ids = [
        item.get("agent_id") if type(item) is dict else None for item in selected
    ]
    if len(selected_ids) < 2 or len(set(selected_ids)) != len(selected_ids):
        raise ValueError("execution snapshot invalid")
    agents_by_id = {agent.agent_id: agent for agent in config.agents}
    if len(agents_by_id) != len(config.agents):
        raise ValueError("execution snapshot invalid")
    workers: list[dict[str, object]] = []
    for selected_row in selected:
        if type(selected_row) is not dict:
            raise ValueError("execution snapshot invalid")
        agent = agents_by_id.get(selected_row.get("agent_id"))
        if agent is None or agent.transport not in {"acp", "tmux"}:
            raise ValueError("execution snapshot invalid")
        if agent.transport == "acp" and not agent.transport_command:
            raise ValueError("execution snapshot invalid")
        if any(type(part) is not str or not part for part in agent.transport_command):
            raise ValueError("execution snapshot invalid")
        if any(
            selected_row.get(field) != getattr(agent, field)
            for field in ("provider", "role", "workspace_mode")
        ):
            raise ValueError("execution snapshot invalid")
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
            raise ValueError("execution snapshot invalid")
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
            raise ValueError("execution snapshot invalid")
        compact_steps.append(
            {
                "step_id": f"step_{position}",
                "position": position,
                "agent_id": agent_id,
                "role": role,
                "task_hash": canonical_snapshot_hash({"task": task}),
            }
        )
    if len(compact_steps) != mission.get("step_count"):
        raise ValueError("execution snapshot invalid")
    root = Path(config.root)
    plan_hash = mission.get("plan_hash")
    if (
        type(plan_hash) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", plan_hash) is None
    ):
        raise ValueError("execution snapshot invalid")
    policy_body = copy.deepcopy(dict(policy))
    if policy_body != execution_policy_snapshot(config):
        raise ValueError("execution snapshot invalid")
    mission_body: dict[str, object] = {
        "mission_id": mission_id,
        "schema_version": mission.get("schema_version"),
        "plan_id": plan_id,
        "plan_hash": plan_hash,
        "goal_hash": canonical_snapshot_hash({"goal": raw_plan.get("goal")}),
        "summary_hash": canonical_snapshot_hash({"summary": raw_plan.get("summary")}),
        "steps": compact_steps,
        "project_scope_hash": canonical_snapshot_hash({"project_root": str(root)}),
        "action_classes": ["worker_task", "declared_local_verification"],
        "skill_provenance": _snapshot_skill_provenance(plan),
        "memory_provenance": copy.deepcopy(memory_provenance),
        "declared_tests_hash": _snapshot_optional_fact_hash(
            raw_plan, "declared_tests"
        ),
        "acceptance_criteria_hash": _snapshot_optional_fact_hash(
            raw_plan, "acceptance_criteria"
        ),
    }
    limits: dict[str, object] = {
        "step_count": mission.get("step_count"),
        "timeout_seconds": mission.get("timeout_seconds"),
        "retry_limit": mission.get("retry_limit"),
        "worker_budget": mission.get("step_count"),
    }
    mission_hash = canonical_snapshot_hash(mission_body)
    policy_hash = canonical_snapshot_hash(policy_body)
    execution_body: dict[str, object] = {
        "mission": mission_body,
        "workers": workers,
        "policy": policy_body,
        "limits": limits,
        "mission_hash": mission_hash,
        "policy_hash": policy_hash,
    }
    return validate_execution_snapshot(
        {**execution_body, "execution_hash": canonical_snapshot_hash(execution_body)}
    )


def derive_attempt_dispatch_key(
    mission_id: str,
    step_id: str,
    agent_id: str,
    configured_transport: str,
    snapshot_hash: str,
    *,
    attempt_ordinal: int = 1,
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
        or type(attempt_ordinal) is not int
        or attempt_ordinal < 1
    ):
        raise ValueError("attempt identity invalid")
    digest = canonical_snapshot_hash(
        {
            "mission_id": mission_id,
            "step_id": step_id,
            "agent_id": agent_id,
            "configured_transport": configured_transport,
            "snapshot_hash": snapshot_hash,
            "attempt_ordinal": attempt_ordinal,
        }
    ).removeprefix("sha256:")
    return f"dsp_{digest[:32]}"


def leader_provider_backend(provider: str | None) -> str:
    if provider in {"deepseek", "openai-compatible"}:
        return "api"
    if provider in {"codex-cli", "claude-cli"}:
        return "cli"
    if provider == "fake":
        return "local"
    return "unknown"


def leader_provider_transport(provider: str | None) -> str:
    if provider in {"deepseek", "openai-compatible"}:
        return "http"
    if provider in {"codex-cli", "claude-cli"}:
        return "subprocess"
    if provider == "fake":
        return "local"
    return "unknown"


def leader_reasoning_backend(provider: str | None) -> str:
    if provider in {"deepseek", "openai-compatible"}:
        return "api-llm"
    if provider in {"codex-cli", "claude-cli"}:
        return "cli-subprocess"
    if provider == "fake":
        return "local-fake"
    return "unknown"


def leader_backend_identity(provider: str | None, model: str | None, dispatch_ready: bool = False) -> dict[str, Any]:
    return {
        "agent_id": "leader",
        "provider": provider,
        "model": model,
        "provider_backend": leader_provider_backend(provider),
        "provider_transport": leader_provider_transport(provider),
        "reasoning_backend": leader_reasoning_backend(provider),
        "runtime_kind": "logical_leader",
        "pane_backed": False,
        "pane_id": None,
        "approval_required": True,
        "dispatch_ready": dispatch_ready,
    }


def leader_coordination_roles(provider: str | None, model: str | None) -> list[dict[str, Any]]:
    return [
        {
            "role_id": "frontdesk",
            "label": "Frontdesk intake",
            "provider": "local-rule",
            "model": "deterministic",
            "lifecycle": "persistent",
            "responsibility": "Intake human requests and route them without provider planning.",
            "state_source": "chat_turns",
            "runtime_kind": "logical_role",
            "pane_backed": False,
            "pane_id": None,
            "dispatch_ready": False,
            "approval_required": False,
            "next_command": 'agentdeck leader chat --message "frontdesk <goal>"',
        },
        {
            "role_id": "planner",
            "label": "Planner",
            "provider": provider,
            "model": model,
            "lifecycle": "persistent",
            "responsibility": "Create macro plans and acceptance criteria without dispatching workers.",
            "state_source": "plans",
            "runtime_kind": "logical_role",
            "pane_backed": False,
            "pane_id": None,
            "dispatch_ready": False,
            "approval_required": True,
            "next_command": "agentdeck leader plan --task <goal>",
        },
        {
            "role_id": "orchestrator",
            "label": "Orchestrator",
            "provider": provider,
            "model": model,
            "lifecycle": "persistent",
            "responsibility": "Review plans, choose approval-gated actions, and coordinate worker handoff.",
            "state_source": "leader_actions",
            "runtime_kind": "logical_role",
            "pane_backed": False,
            "pane_id": None,
            "dispatch_ready": False,
            "approval_required": True,
            "next_command": "agentdeck leader review --plan-id <plan_id>",
        },
    ]


class StateStore:
    def __init__(self, root: Path | None = None, *, ensure_layout: bool = True) -> None:
        self.root = root or project_root()
        self.deck_dir = (
            ensure_project_layout(self.root)
            if ensure_layout
            else agentdeck_dir(self.root)
        )
        self.state_path = self.deck_dir / "state" / "state.json"
        self.events_path = self.deck_dir / "state" / "events.jsonl"

    @classmethod
    def open_existing(cls, root: Path | None = None) -> StateStore:
        return cls(root, ensure_layout=False)

    def load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "agents": {},
                "messages": [],
                "attempts": [],
                "mission_attempts": [],
                "jobs": [],
                "replies": [],
                "artifacts": [],
                "missions": [],
                "plans": [],
                "approvals": [],
                "chat_turns": [],
                "leader_errors": [],
                "leader_actions": [],
                "skill_loads": [],
                "skill_suggestions": [],
                "memory_suggestions": [],
                "agent_sessions": [],
                "protocol_turns": [],
                "transport_updates": [],
                "permission_requests": [],
                "protocol_state_transitions": [],
                "protocol_event_outbox": [],
                "conversation_sessions": [],
                "conversation_turns": [],
                "conversation_preview_bindings": [],
                "conversation_state_transitions": [],
                "conversation_event_outbox": [],
                "daemon_runtime": None,
                "controller_lease": None,
                "daemon_event_outbox": [],
            }
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def append_event(self, event: EventRecord) -> None:
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True) + "\n")

    def record_daemon_state(
        self,
        record: Mapping[str, object],
        *,
        expected_project_root_hash: str,
    ) -> dict[str, object]:
        validated = validate_daemon_record(record)
        if validated["project_root_hash"] != expected_project_root_hash:
            raise ValueError("project identity mismatch")
        with self._protocol_mutation_lock():
            state = self.load()
            state["daemon_runtime"] = dict(validated)
            self._atomic_save(state)
        return dict(validated)

    def commit_controller_lease(
        self, transition: LeaseTransition
    ) -> dict[str, object]:
        # Reject malformed current state and transitions before creating the lock path.
        initial_state = self.load()
        initial_outbox = initial_state.setdefault("daemon_event_outbox", [])
        initial_event_ids = validate_daemon_event_outbox(initial_outbox)
        validate_lease_transition(initial_state.get("controller_lease"), transition)
        candidate_event_id = transition.audit_event.event_id
        if candidate_event_id in initial_event_ids:
            raise ValueError("duplicate daemon event identity")
        # Lease commits stage daemon events in the durable outbox. The explicit
        # outbox-to-journal flush below holds this same mutation lock; this expiry
        # scan does not make arbitrary append_event calls atomic with lease commits.
        if transition.action == "expire" and (
            candidate_event_id in self._daemon_journal_event_ids()
        ):
            raise ValueError("duplicate daemon event identity")
        with self._protocol_mutation_lock():
            state = self.load()
            outbox = state.setdefault("daemon_event_outbox", [])
            event_ids = validate_daemon_event_outbox(outbox)
            persisted = state.get("controller_lease")
            validate_lease_transition(persisted, transition)
            if candidate_event_id in event_ids:
                raise ValueError("duplicate daemon event identity")
            if transition.action == "expire" and (
                candidate_event_id in self._daemon_journal_event_ids()
            ):
                raise ValueError("duplicate daemon event identity")
            assert transition.current is not None
            summary = transition.current.summary()
            state["controller_lease"] = summary
            outbox.append(transition.audit_event.summary())
            self._atomic_save(state)
        return dict(summary)

    def _daemon_journal_event_ids(self) -> set[str]:
        return _strict_event_journal_ids(
            self.events_path,
            malformed_error="daemon event journal is malformed",
            duplicate_error="duplicate daemon event identity",
        )

    def _strict_protocol_journal_event_ids(self) -> set[str]:
        return _strict_event_journal_ids(
            self.events_path,
            malformed_error="protocol event journal is malformed",
            duplicate_error="duplicate protocol event identity",
        )

    def flush_daemon_event_outbox(self) -> dict[str, int]:
        with self._protocol_mutation_lock():
            state = self.load()
            outbox = state.setdefault("daemon_event_outbox", [])
            validate_daemon_event_outbox(outbox)
            durable_ids = self._daemon_journal_event_ids()
            pending = [
                item
                for item in outbox
                if validate_daemon_event_record(item) not in durable_ids
            ]
            if pending:
                journal_existed = self.events_path.exists()
                with self.events_path.open("a", encoding="utf-8") as handle:
                    for item in pending:
                        handle.write(
                            json.dumps(item, ensure_ascii=False, sort_keys=True)
                            + "\n"
                        )
                    handle.flush()
                    os.fsync(handle.fileno())
                if not journal_existed:
                    directory_fd = os.open(self.events_path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            state["daemon_event_outbox"] = []
            self._atomic_save(state)
            return {
                "flushed": len(pending),
                "already_durable": len(outbox) - len(pending),
            }

    def _atomic_save(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.tmp"
        )
        encoded = (
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        try:
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _conversation_collections(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for key in (
            "conversation_sessions",
            "conversation_turns",
            "conversation_preview_bindings",
        ):
            value = state.setdefault(key, [])
            if type(value) is not list:
                raise TypeError(f"{key} must be a list")
            result[key] = value
        transitions = state.setdefault("conversation_state_transitions", [])
        if type(transitions) is not list:
            raise TypeError("conversation_state_transitions must be a list")
        outbox = state.setdefault("conversation_event_outbox", [])
        if type(outbox) is not list:
            raise TypeError("conversation_event_outbox must be a list")
        return result

    def _apply_conversation_mutation(
        self, state: dict[str, Any], mutation: ConversationMutation
    ) -> dict[str, Any]:
        if not isinstance(mutation, ConversationMutation):
            raise TypeError("mutation must be a ConversationMutation")
        proposed = copy.deepcopy(state)
        self._conversation_collections(proposed)
        allowed = {
            "conversation_sessions",
            "conversation_turns",
            "conversation_preview_bindings",
            "conversation_state_transitions",
            "plans",
            "missions",
        }
        for collection, records in mutation.append_records.items():
            if collection not in allowed:
                raise ValueError(f"conversation mutation collection is not allowed: {collection}")
            if not isinstance(records, tuple) or any(
                not isinstance(record, Mapping) for record in records
            ):
                raise TypeError("conversation mutation records must be tuples of mappings")
            target = proposed.setdefault(collection, [])
            if type(target) is not list:
                raise TypeError(f"{collection} must be a list")
            target.extend(copy.deepcopy(list(records)))
        event_ids = [event.event_id for event in mutation.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("duplicate conversation event identity")
        existing_outbox_ids = {
            item.get("event_id")
            for item in proposed["conversation_event_outbox"]
            if isinstance(item, dict)
        }
        if any(event_id in existing_outbox_ids for event_id in event_ids):
            raise ValueError("duplicate conversation event identity")
        proposed["conversation_event_outbox"].extend(
            asdict(event) for event in mutation.events
        )
        validate_conversation_history(
            self._conversation_collections(proposed),
            proposed["conversation_state_transitions"],
        )
        return proposed

    def _flush_conversation_event_outbox_locked(
        self, state: dict[str, Any] | None = None
    ) -> int:
        state = state if state is not None else self.load()
        self._conversation_collections(state)
        outbox = state["conversation_event_outbox"]
        if not outbox:
            return 0
        existing_ids = self._protocol_event_ids()
        appended = 0
        for item in outbox:
            if not isinstance(item, dict):
                raise TypeError("conversation event outbox items must be objects")
            event_id = item.get("event_id")
            if event_id in existing_ids:
                continue
            event = EventRecord(**item)
            self.append_event(event)
            existing_ids.add(event.event_id)
            appended += 1
        state["conversation_event_outbox"] = []
        self._atomic_save(state)
        return appended

    def flush_conversation_event_outbox(self) -> int:
        with self._protocol_mutation_lock():
            return self._flush_conversation_event_outbox_locked()

    def commit_conversation_mutation(
        self, mutation: ConversationMutation
    ) -> dict[str, object]:
        # Reject malformed/history-invalid batches before creating the lock file.
        self._apply_conversation_mutation(self.load(), mutation)
        event_ids = {event.event_id for event in mutation.events}
        if event_ids & self._protocol_event_ids():
            raise ValueError("duplicate conversation event identity")
        with self._protocol_mutation_lock():
            proposed = self._apply_conversation_mutation(self.load(), mutation)
            if event_ids & self._protocol_event_ids():
                raise ValueError("duplicate conversation event identity")
            self._atomic_save(proposed)
            try:
                flushed = self._flush_conversation_event_outbox_locked(proposed)
            except OSError:
                return {
                    "committed": True,
                    "events_flushed": 0,
                    "outbox_blocked": True,
                }
        return {
            "committed": True,
            "events_flushed": flushed,
            "outbox_blocked": False,
        }

    @staticmethod
    def _unique_protocol_record(
        records: list[dict[str, Any]], key: str, value: str, duplicate_error: str
    ) -> dict[str, Any]:
        matches = [item for item in records if isinstance(item, dict) and item.get(key) == value]
        if len(matches) > 1:
            raise ValueError(duplicate_error)
        if not matches:
            raise KeyError(value)
        return matches[0]

    @staticmethod
    def _validate_protocol_identities(state: dict[str, Any]) -> None:
        sessions = state.setdefault("agent_sessions", [])
        turns = state.setdefault("protocol_turns", [])
        updates = state.setdefault("transport_updates", [])
        permissions = state.setdefault("permission_requests", [])
        transitions = state.setdefault("protocol_state_transitions", [])
        state.setdefault("protocol_event_outbox", [])
        session_ids = [item.get("session_id") for item in sessions if isinstance(item, dict)]
        turn_ids = [item.get("turn_id") for item in turns if isinstance(item, dict)]
        update_ids = [item.get("update_id") for item in updates if isinstance(item, dict)]
        permission_ids = [item.get("permission_id") for item in permissions if isinstance(item, dict)]
        transition_ids = [item.get("transition_id") for item in transitions if isinstance(item, dict)]
        if len(session_ids) != len(set(session_ids)):
            raise ValueError("duplicate agent session identity")
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("duplicate protocol turn identity")
        if len(update_ids) != len(set(update_ids)):
            raise ValueError("duplicate transport update identity")
        if len(permission_ids) != len(set(permission_ids)):
            raise ValueError("duplicate permission request identity")
        if len(transition_ids) != len(set(transition_ids)):
            raise ValueError("duplicate protocol transition identity")

    @staticmethod
    def _validate_protocol_lineage(state: dict[str, Any]) -> None:
        sessions = {
            item.get("session_id"): item
            for item in state.get("agent_sessions", [])
            if isinstance(item, dict) and isinstance(item.get("session_id"), str)
        }
        turns = {
            item.get("turn_id"): item
            for item in state.get("protocol_turns", [])
            if isinstance(item, dict) and isinstance(item.get("turn_id"), str)
        }
        for turn in state.get("protocol_turns", []):
            if isinstance(turn, dict) and turn.get("session_id") not in sessions:
                raise ValueError("protocol turn session reference missing")
        for collection, label in (
            ("transport_updates", "transport update"),
            ("permission_requests", "permission request"),
        ):
            for item in state.get(collection, []):
                if not isinstance(item, dict):
                    continue
                if item.get("session_id") not in sessions:
                    raise ValueError(f"{label} session reference missing")
                turn = turns.get(item.get("turn_id"))
                if turn is None:
                    raise ValueError(f"{label} turn reference missing")
                if item.get("session_id") != turn.get("session_id"):
                    raise ValueError(f"{label} session mismatch")

    @contextmanager
    def _protocol_mutation_lock(self):
        lock_path = self.state_path.parent / "protocol-mutation.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _protocol_event_ids(self) -> set[str]:
        event_ids: set[str] = set()
        if not self.events_path.exists():
            return event_ids
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and isinstance(event.get("event_id"), str):
                event_ids.add(event["event_id"])
        return event_ids

    def _flush_protocol_event_outbox_locked(self, state: dict[str, Any] | None = None) -> int:
        state = state if state is not None else self.load()
        outbox = state.setdefault("protocol_event_outbox", [])
        if not outbox:
            return 0
        existing_ids = self._protocol_event_ids()
        appended = 0
        for item in outbox:
            event_id = item.get("event_id") if isinstance(item, dict) else None
            if event_id in existing_ids:
                continue
            event = EventRecord(**item)
            self.append_event(event)
            existing_ids.add(event.event_id)
            appended += 1
        state["protocol_event_outbox"] = []
        self.save(state)
        return appended

    def flush_protocol_event_outbox(self) -> int:
        with self._protocol_mutation_lock():
            return self._flush_protocol_event_outbox_locked()

    def _save_protocol_record(
        self,
        state: dict[str, Any],
        collection: str,
        record: dict[str, Any],
        event: EventRecord,
    ) -> dict[str, Any]:
        state.setdefault(collection, []).append(record)
        state.setdefault("protocol_event_outbox", []).append(asdict(event))
        self.save(state)
        try:
            self.append_event(event)
        except OSError:
            return record
        state["protocol_event_outbox"] = [
            item for item in state["protocol_event_outbox"]
            if item.get("event_id") != event.event_id
        ]
        try:
            self.save(state)
        except OSError:
            pass
        return record

    def record_agent_session(
        self,
        agent_id: str,
        provider: str,
        transport: str,
        native_session_id: str | None,
        workspace: str,
        capabilities: TransportCapabilities,
    ) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            self._validate_protocol_identities(state)
            record = build_agent_session(
                agent_id, provider, transport, native_session_id, workspace, capabilities
            )
            if any(item.get("session_id") == record["session_id"] for item in state["agent_sessions"]):
                raise ValueError("duplicate agent session identity")
            self._flush_protocol_event_outbox_locked(state)
            event = EventRecord.create("agent_session_recorded", {
                "session_id": record["session_id"],
                "agent_id": record["agent_id"],
                "transport": record["transport"],
            })
            return self._save_protocol_record(state, "agent_sessions", record, event)

    def record_protocol_turn(
        self, session_id: str, message_id: str, kind: str = "prompt"
    ) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            self._validate_protocol_identities(state)
            self._unique_protocol_record(
                state.setdefault("agent_sessions", []), "session_id", session_id,
                "duplicate agent session identity",
            )
            record = build_turn(session_id, message_id, kind)
            if any(item.get("turn_id") == record["turn_id"] for item in state["protocol_turns"]):
                raise ValueError("duplicate protocol turn identity")
            self._flush_protocol_event_outbox_locked(state)
            event = EventRecord.create("protocol_turn_recorded", {
                "turn_id": record["turn_id"],
                "session_id": record["session_id"],
                "message_id": record["message_id"],
            })
            return self._save_protocol_record(state, "protocol_turns", record, event)

    @staticmethod
    def _protocol_transition_entity(
        state: dict[str, Any], entity_type: str, entity_id: str
    ) -> dict[str, Any]:
        entity_sources = {
            "session": ("agent_sessions", "session_id", "duplicate agent session identity", "state"),
            "turn": ("protocol_turns", "turn_id", "duplicate protocol turn identity", "state"),
            "permission": (
                "permission_requests", "permission_id",
                "duplicate permission request identity", "status",
            ),
        }
        collection, key, duplicate_error, _ = entity_sources[entity_type]
        return StateStore._unique_protocol_record(
            state.setdefault(collection, []), key, entity_id, duplicate_error
        )

    @staticmethod
    def _derived_protocol_state(
        state: dict[str, Any], entity_type: str, entity_id: str, base: dict[str, Any]
    ) -> str:
        state_field = "status" if entity_type == "permission" else "state"
        current = base.get(state_field)
        if type(current) is not str:
            raise ValueError("invalid protocol base state")
        for item in state.setdefault("protocol_state_transitions", []):
            if not isinstance(item, dict) or item.get("entity_type") != entity_type or item.get("entity_id") != entity_id:
                continue
            if item.get("from_state") != current:
                raise ValueError("stale protocol transition from_state")
            validate_transition_edge(entity_type, item.get("from_state"), item.get("to_state"))
            current = item["to_state"]
        return current

    @staticmethod
    def _validate_protocol_transition_history(
        state: dict[str, Any],
    ) -> dict[tuple[str, str], str]:
        transitions = state.setdefault("protocol_state_transitions", [])
        if type(transitions) is not list:
            raise TypeError("protocol_state_transitions must be a list")
        current_states: dict[tuple[str, str], str] = {}
        entity_specs = {
            "session": ("agent_sessions", "session_id", "state", AGENT_SESSION_STATES, "duplicate agent session identity"),
            "turn": ("protocol_turns", "turn_id", "state", TURN_STATES, "duplicate protocol turn identity"),
            "permission": ("permission_requests", "permission_id", "status", PERMISSION_STATES, "duplicate permission request identity"),
        }
        entity_maps: dict[str, dict[str, dict[str, Any]]] = {}
        for entity_type, (collection, identity_field, state_field, vocabulary, duplicate_error) in entity_specs.items():
            records = state.setdefault(collection, [])
            if type(records) is not list:
                raise TypeError(f"{collection} must be a list")
            indexed: dict[str, dict[str, Any]] = {}
            for record in records:
                if type(record) is not dict:
                    raise TypeError(f"{collection} items must be objects")
                identity = record.get(identity_field)
                if type(identity) is not str or not identity:
                    raise ValueError(f"invalid {identity_field}")
                if identity in indexed:
                    raise ValueError(duplicate_error)
                base_state = record.get(state_field)
                if type(base_state) is not str or base_state not in vocabulary:
                    raise ValueError("invalid protocol base state")
                indexed[identity] = record
            entity_maps[entity_type] = indexed
        for item in transitions:
            validate_protocol_transition_record(item)
            entity_type = item["entity_type"]
            entity_id = item["entity_id"]
            key = (entity_type, entity_id)
            if key not in current_states:
                entity = entity_maps[entity_type].get(entity_id)
                if entity is None:
                    raise KeyError(entity_id)
                state_field = entity_specs[entity_type][2]
                current_states[key] = entity[state_field]
            if item["from_state"] != current_states[key]:
                raise ValueError("stale protocol transition from_state")
            current_states[key] = item["to_state"]
        return current_states

    def record_protocol_transition(
        self,
        entity_type: str,
        entity_id: str,
        from_state: str,
        to_state: str,
        reason: str | None,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            self._validate_protocol_identities(state)
            current_states = self._validate_protocol_transition_history(state)
            record = build_protocol_transition(
                entity_type, entity_id, from_state, to_state, reason, details
            )
            entity = self._protocol_transition_entity(state, entity_type, entity_id)
            state_field = "status" if entity_type == "permission" else "state"
            current_state = current_states.get((entity_type, entity_id), entity.get(state_field))
            if from_state != current_state:
                raise ValueError(
                    f"stale protocol transition from_state: expected {current_state}"
                )
            transitions = state.setdefault("protocol_state_transitions", [])
            if any(item.get("transition_id") == record["transition_id"] for item in transitions):
                raise ValueError("duplicate protocol transition identity")
            self._flush_protocol_event_outbox_locked(state)
            event = EventRecord.create("protocol_state_transition_recorded", {
                key: record[key]
                for key in (
                    "transition_id", "entity_type", "entity_id", "from_state",
                    "to_state", "reason",
                )
            })
            return self._save_protocol_record(
                state, "protocol_state_transitions", record, event
            )

    def _validated_protocol_turn(
        self, state: dict[str, Any], session_id: str, turn_id: str
    ) -> dict[str, Any]:
        self._validate_protocol_identities(state)
        self._unique_protocol_record(
            state.setdefault("agent_sessions", []), "session_id", session_id,
            "duplicate agent session identity",
        )
        turn = self._unique_protocol_record(
            state.setdefault("protocol_turns", []), "turn_id", turn_id,
            "duplicate protocol turn identity",
        )
        if turn.get("session_id") != session_id:
            raise ValueError("protocol turn session mismatch")
        return turn

    def record_transport_update(
        self, session_id: str, turn_id: str, sequence: int, kind: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            self._validated_protocol_turn(state, session_id, turn_id)
            updates = state.setdefault("transport_updates", [])
            if any(
                isinstance(item, dict)
                and item.get("turn_id") == turn_id
                and item.get("sequence") == sequence
                for item in updates
            ):
                raise ValueError("duplicate transport update sequence")
            record = build_transport_update(session_id, turn_id, sequence, kind, payload)
            if any(item.get("update_id") == record["update_id"] for item in updates):
                raise ValueError("duplicate transport update identity")
            self._flush_protocol_event_outbox_locked(state)
            event = EventRecord.create("transport_update_recorded", {
                "update_id": record["update_id"],
                "session_id": record["session_id"],
                "turn_id": record["turn_id"],
                "sequence": record["sequence"],
                "kind": record["kind"],
            })
            return self._save_protocol_record(state, "transport_updates", record, event)

    def record_permission_request(
        self, session_id: str, turn_id: str, tool_name: str, target: str, risk: str
    ) -> dict[str, Any]:
        with self._protocol_mutation_lock():
            state = self.load()
            self._validated_protocol_turn(state, session_id, turn_id)
            record = build_permission_request(session_id, turn_id, tool_name, target, risk)
            permissions = state.setdefault("permission_requests", [])
            if any(item.get("permission_id") == record["permission_id"] for item in permissions):
                raise ValueError("duplicate permission request identity")
            self._flush_protocol_event_outbox_locked(state)
            event = EventRecord.create("permission_request_recorded", {
                "permission_id": record["permission_id"],
                "session_id": record["session_id"],
                "turn_id": record["turn_id"],
                "tool_name": record["tool_name"],
                "risk": record["risk"],
            })
            return self._save_protocol_record(state, "permission_requests", record, event)

    def record_acp_permission_pending(
        self,
        session_id: str,
        turn_id: str,
        sequence: int,
        *,
        tool_name: str,
        target: str,
        risk: str,
        tool_call_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Atomically reserve one bounded ACP permission request and its ledger facts."""
        with self._protocol_mutation_lock():
            state = self.load()
            self._validated_protocol_turn(state, session_id, turn_id)
            current_states = self._validate_protocol_transition_history(state)
            turn = self._unique_protocol_record(
                state.setdefault("protocol_turns", []), "turn_id", turn_id,
                "duplicate protocol turn identity",
            )
            current = current_states.get(("turn", turn_id), turn["state"])
            if current not in {"submitted", "streaming"}:
                raise ValueError("ACP permission requires a submitted or streaming turn")

            permission = build_permission_request(
                session_id, turn_id, tool_name, target, risk
            )
            payload = {
                "permission_id": permission["permission_id"],
                "tool_call_id": tool_call_id,
                "risk": risk,
            }
            updates = state.setdefault("transport_updates", [])
            turn_updates = [
                item for item in updates
                if isinstance(item, dict) and item.get("turn_id") == turn_id
            ]
            if any(item.get("sequence") == sequence for item in turn_updates):
                raise ValueError("duplicate transport update sequence")
            if sequence != len(turn_updates):
                raise ValueError("ACP transport update sequence must be contiguous")
            existing_bytes = sum(
                len(json.dumps(item["payload"], ensure_ascii=False, sort_keys=True).encode("utf-8"))
                for item in turn_updates
            )
            payload_bytes = len(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            )
            total_bytes = existing_bytes + payload_bytes
            total_updates = len(turn_updates) + 1
            if total_bytes + MAX_ACP_TERMINAL_UPDATE_BYTES > MAX_ACP_TURN_PAYLOAD_BYTES:
                raise ValueError(
                    f"ACP turn payload exceeds {MAX_ACP_TURN_PAYLOAD_BYTES} bytes"
                )
            if total_updates + 1 > MAX_ACP_UPDATES_PER_TURN:
                raise ValueError(
                    f"ACP turn updates exceed {MAX_ACP_UPDATES_PER_TURN}"
                )

            update = build_transport_update(
                session_id, turn_id, sequence, "permission_request", payload
            )
            transition = build_protocol_transition(
                "turn", turn_id, current, "waiting_permission", "permission_requested", {}
            )
            permissions = state.setdefault("permission_requests", [])
            if any(item.get("permission_id") == permission["permission_id"] for item in permissions):
                raise ValueError("duplicate permission request identity")
            events = [
                EventRecord.create("permission_request_recorded", {
                    "permission_id": permission["permission_id"], "session_id": session_id,
                    "turn_id": turn_id, "tool_name": tool_name, "risk": risk,
                }),
                EventRecord.create("transport_update_recorded", {
                    "update_id": update["update_id"], "session_id": session_id,
                    "turn_id": turn_id, "sequence": sequence, "kind": "permission_request",
                }),
                EventRecord.create("protocol_state_transition_recorded", {
                    key: transition[key] for key in (
                        "transition_id", "entity_type", "entity_id", "from_state",
                        "to_state", "reason",
                    )
                }),
            ]
            # Flush only after every prospective record and bound has validated.
            self._flush_protocol_event_outbox_locked(state)
            permissions.append(permission)
            updates.append(update)
            state.setdefault("protocol_state_transitions", []).append(transition)
            state.setdefault("protocol_event_outbox", []).extend(asdict(event) for event in events)
            self.save(state)
            for event in events:
                try:
                    self.append_event(event)
                except OSError:
                    return {"permission": permission, "update": update, "transition": transition}
            event_ids = {event.event_id for event in events}
            state["protocol_event_outbox"] = [
                item for item in state["protocol_event_outbox"]
                if item.get("event_id") not in event_ids
            ]
            try:
                self.save(state)
            except OSError:
                pass
            return {"permission": permission, "update": update, "transition": transition}

    def agent_session_by_id(self, session_id: str) -> dict[str, Any]:
        state = self.load()
        self._validate_protocol_identities(state)
        return self._unique_protocol_record(
            state.setdefault("agent_sessions", []), "session_id", session_id,
            "duplicate agent session identity",
        )

    def protocol_turn_by_id(self, turn_id: str) -> dict[str, Any]:
        state = self.load()
        self._validate_protocol_identities(state)
        return self._unique_protocol_record(
            state.setdefault("protocol_turns", []), "turn_id", turn_id,
            "duplicate protocol turn identity",
        )

    def validated_protocol_state(self) -> dict[str, Any]:
        """Load and globally validate all protocol identities and transition lineage."""
        state = self.load()
        self._validate_protocol_identities(state)
        self._validate_protocol_lineage(state)
        self._validate_protocol_transition_history(state)
        return state

    def list_agent_sessions(self) -> list[dict[str, Any]]:
        return list(self.load().setdefault("agent_sessions", []))

    def list_protocol_turns(self) -> list[dict[str, Any]]:
        return list(self.load().setdefault("protocol_turns", []))

    def list_transport_updates(self) -> list[dict[str, Any]]:
        return list(self.load().setdefault("transport_updates", []))

    def list_permission_requests(self) -> list[dict[str, Any]]:
        return list(self.load().setdefault("permission_requests", []))

    def create_mission(
        self,
        *,
        user_message: str,
        can_start: bool,
        blockers: list[str],
        provider: str,
        model: str,
        leader_backend: dict[str, Any],
        plan_id: str,
        plan_hash: str,
        selected_agents: list[dict[str, Any]],
        startup_actions: list[dict[str, Any]],
        step_count: int,
        timeout_seconds: int,
        retry_limit: int = 0,
    ) -> dict[str, Any]:
        state = self.load()
        if not all(
            isinstance(value, str) and value
            for value in (user_message, provider, model, plan_id, plan_hash)
        ):
            raise ValueError("mission identity fields must be non-empty strings")
        if not isinstance(can_start, bool):
            raise ValueError("can_start must be a boolean")
        compact_blockers, invalid_blockers = compact_mission_blockers(blockers)
        if invalid_blockers:
            raise ValueError("blockers must be a list of strings")
        if can_start and compact_blockers:
            raise ValueError("can_start requires empty blockers")
        if not isinstance(step_count, int) or isinstance(step_count, bool) or step_count < 1:
            raise ValueError("step_count must be a positive integer")
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 1
        ):
            raise ValueError("timeout_seconds must be a positive integer")
        if type(retry_limit) is not int or retry_limit < 0:
            raise ValueError("retry_limit must be a non-negative integer")
        expected_backend = leader_backend_identity(provider, model)
        if leader_backend != expected_backend:
            raise ValueError("leader_backend must match provider and model")
        compact_agents, invalid_agents = compact_mission_worker_entries(
            selected_agents, kind="selected_agents"
        )
        compact_actions, invalid_actions = compact_mission_worker_entries(
            startup_actions, kind="startup_actions"
        )
        if invalid_agents or invalid_actions:
            raise ValueError("mission worker summaries must use compact domain fields")
        selected_ids = [item["agent_id"] for item in compact_agents]
        action_ids = [item["agent_id"] for item in compact_actions]
        if can_start and (
            len(compact_agents) < 2
            or len(compact_actions) < 2
            or selected_ids != action_ids
        ):
            raise ValueError("startable mission requires matching worker and startup summaries")
        now = utc_now()
        record = {
            "mission_id": new_id("mis"),
            "schema_version": MISSION_SCHEMA_VERSION,
            "user_message": user_message,
            "status": MISSION_STATUSES[0],
            "stop_reason": None,
            "can_start": can_start,
            "blockers": compact_blockers,
            "provider": provider,
            "model": model,
            "leader_backend": expected_backend,
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "selected_agents": compact_agents,
            "startup_actions": compact_actions,
            "step_count": step_count,
            "timeout_seconds": timeout_seconds,
            "retry_limit": retry_limit,
            "workflow_run_id": None,
            "current_step": 0,
            "confirmed_at": None,
            "execution_snapshot": None,
            "snapshot_hash": None,
            "execution_authority_hash": None,
            "completed_at": None,
            "created_at": now,
            "updated_at": now,
        }
        state.setdefault("missions", []).append(record)
        self.save(state)
        return record

    def build_mission_record(
        self,
        *,
        user_message: str,
        can_start: bool,
        blockers: list[str],
        provider: str,
        model: str,
        leader_backend: dict[str, Any],
        plan_id: str,
        plan_hash: str,
        selected_agents: list[dict[str, Any]],
        startup_actions: list[dict[str, Any]],
        step_count: int,
        timeout_seconds: int,
        retry_limit: int = 0,
    ) -> dict[str, Any]:
        if not all(
            isinstance(value, str) and value
            for value in (user_message, provider, model, plan_id, plan_hash)
        ):
            raise ValueError("mission identity fields must be non-empty strings")
        if not isinstance(can_start, bool):
            raise ValueError("can_start must be a boolean")
        compact_blockers, invalid_blockers = compact_mission_blockers(blockers)
        if invalid_blockers:
            raise ValueError("blockers must be a list of strings")
        if can_start and compact_blockers:
            raise ValueError("can_start requires empty blockers")
        if not isinstance(step_count, int) or isinstance(step_count, bool) or step_count < 1:
            raise ValueError("step_count must be a positive integer")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1:
            raise ValueError("timeout_seconds must be a positive integer")
        if type(retry_limit) is not int or retry_limit < 0:
            raise ValueError("retry_limit must be a non-negative integer")
        expected_backend = leader_backend_identity(provider, model)
        if leader_backend != expected_backend:
            raise ValueError("leader_backend must match provider and model")
        compact_agents, invalid_agents = compact_mission_worker_entries(
            selected_agents, kind="selected_agents"
        )
        compact_actions, invalid_actions = compact_mission_worker_entries(
            startup_actions, kind="startup_actions"
        )
        if invalid_agents or invalid_actions:
            raise ValueError("mission worker summaries must use compact domain fields")
        selected_ids = [item["agent_id"] for item in compact_agents]
        action_ids = [item["agent_id"] for item in compact_actions]
        if can_start and (
            len(compact_agents) < 2
            or len(compact_actions) < 2
            or selected_ids != action_ids
        ):
            raise ValueError("startable mission requires matching worker and startup summaries")
        now = utc_now()
        return {
            "mission_id": new_id("mis"),
            "schema_version": MISSION_SCHEMA_VERSION,
            "user_message": user_message,
            "status": MISSION_STATUSES[0],
            "stop_reason": None,
            "can_start": can_start,
            "blockers": compact_blockers,
            "provider": provider,
            "model": model,
            "leader_backend": expected_backend,
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "selected_agents": compact_agents,
            "startup_actions": compact_actions,
            "step_count": step_count,
            "timeout_seconds": timeout_seconds,
            "retry_limit": retry_limit,
            "workflow_run_id": None,
            "current_step": 0,
            "confirmed_at": None,
            "execution_snapshot": None,
            "snapshot_hash": None,
            "execution_authority_hash": None,
            "completed_at": None,
            "created_at": now,
            "updated_at": now,
        }

    def mission_by_id(self, mission_id: str) -> dict[str, Any]:
        for item in self.load().get("missions", []):
            if isinstance(item, dict) and item.get("mission_id") == mission_id:
                return item
        raise KeyError(mission_id)

    def list_missions(self) -> list[dict[str, Any]]:
        return list(self.load().get("missions", []))

    @staticmethod
    def _unique_mission_record(
        state: dict[str, Any], mission_id: str
    ) -> dict[str, Any]:
        matches = [
            item
            for item in state.setdefault("missions", [])
            if isinstance(item, dict) and item.get("mission_id") == mission_id
        ]
        if not matches:
            raise KeyError(mission_id)
        if len(matches) != 1:
            raise ValueError("duplicate mission identity")
        return matches[0]

    @staticmethod
    def _unique_plan_record(state: dict[str, Any], plan_id: str) -> dict[str, Any]:
        matches = [
            item
            for item in state.setdefault("plans", [])
            if isinstance(item, dict) and item.get("plan_id") == plan_id
        ]
        if not matches:
            raise KeyError(plan_id)
        if len(matches) != 1:
            raise ValueError("duplicate plan identity")
        return matches[0]

    def freeze_mission_execution(
        self,
        mission_id: str,
        *,
        confirmed_at: str,
    ) -> dict[str, Any]:
        if not is_canonical_mission_id(mission_id):
            raise ValueError("mission identity invalid")
        if type(confirmed_at) is not str or not confirmed_at:
            raise ValueError("confirmation timestamp invalid")
        try:
            parsed = datetime.fromisoformat(confirmed_at)
        except ValueError:
            raise ValueError("confirmation timestamp invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("confirmation timestamp invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            mission = self._unique_mission_record(state, mission_id)
            plan_id = str(mission.get("plan_id") or "")
            plan = self._unique_plan_record(state, plan_id)
            created_at = mission.get("created_at")
            if type(created_at) is not str:
                raise ValueError("confirmation timestamp invalid")
            try:
                created_time = datetime.fromisoformat(created_at)
            except ValueError:
                raise ValueError("confirmation timestamp invalid") from None
            if (
                created_time.tzinfo is None
                or created_time.utcoffset() is None
                or parsed < created_time
            ):
                raise ValueError("confirmation timestamp invalid")
            if (
                mission.get("status") != "pending_confirmation"
                or mission.get("can_start") is not True
                or mission.get("blockers") != []
                or mission.get("confirmed_at") is not None
                or mission.get("execution_snapshot") is not None
                or mission.get("snapshot_hash") is not None
            ):
                raise ValueError("mission is not confirmable")
            try:
                config = load_config(self.root)
                snapshot = build_execution_snapshot_authority(
                    config,
                    mission,
                    plan,
                    execution_policy_snapshot(config),
                    memory_provenance=collect_execution_memory_provenance(
                        Path(config.root)
                    ),
                )
            except MissionStateError:
                raise
            except (OSError, TypeError, ValueError):
                raise MissionStateError("execution authority drift") from None
            authority_hash = mission.get("execution_authority_hash")
            if (
                type(authority_hash) is not str
                or re.fullmatch(r"sha256:[0-9a-f]{64}", authority_hash) is None
                or snapshot["execution_hash"] != authority_hash
            ):
                raise MissionStateError("execution authority drift")
            event = EventRecord.create(
                "mission_execution_frozen",
                {
                    "mission_id": mission_id,
                    "snapshot_hash": snapshot["execution_hash"],
                },
            )
            mission.update(
                {
                    "status": "preparing",
                    "can_start": False,
                    "confirmed_at": confirmed_at,
                    "updated_at": confirmed_at,
                    "execution_snapshot": copy.deepcopy(snapshot),
                    "snapshot_hash": snapshot["execution_hash"],
                }
            )
            state.setdefault("protocol_event_outbox", []).append(asdict(event))
            self._atomic_save(state)
            return copy.deepcopy(mission)

    def prepare_mission_attempt(
        self,
        *,
        mission_id: str,
        step_id: str,
        agent_id: str,
        configured_transport: str,
    ) -> dict[str, Any]:
        if type(step_id) is not str or not re.fullmatch(r"step_[1-9][0-9]*", step_id):
            raise ValueError("mission step identity invalid")
        if type(agent_id) is not str or not agent_id:
            raise ValueError("mission step agent invalid")
        if configured_transport not in {"acp", "tmux"}:
            raise ValueError("mission step transport invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            mission = self._unique_mission_record(state, mission_id)
            status = mission.get("status")
            if status in {"completed", "stopped", "interrupted"}:
                raise ValueError("terminal mission step")
            if (
                status not in {"preparing", "running"}
                or not mission.get("confirmed_at")
                or mission.get("execution_snapshot") is None
            ):
                raise ValueError("mission execution is not confirmed")
            confirmed_at = mission.get("confirmed_at")
            created_at = mission.get("created_at")
            if type(confirmed_at) is not str or type(created_at) is not str:
                raise ValueError("mission confirmation state invalid")
            try:
                confirmed_time = datetime.fromisoformat(confirmed_at)
                created_time = datetime.fromisoformat(created_at)
            except ValueError:
                raise ValueError("mission confirmation state invalid") from None
            if (
                confirmed_time.tzinfo is None
                or confirmed_time.utcoffset() is None
                or created_time.tzinfo is None
                or created_time.utcoffset() is None
                or confirmed_time < created_time
            ):
                raise ValueError("mission confirmation state invalid")
            try:
                persisted_snapshot = validate_execution_snapshot(
                    mission["execution_snapshot"]
                )
            except ValueError:
                raise ValueError("frozen execution snapshot invalid") from None
            if mission.get("snapshot_hash") != persisted_snapshot["execution_hash"]:
                raise ValueError("frozen execution drift")
            if (
                mission.get("execution_authority_hash")
                != persisted_snapshot["execution_hash"]
            ):
                raise ValueError("frozen execution drift")
            if (
                persisted_snapshot["mission"].get("plan_hash")
                != mission.get("plan_hash")
            ):
                raise MissionStateError("plan hash drift")
            plan = self._unique_plan_record(state, str(mission.get("plan_id") or ""))
            try:
                config = load_config(self.root)
                snapshot = build_execution_snapshot_authority(
                    config,
                    mission,
                    plan,
                    execution_policy_snapshot(config),
                    memory_provenance=collect_execution_memory_provenance(
                        Path(config.root)
                    ),
                )
            except MissionStateError:
                raise
            except (OSError, TypeError, ValueError):
                raise ValueError("frozen execution drift") from None
            if snapshot != persisted_snapshot:
                raise ValueError("frozen execution drift")
            steps = snapshot["mission"].get("steps")
            if type(steps) is not list:
                raise ValueError("frozen execution snapshot invalid")
            matches = [item for item in steps if type(item) is dict and item.get("step_id") == step_id]
            if len(matches) != 1:
                raise ValueError("unknown mission step")
            step = matches[0]
            position = step.get("position")
            current_step = mission.get("current_step")
            if type(position) is not int or type(current_step) is not int:
                raise ValueError("frozen execution snapshot invalid")
            if position <= current_step:
                raise ValueError("terminal mission step")
            if position != current_step + 1:
                raise ValueError("mission step lineage drift")
            if step.get("agent_id") != agent_id:
                raise ValueError("mission step agent drift")
            workers = snapshot["workers"]
            worker = next(
                (
                    item
                    for item in workers
                    if type(item) is dict and item.get("agent_id") == agent_id
                ),
                None,
            )
            if worker is None:
                raise ValueError("mission step agent drift")
            if worker.get("configured_transport") != configured_transport:
                raise ValueError("mission step transport drift")
            attempts = state.setdefault("mission_attempts", [])
            if type(attempts) is not list:
                raise ValueError("mission attempt state invalid")
            validated_attempts = [
                _validate_mission_attempt_record(item) for item in attempts
            ]
            attempt_ids = [item["attempt_id"] for item in validated_attempts]
            if len(attempt_ids) != len(set(attempt_ids)):
                raise ValueError("duplicate mission attempt identity")
            dispatch_keys = [item["dispatch_key"] for item in validated_attempts]
            if len(dispatch_keys) != len(set(dispatch_keys)):
                raise ValueError("duplicate mission dispatch key")
            matching = [
                item
                for item in validated_attempts
                if item["mission_id"] == mission_id and item["step_id"] == step_id
            ]
            for ordinal, prior in enumerate(matching, start=1):
                expected_key = derive_attempt_dispatch_key(
                    mission_id,
                    step_id,
                    agent_id,
                    configured_transport,
                    persisted_snapshot["execution_hash"],
                    attempt_ordinal=ordinal,
                )
                if (
                    prior["snapshot_hash"] != persisted_snapshot["execution_hash"]
                    or prior["agent_id"] != step["agent_id"]
                    or prior["configured_transport"]
                    != worker["configured_transport"]
                    or prior["dispatch_key"] != expected_key
                ):
                    raise ValueError("mission attempt lineage drift")
            if any(item["state"] in _MISSION_ATTEMPT_ACTIVE_STATES for item in matching):
                raise ValueError("active attempt already exists")
            if any(
                item["state"] not in _MISSION_ATTEMPT_RETRYABLE_STATES
                for item in matching
            ):
                raise ValueError("terminal mission attempt")
            retry_limit = persisted_snapshot["limits"]["retry_limit"]
            if len(matching) >= 1 + retry_limit:
                raise ValueError("mission retry budget exhausted")
            attempt_ordinal = len(matching) + 1
            dispatch_key = derive_attempt_dispatch_key(
                mission_id,
                step_id,
                agent_id,
                configured_transport,
                persisted_snapshot["execution_hash"],
                attempt_ordinal=attempt_ordinal,
            )
            if any(item["dispatch_key"] == dispatch_key for item in validated_attempts):
                raise ValueError("duplicate mission dispatch key")
            now = utc_now()
            attempt = {
                "attempt_id": new_id("mat"),
                "mission_id": mission_id,
                "step_id": step_id,
                "agent_id": agent_id,
                "configured_transport": configured_transport,
                "dispatch_key": dispatch_key,
                "snapshot_hash": snapshot["execution_hash"],
                "state": "prepared",
                "created_at": now,
                "updated_at": now,
                "receipt_summary": None,
                "blocker": None,
                "terminal_reason": None,
            }
            attempt = _validate_mission_attempt_record(attempt)
            if attempt["attempt_id"] in set(attempt_ids):
                raise ValueError("duplicate mission attempt identity")
            attempt_time = datetime.fromisoformat(attempt["created_at"])
            if attempt_time < confirmed_time:
                raise ValueError("mission attempt state invalid")
            event = EventRecord(
                event_id=new_id("evt"),
                event_type="mission_attempt_prepared",
                created_at=attempt["created_at"],
                payload={
                    "attempt_id": attempt["attempt_id"],
                    "mission_id": mission_id,
                    "step_id": step_id,
                    "dispatch_key": attempt["dispatch_key"],
                },
            )
            event_summary = asdict(event)
            try:
                candidate_event_id = validate_daemon_event_record(event_summary)
            except LeaseError:
                raise ValueError("protocol event record is invalid") from None
            outbox = state.setdefault("protocol_event_outbox", [])
            outbox_ids = _validated_protocol_event_outbox_ids(outbox)
            journal_ids = self._strict_protocol_journal_event_ids()
            if candidate_event_id in outbox_ids or candidate_event_id in journal_ids:
                raise ValueError("duplicate protocol event identity")
            attempts.append(attempt)
            outbox.append(event_summary)
            self._atomic_save(state)
            return copy.deepcopy(attempt)

    def mission_attempt_by_id(self, attempt_id: str) -> dict[str, Any]:
        if type(attempt_id) is not str or re.fullmatch(r"mat_[0-9a-f]{12}", attempt_id) is None:
            raise ValueError("mission attempt identity invalid")
        attempts = self.load().get("mission_attempts", [])
        if type(attempts) is not list:
            raise ValueError("mission attempt state invalid")
        validated = [_validate_mission_attempt_record(item) for item in attempts]
        matches = [item for item in validated if item["attempt_id"] == attempt_id]
        if len(matches) != 1:
            if not matches:
                raise KeyError(attempt_id)
            raise ValueError("duplicate mission attempt identity")
        return matches[0]

    def _transition_mission_attempt_receipt(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        observed_dispatch_key: str | None,
        receipt_summary: str,
        target_state: str,
        reason: str | None,
    ) -> dict[str, Any]:
        if (
            type(attempt_id) is not str
            or re.fullmatch(r"mat_[0-9a-f]{12}", attempt_id) is None
            or type(dispatch_key) is not str
            or re.fullmatch(r"dsp_[0-9a-f]{32}", dispatch_key) is None
            or type(receipt_summary) is not str
            or not receipt_summary
            or (
                observed_dispatch_key is not None
                and (
                    type(observed_dispatch_key) is not str
                    or re.fullmatch(r"dsp_[0-9a-f]{32}", observed_dispatch_key)
                    is None
                )
            )
        ):
            raise ValueError("mission attempt receipt is invalid")
        if target_state not in {"submitted", "ambiguous"}:
            raise ValueError("mission attempt receipt transition is invalid")
        if target_state == "ambiguous" and reason != "receipt_persistence_unknown":
            raise ValueError("mission attempt ambiguity reason is invalid")
        with self._protocol_mutation_lock():
            state = self.load()
            attempts = state.get("mission_attempts", [])
            if type(attempts) is not list:
                raise ValueError("mission attempt state invalid")
            validated = [_validate_mission_attempt_record(item) for item in attempts]
            matches = [item for item in validated if item["attempt_id"] == attempt_id]
            if len(matches) != 1:
                if not matches:
                    raise KeyError(attempt_id)
                raise ValueError("duplicate mission attempt identity")
            persisted = matches[0]
            if persisted["dispatch_key"] != dispatch_key:
                raise ValueError("mission attempt receipt lineage drift")
            if persisted["state"] == target_state:
                expected_reason = reason if target_state == "ambiguous" else None
                if (
                    persisted["receipt_summary"] == receipt_summary
                    and persisted["blocker"] == expected_reason
                    and persisted["terminal_reason"] == expected_reason
                ):
                    return persisted
                raise ValueError("mission attempt receipt conflict")
            allowed_states = {"prepared"} if target_state == "submitted" else {"prepared", "submitted"}
            if persisted["state"] not in allowed_states:
                raise ValueError("mission attempt receipt transition is invalid")
            if (
                target_state == "ambiguous"
                and persisted["state"] == "submitted"
                and persisted["receipt_summary"] != receipt_summary
            ):
                raise ValueError("mission attempt receipt conflict")
            now = utc_now()
            candidate = {
                **persisted,
                "state": target_state,
                "updated_at": now,
                "receipt_summary": receipt_summary,
                "blocker": reason if target_state == "ambiguous" else None,
                "terminal_reason": reason if target_state == "ambiguous" else None,
            }
            candidate = _validate_mission_attempt_record(candidate)
            event_payload = {
                "attempt_id": attempt_id,
                "mission_id": candidate["mission_id"],
                "step_id": candidate["step_id"],
                "dispatch_key": dispatch_key,
                "reason": reason,
            }
            if target_state == "ambiguous":
                event_payload.update(
                    {
                        "expected_dispatch_key": dispatch_key,
                        "observed_dispatch_key": observed_dispatch_key or dispatch_key,
                    }
                )
            event = EventRecord(
                event_id=new_id("evt"),
                event_type=f"mission_attempt_{target_state}",
                created_at=now,
                payload=event_payload,
            )
            outbox = state.setdefault("protocol_event_outbox", [])
            outbox_ids = _validated_protocol_event_outbox_ids(outbox)
            journal_ids = self._strict_protocol_journal_event_ids()
            event_summary = asdict(event)
            try:
                event_id = validate_daemon_event_record(event_summary)
            except LeaseError:
                raise ValueError("protocol event record is invalid") from None
            if event_id in outbox_ids or event_id in journal_ids:
                raise ValueError("duplicate protocol event identity")
            index = next(
                index
                for index, item in enumerate(attempts)
                if type(item) is dict and item.get("attempt_id") == attempt_id
            )
            attempts[index] = candidate
            outbox.append(event_summary)
            self._atomic_save(state)
            return copy.deepcopy(candidate)

    def record_mission_attempt_submitted(
        self, *, attempt_id: str, dispatch_key: str, receipt_summary: str
    ) -> dict[str, Any]:
        return self._transition_mission_attempt_receipt(
            attempt_id=attempt_id,
            dispatch_key=dispatch_key,
            observed_dispatch_key=dispatch_key,
            receipt_summary=receipt_summary,
            target_state="submitted",
            reason=None,
        )

    def mark_mission_attempt_ambiguous(
        self,
        *,
        attempt_id: str,
        dispatch_key: str,
        observed_dispatch_key: str | None = None,
        receipt_summary: str,
        reason: str,
    ) -> dict[str, Any]:
        return self._transition_mission_attempt_receipt(
            attempt_id=attempt_id,
            dispatch_key=dispatch_key,
            observed_dispatch_key=observed_dispatch_key,
            receipt_summary=receipt_summary,
            target_state="ambiguous",
            reason=reason,
        )

    def claim_mission_execution(
        self,
        mission_id: str,
        *,
        resuming: bool,
        confirmed_at: str,
    ) -> dict[str, Any]:
        if not isinstance(resuming, bool):
            raise TypeError("resuming must be a boolean")
        if not isinstance(confirmed_at, str) or not confirmed_at:
            raise ValueError("confirmed_at must be a non-empty string")
        lock_path = self.state_path.parent / "mission-execution.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                state = self.load()
                record = next(
                    (
                        item
                        for item in state.setdefault("missions", [])
                        if isinstance(item, dict) and item.get("mission_id") == mission_id
                    ),
                    None,
                )
                if record is None:
                    raise KeyError(mission_id)
                status = record.get("status")
                if status in {"preparing", "running", "completed"}:
                    return {"claimed": False, "mission": copy.deepcopy(record)}
                allowed = {"stopped", "interrupted"} if resuming else {"pending_confirmation"}
                if status not in allowed:
                    raise ValueError("mission is not claimable")
                record.update(
                    {
                        "status": "preparing",
                        "confirmed_at": record.get("confirmed_at") or confirmed_at,
                        "stop_reason": None,
                        "blockers": [],
                        "can_start": False,
                        "updated_at": utc_now(),
                    }
                )
                self.save(state)
                return {"claimed": True, "mission": copy.deepcopy(record)}
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def update_mission(self, mission_id: str, /, **changes: Any) -> dict[str, Any]:
        state = self.load()
        record = next(
            (
                item
                for item in state.setdefault("missions", [])
                if isinstance(item, dict) and item.get("mission_id") == mission_id
            ),
            None,
        )
        if record is None:
            raise KeyError(mission_id)
        mutable_fields = {
            "status",
            "stop_reason",
            "can_start",
            "blockers",
            "workflow_run_id",
            "current_step",
            "confirmed_at",
        }
        unknown_fields = set(changes) - mutable_fields
        if unknown_fields:
            raise ValueError(
                f"immutable or unknown mission fields: {', '.join(sorted(unknown_fields))}"
            )
        current_status = record.get("status")
        target_status = changes.get("status", current_status)
        if current_status == "completed":
            if changes == {"status": "completed"}:
                return record
            raise ValueError("completed mission is terminal")
        if not mission_status_transition_allowed(current_status, target_status):
            raise ValueError(f"invalid mission status transition: {current_status} -> {target_status}")
        if "stop_reason" in changes and changes["stop_reason"] is not None and not isinstance(
            changes["stop_reason"], str
        ):
            raise ValueError("stop_reason must be a string or null")
        if "can_start" in changes and not isinstance(changes["can_start"], bool):
            raise ValueError("can_start must be a boolean")
        if "blockers" in changes:
            compact_blockers, invalid_blockers = compact_mission_blockers(
                changes["blockers"]
            )
            if invalid_blockers:
                raise ValueError("blockers must be a list of strings")
            changes["blockers"] = compact_blockers
        effective_can_start = changes.get("can_start", record.get("can_start"))
        effective_blockers = changes.get("blockers", record.get("blockers"))
        if effective_can_start is True and effective_blockers:
            raise ValueError("can_start requires empty blockers")
        if "workflow_run_id" in changes:
            workflow_run_id = changes["workflow_run_id"]
            if workflow_run_id is not None and not isinstance(workflow_run_id, str):
                raise ValueError("workflow_run_id must be a string or null")
            existing_run_id = record.get("workflow_run_id")
            if existing_run_id is not None and workflow_run_id != existing_run_id:
                raise ValueError("workflow_run_id cannot change once set")
        if "confirmed_at" in changes:
            confirmed_at = changes["confirmed_at"]
            if confirmed_at is not None and not isinstance(confirmed_at, str):
                raise ValueError("confirmed_at must be a string or null")
            existing_confirmed_at = record.get("confirmed_at")
            if existing_confirmed_at is not None and confirmed_at != existing_confirmed_at:
                raise ValueError("confirmed_at cannot change once set")
        if "current_step" in changes:
            current_step = changes["current_step"]
            step_count = record.get("step_count")
            if (
                not isinstance(current_step, int)
                or isinstance(current_step, bool)
                or not isinstance(step_count, int)
                or isinstance(step_count, bool)
                or current_step < record.get("current_step", 0)
                or current_step < 0
                or current_step > step_count
            ):
                raise ValueError("current_step must advance within 0..step_count")
        record.update(changes)
        record["updated_at"] = utc_now()
        if changes.get("status") == "completed" and not record.get("completed_at"):
            record["completed_at"] = record["updated_at"]
        self.save(state)
        return record

    def record_skill_load(
        self,
        *,
        agent_id: str,
        purpose: str,
        skill: dict[str, Any],
    ) -> dict[str, Any]:
        state = self.load()
        record = {
            "load_id": new_id("skl"),
            "agent_id": agent_id,
            "purpose": purpose,
            "name": skill.get("name"),
            "source": skill.get("source"),
            "path": skill.get("path"),
            "content_hash": skill.get("content_hash"),
            "content_snapshot": skill.get("content_snapshot"),
            "description": skill.get("description"),
            "required_tools": skill.get("required_tools") if isinstance(skill.get("required_tools"), list) else [],
            "planning_guidance": skill.get("planning_guidance")
            if isinstance(skill.get("planning_guidance"), list)
            else [],
            "risk": skill.get("risk"),
            "created_at": utc_now(),
        }
        state.setdefault("skill_loads", []).append(record)
        self.save(state)
        return record

    def record_skill_suggestion(
        self,
        *,
        name: str,
        summary: str,
        rationale: str,
        source: str,
        agent_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        record = {
            "suggestion_id": new_id("sgs"),
            "status": "pending",
            "name": name,
            "summary": summary,
            "rationale": rationale,
            "source": source,
            "agent_id": agent_id,
            "trace_id": trace_id,
            "draft_path": f".agentdeck/skills/{name}/SKILL.md",
            "created_at": utc_now(),
            "controls": [
                {
                    "kind": "inspect",
                    "label": "List skill suggestions",
                    "command": "agentdeck skills suggestions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                }
            ],
        }
        state.setdefault("skill_suggestions", []).append(record)
        self.save(state)
        return record

    def record_memory_suggestion(
        self,
        *,
        summary: str,
        rationale: str,
        source: str,
        scope: str,
        agent_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        target = ".agentdeck/memory/global.md" if scope == "global" else ".agentdeck/memory/project.md"
        record = {
            "suggestion_id": new_id("mem"),
            "status": "pending",
            "scope": scope,
            "summary": summary,
            "rationale": rationale,
            "source": source,
            "agent_id": agent_id,
            "trace_id": trace_id,
            "target": target,
            "created_at": utc_now(),
            "controls": [
                {
                    "kind": "inspect",
                    "label": "List memory suggestions",
                    "command": "agentdeck memory suggestions",
                    "safety": "inspect",
                    "enabled": True,
                    "blocker": None,
                }
            ],
        }
        state.setdefault("memory_suggestions", []).append(record)
        self.save(state)
        return record

    def record_release(
        self,
        *,
        review_gate_status: str,
        artifact_count: int,
        review_reply_count: int,
        code_reviewer_id: str | None,
        round_reviewer_id: str | None,
        code_review_reply_id: str | None,
        round_review_reply_id: str | None,
    ) -> dict[str, Any]:
        state = self.load()
        releases = state.setdefault("releases", [])
        record = {
            "release_id": new_id("rel"),
            "round": len(releases) + 1,
            "status": "released",
            "review_gate_status": review_gate_status,
            "artifact_count": artifact_count,
            "review_reply_count": review_reply_count,
            "code_reviewer_id": code_reviewer_id,
            "round_reviewer_id": round_reviewer_id,
            "code_review_reply_id": code_review_reply_id,
            "round_review_reply_id": round_review_reply_id,
            "created_at": utc_now(),
        }
        releases.append(record)
        self.save(state)
        return record

    def list_releases(self) -> list[dict[str, Any]]:
        releases = self.load().get("releases", [])
        return [item for item in releases if isinstance(item, dict)]

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        events = self.all_events()
        if limit <= 0:
            return []
        return events[-limit:]

    def all_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def bind_agent(self, binding: AgentRuntimeBinding) -> None:
        state = self.load()
        agents = state.setdefault("agents", {})
        agents[binding.agent_id] = asdict(binding)
        self.save(state)

    def mark_agent_stopped(self, agent_id: str) -> dict[str, Any]:
        state = self.load()
        agents = state.setdefault("agents", {})
        current = agents.get(
            agent_id,
            {
                "agent_id": agent_id,
                "pane_id": None,
                "session_name": None,
                "cwd": None,
                "status": "configured",
            },
        )
        current.update({"pane_id": None, "status": "stopped"})
        agents[agent_id] = current
        self.save(state)
        return current

    def mark_agent_stale(self, agent_id: str) -> dict[str, Any]:
        state = self.load()
        agents = state.setdefault("agents", {})
        current = agents.get(
            agent_id,
            {
                "agent_id": agent_id,
                "pane_id": None,
                "session_name": None,
                "cwd": None,
                "status": "configured",
            },
        )
        current.update({"pane_id": None, "status": "stale"})
        agents[agent_id] = current
        self.save(state)
        return current

    def agent_binding(self, agent_id: str) -> dict[str, Any] | None:
        return self.load().get("agents", {}).get(agent_id)

    def append_message(self, from_actor: str, to_agent: str, task: str, prompt: str) -> dict[str, Any]:
        state = self.load()
        messages = state.setdefault("messages", [])
        message = {
            "message_id": new_id("msg"),
            "from_actor": from_actor,
            "to_agent": to_agent,
            "task": task,
            "prompt": prompt,
            "status": "dispatched",
            "created_at": utc_now(),
        }
        messages.append(message)
        self.save(state)
        return message

    def record_plan(
        self,
        task: str,
        provider: str,
        model: str,
        plan: dict[str, Any],
        skill_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        record = {
            "plan_id": new_id("pln"),
            "task": task,
            "provider": provider,
            "provider_backend": leader_provider_backend(provider),
            "provider_transport": leader_provider_transport(provider),
            "leader_backend": leader_backend_identity(provider, model, bool(plan.get("dispatch_ready", False))),
            "model": model,
            "status": "planned",
            "dispatch_ready": bool(plan.get("dispatch_ready", False)),
            "skill_context": self._plan_skill_context(skill_context),
            "plan": plan,
            "created_at": utc_now(),
        }
        state.setdefault("plans", []).append(record)
        self.save(state)
        return record

    def build_plan_record(
        self,
        task: str,
        provider: str,
        model: str,
        plan: dict[str, Any],
        skill_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "plan_id": new_id("pln"),
            "task": task,
            "provider": provider,
            "provider_backend": leader_provider_backend(provider),
            "provider_transport": leader_provider_transport(provider),
            "leader_backend": leader_backend_identity(
                provider, model, bool(plan.get("dispatch_ready", False))
            ),
            "model": model,
            "status": "planned",
            "dispatch_ready": bool(plan.get("dispatch_ready", False)),
            "skill_context": self._plan_skill_context(skill_context),
            "plan": copy.deepcopy(plan),
            "created_at": utc_now(),
        }

    def list_plans(self) -> list[dict[str, Any]]:
        return list(self.load().get("plans", []))

    def plan_by_id(self, plan_id: str) -> dict[str, Any]:
        for plan in self.load().get("plans", []):
            if plan.get("plan_id") == plan_id:
                return plan
        raise KeyError(plan_id)

    def create_workflow_run(
        self,
        *,
        plan_id: str,
        plan_hash: str,
        timeout_seconds: int,
        authorized_steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        state = self.load()
        now = utc_now()
        record = {
            "run_id": new_id("wfr"),
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "status": "running",
            "current_step": 1,
            "step_count": len(authorized_steps),
            "timeout_seconds": timeout_seconds,
            "authorized_steps": authorized_steps,
            "turns": [],
            "stop_reason": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        state.setdefault("workflow_runs", []).append(record)
        self.save(state)
        return record

    def workflow_run_by_id(self, run_id: str) -> dict[str, Any]:
        for item in self.load().get("workflow_runs", []):
            if item.get("run_id") == run_id:
                return item
        raise KeyError(run_id)

    def update_workflow_run(self, run_id: str, **changes: Any) -> dict[str, Any]:
        state = self.load()
        record = next(
            (
                item
                for item in state.setdefault("workflow_runs", [])
                if item.get("run_id") == run_id
            ),
            None,
        )
        if record is None:
            raise KeyError(run_id)
        record.update(changes)
        record["updated_at"] = utc_now()
        if changes.get("status") == "completed" and not record.get("completed_at"):
            record["completed_at"] = record["updated_at"]
        self.save(state)
        return record

    def plan_status(self, plan_id: str) -> dict[str, Any]:
        state = self.load()
        plan_record = next((plan for plan in state.get("plans", []) if plan.get("plan_id") == plan_id), None)
        if plan_record is None:
            raise KeyError(plan_id)
        plan_body = plan_record.get("plan", {})
        steps = plan_body.get("steps", []) if isinstance(plan_body, dict) else []
        approvals = [item for item in state.get("approvals", []) if item.get("plan_id") == plan_id]
        approvals_by_step = {item.get("step"): item for item in approvals}
        status_counts = {
            "steps": len(steps) if isinstance(steps, list) else 0,
            "approvals": len(approvals),
            "pending": 0,
            "approved": 0,
            "rejected": 0,
            "dispatched": 0,
        }
        status_steps = []
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                approval = approvals_by_step.get(step.get("step"))
                approval_status = approval.get("status") if approval else "not_created"
                if approval_status in status_counts:
                    status_counts[approval_status] += 1
                status_item = {
                    "step": step.get("step"),
                    "agent_id": step.get("agent_id"),
                    "role": step.get("role"),
                    "task": step.get("task"),
                    "approval_id": approval.get("approval_id") if approval else None,
                    "approval_status": approval_status,
                    "message_id": approval.get("message_id") if approval else None,
                    "attempt_id": approval.get("attempt_id") if approval else None,
                    "job_id": approval.get("job_id") if approval else None,
                }
                if approval and approval.get("reason"):
                    status_item["reason"] = approval.get("reason")
                status_steps.append(status_item)
        return {
            "plan_id": plan_id,
            "task": plan_record.get("task"),
            "status": plan_record.get("status"),
            "provider": plan_record.get("provider"),
            "provider_backend": plan_record.get("provider_backend")
            or leader_provider_backend(str(plan_record.get("provider") or "")),
            "provider_transport": plan_record.get("provider_transport")
            or leader_provider_transport(str(plan_record.get("provider") or "")),
            "leader_backend": plan_record.get("leader_backend")
            or leader_backend_identity(
                str(plan_record.get("provider") or ""),
                str(plan_record.get("model") or ""),
                bool(plan_record.get("dispatch_ready", False)),
            ),
            "model": plan_record.get("model"),
            "created_at": plan_record.get("created_at"),
            "skill_context": self._plan_skill_context(plan_record.get("skill_context")),
            "counts": status_counts,
            "steps": status_steps,
        }

    def leader_review(self, plan_id: str) -> dict[str, Any]:
        status = self.plan_status(plan_id)
        leader_backend = status.get("leader_backend")
        state = self.load()
        replies = state.get("replies", [])
        replies_by_message = {reply.get("message_id"): reply for reply in replies}
        for step in status["steps"]:
            if step.get("approval_status") == "approved":
                return {
                    "plan_id": plan_id,
                    "next_action": "dispatch_approved",
                    "reason": "approved step is waiting for dispatch",
                    "approval_id": step.get("approval_id"),
                    "agent_id": step.get("agent_id"),
                    "counts": status["counts"],
                    "leader_backend": leader_backend,
                }
        dispatched_without_reply = []
        completed_replies = []
        for step in status["steps"]:
            message_id = step.get("message_id")
            if step.get("approval_status") != "dispatched" or not message_id:
                continue
            reply = replies_by_message.get(message_id)
            if reply is None:
                dispatched_without_reply.append(step)
            else:
                completed_replies.append(
                    {
                        "agent_id": step.get("agent_id"),
                        "message_id": message_id,
                        "reply_id": reply.get("reply_id"),
                    }
                )
        if dispatched_without_reply:
            step = dispatched_without_reply[0]
            return {
                "plan_id": plan_id,
                "next_action": "wait_for_reply",
                "reason": "dispatched step has no reply yet",
                "agent_id": step.get("agent_id"),
                "message_id": step.get("message_id"),
                "counts": status["counts"],
                "leader_backend": leader_backend,
            }
        if completed_replies:
            return {
                "plan_id": plan_id,
                "next_action": "summarize",
                "reason": "all dispatched steps have replies",
                "replies": completed_replies,
                "counts": status["counts"],
                "leader_backend": leader_backend,
            }
        return {
            "plan_id": plan_id,
            "next_action": "wait_for_approval",
            "reason": "no approved or dispatched steps are ready",
            "counts": status["counts"],
            "leader_backend": leader_backend,
        }

    def record_chat_turn(
        self,
        mode: str,
        message: str,
        plan_id: str | None,
        next_command: str | None,
        provider: str | None = None,
        model: str | None = None,
        review: dict[str, Any] | None = None,
        action_id: str | None = None,
        action_kind: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        turn = {
            "turn_id": new_id("cht"),
            "mode": mode,
            "message": message,
            "plan_id": plan_id,
            "next_command": next_command,
            "provider": provider,
            "model": model,
            "review": review,
            "action_id": action_id,
            "action_kind": action_kind,
            "created_at": utc_now(),
        }
        state.setdefault("chat_turns", []).append(turn)
        self.save(state)
        return turn

    def list_chat_turns(self) -> list[dict[str, Any]]:
        return list(self.load().get("chat_turns", []))

    def record_leader_error(
        self,
        mode: str,
        provider: str,
        model: str | None,
        task: str,
        error: str,
    ) -> dict[str, Any]:
        state = self.load()
        record = {
            "error_id": new_id("err"),
            "mode": mode,
            "provider": provider,
            "model": model,
            "task": task,
            "error": error,
            "created_at": utc_now(),
        }
        state.setdefault("leader_errors", []).append(record)
        self.save(state)
        return record

    def suggest_leader_action(self, plan_id: str | None = None) -> dict[str, Any]:
        state = self.load()
        plans = state.get("plans", [])
        if plan_id is None:
            if not plans:
                raise KeyError("no plans")
            plan_id = str(plans[-1]["plan_id"])
        status = self.plan_status(plan_id)
        if status["counts"]["approvals"] == 0:
            action = {
                "action_id": new_id("act"),
                "kind": "create_approvals",
                "status": "pending",
                "requires_confirmation": True,
                "plan_id": plan_id,
                "approval_id": None,
                "agent_id": None,
                "message_id": None,
                "command": f"agentdeck approval create-from-plan --plan-id {plan_id}",
                "reason": "plan has no approval records",
                "created_at": utc_now(),
            }
            return self._record_or_reuse_pending_leader_action(state, action)
        review = self.leader_review(plan_id)
        action = self._action_from_review(review)
        state = self.load()
        return self._record_or_reuse_pending_leader_action(state, action)

    def _record_or_reuse_pending_leader_action(self, state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        actions = state.setdefault("leader_actions", [])
        existing = next((item for item in actions if self._same_pending_leader_action(item, action)), None)
        if existing is not None:
            return existing
        actions.append(action)
        self.save(state)
        return action

    @staticmethod
    def _same_pending_leader_action(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        if existing.get("status") != "pending":
            return False
        return all(
            existing.get(key) == candidate.get(key)
            for key in ["kind", "plan_id", "approval_id", "agent_id", "message_id"]
        )

    def _action_from_review(self, review: dict[str, Any]) -> dict[str, Any]:
        next_action = review.get("next_action")
        command = None
        if next_action == "dispatch_approved" and review.get("approval_id"):
            command = f"agentdeck approval dispatch --approval-id {review['approval_id']}"
        elif next_action == "wait_for_reply" and review.get("agent_id") and review.get("message_id"):
            command = f"agentdeck capture-reply --agent {review['agent_id']} --message-id {review['message_id']}"
        elif next_action == "summarize" and review.get("plan_id"):
            command = f"agentdeck leader summary --plan-id {review['plan_id']}"
        elif next_action == "wait_for_approval" and review.get("plan_id"):
            command = f"agentdeck approval list"
        return {
            "action_id": new_id("act"),
            "kind": next_action,
            "status": "pending",
            "requires_confirmation": True,
            "plan_id": review.get("plan_id"),
            "approval_id": review.get("approval_id"),
            "agent_id": review.get("agent_id"),
            "message_id": review.get("message_id"),
            "command": command,
            "reason": review.get("reason"),
            "created_at": utc_now(),
        }

    def list_leader_actions(self) -> list[dict[str, Any]]:
        return list(self.load().get("leader_actions", []))

    def leader_action_detail(self, action_id: str) -> dict[str, Any]:
        action = next((item for item in self.load().get("leader_actions", []) if item.get("action_id") == action_id), None)
        if action is None:
            raise KeyError(action_id)
        return {**action, **self._leader_action_detail_fields(action)}

    @staticmethod
    def _leader_action_detail_fields(action: dict[str, Any]) -> dict[str, Any]:
        action_id = str(action.get("action_id"))
        can_apply = action.get("status") == "pending" and action.get("kind") == "create_approvals"
        apply_blocker = None
        if action.get("status") != "pending":
            apply_blocker = f"leader action is not pending: {action_id}"
        elif action.get("kind") != "create_approvals":
            apply_blocker = "leader action requires explicit command"
        apply_command = f"agentdeck leader apply-action --action-id {action_id}" if can_apply else None
        return {
            "can_apply": can_apply,
            "preview_command": f"agentdeck leader action --action-id {action_id}",
            "controls": StateStore._leader_action_controls(
                action_id=action_id,
                apply_command=apply_command,
                explicit_command=action.get("command"),
                apply_blocker=apply_blocker,
            ),
            "apply_command": apply_command,
            "explicit_command": action.get("command"),
            "apply_blocker": apply_blocker,
        }

    @staticmethod
    def _leader_action_controls(
        *, action_id: str, apply_command: object, explicit_command: object, apply_blocker: object
    ) -> list[dict[str, Any]]:
        return [
            {
                "kind": "preview",
                "label": "Preview Leader action",
                "command": f"agentdeck leader action --action-id {action_id}",
                "safety": "inspect",
                "enabled": True,
                "blocker": None,
            },
            {
                "kind": "apply",
                "label": "Apply safe Leader action",
                "command": apply_command,
                "safety": "safe_apply",
                "enabled": apply_command is not None,
                "blocker": apply_blocker,
            },
            {
                "kind": "explicit",
                "label": "Run explicit command",
                "command": explicit_command,
                "safety": "explicit_runtime",
                "enabled": explicit_command is not None,
                "blocker": None,
            },
        ]

    def apply_leader_action(self, action_id: str) -> dict[str, Any]:
        state = self.load()
        action = next((item for item in state.get("leader_actions", []) if item.get("action_id") == action_id), None)
        if action is None:
            raise KeyError(action_id)
        if action.get("status") != "pending":
            raise ValueError(f"leader action is not pending: {action_id}")
        if action.get("kind") != "create_approvals":
            raise PermissionError(f"leader action requires explicit command: {action_id}")
        plan_id = str(action.get("plan_id"))
        approvals = self._create_approvals_from_plan_state(state, plan_id)
        action["status"] = "applied"
        action["applied_at"] = utc_now()
        self.save(state)
        return {
            "action": action,
            "result": {
                "plan_id": plan_id,
                "count": len(approvals),
                "approvals": approvals,
            },
        }

    def create_approvals_from_plan(self, plan_id: str) -> list[dict[str, Any]]:
        state = self.load()
        approvals = self._create_approvals_from_plan_state(state, plan_id)
        self.save(state)
        return approvals

    def create_chat_assignment_approval(self, agent_id: str, role: str, task: str) -> dict[str, Any]:
        state = self.load()
        approval = {
            "approval_id": new_id("apv"),
            "plan_id": None,
            "step": 1,
            "agent_id": agent_id,
            "role": role,
            "task": task,
            "risk": "human_requested",
            "status": "pending",
            "source": "leader_chat_task_assignment",
            "created_at": utc_now(),
        }
        state.setdefault("approvals", []).append(approval)
        self.save(state)
        return approval

    def _create_approvals_from_plan_state(self, state: dict[str, Any], plan_id: str) -> list[dict[str, Any]]:
        plan_record = next((plan for plan in state.get("plans", []) if plan.get("plan_id") == plan_id), None)
        if plan_record is None:
            raise KeyError(plan_id)
        existing = [item for item in state.setdefault("approvals", []) if item.get("plan_id") == plan_id]
        if existing:
            return existing
        plan_body = plan_record.get("plan", {})
        steps = plan_body.get("steps", []) if isinstance(plan_body, dict) else []
        approvals = []
        for step in steps:
            if not isinstance(step, dict) or not step.get("requires_approval", False):
                continue
            approval = {
                "approval_id": new_id("apv"),
                "plan_id": plan_id,
                "step": step.get("step"),
                "agent_id": step.get("agent_id"),
                "role": step.get("role"),
                "task": step.get("task"),
                "risk": step.get("risk"),
                "status": "pending",
                "created_at": utc_now(),
            }
            approvals.append(approval)
        state.setdefault("approvals", []).extend(approvals)
        return approvals

    def list_approvals(self) -> list[dict[str, Any]]:
        return list(self.load().get("approvals", []))

    def approval_by_id(self, approval_id: str) -> dict[str, Any]:
        for approval in self.load().get("approvals", []):
            if approval.get("approval_id") == approval_id:
                return approval
        raise KeyError(approval_id)

    def decide_approval(self, approval_id: str, status: str, reason: str | None = None) -> dict[str, Any]:
        state = self.load()
        approval = next((item for item in state.setdefault("approvals", []) if item.get("approval_id") == approval_id), None)
        if approval is None:
            raise KeyError(approval_id)
        approval["status"] = status
        approval["decided_at"] = utc_now()
        if reason:
            approval["reason"] = reason
        self.save(state)
        return approval

    def mark_approval_dispatched(self, approval_id: str, message_id: str, attempt_id: str, job_id: str) -> dict[str, Any]:
        state = self.load()
        approval = next((item for item in state.setdefault("approvals", []) if item.get("approval_id") == approval_id), None)
        if approval is None:
            raise KeyError(approval_id)
        approval["status"] = "dispatched"
        approval["message_id"] = message_id
        approval["attempt_id"] = attempt_id
        approval["job_id"] = job_id
        approval["dispatched_at"] = utc_now()
        self.save(state)
        return approval

    def create_dispatch_records(
        self,
        from_actor: str,
        to_agent: str,
        task: str,
        prompt: str,
        pane_id: str,
        prompt_skill_context: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        state = self.load()
        message = {
            "message_id": new_id("msg"),
            "from_actor": from_actor,
            "to_agent": to_agent,
            "task": task,
            "prompt": prompt,
            "status": "dispatched",
            "created_at": utc_now(),
        }
        if prompt_skill_context is not None:
            message["prompt_skill_context"] = self._plan_skill_context(prompt_skill_context)
        attempt = {
            "attempt_id": new_id("att"),
            "message_id": message["message_id"],
            "agent_id": to_agent,
            "status": "dispatched",
            "created_at": utc_now(),
        }
        job = {
            "job_id": new_id("job"),
            "message_id": message["message_id"],
            "attempt_id": attempt["attempt_id"],
            "agent_id": to_agent,
            "pane_id": pane_id,
            "status": "dispatched",
            "created_at": utc_now(),
        }
        inbox_item = {
            "inbox_id": new_id("inb"),
            "event_type": "task_request",
            "message_id": message["message_id"],
            "attempt_id": attempt["attempt_id"],
            "job_id": job["job_id"],
            "from_actor": from_actor,
            "to_agent": to_agent,
            "task": task,
            "status": "pending",
            "created_at": utc_now(),
        }
        state.setdefault("messages", []).append(message)
        state.setdefault("attempts", []).append(attempt)
        state.setdefault("jobs", []).append(job)
        state.setdefault("inbox", {}).setdefault(to_agent, []).append(inbox_item)
        self.save(state)
        return {
            "message": message,
            "attempt": attempt,
            "job": job,
            "inbox_item": inbox_item,
        }

    def inbox_items(self, agent_id: str) -> list[dict[str, Any]]:
        return list(self.load().get("inbox", {}).get(agent_id, []))

    def record_reply(self, from_agent: str, message_id: str, text: str) -> dict[str, Any]:
        state = self.load()
        messages = state.setdefault("messages", [])
        message = next((item for item in messages if item.get("message_id") == message_id), None)
        if message is None:
            raise KeyError(message_id)
        attempt = next(
            (
                item
                for item in state.setdefault("attempts", [])
                if item.get("message_id") == message_id and item.get("agent_id") == from_agent
            ),
            None,
        )
        job = next(
            (
                item
                for item in state.setdefault("jobs", [])
                if item.get("message_id") == message_id and item.get("agent_id") == from_agent
            ),
            None,
        )
        reply = {
            "reply_id": new_id("rep"),
            "message_id": message_id,
            "attempt_id": attempt.get("attempt_id") if attempt else None,
            "job_id": job.get("job_id") if job else None,
            "from_agent": from_agent,
            "to_actor": message.get("from_actor", "user"),
            "text": text,
            "created_at": utc_now(),
        }
        state.setdefault("replies", []).append(reply)
        artifacts = self._artifacts_from_reply(reply, text)
        state.setdefault("artifacts", []).extend(artifacts)
        message["status"] = "replied"
        if attempt:
            attempt["status"] = "completed"
        if job:
            job["status"] = "completed"
        to_actor = str(message.get("from_actor", "user"))
        if to_actor != "user":
            state.setdefault("inbox", {}).setdefault(to_actor, []).append(
                {
                    "inbox_id": new_id("inb"),
                    "event_type": "task_reply",
                    "message_id": message_id,
                    "attempt_id": reply["attempt_id"],
                    "job_id": reply["job_id"],
                    "reply_id": reply["reply_id"],
                    "from_agent": from_agent,
                    "to_agent": to_actor,
                    "task": message.get("task", ""),
                    "status": "pending",
                    "created_at": utc_now(),
                }
            )
        self.save(state)
        return {**reply, "artifacts": artifacts}

    @classmethod
    def _artifacts_from_reply(cls, reply: dict[str, Any], text: str) -> list[dict[str, Any]]:
        output_path = cls._structured_reply_value(text, "full_output_path")
        if not output_path:
            return []
        return [
            {
                "artifact_id": new_id("art"),
                "message_id": reply.get("message_id"),
                "attempt_id": reply.get("attempt_id"),
                "job_id": reply.get("job_id"),
                "reply_id": reply.get("reply_id"),
                "from_agent": reply.get("from_agent"),
                "path": output_path,
                "kind": cls._artifact_kind(output_path),
                "status": "created",
                "created_at": utc_now(),
            }
        ]

    @staticmethod
    def _structured_reply_value(text: str, key: str) -> str | None:
        prefix = f"{key}:"
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith(prefix):
                value = stripped[len(prefix):].strip().strip("\"'`")
                return value or None
        return None

    @staticmethod
    def _artifact_kind(path: str) -> str:
        suffix = Path(path).suffix.lower()
        if suffix in {".md", ".markdown"}:
            return "markdown"
        if suffix == ".json":
            return "json"
        if suffix in {".txt", ".log"}:
            return "text"
        if suffix == ".py":
            return "python"
        return "file"

    def ack_inbox_item(self, agent_id: str, inbox_id: str) -> dict[str, Any]:
        state = self.load()
        items = state.setdefault("inbox", {}).setdefault(agent_id, [])
        item = next((entry for entry in items if entry.get("inbox_id") == inbox_id), None)
        if item is None:
            raise KeyError(inbox_id)
        head = next((entry for entry in items if entry.get("status") == "pending"), None)
        if head is not None and head.get("inbox_id") != inbox_id:
            raise ValueError(f"inbox item is not head: {inbox_id}; head is {head['inbox_id']}")
        item["status"] = "acked"
        item["acked_at"] = utc_now()
        self.save(state)
        return item

    def trace(self, query_id: str) -> dict[str, Any]:
        state = self.load()
        message_id = self._resolve_message_id(state, query_id)
        if message_id is None:
            raise KeyError(query_id)
        message = next(item for item in state.get("messages", []) if item.get("message_id") == message_id)
        attempts = [item for item in state.get("attempts", []) if item.get("message_id") == message_id]
        jobs = [item for item in state.get("jobs", []) if item.get("message_id") == message_id]
        replies = [item for item in state.get("replies", []) if item.get("message_id") == message_id]
        artifacts = [item for item in state.get("artifacts", []) if item.get("message_id") == message_id]
        inbox_items = []
        for items in state.get("inbox", {}).values():
            inbox_items.extend(item for item in items if item.get("message_id") == message_id)
        return {
            "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
            "query_id": query_id,
            "message": self._trace_message(message),
            "plan": self._trace_plan_for_message(state, message_id),
            "attempts": [self._trace_attempt(item) for item in attempts],
            "jobs": [self._trace_job(item) for item in jobs],
            "replies": [self._trace_reply(item) for item in replies],
            "artifacts": [self._trace_artifact(item) for item in artifacts],
            "inbox_items": [self._trace_inbox_item(item) for item in inbox_items],
            "controls": self._trace_controls(query_id),
        }

    @staticmethod
    def _trace_controls(query_id: Any) -> list[dict[str, Any]]:
        trace_command = StateStore._trace_command(query_id)
        return [
            {
                "kind": "inspect",
                "label": "Inspect trace",
                "command": trace_command,
                "safety": "inspect",
                "enabled": trace_command is not None,
                "blocker": None if trace_command is not None else "requires trace id",
            }
        ]

    @staticmethod
    def _trace_plan_for_message(state: dict[str, Any], message_id: str) -> dict[str, Any] | None:
        approval = next(
            (item for item in state.get("approvals", []) if item.get("message_id") == message_id and item.get("plan_id")),
            None,
        )
        if approval is None:
            return None
        plan_id = approval.get("plan_id")
        plan = next((item for item in state.get("plans", []) if item.get("plan_id") == plan_id), None)
        if plan is None:
            return None
        body = plan.get("plan", {})
        steps = body.get("steps", []) if isinstance(body, dict) else []
        return {
            "plan_id": plan.get("plan_id"),
            "task": plan.get("task"),
            "status": plan.get("status"),
            "provider": plan.get("provider"),
            "provider_backend": plan.get("provider_backend")
            or leader_provider_backend(str(plan.get("provider") or "")),
            "provider_transport": plan.get("provider_transport")
            or leader_provider_transport(str(plan.get("provider") or "")),
            "leader_backend": plan.get("leader_backend")
            or leader_backend_identity(
                str(plan.get("provider") or ""),
                str(plan.get("model") or ""),
                bool(plan.get("dispatch_ready", False)),
            ),
            "model": plan.get("model"),
            "dispatch_ready": plan.get("dispatch_ready"),
            "skill_context": StateStore._plan_skill_context(plan.get("skill_context")),
            "step_count": len(steps) if isinstance(steps, list) else 0,
            "created_at": plan.get("created_at"),
        }

    @staticmethod
    def _trace_message(message: dict[str, Any]) -> dict[str, Any]:
        return {
            "message_id": message.get("message_id"),
            "from_actor": message.get("from_actor"),
            "to_agent": message.get("to_agent"),
            "task": message.get("task"),
            "prompt": message.get("prompt"),
            "prompt_skill_context": StateStore._plan_skill_context(message.get("prompt_skill_context")),
            "status": message.get("status"),
            "created_at": message.get("created_at"),
        }

    @staticmethod
    def _trace_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
        return {
            "attempt_id": attempt.get("attempt_id"),
            "message_id": attempt.get("message_id"),
            "agent_id": attempt.get("agent_id"),
            "status": attempt.get("status"),
            "created_at": attempt.get("created_at"),
        }

    @staticmethod
    def _trace_job(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": job.get("job_id"),
            "message_id": job.get("message_id"),
            "attempt_id": job.get("attempt_id"),
            "agent_id": job.get("agent_id"),
            "pane_id": job.get("pane_id"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
        }

    @staticmethod
    def _trace_reply(reply: dict[str, Any]) -> dict[str, Any]:
        return {
            "reply_id": reply.get("reply_id"),
            "message_id": reply.get("message_id"),
            "attempt_id": reply.get("attempt_id"),
            "job_id": reply.get("job_id"),
            "from_agent": reply.get("from_agent"),
            "to_actor": reply.get("to_actor"),
            "text": reply.get("text"),
            "created_at": reply.get("created_at"),
        }

    @staticmethod
    def _trace_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": artifact.get("artifact_id"),
            "message_id": artifact.get("message_id"),
            "attempt_id": artifact.get("attempt_id"),
            "job_id": artifact.get("job_id"),
            "reply_id": artifact.get("reply_id"),
            "from_agent": artifact.get("from_agent"),
            "path": artifact.get("path"),
            "kind": artifact.get("kind"),
            "status": artifact.get("status"),
            "created_at": artifact.get("created_at"),
        }

    @staticmethod
    def _trace_inbox_item(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "inbox_id": item.get("inbox_id"),
            "event_type": item.get("event_type"),
            "message_id": item.get("message_id"),
            "attempt_id": item.get("attempt_id"),
            "job_id": item.get("job_id"),
            "reply_id": item.get("reply_id"),
            "from_actor": item.get("from_actor"),
            "from_agent": item.get("from_agent"),
            "to_agent": item.get("to_agent"),
            "task": item.get("task"),
            "status": item.get("status"),
            "created_at": item.get("created_at"),
        }

    def _resolve_message_id(self, state: dict[str, Any], query_id: str) -> str | None:
        for item in state.get("messages", []):
            if item.get("message_id") == query_id:
                return str(item["message_id"])
        for collection in ("attempts", "jobs", "replies"):
            for item in state.get(collection, []):
                if query_id in {
                    item.get("attempt_id"),
                    item.get("job_id"),
                    item.get("reply_id"),
                }:
                    return str(item["message_id"])
        for items in state.get("inbox", {}).values():
            for item in items:
                if item.get("inbox_id") == query_id:
                    return str(item["message_id"])
        for item in state.get("artifacts", []):
            if item.get("artifact_id") == query_id:
                return str(item["message_id"])
        return None

    @staticmethod
    def _status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            status = str(item.get("status", "unknown"))
            counts[status] = counts.get(status, 0) + 1
        return counts

    @staticmethod
    def _protocol_summary_items(
        records: object,
        *,
        identity_field: str,
        group_field: str,
        item_fields: tuple[str, ...],
        transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        if not isinstance(records, list):
            raise ValueError("protocol summary source must be a list")
        prepared: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        identities: set[str] = set()
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError(f"protocol summary item at index {index} must be an object")
            identity = record.get(identity_field)
            created_at = record.get("created_at")
            group = record.get(group_field)
            if not isinstance(identity, str) or not isinstance(created_at, str) or not isinstance(group, str):
                raise ValueError(f"invalid protocol summary item at index {index}")
            if identity in identities:
                raise ValueError(f"duplicate {identity_field}: {identity}")
            identities.add(identity)
            for field in item_fields:
                value = record.get(field)
                if field == "decision" and (
                    value is None or isinstance(value, str) and bool(value.strip())
                ):
                    continue
                if field == "reason" and (
                    value is None or isinstance(value, str) and bool(value.strip())
                ):
                    continue
                if field == "sequence" and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    continue
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"invalid protocol summary field: {field}")
            item = {field: record.get(field) for field in item_fields}
            prepared.append(transform(record) if transform is not None else item)
            counts[group] = counts.get(group, 0) + 1
        prepared.sort(key=lambda item: (str(item.get("created_at", "")), str(item.get(identity_field, ""))))
        return prepared[-20:], {key: counts[key] for key in sorted(counts)}

    @staticmethod
    def _agent_session_summaries(records: object) -> dict[str, Any]:
        capability_fields = (
            "structured_sessions", "streaming_updates", "structured_tools",
            "permission_requests", "resume_session", "observable_terminal",
        )

        def compact(record: dict[str, Any]) -> dict[str, Any]:
            for field in ("session_id", "agent_id", "provider", "transport", "workspace", "created_at", "updated_at"):
                if not isinstance(record.get(field), str) or not record[field].strip():
                    raise ValueError(f"invalid agent session field: {field}")
            if record.get("state") not in AGENT_SESSION_STATES:
                raise ValueError("invalid agent session state")
            transport = record.get("transport")
            if type(transport) is not str or transport not in TRANSPORT_KINDS:
                raise ValueError("invalid agent session transport")
            native_session_id = record.get("native_session_id")
            if native_session_id is not None and (
                not isinstance(native_session_id, str) or not native_session_id.strip()
            ):
                raise ValueError("invalid agent session native_session_id")
            capabilities = record.get("capabilities")
            if (
                not isinstance(capabilities, dict)
                or set(capabilities) != set(capability_fields)
                or any(type(capabilities[field]) is not bool for field in capability_fields)
            ):
                raise ValueError("invalid agent session capabilities")
            return {
                "session_id": record.get("session_id"),
                "agent_id": record.get("agent_id"),
                "provider": record.get("provider"),
                "transport": record.get("transport"),
                "state": record.get("state"),
                "capabilities": {field: capabilities.get(field) for field in capability_fields},
                "native_session_present": bool(record.get("native_session_id")),
                "workspace": record.get("workspace"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
            }

        items, by_state = StateStore._protocol_summary_items(
            records, identity_field="session_id", group_field="state", item_fields=(), transform=compact,
        )
        return {"count": len(records), "by_state": by_state, "items": items}

    @staticmethod
    def _protocol_turn_summaries(records: object) -> dict[str, Any]:
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and record.get("state") not in TURN_STATES:
                    raise ValueError("invalid protocol turn state")
        items, by_state = StateStore._protocol_summary_items(
            records, identity_field="turn_id", group_field="state",
            item_fields=("turn_id", "session_id", "message_id", "state", "created_at", "updated_at"),
        )
        return {"count": len(records), "by_state": by_state, "items": items}

    @staticmethod
    def _transport_update_summaries(records: object) -> dict[str, Any]:
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and record.get("kind") not in UPDATE_KINDS:
                    raise ValueError("invalid transport update kind")
                if isinstance(record, dict) and (
                    type(record.get("sequence")) is not int or record["sequence"] < 0
                ):
                    raise ValueError("invalid transport update sequence")
        items, by_kind = StateStore._protocol_summary_items(
            records, identity_field="update_id", group_field="kind",
            item_fields=("update_id", "session_id", "turn_id", "sequence", "kind", "created_at"),
        )
        return {"count": len(records), "by_kind": by_kind, "items": items}

    @staticmethod
    def _permission_request_summaries(records: object) -> dict[str, Any]:
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and record.get("status") not in PERMISSION_STATES:
                    raise ValueError("invalid permission request status")
        items, by_status = StateStore._protocol_summary_items(
            records, identity_field="permission_id", group_field="status",
            item_fields=(
                "permission_id", "session_id", "turn_id", "tool_name", "risk", "status", "decision", "created_at",
            ),
        )
        return {
            "count": len(records),
            "pending_count": by_status.get("pending", 0),
            "by_status": by_status,
            "items": items,
        }

    @staticmethod
    def _protocol_transition_summaries(records: object) -> dict[str, Any]:
        item_fields = (
            "transition_id", "entity_type", "entity_id", "from_state",
            "to_state", "reason", "created_at",
        )
        items, by_entity_type = StateStore._protocol_summary_items(
            records,
            identity_field="transition_id",
            group_field="entity_type",
            item_fields=item_fields,
        )
        return {
            "count": len(records),
            "by_entity_type": by_entity_type,
            "items": items,
        }

    @staticmethod
    def _plan_summaries(plans: list[dict[str, Any]]) -> dict[str, Any]:
        items = []
        for plan in plans:
            body = plan.get("plan", {})
            steps = body.get("steps", []) if isinstance(body, dict) else []
            items.append(
                {
                    "plan_id": plan.get("plan_id"),
                    "task": plan.get("task"),
                    "status": plan.get("status"),
                    "provider": plan.get("provider"),
                    "provider_backend": plan.get("provider_backend")
                    or leader_provider_backend(str(plan.get("provider") or "")),
                    "provider_transport": plan.get("provider_transport")
                    or leader_provider_transport(str(plan.get("provider") or "")),
                    "leader_backend": plan.get("leader_backend")
                    or leader_backend_identity(
                        str(plan.get("provider") or ""),
                        str(plan.get("model") or ""),
                        bool(plan.get("dispatch_ready", False)),
                    ),
                    "model": plan.get("model"),
                    "dispatch_ready": plan.get("dispatch_ready"),
                    "skill_context": StateStore._plan_skill_context(plan.get("skill_context")),
                    "step_count": len(steps) if isinstance(steps, list) else 0,
                    "created_at": plan.get("created_at"),
                }
            )
        return {"count": len(items), "items": items}

    @staticmethod
    def _mission_summaries(missions: list[dict[str, Any]]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        source_count = len(missions) if isinstance(missions, list) else -1
        if not isinstance(missions, list):
            missions = []
        for mission in missions:
            if not isinstance(mission, dict):
                continue
            mission_id = mission.get("mission_id")
            if not is_canonical_mission_id(mission_id):
                continue
            status = mission.get("status")
            provider = mission.get("provider")
            model = mission.get("model")
            step_count = mission.get("step_count")
            current_step = mission.get("current_step", 0)
            if (
                mission.get("schema_version") != MISSION_SCHEMA_VERSION
                or status not in MISSION_STATUSES
                or not isinstance(provider, str)
                or not isinstance(model, str)
                or not isinstance(step_count, int)
                or isinstance(step_count, bool)
                or not isinstance(current_step, int)
                or isinstance(current_step, bool)
                or current_step < 0
                or current_step > step_count
            ):
                continue
            selected_agents, invalid_agents = compact_mission_worker_entries(
                mission.get("selected_agents"), kind="selected_agents"
            )
            startup_actions, invalid_actions = compact_mission_worker_entries(
                mission.get("startup_actions"), kind="startup_actions"
            )
            selected_ids = [item["agent_id"] for item in selected_agents]
            action_ids = [item["agent_id"] for item in startup_actions]
            workers_ready = (
                not invalid_agents
                and not invalid_actions
                and len(selected_agents) >= 2
                and len(startup_actions) >= 2
                and selected_ids == action_ids
            )
            raw_can_start = mission.get("can_start") is True
            blockers, invalid_blockers = compact_mission_blockers(
                mission.get("blockers")
            )
            if invalid_blockers and MISSION_INVALID_BLOCKERS_BLOCKER not in blockers:
                blockers.append(MISSION_INVALID_BLOCKERS_BLOCKER)
            if (invalid_agents or invalid_actions or raw_can_start) and not workers_ready:
                if "invalid mission worker summaries" not in blockers:
                    blockers.append("invalid mission worker summaries")
            commands = mission_commands(mission_id)
            items.append(
                {
                    "mission_id": mission_id,
                    "schema_version": mission.get("schema_version"),
                    "user_message": mission.get("user_message"),
                    "status": status,
                    "stop_reason": mission.get("stop_reason"),
                    "can_start": raw_can_start and workers_ready and not blockers,
                    "can_resume": status in {MISSION_STATUSES[4], MISSION_STATUSES[5]} and not blockers,
                    "blockers": blockers,
                    "provider": provider,
                    "model": model,
                    "leader_backend": leader_backend_identity(provider, model),
                    "plan_id": mission.get("plan_id"),
                    "plan_hash": mission.get("plan_hash"),
                    "workflow_run_id": mission.get("workflow_run_id"),
                    "current_step": current_step,
                    "step_count": step_count,
                    "timeout_seconds": mission.get("timeout_seconds"),
                    "selected_agents": selected_agents,
                    "startup_actions": startup_actions,
                    "created_at": mission.get("created_at"),
                    "updated_at": mission.get("updated_at"),
                    "confirmed_at": mission.get("confirmed_at"),
                    "completed_at": mission.get("completed_at"),
                    **commands,
                }
            )
        return {
            "count": source_count,
            "by_status": StateStore._status_counts(items),
            "latest_id": items[-1]["mission_id"] if items else None,
            "items": items,
        }

    @staticmethod
    def _plan_skill_context(skill_context: Any) -> dict[str, Any]:
        if not isinstance(skill_context, dict):
            return {"count": 0, "by_agent": {}, "by_source": {}, "items": []}
        items = []
        for raw_item in skill_context.get("items", []):
            if not isinstance(raw_item, dict):
                continue
            items.append(
                {
                    "load_id": raw_item.get("load_id"),
                    "agent_id": raw_item.get("agent_id"),
                    "purpose": raw_item.get("purpose"),
                    "name": raw_item.get("name"),
                    "source": raw_item.get("source"),
                    "path": raw_item.get("path"),
                    "content_hash": raw_item.get("content_hash"),
                    "description": raw_item.get("description"),
                "required_tools": raw_item.get("required_tools")
                    if isinstance(raw_item.get("required_tools"), list)
                    else [],
                    "planning_guidance": raw_item.get("planning_guidance")
                    if isinstance(raw_item.get("planning_guidance"), list)
                    else [],
                    "risk": raw_item.get("risk"),
                    "created_at": raw_item.get("created_at"),
                    "show_command": raw_item.get("show_command"),
                    "reload_command": raw_item.get("reload_command"),
                }
            )
        by_agent = skill_context.get("by_agent") if isinstance(skill_context.get("by_agent"), dict) else {}
        by_source = skill_context.get("by_source") if isinstance(skill_context.get("by_source"), dict) else {}
        return {
            "count": len(items),
            "by_agent": dict(by_agent),
            "by_source": dict(by_source),
            "items": items,
        }

    def _approval_summaries(self, approvals: list[dict[str, Any]]) -> dict[str, Any]:
        counts = self._status_counts(approvals)
        items = [
            {
                "approval_id": approval.get("approval_id"),
                "plan_id": approval.get("plan_id"),
                "step_index": approval.get("step_index"),
                "agent_id": approval.get("agent_id"),
                "task": approval.get("task"),
                "status": approval.get("status"),
                "message_id": approval.get("message_id"),
                "job_id": approval.get("job_id"),
            }
            for approval in approvals
        ]
        return {
            "count": len(items),
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "dispatched": counts.get("dispatched", 0),
            "by_status": counts,
            "items": items,
        }

    def _message_summaries(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(messages),
            "by_status": self._status_counts(messages),
            "items": [
                {
                    "message_id": message.get("message_id"),
                    "from_actor": message.get("from_actor"),
                    "to_agent": message.get("to_agent"),
                    "task": message.get("task"),
                    "status": message.get("status"),
                    "created_at": message.get("created_at"),
                    "trace_command": self._trace_command(message.get("message_id")),
                    "prompt_skill_context": StateStore._plan_skill_context(message.get("prompt_skill_context")),
                }
                for message in messages
            ],
        }

    def _job_summaries(self, jobs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(jobs),
            "by_status": self._status_counts(jobs),
            "items": [
                {
                    "job_id": job.get("job_id"),
                    "message_id": job.get("message_id"),
                    "agent_id": job.get("agent_id"),
                    "status": job.get("status"),
                    "created_at": job.get("created_at"),
                    "trace_command": self._trace_command(job.get("job_id")),
                }
                for job in jobs
            ],
        }

    def _reply_summaries(self, replies: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(replies),
            "items": [
                {
                    "reply_id": reply.get("reply_id"),
                    "message_id": reply.get("message_id"),
                    "job_id": reply.get("job_id"),
                    "from_agent": reply.get("from_agent"),
                    "to_actor": reply.get("to_actor"),
                    "created_at": reply.get("created_at"),
                    "trace_command": self._trace_command(reply.get("reply_id")),
                }
                for reply in replies
            ],
        }

    def _release_summaries(self, releases: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(releases),
            "items": [
                {
                    "release_id": release.get("release_id"),
                    "round": release.get("round"),
                    "status": release.get("status"),
                    "review_gate_status": release.get("review_gate_status"),
                    "artifact_count": release.get("artifact_count"),
                    "review_reply_count": release.get("review_reply_count"),
                    "code_reviewer_id": release.get("code_reviewer_id"),
                    "round_reviewer_id": release.get("round_reviewer_id"),
                    "code_review_reply_id": release.get("code_review_reply_id"),
                    "round_review_reply_id": release.get("round_review_reply_id"),
                    "created_at": release.get("created_at"),
                    "trace_command": self._trace_command(release.get("round_review_reply_id")),
                }
                for release in releases
            ],
        }

    def artifact_summaries(self, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for artifact in artifacts:
            kind = str(artifact.get("kind", "unknown"))
            by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "count": len(artifacts),
            "by_status": self._status_counts(artifacts),
            "by_kind": by_kind,
            "items": [
                {
                    "artifact_id": artifact.get("artifact_id"),
                    "message_id": artifact.get("message_id"),
                    "job_id": artifact.get("job_id"),
                    "reply_id": artifact.get("reply_id"),
                    "from_agent": artifact.get("from_agent"),
                    "path": artifact.get("path"),
                    "kind": artifact.get("kind"),
                    "status": artifact.get("status"),
                    "created_at": artifact.get("created_at"),
                    "trace_command": self._trace_command(
                        artifact.get("message_id") or artifact.get("job_id") or artifact.get("reply_id")
                    ),
                }
                for artifact in artifacts
            ],
        }

    @staticmethod
    def _trace_command(trace_id: Any) -> str | None:
        if trace_id is None:
            return None
        return f"agentdeck trace --id {trace_id}"

    @staticmethod
    def _chat_turn_summaries(chat_turns: list[dict[str, Any]]) -> dict[str, Any]:
        by_mode: dict[str, int] = {}
        items = []
        for turn in chat_turns:
            mode = str(turn.get("mode", "unknown"))
            by_mode[mode] = by_mode.get(mode, 0) + 1
            items.append(
                {
                    "turn_id": turn.get("turn_id"),
                    "mode": turn.get("mode"),
                    "message": turn.get("message"),
                    "plan_id": turn.get("plan_id"),
                    "next_command": turn.get("next_command"),
                    "action_id": turn.get("action_id"),
                    "action_kind": turn.get("action_kind"),
                    "created_at": turn.get("created_at"),
                }
            )
        return {"count": len(items), "by_mode": by_mode, "items": items}

    @staticmethod
    def _leader_error_summaries(leader_errors: list[dict[str, Any]]) -> dict[str, Any]:
        by_mode: dict[str, int] = {}
        items = []
        for error in leader_errors:
            mode = str(error.get("mode", "unknown"))
            by_mode[mode] = by_mode.get(mode, 0) + 1
            items.append(
                {
                    "error_id": error.get("error_id"),
                    "mode": error.get("mode"),
                    "provider": error.get("provider"),
                    "model": error.get("model"),
                    "task": error.get("task"),
                    "error": error.get("error"),
                    "created_at": error.get("created_at"),
                }
            )
        return {"count": len(items), "by_mode": by_mode, "items": items}

    @staticmethod
    def _leader_action_summaries(leader_actions: list[dict[str, Any]]) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        by_status: dict[str, int] = {}
        pending_actions = [item for item in leader_actions if item.get("status") == "pending"]
        recommended_action_id = pending_actions[-1].get("action_id") if pending_actions else None
        items = []
        for action in leader_actions:
            kind = str(action.get("kind", "unknown"))
            status = str(action.get("status", "unknown"))
            by_kind[kind] = by_kind.get(kind, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
            items.append(
                {
                    "action_id": action.get("action_id"),
                    "kind": action.get("kind"),
                    "status": action.get("status"),
                    "requires_confirmation": action.get("requires_confirmation"),
                    "plan_id": action.get("plan_id"),
                    "approval_id": action.get("approval_id"),
                    "agent_id": action.get("agent_id"),
                    "message_id": action.get("message_id"),
                    "command": action.get("command"),
                    "reason": action.get("reason"),
                    **StateStore._leader_action_detail_fields(action),
                    "is_recommended": action.get("action_id") == recommended_action_id,
                    "created_at": action.get("created_at"),
                }
            )
        return {
            "count": len(items),
            "by_kind": by_kind,
            "by_status": by_status,
            "recommended_action_id": recommended_action_id,
            "items": items,
        }

    @staticmethod
    def _skill_load_summaries(skill_loads: list[dict[str, Any]]) -> dict[str, Any]:
        by_agent: dict[str, int] = {}
        by_source: dict[str, int] = {}
        items = []
        for load in skill_loads:
            agent_id = str(load.get("agent_id") or "unknown")
            source = str(load.get("source") or "unknown")
            name = str(load.get("name") or "")
            purpose = str(load.get("purpose") or "")
            by_agent[agent_id] = by_agent.get(agent_id, 0) + 1
            by_source[source] = by_source.get(source, 0) + 1
            item = {
                "load_id": load.get("load_id"),
                "agent_id": load.get("agent_id"),
                "purpose": load.get("purpose"),
                "name": load.get("name"),
                "source": load.get("source"),
                "path": load.get("path"),
                "content_hash": load.get("content_hash"),
                "description": load.get("description"),
                "required_tools": load.get("required_tools") if isinstance(load.get("required_tools"), list) else [],
                "planning_guidance": load.get("planning_guidance")
                if isinstance(load.get("planning_guidance"), list)
                else [],
                "risk": load.get("risk"),
                "created_at": load.get("created_at"),
                "show_command": f"agentdeck skills show --name {shlex.quote(name)}" if name else None,
                "reload_command": StateStore._skill_reload_command(name, agent_id, purpose),
            }
            items.append(item)
        return {
            "count": len(items),
            "by_agent": by_agent,
            "by_source": by_source,
            "items": items,
        }

    @staticmethod
    def _skill_reload_command(name: str, agent_id: str, purpose: str) -> str | None:
        if not name:
            return None
        command = f"agentdeck skills load --name {shlex.quote(name)} --agent {shlex.quote(agent_id)}"
        if purpose:
            command = f"{command} --purpose {shlex.quote(purpose)}"
        return command

    def _inbox_summary(self, inbox: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        by_agent = {agent_id: len(items) for agent_id, items in inbox.items()}
        all_items = [item for items in inbox.values() for item in items]
        return {
            "total": len(all_items),
            "by_agent": by_agent,
            "by_status": self._status_counts(all_items),
            "heads": {agent_id: self._inbox_head_summary(items) for agent_id, items in inbox.items()},
        }

    @staticmethod
    def _inbox_head_summary(items: list[dict[str, Any]]) -> dict[str, Any] | None:
        head = next((item for item in items if item.get("status") == "pending"), None)
        if head is None:
            return None
        return {
            "inbox_id": head.get("inbox_id"),
            "event_type": head.get("event_type"),
            "message_id": head.get("message_id"),
            "reply_id": head.get("reply_id"),
            "from_actor": head.get("from_actor"),
            "from_agent": head.get("from_agent"),
            "to_agent": head.get("to_agent"),
            "task": head.get("task"),
            "status": head.get("status"),
            "created_at": head.get("created_at"),
        }

    def _recovery_summary(self, state: dict[str, Any], config: ProjectConfig) -> dict[str, Any]:
        approvals = state.get("approvals", [])
        leader_actions = state.get("leader_actions", [])
        leader_errors = state.get("leader_errors", [])
        agent_bindings = state.get("agents", {})
        inbox_items = [item for items in state.get("inbox", {}).values() for item in items]
        pending_leader_actions = [item for item in leader_actions if item.get("status") == "pending"]
        pending_approvals = [item for item in approvals if item.get("status") == "pending"]
        approved_approvals = [item for item in approvals if item.get("status") == "approved"]
        pending_inbox_items = [item for item in inbox_items if item.get("status") == "pending"]
        waiting_reply_review = self._latest_waiting_reply_review(state)
        stale_agents = [
            agent_id
            for agent_id, binding in agent_bindings.items()
            if isinstance(binding, dict) and binding.get("status") == "stale"
        ]
        recent_events = [self._event_summary(event) for event in self.list_events(5)]
        summary = {
            "status": "idle",
            "reason": "no pending recovery action",
            "next_command": None,
            "recommended_action": None,
            "pending": {
                "leader_actions": len(pending_leader_actions),
                "approvals": len(pending_approvals),
                "approved_approvals": len(approved_approvals),
                "inbox_items": len(pending_inbox_items),
                "leader_errors": len(leader_errors),
                "runtime_stale": len(stale_agents),
                "reply_waiting": 1 if waiting_reply_review else 0,
            },
            "leader_action": None,
            "latest_event": recent_events[-1] if recent_events else None,
            "recent_events": recent_events,
        }
        if pending_leader_actions:
            action = pending_leader_actions[-1]
            detail = self._leader_action_detail_fields(action)
            next_command = detail.get("apply_command") or action.get("command")
            summary.update(
                {
                    "status": "action_required",
                    "reason": f"pending leader action: {action.get('kind')}",
                    "next_command": next_command,
                    "recommended_action": self._recommended_action(
                        label="Apply safe Leader action" if detail.get("can_apply") else "Run explicit Leader action",
                        command=next_command,
                        safety="safe_apply" if detail.get("can_apply") else "explicit_runtime",
                        requires_explicit_user=not bool(detail.get("can_apply")),
                        source="leader_action",
                        target_id=action.get("action_id"),
                    ),
                    "leader_action": {
                        "action_id": action.get("action_id"),
                        "kind": action.get("kind"),
                        "command": action.get("command"),
                        "can_apply": detail.get("can_apply"),
                        "apply_command": detail.get("apply_command"),
                        "apply_blocker": detail.get("apply_blocker"),
                    },
                }
            )
        elif approved_approvals:
            approval_id = approved_approvals[0].get("approval_id")
            next_command = f"agentdeck approval dispatch --approval-id {approval_id}"
            summary.update(
                {
                    "status": "dispatch_ready",
                    "reason": "approved approval is waiting for dispatch",
                    "next_command": next_command,
                    "recommended_action": self._recommended_action(
                        label="Dispatch approved task",
                        command=next_command,
                        safety="explicit_runtime",
                        requires_explicit_user=True,
                        source="approval",
                        target_id=approval_id,
                    ),
                }
            )
        elif pending_approvals:
            summary.update(
                {
                    "status": "approval_required",
                    "reason": "pending approvals require human decision",
                    "next_command": "agentdeck approval list",
                    "recommended_action": self._recommended_action(
                        label="Review approvals",
                        command="agentdeck approval list",
                        safety="inspect",
                        requires_explicit_user=False,
                        source="approval",
                        target_id=pending_approvals[0].get("approval_id"),
                    ),
                }
            )
        elif stale_agents:
            stale_agent_id = stale_agents[0]
            summary.update(
                {
                    "status": "runtime_stale",
                    "reason": "agent runtime binding is stale",
                    "next_command": "agentdeck agent refresh",
                    "recommended_action": self._recommended_action(
                        label="Refresh stale runtime",
                        command="agentdeck agent refresh",
                        safety="inspect",
                        requires_explicit_user=False,
                        source="runtime",
                        target_id=stale_agent_id,
                    ),
                }
            )
        elif pending_inbox_items:
            inbox_item = pending_inbox_items[0]
            agent_id = self._inbox_item_agent_id(state.get("inbox", {}), inbox_item)
            next_command = f"agentdeck inbox --agent {agent_id}" if agent_id else "agentdeck status"
            summary.update(
                {
                    "status": "inbox_pending",
                    "reason": "agent inbox has pending items",
                    "next_command": next_command,
                    "recommended_action": self._recommended_action(
                        label="Inspect pending inbox",
                        command=next_command,
                        safety="inspect",
                        requires_explicit_user=False,
                        source="inbox",
                        target_id=inbox_item.get("inbox_id"),
                    ),
                }
            )
        elif waiting_reply_review:
            agent_id = waiting_reply_review.get("agent_id")
            message_id = waiting_reply_review.get("message_id")
            next_command = f"agentdeck capture-reply --agent {agent_id} --message-id {message_id}"
            summary.update(
                {
                    "status": "reply_waiting",
                    "reason": waiting_reply_review.get("reason"),
                    "next_command": next_command,
                    "recommended_action": self._recommended_action(
                        label="Capture pending reply",
                        command=next_command,
                        safety="explicit_runtime",
                        requires_explicit_user=True,
                        source="reply",
                        target_id=message_id,
                    ),
                }
            )
        elif leader_errors:
            error = leader_errors[-1]
            summary.update(
                {
                    "status": "leader_error",
                    "reason": "leader error requires inspection",
                    "next_command": "agentdeck status",
                    "recommended_action": self._recommended_action(
                        label="Inspect Leader error",
                        command="agentdeck status",
                        safety="inspect",
                        requires_explicit_user=False,
                        source="leader_error",
                        target_id=error.get("error_id"),
                    ),
                }
            )
        elif provider_setup := self._leader_provider_setup_action(config):
            summary.update(
                {
                    "status": "provider_setup_required",
                    "reason": f"configured Leader provider is not ready: {config.leader.provider}",
                    "next_command": provider_setup["command"],
                    "recommended_action": self._recommended_action(
                        label="Inspect Leader provider setup",
                        command=provider_setup["command"],
                        safety="inspect",
                        requires_explicit_user=False,
                        source="provider_health",
                        target_id=config.leader.provider,
                    ),
                }
            )
        return summary

    @staticmethod
    def _leader_provider_setup_action(config: ProjectConfig) -> dict[str, Any] | None:
        required_env = {
            "deepseek": "DEEPSEEK_API_KEY",
            "openai-compatible": "AGENTDECK_LEADER_API_KEY",
        }.get(config.leader.provider)
        cli_command = {
            "codex-cli": "codex",
            "claude-cli": "claude",
        }.get(config.leader.provider)
        if cli_command is not None:
            if shutil.which(cli_command):
                return None
            return {"command": "agentdeck doctor", "missing_command": cli_command}
        if required_env is None or os.environ.get(required_env):
            return None
        return {"command": "agentdeck doctor", "missing_env": required_env}

    def _latest_waiting_reply_review(self, state: dict[str, Any]) -> dict[str, Any] | None:
        plans = state.get("plans", [])
        if not plans:
            return None
        latest_plan_id = plans[-1].get("plan_id") if isinstance(plans[-1], dict) else None
        if not latest_plan_id:
            return None
        try:
            review = self.leader_review(str(latest_plan_id))
        except KeyError:
            return None
        if review.get("next_action") != "wait_for_reply":
            return None
        if not review.get("agent_id") or not review.get("message_id"):
            return None
        return review

    @staticmethod
    def _inbox_item_agent_id(inbox: dict[str, list[dict[str, Any]]], item: dict[str, Any]) -> str | None:
        to_agent = item.get("to_agent")
        if to_agent:
            return str(to_agent)
        inbox_id = item.get("inbox_id")
        for agent_id, items in inbox.items():
            if any(candidate is item or candidate.get("inbox_id") == inbox_id for candidate in items):
                return str(agent_id)
        return None

    @staticmethod
    def _recommended_action(
        label: str,
        command: object,
        safety: str,
        requires_explicit_user: bool,
        source: str,
        target_id: object,
    ) -> dict[str, Any] | None:
        if not command:
            return None
        return {
            "label": label,
            "command": command,
            "safety": safety,
            "requires_explicit_user": requires_explicit_user,
            "source": source,
            "target_id": target_id,
        }

    @staticmethod
    def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "created_at": event.get("created_at"),
        }

    @staticmethod
    def _memory_context_summary(root: Path) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        by_scope: dict[str, int] = {}
        for scope, relative_path in (
            ("project", ".agentdeck/memory/project.md"),
            ("global", ".agentdeck/memory/global.md"),
        ):
            path = root / relative_path
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            encoded = text.encode("utf-8")
            preview = next((line for line in text.splitlines() if line.strip()), "")
            items.append(
                {
                    "scope": scope,
                    "path": relative_path,
                    "exists": True,
                    "line_count": len(text.splitlines()),
                    "byte_count": len(encoded),
                    "content_hash": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
                    "preview": preview,
                }
            )
            by_scope[scope] = by_scope.get(scope, 0) + 1
        return {"count": len(items), "by_scope": by_scope, "items": items}

    @staticmethod
    def _conversation_summary(state: dict[str, Any]) -> dict[str, Any]:
        collections = {
            key: state.get(key, [])
            for key in (
                "conversation_sessions",
                "conversation_turns",
                "conversation_preview_bindings",
            )
        }
        transitions = state.get("conversation_state_transitions", [])
        projection = validate_conversation_history(collections, transitions)
        sessions = collections["conversation_sessions"]
        turns = collections["conversation_turns"]
        previews = collections["conversation_preview_bindings"]
        latest_session = sessions[-1] if sessions else None
        latest_turn = turns[-1] if turns else None
        latest_conversation_id = (
            latest_session.get("conversation_id")
            if isinstance(latest_session, dict)
            else None
        )
        latest_turn_id = (
            latest_turn.get("turn_id") if isinstance(latest_turn, dict) else None
        )
        pending_preview_id = (
            projection["pending_preview_by_conversation"].get(latest_conversation_id)
            if latest_conversation_id is not None
            else None
        )
        pending_preview = next(
            (
                {
                    "preview_id": item.get("preview_id"),
                    "preview_kind": item.get("preview_kind"),
                    "expires_at": item.get("expires_at"),
                }
                for item in previews
                if isinstance(item, dict) and item.get("preview_id") == pending_preview_id
            ),
            None,
        )
        ownership = [
            {"agent_id": agent_id, "state": ownership_state}
            for agent_id, ownership_state in sorted(
                projection["ownership_states"].items()
            )
        ]
        outbox = state.get("conversation_event_outbox", [])
        outbox_count = len(outbox) if isinstance(outbox, list) else 0
        return {
            "session_count": len(sessions),
            "turn_count": len(turns),
            "preview_count": len(previews),
            "transition_count": len(transitions),
            "latest_conversation_id": latest_conversation_id,
            "latest_conversation_state": projection["conversation_states"].get(
                latest_conversation_id
            ),
            "latest_turn_id": latest_turn_id,
            "latest_turn_state": projection["turn_states"].get(latest_turn_id),
            "pending_preview": pending_preview,
            "ownership": ownership,
            "outbox_count": outbox_count,
            "blockers": ["conversation event outbox pending"] if outbox_count else [],
        }

    def project_view(self, config: ProjectConfig) -> ProjectView:
        state = self.load()
        # Preserve source-row validation precedence before cross-record lineage
        # checks, then validate every transition before deriving current state.
        self._agent_session_summaries(state.get("agent_sessions", []))
        self._protocol_turn_summaries(state.get("protocol_turns", []))
        self._transport_update_summaries(state.get("transport_updates", []))
        self._permission_request_summaries(state.get("permission_requests", []))
        self._validate_protocol_lineage(state)
        current_states = self._validate_protocol_transition_history(state)
        sessions = copy.deepcopy(state.get("agent_sessions", []))
        turns = copy.deepcopy(state.get("protocol_turns", []))
        permissions = copy.deepcopy(state.get("permission_requests", []))
        for entity_type, records, identity_field, state_field in (
            ("session", sessions, "session_id", "state"),
            ("turn", turns, "turn_id", "state"),
            ("permission", permissions, "permission_id", "status"),
        ):
            for record in records:
                identity = record.get(identity_field)
                record[state_field] = current_states.get(
                    (entity_type, identity), record.get(state_field)
                )
        agent_sessions = self._agent_session_summaries(sessions)
        protocol_turns = self._protocol_turn_summaries(turns)
        transport_updates = self._transport_update_summaries(state.get("transport_updates", []))
        permission_requests = self._permission_request_summaries(permissions)
        protocol_state_transitions = self._protocol_transition_summaries(
            state.get("protocol_state_transitions", [])
        )
        bindings = state.get("agents", {})
        agents = []
        for agent in config.agents:
            binding = bindings.get(
                agent.agent_id,
                {
                    "agent_id": agent.agent_id,
                    "pane_id": None,
                    "session_name": None,
                    "cwd": None,
                    "status": "configured",
                },
            )
            agents.append(
                {
                    "agent_id": agent.agent_id,
                    "role": agent.role,
                    "provider": agent.provider,
                    "command": agent.command,
                    "workspace_mode": agent.workspace_mode,
                    "role_prompt": agent.role_prompt,
                    "runtime": binding,
                }
            )
        leader = {
            "agent_id": config.leader.agent_id,
            "provider": config.leader.provider,
            "model": config.leader.model,
            "approval_mode": config.leader.approval_mode,
        }
        leader["leader_backend"] = leader_backend_identity(config.leader.provider, config.leader.model)
        leader["coordination_roles"] = leader_coordination_roles(config.leader.provider, config.leader.model)
        daemon_record = state.get("daemon_runtime")
        daemon_state = (
            str(daemon_record.get("state"))
            if isinstance(daemon_record, dict)
            else "stopped"
        )
        daemon_blockers = [] if daemon_state in {"ready", "busy", "idle_grace"} else ["project daemon is not running"]
        daemon_summary = {
            "state": daemon_state,
            "health": "unknown" if daemon_state in {"ready", "busy", "idle_grace"} else "unavailable",
            "client_count": 0,
            "controller_present": controller_lease_is_active(
                state.get("controller_lease"), now=datetime.now(timezone.utc)
            ),
            "idle_exit_pending": daemon_state == "idle_grace",
            "protocol_version": "daemon-rpc/v1",
            "compatibility": "unverified",
            "blockers": (
                ["daemon health requires endpoint verification"]
                if daemon_state in {"ready", "busy", "idle_grace"}
                else daemon_blockers
            ),
        }
        scheduler_summary = {
            "state": "inactive",
            "active_mission_id": None,
            "active_step": None,
            "next_transition": None,
            "blockers": ["background Mission scheduling is not implemented in M2a"],
        }
        return ProjectView(
            schema_version=PROJECT_VIEW_SCHEMA_VERSION,
            project=config.name,
            root=config.root,
            runtime_backend=config.runtime.backend,
            leader=leader,
            agents=agents,
            state_path=str(self.state_path),
            missions=self._mission_summaries(state.get("missions", [])),
            plans=self._plan_summaries(state.get("plans", [])),
            approvals=self._approval_summaries(state.get("approvals", [])),
            messages=self._message_summaries(state.get("messages", [])),
            jobs=self._job_summaries(state.get("jobs", [])),
            replies=self._reply_summaries(state.get("replies", [])),
            artifacts=self.artifact_summaries(state.get("artifacts", [])),
            releases=self._release_summaries(state.get("releases", [])),
            chat_turns=self._chat_turn_summaries(state.get("chat_turns", [])),
            leader_errors=self._leader_error_summaries(state.get("leader_errors", [])),
            leader_actions=self._leader_action_summaries(state.get("leader_actions", [])),
            skills=self._skill_load_summaries(state.get("skill_loads", [])),
            memory=self._memory_context_summary(self.root),
            agent_sessions=agent_sessions,
            protocol_turns=protocol_turns,
            transport_updates=transport_updates,
            permission_requests=permission_requests,
            protocol_state_transitions=protocol_state_transitions,
            conversation=self._conversation_summary(state),
            inbox=self._inbox_summary(state.get("inbox", {})),
            recovery=self._recovery_summary(state, config),
            daemon=daemon_summary,
            scheduler=scheduler_summary,
        )


def agentdeck_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / CONFIG_DIR
