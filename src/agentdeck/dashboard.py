"""Read-only reference client that renders the ``agentdeck workbench`` contract.

This module is a *consumer* of the workbench snapshot contract: every value it
renders is derived from the payload dict alone (as an external GUI/TUI client
would fetch it from ``agentdeck workbench``). It never reads state, calls a
provider, or mutates anything — it only formats the read-only contract as text
and echoes the explicit commands a human would run.
"""

from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _rule(title: str) -> str:
    return f"── {title} " + "─" * max(0, 48 - len(title))


def _render_header(payload: dict[str, Any]) -> list[str]:
    project_view = _as_dict(payload.get("project_view"))
    project = project_view.get("project") or "unknown project"
    mode = payload.get("mode") or "workbench"
    schema = payload.get("schema_version") or ""
    lines = [f"AgentDeck — {project}  [mode: {mode}]"]
    if schema:
        lines.append(f"schema: {schema}")
    next_command = payload.get("next_command")
    if next_command:
        lines.append(f"Next: {next_command}")
    return lines


def _render_recovery(payload: dict[str, Any]) -> list[str]:
    recovery = _as_dict(payload.get("recovery"))
    if not recovery:
        return []
    status = recovery.get("status") or "unknown"
    reason = recovery.get("reason")
    lines = [_rule("Recovery"), f"status: {status}" + (f" — {reason}" if reason else "")]
    recommended = _as_dict(recovery.get("recommended_action"))
    command = recommended.get("command")
    if command:
        lines.append(f"  → {command}")
    return lines


def _render_run_progress(payload: dict[str, Any]) -> list[str]:
    card = payload.get("run_progress_card")
    if not isinstance(card, dict):
        return []
    plan_id = card.get("plan_id") or ""
    task = card.get("task") or ""
    status = card.get("status") or ""
    lines = [_rule("Run progress"), f"plan {plan_id} — {task}  [status: {status}]"]
    counts = _as_dict(card.get("counts"))
    if counts:
        lines.append(
            f"steps {counts.get('steps', 0)}  approvals {counts.get('approvals', 0)} "
            f"(pending {counts.get('pending', 0)}, approved {counts.get('approved', 0)}, "
            f"dispatched {counts.get('dispatched', 0)})"
        )
    for step in _as_list(card.get("steps")):
        step = _as_dict(step)
        num = step.get("step")
        agent_id = str(step.get("agent_id") or "")
        approval_status = str(step.get("approval_status") or "")
        step_task = str(step.get("task") or "")
        lines.append(f"  step {num}  {agent_id:<12} {approval_status:<12} {step_task}".rstrip())
    review = _as_dict(card.get("review"))
    next_action = review.get("next_action")
    if next_action:
        reason = review.get("reason")
        lines.append(f"review: {next_action}" + (f" — {reason}" if reason else ""))
    next_command = card.get("next_command")
    if next_command:
        lines.append(f"  → {next_command}")
    return lines


def _render_role_topology(payload: dict[str, Any]) -> list[str]:
    card = _as_dict(payload.get("role_topology_card"))
    if not card:
        return []
    count = card.get("count", 0)
    blocked = card.get("blocked_count", 0)
    lines = [_rule("Role topology"), f"{count} roles, {blocked} blocked"]
    for role in _as_list(card.get("roles")):
        role = _as_dict(role)
        kind = str(role.get("kind") or "")
        kind_label = "logical" if kind == "logical_role" else "worker"
        role_id = str(role.get("role_id") or "")
        status = str(role.get("status") or "")
        provider = str(role.get("provider") or "")
        next_command = role.get("next_command") or ""
        row = f"  {kind_label:<7} {role_id:<16} {status:<20} {provider:<12}"
        if next_command:
            row += f" → {next_command}"
        lines.append(row.rstrip())
        blocker = role.get("blocker")
        if blocker:
            lines.append(f"      ⨯ blocked: {blocker}")
    return lines


def _render_worker_activity(payload: dict[str, Any]) -> list[str]:
    card = _as_dict(payload.get("worker_lifecycle_card"))
    items = _as_list(card.get("items"))
    if not items:
        return []
    lines = [_rule("Worker activity")]
    for item in items:
        item = _as_dict(item)
        agent_id = str(item.get("agent_id") or "")
        stage = str(item.get("lifecycle_stage") or "")
        detail: list[str] = []
        pending = item.get("pending_inbox_count") or 0
        if pending:
            detail.append(f"inbox:{pending}")
        artifacts = item.get("artifact_count") or 0
        if artifacts:
            detail.append(f"artifacts:{artifacts}")
        for label, key in (("msg", "active_message_id"), ("job", "active_job_id"), ("reply", "latest_reply_id")):
            value = item.get(key)
            if value:
                detail.append(f"{label}:{value}")
        row = f"  {agent_id:<14} {stage:<18}"
        if detail:
            row += "  " + "  ".join(detail)
        lines.append(row.rstrip())
    return lines


def _render_review_gate(payload: dict[str, Any]) -> list[str]:
    card = _as_dict(payload.get("review_gate_card"))
    if not card:
        return []
    status = card.get("status") or "unknown"
    reason = card.get("reason")
    header = f"status: {status}" + (f" — {reason}" if reason else "")
    lines = [_rule("Review gate"), header]
    for stage_name in ("code_review", "round_review"):
        stage = _as_dict(card.get(stage_name))
        if not stage:
            continue
        agent_id = stage.get("agent_id") or "—"
        stage_status = stage.get("status") or ""
        row = f"  {stage_name:<13} {str(agent_id):<12} {stage_status}"
        blocker = stage.get("blocker")
        if blocker:
            row += f"  ({blocker})"
        lines.append(row.rstrip())
    return lines


def _render_release_preview(payload: dict[str, Any]) -> list[str]:
    card = _as_dict(payload.get("release_preview_card"))
    if not card:
        return []
    status = card.get("status") or "unknown"
    lines = [_rule("Release"), f"status: {status}"]
    reason = card.get("reason")
    if reason:
        lines[-1] += f" — {reason}"
    release_command = card.get("release_command")
    if release_command:
        lines.append(f"  → {release_command}")
    release_count = card.get("release_count")
    if release_count:
        detail = f"  released rounds: {release_count}"
        latest = card.get("latest_release_id")
        if latest:
            detail += f" (latest {latest})"
        lines.append(detail)
    return lines


def _render_ledger(payload: dict[str, Any]) -> list[str]:
    card = _as_dict(payload.get("ledger_card"))
    if not card:
        return []
    parts = []
    for name in ("messages", "jobs", "replies", "artifacts", "inbox"):
        summary = _as_dict(card.get(name))
        count = summary.get("count", 0)
        parts.append(f"{name} {count}")
    return [_rule("Ledger"), "  " + "  ".join(parts)]


def _render_queue(payload: dict[str, Any]) -> list[str]:
    card = _as_dict(payload.get("queue_card"))
    if not card:
        return []
    source = card.get("active_queue_source") or payload.get("active_queue_source") or "none"
    lines = [_rule("Queue"), f"active source: {source}"]
    next_command = card.get("next_command")
    if next_command:
        lines.append(f"  next: {next_command}")
    return lines


def _render_control_palette(payload: dict[str, Any]) -> list[str]:
    registry = _as_list(payload.get("control_registry"))
    if not registry:
        return []
    # preserve first-seen scope order for a stable, grouped view
    scope_order: list[str] = []
    totals: dict[str, int] = {}
    enabled: dict[str, int] = {}
    blocked: dict[str, int] = {}
    for item in registry:
        item = _as_dict(item)
        scope = str(item.get("scope") or "")
        if scope not in totals:
            scope_order.append(scope)
            totals[scope] = 0
            enabled[scope] = 0
            blocked[scope] = 0
        totals[scope] += 1
        if item.get("enabled") is True:
            enabled[scope] += 1
        else:
            blocked[scope] += 1
    lines = [
        _rule("Command palette"),
        f"{len(registry)} controls  (drill down: agentdeck controls --scope <scope>)",
    ]
    for scope in scope_order:
        lines.append(
            f"  {scope:<18} {totals[scope]:>3} controls   "
            f"{enabled[scope]} enabled   {blocked[scope]} blocked"
        )
    return lines


def _render_learning_layer(payload: dict[str, Any]) -> list[str]:
    skills = _as_dict(payload.get("skill_suggestions_card"))
    memory = _as_dict(payload.get("memory_suggestions_card"))
    if not skills and not memory:
        return []
    lines = [_rule("Learning layer")]
    if skills:
        pending = skills.get("pending_count", skills.get("count", 0))
        lines.append(f"skill suggestions: {pending} pending  ({skills.get('suggestions_command', 'agentdeck skills suggestions')})")
        for item in _as_list(skills.get("items")):
            item = _as_dict(item)
            name = str(item.get("name") or item.get("suggestion_id") or "")
            status = str(item.get("status") or "")
            summary = str(item.get("summary") or "")
            source = str(item.get("source") or "")
            lines.append(f"  • {name} [{status}] — {summary}  (source: {source})".rstrip())
    if memory:
        pending = memory.get("pending_count", memory.get("count", 0))
        lines.append(f"memory suggestions: {pending} pending  ({memory.get('suggestions_command', 'agentdeck memory suggestions')})")
        for item in _as_list(memory.get("items")):
            item = _as_dict(item)
            scope = str(item.get("scope") or item.get("suggestion_id") or "")
            status = str(item.get("status") or "")
            summary = str(item.get("summary") or "")
            lines.append(f"  • {scope} [{status}] — {summary}".rstrip())
            apply_command = None
            for control in _as_list(item.get("controls")):
                control = _as_dict(control)
                if control.get("kind") == "apply_memory":
                    apply_command = control.get("command")
                    break
            if apply_command:
                lines.append(f"      apply: {apply_command}")
    return lines


def render_workbench_dashboard(payload: dict[str, Any]) -> str:
    """Render a read-only text dashboard from a workbench snapshot payload."""
    payload = _as_dict(payload)
    sections: list[list[str]] = [
        _render_header(payload),
        _render_recovery(payload),
        _render_run_progress(payload),
        _render_role_topology(payload),
        _render_worker_activity(payload),
        _render_review_gate(payload),
        _render_release_preview(payload),
        _render_ledger(payload),
        _render_queue(payload),
        _render_learning_layer(payload),
        _render_control_palette(payload),
    ]
    blocks = ["\n".join(section) for section in sections if section]
    return "\n\n".join(blocks) + "\n"
