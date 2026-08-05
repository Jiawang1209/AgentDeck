"""workflow 必须认得 worker 的 transport。

2026-08-05:AgentDeck 有两套执行栈。Mission daemon 那套有完整的传输抽象——
`AcpWorkerTransport` 与 `TmuxWorkerTransport` 同接口,`admit()` 给出**送达
回执**,完成判据也分得清楚(ACP 要 `stop_reason == "end_turn"`,是协议告诉的;
tmux 要 `"structured_reply"`,是从屏幕上猜的)。而 `workflow` 这一套完全没有
transport 感知:不管配置写了什么,一律 `backend.send_input` 往 pane 里打字。

于是一个配了 `transport = "acp"` 的 agent,在 workflow 里会被**静默地**当成
tmux 来对待——而 CLAUDE.md 明文禁止 ACP 与 tmux 互相静默 fallback。同日六个
live bug 全长在"往终端打字"这条缝上,把一个本可以走协议的 worker 推到这条缝
上,是把已知的脆弱强加给它。

本切片先把**静默**去掉:workflow 遇到非 tmux transport 必须停下并说清楚,
一个字都不许往那个 pane 里打。真正接上 ACP 派发是下一刀。
"""
from __future__ import annotations

from pathlib import Path

from adversarial_backends import ScriptedPaneBackend
from agentdeck.config import load_config, write_default_config
from agentdeck.state import StateStore
from agentdeck.workflow import (
    authorized_steps,
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


def _project(tmp_path: Path, *, acp_agent: str | None = None):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    if acp_agent is not None:
        path = root / ".agentdeck" / "config.toml"
        text = path.read_text(encoding="utf-8")
        marker = f'agent_id = "{acp_agent}"'
        assert text.count(marker) == 1
        path.write_text(
            text.replace(
                marker,
                f'{marker}\ntransport = "acp"\ntransport_command = ["fake-acp-adapter"]',
            ),
            encoding="utf-8",
        )
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


def _run(config, store, run_id, backend):
    return run_sequential_workflow(
        config=config,
        store=store,
        backend=backend,
        run_id=run_id,
        poll_interval=0,
        sleeper=lambda _seconds: None,
    )


def test_an_acp_worker_is_never_typed_into(tmp_path) -> None:
    config, store, run_id = _project(tmp_path, acp_agent="planner")
    backend = ScriptedPaneBackend(deliver_via="file")

    stopped = _run(config, store, run_id, backend)

    # 一个字都不许发进去:配置说这个 worker 走协议,不是走键盘。
    assert backend.sent == []
    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "transport_unsupported"
    turn = stopped["turns"][0]
    assert turn["message_id"] is None, "什么都没派出去,就不该有 message"
    blocker = turn.get("blocker") or ""
    assert "acp" in blocker.lower() and "planner" in blocker


def test_tmux_workers_are_unaffected(tmp_path) -> None:
    # 缺省 transport 是 tmux,这条路径必须逐字节不变。
    config, store, run_id = _project(tmp_path)
    backend = ScriptedPaneBackend(deliver_via="file")

    finished = _run(config, store, run_id, backend)

    assert finished["status"] == "completed"
    assert len(backend.sent) == 2


def test_a_mixed_plan_stops_at_the_acp_step_after_the_tmux_step_ran(tmp_path) -> None:
    # 混合 plan:第 1 步 tmux 正常跑完,第 2 步 ACP 停下。停在正确的位置,
    # 且已完成的那步不受影响——不是整条 plan 一开始就拒绝。
    config, store, run_id = _project(tmp_path, acp_agent="reviewer")
    backend = ScriptedPaneBackend(deliver_via="file")

    stopped = _run(config, store, run_id, backend)

    assert stopped["stop_reason"] == "transport_unsupported"
    assert stopped["turns"][0]["status"] == "completed"
    assert len(backend.sent) == 1
