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


def render_workbench_dashboard(payload: dict[str, Any]) -> str:
    """Render a read-only text dashboard from a workbench snapshot payload."""
    payload = _as_dict(payload)
    sections: list[list[str]] = [
        _render_header(payload),
        _render_recovery(payload),
        _render_role_topology(payload),
        _render_review_gate(payload),
        _render_queue(payload),
        _render_control_palette(payload),
    ]
    blocks = ["\n".join(section) for section in sections if section]
    return "\n\n".join(blocks) + "\n"
