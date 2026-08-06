"""一个 ACP turn 的取证记录 —— 账本之外的旁路文件。

**为什么需要它**：账本里已经有权威结论——派发的任务原文、结构化回复
（summary / verification / risks / next_steps）、产物路径与 hash、复审判定、
以及 worker 自己在任务分支上的 git commit。「它把代码改成了什么」由 git 回答，
比任何 transcript 都准。

缺的是**中间过程**：它调了哪个工具、跑了什么命令、读了什么、说了什么。
2026-08-05 一晚挖出的九个 bug，每一个都是靠手动还原现场查出来的——任务发断了、
答案被 TUI 清屏冲掉了、回车跑到粘贴前面。真实场景里没人在旁边看：你设几百个
wave 让它跑一夜，第 47 步出问题时你在睡觉，第二天只能靠留下来的东西查。

**为什么不写进 `state.json`**：账本每次全量重写，塞进流式 transcript 会让它无限
膨胀；而且 agent 的输出里可能有它读过的源码与凭据。仓库那条「绝不留存 provider
原文」的纪律管的是**账本**。放到一份可以整目录删掉的旁路文件里，纪律不破，取证
能力有了。这个模式仓库里已有两处先例：`run-loop-host` 的 append-only `host.log`，
以及 worker 回复的文件通道。

三条硬性质：

- **有界**：单条封顶。截断必须**在文件里说出来**（`truncated` / `original_bytes`），
  绝不静默截断——悄悄截断会让人以为自己看到了全部。
- **可删**：整个 `.agentdeck/transcripts/` 删掉不影响 `state.json` 的完整性。
- **写失败不影响运行**：取证记录写不下去是磁盘的事，不该让 worker 的活失败。
  失败只置 `write_failed`，由调用方决定要不要说。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import utc_now

# 单条上限。工具结果可能带整个文件内容，必须封顶，否则一次 `cat` 就能撑爆磁盘。
MAX_TRANSCRIPT_LINE_BYTES = 16 * 1024

TRANSCRIPT_DIRNAME = "transcripts"


def _encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")


class AcpTranscript:
    """一个 turn 一份 append-only JSONL。"""

    def __init__(self, root: str | Path, *, message_id: str, agent_id: str) -> None:
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("message_id must be a non-empty string")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        self.path = (
            Path(root) / ".agentdeck" / TRANSCRIPT_DIRNAME / f"{message_id}.jsonl"
        )
        self.agent_id = agent_id
        self.message_id = message_id
        self.write_failed = False
        self._started = False

    def _write(self, record: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # 取证记录写不下去，不该让 worker 的活失败。
            self.write_failed = True

    def _ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        self._write(
            {
                "event": "start",
                "agent_id": self.agent_id,
                "message_id": self.message_id,
                "at": utc_now(),
            }
        )

    def append(self, sequence: int, kind: str, payload: dict[str, Any]) -> None:
        self._ensure_started()
        encoded = _encode(payload)
        truncated = len(encoded) > MAX_TRANSCRIPT_LINE_BYTES
        record: dict[str, Any] = {
            "event": "update",
            "sequence": sequence,
            "kind": kind,
            "at": utc_now(),
            "truncated": truncated,
        }
        if truncated:
            # 留下**可读的开头**而不是整段丢掉：查错时前几百字节往往就够了。
            record["original_bytes"] = len(encoded)
            record["payload_head"] = encoded[:MAX_TRANSCRIPT_LINE_BYTES].decode(
                "utf-8", errors="replace"
            )
        else:
            record["payload"] = payload
        self._write(record)

    def finish(self, stop_reason: str | None) -> None:
        self._ensure_started()
        self._write({"event": "finish", "stop_reason": stop_reason, "at": utc_now()})
