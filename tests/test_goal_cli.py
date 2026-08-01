from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import load_config, write_default_config
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


def preview_json(capsys, *extra: str) -> dict:
    exit_code = cli.main(["goal", "preview", "--task", "让测试全绿", "--json", *extra])
    assert exit_code == 0
    return json.loads(capsys.readouterr().out)


def test_goal_preview_lays_out_the_whole_authorization_under_defaults(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)

    payload = preview_json(capsys)

    assert payload["ok"] is True
    assert payload["mode"] == "goal_preview"
    assert payload["task"] == "让测试全绿"
    assert payload["plan_id"].startswith("pln_")
    assert payload["step_count"] == len(payload["steps"]) == 3
    assert [step["agent_id"] for step in payload["steps"]] == ["planner", "coder", "reviewer"]
    assert set(payload["steps"][0]) == {"step", "agent_id", "role", "task"}
    # user 拍板的两点缺省
    assert payload["budget"]["max_waves"] == 300
    assert payload["budget"]["max_waves_is_default"] is True
    assert payload["budget"]["max_approvals"] == 20
    assert payload["budget"]["max_review_rounds"] == 2
    assert payload["release_boxes"] is True
    assert payload["merge_on_complete"] is False
    assert payload["blocker"] is None
    assert payload["confirm_command"] == (
        f"agentdeck goal start --plan-id {payload['plan_id']} --confirm --max-waves 300"
    )
    assert payload["safety"] == "explicit_user"
    assert payload["requires_explicit_user"] is True
    # 停下来找你的条件必须至少覆盖 spec 点名的四条
    kinds = {item["kind"] for item in payload["stop_conditions"]}
    assert {
        "review_passed_awaiting_merge",
        "human_gate",
        "review_budget_exhausted",
        "approval_outside_allowlist",
    } <= kinds

    # preview 写 plan(与 leader plan 等价),但不批准、不派发
    state = StateStore(root).load()
    assert [plan["plan_id"] for plan in state["plans"]] == [payload["plan_id"]]
    assert state.get("approvals", []) == []
    assert state.get("messages", []) == []


def test_goal_preview_explicit_max_waves_is_not_a_default(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)

    payload = preview_json(capsys, "--max-waves", "50")

    assert payload["budget"]["max_waves"] == 50
    assert payload["budget"]["max_waves_is_default"] is False
    assert payload["confirm_command"].endswith("--confirm --max-waves 50")


def test_goal_preview_carries_explicit_merge_and_release_choices(
    tmp_path, monkeypatch, capsys
) -> None:
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)

    payload = preview_json(capsys, "--merge-on-complete", "--no-release-boxes")

    assert payload["merge_on_complete"] is True
    assert payload["release_boxes"] is False
    assert "--merge-on-complete" in payload["confirm_command"]
    assert "--no-release-boxes" in payload["confirm_command"]
    kinds = {item["kind"] for item in payload["stop_conditions"]}
    assert "review_passed_merged" in kinds
    assert "review_passed_awaiting_merge" not in kinds


def test_goal_preview_never_flips_approval_mode_and_creates_nothing_to_approve(
    tmp_path, monkeypatch, capsys
) -> None:
    """最重要的一条边界:非 autonomous 项目只报 blocker,绝不代人翻开关。"""
    root = prepare_project(tmp_path, monkeypatch)
    before_config = (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8")

    payload = preview_json(capsys)

    assert payload["blocker"] is not None
    assert "policy set-mode --mode autonomous --confirm" in payload["blocker"]
    assert "--allow-agent <id>" in payload["blocker"]
    assert "--max-approvals <N>" in payload["blocker"]
    assert payload["confirm_command"] is None
    next_control = next(c for c in payload["controls"] if c["kind"] == "next")
    assert next_control["enabled"] is False
    assert next_control["blocker"] == payload["blocker"]

    state = StateStore(root).load()
    assert state.get("approvals", []) == []
    assert (root / ".agentdeck" / "config.toml").read_text(encoding="utf-8") == before_config
    assert load_config(root).leader.approval_mode != "autonomous"


def test_goal_preview_default_output_is_a_human_render_not_a_json_dump(
    tmp_path, monkeypatch, capsys
) -> None:
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)

    assert cli.main(["goal", "preview", "--task", "让测试全绿"]) == 0
    rendered = capsys.readouterr().out

    assert not rendered.lstrip().startswith("{")
    assert "将要授权" in rendered
    assert "pln_" in rendered
    assert "300" in rendered
    assert "缺省值" in rendered and "--max-waves" in rendered
    assert "agentdeck goal start --plan-id pln_" in rendered
    # 同一份数据:渲染里的确认命令与 --json payload 的字段同源
    payload = preview_json(capsys)
    assert payload["confirm_command"].startswith("agentdeck goal start --plan-id pln_")


def test_goal_preview_render_shows_active_delegations_as_display_only(
    tmp_path, monkeypatch, capsys
) -> None:
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    assert cli.main([
        "delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm",
    ]) == 0
    capsys.readouterr()

    payload = preview_json(capsys)

    assert [item["prefix"] for item in payload["delegations"]] == ["node tests/"]
    assert payload["delegations"][0]["agent_id"] == "coder"
    assert payload["delegations"][0]["kind"] == "command_prefix"

    assert cli.main(["goal", "preview", "--task", "让测试全绿"]) == 0
    assert "node tests/" in capsys.readouterr().out


class RecordingSpawn:
    def __init__(self, pid: int = 999_001) -> None:
        self.pid = pid
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, argv: list[str], cwd) -> int:
        self.calls.append((list(argv), str(cwd)))
        return self.pid


def event_types(root: Path) -> list[str]:
    path = root / ".agentdeck" / "state" / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)["event_type"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_goal_start_four_gates_refuse_with_zero_writes_and_zero_spawn(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    spawn = RecordingSpawn()
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)
    plan_id = preview_json(capsys)["plan_id"]  # 非 autonomous 时也会产出 plan
    state_path = StateStore(root).state_path
    before = state_path.read_bytes()
    before_events = event_types(root)

    # 1. 缺 --confirm
    assert cli.main(["goal", "start", "--plan-id", plan_id]) == 1
    assert "confirm" in capsys.readouterr().err
    # 2. 非 autonomous 模式(goal 绝不代人翻这个开关)
    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm"]) == 1
    err = capsys.readouterr().err
    assert "autonomous" in err and "policy set-mode" in err

    enable_autonomous(capsys)
    state_path = StateStore(root).state_path
    before = state_path.read_bytes()
    before_events = event_types(root)
    # 3. 未知 plan
    assert cli.main(["goal", "start", "--plan-id", "pln_ghost", "--confirm"]) == 1
    assert "unknown plan" in capsys.readouterr().err
    # 4. --max-waves < 1(缺省 300 也要过这一关)
    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm", "--max-waves", "0"]) == 1
    assert "max-waves" in capsys.readouterr().err

    assert state_path.read_bytes() == before
    assert event_types(root) == before_events
    assert spawn.calls == []
    from agentdeck.run_loop_host import read_host_record

    assert read_host_record(root) is None


def test_goal_start_approves_then_hosts_and_audits_both(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    spawn = RecordingSpawn(pid=999_007)
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)
    plan_id = preview_json(capsys)["plan_id"]

    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["mode"] == "goal_start"
    assert payload["plan_id"] == plan_id
    assert payload["approved_count"] == 3
    assert payload["host_pid"] == 999_007
    assert payload["max_waves"] == 300
    assert payload["release_boxes"] is True
    assert payload["merge_on_complete"] is False
    assert payload["status_command"] == "agentdeck run-loop-host status"
    assert payload["stop_command"] == "agentdeck run-loop-host stop --confirm"
    assert payload["next_command"] == "agentdeck run-loop-host status"
    assert payload["safety"] == "delegated"
    assert payload["requires_explicit_user"] is True

    # 顺序:先批准,再启动宿主;宿主自己的事件不得被抑制
    types = event_types(root)
    assert types.index("approval_plan_approved") < types.index("run_loop_host_started")
    assert types.index("run_loop_host_started") < types.index("goal_started")

    # 缺省透传给宿主
    argv, cwd = spawn.calls[0]
    assert argv[:3] == ["agentdeck", "run-loop-host", "serve"]
    assert "--max-waves" in argv and "300" in argv
    assert "--release-boxes" in argv
    assert "--merge-on-complete" not in argv
    assert cwd == str(root)

    state = StateStore(root).load()
    plan_approvals = [a for a in state["approvals"] if a["plan_id"] == plan_id]
    assert len(plan_approvals) == 3
    assert {a["status"] for a in plan_approvals} == {"approved"}
    from agentdeck.run_loop_host import read_host_record

    assert read_host_record(root)["pid"] == 999_007


def test_goal_start_passes_explicit_budget_and_switches_through(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    spawn = RecordingSpawn()
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)
    plan_id = preview_json(capsys, "--max-waves", "7", "--merge-on-complete")["plan_id"]

    assert cli.main([
        "goal", "start", "--plan-id", plan_id, "--confirm",
        "--max-waves", "7", "--merge-on-complete", "--no-release-boxes",
    ]) == 0
    rendered = capsys.readouterr().out
    assert not rendered.lstrip().startswith("{")
    assert "999001" in rendered or "999,001" in rendered

    argv, _cwd = spawn.calls[0]
    assert "7" in argv and "--merge-on-complete" in argv
    assert "--release-boxes" not in argv
    from agentdeck.run_loop_host import read_host_record

    record = read_host_record(root)
    assert record["max_waves"] == 7
    assert record["merge_on_complete"] is True
    assert record["release_boxes"] is False


def test_goal_start_never_spawns_the_host_when_approve_fails(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    spawn = RecordingSpawn()
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)
    plan_id = preview_json(capsys)["plan_id"]
    # 人类已手工批准过 → approve 阶段没有 pending 可批,必须失败
    assert cli.main(["approval", "create-from-plan", "--plan-id", plan_id]) == 0
    assert cli.main(["approval", "approve-plan", "--plan-id", plan_id, "--confirm"]) == 0
    capsys.readouterr()

    before_events = event_types(root)

    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no pending approvals" in captured.err

    assert event_types(root) == before_events  # 失败的 approve 阶段一个事件都不写
    assert spawn.calls == []
    from agentdeck.run_loop_host import read_host_record

    assert read_host_record(root) is None
    assert "goal_started" not in event_types(root)
    assert "run_loop_host_started" not in event_types(root)


def test_validate_goal_start_contract_guards_the_response() -> None:
    from agentdeck.contracts import goal_start_example, validate_goal_start_contract

    example = goal_start_example()
    assert validate_goal_start_contract(example) == {"ok": True, "errors": []}

    broken = dict(example, max_waves=0)
    result = validate_goal_start_contract(broken)
    assert result["ok"] is False
    assert any("max_waves" in error for error in result["errors"])


def test_validate_goal_preview_contract_rejects_confirm_command_behind_a_blocker() -> None:
    from agentdeck.contracts import goal_preview_example, validate_goal_preview_contract

    example = goal_preview_example()
    assert validate_goal_preview_contract(example) == {"ok": True, "errors": []}

    broken = dict(example)
    broken["blocker"] = "autonomous mode is not enabled"
    result = validate_goal_preview_contract(broken)
    assert result["ok"] is False
    assert any("confirm_command" in error for error in result["errors"])


def test_goal_contract_is_discoverable_for_gui_clients(capsys) -> None:
    assert cli.main(["contract", "goal"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "goal"
    assert payload["preview_command_template"].startswith("agentdeck goal preview --task")
    assert payload["contract_path"].endswith("docs/contracts/goal-schema.md")
    assert payload["default_max_waves"] == 300

    assert cli.main(["contract", "goal", "--example"]) == 0
    example = json.loads(capsys.readouterr().out)["example"]["preview"]
    assert example["mode"] == "goal_preview"

    assert cli.main(["contract", "list"]) == 0
    names = [item["name"] for item in json.loads(capsys.readouterr().out)["contracts"]]
    assert "goal" in names
