"""Decide whether a pane can actually receive a dispatched task right now.

2026-08-04, live: three panes were spawned, `agent ready` reported
`all_running: True, 3/3`, and `goal start` dispatched into them. The keystrokes
went into a first-run directory-trust dialog instead of a composer. The task was
lost; the approval was recorded as `dispatched` anyway; the host then waited
**300 waves — fifty minutes** for a reply that could never arrive, and stopped
with `budget_exhausted`. No box was ever detected, because by then there was
nothing on screen but an idle prompt: `human_gate` had nothing to find.

That failure is worse than the trust box it grew out of. A box can be seen and
reported; this was silence. So the check belongs **before** the send, not after:
once keystrokes leave, they cannot be recalled, and this repository's own rule
is that a contract is validated before the effect, never after.

Three states:

| state | meaning |
| --- | --- |
| `receptive` | a normal composer — a dispatch will land where it is meant to |
| `blocked` | a live dialog owns the keyboard; anything sent now is eaten |
| `unverifiable` | the pane could not be read |

`unverifiable` deliberately does **not** block. A failed capture is ordinary
runtime jitter that this codebase already treats as jitter elsewhere, and
turning a read failure into a refused dispatch would stop healthy work on a
hiccup. It equally must not be reported as `receptive`: that would state a fact
nobody established. It is its own answer, and the caller decides.

The liveness test is the active selector glyph (`> 1.` / `❯ 1.`), the same
positive proof the box extractor and the trust detector already rely on: an
answered dialog folds into scrollback and stops rendering it, so history alone
must never block a dispatch. Only the tail is examined, for the same reason.

Pure module: zero IO, zero LLM, no tmux, no subprocess; it does not import
`cli`, `state` or `config`. The caller captures the pane and passes the text in.
Nothing here sends, records, or authorizes anything — it only answers whether
sending would land.
"""
from __future__ import annotations

import re
from typing import Any

RECEPTIVE_STATES = ("receptive", "blocked", "unverifiable")

RECEPTIVE_REASONS = (
    # 首次目录信任框:按键会被它吃掉,而且它**永远**不该由程序代按。
    "directory_trust",
    # 待批授权框(或其它活动编号对话框)同理。
    "pending_box",
    # 读不出来——不是"被拦住",是"没查成"。
    "pane_unreadable",
)

# 只看尾窗:挂起的对话框一定停在画面底部,滚动历史里的旧框不该拦住派发。
_RECEPTIVE_TAIL_LINES = 20

# "对话框还活着"的正证明。注意 codex 的普通输入行也以 `› ` 开头,所以必须
# 要求其后紧跟编号 `1.`——只认 `›` 会把正常的 composer 判成对话框。
_ACTIVE_SELECTOR = re.compile(r"[›❯]\s*1\.")

_TRUST_MARKERS = (
    "Do you trust the contents of this directory",
    "Is this a project you created or one you trust",
)


def classify_pane_receptive(*, pane_text: str | None) -> dict[str, Any]:
    """此刻往这个 pane 发送任务,会落到该落的地方吗。

    `pane_text` 为 `None` 表示读取失败——它既不是 `blocked` 也不是
    `receptive`,把两者中任何一个安在它头上都是在陈述没确立的事实。
    """
    if pane_text is None:
        return {"state": "unverifiable", "reason": "pane_unreadable"}
    tail = pane_text.splitlines()[-_RECEPTIVE_TAIL_LINES:]
    if not any(_ACTIVE_SELECTOR.search(line) for line in tail):
        return {"state": "receptive", "reason": None}
    folded = " ".join(" ".join(tail).split())
    if any(marker in folded for marker in _TRUST_MARKERS):
        return {"state": "blocked", "reason": "directory_trust"}
    # 认得出是活动对话框、但认不出是哪一种:仍然拦住。此刻发进去的按键一样
    # 会被吃掉,而"拦错一次"的代价远小于"任务静默丢失五十分钟"。
    return {"state": "blocked", "reason": "pending_box"}
