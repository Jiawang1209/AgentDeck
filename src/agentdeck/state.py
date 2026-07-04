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
            return {"agents": {}, "messages": [], "jobs": [], "replies": [], "plans": []}
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

    def record_plan(
        self,
        task: str,
        provider: str,
        model: str,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        state = self.load()
        record = {
            "plan_id": new_id("pln"),
            "task": task,
            "provider": provider,
            "model": model,
            "status": "planned",
            "dispatch_ready": bool(plan.get("dispatch_ready", False)),
            "plan": plan,
            "created_at": utc_now(),
        }
        state.setdefault("plans", []).append(record)
        self.save(state)
        return record

    def create_dispatch_records(
        self,
        from_actor: str,
        to_agent: str,
        task: str,
        prompt: str,
        pane_id: str,
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
        return reply

    def ack_inbox_item(self, agent_id: str, inbox_id: str) -> dict[str, Any]:
        state = self.load()
        items = state.setdefault("inbox", {}).setdefault(agent_id, [])
        item = next((entry for entry in items if entry.get("inbox_id") == inbox_id), None)
        if item is None:
            raise KeyError(inbox_id)
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
        inbox_items = []
        for items in state.get("inbox", {}).values():
            inbox_items.extend(item for item in items if item.get("message_id") == message_id)
        return {
            "query_id": query_id,
            "message": message,
            "attempts": attempts,
            "jobs": jobs,
            "replies": replies,
            "inbox_items": inbox_items,
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
        return None

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
