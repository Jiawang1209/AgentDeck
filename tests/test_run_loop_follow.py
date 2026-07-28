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
