from __future__ import annotations

import hashlib
import json
from typing import Any


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
            raise ValueError(f"missing workflow reply field: {field}")
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
