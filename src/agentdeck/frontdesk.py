"""Deterministic frontdesk intake classification.

G1 承诺前台把用户原话分类为 plan/run/status/help/skill/memory 等候选路径,
并给出显式下一步命令。本模块是那份分类的**纯实现**:零 IO、不 import
cli/state/config、不查 state、不读 tmux、绝不调用任何 provider。匹配一律
只读文本,置信度是确定性小档位(强措辞 = high、弱线索 = medium、
兜底 = low),不是模型打分;`rationale` 只说命中了哪个词。

候选命令只是**建议文本**,不是授权:调用方仍必须由人类显式执行。
"""
from __future__ import annotations

import re
import shlex

# 闭合路由枚举。顺序同时是同置信度候选的确定性排序依据。
FRONTDESK_ROUTES = ("plan", "run", "status", "help", "skill", "memory")

# 置信度是确定性小档位,从高到低。
FRONTDESK_CONFIDENCES = ("high", "medium", "low")

FRONTDESK_CANDIDATE_FIELDS = ("route", "label", "command", "confidence", "rationale")

# 每条候选命令自身的 safety 等级,供卡面渲染 control 使用;它描述命令性质,
# 不是执行授权。
FRONTDESK_ROUTE_SAFETY = {
    "plan": "plan_only",
    "run": "approval_gated",
    "status": "inspect",
    "help": "inspect",
    "skill": "inspect",
    "memory": "inspect",
}

_ROUTE_LABELS = {
    "plan": "Create Leader plan",
    "run": "Start approval-gated run",
    "status": "Inspect project status",
    "help": "Open Leader help",
    "skill": "List skills",
    "memory": "Review memory suggestions",
}

_HELP_COMMAND = 'agentdeck leader chat --message "帮助"'

_ROUTE_COMMANDS = {
    "status": "agentdeck status",
    "help": _HELP_COMMAND,
    "skill": "agentdeck skills list",
    "memory": "agentdeck memory suggestions",
}

# 强措辞(high)与弱线索(medium)。匹配按声明顺序取第一个命中的词,
# 使 rationale 确定。
_STRONG_MARKERS = {
    "plan": ("规划", "计划", "设计", "拆解", "架构", "plan"),
    "run": ("开始运行", "开始执行", "开始跑", "/run"),
    "status": ("状态", "进度", "看板", "status", "progress"),
    "help": ("帮助", "help", "能做什么", "有哪些能力", "命令面板"),
    "skill": ("技能", "skill"),
    "memory": ("记忆", "memory", "记住"),
}

_WEAK_MARKERS = {
    "plan": (),
    "run": ("跑一下", "运行一下", "执行一下", "跑起来"),
    "status": ("现在怎么样", "怎么样了", "现在如何"),
    "help": ("怎么用", "不知道怎么"),
    "skill": ("工作流", "workflow"),
    "memory": ("别忘了", "不要忘"),
}

_GOAL_ONLY_RATIONALE = "goal text present without an explicit planning keyword"
_FALLBACK_RATIONALE = "no route keyword matched"

_FRONTDESK_PREFIX = re.compile(r"^/?frontdesk[:：\s-]*", re.IGNORECASE)

_FRONTDESK_TOKENS = (
    "前台接待",
    "前台处理",
    "前台路由",
    "帮我梳理需求",
    "帮我澄清需求",
    "梳理需求",
    "澄清需求",
)


def frontdesk_goal(message: str) -> str:
    """Strip the frontdesk trigger prefix and return the remaining goal text.

    与 `cli._frontdesk_goal_from_message` 同一份规则(CLI 直接复用本函数),
    因此既有卡面的 `intake_summary` / `next_command` 取值逐字节不变。
    """
    text = message.strip()
    text = _FRONTDESK_PREFIX.sub("", text).strip()
    for token in _FRONTDESK_TOKENS:
        if text.startswith(token):
            text = text[len(token) :].strip(" ：:-")
    return text


def _first_marker(haystack: str, markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        if marker in haystack:
            return marker
    return None


def _hit(haystack: str, route: str) -> tuple[str, str] | None:
    """Return (confidence, matched marker) for a route, or None when it misses."""
    marker = _first_marker(haystack, _STRONG_MARKERS[route])
    if marker is not None:
        return "high", marker
    marker = _first_marker(haystack, _WEAK_MARKERS[route])
    if marker is not None:
        return "medium", marker
    return None


def _candidate(route: str, command: str, confidence: str, rationale: str) -> dict[str, object]:
    return {
        "route": route,
        "label": _ROUTE_LABELS[route],
        "command": command,
        "confidence": confidence,
        "rationale": rationale,
    }


def classify_frontdesk(message: str) -> list[dict[str, object]]:
    """Classify one intake message into deterministic route candidates.

    Returns candidates ordered by confidence descending; ties keep the
    `FRONTDESK_ROUTES` declaration order. Pure text matching only — it never
    reads state, plans, approvals, tmux, or a provider.
    """
    text = message if isinstance(message, str) else ""
    haystack = text.lower()
    goal = frontdesk_goal(text)
    has_goal = bool(goal)

    candidates: list[dict[str, object]] = []
    for route in FRONTDESK_ROUTES:
        hit = _hit(haystack, route)
        if route == "plan":
            # 只有可提取目标时才推荐规划路径。
            if not has_goal:
                continue
            confidence, rationale = (
                (hit[0], f'matched "{hit[1]}"') if hit is not None else ("medium", _GOAL_ONLY_RATIONALE)
            )
            command = f"agentdeck leader plan --task {shlex.quote(goal)}"
        elif route == "run":
            # 启动语气且可提取目标,二者缺一不推荐运行路径。
            if hit is None or not has_goal:
                continue
            confidence, rationale = hit[0], f'matched "{hit[1]}"'
            command = f"agentdeck run --task {shlex.quote(goal)}"
        else:
            if hit is None:
                continue
            confidence, rationale = hit[0], f'matched "{hit[1]}"'
            command = _ROUTE_COMMANDS[route]
        candidates.append(_candidate(route, command, confidence, rationale))

    if not candidates:
        return [_candidate("help", _HELP_COMMAND, "low", _FALLBACK_RATIONALE)]

    rank = {level: index for index, level in enumerate(FRONTDESK_CONFIDENCES)}
    candidates.sort(key=lambda item: rank[str(item["confidence"])])
    return candidates
