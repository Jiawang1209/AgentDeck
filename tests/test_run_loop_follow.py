from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


class FakeTmuxBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.output = ""

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))
        # 真实 TUI 在回车后授权框消失;静态输出会让框被重复扫描。
        self.output = ""

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        return self.output


def bind_agent(root: Path, agent_id: str, pane_id: str) -> None:
    store = StateStore(root)
    state = store.load()
    state["agents"][agent_id] = {
        "agent_id": agent_id,
        "pane_id": pane_id,
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    store.save(state)


def enable_autonomous(capsys, allow: list[str], budget: int) -> None:
    cli.main([
        "policy", "set-mode", "--mode", "autonomous", "--confirm",
        *sum((["--allow-agent", a] for a in allow), []),
        "--max-approvals", str(budget),
    ])
    capsys.readouterr()


def seed_plan(root: Path, steps: list[str]) -> str:
    """Seed a sequential plan whose step N targets steps[N-1] and create approvals."""
    store = StateStore(root)
    state = store.load()
    plan_id = "pln_follow_1"
    config = cli.load_config(root)
    roles = {a.agent_id: a.role for a in config.agents}
    state.setdefault("plans", []).append({
        "plan_id": plan_id, "task": "g", "status": "planned",
        "provider": "fake", "model": "fake-plan",
        "plan": {
            "goal": "g", "summary": "s",
            "steps": [
                {"step": index + 1, "agent_id": agent_id, "role": roles[agent_id],
                 "task": f"step {index + 1}", "risk": "low", "requires_approval": True}
                for index, agent_id in enumerate(steps)
            ],
        },
        "created_at": "2026-07-26T00:00:00+00:00",
    })
    store.save(state)
    store.create_approvals_from_plan(plan_id)
    return plan_id


def _write_pending_reply_files(root: Path) -> None:
    """Simulate workers finishing: write a structured reply file for every
    dispatched-but-unreplied message."""
    state = StateStore(root).load()
    replied = {r.get("message_id") for r in state.get("replies", [])}
    for message in state.get("messages", []):
        message_id = message.get("message_id")
        if message_id in replied:
            continue
        reply_file = root / ".agentdeck" / "replies" / f"{message_id}.reply.txt"
        reply_file.parent.mkdir(parents=True, exist_ok=True)
        if not reply_file.exists():
            reply_file.write_text(
                "status: completed\nsummary: done\n", encoding="utf-8"
            )


def test_run_loop_follow_rejects_bad_max_waves(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    enable_autonomous(capsys, ["planner"], 5)
    plan_id = seed_plan(root, ["planner"])

    assert cli.main([
        "run-loop", "--plan-id", plan_id, "--confirm", "--follow",
        "--max-waves", "0", "--interval", "0",
    ]) == 1
    assert "max-waves" in capsys.readouterr().err


def test_run_loop_follow_stops_at_bound_while_waiting(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    enable_autonomous(capsys, ["planner"], 5)
    plan_id = seed_plan(root, ["planner"])

    exit_code = cli.main([
        "run-loop", "--plan-id", plan_id, "--confirm", "--follow",
        "--max-waves", "2", "--interval", "0",
    ])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_follow"
    assert payload["plan_id"] == plan_id
    assert payload["max_waves"] == 2
    assert payload["wave_count"] == 2
    assert payload["stopped_reason"] == "waiting_for_reply"
    assert payload["waves"][0]["wave"] == 1
    assert payload["waves"][0]["mode"] == "run_loop"
    assert payload["waves"][0]["dispatched"][0]["agent_id"] == "planner"
    assert payload["released_box_count"] == 0


def test_run_loop_follow_advances_to_complete_as_replies_arrive(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    bind_agent(root, "coder", "%43")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    enable_autonomous(capsys, ["planner", "coder"], 5)
    plan_id = seed_plan(root, ["planner", "coder"])

    # 用 interval sleep 模拟 worker 在等待间隙写出回复文件
    monkeypatch.setattr(cli.time, "sleep", lambda _s: _write_pending_reply_files(root))

    exit_code = cli.main([
        "run-loop", "--plan-id", plan_id, "--confirm", "--follow",
        "--max-waves", "6", "--interval", "1",
    ])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_follow"
    assert payload["stopped_reason"] == "complete"
    assert payload["wave_count"] < 6
    dispatched_agents = [
        d["agent_id"] for wave in payload["waves"] for d in wave["dispatched"]
    ]
    assert dispatched_agents == ["planner", "coder"]
    captured = [
        c["agent_id"] for wave in payload["waves"] for c in wave.get("captured_replies") or []
    ]
    assert captured == ["planner", "coder"]


def test_run_loop_all_ingests_file_channel_replies(tmp_path, monkeypatch, capsys) -> None:
    # 单计划 wave 的文件摄入语义补齐到 --all（此前明文标注"后续切片"）。
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    enable_autonomous(capsys, ["planner"], 5)
    plan_id = seed_plan(root, ["planner"])

    # wave 1：--all 派发 step 1
    assert cli.main(["run-loop", "--all", "--confirm"]) == 0
    capsys.readouterr()
    # worker 写出文件通道回复
    _write_pending_reply_files(root)

    # wave 2：--all 应摄入回复并把该计划推进到 complete
    assert cli.main(["run-loop", "--all", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    item = next(p for p in payload["plans"] if p["plan_id"] == plan_id)
    captured = item.get("captured_replies") or []
    assert [c["captured_from"] for c in captured] == ["file"]
    assert item["gate"] == "complete"
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "run_loop_reply_captured"' in events


def _init_real_git(root: Path) -> None:
    import subprocess

    (root / ".git").rmdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)


def test_run_loop_follow_merge_on_complete_merges_plan_branches(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    root = prepare_project(tmp_path, monkeypatch)
    _init_real_git(root)
    bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    enable_autonomous(capsys, ["coder"], 5)
    plan_id = seed_plan(root, ["coder"])

    def _finish_work(_seconds: float) -> None:
        state = StateStore(root).load()
        for message in state.get("messages", []):
            wt = message.get("worktree_path")
            if not wt:
                continue
            feature = Path(wt) / "feature.txt"
            if not feature.exists():
                feature.write_text("done\n", encoding="utf-8")
                subprocess.run(["git", "-C", wt, "add", "feature.txt"], check=True)
                subprocess.run(["git", "-C", wt, "commit", "-qm", "feature"], check=True)
        _write_pending_reply_files(root)

    monkeypatch.setattr(cli.time, "sleep", _finish_work)

    exit_code = cli.main([
        "run-loop", "--plan-id", plan_id, "--confirm", "--follow",
        "--max-waves", "4", "--interval", "1", "--merge-on-complete",
    ])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stopped_reason"] == "complete"
    assert payload["merge_on_complete"] is True
    merge = payload["plan_merge"]
    assert merge["mode"] == "worktree_merge_plan"
    assert len(merge["merged"]) == 1
    assert (root / "feature.txt").is_file()


CODEX_AUTH_BOX = (
    "  Would you like to run the following command?\n"
    "  $ node tests/regression.mjs\n"
    "› 1. Yes, proceed (y)\n"
    "  Press enter to confirm or esc to cancel\n"
)


def test_run_loop_follow_release_boxes_scans_at_segment_start(tmp_path, monkeypatch, capsys) -> None:
    # Round 11 live 发现 #4：委托框在两段 follow 之间弹出时,只在 wave
    # 间隙扫描会整段错过——段首必须也扫一次(max-waves 1 时唯一机会)。
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    enable_autonomous(capsys, ["coder"], 5)
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    capsys.readouterr()
    plan_id = seed_plan(root, ["coder"])
    fake.output = CODEX_AUTH_BOX

    exit_code = cli.main([
        "run-loop", "--plan-id", plan_id, "--confirm", "--follow",
        "--max-waves", "1", "--interval", "0", "--release-boxes",
    ])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["released_box_count"] == 1
    assert payload["released_boxes"][0]["agent_id"] == "coder"
    assert ("%50", "") in fake.sent


def test_run_loop_follow_release_boxes_releases_delegated_box(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    enable_autonomous(capsys, ["coder"], 5)
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    capsys.readouterr()
    plan_id = seed_plan(root, ["coder"])
    fake.output = CODEX_AUTH_BOX

    exit_code = cli.main([
        "run-loop", "--plan-id", plan_id, "--confirm", "--follow",
        "--max-waves", "2", "--interval", "0", "--release-boxes",
    ])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["release_boxes"] is True
    assert payload["released_box_count"] == 1
    assert payload["released_boxes"][0]["agent_id"] == "coder"
    assert payload["released_boxes"][0]["command"] == "node tests/regression.mjs"
    assert ("%50", "") in fake.sent
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "auth_box_released"' in events


def _failing_validator(_payload):
    return {"ok": False, "errors": ["injected: follow contract violated"]}


def test_run_loop_follow_validates_before_merging(tmp_path, monkeypatch, capsys) -> None:
    """Pins a latent path: the follow payload's contract is validated *before*
    the plan merge, so a validation failure can never leave task branches
    already merged into the base branch.

    The validator is tautological on today's literal payload, so the failing
    path is only reachable by injecting a failing validator. That is the
    correct way to pin a latent defect: adding one field or one enum value
    later would make it live.
    """
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    enable_autonomous(capsys, ["coder"], 5)
    plan_id = seed_plan(root, ["coder"])
    monkeypatch.setattr(cli.time, "sleep", lambda _s: _write_pending_reply_files(root))

    merge_calls: list[str] = []
    monkeypatch.setattr(
        cli,
        "_merge_plan_worktrees",
        lambda _config, _store, pid: merge_calls.append(pid) or {"mode": "worktree_merge_plan"},
    )
    monkeypatch.setattr(cli, "validate_run_loop_follow_contract", _failing_validator)

    exit_code = cli.main([
        "run-loop", "--plan-id", plan_id, "--confirm", "--follow",
        "--max-waves", "4", "--interval", "1", "--merge-on-complete",
    ])
    assert exit_code == 1
    assert merge_calls == []


def test_run_loop_follow_failure_reports_the_effects_that_already_happened(
    tmp_path, monkeypatch, capsys
) -> None:
    """Pins a latent path: when the follow contract fails, the wave effects
    (tmux dispatches, released boxes) cannot be undone, so the failure must
    name them instead of reading as 'the follow did not run'."""
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    enable_autonomous(capsys, ["coder"], 5)
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    capsys.readouterr()
    plan_id = seed_plan(root, ["coder"])
    fake.output = CODEX_AUTH_BOX
    monkeypatch.setattr(cli, "validate_run_loop_follow_contract", _failing_validator)

    exit_code = cli.main([
        "run-loop", "--plan-id", plan_id, "--confirm", "--follow",
        "--max-waves", "1", "--interval", "0", "--release-boxes",
    ])
    assert exit_code == 1
    err = capsys.readouterr().err
    state = StateStore(root).load()
    message_id = state["messages"][0]["message_id"]
    assert "already happened" in err
    assert "1 wave" in err
    assert message_id in err
    assert "1 authorization box" in err
    assert "not attempted" in err

    events = [
        json.loads(line)
        for line in (root / ".agentdeck" / "state" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    failed = [e for e in events if e["event_type"] == "run_loop_contract_failed"]
    assert failed, "the failure must stay in the ledger"
    payload = failed[-1]["payload"]
    assert payload["wave_count"] == 1
    assert payload["dispatched_message_ids"] == [message_id]
    assert payload["released_box_count"] == 1
    assert payload["plan_merge"] == "not attempted"


# ---------------------------------------------------------------------------
# 人类门检测的前台对称实现(2026-08-02)。
#
# `run-loop-host` 已经会在被等待的 worker 停在一道**未委托**授权框上时诚实
# 停下(live:同一场景从烧 834/846 个 wave 变成 wave 1、约 7 秒停)。原 spec
# 把 `--follow` 的对称实现列为非目标,理由是"前台有人看着"——但
# `--follow --max-waves 300 --interval 10` 同样是几小时无人值守轮询,于是
# 同一个走开命令后台会指名道姓停下、前台仍闷头烧光预算。
#
# 复用完全相同的一批零件:纯函数 `human_gate_candidate` / `same_human_gate`、
# 同一份 `_plan_awaiting` awaiting 集、同一个 `box_pending` 正证明,并保留
# 同一条能力边界——**只在 `--release-boxes` 开启时检测**。
#
# 一处刻意的形状差异:follow payload 的 `stopped_reason` 是**最后一个 wave
# 的 gate**,把 `human_gate` 塞进去会污染 wave gate 枚举。所以 follow 保持
# 真实 gate,人类门以独立的 `human_gate` 证据字段呈现,提前退出体现为
# `wave_count < max_waves`。
# ---------------------------------------------------------------------------

_REAL_WAITING_HINT = "Press enter to confirm or esc to cancel"


def _undelegated_box(command: str = "playwright open x", agent_id: str = "coder") -> dict:
    return {
        "agent_id": agent_id, "command": command, "box_kind": "command",
        "mcp_server": None, "mcp_tool": None,
        "waiting_hint": _REAL_WAITING_HINT,
        # 真待批框(活动选择器在屏上)。人类门检测要求这道正证明。
        "box_pending": True,
        "reason": "no active delegation", "iteration": 0,
    }


def _waiting_wave(*_args, **_kwargs) -> dict:
    """一个**完整合法**的单 wave payload:follow 的 validator 会逐个复验
    嵌套 wave,半个 payload 会被契约挡下而不是走到人类门判定。"""
    return {
        "ok": True, "mode": "run_loop", "plan_id": "pln_follow_1",
        "requires_explicit_user": True, "safety": "delegated",
        "auto_approved": 0, "dispatched": [], "blocked": [], "skipped": [],
        "stopped_reason": "waiting_for_reply",
        "next_command": "agentdeck capture-reply --agent coder --message-id msg_gate_1",
        "policy": {"allowed_agents": ["coder"], "max_approvals": 3},
    }


def _seed_dispatched_approval(
    root: Path, plan_id: str, agent_id: str = "coder", message_id: str = "msg_gate_1"
) -> None:
    """本 plan 有一条已派发未回复的审批 → 该 agent 进入 awaiting 集。"""
    store = StateStore(root)
    state = store.load()
    state.setdefault("approvals", []).append({
        "approval_id": "apv_gate_1", "plan_id": plan_id, "status": "dispatched",
        "message_id": message_id, "agent_id": agent_id, "step": 1,
    })
    store.save(state)


def _follow_argv(plan_id: str, max_waves: int, *extra: str) -> list[str]:
    return [
        "run-loop", "--plan-id", plan_id, "--confirm", "--follow",
        "--max-waves", str(max_waves), "--interval", "0", *extra,
    ]


def test_follow_stops_on_the_second_consecutive_sighting_of_the_same_human_gate(
    tmp_path, monkeypatch, capsys
) -> None:
    """第一次命中只记候选;第二次同一道框才停,并带出屏上证据。

    停止体现为 `wave_count < max_waves` + 非空 `human_gate`;`stopped_reason`
    仍是最后一个 wave 的真实 gate(那个 wave 确实在等一个回复)。"""
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "coder", "%50")
    enable_autonomous(capsys, ["coder"], 3)
    plan_id = seed_plan(root, ["coder"])
    _seed_dispatched_approval(root, plan_id)

    scans: list[int] = []

    def fake_scan(_config, _store, _backend, _agent_ids, iteration, source="boxes_watch"):
        scans.append(iteration)
        assert source == "run_loop_follow"
        return [], [dict(_undelegated_box(), iteration=iteration)]

    monkeypatch.setattr(cli, "_scan_release_delegated_boxes", fake_scan)
    monkeypatch.setattr(cli, "TmuxBackend", lambda: object())
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)

    assert cli.main(_follow_argv(plan_id, 10, "--release-boxes")) == 0
    payload = json.loads(capsys.readouterr().out)

    # 段首扫描(wave 0)+ wave1 后扫描 = 两次同一道框 → 停在 wave 1
    assert scans == [0, 1]
    assert payload["wave_count"] == 1
    assert payload["wave_count"] < payload["max_waves"]  # 提前退出的唯一体现
    # 形状差异:gate 枚举不被污染,最后一个 wave 的真实 gate 原样保留
    assert payload["stopped_reason"] == "waiting_for_reply"
    assert payload["human_gate"] == {
        "agent_id": "coder", "box_kind": "command", "command": "playwright open x",
        "mcp_server": None, "mcp_tool": None, "waiting_hint": _REAL_WAITING_HINT,
    }


def test_follow_ignores_a_box_on_an_agent_outside_the_awaiting_set(
    tmp_path, monkeypatch, capsys
) -> None:
    """框在别的 agent 身上 → 不是本 plan 的人类门,照常烧到预算上限。"""
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "coder", "%50")
    enable_autonomous(capsys, ["coder"], 3)
    plan_id = seed_plan(root, ["coder"])
    _seed_dispatched_approval(root, plan_id, agent_id="coder")

    monkeypatch.setattr(
        cli, "_scan_release_delegated_boxes",
        lambda *_a, **_k: ([], [_undelegated_box(agent_id="reviewer")]),
    )
    monkeypatch.setattr(cli, "TmuxBackend", lambda: object())
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)

    assert cli.main(_follow_argv(plan_id, 3, "--release-boxes")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["wave_count"] == 3
    assert payload["stopped_reason"] == "waiting_for_reply"
    assert payload["human_gate"] is None


def test_follow_does_not_stop_when_the_box_changes_between_scans(
    tmp_path, monkeypatch, capsys
) -> None:
    """两次命中的是不同的框 → 不判定,重新计数。"""
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "coder", "%50")
    enable_autonomous(capsys, ["coder"], 3)
    plan_id = seed_plan(root, ["coder"])
    _seed_dispatched_approval(root, plan_id)

    commands = ["a", "b", "a", "b", "a", "b"]
    calls = {"n": 0}

    def alternating_scan(*_a, **_k):
        command = commands[calls["n"]]
        calls["n"] += 1
        return [], [_undelegated_box(command=command)]

    monkeypatch.setattr(cli, "_scan_release_delegated_boxes", alternating_scan)
    monkeypatch.setattr(cli, "TmuxBackend", lambda: object())
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)

    assert cli.main(_follow_argv(plan_id, 4, "--release-boxes")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["wave_count"] == 4
    assert payload["human_gate"] is None


def test_follow_without_release_boxes_never_reads_a_pane(
    tmp_path, monkeypatch, capsys
) -> None:
    """不开 `--release-boxes`:扫描器一次都不被调用,行为与既有逐字节相同。"""
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "coder", "%50")
    enable_autonomous(capsys, ["coder"], 3)
    plan_id = seed_plan(root, ["coder"])
    _seed_dispatched_approval(root, plan_id)

    scanned: list[int] = []

    def never_scan(*_a, **_k):
        scanned.append(1)
        return [], [_undelegated_box()]

    monkeypatch.setattr(cli, "_scan_release_delegated_boxes", never_scan)
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)

    assert cli.main(_follow_argv(plan_id, 2)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert scanned == []  # 一次 pane 都没读
    assert payload["release_boxes"] is False
    assert payload["wave_count"] == 2
    assert payload["stopped_reason"] == "waiting_for_reply"
    assert payload["human_gate"] is None


def test_human_gate_stop_never_triggers_the_automatic_merge(
    tmp_path, monkeypatch, capsys
) -> None:
    """人类门停止时最后一个 wave 的 gate 不是 complete → 绝不自动合并。"""
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "coder", "%50")
    enable_autonomous(capsys, ["coder"], 3)
    plan_id = seed_plan(root, ["coder"])
    _seed_dispatched_approval(root, plan_id)

    merges: list[str] = []
    monkeypatch.setattr(
        cli, "_scan_release_delegated_boxes", lambda *_a, **_k: ([], [_undelegated_box()])
    )
    monkeypatch.setattr(cli, "TmuxBackend", lambda: object())
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)
    monkeypatch.setattr(
        cli, "_merge_plan_worktrees",
        lambda _config, _store, merged: merges.append(merged) or {"ok": True},
    )
    monkeypatch.setattr(cli, "_verdict_merge_blocker", lambda _store, _plan: None)

    assert cli.main(_follow_argv(plan_id, 10, "--release-boxes", "--merge-on-complete")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["human_gate"] is not None
    assert payload["merge_on_complete"] is True
    assert "plan_merge" not in payload
    assert merges == []


# 端到端:mock 掉扫描器的测试**看不到** pane 文本 → 框解析 → 候选这一整段。
# 宿主切片正是因为每个 serve 级测试都 mock 了扫描器,让"已答复的折叠框误停
# 一个健康走开段"活过了七个 commit 和一次 live PASS。下面喂真实 pane 文本、
# 跑真实扫描。
_ANSWERED_BOX_PANE = """\
• Ran node tests/a.mjs
  Would you like to run the following command? -> Yes
• Working (esc to interrupt)
"""


def test_follow_does_not_stop_on_an_already_answered_collapsed_box(
    tmp_path, monkeypatch, capsys
) -> None:
    """已答复的折叠框仍会命中 `_detect_waiting_for_input` 的 marker,但屏上
    没有任何东西在等人按。全 None 身份恒等于自身,若无 `box_pending` 正证明,
    debounce 会必然确认,一个健康的走开段被误停。扫描器刻意**不** mock。"""
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "coder", "%50")
    fake = FakeTmuxBackend()
    fake.output = _ANSWERED_BOX_PANE
    # 真实 TUI 里回车会让框消失;这里框已答复,谁也不该按它,故输出恒定。
    fake.send_input = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("detection must never send tmux input")
    )
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    enable_autonomous(capsys, ["coder"], 3)
    plan_id = seed_plan(root, ["coder"])
    _seed_dispatched_approval(root, plan_id)
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)

    assert cli.main(_follow_argv(plan_id, 3, "--release-boxes")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["wave_count"] == 3  # 烧完预算,绝不误停
    assert payload["human_gate"] is None
    assert payload["released_box_count"] == 0
    assert fake.sent == []
