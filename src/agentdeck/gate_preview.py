"""One-key decision from a human gate: derive the authorization-box grant ladder.

This module is **pure**: zero IO, zero LLM, no tmux, no state writes, no
provider calls. It does not import `cli`, `state` or `config` — every input is
plain data, so the whole ladder matrix can be tested without a project on disk.

The pivot of the design (see
`docs/superpowers/specs/2026-08-03-delegation-gate-preview-design.md`):
**the width of a delegation must be chosen by a human, never by AgentDeck.**
For one and the same box the authorized width differs by orders of magnitude::

    .../playwright_cli.sh open file:///…/index.html   → only this one file
    .../playwright_cli.sh open                        → any file
    .../playwright_cli.sh                             → navigate / fill / evaluate too

So this module lays out a *deterministic ladder* of candidates and annotates
what each one leaves unpinned — and **recommends none of them**. Nothing here
sorts by "safety", marks a preferred entry, or picks a default: the ladder is
ordered narrowest-first purely because that is the order the prefixes shrink in.

MCP boxes have no ladder at all: one grant covers exactly one `(server, tool)`
pair by exact equality, so there is no width to trade off.

One refusal is deliberate (it is in the spec's non-goals): this module does
**not** pattern-match dangerous commands (`push` / `install` / `curl` …). A
partial detector would make "no warning" read as "safe", which is precisely the
class of displayed-fact-that-does-not-hold this project has spent many slices
eliminating. Instead `VERIFICATION_NOTICE` states plainly, once, that AgentDeck
cannot verify what a command does and that the absence of a warning means
nothing.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

GATE_PREVIEW_MODE = "delegation_gate_preview"

# 两条只读证据来源:宿主记录(缺省,零 pane 读取)与一次实时只读框扫描
# (`--follow` 不写宿主记录,它的用户只能走这条)。
GATE_PREVIEW_SOURCES = ("host_record", "agent_scan")

# 梯子上限:再多人就不看了,而"看不看得完"正是这条命令唯一的价值。
GATE_PREVIEW_LADDER_CAP = 5

GATE_PREVIEW_CANDIDATE_FIELDS = (
    "index",
    "prefix",
    # 该前缀**没有**钉住的那一段——即"这部分可以是任何东西"。
    "unpinned_tail",
    # 是否是裸单 token(最大宽度)。这是事实陈述,不是评级。
    "is_widest",
    "grant_command",
)

GATE_PREVIEW_CONTROL_FIELDS = (
    "kind",
    "label",
    "command",
    "safety",
    "enabled",
    "blocker",
)

VERIFICATION_NOTICE = (
    "AgentDeck 无法核验一条命令的性质。打开本地页面看似只是观察,"
    "但同一个脚本也能导航、填表、执行脚本。前缀越短,授权越宽。"
    "本命令不判断、不排序、不执行任何一条——没有警告不代表无害。"
)

_TOKEN = re.compile(r"\S+")


def _quote(value: str) -> str:
    """把前缀原文变成可直接粘贴的一个 shell 参数。"""
    return shlex.quote(value)


def _grant_command(agent_id: str, prefix: str) -> str:
    return (
        f"agentdeck delegation grant --agent {agent_id} "
        f"--prefix {_quote(prefix)} --confirm"
    )


def _mcp_grant_command(agent_id: str, server: str, tool: str) -> str:
    return (
        f"agentdeck delegation grant --agent {agent_id} "
        f"--mcp-server {server} --mcp-tool {tool} --confirm"
    )


def release_box_command(agent_id: str) -> str:
    """授权之后这道框仍需显式放行一次——它自身的既有门一字不动。"""
    return f"agentdeck agent release-box --agent {agent_id} --confirm"


def _ladder_levels(token_count: int, cap: int) -> list[int]:
    """从最窄(全部 token)到最宽(1 个 token)取至多 `cap` 级,首尾必在。

    确定性:级数完全由 token 数与 cap 决定,不看命令内容——内容一旦参与
    取舍,这条命令就开始替人做宽度判断了。
    """
    levels = list(range(token_count, 0, -1))
    if token_count <= cap:
        return levels
    last = len(levels) - 1
    picked: list[int] = []
    for step in range(cap):
        # 整数化的均匀取样(+0.5 四舍五入),两端恒被取到。
        index = int(step * last / (cap - 1) + 0.5)
        level = levels[index]
        if level not in picked:
            picked.append(level)
    return picked


def prefix_ladder(
    command: str | None, cap: int = GATE_PREVIEW_LADDER_CAP, agent_id: str = "<agent_id>"
) -> list[dict[str, Any]]:
    """由一条命令推出逐级变宽的前缀候选(最窄在前)。

    前缀按**原文切片**产生,不是 `" ".join(tokens)`:命令里的多重空格必须
    原样保留,否则得到的前缀 `startswith` 不上屏上那条真命令。
    """
    if not command:
        return []
    spans = [match.span() for match in _TOKEN.finditer(command)]
    if not spans:
        return []
    candidates: list[dict[str, Any]] = []
    for level in _ladder_levels(len(spans), cap):
        end = spans[level - 1][1]
        prefix = command[:end]
        candidates.append(
            {
                "index": len(candidates) + 1,
                "prefix": prefix,
                "unpinned_tail": command[end:].strip(),
                "is_widest": level == 1,
                "grant_command": _grant_command(agent_id, prefix),
            }
        )
    return candidates


def derive_gate_preview(
    *,
    agent_id: str,
    box_kind: str | None,
    command: str | None,
    mcp_server: str | None,
    mcp_tool: str | None,
    waiting_hint: str | None,
    source: str,
) -> dict[str, Any]:
    """把一道框的证据摊开成完整的两步闭环——**只是文本,不执行任何一步**。"""
    if source not in GATE_PREVIEW_SOURCES:
        raise ValueError(f"unknown gate-preview source: {source}")
    is_mcp = box_kind == "mcp_tool" and mcp_server and mcp_tool
    candidates = [] if is_mcp else prefix_ladder(command, agent_id=agent_id)
    if is_mcp:
        grant_command = _mcp_grant_command(agent_id, str(mcp_server), str(mcp_tool))
        grant_enabled = True
        grant_blocker = None
    else:
        # 含占位符 → control 必须 disabled。这不是 UI 洁癖:宽度只能由人
        # 从上面那梯候选里挑一条,程序永不代填。
        grant_command = (
            f"agentdeck delegation grant --agent {agent_id} --prefix <prefix> --confirm"
        )
        grant_enabled = False
        grant_blocker = "requires a prefix chosen by a human from the ladder above"
    controls = [
        {
            "kind": "grant",
            "label": "Grant a delegation",
            "command": grant_command,
            "safety": "explicit_user",
            "enabled": grant_enabled,
            "blocker": grant_blocker,
        },
        {
            "kind": "release_box",
            "label": "Release this box once",
            "command": release_box_command(agent_id),
            "safety": "explicit_runtime",
            "enabled": True,
            "blocker": None,
        },
        {
            "kind": "inspect",
            "label": "List delegations",
            "command": "agentdeck delegation list",
            "safety": "inspect",
            "enabled": True,
            "blocker": None,
        },
    ]
    return {
        "ok": True,
        "mode": GATE_PREVIEW_MODE,
        "source": source,
        "agent_id": agent_id,
        "box_kind": box_kind,
        "command": command,
        "mcp_server": mcp_server,
        "mcp_tool": mcp_tool,
        "waiting_hint": waiting_hint,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "grant_command": grant_command,
        "release_command": release_box_command(agent_id),
        "verification_notice": VERIFICATION_NOTICE,
        "controls": controls,
    }


_SOURCE_LABELS = {
    "host_record": "走开段的宿主记录",
    "agent_scan": "一次实时只读框扫描",
}


def _wrap_notice(notice: str, indent: str, width: int = 34) -> list[str]:
    """按中文标点软换行,免得一段风险说明糊成一坨没人读。"""
    lines: list[str] = []
    current = ""
    for char in notice:
        current += char
        if char in "。;" and len(current) >= width:
            lines.append(current)
            current = ""
    if current:
        lines.append(current)
    return [f"{indent}{line}" for line in lines]


def render_gate_preview(payload: dict[str, Any]) -> str:
    """人类可读渲染。绝不推荐任何一条候选,也绝不因命令看起来无害就少说一句。"""
    agent_id = payload["agent_id"]
    lines = [
        f"{agent_id} 卡在一道未委托的授权框上"
        f"(证据来自{_SOURCE_LABELS.get(str(payload['source']), payload['source'])}):"
    ]
    if payload["box_kind"] == "mcp_tool":
        lines.append(f"  MCP  {payload['mcp_server']} / {payload['mcp_tool']}")
    elif payload["command"]:
        lines.append(f"  $ {payload['command']}")
    else:
        lines.append("  (框身份解析不出来——去那个 pane 自己看一眼)")
    if payload["waiting_hint"]:
        lines.append(f"    {payload['waiting_hint']}")
    lines.append("")
    candidates = payload["candidates"]
    if candidates:
        lines.append("若你决定授权这一类,可选的前缀(越往下越宽,自己挑一条):")
        lines.append("")
        for candidate in candidates:
            lines.append(f"  {candidate['index']}) {candidate['prefix']}")
            tail = candidate["unpinned_tail"]
            if not tail:
                covered = "(无——仅此一条命令)"
            elif candidate["is_widest"]:
                covered = f"任意子命令与参数(本次是 <{tail}>)"
            else:
                covered = f"任意 <{tail}> 的位置"
            marker = "  ⚠ 最宽" if candidate["is_widest"] else ""
            lines.append(f"     连带授权: {covered}{marker}")
        lines.append("")
        lines.append("  把选定的那一条填进去:")
        lines.append(f"  {payload['grant_command']}")
    else:
        lines.append("这道框没有宽度可调——一条委托恰好覆盖一个 (server, tool):")
        lines.append(f"  {payload['grant_command']}")
    lines.append("")
    lines.append("授权之后,这道框仍需显式放行一次:")
    lines.append(f"  {payload['release_command']}")
    lines.append("")
    lines.append("⚠ " + _wrap_notice(payload["verification_notice"], "")[0])
    lines.extend(_wrap_notice(payload["verification_notice"], "   ")[1:])
    return "\n".join(lines)
