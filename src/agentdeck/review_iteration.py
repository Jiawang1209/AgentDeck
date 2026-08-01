"""Pure derivation for the review-driven iteration loop.

review verdict `fail`/`needs_changes` 时,从既有 plan/replies 推导应追加的
rework + re-review step 对。只做推导:不碰 IO、不 import cli/state、零 LLM
(回炉任务是确定性模板)。唯一写点是 StateStore.append_review_iteration。
See docs/superpowers/specs/2026-07-30-review-iteration-loop-design.md.
"""
from __future__ import annotations

from typing import Any

from .review_group import (
    aggregate_group_verdicts,
    latest_complete_group,
    latest_group_status,
    review_group_numbers,
)
from .review_verdict import REVIEW_VERDICT_SCHEMA_VERSION

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


def rework_step_numbers(steps: list[dict[str, Any]]) -> set[int]:
    """已追加迭代对中 rework(回炉)成员的 step 编号。

    这些 step 由实现 agent 执行,其回复里的自评 `verdict:` 绝不能被当成
    plan 的 review verdict——否则自评 fail 会触发"coder 复审自己"的幻影
    迭代轮,自评 pass 会越过 reviewer 放行 merge。触发器与
    plan_verdict_summary 共用本判定(单一来源)。"""
    numbers: set[int] = set()
    for step in steps:
        if not isinstance(step, dict) or step.get("origin") != REVIEW_ITERATION_ORIGIN:
            continue
        if step.get("iteration_kind") != "rework":
            continue
        value = step.get("step")
        if isinstance(value, int) and not isinstance(value, bool):
            numbers.add(value)
    return numbers


def _consumed_reply_ids(steps: list[dict[str, Any]]) -> set[str]:
    return {
        str(step["triggered_by_reply"])
        for step in steps
        if isinstance(step, dict)
        and step.get("origin") == REVIEW_ITERATION_ORIGIN
        and step.get("triggered_by_reply")
    }


def _latest_verdict_reply(
    state: dict[str, Any], plan_id: str, steps: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(reply, 该 reply 所属 review step 的 approval);与 plan_verdict_summary
    同源的最新有效 verdict 选取(replies 按入账顺序,取最后一条),并同源
    排除 rework step 的自评 verdict。"""
    excluded_steps = rework_step_numbers(steps)
    approvals_by_message = {
        str(approval.get("message_id")): approval
        for approval in state.get("approvals", [])
        if isinstance(approval, dict)
        and approval.get("plan_id") == plan_id
        and approval.get("message_id")
        and approval.get("step") not in excluded_steps
    }
    latest: tuple[dict[str, Any], dict[str, Any]] | None = None
    for reply in state.get("replies", []):
        if not isinstance(reply, dict) or not isinstance(reply.get("verdict"), dict):
            continue
        approval = approvals_by_message.get(str(reply.get("message_id")))
        if approval is not None:
            latest = (reply, approval)
    return latest


def select_plan_verdict(
    state: dict[str, Any],
    plan_id: str,
    steps: list[dict[str, Any]],
    require_complete_group: bool = True,
) -> dict[str, Any] | None:
    """组感知的**单一来源** verdict 选取(plan_verdict_summary 与迭代触发器共用)。

    返回 `{verdict, reply_id, reply_text, approval, members, group, complete}`
    或 None:
    - plan 带 review 组标记时按组聚合(any-fail-blocks)成一份 schema
      合规的合成 verdict。`require_complete_group=True`(**触发面**默认)
      只认最新完整组:组未齐一律 None——先 fail 的成员绝不能在其余成员
      还在审旧代码时开一轮,否则后到的 fail 会再开一轮、预算双烧。
      `False`(**展示与 merge gate 面**)则对已报到成员聚合并带
      `complete=False`:组内一人 verdict 无效/缺失时,另一人的有效 fail
      绝不能随整组一起消失而放开自动合并(2026-08-01 终审 Critical)。
    - 无组标记时逐字节沿用今天的"最后一条有效 verdict reply"路径
      (含 rework 自评排除)。
    `approval` 是该 verdict 所属 review step 的 approval;组路径取**第一个**
    成员的 approval,使 `_implementation_approval` 的"review step 之前"边界
    仍落在被审查的实现 step 上,而不是同组的另一个 reviewer。
    """
    steps = steps if isinstance(steps, list) else []
    if not review_group_numbers(steps):
        latest = _latest_verdict_reply(state, plan_id, steps)
        if latest is None:
            return None
        reply, approval = latest
        return {
            "verdict": reply["verdict"],
            "reply_id": str(reply.get("reply_id")),
            "reply_text": str(reply.get("text") or ""),
            "approval": approval,
            "members": None,
            "group": None,
            "complete": True,
        }
    approvals = [
        approval
        for approval in state.get("approvals", [])
        if isinstance(approval, dict)
    ]
    replies = [reply for reply in state.get("replies", []) if isinstance(reply, dict)]
    if require_complete_group:
        group = latest_complete_group(steps, approvals, replies, plan_id)
    else:
        group = latest_group_status(steps, approvals, replies, plan_id)
    if group is None:
        return None
    members = group["members"]
    reported = [member for member in members if member.get("verdict")]
    if not reported:
        return None
    approval = next(
        (
            item
            for item in approvals
            if item.get("plan_id") == plan_id and item.get("step") == members[0]["step"]
        ),
        None,
    )
    if approval is None:
        return None
    texts = {
        str(reply.get("reply_id")): str(reply.get("text") or "") for reply in replies
    }
    aggregate = aggregate_group_verdicts(reported)
    verdict: dict[str, Any] = {
        "schema_version": REVIEW_VERDICT_SCHEMA_VERSION,
        "criteria": aggregate["criteria"],
        "overall": aggregate["overall"],
    }
    score = aggregate.get("score")
    if isinstance(score, int) and not isinstance(score, bool):
        verdict["score"] = score
    return {
        "verdict": verdict,
        "reply_id": str(group["last_reply_id"]),
        "reply_text": "",
        "approval": approval,
        "members": [
            {
                "agent_id": member.get("agent_id"),
                "step": member.get("step"),
                "reply_id": member.get("reply_id"),
                "verdict": member.get("verdict") or {},
                "text": texts.get(str(member.get("reply_id")), ""),
            }
            for member in members
        ],
        "group": group["group"],
        "complete": bool(group.get("complete", True)),
    }


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


def build_group_review_text(members: list[dict[str, Any]]) -> str:
    """逐 reviewer 署名分段合并组内**非 pass** 成员的 fail 标准与回复原文。

    只是把已有事实重排成 `### reviewer <agent_id>` 小节喂给既有截断预算,
    不调用 provider、不读产物文件;全 pass 组不会走到这里(不触发迭代)。
    """
    blocks: list[str] = []
    for member in members:
        verdict = member.get("verdict") or {}
        overall = str(verdict.get("overall"))
        if overall == "pass":
            continue
        block = [f"### reviewer {member.get('agent_id')}", f"判定: {overall}"]
        failed = [
            item
            for item in verdict.get("criteria", [])
            if isinstance(item, dict) and item.get("verdict") == "fail"
        ]
        if failed:
            block.append("未通过的验收标准:")
            for item in failed:
                entry = f"- {item.get('criterion')}"
                if item.get("evidence"):
                    entry += f" (证据: {item['evidence']})"
                block.append(entry)
        text = str(member.get("text") or "")
        if text:
            block.append(text)
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def build_rework_task(
    *,
    round_number: int,
    original_task: str,
    verdict: dict[str, Any],
    reply_id: str,
    reply_text: str,
    trace_ids: list[str] | None = None,
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
        pointers = " / ".join(
            f"agentdeck trace --id {trace_id}" for trace_id in (trace_ids or [reply_id])
        )
        marker = f"\n[审查意见已截断,全文见 {pointers}]\n{footer}"
        text = text[: MAX_REWORK_TASK_CHARS - len(marker)] + marker
    return text


def _review_binding_pairs(
    review_binding: dict[str, Any] | None,
) -> tuple[tuple[tuple[str, str], ...], tuple[str, str] | None]:
    """`{round_reviewer: (id, role)|None, reviewers: ((id, role), …)}` → 纯数据。

    None / 空 / 半坏输入一律退化为"今天的克隆行为"(fail-safe:配置从不
    扩大授权面,解析不了就不换人)。"""
    binding = review_binding if isinstance(review_binding, dict) else {}

    def _pair(value: object) -> tuple[str, str] | None:
        if isinstance(value, (tuple, list)) and len(value) == 2 and all(value):
            return (str(value[0]), str(value[1]))
        return None

    reviewers = tuple(
        pair
        for pair in (_pair(item) for item in (binding.get("reviewers") or ()))
        if pair is not None
    )
    return reviewers, _pair(binding.get("round_reviewer"))


def _review_step(
    *,
    step: int,
    agent_id: Any,
    role: Any,
    task: str,
    risk: Any,
    provenance: dict[str, Any],
    group: int | None = None,
    member: int | None = None,
) -> dict[str, Any]:
    item = {
        "iteration_kind": "review",
        "step": step,
        "agent_id": agent_id,
        "role": role,
        "task": task,
        "risk": risk,
        "requires_approval": True,
        **provenance,
    }
    if group is not None:
        item["review_group"] = group
        item["review_group_member"] = member
    return item


def derive_review_iteration(
    state: dict[str, Any],
    plan_id: str,
    max_review_rounds: int,
    review_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """纯触发判定 + step 推导;任何条件不满足都返回 {ok: False, reason}。

    `review_binding` 为 None(或空)时行为与配置 `[review]` 段缺省时逐字节
    一致:复审步克隆原 review step 的 agent/role。"""
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
    selection = select_plan_verdict(state, plan_id, steps)
    if selection is None:
        return _refuse("no_verdict")
    verdict = selection["verdict"]
    review_approval = selection["approval"]
    if verdict.get("overall") not in REWORK_TRIGGER_OVERALLS:
        return _refuse("verdict_pass")
    # 组级幂等:组路径的 reply_id 是组内**最后一个**成员的回复(组完成的
    # 标志),因此同一组绝不会触发第二轮。
    reply_id = selection["reply_id"]
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
    members = selection["members"]
    if members:
        reply_text = build_group_review_text(members)
        trace_ids = [
            str(member["reply_id"])
            for member in members
            if (member.get("verdict") or {}).get("overall") != "pass"
        ] or [reply_id]
    else:
        reply_text = selection["reply_text"]
        trace_ids = None
    rework_step = {
        "iteration_kind": "rework",
        "step": next_number,
        "agent_id": implementation.get("agent_id"),
        "role": implementation.get("role"),
        "task": build_rework_task(
            round_number=round_number,
            original_task=str(implementation.get("task") or ""),
            verdict=verdict,
            reply_id=reply_id,
            reply_text=reply_text,
            trace_ids=trace_ids,
        ),
        "risk": implementation.get("risk") or "low",
        "requires_approval": True,
        **provenance,
    }
    reviewers, round_reviewer = _review_binding_pairs(review_binding)
    review_task = str(review_approval.get("task") or "")
    review_risk = review_approval.get("risk") or "low"
    if reviewers:
        # 追加的复审组自身也带组标记,使组感知选取同样覆盖它——否则下一轮
        # 只要有一个成员先回就会被当成完整 verdict。
        next_group = max(review_group_numbers(steps).values(), default=0) + 1
        review_steps = [
            _review_step(
                step=next_number + 1 + index,
                agent_id=agent_id,
                role=role,
                task=review_task,
                risk=review_risk,
                provenance=provenance,
                group=next_group,
                member=index,
            )
            for index, (agent_id, role) in enumerate(reviewers)
        ]
    elif round_reviewer is not None:
        review_steps = [
            _review_step(
                step=next_number + 1,
                agent_id=round_reviewer[0],
                role=round_reviewer[1],
                task=review_task,
                risk=review_risk,
                provenance=provenance,
            )
        ]
    else:
        review_steps = [
            _review_step(
                step=next_number + 1,
                agent_id=review_approval.get("agent_id"),
                role=review_approval.get("role"),
                task=review_task,
                risk=review_risk,
                provenance=provenance,
            )
        ]
    return {
        "ok": True,
        "round": round_number,
        "triggered_by_reply": reply_id,
        "rework_step": rework_step,
        "review_step": review_steps[0],
        "review_steps": review_steps,
    }
