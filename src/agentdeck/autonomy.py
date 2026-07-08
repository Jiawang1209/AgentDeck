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


def run_loop_gate(
    review: dict[str, Any],
    has_error: bool,
    plan_id: str,
) -> tuple[str, str]:
    """Diagnose where a plan is stuck after one run-loop wave.

    Returns (stopped_reason, next_command) -- a read-only, explicit next step
    for the human. Priority: error first, then the leader_review next_action.
    """
    if has_error:
        return "error", f"agentdeck plan status --plan-id {plan_id}"
    next_action = review.get("next_action")
    if next_action == "dispatch_approved":
        # an approved step survived the wave -> its agent has no running pane
        return "blocked", f"agentdeck agent spawn --agent {review.get('agent_id')}"
    if next_action == "wait_for_approval":
        return "needs_human_approval", "agentdeck approval list"
    if next_action == "wait_for_reply":
        return (
            "waiting_for_reply",
            f"agentdeck capture-reply --agent {review.get('agent_id')} --message-id {review.get('message_id')}",
        )
    if next_action == "summarize":
        return "complete", f"agentdeck leader summary --plan-id {plan_id}"
    return "idle", f"agentdeck run --plan-id {plan_id}"
