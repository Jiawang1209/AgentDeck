from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.run_loop_host import host_record_path, read_host_record, write_host_record
from agentdeck.state import StateStore


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def enable_autonomous(capsys) -> None:
    cli.main([
        "policy", "set-mode", "--mode", "autonomous", "--confirm",
        "--allow-agent", "coder", "--max-approvals", "3",
    ])
    capsys.readouterr()


def seed_plan(root: Path, plan_id: str = "pln_host_1") -> str:
    store = StateStore(root)
    state = store.load()
    state.setdefault("plans", []).append({
        "plan_id": plan_id,
        "goal": "host test",
        "summary": "host test",
        "steps": [{"step": 1, "agent_id": "coder", "role": "implementation",
                   "task": "do work", "risk": "low", "requires_approval": True}],
    })
    store.save(state)
    return plan_id


class RecordingSpawn:
    def __init__(self, pid: int = 999_001) -> None:
        self.pid = pid
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, argv: list[str], cwd: Path) -> int:
        self.calls.append((list(argv), str(cwd)))
        return self.pid


def test_start_gate_matrix_refuses_with_zero_writes(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    spawn = RecordingSpawn()
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)

    # 缺 --confirm
    assert cli.main(["run-loop-host", "start", "--plan-id", plan_id, "--max-waves", "5"]) == 1
    assert "confirm" in capsys.readouterr().err
    # 非 autonomous 模式
    assert cli.main([
        "run-loop-host", "start", "--plan-id", plan_id, "--confirm", "--max-waves", "5",
    ]) == 1
    assert "autonomous" in capsys.readouterr().err

    enable_autonomous(capsys)
    # 缺 --max-waves
    assert cli.main(["run-loop-host", "start", "--plan-id", plan_id, "--confirm"]) == 1
    assert "max-waves" in capsys.readouterr().err
    # --max-waves < 1
    assert cli.main([
        "run-loop-host", "start", "--plan-id", plan_id, "--confirm", "--max-waves", "0",
    ]) == 1
    capsys.readouterr()
    # 未知 plan
    assert cli.main([
        "run-loop-host", "start", "--plan-id", "pln_ghost", "--confirm", "--max-waves", "5",
    ]) == 1
    assert "unknown plan" in capsys.readouterr().err

    # 全程零写、零 spawn
    assert read_host_record(root) is None
    assert spawn.calls == []


def test_start_spawns_records_and_refuses_second_instance(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    spawn = RecordingSpawn(pid=999_002)
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)
    # 记录里的 pid 探活必须为真,否则第二次 start 不会被判为"已在跑"
    monkeypatch.setattr(cli, "_host_pid_alive", lambda pid: pid == 999_002)

    assert cli.main([
        "run-loop-host", "start", "--plan-id", plan_id, "--confirm",
        "--max-waves", "40", "--interval", "3", "--release-boxes", "--merge-on-complete",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_host_started"
    assert payload["pid"] == 999_002
    assert payload["plan_id"] == plan_id
    assert payload["max_waves"] == 40
    assert payload["release_boxes"] is True
    assert payload["merge_on_complete"] is True
    assert payload["status_command"] == "agentdeck run-loop-host status"
    assert payload["safety"] == "delegated"

    record = read_host_record(root)
    assert record["pid"] == 999_002
    assert record["plan_id"] == plan_id
    assert record["wave_count"] == 0
    assert record["stopped_reason"] is None

    # spawn argv 指向 serve 子命令并带全部参数
    argv, cwd = spawn.calls[0]
    assert argv[:3] == ["agentdeck", "run-loop-host", "serve"]
    assert "--plan-id" in argv and plan_id in argv
    assert "--max-waves" in argv and "40" in argv
    assert "--release-boxes" in argv and "--merge-on-complete" in argv
    assert cwd == str(root)
    assert '"event_type": "run_loop_host_started"' in (
        root / ".agentdeck" / "state" / "events.jsonl"
    ).read_text(encoding="utf-8")

    # 单例:第二次 start 拒绝,不再 spawn
    assert cli.main([
        "run-loop-host", "start", "--plan-id", plan_id, "--confirm", "--max-waves", "5",
    ]) == 1
    assert "already running" in capsys.readouterr().err
    assert len(spawn.calls) == 1


def test_status_is_read_only_across_three_record_states(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    # 无记录
    assert cli.main(["run-loop-host", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_host_status"
    assert payload["running"] is False and payload["stale"] is False
    assert payload["plan_id"] is None
    assert "--max-waves <n>" in payload["start_command_template"]

    # 活 pid
    write_host_record(root, {
        "pid": 999_003, "plan_id": "pln_host_1", "wave_count": 4, "max_waves": 40,
        "interval": 10.0, "last_gate": "waiting_for_reply",
        "last_wave_at": "2026-07-30T02:00:00+00:00", "stopped_reason": None,
        "log_path": ".agentdeck/run-loop-host/host.log",
    })
    monkeypatch.setattr(cli, "_host_pid_alive", lambda pid: True)
    before = StateStore(root).load()
    assert cli.main(["run-loop-host", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is True and payload["stale"] is False
    assert payload["wave_count"] == 4
    assert payload["stop_command"] == "agentdeck run-loop-host stop --confirm"
    assert StateStore(root).load() == before  # 只读

    # 死 pid → stale
    monkeypatch.setattr(cli, "_host_pid_alive", lambda pid: False)
    assert cli.main(["run-loop-host", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False and payload["stale"] is True

    # 干净停止(pid 已清)既不 running 也不 stale
    write_host_record(root, {
        "pid": None, "plan_id": "pln_host_1", "wave_count": 9, "max_waves": 40,
        "interval": 10.0, "last_gate": "complete",
        "last_wave_at": "2026-07-30T02:30:00+00:00", "stopped_reason": "gate_reached",
        "log_path": ".agentdeck/run-loop-host/host.log",
    })
    assert cli.main(["run-loop-host", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False and payload["stale"] is False
    assert payload["stopped_reason"] == "gate_reached"


def test_status_surfaces_the_human_gate_evidence(tmp_path, monkeypatch, capsys) -> None:
    """人类门停止后,status 必须把屏上框证据原样带出来供人类去按。"""
    root = prepare_project(tmp_path, monkeypatch)
    write_host_record(root, {
        "pid": None, "plan_id": "pln_host_1", "wave_count": 2, "max_waves": 10,
        "interval": 0.0, "last_gate": "waiting_for_reply",
        "last_wave_at": "2026-08-01T02:00:00+00:00", "stopped_reason": "human_gate",
        "log_path": ".agentdeck/run-loop-host/host.log",
        "human_gate": {
            "agent_id": "planner", "box_kind": "command",
            "command": "playwright open x", "mcp_server": None,
            "mcp_tool": None, "waiting_hint": _REAL_WAITING_HINT,
        },
    })
    before = StateStore(root).load()

    assert cli.main(["run-loop-host", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stopped_reason"] == "human_gate"
    assert payload["human_gate"] == {
        "agent_id": "planner", "box_kind": "command",
        "command": "playwright open x", "mcp_server": None,
        "mcp_tool": None, "waiting_hint": _REAL_WAITING_HINT,
    }
    assert StateStore(root).load() == before  # 只读


def test_status_human_gate_is_null_without_one(tmp_path, monkeypatch, capsys) -> None:
    """没有人类门时字段必须是 null,而不是缺失。"""
    root = prepare_project(tmp_path, monkeypatch)
    write_host_record(root, {
        "pid": None, "plan_id": "pln_host_1", "wave_count": 2, "max_waves": 10,
        "interval": 0.0, "last_gate": "waiting_for_reply",
        "last_wave_at": "2026-08-01T02:00:00+00:00",
        "stopped_reason": "budget_exhausted",
        "log_path": ".agentdeck/run-loop-host/host.log",
    })
    assert cli.main(["run-loop-host", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "human_gate" in payload
    assert payload["human_gate"] is None

    # 完全无记录时同样是 null,不是缺失
    host_record_path(root).unlink()
    assert cli.main(["run-loop-host", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["human_gate"] is None


def _serve_argv(
    root: Path,
    plan_id: str,
    max_waves: int,
    interval: str = "0",
    release_boxes: bool = False,
    merge_on_complete: bool = False,
) -> list[str]:
    argv = [
        "run-loop-host", "serve", "--project", str(root),
        "--plan-id", plan_id, "--max-waves", str(max_waves), "--interval", interval,
    ]
    if release_boxes:
        argv.append("--release-boxes")
    if merge_on_complete:
        argv.append("--merge-on-complete")
    return argv


def _log_lines(root: Path) -> list[dict]:
    from agentdeck.run_loop_host import host_log_path

    text = host_log_path(root).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_serve_runs_waves_until_gate_and_records(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": 5, "interval": 0.0,
        "release_boxes": False, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })
    gates = ["waiting_for_reply", "waiting_for_reply", "complete"]
    calls = {"n": 0}

    def fake_wave(_config, _store, wave_plan_id):
        assert wave_plan_id == plan_id
        gate = gates[calls["n"]]
        calls["n"] += 1
        return {"ok": True, "mode": "run_loop", "plan_id": wave_plan_id,
                "stopped_reason": gate, "next_command": "agentdeck leader summary"}

    monkeypatch.setattr(cli, "_run_loop_single_wave", fake_wave)
    assert cli.main(_serve_argv(root, plan_id, 5)) == 0

    record = read_host_record(root)
    assert record["wave_count"] == 3
    assert record["last_gate"] == "complete"
    assert record["stopped_reason"] == "gate_reached"
    assert record["pid"] is None  # 干净停止清 pid
    lines = _log_lines(root)
    assert [line["wave"] for line in lines if line.get("event") != "host_stopped"] == [1, 2, 3]
    assert all(line["plan_id"] == plan_id for line in lines)
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "run_loop_host_stopped"' in events
    assert '"stopped_reason": "gate_reached"' in events


def test_serve_continues_past_review_iteration_append(tmp_path, monkeypatch, capsys) -> None:
    """Walk-away-chain fix (2026-07-31): a wave that appended a review round
    reports a non-waiting_for_reply gate (needs_human_approval) in its own
    payload -- gate honesty is unchanged -- but must NOT make serve break
    early; the sanctioned next wave gets a chance to auto-approve + dispatch
    the appended rework itself, bounded by --max-waves exactly as ever."""
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": 2, "interval": 0.0,
        "release_boxes": False, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })
    gates = ["needs_human_approval", "waiting_for_reply"]
    review_iterations = [[{"round": 1, "steps": [2, 3], "approval_ids": ["apv_2", "apv_3"],
                            "triggered_by_reply": "rep_1"}], None]
    calls = {"n": 0}

    def fake_wave(_config, _store, wave_plan_id):
        n = calls["n"]
        calls["n"] += 1
        payload = {"ok": True, "mode": "run_loop", "plan_id": wave_plan_id,
                   "stopped_reason": gates[n], "next_command": "agentdeck approval list"}
        if review_iterations[n] is not None:
            payload["review_iterations"] = review_iterations[n]
        return payload

    monkeypatch.setattr(cli, "_run_loop_single_wave", fake_wave)
    assert cli.main(_serve_argv(root, plan_id, 2)) == 0

    record = read_host_record(root)
    # both waves ran -- wave 1's needs_human_approval gate (from the appended
    # round) did NOT stop serve early.
    assert record["wave_count"] == 2
    assert record["last_gate"] == "waiting_for_reply"
    # bounded by --max-waves, not an early gate_reached break after wave 1.
    assert record["stopped_reason"] == "budget_exhausted"
    lines = _log_lines(root)
    assert [line["wave"] for line in lines if line.get("event") != "host_stopped"] == [1, 2]


def test_serve_stops_at_budget(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": 2, "interval": 0.0,
        "release_boxes": False, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })
    monkeypatch.setattr(cli, "_run_loop_single_wave", lambda *_a: {
        "ok": True, "mode": "run_loop", "plan_id": plan_id,
        "stopped_reason": "waiting_for_reply", "next_command": "agentdeck capture-reply",
    })
    assert cli.main(_serve_argv(root, plan_id, 2)) == 0
    record = read_host_record(root)
    assert record["wave_count"] == 2
    assert record["stopped_reason"] == "budget_exhausted"


def test_serve_policy_brake_stops_when_mode_leaves_autonomous(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": 5, "interval": 0.0,
        "release_boxes": False, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })
    waves = {"n": 0}

    def fake_wave(_config, _store, _plan_id):
        waves["n"] += 1
        if waves["n"] == 1:
            # 第一 wave 后人类把模式改回 ask(远程刹车)
            cli.main(["policy", "set-mode", "--mode", "ask"])
            capsys.readouterr()
        return {"ok": True, "mode": "run_loop", "plan_id": plan_id,
                "stopped_reason": "waiting_for_reply", "next_command": "agentdeck capture-reply"}

    monkeypatch.setattr(cli, "_run_loop_single_wave", fake_wave)
    assert cli.main(_serve_argv(root, plan_id, 5)) == 0
    record = read_host_record(root)
    assert record["stopped_reason"] == "policy_revoked"
    assert record["wave_count"] == 1


def test_serve_engine_error_is_recorded_not_crashed(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": 3, "interval": 0.0,
        "release_boxes": False, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })

    def boom(*_args):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(cli, "_run_loop_single_wave", boom)
    assert cli.main(_serve_argv(root, plan_id, 3)) == 1
    record = read_host_record(root)
    assert record["stopped_reason"] == "engine_error"
    # 只记异常类型,不记消息(避免 provider 输出/密钥入日志)
    line = [entry for entry in _log_lines(root) if entry.get("event") == "engine_error"][-1]
    assert line["error_type"] == "RuntimeError"
    assert "exploded" not in json.dumps(_log_lines(root))


def test_serve_signal_finishes_current_wave_then_exits(tmp_path, monkeypatch, capsys) -> None:
    import signal as signal_module

    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": 9, "interval": 0.0,
        "release_boxes": False, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })
    handlers: dict[int, object] = {}
    monkeypatch.setattr(
        cli.signal, "signal", lambda number, handler: handlers.setdefault(number, handler)
    )

    def fake_wave(*_args):
        # wave 执行中收到 SIGTERM:必须完成本 wave 再退出
        handler = handlers.get(signal_module.SIGTERM)
        if handler is not None:
            handler(signal_module.SIGTERM, None)
        return {"ok": True, "mode": "run_loop", "plan_id": plan_id,
                "stopped_reason": "waiting_for_reply", "next_command": "agentdeck capture-reply"}

    monkeypatch.setattr(cli, "_run_loop_single_wave", fake_wave)
    assert cli.main(_serve_argv(root, plan_id, 9)) == 0
    record = read_host_record(root)
    assert record["wave_count"] == 1  # 当前 wave 完整跑完
    assert record["stopped_reason"] == "signalled"


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


def _host_record_for_gate(root: Path, plan_id: str, max_waves: int) -> None:
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": max_waves, "interval": 0.0,
        "release_boxes": True, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })


def _waiting_wave(*_args, **_kwargs) -> dict:
    return {"ok": True, "mode": "run_loop", "plan_id": "pln_host_1",
            "stopped_reason": "waiting_for_reply",
            "next_command": "agentdeck capture-reply"}


# 屏上原文提示是 _detect_waiting_for_input 返回的 **marker 行**,不是选项行
# (终审 2026-08-01 F4:此处原本写成 "› 1. Yes, proceed (y)",自洽但不对应
# 真实取值;live 证据与 delegation contract 示例都是这一句)。
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


def test_serve_stops_on_the_second_consecutive_sighting_of_the_same_human_gate(
    tmp_path, monkeypatch, capsys
) -> None:
    """第一次命中只记候选;第二次同一道框才停,并带出屏上证据。"""
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    _seed_dispatched_approval(root, plan_id, agent_id="coder")
    _host_record_for_gate(root, plan_id, 10)

    scans: list[int] = []

    def fake_scan(_config, _store, _backend, _agent_ids, iteration, source="boxes_watch"):
        scans.append(iteration)
        assert source == "run_loop_host"
        return [], [dict(_undelegated_box(), iteration=iteration)]

    monkeypatch.setattr(cli, "_scan_release_delegated_boxes", fake_scan)
    monkeypatch.setattr(cli, "TmuxBackend", lambda: object())
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)

    assert cli.main(_serve_argv(root, plan_id, 10, release_boxes=True)) == 0

    # 段首扫描(wave 0)+ wave1 后扫描 = 两次同一道框 → 停在 wave 1
    assert scans == [0, 1]
    record = read_host_record(root)
    assert record["stopped_reason"] == "human_gate"
    assert record["wave_count"] == 1
    assert record["human_gate"] == {
        "agent_id": "coder", "box_kind": "command", "command": "playwright open x",
        "mcp_server": None, "mcp_tool": None, "waiting_hint": _REAL_WAITING_HINT,
    }
    gate_lines = [line for line in _log_lines(root) if line.get("event") == "human_gate"]
    assert len(gate_lines) == 1
    assert gate_lines[0]["agent_id"] == "coder"
    assert gate_lines[0]["waiting_hint"] == _REAL_WAITING_HINT
    assert gate_lines[0]["wave"] == 1
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"stopped_reason": "human_gate"' in events


def test_serve_ignores_a_box_on_an_agent_outside_the_awaiting_set(
    tmp_path, monkeypatch, capsys
) -> None:
    """框在别的 agent 身上 → 不是本 plan 的人类门,照常烧到预算上限。"""
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    _seed_dispatched_approval(root, plan_id, agent_id="coder")
    _host_record_for_gate(root, plan_id, 3)

    monkeypatch.setattr(
        cli, "_scan_release_delegated_boxes",
        lambda *_a, **_k: ([], [_undelegated_box(agent_id="reviewer")]),
    )
    monkeypatch.setattr(cli, "TmuxBackend", lambda: object())
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)

    assert cli.main(_serve_argv(root, plan_id, 3, release_boxes=True)) == 0
    record = read_host_record(root)
    assert record["stopped_reason"] == "budget_exhausted"
    assert record["wave_count"] == 3
    assert record["human_gate"] is None


def test_serve_does_not_stop_when_the_box_changes_between_scans(
    tmp_path, monkeypatch, capsys
) -> None:
    """两次命中的是不同的框 → 不判定,重新计数。"""
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    _seed_dispatched_approval(root, plan_id, agent_id="coder")
    _host_record_for_gate(root, plan_id, 4)

    commands = ["a", "b", "a", "b", "a", "b"]
    calls = {"n": 0}

    def alternating_scan(*_a, **_k):
        command = commands[calls["n"]]
        calls["n"] += 1
        return [], [_undelegated_box(command=command)]

    monkeypatch.setattr(cli, "_scan_release_delegated_boxes", alternating_scan)
    monkeypatch.setattr(cli, "TmuxBackend", lambda: object())
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)

    assert cli.main(_serve_argv(root, plan_id, 4, release_boxes=True)) == 0
    record = read_host_record(root)
    assert record["stopped_reason"] == "budget_exhausted"
    assert record["wave_count"] == 4
    assert record["human_gate"] is None
    assert [line for line in _log_lines(root) if line.get("event") == "human_gate"] == []


def test_serve_without_release_boxes_never_detects_a_human_gate(
    tmp_path, monkeypatch, capsys
) -> None:
    """不开 --release-boxes:零 pane 读取,行为逐字节不变。"""
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    _seed_dispatched_approval(root, plan_id, agent_id="coder")
    _host_record_for_gate(root, plan_id, 2)

    scanned: list[int] = []

    def never_scan(*_a, **_k):
        scanned.append(1)
        return [], [_undelegated_box()]

    monkeypatch.setattr(cli, "_scan_release_delegated_boxes", never_scan)
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)

    assert cli.main(_serve_argv(root, plan_id, 2, release_boxes=False)) == 0
    assert scanned == []  # 一次 pane 都没读
    record = read_host_record(root)
    assert record["stopped_reason"] == "budget_exhausted"
    assert record["wave_count"] == 2
    assert record["human_gate"] is None


def test_human_gate_stop_never_triggers_the_automatic_merge(
    tmp_path, monkeypatch, capsys
) -> None:
    """人类门停止不是 complete gate:绝不触发 --merge-on-complete 的自动合并。"""
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    _seed_dispatched_approval(root, plan_id, agent_id="coder")
    _host_record_for_gate(root, plan_id, 10)

    merges: list[str] = []
    monkeypatch.setattr(
        cli, "_scan_release_delegated_boxes", lambda *_a, **_k: ([], [_undelegated_box()])
    )
    monkeypatch.setattr(cli, "TmuxBackend", lambda: object())
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)
    monkeypatch.setattr(
        cli, "_merge_plan_worktrees",
        lambda _config, _store, merged_plan: merges.append(merged_plan) or {"ok": True},
    )
    monkeypatch.setattr(cli, "_verdict_merge_blocker", lambda _store, _plan: None)

    assert cli.main(
        _serve_argv(root, plan_id, 10, release_boxes=True, merge_on_complete=True)
    ) == 0
    record = read_host_record(root)
    assert record["stopped_reason"] == "human_gate"
    assert merges == []
    assert [line for line in _log_lines(root) if line.get("event") == "plan_merge"] == []


def test_stop_requires_confirm_and_refuses_without_record(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    assert cli.main(["run-loop-host", "stop"]) == 1
    assert "confirm" in capsys.readouterr().err
    assert cli.main(["run-loop-host", "stop", "--confirm"]) == 1
    assert "no run-loop host" in capsys.readouterr().err
    assert read_host_record(root) is None


def test_stop_signals_live_host_and_reports(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    write_host_record(root, {
        "pid": 999_010, "plan_id": "pln_host_1", "wave_count": 6, "max_waves": 40,
        "interval": 10.0, "last_gate": "waiting_for_reply", "last_wave_at": None,
        "stopped_reason": None, "log_path": ".agentdeck/run-loop-host/host.log",
    })
    signals: list[tuple[int, int]] = []
    liveness = {"alive": True}

    def fake_kill(pid: int, number: int) -> None:
        signals.append((pid, number))
        liveness["alive"] = False  # 子进程接受信号后退出

    monkeypatch.setattr(cli.os, "kill", fake_kill)
    monkeypatch.setattr(cli, "_host_pid_alive", lambda _pid: liveness["alive"])
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)

    assert cli.main(["run-loop-host", "stop", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_host_stopped"
    assert payload["pid"] == 999_010
    assert payload["wave_count"] == 6
    import signal as signal_module
    assert signals == [(999_010, signal_module.SIGTERM)]
    assert read_host_record(root)["pid"] is None
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "run_loop_host_stopped"' in events
    assert '"source": "explicit"' in events


def test_stop_timeout_keeps_record_and_never_kills(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    write_host_record(root, {
        "pid": 999_011, "plan_id": "pln_host_1", "wave_count": 2, "max_waves": 40,
        "interval": 10.0, "last_gate": "waiting_for_reply", "last_wave_at": None,
        "stopped_reason": None, "log_path": ".agentdeck/run-loop-host/host.log",
    })
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(cli.os, "kill", lambda pid, number: signals.append((pid, number)))
    monkeypatch.setattr(cli, "_host_pid_alive", lambda _pid: True)  # 永不退出
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    # 缩短有界等待,避免测试真实忙等 60s;超时语义本身不变
    monkeypatch.setattr(cli, "_HOST_STOP_TIMEOUT_SECONDS", 0.05)

    assert cli.main(["run-loop-host", "stop", "--confirm"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_host_stop_timed_out"
    import signal as signal_module
    assert {number for _pid, number in signals} == {signal_module.SIGTERM}  # 绝不 SIGKILL
    assert read_host_record(root)["pid"] == 999_011  # 记录保留给人工


def test_stop_clears_stale_record(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    write_host_record(root, {
        "pid": 999_012, "plan_id": "pln_host_1", "wave_count": 3, "max_waves": 40,
        "interval": 10.0, "last_gate": "waiting_for_reply", "last_wave_at": None,
        "stopped_reason": None, "log_path": ".agentdeck/run-loop-host/host.log",
    })
    monkeypatch.setattr(cli, "_host_pid_alive", lambda _pid: False)
    killed: list[int] = []
    monkeypatch.setattr(cli.os, "kill", lambda pid, _n: killed.append(pid))

    assert cli.main(["run-loop-host", "stop", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_host_stale_cleared"
    assert killed == []  # 死进程不发信号
    assert read_host_record(root)["pid"] is None


# ── 端到端:真实检测器,不 mock 扫描 ────────────────────────────────────
#
# 终审 2026-08-01 指出的覆盖缺口:上面每个 serve 级人类门测试都 monkeypatch
# 掉了 `_scan_release_delegated_boxes`,于是 pane 文本 → 框解析 → 候选这一
# 整段从未被端到端执行过。F1(已答复的折叠框停掉健康走开段)正是因此活过
# 了七个 commit 和一次 live PASS——live 那次恰好是一道真待批框,只走了
# happy path。下面的测试喂真实 pane 文本、跑真实扫描。

_PENDING_BOX_PANE = """\
  Would you like to run the following command?

  $ ./scripts/smoke.sh --dry-run

› 1. Yes, proceed (y)
  2. Yes, and don't ask again for commands that start with `./scripts/` (p)
  3. No, and tell Codex what to do differently (esc)

  Press enter to confirm or esc to cancel
"""

# 已答复的框折叠成单行历史:marker "Would you like to run" 仍在,但正文与
# 选项列表都没了 —— 屏上没有任何东西在等人按。
_ANSWERED_BOX_PANE = """\
• Ran node tests/a.mjs
  Would you like to run the following command? -> Yes
• Working (esc to interrupt)
"""

# 更阴险的一种:已答复的框上方还留着一条陈旧的 `$ ` 行,提取器会刮出一条
# 从来没有对应过任何框的命令。
_ANSWERED_BOX_WITH_STALE_COMMAND_PANE = """\
• Ran node tests/a.mjs
  Would you like to run the following command? -> Yes
  $ ./scripts/deploy.sh --dry-run
• Working (esc to interrupt)
"""


class _PaneBackend:
    """只回放固定 pane 文本;记录是否有人往 pane 里发过按键。"""

    def __init__(self, pane_text: str) -> None:
        self.pane_text = pane_text
        self.sent: list[tuple[str, str]] = []

    def capture_output(self, _runtime, pane_id: str, _lines: int) -> str:
        return self.pane_text

    def send_input(self, _runtime, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))


def _seed_running_agent(root: Path, agent_id: str = "coder", pane_id: str = "%9") -> None:
    store = StateStore(root)
    state = store.load()
    state.setdefault("agents", {})[agent_id] = {
        "agent_id": agent_id, "status": "running", "pane_id": pane_id,
        "session": "agentdeck-test", "cwd": str(root),
    }
    store.save(state)


def _serve_against_pane(root, plan_id, monkeypatch, capsys, pane_text, max_waves=4):
    enable_autonomous(capsys)
    _seed_dispatched_approval(root, plan_id, agent_id="coder")
    _seed_running_agent(root)
    _host_record_for_gate(root, plan_id, max_waves)
    backend = _PaneBackend(pane_text)
    monkeypatch.setattr(cli, "TmuxBackend", lambda: backend)
    monkeypatch.setattr(cli, "_run_loop_single_wave", _waiting_wave)
    # `_scan_release_delegated_boxes` 刻意 **不** mock —— 这正是本节的意义。
    assert cli.main(_serve_argv(root, plan_id, max_waves, release_boxes=True)) == 0
    return backend, read_host_record(root)


def test_serve_stops_on_a_real_pending_box_through_the_unmocked_scanner(
    tmp_path, monkeypatch, capsys
) -> None:
    """真待批框走完整条 pane→解析→候选→debounce 链路,必须停。"""
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    backend, record = _serve_against_pane(
        root, plan_id, monkeypatch, capsys, _PENDING_BOX_PANE
    )

    assert record["stopped_reason"] == "human_gate"
    assert record["wave_count"] == 1
    gate = record["human_gate"]
    assert gate["agent_id"] == "coder"
    assert gate["box_kind"] == "command"
    assert gate["command"] == "./scripts/smoke.sh --dry-run"
    assert gate["waiting_hint"] == _REAL_WAITING_HINT
    # 绝不代按:检测路径一个按键都不发
    assert backend.sent == []


def test_serve_does_not_stop_on_an_already_answered_collapsed_box(
    tmp_path, monkeypatch, capsys
) -> None:
    """终审 F1 回归:已答复的折叠框产出全 None 身份,全 None 恒等于自身,
    debounce 会必然确认 —— 修复前这里会误停一个健康的走开段。"""
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    backend, record = _serve_against_pane(
        root, plan_id, monkeypatch, capsys, _ANSWERED_BOX_PANE
    )

    assert record["stopped_reason"] == "budget_exhausted"
    assert record["human_gate"] is None
    assert [line for line in _log_lines(root) if line.get("event") == "human_gate"] == []
    assert backend.sent == []


def test_serve_does_not_stop_on_a_stale_command_line_above_an_answered_box(
    tmp_path, monkeypatch, capsys
) -> None:
    """终审 F2 回归:陈旧 `$ ` 行能刮出一条从未对应过任何框的命令
    (box_kind=command),仅靠身份非空挡不住 —— 必须要求屏上确有待批框。"""
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    backend, record = _serve_against_pane(
        root, plan_id, monkeypatch, capsys, _ANSWERED_BOX_WITH_STALE_COMMAND_PANE
    )

    assert record["stopped_reason"] == "budget_exhausted"
    assert record["human_gate"] is None
    assert backend.sent == []
