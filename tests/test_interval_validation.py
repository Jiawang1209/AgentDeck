"""A negative `--interval` must be refused at every entry point that accepts it.

Why this is a safety gate and not merely input hygiene: every consumer guards
the sleep with `if interval > 0: time.sleep(...)`, so a negative interval
silently means "never sleep". The wave budget is a *bound* the human reads as
wall-clock walk-away time --
`run-loop-host start --confirm --max-waves 300 --interval -5` looks like
"300 waves over ~8 hours" and actually burns all 300 waves as fast as the
machine allows. `goal preview` even prints the interval on its confirmation
screen ("每 10s 一轮"), so a nonsense value there is a displayed fact that does
not hold.

Zero stays legal: it means "no sleep between waves", it is explicit, and it is
visible in the preview. Only negative is nonsense that silently behaves like
zero.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.run_loop_host import read_host_record
from agentdeck.state import StateStore


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    """A default project whose Leader is the local fake provider (dry-run)."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    text = (
        config_path.read_text(encoding="utf-8")
        .replace('provider = "deepseek"', 'provider = "fake"', 1)
        .replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    )
    config_path.write_text(text, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def enable_autonomous(capsys, max_approvals: int = 20) -> None:
    cli.main([
        "policy", "set-mode", "--mode", "autonomous", "--confirm",
        "--allow-agent", "coder", "--max-approvals", str(max_approvals),
    ])
    capsys.readouterr()


def seed_plan(root: Path, plan_id: str = "pln_interval_1") -> str:
    store = StateStore(root)
    state = store.load()
    state.setdefault("plans", []).append({
        "plan_id": plan_id,
        "goal": "interval test",
        "summary": "interval test",
        "steps": [{"step": 1, "agent_id": "coder", "role": "implementation",
                   "task": "do work", "risk": "low", "requires_approval": True}],
    })
    store.save(state)
    return plan_id


def event_types(root: Path) -> list[str]:
    path = root / ".agentdeck" / "state" / "events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)["event_type"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class RecordingSpawn:
    def __init__(self, pid: int = 999_101) -> None:
        self.pid = pid
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, argv: list[str], cwd: Path) -> int:
        self.calls.append((list(argv), str(cwd)))
        return self.pid


# 八个入口的完整清单。新增一个接受 `--interval` 的命令时必须同步这张表,
# 否则该入口就会重新落回"负值静默当零用"的老缺陷。
ENTRY_POINTS = [
    (
        "run-loop --follow",
        "run-loop",
        ["run-loop", "--plan-id", "pln_interval_1", "--confirm", "--follow",
         "--max-waves", "2", "--interval", "-5"],
    ),
    (
        "goal preview",
        "goal preview",
        ["goal", "preview", "--task", "让测试全绿", "--interval", "-5"],
    ),
    (
        "goal start",
        "goal start",
        ["goal", "start", "--plan-id", "pln_interval_1", "--confirm", "--interval", "-5"],
    ),
    (
        "run-loop-host start",
        "run-loop-host start",
        ["run-loop-host", "start", "--plan-id", "pln_interval_1", "--confirm",
         "--max-waves", "5", "--interval", "-5"],
    ),
    (
        "run-loop-host serve",
        "run-loop-host serve",
        ["run-loop-host", "serve", "--project", ".", "--plan-id", "pln_interval_1",
         "--max-waves", "5", "--interval", "-5"],
    ),
    (
        "workbench --watch",
        "workbench",
        ["workbench", "--watch", "--iterations", "2", "--interval", "-5"],
    ),
    (
        "dashboard --watch",
        "dashboard",
        ["dashboard", "--watch", "--iterations", "2", "--interval", "-5"],
    ),
    (
        "boxes watch",
        "boxes watch",
        ["boxes", "watch", "--agent", "coder", "--confirm", "--iterations", "2",
         "--interval", "-5"],
    ),
]


@pytest.mark.parametrize(
    "label,command_name,argv",
    ENTRY_POINTS,
    ids=[entry[0] for entry in ENTRY_POINTS],
)
def test_every_entry_point_refuses_a_negative_interval(
    tmp_path, monkeypatch, capsys, label, command_name, argv
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    seed_plan(root)
    enable_autonomous(capsys)
    monkeypatch.setattr(cli, "_spawn_host_process", RecordingSpawn())

    assert cli.main(list(argv)) == 1, f"{label} accepted a negative --interval"
    err = capsys.readouterr().err
    assert f"{command_name} requires --interval >= 0" in err, err


def test_all_eight_interval_definitions_are_covered() -> None:
    """The parser must not grow a ninth `--interval` without a gate for it."""
    source = (Path(cli.__file__)).read_text(encoding="utf-8")
    assert source.count('add_argument("--interval"') == len(ENTRY_POINTS)


def test_run_loop_host_start_refuses_with_zero_writes_and_zero_spawn(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    spawn = RecordingSpawn()
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)

    state_path = StateStore(root).state_path
    before = state_path.read_bytes()
    before_events = event_types(root)

    assert cli.main([
        "run-loop-host", "start", "--plan-id", plan_id, "--confirm",
        "--max-waves", "300", "--interval", "-5",
    ]) == 1
    assert "run-loop-host start requires --interval >= 0" in capsys.readouterr().err

    assert state_path.read_bytes() == before
    assert event_types(root) == before_events
    assert spawn.calls == []
    assert read_host_record(root) is None


def test_goal_start_refuses_with_zero_writes_and_zero_spawn(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    spawn = RecordingSpawn()
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)
    enable_autonomous(capsys)
    assert cli.main(["goal", "preview", "--task", "让测试全绿", "--json"]) == 0
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]

    state_path = StateStore(root).state_path
    before = state_path.read_bytes()
    before_events = event_types(root)

    assert cli.main([
        "goal", "start", "--plan-id", plan_id, "--confirm", "--interval", "-5",
    ]) == 1
    assert "goal start requires --interval >= 0" in capsys.readouterr().err

    assert state_path.read_bytes() == before
    assert event_types(root) == before_events
    assert spawn.calls == []
    assert read_host_record(root) is None


def test_goal_preview_refuses_before_the_provider_call(tmp_path, monkeypatch, capsys) -> None:
    """A bad flag must not cost a Leader API round trip."""
    prepare_project(tmp_path, monkeypatch)
    calls: list[str] = []

    def _explode(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("goal preview called the Leader provider behind a bad --interval")

    monkeypatch.setattr(cli, "_generate_leader_plan", _explode)

    assert cli.main([
        "goal", "preview", "--task", "让测试全绿", "--interval", "-5",
    ]) == 1
    assert "goal preview requires --interval >= 0" in capsys.readouterr().err
    assert calls == []


def test_zero_interval_stays_legal(tmp_path, monkeypatch, capsys) -> None:
    """Zero means "no sleep between waves" -- explicit, visible, and legal."""
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)

    assert cli.main(["workbench", "--watch", "--iterations", "2", "--interval", "0"]) == 0
    capsys.readouterr()
    assert cli.main(["dashboard", "--watch", "--iterations", "2", "--interval", "0"]) == 0
    capsys.readouterr()
    assert cli.main([
        "goal", "preview", "--task", "让测试全绿", "--interval", "0", "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["budget"]["interval"] == 0.0
