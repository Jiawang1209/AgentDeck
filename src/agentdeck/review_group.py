"""Deterministic review-group expansion and any-fail-blocks aggregation.

`[review].reviewers` 让一个 review 环节确定性展开为 N 个串行 review step
(执行引擎零改动:仍是线性 plan + step 顺序守卫)。聚合按 user 拍板的
any-fail-blocks,且**组完成才判定**——组未齐绝不触发迭代,否则先 fail 的
成员会开一轮、后审旧代码的成员再开一轮,预算双烧。
纯模块:不碰 IO、不 import cli/state/config 对象(reviewers 以
(agent_id, role) 纯数据传入)。
See docs/superpowers/specs/2026-08-01-review-group-round-reviewer-design.md.
"""
from __future__ import annotations

import copy
from typing import Any

REVIEW_GROUP_ORIGIN = "review_group"
REVIEW_GROUP_RULE = "any_fail_blocks"
# 最严优先:fail > needs_changes > pass
_SEVERITY = {"pass": 0, "needs_changes": 1, "fail": 2}


def expand_review_group(
    plan: dict[str, Any], reviewers: tuple[tuple[str, str], ...]
) -> dict[str, Any]:
    """把每个 review step 展开为 N 个连续 step(编号顺延重排)。

    识别谓词:step 的 role 等于 reviewers[0] 的 role——首位 reviewer 是
    主 reviewer,其角色签名定义什么算 review 环节(跨角色组时不会误伤
    planning step)。reviewers 为空即原样返回。输入永不被修改。
    """
    if not reviewers:
        return plan
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return plan
    signature_role = reviewers[0][1]
    expanded: list[dict[str, Any]] = []
    group_number = 0
    number = 0
    for step in steps:
        if not isinstance(step, dict):
            return plan
        if step.get("role") != signature_role:
            number += 1
            item = copy.deepcopy(step)
            item["step"] = number
            expanded.append(item)
            continue
        group_number += 1
        for member_index, (agent_id, role) in enumerate(reviewers):
            number += 1
            item = copy.deepcopy(step)
            item.update({
                "step": number,
                "agent_id": agent_id,
                "role": role,
                "origin": REVIEW_GROUP_ORIGIN,
                "review_group": group_number,
                "review_group_member": member_index,
            })
            expanded.append(item)
    result = copy.deepcopy(plan)
    result["steps"] = expanded
    return result


def review_group_numbers(steps: list[dict[str, Any]]) -> dict[int, int]:
    """{step 编号: 组号};非组成员不出现(单 reviewer plan 返回空 dict)。

    谓词是 `review_group` 标记本身,不是 `origin`——迭代追加的复审组带
    `origin="review_iteration"` + 同一套组标记,必须同样被组感知选取覆盖。
    rework 成员从不带 `review_group`,因此仍被结构性排除(自评不是 verdict)。
    """
    mapping: dict[int, int] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        number = step.get("step")
        group = step.get("review_group")
        if (
            isinstance(number, int)
            and not isinstance(number, bool)
            and isinstance(group, int)
            and not isinstance(group, bool)
        ):
            mapping[number] = group
    return mapping


def latest_complete_group(
    steps: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    replies: list[dict[str, Any]],
    plan_id: str,
) -> dict[str, Any] | None:
    """最新一个**完整**组(每个成员 step 都有带 verdict 的回复)。

    返回 {group, members: [{step, agent_id, reply_id, verdict}],
    last_reply_id}。组未齐返回 None(调用方据此不触发迭代);plan 无
    组标记时也返回 None(调用方回落单 reviewer 路径)。
    """
    groups = review_group_numbers(steps)
    if not groups:
        return None
    steps_by_number = {
        step.get("step"): step for step in steps if isinstance(step, dict)
    }
    approval_by_step = {
        approval.get("step"): approval
        for approval in approvals
        if isinstance(approval, dict) and approval.get("plan_id") == plan_id
    }
    reply_by_message: dict[str, dict[str, Any]] = {}
    reply_order: dict[str, int] = {}
    for index, reply in enumerate(replies):
        if not isinstance(reply, dict) or not isinstance(reply.get("verdict"), dict):
            continue
        message_id = str(reply.get("message_id"))
        reply_by_message[message_id] = reply
        reply_order[message_id] = index
    by_group: dict[int, list[int]] = {}
    for number, group in groups.items():
        by_group.setdefault(group, []).append(number)
    for group in sorted(by_group, reverse=True):
        members: list[dict[str, Any]] = []
        last_index = -1
        for number in sorted(by_group[group]):
            approval = approval_by_step.get(number)
            message_id = str((approval or {}).get("message_id"))
            reply = reply_by_message.get(message_id)
            if reply is None:
                members = []
                break
            members.append({
                "step": number,
                "agent_id": (steps_by_number.get(number) or {}).get("agent_id"),
                "reply_id": reply.get("reply_id"),
                "verdict": reply["verdict"],
            })
            last_index = max(last_index, reply_order.get(message_id, -1))
        if members:
            return {
                "group": group,
                "members": members,
                "last_reply_id": members[-1]["reply_id"],
                "last_reply_index": last_index,
            }
    return None


def aggregate_group_verdicts(members: list[dict[str, Any]]) -> dict[str, Any]:
    """any-fail-blocks:overall 取最严;criteria 逐条合并(任一 fail 即
    fail,否则任一 unknown 即 unknown,全 pass 才 pass)。"""
    overall = "pass"
    merged: dict[str, str] = {}
    order: list[str] = []
    scores: list[int] = []
    for member in members:
        verdict = member.get("verdict") or {}
        value = str(verdict.get("overall"))
        if _SEVERITY.get(value, 2) > _SEVERITY.get(overall, 0):
            overall = value
        score = verdict.get("score")
        if isinstance(score, int) and not isinstance(score, bool):
            scores.append(score)
        for item in verdict.get("criteria") or []:
            if not isinstance(item, dict):
                continue
            criterion = str(item.get("criterion"))
            current = merged.get(criterion)
            incoming = str(item.get("verdict"))
            if criterion not in merged:
                order.append(criterion)
                merged[criterion] = incoming
            elif current != "fail" and (incoming == "fail" or current == "pass"):
                merged[criterion] = incoming
    return {
        "overall": overall,
        "rule": REVIEW_GROUP_RULE,
        "criteria": [
            {"criterion": criterion, "verdict": merged[criterion]}
            for criterion in order
        ],
        "score": min(scores) if scores else None,
    }
