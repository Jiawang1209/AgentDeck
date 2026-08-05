"""在不配合的 runtime 上跑 workflow —— 五个现场故障各一条。

这些不是"边缘情况":2026-08-05 那晚每一次真跑都撞上其中之一,而当时 5306 条
测试一条都没抓到,因为桩永远配合。每条测试要求的都是同一件事——AgentDeck
要么把活干完,要么**如实说出**没干完;绝不允许"静默地不工作",也绝不允许
账本声称一件不成立的事。
"""
from __future__ import annotations

from pathlib import Path

from adversarial_backends import ScriptedPaneBackend, reply_block
from agentdeck.config import load_config, write_default_config
from agentdeck.state import StateStore
from agentdeck.workflow import (
    authorized_steps,
    parse_correlated_reply,
    run_sequential_workflow,
    workflow_plan_hash,
)


PLAN = {
    "steps": [
        {
            "step": 1,
            "agent_id": "planner",
            "role": "planning",
            "task": "Prepare evidence",
            "risk": "low",
            "requires_approval": True,
        },
        {
            "step": 2,
            "agent_id": "reviewer",
            "role": "review",
            "task": "Review evidence",
            "risk": "low",
            "requires_approval": True,
        },
    ]
}


def _project(tmp_path: Path):
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
    plan = store.record_plan("Prepare and review", "fake", "fake-plan", PLAN)
    run = store.create_workflow_run(
        plan_id=str(plan["plan_id"]),
        plan_hash=workflow_plan_hash(plan),
        timeout_seconds=30,
        authorized_steps=authorized_steps(plan),
    )
    return config, store, str(run["run_id"])


def _run(config, store, run_id, backend, *, clock=None):
    return run_sequential_workflow(
        config=config,
        store=store,
        backend=backend,
        run_id=run_id,
        poll_interval=0,
        sleeper=lambda _seconds: None,
        **({"monotonic": clock} if clock else {}),
    )


def test_scrollback_cleared_still_completes_through_the_file_channel(tmp_path) -> None:
    """现场:真实 agent TUI 清掉滚动区,pane 里什么都刮不出来。

    这正是 run-loop 早就不刮屏幕的原因(它的派发提示词里写着这句话),而
    workflow 直到 2026-08-05 才接上文件通道。
    """
    config, store, run_id = _project(tmp_path)
    backend = ScriptedPaneBackend(scrollback_cleared=True, deliver_via="file")

    finished = _run(config, store, run_id, backend)

    assert finished["status"] == "completed"
    assert [turn["status"] for turn in finished["turns"]] == ["completed", "completed"]


def test_wrapped_field_value_still_completes(tmp_path) -> None:
    """现场:claude 把长句写成 `summary:` 换行再写内容。

    七个字段对了六个,整份回复曾被丢掉,运行器等满超时报 timed_out——
    屏幕上躺着正确答复,账本说"没收到"。
    """
    config, store, run_id = _project(tmp_path)
    backend = ScriptedPaneBackend(wrap_field="summary", deliver_via="file")

    finished = _run(config, store, run_id, backend)

    assert finished["status"] == "completed"
    assert finished["turns"][0]["handoff"]["summary"] == "done"


def test_pane_vanishing_mid_run_never_claims_success(tmp_path) -> None:
    """现场:tmux session 整个退出。

    要的不是"跑完",是**绝不谎报**:pane 死了就如实停下,那一步不得被标成
    completed,run 仍然可续。至于死在哪一步是偶然的,不谎报才是要守的性质。
    """
    config, store, run_id = _project(tmp_path)
    backend = ScriptedPaneBackend(vanish_after=1, deliver_via="file")

    stopped = _run(config, store, run_id, backend)

    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "pane_lost"
    assert not any(turn["status"] == "completed" for turn in stopped["turns"])

    # 而且它必须是可救的:换回健康 runtime,同一个 run 续跑到完成。
    resumed = _run(config, store, run_id, ScriptedPaneBackend(deliver_via="file"))

    assert resumed["status"] == "completed"


def test_a_pane_owned_by_a_dialog_is_never_typed_into(tmp_path) -> None:
    """现场:模态框占住键盘,发进去的按键被吃掉——而 tmux **不报错**。

    2026-08-04 就是这样丢掉五十分钟的:任务消失、审批被记成 `dispatched`、
    宿主等一个永远不会来的回复。今晚修的守卫判据是"message_id 是不是 None",
    **盖不住这一类**——记录建得好好的,只是那句话从没到达。

    所以检查必须在**发送之前**:按键一旦离开就收不回来。框由人关掉之后,
    同一个 run 必须能续跑完成。
    """
    config, store, run_id = _project(tmp_path)
    backend = ScriptedPaneBackend(dialog_open=True, deliver_via="file")

    stopped = _run(config, store, run_id, backend)

    assert stopped["status"] == "stopped"
    # 一个字都不许发进去。
    assert backend.sent == []
    assert backend.swallowed == []
    assert stopped["stop_reason"] == "pane_not_receptive"
    assert "trust" in (stopped["turns"][0].get("blocker") or "").lower()
    assert stopped["turns"][0]["message_id"] is None

    backend.dialog_open = False
    resumed = _run(config, store, run_id, backend)

    assert resumed["status"] == "completed"
    assert len(backend.sent) == 2


def test_a_late_reply_is_waited_for_not_missed(tmp_path) -> None:
    """现场:worker 在想,回复迟到几轮轮询。

    迟到不是失败——运行器必须继续等,而不是把"还没到"当成"不会到"。
    """
    config, store, run_id = _project(tmp_path)
    backend = ScriptedPaneBackend(reply_delay_polls=3, deliver_via="pane")

    finished = _run(config, store, run_id, backend)

    assert finished["status"] == "completed"
    assert backend.polls > 3


def test_wrapped_value_parses_from_the_stub_block() -> None:
    """桩自身的自检:折行块必须真的折行,否则上面那条测试测的是空气。"""
    block = reply_block("tok", wrap_field="summary", summary="很长的一句话")

    assert "summary:\n很长的一句话" in block
    assert parse_correlated_reply(block, "tok")["summary"] == "很长的一句话"
