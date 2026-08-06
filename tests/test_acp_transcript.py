"""ACP turn 的取证记录。

设计场景**不是**让几个 agent 轮流报数——那些只是验能力的探针。真实场景是:
你设了几百个 wave 让它跑一夜,第 47 步出了问题,你在睡觉。第二天你要问的是
「它当时到底做了什么、为什么错了」。

账本里已经有权威结论(任务原文、结构化回复、产物 hash、复审判定、worker 自己的
git commit)。缺的是**中间过程**:它调了哪个工具、读了什么、说了什么。
2026-08-05 那九个 bug,每一个都是靠手动还原现场查出来的——那正是这份记录的用途。

三条硬性质:
- **有界**:单条与整轮都封顶,截断必须**在文件里说出来**,绝不静默截断。
- **可删**:写在账本之外的旁路文件里。删掉它不影响 `state.json` 的完整性。
- **写失败不影响运行**:取证记录写不下去是磁盘的事,不该让 worker 的活失败。
"""
from __future__ import annotations

import json
from pathlib import Path

from agentdeck.acp_transcript import AcpTranscript, MAX_TRANSCRIPT_LINE_BYTES


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_transcript_records_what_the_worker_actually_did(tmp_path) -> None:
    t = AcpTranscript(tmp_path, dispatch_key="dsp_" + "a" * 32, agent_id="coder")

    t.append(0, "text", {"role": "agent", "content": {"type": "text", "text": "开始改 README"}})
    t.append(1, "tool_call", {"title": "Bash", "kind": "execute", "raw_input": {"command": "pytest -q"}})
    t.append(2, "tool_result", {"title": "Bash", "raw_output": "2 failed"})

    rows = _lines(t.path)
    assert rows[0]["event"] == "start"
    assert rows[0]["agent_id"] == "coder"
    assert [r["kind"] for r in rows[1:]] == ["text", "tool_call", "tool_result"]
    # 工具调用必须留下**它到底跑了什么**——那是取证的核心。
    assert rows[2]["payload"]["raw_input"]["command"] == "pytest -q"
    assert rows[1]["payload"]["content"]["text"] == "开始改 README"


def test_an_oversized_payload_is_truncated_out_loud(tmp_path) -> None:
    """截断必须说出来。悄悄截断会让人以为自己看到了全部。"""
    t = AcpTranscript(tmp_path, dispatch_key="dsp_" + "b" * 32, agent_id="coder")

    t.append(0, "tool_result", {"raw_output": "x" * (MAX_TRANSCRIPT_LINE_BYTES * 2)})

    row = _lines(t.path)[1]
    assert row["truncated"] is True
    assert row["original_bytes"] > MAX_TRANSCRIPT_LINE_BYTES
    assert len(json.dumps(row, ensure_ascii=False).encode("utf-8")) <= MAX_TRANSCRIPT_LINE_BYTES * 2


def test_a_normal_line_is_not_marked_truncated(tmp_path) -> None:
    t = AcpTranscript(tmp_path, dispatch_key="dsp_" + "c" * 32, agent_id="coder")

    t.append(0, "text", {"role": "agent", "content": {"type": "text", "text": "短"}})

    assert _lines(t.path)[1].get("truncated") is False


def test_a_write_failure_never_breaks_the_run(tmp_path) -> None:
    """取证记录写不下去是磁盘的事,不该让 worker 的活失败。"""
    t = AcpTranscript(tmp_path, dispatch_key="dsp_" + "d" * 32, agent_id="coder")
    t.path.parent.mkdir(parents=True, exist_ok=True)
    t.path.write_text("", encoding="utf-8")
    t.path.chmod(0o400)
    try:
        t.append(0, "text", {"role": "agent", "content": {"type": "text", "text": "hi"}})
    finally:
        t.path.chmod(0o600)
    assert t.write_failed is True


def test_the_file_lives_outside_the_ledger(tmp_path) -> None:
    """删掉整个目录不该影响 state.json——这是它能被安心写下去的前提。"""
    t = AcpTranscript(tmp_path, dispatch_key="dsp_" + "e" * 32, agent_id="coder")

    assert t.path.parent == tmp_path / ".agentdeck" / "transcripts"
    assert "state" not in t.path.parts
