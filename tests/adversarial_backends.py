"""不配合的 runtime backend —— 让测试有能力失败。

2026-08-05 一次真实的接力探针(三个 agent 轮流背《出师表》)一晚上挖出五个
bug,而当时 5306 条测试**一条都没抓到**。原因不在数量,在桩:

    class CorrelatedFakeBackend:
        def pane_exists(...):    return True          # pane 永远在
        def capture_output(...): return <完美的回复块>  # 发出去必到、格式必对

它模拟的是一个从不出错的世界。真实世界里 pane 会中途消失、会还没启动完就被
打字、TUI 会清掉滚动区、长值会折到下一行、回复会迟到。这些不是边缘情况,是
**当晚每一次真跑都会遇到的常态**。

本模块把那五次故障固化成一个可参数化的桩。每个开关都写明它复现的是哪一次
现场故障——这样后来的人知道它为什么在这儿,不会顺手把它"简化"掉。

它只描述 runtime 的不配合,不放宽任何被测行为:断言仍然要求 AgentDeck 做对
的事(要么完成,要么如实报告失败),绝不接受"静默地不工作"。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


# 首次目录信任框:它占住键盘,此刻发进去的任何按键都会被它吃掉。
# 逐字取自真实 codex 首启屏(活动选择器字形是"框还活着"的正证明)。
TRUST_DIALOG_SCREEN = "\n".join(
    [
        "  You are running Codex in ~/Desktop/demo",
        "",
        "  Do you trust the contents of this directory?",
        "",
        "› 1. Yes, proceed (y)",
        "  2. No, exit",
    ]
)


DEFAULT_REPLY_FIELDS = {
    "status": "completed",
    "summary": "done",
    "verification": "checked",
    "risks": "none",
    "next_steps": "continue",
}


def reply_block(token: str, *, wrap_field: str | None = None, **overrides: str) -> str:
    """构造一份结构化回复。

    `wrap_field` 复现 2026-08-05 现场:claude 把长句写成 `summary:` 换行再写
    内容。解析器当时逐行读 `key: value`,于是该字段读成空串,整份**完全正确**
    的回复被丢掉,运行器等满超时报 timed_out——屏幕上躺着答复,账本说没收到。
    """
    fields = {**DEFAULT_REPLY_FIELDS, **overrides}
    lines = [f"handoff_token: {token}"]
    for key, value in fields.items():
        if key == wrap_field:
            lines.append(f"{key}:")
            lines.append(value)
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def reply_path_from_prompt(prompt: str) -> Path | None:
    """从派发提示词里取出回复文件路径——worker 就是这么知道往哪写的。"""
    # 按**内容**找,不靠位置:提示词末尾还跟着"通道优先于任务限制"那句声明,
    # 靠"最后一行"会取到那句话。真实 worker 也是认路径本身,不是数行号。
    for line in reversed([line.strip() for line in prompt.splitlines()]):
        if line.endswith(".reply.txt"):
            return Path(line)
    return None


def token_from_prompt(prompt: str) -> str | None:
    for line in prompt.splitlines():
        if line.startswith("Complete only this task. Use this handoff token exactly:"):
            return line.rsplit(":", 1)[1].strip()
    return None


class ScriptedPaneBackend:
    """一个会以真实方式失灵的 tmux backend。

    每个开关对应一次 2026-08-05 现场故障:

    | 开关 | 复现的现场 |
    | --- | --- |
    | `vanish_after` | tmux session 整个退出,run 停在 `pane_lost` |
    | `dialog_open` | 模态框占住键盘,发进去的按键被吃掉(框由人关,不会自己消失) |
    | `scrollback_cleared` | 真实 agent TUI 清滚动区,pane 刮不出任何东西 |
    | `wrap_field` | 长值折到下一行 |
    | `reply_delay_polls` | 回复迟到——worker 在想,不是没收到 |

    `deliver_via` 决定回复走哪条通道:`"file"`(worker 写文件,可靠)或
    `"pane"`(旧路径,靠刮屏)。
    """

    def __init__(
        self,
        *,
        vanish_after: int | None = None,
        dialog_open: bool = False,
        scrollback_cleared: bool = False,
        wrap_field: str | None = None,
        reply_delay_polls: int = 0,
        deliver_via: str = "file",
    ) -> None:
        if deliver_via not in {"file", "pane"}:
            raise ValueError("deliver_via must be 'file' or 'pane'")
        self.vanish_after = vanish_after
        self.dialog_open = dialog_open
        self.scrollback_cleared = scrollback_cleared
        self.wrap_field = wrap_field
        self.reply_delay_polls = reply_delay_polls
        self.deliver_via = deliver_via
        self.sent: list[tuple[str, str]] = []
        self.swallowed: list[tuple[str, str]] = []
        self.polls = 0
        self._pane_replies: dict[str, str] = {}

    # ---- RuntimeBackend 接口 ----

    def pane_exists(self, _config: Any, _pane_id: str) -> bool:
        if self.vanish_after is None:
            return True
        return len(self.sent) < self.vanish_after

    def send_input(self, _config: Any, pane_id: str, text: str) -> None:
        if self.dialog_open:
            # 按键被吃掉:tmux **不报错**,`send_input` 正常返回,keystroke 就是
            # 没了。2026-08-04 现场如此——任务丢失,审批却被记成 `dispatched`,
            # 宿主等了五十分钟。这也说明"message_id 是不是 None"盖不住这一类:
            # 记录建得好好的,只是那句话从没到达。
            self.swallowed.append((pane_id, text))
            return
        self.sent.append((pane_id, text))
        token = token_from_prompt(text)
        if token is None:
            return
        block = reply_block(token, wrap_field=self.wrap_field)
        if self.deliver_via == "file":
            path = reply_path_from_prompt(text)
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(block, encoding="utf-8")
                return
        self._pane_replies[pane_id] = block

    def capture_output(self, _config: Any, pane_id: str, lines: int = 200) -> str:
        self.polls += 1
        if self.dialog_open:
            # 按键会被吃掉的时候,屏幕上**是有东西**的:一个占住键盘的模态框。
            # 这就是派发前该看见的证据——2026-08-04 那次没人看,于是打了进去。
            return TRUST_DIALOG_SCREEN
        if self.scrollback_cleared:
            return "the agent TUI cleared its scrollback; nothing scrapeable here"
        if self.polls <= self.reply_delay_polls:
            return "• working (esc to interrupt)"
        return self._pane_replies.get(pane_id, "no output yet")
