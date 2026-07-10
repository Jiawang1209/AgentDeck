from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from .models import EventRecord, ProjectConfig, utc_now
from .runtime.base import RuntimeBackend
from .state import StateStore


REPLY_FIELDS = (
    "handoff_token",
    "status",
    "summary",
    "verification",
    "risks",
    "next_steps",
)
REPLY_STATUSES = {"completed", "blocked", "failed"}


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def authorized_steps(plan_record: dict[str, Any]) -> list[dict[str, Any]]:
    plan = plan_record.get("plan") if isinstance(plan_record.get("plan"), dict) else {}
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    result = []
    for item in steps:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task") or "")
        result.append(
            {
                "step": int(item.get("step") or 0),
                "agent_id": str(item.get("agent_id") or ""),
                "role": str(item.get("role") or ""),
                "task": task,
                "task_hash": _sha256_text(task),
            }
        )
    return result


def workflow_plan_hash(plan_record: dict[str, Any]) -> str:
    canonical = {
        "plan_id": str(plan_record.get("plan_id") or ""),
        "steps": authorized_steps(plan_record),
    }
    return _sha256_text(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _reply_blocks(output: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        for tui_prefix in ("• ", "› ", "⏺ "):
            if line.startswith(tui_prefix):
                line = line.removeprefix(tui_prefix).lstrip()
                break
        if line.startswith("handoff_token:"):
            if current is not None:
                blocks.append(current)
            current = {"handoff_token": line.split(":", 1)[1].strip()}
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {*REPLY_FIELDS, "full_output_path"}:
            current[key] = value.strip()
    if current is not None:
        blocks.append(current)
    return blocks


def parse_correlated_reply(output: str, token: str) -> dict[str, str] | None:
    matching = [
        item for item in _reply_blocks(output) if item.get("handoff_token") == token
    ]
    if not matching:
        return None
    reply = matching[-1]
    for field in REPLY_FIELDS:
        if not reply.get(field):
            return None
    if reply["status"] not in REPLY_STATUSES:
        raise ValueError(f"invalid workflow reply status: {reply['status']}")
    return reply


def build_compact_handoff(
    *,
    step: int,
    agent_id: str,
    reply: dict[str, str],
    reply_id: str,
    artifact_paths: list[str],
) -> dict[str, Any]:
    return {
        "step": step,
        "agent_id": agent_id,
        "status": reply["status"],
        "summary": reply["summary"],
        "verification": reply["verification"],
        "risks": reply["risks"],
        "next_steps": reply["next_steps"],
        "artifact_paths": list(artifact_paths),
        "trace_command": f"agentdeck trace --id {reply_id}",
    }


def build_workflow_prompt(
    *,
    role: str,
    role_prompt: str,
    task: str,
    handoff_token: str,
    previous_handoff: dict[str, Any] | None,
) -> str:
    handoff = (
        json.dumps(previous_handoff, ensure_ascii=False, sort_keys=True, indent=2)
        if previous_handoff is not None
        else "none"
    )
    return (
        "You are executing one explicitly authorized AgentDeck sequential workflow step.\n"
        f"Role: {role}\n"
        f"Role instructions: {role_prompt}\n"
        f"Task: {task}\n"
        "Previous compact handoff:\n"
        f"{handoff}\n\n"
        "Complete only this task. "
        f"Use this handoff token exactly: {handoff_token}\n"
        "Return exactly one structured block:\n"
        "handoff_token: <provided token>\n"
        "status: completed | blocked | failed\n"
        "summary: <text>\n"
        "verification: <text>\n"
        "risks: <text>\n"
        "next_steps: <text>\n"
        "full_output_path: <optional path>"
    )


def _structured_reply_text(reply: dict[str, str]) -> str:
    fields = [*REPLY_FIELDS]
    if reply.get("full_output_path"):
        fields.append("full_output_path")
    return "\n".join(f"{field}: {reply[field]}" for field in fields)


def _stop_workflow(
    store: StateStore,
    *,
    run_id: str,
    turns: list[dict[str, Any]],
    turn: dict[str, Any],
    turn_status: str,
    reason: str,
) -> dict[str, Any]:
    turn["status"] = turn_status
    turn["completed_at"] = utc_now()
    record = store.update_workflow_run(
        run_id,
        status="stopped",
        turns=turns,
        stop_reason=reason,
    )
    store.append_event(
        EventRecord.create(
            "workflow_stopped",
            {
                "run_id": run_id,
                "plan_id": record.get("plan_id"),
                "step": turn.get("step"),
                "agent_id": turn.get("agent_id"),
                "reason": reason,
            },
        )
    )
    return record


def run_sequential_workflow(
    *,
    config: ProjectConfig,
    store: StateStore,
    backend: RuntimeBackend,
    run_id: str,
    poll_interval: float = 0.25,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    record = store.workflow_run_by_id(run_id)
    steps = list(record.get("authorized_steps") or [])
    turns = list(record.get("turns") or [])
    agents = {agent.agent_id: agent for agent in config.agents}
    timeout_seconds = int(record.get("timeout_seconds") or 0)
    previous_handoff = next(
        (
            turn.get("handoff")
            for turn in reversed(turns)
            if turn.get("status") == "completed" and isinstance(turn.get("handoff"), dict)
        ),
        None,
    )

    for step in steps:
        step_number = int(step.get("step") or 0)
        existing = next((turn for turn in turns if turn.get("step") == step_number), None)
        if existing is not None and existing.get("status") == "completed":
            if isinstance(existing.get("handoff"), dict):
                previous_handoff = existing["handoff"]
            continue

        agent_id = str(step.get("agent_id") or "")
        agent = agents.get(agent_id)
        binding = store.agent_binding(agent_id)
        if agent is None or not binding or binding.get("status") != "running" or not binding.get("pane_id"):
            turn = existing or {
                "step": step_number,
                "agent_id": agent_id,
                "handoff_token": f"{run_id}_step_{step_number}",
                "status": "pending",
                "message_id": None,
                "job_id": None,
                "reply_id": None,
                "handoff": None,
                "artifact_paths": [],
                "trace_command": None,
                "started_at": utc_now(),
                "completed_at": None,
            }
            if existing is None:
                turns.append(turn)
            return _stop_workflow(
                store,
                run_id=run_id,
                turns=turns,
                turn=turn,
                turn_status="failed",
                reason="agent_unavailable",
            )
        pane_id = str(binding["pane_id"])
        if not backend.pane_exists(config.runtime, pane_id):
            turn = existing or {
                "step": step_number,
                "agent_id": agent_id,
                "handoff_token": f"{run_id}_step_{step_number}",
                "status": "pending",
                "message_id": None,
                "job_id": None,
                "reply_id": None,
                "handoff": None,
                "artifact_paths": [],
                "trace_command": None,
                "started_at": utc_now(),
                "completed_at": None,
            }
            if existing is None:
                turns.append(turn)
            return _stop_workflow(
                store,
                run_id=run_id,
                turns=turns,
                turn=turn,
                turn_status="failed",
                reason="pane_lost",
            )

        if existing is None:
            token = f"{run_id}_step_{step_number}"
            prompt = build_workflow_prompt(
                role=str(step.get("role") or agent.role),
                role_prompt=agent.role_prompt,
                task=str(step.get("task") or ""),
                handoff_token=token,
                previous_handoff=previous_handoff,
            )
            dispatch = store.create_dispatch_records(
                "leader", agent_id, str(step.get("task") or ""), prompt, pane_id
            )
            turn = {
                "step": step_number,
                "agent_id": agent_id,
                "handoff_token": token,
                "status": "dispatched",
                "message_id": dispatch["message"]["message_id"],
                "job_id": dispatch["job"]["job_id"],
                "reply_id": None,
                "handoff": None,
                "artifact_paths": [],
                "trace_command": f"agentdeck trace --id {dispatch['message']['message_id']}",
                "started_at": utc_now(),
                "completed_at": None,
            }
            turns.append(turn)
            store.update_workflow_run(
                run_id,
                status="running",
                current_step=step_number,
                turns=turns,
                stop_reason=None,
            )
            try:
                backend.send_input(config.runtime, pane_id, prompt)
            except Exception:
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="failed",
                    reason="pane_lost",
                )
            store.append_event(
                EventRecord.create(
                    "workflow_step_dispatched",
                    {
                        "run_id": run_id,
                        "step": step_number,
                        "agent_id": agent_id,
                        "message_id": turn["message_id"],
                        "pane_id": pane_id,
                    },
                )
            )
        else:
            turn = existing
            token = str(turn.get("handoff_token") or "")

        started = monotonic()
        while True:
            if not backend.pane_exists(config.runtime, pane_id):
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="failed",
                    reason="pane_lost",
                )
            output = backend.capture_output(config.runtime, pane_id, lines=400)
            try:
                reply = parse_correlated_reply(output, token)
            except ValueError:
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="failed",
                    reason="invalid_reply",
                )
            if reply is not None:
                recorded = store.record_reply(
                    agent_id,
                    str(turn["message_id"]),
                    _structured_reply_text(reply),
                )
                artifact_paths = [
                    str(item.get("path"))
                    for item in recorded.get("artifacts", [])
                    if item.get("path")
                ]
                handoff = build_compact_handoff(
                    step=step_number,
                    agent_id=agent_id,
                    reply=reply,
                    reply_id=str(recorded["reply_id"]),
                    artifact_paths=artifact_paths,
                )
                turn.update(
                    {
                        "status": reply["status"],
                        "reply_id": recorded["reply_id"],
                        "handoff": handoff,
                        "artifact_paths": artifact_paths,
                        "trace_command": f"agentdeck trace --id {recorded['reply_id']}",
                        "completed_at": utc_now(),
                    }
                )
                store.update_workflow_run(
                    run_id,
                    turns=turns,
                    current_step=step_number + 1,
                )
                if reply["status"] == "completed":
                    store.append_event(
                        EventRecord.create(
                            "workflow_step_completed",
                            {
                                "run_id": run_id,
                                "step": step_number,
                                "agent_id": agent_id,
                                "message_id": turn["message_id"],
                                "reply_id": recorded["reply_id"],
                            },
                        )
                    )
                    previous_handoff = handoff
                    break
                reason = (
                    "worker_blocked" if reply["status"] == "blocked" else "worker_failed"
                )
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status=reply["status"],
                    reason=reason,
                )
            if monotonic() - started >= timeout_seconds:
                return _stop_workflow(
                    store,
                    run_id=run_id,
                    turns=turns,
                    turn=turn,
                    turn_status="timed_out",
                    reason="timed_out",
                )
            sleeper(poll_interval)

    result = store.update_workflow_run(
        run_id,
        status="completed",
        current_step=len(steps) + 1,
        turns=turns,
        stop_reason=None,
    )
    store.append_event(
        EventRecord.create(
            "workflow_completed",
            {
                "run_id": run_id,
                "plan_id": result.get("plan_id"),
                "step_count": len(steps),
            },
        )
    )
    return result
