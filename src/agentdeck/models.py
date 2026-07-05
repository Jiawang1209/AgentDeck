from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4


PROJECT_VIEW_SCHEMA_VERSION = "project-view/v1"


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


@dataclass(frozen=True)
class RuntimeConfig:
    backend: str = "tmux"
    session_name: str = "agentdeck"
    socket_name: str = "agentdeck-local"


@dataclass(frozen=True)
class LeaderConfig:
    agent_id: str = "leader"
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    approval_mode: str = "confirm"


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    root: str
    leader: LeaderConfig
    agents: tuple[AgentSpec, ...]
    runtime: RuntimeConfig


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
    plans: dict[str, Any] = field(default_factory=dict)
    approvals: dict[str, Any] = field(default_factory=dict)
    messages: dict[str, Any] = field(default_factory=dict)
    jobs: dict[str, Any] = field(default_factory=dict)
    replies: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    chat_turns: dict[str, Any] = field(default_factory=dict)
    leader_errors: dict[str, Any] = field(default_factory=dict)
    leader_actions: dict[str, Any] = field(default_factory=dict)
    inbox: dict[str, Any] = field(default_factory=dict)
    recovery: dict[str, Any] = field(default_factory=dict)
