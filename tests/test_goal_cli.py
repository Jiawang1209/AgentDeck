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
