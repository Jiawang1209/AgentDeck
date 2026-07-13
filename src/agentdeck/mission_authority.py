from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def canonical_workflow_authorized_steps(
    plan_record: Mapping[str, object],
) -> list[dict[str, Any]]:
    plan = plan_record.get("plan")
    plan = plan if isinstance(plan, dict) else {}
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    result: list[dict[str, Any]] = []
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
                "task_hash": f"sha256:{hashlib.sha256(task.encode('utf-8')).hexdigest()}",
            }
        )
    return result


def canonical_workflow_plan_hash(plan_record: Mapping[str, object]) -> str:
    canonical = {
        "plan_id": str(plan_record.get("plan_id") or ""),
        "steps": canonical_workflow_authorized_steps(plan_record),
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
