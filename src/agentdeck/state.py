from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from .config import CONFIG_DIR, ensure_project_layout, project_root
from .models import AgentRuntimeBinding, EventRecord, ProjectConfig, ProjectView, new_id, utc_now


class StateStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or project_root()
        self.deck_dir = ensure_project_layout(self.root)
        self.state_path = self.deck_dir / "state" / "state.json"
        self.events_path = self.deck_dir / "state" / "events.jsonl"

    def load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"agents": {}, "messages": [], "jobs": [], "replies": []}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def append_event(self, event: EventRecord) -> None:
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True) + "\n")

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

    def project_view(self, config: ProjectConfig) -> ProjectView:
        state = self.load()
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
        return ProjectView(
            project=config.name,
            root=config.root,
            runtime_backend=config.runtime.backend,
            leader=asdict(config.leader),
            agents=agents,
            state_path=str(self.state_path),
        )


def agentdeck_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / CONFIG_DIR
