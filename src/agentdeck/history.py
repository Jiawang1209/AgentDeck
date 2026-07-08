"""Read-only renderer that projects the events.jsonl audit ledger to Markdown.

Deterministic and LLM-free: every line is derived from the audit events alone.
Rendering never mutates state; the optional file write in the CLI materializes a
regenerable projection (mirrors how dashboard.py renders the workbench contract).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


def _detail(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


# event_type -> (action, detail) derived from the payload. Any event_type NOT in
# this map (e.g. leader_chat_turn, internal validation failures, future events) is
# skipped, so new events never break rendering.
_MILESTONES = {
    "project_initialized": lambda p: ("Project initialized", ""),
    "leader_plan_created": lambda p: ("Plan created", _detail(p, "plan_id")),
    "run_started": lambda p: ("Run started", _detail(p, "task")),
    "approvals_created_from_plan": lambda p: ("Approvals created from plan", _detail(p, "plan_id")),
    "approval_created_from_chat": lambda p: ("Approval created (from chat)", ""),
    "approval_decided": lambda p: (f"Approval {_detail(p, 'status') or 'decided'}", _detail(p, "approval_id")),
    "approval_dispatched": lambda p: ("Approval dispatched", _detail(p, "approval_id")),
    "approval_dispatch_ready_completed": lambda p: ("Batch dispatch completed", _detail(p, "dispatched_count", "count")),
    "task_dispatched": lambda p: ("Task dispatched", _detail(p, "agent_id", "to_agent")),
    "task_replied": lambda p: ("Reply captured", _detail(p, "agent_id", "from_agent")),
    "reply_captured": lambda p: ("Reply captured", _detail(p, "agent_id", "from_agent")),
    "round_released": lambda p: ("Round released", f"round {_detail(p, 'round')}" if _detail(p, "round") else ""),
    "round_release_rejected": lambda p: ("Release rejected", _detail(p, "reason")),
    "policy_mode_updated": lambda p: ("Control mode changed", _detail(p, "mode")),
    "policy_mode_rejected": lambda p: ("Control mode change rejected", _detail(p, "mode")),
    "leader_provider_updated": lambda p: ("Leader provider switched", "/".join(x for x in [_detail(p, "provider"), _detail(p, "model")] if x)),
    "leader_provider_update_rejected": lambda p: ("Provider switch rejected", ""),
    "leader_provider_failed": lambda p: ("Provider failed", _detail(p, "detail", "error")),
    "agent_spawned": lambda p: ("Agent spawned", _detail(p, "agent_id")),
    "agent_spawn_ready_completed": lambda p: ("Agents spawned", _detail(p, "spawned_count")),
    "agent_stopped": lambda p: ("Agent stopped", _detail(p, "agent_id")),
    "agent_input_sent": lambda p: ("Input sent", _detail(p, "agent_id")),
    "agent_role_assigned": lambda p: ("Role assigned", "/".join(x for x in [_detail(p, "agent_id"), _detail(p, "role")] if x)),
    "agent_runtime_stale": lambda p: ("Runtime marked stale", _detail(p, "agent_id")),
    "inbox_item_acked": lambda p: ("Inbox acked", _detail(p, "inbox_id")),
    "leader_action_suggested": lambda p: ("Leader action suggested", _detail(p, "kind", "action_kind")),
    "leader_action_applied": lambda p: ("Leader action applied", _detail(p, "kind", "action_kind")),
    "skill_imported": lambda p: ("Skill imported", _detail(p, "name")),
    "skill_loaded": lambda p: ("Skill loaded", _detail(p, "name")),
    "skill_suggested": lambda p: ("Skill suggested", _detail(p, "name")),
    "skill_created": lambda p: ("Skill created", _detail(p, "name")),
    "memory_suggested": lambda p: ("Memory suggested", ""),
    "memory_applied": lambda p: ("Memory applied", ""),
}


def _humanize_event(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("event_type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    render = _MILESTONES.get(event_type)
    if render is None:
        return None
    action, detail = render(payload)
    return f"{action} · {detail}" if detail else action
