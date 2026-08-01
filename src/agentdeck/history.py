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
    "run_started": lambda p: ("Run started", _detail(p, "plan_id")),
    "approvals_created_from_plan": lambda p: ("Approvals created from plan", _detail(p, "plan_id")),
    "approval_created_from_chat": lambda p: ("Approval created (from chat)", ""),
    "approval_decided": lambda p: (
        "Approval auto-approved" if _detail(p, "source") == "autonomous" else f"Approval {_detail(p, 'status') or 'decided'}",
        _detail(p, "approval_id"),
    ),
    "approval_auto_completed": lambda p: ("Auto-approve run", f"{_detail(p, 'auto_approved') or 0} approved, {_detail(p, 'dispatched') or 0} dispatched"),
    "approval_dispatched": lambda p: ("Approval dispatched", _detail(p, "approval_id")),
    "approval_dispatch_failed": lambda p: ("Approval dispatch failed", _detail(p, "approval_id")),
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
    "run_loop_advanced": lambda p: (
        "Run-loop advanced",
        f"{_detail(p, 'dispatched') or 0} dispatched, stopped: {_detail(p, 'stopped_reason') or 'unknown'}",
    ),
    "run_loop_all_advanced": lambda p: (
        "Parallel wave",
        f"{_detail(p, 'plans_advanced') or 0} plans, {_detail(p, 'dispatched') or 0} dispatched",
    ),
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


def _split_timestamp(created_at: str) -> tuple[str, str]:
    if "T" in created_at:
        date, _, rest = created_at.partition("T")
        return date, rest[:8]
    return created_at[:10], ""


def render_history_markdown(events: list[dict[str, Any]], project: str) -> str:
    header = [f"# AgentDeck History — {project}", ""]
    rendered: list[tuple[str, str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        text = _humanize_event(event)
        if text is None:
            continue
        date, time = _split_timestamp(str(event.get("created_at") or ""))
        rendered.append((date, time, text))
    if not rendered:
        return "\n".join(header + ["_No recorded activity yet._"]) + "\n"
    rendered.reverse()  # ledger is oldest-first; reverse for newest-first
    by_date: "OrderedDict[str, list[tuple[str, str]]]" = OrderedDict()
    for date, time, text in rendered:
        by_date.setdefault(date or "unknown", []).append((time, text))
    lines = list(header)
    for date, entries in by_date.items():
        lines.append(f"## {date}")
        for time, text in entries:
            lines.append(f"- {time} · {text}" if time else f"- {text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
