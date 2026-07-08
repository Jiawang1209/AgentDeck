"""Pure autonomous-mode decision logic (no I/O).

Given the human-configured allowlist + count budget, decide which pending
approvals AgentDeck may auto-approve. Reused by `agentdeck approval auto` and,
later, by the sub-project 3 execution loop.
"""

from __future__ import annotations

from typing import Any


def select_auto_approvals(
    pending: list[dict[str, Any]],
    allowed_agents: tuple[str, ...],
    max_approvals: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed = set(allowed_agents)
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for approval in pending:
        if not isinstance(approval, dict):
            continue
        agent_id = approval.get("agent_id")
        if agent_id not in allowed:
            skipped.append({**approval, "reason": "agent not in allowlist"})
        elif len(selected) >= max_approvals:
            skipped.append({**approval, "reason": "budget exhausted"})
        else:
            selected.append(approval)
    return selected, skipped
