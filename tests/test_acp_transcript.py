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
    t = AcpTranscript(tmp_path, message_id="msg_aaaaaaaaaaaa", agent_id="coder")

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
    t = AcpTranscript(tmp_path, message_id="msg_bbbbbbbbbbbb", agent_id="coder")

    t.append(0, "tool_result", {"raw_output": "x" * (MAX_TRANSCRIPT_LINE_BYTES * 2)})

    row = _lines(t.path)[1]
    assert row["truncated"] is True
    assert row["original_bytes"] > MAX_TRANSCRIPT_LINE_BYTES
    assert len(json.dumps(row, ensure_ascii=False).encode("utf-8")) <= MAX_TRANSCRIPT_LINE_BYTES * 2


def test_a_normal_line_is_not_marked_truncated(tmp_path) -> None:
    t = AcpTranscript(tmp_path, message_id="msg_cccccccccccc", agent_id="coder")

    t.append(0, "text", {"role": "agent", "content": {"type": "text", "text": "短"}})

    assert _lines(t.path)[1].get("truncated") is False


def test_a_write_failure_never_breaks_the_run(tmp_path) -> None:
    """取证记录写不下去是磁盘的事,不该让 worker 的活失败。"""
    t = AcpTranscript(tmp_path, message_id="msg_dddddddddddd", agent_id="coder")
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
    t = AcpTranscript(tmp_path, message_id="msg_eeeeeeeeeeee", agent_id="coder")

    assert t.path.parent == tmp_path / ".agentdeck" / "transcripts"
    assert "state" not in t.path.parts


def test_transcript_cli_reads_the_tail_and_says_it_did(tmp_path, monkeypatch, capsys) -> None:
    """GUI 只有三个只读端点,读不了任意文件——所以取证记录得由一条只读命令供出。

    它必须**有界**:一轮几百条 update 全吐给界面没有意义,而且查错时最有用的
    永远是尾部。取多少、丢了多少,都要说出来——这与 transcript 自己"截断必须
    出声"是同一条规矩。
    """
    from agentdeck import cli
    from agentdeck.config import write_default_config

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)

    t = AcpTranscript(root, message_id="msg_abc123abc123", agent_id="coder")
    for i in range(30):
        t.append(i, "text", {"role": "agent", "content": {"type": "text", "text": f"第{i}句"}})
    t.finish("end_turn")

    exit_code = cli.main(["transcript", "--message-id", "msg_abc123abc123", "--limit", "5"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "acp_transcript"
    assert payload["message_id"] == "msg_abc123abc123"
    assert payload["agent_id"] == "coder"
    assert len(payload["lines"]) == 5
    # 尾部:最后一条是 finish。
    assert payload["lines"][-1]["event"] == "finish"
    # 丢了多少必须说出来。
    assert payload["total_lines"] == 32
    assert payload["omitted"] == 27


def test_transcript_cli_is_honest_when_there_is_none(tmp_path, monkeypatch, capsys) -> None:
    """没有记录就说没有,不要打印一个空壳让人以为查过了。"""
    from agentdeck import cli
    from agentdeck.config import write_default_config

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)

    exit_code = cli.main(["transcript", "--message-id", "msg_nothinghere"])

    assert exit_code != 0
    assert "msg_nothinghere" in capsys.readouterr().err


def test_worker_card_offers_the_transcript_only_when_one_exists(tmp_path) -> None:
    """按钮要么真能点开东西,要么如实禁用——不给死按钮。

    GUI 侧不改端点白名单:`/api/inspect` 能执行注册表里任何 enabled 的 inspect
    控件,所以取证记录经这条控件暴露就够了。
    """
    from agentdeck.cli import _worker_lifecycle_controls

    with_transcript = _worker_lifecycle_controls(
        trace_command="agentdeck trace --id msg_x",
        inbox_command="agentdeck inbox --agent coder",
        terminal_command="agentdeck agent terminal --agent coder",
        capture_command="agentdeck agent capture --agent coder --lines 200",
        can_capture=False,
        transcript_command="agentdeck transcript --message-id msg_x",
    )
    item = next(c for c in with_transcript if c["kind"] == "transcript")
    assert item["enabled"] is True
    assert item["safety"] == "inspect"
    assert item["command"] == "agentdeck transcript --message-id msg_x"

    without = _worker_lifecycle_controls(
        trace_command=None,
        inbox_command="agentdeck inbox --agent coder",
        terminal_command="agentdeck agent terminal --agent coder",
        capture_command="agentdeck agent capture --agent coder --lines 200",
        can_capture=False,
        transcript_command=None,
    )
    item = next(c for c in without if c["kind"] == "transcript")
    assert item["enabled"] is False
    assert item["blocker"]
