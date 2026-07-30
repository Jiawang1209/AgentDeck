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
