from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
from typing import Any

from .config import CONFIG_DIR, ensure_project_layout, project_root
from .models import PROJECT_VIEW_SCHEMA_VERSION, AgentRuntimeBinding, EventRecord, ProjectConfig, ProjectView, new_id, utc_now


class StateStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or project_root()
        self.deck_dir = ensure_project_layout(self.root)
        self.state_path = self.deck_dir / "state" / "state.json"
        self.events_path = self.deck_dir / "state" / "events.jsonl"

    def load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "agents": {},
                "messages": [],
                "jobs": [],
                "replies": [],
                "artifacts": [],
                "plans": [],
                "approvals": [],
                "chat_turns": [],
                "leader_errors": [],
                "leader_actions": [],
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

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events = [
            json.loads(line)
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if limit <= 0:
            return []
        return events[-limit:]

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

    def list_plans(self) -> list[dict[str, Any]]:
        return list(self.load().get("plans", []))

    def plan_by_id(self, plan_id: str) -> dict[str, Any]:
        for plan in self.load().get("plans", []):
            if plan.get("plan_id") == plan_id:
                return plan
        raise KeyError(plan_id)

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
            "model": plan_record.get("model"),
            "created_at": plan_record.get("created_at"),
            "counts": status_counts,
            "steps": status_steps,
        }

    def leader_review(self, plan_id: str) -> dict[str, Any]:
        status = self.plan_status(plan_id)
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
            }
        if completed_replies:
            return {
                "plan_id": plan_id,
                "next_action": "summarize",
                "reason": "all dispatched steps have replies",
                "replies": completed_replies,
                "counts": status["counts"],
            }
        return {
            "plan_id": plan_id,
            "next_action": "wait_for_approval",
            "reason": "no approved or dispatched steps are ready",
            "counts": status["counts"],
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
            command = f"agentdeck plan status --plan-id {review['plan_id']}"
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
            "attempts": [self._trace_attempt(item) for item in attempts],
            "jobs": [self._trace_job(item) for item in jobs],
            "replies": [self._trace_reply(item) for item in replies],
            "artifacts": [self._trace_artifact(item) for item in artifacts],
            "inbox_items": [self._trace_inbox_item(item) for item in inbox_items],
        }

    @staticmethod
    def _trace_message(message: dict[str, Any]) -> dict[str, Any]:
        return {
            "message_id": message.get("message_id"),
            "from_actor": message.get("from_actor"),
            "to_agent": message.get("to_agent"),
            "task": message.get("task"),
            "prompt": message.get("prompt"),
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
                    "model": plan.get("model"),
                    "dispatch_ready": plan.get("dispatch_ready"),
                    "step_count": len(steps) if isinstance(steps, list) else 0,
                    "created_at": plan.get("created_at"),
                }
            )
        return {"count": len(items), "items": items}

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
            schema_version=PROJECT_VIEW_SCHEMA_VERSION,
            project=config.name,
            root=config.root,
            runtime_backend=config.runtime.backend,
            leader=asdict(config.leader),
            agents=agents,
            state_path=str(self.state_path),
            plans=self._plan_summaries(state.get("plans", [])),
            approvals=self._approval_summaries(state.get("approvals", [])),
            messages=self._message_summaries(state.get("messages", [])),
            jobs=self._job_summaries(state.get("jobs", [])),
            replies=self._reply_summaries(state.get("replies", [])),
            artifacts=self.artifact_summaries(state.get("artifacts", [])),
            chat_turns=self._chat_turn_summaries(state.get("chat_turns", [])),
            leader_errors=self._leader_error_summaries(state.get("leader_errors", [])),
            leader_actions=self._leader_action_summaries(state.get("leader_actions", [])),
            inbox=self._inbox_summary(state.get("inbox", {})),
            recovery=self._recovery_summary(state, config),
        )


def agentdeck_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / CONFIG_DIR
