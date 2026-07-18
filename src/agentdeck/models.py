from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4


PROJECT_VIEW_SCHEMA_VERSION = "project-view/v1"
PROJECT_VIEW_V2_SCHEMA_VERSION = "project-view/v2"
MIGRATION_SCHEMA_VERSION = "migration/v1"
PROJECT_VIEW_SEMANTIC_AUTHORITY_FIELDS = (
    "schema_version",
    "state",
    "authority_hash",
    "requirement_count",
    "proposed_effect_count",
    "unresolved_count",
    "compiled_step_count",
    "blockers",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    role: str
    provider: str
    command: str
    workspace_mode: str = "shared"
    role_prompt: str = ""
    transport: str = "tmux"
    transport_command: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeConfig:
    backend: str = "tmux"
    session_name: str = "agentdeck"
    socket_name: str = "agentdeck-local"


@dataclass(frozen=True)
class DaemonConfig:
    idle_grace_seconds: int = 600
    start_timeout_seconds: int = 10
    controller_ttl_seconds: int = 30
    max_frame_bytes: int = 1024 * 1024


@dataclass(frozen=True)
class LeaderConfig:
    agent_id: str = "leader"
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    approval_mode: str = "confirm"
    backend_kind: str | None = None
    transport: str | None = None
    transport_command: tuple[str, ...] = ()


@dataclass(frozen=True)
class AutonomousPolicy:
    allowed_agents: tuple[str, ...] = ()
    max_approvals: int = 0


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    root: str
    leader: LeaderConfig
    agents: tuple[AgentSpec, ...]
    runtime: RuntimeConfig
    daemon: DaemonConfig = DaemonConfig()
    autonomous: AutonomousPolicy = AutonomousPolicy()
    skills: dict[str, Any] = field(default_factory=dict)


@dataclass
class EventRecord:
    event_id: str
    event_type: str
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, event_type: str, payload: dict[str, Any] | None = None) -> "EventRecord":
        return cls(
            event_id=new_id("evt"),
            event_type=event_type,
            created_at=utc_now(),
            payload=payload or {},
        )


@dataclass
class AgentRuntimeBinding:
    agent_id: str
    pane_id: str | None = None
    session_name: str | None = None
    cwd: str | None = None
    status: Literal["configured", "running", "stopped", "unknown"] = "configured"


@dataclass
class ProjectView:
    schema_version: str
    project: str
    root: str
    runtime_backend: str
    leader: dict[str, Any]
    agents: list[dict[str, Any]]
    state_path: str
    missions: dict[str, Any] = field(default_factory=dict)
    plans: dict[str, Any] = field(default_factory=dict)
    approvals: dict[str, Any] = field(default_factory=dict)
    messages: dict[str, Any] = field(default_factory=dict)
    jobs: dict[str, Any] = field(default_factory=dict)
    replies: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    releases: dict[str, Any] = field(default_factory=dict)
    chat_turns: dict[str, Any] = field(default_factory=dict)
    leader_errors: dict[str, Any] = field(default_factory=dict)
    leader_actions: dict[str, Any] = field(default_factory=dict)
    skills: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    agent_sessions: dict[str, Any] = field(default_factory=dict)
    protocol_turns: dict[str, Any] = field(default_factory=dict)
    transport_updates: dict[str, Any] = field(default_factory=dict)
    permission_requests: dict[str, Any] = field(default_factory=dict)
    protocol_state_transitions: dict[str, Any] = field(default_factory=dict)
    conversation: dict[str, Any] = field(default_factory=dict)
    inbox: dict[str, Any] = field(default_factory=dict)
    recovery: dict[str, Any] = field(default_factory=dict)
    mission_recovery: dict[str, Any] = field(default_factory=dict)
    daemon: dict[str, Any] = field(default_factory=dict)
    scheduler: dict[str, Any] = field(default_factory=dict)
