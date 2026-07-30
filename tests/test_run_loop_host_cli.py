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


def _serve_argv(root: Path, plan_id: str, max_waves: int, interval: str = "0") -> list[str]:
    return [
        "run-loop-host", "serve", "--project", str(root),
        "--plan-id", plan_id, "--max-waves", str(max_waves), "--interval", interval,
    ]


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
