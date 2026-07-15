from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping

from .semantic_authority import semantic_authority_hash, validate_semantic_authority
from .semantic_planning import compile_worker_task, semantic_step_hash


def _bounded_text(value: object, limit: int = 4096) -> bool:
    if type(value) is not str or not value:
        return False
    try:
        return len(value.encode("utf-8")) <= limit
    except UnicodeError:
        return False


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
    plan = plan_record.get("plan")
    if isinstance(plan, dict) and (
        "semantic_authority" in plan or "semantic_steps" in plan
    ):
        if type(plan) is not dict or type(plan.get("semantic_steps")) is not list:
            raise ValueError("semantic plan invalid")
        canonical["semantic_authority_hash"] = semantic_authority_hash(
            plan.get("semantic_authority")
        )
        canonical["semantic_step_hashes"] = [
            semantic_step_hash(item) for item in plan["semantic_steps"]
        ]
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def validated_compiled_semantic_plan(plan: object) -> dict[str, Any]:
    """Return an exact defensive copy of a fully compiled semantic plan."""
    if (
        type(plan) is not dict
        or set(plan)
        != {"goal", "summary", "steps", "semantic_authority", "semantic_steps"}
        or not _bounded_text(plan.get("goal"))
        or not _bounded_text(plan.get("summary"))
        or type(plan.get("semantic_authority")) is not dict
        or type(plan.get("semantic_steps")) is not list
        or type(plan.get("steps")) is not list
        or len(plan["semantic_steps"]) != len(plan["steps"])
        or not plan["steps"]
    ):
        raise ValueError("semantic plan invalid")
    try:
        authority = validate_semantic_authority(plan["semantic_authority"])
        requirements = {
            item["requirement_id"]: item for item in authority["requirements"]
        }
        covered: list[str] = []
        proposals: list[dict[str, object]] = []
        for position, (semantic, compatibility) in enumerate(
            zip(plan["semantic_steps"], plan["steps"]), start=1
        ):
            if type(semantic) is not dict or type(compatibility) is not dict:
                raise ValueError
            if (
                set(compatibility)
                != {
                    "step",
                    "agent_id",
                    "role",
                    "task",
                    "risk",
                    "requires_approval",
                }
                or type(compatibility.get("step")) is not int
                or not _bounded_text(compatibility.get("agent_id"), 256)
                or not _bounded_text(compatibility.get("role"), 4096)
                or not _bounded_text(compatibility.get("task"), 65536)
                or not _bounded_text(compatibility.get("risk"), 256)
                or type(compatibility.get("requires_approval")) is not bool
            ):
                raise ValueError
            if semantic_step_hash(semantic) != semantic["semantic_step_hash"]:
                raise ValueError
            if (
                compatibility.get("step") != position
                or semantic.get("step") != position
                or compatibility.get("agent_id") != semantic.get("agent_id")
                or compatibility.get("role") != semantic.get("role")
                or compatibility.get("task") != compile_worker_task(semantic)
            ):
                raise ValueError
            refs = semantic.get("authority_refs")
            effects = semantic.get("required_effects")
            if type(refs) is not list or type(effects) is not list:
                raise ValueError
            if [requirements.get(ref) for ref in refs] != effects:
                raise ValueError
            covered.extend(refs)
            step_proposals = semantic.get("proposed_effects")
            if type(step_proposals) is not list:
                raise ValueError
            proposals.extend(step_proposals)
        if covered != list(requirements) or proposals != authority["proposed_effects"]:
            raise ValueError
    except Exception:
        raise ValueError("semantic plan invalid") from None
    return deepcopy(plan)
