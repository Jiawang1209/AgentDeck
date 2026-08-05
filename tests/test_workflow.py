from __future__ import annotations

from pathlib import Path

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.workflow import (
    authorized_steps,
    build_compact_handoff,
    build_workflow_prompt,
    parse_correlated_reply,
    run_sequential_workflow,
    workflow_plan_hash,
)
from agentdeck.state import StateStore


PLAN = {
    "plan_id": "pln_demo",
    "plan": {
        "steps": [
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planning",
                "task": "Prepare evidence",
                "requires_approval": True,
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "role": "review",
                "task": "Review evidence",
                "requires_approval": True,
            },
        ]
    },
}


def test_workflow_plan_hash_is_deterministic_and_task_sensitive() -> None:
    first = workflow_plan_hash(PLAN)
    second = workflow_plan_hash(PLAN)
    changed = {
        **PLAN,
        "plan": {
            "steps": [
                {**PLAN["plan"]["steps"][0], "task": "Changed"},
                PLAN["plan"]["steps"][1],
            ]
        },
    }

    assert first == second
    assert first.startswith("sha256:")
    assert workflow_plan_hash(changed) != first
    assert [item["task_hash"] for item in authorized_steps(PLAN)] != []


def test_parse_correlated_reply_ignores_stale_token_and_accepts_matching_block() -> None:
    output = """handoff_token: old
status: completed
summary: stale
verification: old
risks: none
next_steps: none

handoff_token: wfr_demo_step_1
status: completed
summary: fresh
verification: pytest
risks: none
next_steps: review
full_output_path: docs/result.md"""

    reply = parse_correlated_reply(output, "wfr_demo_step_1")

    assert reply is not None
    assert reply["status"] == "completed"
    assert reply["summary"] == "fresh"
    assert reply["full_output_path"] == "docs/result.md"


def test_parse_correlated_reply_accepts_codex_tui_bullet_prefix() -> None:
    output = """• handoff_token: wfr_demo_step_1
  status: completed
  summary: 赵钱孙李
  verification: 赵钱孙李
  risks: none
  next_steps: continue"""

    reply = parse_correlated_reply(output, "wfr_demo_step_1")

    assert reply is not None
    assert reply["summary"] == "赵钱孙李"


def test_parse_correlated_reply_accepts_claude_tui_bullet_prefix() -> None:
    output = """⏺ handoff_token: wfr_demo_step_2
  status: completed
  summary: 周吴郑王
  verification: 周吴郑王
  risks: none
  next_steps: continue"""

    reply = parse_correlated_reply(output, "wfr_demo_step_2")

    assert reply is not None
    assert reply["summary"] == "周吴郑王"


def test_parse_correlated_reply_waits_for_matching_partial_block() -> None:
    assert (
        parse_correlated_reply(
            "handoff_token: wfr_demo_step_1\nstatus: completed\nsummary: incomplete",
            "wfr_demo_step_1",
        )
        is None
    )


def test_parse_correlated_reply_rejects_matching_invalid_status() -> None:
    with pytest.raises(ValueError, match="invalid workflow reply status: unknown"):
        parse_correlated_reply(
            """handoff_token: wfr_demo_step_1
status: unknown
summary: invalid
verification: invalid
risks: none
next_steps: none""",
            "wfr_demo_step_1",
        )


def test_workflow_prompt_echo_is_not_a_correlated_reply() -> None:
    token = "wfr_demo_step_1"
    prompt = build_workflow_prompt(
        role="planning",
        role_prompt="Plan carefully.",
        task="Prepare evidence",
        handoff_token=token,
        previous_handoff=None,
    )

    assert parse_correlated_reply(prompt, token) is None


def test_build_compact_handoff_excludes_full_reply_text() -> None:
    handoff = build_compact_handoff(
        step=1,
        agent_id="planner",
        reply={
            "handoff_token": "wfr_demo_step_1",
            "status": "completed",
            "summary": "done",
            "verification": "pytest",
            "risks": "none",
            "next_steps": "review",
            "full_output_path": "docs/result.md",
        },
        reply_id="rep_demo",
        artifact_paths=["docs/result.md"],
    )

    assert handoff == {
        "step": 1,
        "agent_id": "planner",
        "status": "completed",
        "summary": "done",
        "verification": "pytest",
        "risks": "none",
        "next_steps": "review",
        "artifact_paths": ["docs/result.md"],
        "trace_command": "agentdeck trace --id rep_demo",
    }
    assert "text" not in handoff


def test_state_store_records_and_updates_workflow_run(tmp_path) -> None:
    store = StateStore(tmp_path)
    record = store.create_workflow_run(
        plan_id="pln_demo",
        plan_hash="sha256:plan",
        timeout_seconds=30,
        authorized_steps=[
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planning",
                "task": "Do",
                "task_hash": "sha256:task",
            }
        ],
    )

    assert record["run_id"].startswith("wfr_")
    assert record["status"] == "running"
    updated = store.update_workflow_run(
        record["run_id"], status="stopped", stop_reason="timed_out"
    )
    assert updated["status"] == "stopped"
    assert store.workflow_run_by_id(record["run_id"])["stop_reason"] == "timed_out"


class CorrelatedFakeBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        prompt = next(text for sent_pane, text in reversed(self.sent) if sent_pane == pane_id)
        token = next(
            line.rsplit(":", 1)[1].strip()
            for line in prompt.splitlines()
            if line.startswith("Complete only this task. Use this handoff token exactly:")
        )
        return (
            "unrelated pane history that must not be forwarded\n"
            f"handoff_token: {token}\n"
            "status: completed\n"
            f"summary: finished {pane_id}\n"
            "verification: fake backend\n"
            "risks: none\n"
            "next_steps: continue\n"
            f"full_output_path: docs/{pane_id.removeprefix('%')}.md"
        )

    def pane_exists(self, _config, _pane_id: str) -> bool:
        return True


class ToggleReplyBackend(CorrelatedFakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.respond = False

    def capture_output(self, config, pane_id: str, lines: int = 200) -> str:
        if not self.respond:
            return "still working"
        return super().capture_output(config, pane_id, lines)


class SendFailureBackend(CorrelatedFakeBackend):
    def send_input(self, _config, pane_id: str, text: str) -> None:
        raise RuntimeError(f"pane disappeared while sending to {pane_id}")


def test_runner_completes_two_steps_with_compact_correlated_handoff(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config = load_config(root)
    store = StateStore(root)
    state = store.load()
    for agent_id, pane_id in (("planner", "%1"), ("reviewer", "%2")):
        state["agents"][agent_id] = {
            "agent_id": agent_id,
            "pane_id": pane_id,
            "session_name": "agentdeck",
            "cwd": str(root),
            "status": "running",
        }
    store.save(state)
    plan = store.record_plan(
        "Prepare and review evidence",
        "fake",
        "fake-plan",
        PLAN["plan"],
    )
    steps = authorized_steps(plan)
    run = store.create_workflow_run(
        plan_id=str(plan["plan_id"]),
        plan_hash=workflow_plan_hash(plan),
        timeout_seconds=30,
        authorized_steps=steps,
    )
    backend = CorrelatedFakeBackend()

    result = run_sequential_workflow(
        config=config,
        store=store,
        backend=backend,
        run_id=str(run["run_id"]),
        poll_interval=0,
    )

    assert result["status"] == "completed"
    assert len(backend.sent) == 2
    assert backend.sent[0][0] == "%1"
    assert backend.sent[1][0] == "%2"
    assert '"summary": "finished %1"' in backend.sent[1][1]
    assert "unrelated pane history" not in backend.sent[1][1]
    persisted = store.workflow_run_by_id(str(run["run_id"]))
    assert [turn["status"] for turn in persisted["turns"]] == [
        "completed",
        "completed",
    ]
    state = store.load()
    assert len(state["messages"]) == 2
    assert len(state["replies"]) == 2
    assert [event["event_type"] for event in store.all_events()] == [
        "workflow_step_dispatched",
        "workflow_step_completed",
        "workflow_step_dispatched",
        "workflow_step_completed",
        "workflow_completed",
    ]


def test_runner_stops_and_persists_when_send_input_fails(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config = load_config(root)
    store = StateStore(root)
    state = store.load()
    state["agents"]["planner"] = {
        "agent_id": "planner",
        "pane_id": "%1",
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    store.save(state)
    plan = store.record_plan(
        "Prepare and review evidence", "fake", "fake-plan", PLAN["plan"]
    )
    run = store.create_workflow_run(
        plan_id=str(plan["plan_id"]),
        plan_hash=workflow_plan_hash(plan),
        timeout_seconds=30,
        authorized_steps=authorized_steps(plan),
    )

    result = run_sequential_workflow(
        config=config,
        store=store,
        backend=SendFailureBackend(),
        run_id=str(run["run_id"]),
        poll_interval=0,
    )

    assert result["status"] == "stopped"
    assert result["stop_reason"] == "pane_lost"
    assert result["turns"][0]["status"] == "failed"
    assert store.workflow_run_by_id(str(run["run_id"])) == result
    assert [event["event_type"] for event in store.all_events()] == [
        "workflow_stopped"
    ]


def test_runner_resume_waits_for_existing_token_without_duplicate_dispatch(
    tmp_path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config = load_config(root)
    store = StateStore(root)
    state = store.load()
    for agent_id, pane_id in (("planner", "%1"), ("reviewer", "%2")):
        state["agents"][agent_id] = {
            "agent_id": agent_id,
            "pane_id": pane_id,
            "session_name": "agentdeck",
            "cwd": str(root),
            "status": "running",
        }
    store.save(state)
    plan = store.record_plan(
        "Prepare and review evidence", "fake", "fake-plan", PLAN["plan"]
    )
    run = store.create_workflow_run(
        plan_id=str(plan["plan_id"]),
        plan_hash=workflow_plan_hash(plan),
        timeout_seconds=30,
        authorized_steps=authorized_steps(plan),
    )
    backend = ToggleReplyBackend()
    clock = iter((0.0, 31.0))

    stopped = run_sequential_workflow(
        config=config,
        store=store,
        backend=backend,
        run_id=str(run["run_id"]),
        poll_interval=0,
        monotonic=lambda: next(clock),
        sleeper=lambda _seconds: None,
    )

    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "timed_out"
    assert len(backend.sent) == 1
    first_token = stopped["turns"][0]["handoff_token"]

    backend.respond = True
    resumed = run_sequential_workflow(
        config=config,
        store=store,
        backend=backend,
        run_id=str(run["run_id"]),
        poll_interval=0,
    )

    assert resumed["status"] == "completed"
    assert len(backend.sent) == 2
    assert sum(first_token in prompt for _pane, prompt in backend.sent) == 1
    assert [turn["status"] for turn in resumed["turns"]] == [
        "completed",
        "completed",
    ]


class LatePaneBackend(CorrelatedFakeBackend):
    """pane 先不在,后来才起来——重建 runtime 之后 resume 的真实情形。"""

    def __init__(self) -> None:
        super().__init__()
        self.panes_up = False

    def pane_exists(self, _config, _pane_id: str) -> bool:
        return self.panes_up


def test_runner_resume_dispatches_a_turn_that_was_never_delivered(tmp_path) -> None:
    # 2026-08-05 live:pane 在第 1 步之前整个没了,run 以 `pane_lost` 停下并
    # 记了一条 turn。重建 pane 之后连续两次 resume 都只是干等满超时——因为
    # "不重复派发"这条守卫认的是**turn 记录是否存在**,而不是"这个任务到底
    # 有没有送出去过"。那条 turn 的 `message_id` 是 None:它从没被派发过。
    #
    # 于是 run 永久卡死,而 `can_resume: true` 一直宣称它还能救——账本说了一
    # 件不成立的事。不重复派发仍然要守(重发会让 worker 收到同一份任务两次),
    # 但判据必须是"送出去过没有",不是"有没有这条记录"。
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config = load_config(root)
    store = StateStore(root)
    state = store.load()
    for agent_id, pane_id in (("planner", "%1"), ("reviewer", "%2")):
        state["agents"][agent_id] = {
            "agent_id": agent_id,
            "pane_id": pane_id,
            "session_name": "agentdeck",
            "cwd": str(root),
            "status": "running",
        }
    store.save(state)
    plan = store.record_plan(
        "Prepare and review evidence", "fake", "fake-plan", PLAN["plan"]
    )
    run = store.create_workflow_run(
        plan_id=str(plan["plan_id"]),
        plan_hash=workflow_plan_hash(plan),
        timeout_seconds=30,
        authorized_steps=authorized_steps(plan),
    )
    backend = LatePaneBackend()

    stopped = run_sequential_workflow(
        config=config,
        store=store,
        backend=backend,
        run_id=str(run["run_id"]),
        poll_interval=0,
        sleeper=lambda _seconds: None,
    )

    assert stopped["stop_reason"] == "pane_lost"
    assert backend.sent == []
    assert stopped["turns"][0]["message_id"] is None

    backend.panes_up = True
    resumed = run_sequential_workflow(
        config=config,
        store=store,
        backend=backend,
        run_id=str(run["run_id"]),
        poll_interval=0,
        sleeper=lambda _seconds: None,
    )

    # 从没送达的那一步现在真的被送出去了,run 得以继续。
    assert backend.sent, "resume must dispatch a turn that was never delivered"
    assert resumed["status"] == "completed"
    assert [turn["status"] for turn in resumed["turns"]] == ["completed", "completed"]
    # 每一步仍然只被派发一次——"不重复派发"这条守卫没有被放宽。
    assert len(backend.sent) == 2


def test_reply_field_value_may_continue_on_the_next_line() -> None:
    # 2026-08-05 live:reviewer(claude)交回了一份**完全正确**的回复,token 精确
    # 匹配、七个字段对了六个,却被整份丢掉——因为它把长句写成
    #     summary:
    #     侍中、侍郎郭攸之、费祎、董允等,……
    # 解析器按 `key: value` 逐行读,于是 summary 读成空串,`parse_correlated_reply`
    # 返回 None(=「还没来」),运行器继续等,240 秒后报 timed_out。
    # 屏幕上躺着正确答复,账本说「没收到」。
    # 值换行是聊天式 agent 的常态,不是异常。
    output = (
        "handoff_token: tok_1\n"
        "status: completed\n"
        "summary:\n"
        "侍中、侍郎郭攸之、费祎、董允等，此皆良实，志虑忠纯。\n"
        "verification: 已按任务要求逐字输出，无增删。\n"
        "risks: 无。\n"
        "next_steps: 无。\n"
        "full_output_path:\n"
    )

    reply = parse_correlated_reply(output, "tok_1")

    assert reply is not None
    assert reply["summary"].startswith("侍中、侍郎郭攸之")
    assert reply["verification"] == "已按任务要求逐字输出，无增删。"


def test_reply_continuation_stops_at_a_blank_line() -> None:
    # 续行必须**有界**:遇到下一个已知字段或空行就结束,绝不吞掉后面无关的内容。
    output = (
        "handoff_token: tok_2\n"
        "status: completed\n"
        "summary:\n"
        "第一句原文。\n"
        "\n"
        "这一段是 agent 后来的闲聊，绝不能进 summary。\n"
        "verification: ok\n"
        "risks: none\n"
        "next_steps: none\n"
    )

    reply = parse_correlated_reply(output, "tok_2")

    assert reply is not None
    assert reply["summary"] == "第一句原文。"


class FileChannelBackend(CorrelatedFakeBackend):
    """worker 走文件通道交回复,终端里只剩刮不出东西的残影。

    这就是真实 agent TUI 的样子:它会清滚动区、会折行、会把长值换到下一行。
    run-loop 早就只认文件了;`workflow` 是唯一还在刮屏幕的引擎。
    """

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))
        token = next(
            line.rsplit(":", 1)[1].strip()
            for line in text.splitlines()
            if line.startswith("Complete only this task. Use this handoff token exactly:")
        )
        path = next(
            Path(line.strip())
            for line in reversed(text.splitlines())
            if line.strip().endswith(".reply.txt")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"handoff_token: {token}\n"
            "status: completed\n"
            "summary: finished via the file channel\n"
            "verification: file channel\n"
            "risks: none\n"
            "next_steps: continue\n",
            encoding="utf-8",
        )

    def capture_output(self, _config, _pane_id: str, lines: int = 200) -> str:
        return "the agent TUI cleared its scrollback; nothing scrapeable here"


def test_runner_accepts_a_reply_written_to_the_file_channel(tmp_path) -> None:
    # 今晚五个 bug 里有三个长在同一条接缝上:AgentDeck 靠往 pane 里打字、
    # 再从 pane 里读像素来和 worker 通信。run-loop 早已改用 worker 写出的
    # 文件("真实 agent TUI 会清滚动区导致 pane 刮取失败"),而 workflow
    # 还在刮屏幕。这条测试钉住:屏幕完全读不出东西时,文件通道仍能收回结果。
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config = load_config(root)
    store = StateStore(root)
    state = store.load()
    for agent_id, pane_id in (("planner", "%1"), ("reviewer", "%2")):
        state["agents"][agent_id] = {
            "agent_id": agent_id,
            "pane_id": pane_id,
            "session_name": "agentdeck",
            "cwd": str(root),
            "status": "running",
        }
    store.save(state)
    plan = store.record_plan(
        "Prepare and review evidence", "fake", "fake-plan", PLAN["plan"]
    )
    run = store.create_workflow_run(
        plan_id=str(plan["plan_id"]),
        plan_hash=workflow_plan_hash(plan),
        timeout_seconds=30,
        authorized_steps=authorized_steps(plan),
    )

    finished = run_sequential_workflow(
        config=config,
        store=store,
        backend=FileChannelBackend(),
        run_id=str(run["run_id"]),
        poll_interval=0,
        sleeper=lambda _seconds: None,
    )

    assert finished["status"] == "completed"
    assert [turn["status"] for turn in finished["turns"]] == ["completed", "completed"]
    assert finished["turns"][0]["handoff"]["summary"] == "finished via the file channel"


def test_reply_channel_instruction_overrides_task_file_restrictions() -> None:
    # 2026-08-05 live:任务文本里写着"不要创建或修改任何文件",派发提示词里
    # 写着"必须把回复写入该文件"。claude 选择听任务那条,于是它 4 秒就答完
    # 了、屏幕上答案齐全,但**没有回家的路**——TUI 又清掉了滚动区,刮屏也捞
    # 不回来,那一步就此永远停在 `dispatched`。
    #
    # 回复通道不是任务的一部分,它是机器取回答案的路径。一条任务的措辞能把
    # 返回通道弄坏,这不该可能——所以通道段落必须排在任务之后,并**显式声明
    # 它不受任务级文件限制约束**。
    prompt = build_workflow_prompt(
        role="recitation",
        role_prompt="只念字母",
        task="仅输出：A B。不要创建或修改任何文件。",
        handoff_token="tok_1",
        previous_handoff=None,
        reply_file="/tmp/demo/.agentdeck/replies/msg_x.reply.txt",
    )

    assert "/tmp/demo/.agentdeck/replies/msg_x.reply.txt" in prompt
    # 通道段落必须在任务正文之后——后出现的指令才压得住前面的限制。
    assert prompt.index("不要创建或修改任何文件") < prompt.index("回复通道")
    # 而且必须把"不受任务限制约束"这件事说出口,不能指望模型自己推断。
    channel = prompt[prompt.index("回复通道"):]
    assert "优先" in channel or "不受" in channel
