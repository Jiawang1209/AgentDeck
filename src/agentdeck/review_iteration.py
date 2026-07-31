"""Pure derivation for the review-driven iteration loop.

review verdict `fail`/`needs_changes` 时,从既有 plan/replies 推导应追加的
rework + re-review step 对。只做推导:不碰 IO、不 import cli/state、零 LLM
(回炉任务是确定性模板)。唯一写点是 StateStore.append_review_iteration。
See docs/superpowers/specs/2026-07-30-review-iteration-loop-design.md.
"""
from __future__ import annotations

from typing import Any

REVIEW_ITERATION_ORIGIN = "review_iteration"
REWORK_TRIGGER_OVERALLS = frozenset({"fail", "needs_changes"})
MAX_REWORK_TASK_CHARS = 4000

# 闭合拒绝原因:调用方按原因决定沉默跳过还是如实报告。
REVIEW_ITERATION_SKIP_REASONS = (
    "no_plan",
    "no_verdict",
    "verdict_pass",
    "already_triggered",
    "rounds_exhausted",
    "no_implementation_step",
)


def _refuse(reason: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason}


def plan_review_rounds(steps: list[dict[str, Any]]) -> int:
    """已追加的迭代轮数 = step 上 round 标记的最大值(无标记 = 0)。"""
    rounds = 0
    for step in steps:
        if not isinstance(step, dict) or step.get("origin") != REVIEW_ITERATION_ORIGIN:
            continue
        value = step.get("round")
        if isinstance(value, int) and value > rounds:
            rounds = value
    return rounds


def _consumed_reply_ids(steps: list[dict[str, Any]]) -> set[str]:
    return {
        str(step["triggered_by_reply"])
        for step in steps
        if isinstance(step, dict)
        and step.get("origin") == REVIEW_ITERATION_ORIGIN
        and step.get("triggered_by_reply")
    }


def _latest_verdict_reply(
    state: dict[str, Any], plan_id: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(reply, 该 reply 所属 review step 的 approval);与 plan_verdict_summary
    同源的最新有效 verdict 选取(replies 按入账顺序,取最后一条)。"""
    approvals_by_message = {
        str(approval.get("message_id")): approval
        for approval in state.get("approvals", [])
        if isinstance(approval, dict)
        and approval.get("plan_id") == plan_id
        and approval.get("message_id")
    }
    latest: tuple[dict[str, Any], dict[str, Any]] | None = None
    for reply in state.get("replies", []):
        if not isinstance(reply, dict) or not isinstance(reply.get("verdict"), dict):
            continue
        approval = approvals_by_message.get(str(reply.get("message_id")))
        if approval is not None:
            latest = (reply, approval)
    return latest


def _implementation_approval(
    state: dict[str, Any], plan_id: str, review_step: int
) -> dict[str, Any] | None:
    """被审查的实现 step:优先取 review step 之前、message 带 worktree_branch
    的最大 step(base branch 直接来源);无 worktree 运行时回退到之前任一
    已派发 step 的最大者;都没有则 None(fail-closed 不追加)。"""
    messages_by_id = {
        str(message.get("message_id")): message
        for message in state.get("messages", [])
        if isinstance(message, dict)
    }
    best_branch: tuple[int, dict[str, Any]] | None = None
    best_dispatched: tuple[int, dict[str, Any]] | None = None
    for approval in state.get("approvals", []):
        if not isinstance(approval, dict) or approval.get("plan_id") != plan_id:
            continue
        try:
            step = int(approval.get("step") or 0)
        except (TypeError, ValueError):
            continue
        if step >= review_step or not approval.get("message_id"):
            continue
        message = messages_by_id.get(str(approval.get("message_id")), {})
        if message.get("worktree_branch"):
            if best_branch is None or step > best_branch[0]:
                best_branch = (step, approval)
        if best_dispatched is None or step > best_dispatched[0]:
            best_dispatched = (step, approval)
    chosen = best_branch or best_dispatched
    return chosen[1] if chosen else None


def build_rework_task(
    *,
    round_number: int,
    original_task: str,
    verdict: dict[str, Any],
    reply_id: str,
    reply_text: str,
) -> str:
    """确定性模板:fail 标准原文 + reviewer 回复原文,超长截断并附 trace
    指引;绝不调用 provider、不读产物文件。"""
    overall = str(verdict.get("overall"))
    score = verdict.get("score")
    header = [
        f"Review 第 {round_number} 轮未通过,按审查意见返工。",
        f"原任务: {original_task}",
        "审查判定: " + overall + (f" (score {score})" if isinstance(score, int) else ""),
    ]
    failed = [
        item
        for item in verdict.get("criteria", [])
        if isinstance(item, dict) and item.get("verdict") == "fail"
    ]
    if failed:
        header.append("未通过的验收标准:")
        for item in failed:
            entry = f"- {item.get('criterion')}"
            if item.get("evidence"):
                entry += f" (证据: {item['evidence']})"
            header.append(entry)
    footer = "修复后 commit 到任务分支。"
    text = "\n".join([*header, "审查意见原文:", reply_text, footer])
    if len(text) > MAX_REWORK_TASK_CHARS:
        marker = (
            f"\n[审查意见已截断,全文见 agentdeck trace --id {reply_id}]\n{footer}"
        )
        text = text[: MAX_REWORK_TASK_CHARS - len(marker)] + marker
    return text


def derive_review_iteration(
    state: dict[str, Any], plan_id: str, max_review_rounds: int
) -> dict[str, Any]:
    """纯触发判定 + step 对推导;任何条件不满足都返回 {ok: False, reason}。"""
    plan = next(
        (
            item
            for item in state.get("plans", [])
            if isinstance(item, dict) and item.get("plan_id") == plan_id
        ),
        None,
    )
    if plan is None:
        return _refuse("no_plan")
    body = plan.get("plan")
    steps = body.get("steps", []) if isinstance(body, dict) else []
    latest = _latest_verdict_reply(state, plan_id)
    if latest is None:
        return _refuse("no_verdict")
    reply, review_approval = latest
    if reply["verdict"].get("overall") not in REWORK_TRIGGER_OVERALLS:
        return _refuse("verdict_pass")
    reply_id = str(reply.get("reply_id"))
    if reply_id in _consumed_reply_ids(steps):
        return _refuse("already_triggered")
    rounds = plan_review_rounds(steps)
    if rounds >= max_review_rounds:
        return _refuse("rounds_exhausted")
    try:
        review_step_number = int(review_approval.get("step") or 0)
    except (TypeError, ValueError):
        return _refuse("no_implementation_step")
    implementation = _implementation_approval(state, plan_id, review_step_number)
    if implementation is None:
        return _refuse("no_implementation_step")
    round_number = rounds + 1
    numbers = [int(step.get("step") or 0) for step in steps if isinstance(step, dict)]
    next_number = (max(numbers) if numbers else 0) + 1
    provenance = {
        "origin": REVIEW_ITERATION_ORIGIN,
        "round": round_number,
        "triggered_by_reply": reply_id,
    }
    rework_step = {
        "step": next_number,
        "agent_id": implementation.get("agent_id"),
        "role": implementation.get("role"),
        "task": build_rework_task(
            round_number=round_number,
            original_task=str(implementation.get("task") or ""),
            verdict=reply["verdict"],
            reply_id=reply_id,
            reply_text=str(reply.get("text") or ""),
        ),
        "risk": implementation.get("risk") or "low",
        "requires_approval": True,
        **provenance,
    }
    review_step = {
        "step": next_number + 1,
        "agent_id": review_approval.get("agent_id"),
        "role": review_approval.get("role"),
        "task": str(review_approval.get("task") or ""),
        "risk": review_approval.get("risk") or "low",
        "requires_approval": True,
        **provenance,
    }
    return {
        "ok": True,
        "round": round_number,
        "triggered_by_reply": reply_id,
        "rework_step": rework_step,
        "review_step": review_step,
    }
