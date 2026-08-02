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
    # 字段清单随 in_allowlist 一起生长,断言仍然逐字段精确
    assert set(payload["steps"][0]) == {"step", "agent_id", "role", "task", "in_allowlist"}
    # user 拍板的两点缺省
    assert payload["budget"]["max_waves"] == 300
    assert payload["budget"]["max_waves_is_default"] is True
    assert payload["budget"]["max_approvals"] == 20
    assert payload["budget"]["max_review_rounds"] == 2
    assert payload["budget"]["allowed_agents"] == ["coder"]
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


# 2026-08-01 live 发现:真实项目有 22 条活跃委托,分属三个 agent,各自
# 独立 grant 了重叠前缀。旧渲染把 agent_id 拍平,同一行 22 项读起来像
# 一堆重复噪声,人类无法看出"哪个 agent 被授权了什么"——而这正是委托
# 授予的那个事实。下面这组用例把真实形状钉进测试。
LIVE_22_DELEGATIONS: tuple[tuple[str, str], ...] = (
    ("coder", "node tests/"),
    ("coder", "git add"),
    ("coder", "git commit"),
    ("coder", "git diff"),
    ("coder", "git status"),
    ("planner", "node tests/"),
    ("planner", "git add"),
    ("planner", "git commit"),
    ("planner", "git diff"),
    ("planner", "git status"),
    ("reviewer", "node tests/"),
    ("reviewer", "git add"),
    ("reviewer", "git commit"),
    ("reviewer", "git diff"),
    ("reviewer", "git status"),
    ("coder", "node --check tests/"),
    ("coder", "rg -n"),
    ("coder", "git log"),
)

LIVE_22_MCP_DELEGATIONS: tuple[tuple[str, str, str], ...] = (
    ("coder", "chrome-devtools", "hover"),
    ("coder", "chrome-devtools", "press_key"),
    ("planner", "chrome-devtools", "hover"),
    ("planner", "chrome-devtools", "press_key"),
)


def grant_live_22_delegations(capsys) -> None:
    """复刻 live 项目的 22 条活跃委托(三个 agent,前缀互相重叠)。"""
    for agent, prefix in LIVE_22_DELEGATIONS:
        assert cli.main([
            "delegation", "grant", "--agent", agent, "--prefix", prefix, "--confirm",
        ]) == 0
    for agent, server, tool in LIVE_22_MCP_DELEGATIONS:
        assert cli.main([
            "delegation", "grant", "--agent", agent,
            "--mcp-server", server, "--mcp-tool", tool, "--confirm",
        ]) == 0
    capsys.readouterr()


def delegation_block(rendered: str) -> list[str]:
    """渲染里从 `委托` 标签起、到下一个顶层标签前的那一段。"""
    lines = rendered.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("  委托"))
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.startswith("  ") and not line.startswith("   "):
            break
        block.append(line)
    return block


def test_live_22_delegation_fixture_matches_delegation_list_active_count(
    tmp_path, monkeypatch, capsys
) -> None:
    """先钉住 fixture 本身:22 条,且都活跃(`items` 键,不是 `delegations`)。"""
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    grant_live_22_delegations(capsys)

    assert cli.main(["delegation", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 22
    active = [item for item in listed["items"] if item["active"]]
    assert len(active) == 22


def test_goal_preview_render_groups_delegations_by_agent(
    tmp_path, monkeypatch, capsys
) -> None:
    """每个 agent 一行,agent id 可见——人类要看得出谁被授权了什么。"""
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    grant_live_22_delegations(capsys)

    assert cli.main(["goal", "preview", "--task", "让测试全绿"]) == 0
    block = delegation_block(capsys.readouterr().out)

    # 三个 agent 各占一行,且每行只出现自己的 agent id
    agent_lines = [line for line in block if any(a in line for a in ("coder", "planner", "reviewer"))]
    assert len(agent_lines) == 3
    assert sum("coder" in line for line in agent_lines) == 1
    assert sum("planner" in line for line in agent_lines) == 1
    assert sum("reviewer" in line for line in agent_lines) == 1
    # reviewer 那一行不得混进只属于 coder 的前缀
    reviewer_line = next(line for line in agent_lines if "reviewer" in line)
    assert "rg -n" not in reviewer_line
    assert "git log" not in reviewer_line


def test_goal_preview_render_caps_each_agent_line_and_counts_the_rest(
    tmp_path, monkeypatch, capsys
) -> None:
    """长清单必须收口:每 agent 至多 6 项,余量以计数呈现,绝不再摊 22 项。"""
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    grant_live_22_delegations(capsys)

    assert cli.main(["goal", "preview", "--task", "让测试全绿"]) == 0
    block = delegation_block(capsys.readouterr().out)

    coder_line = next(line for line in block if "coder" in line)
    # coder 有 10 条:显示 6 条 + 余 4 条的计数
    assert coder_line.count(",") <= 5
    assert "4" in coder_line
    # reviewer 只有 5 条,不该出现余量计数
    reviewer_line = next(line for line in block if "reviewer" in line)
    assert "还有" not in reviewer_line
    # 任何一行都不该长到不可扫读
    assert all(len(line) < 120 for line in block)


def test_goal_preview_render_keeps_the_honest_total_and_release_semantics(
    tmp_path, monkeypatch, capsys
) -> None:
    """总数必须仍与 `delegation list` 的活跃条数一致,放行语义保持不变。"""
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    grant_live_22_delegations(capsys)

    assert cli.main(["goal", "preview", "--task", "让测试全绿"]) == 0
    block = delegation_block(capsys.readouterr().out)
    text = "\n".join(block)

    assert "22 条活跃委托" in text
    assert "遇到即自动放行" in text

    assert cli.main(["goal", "preview", "--task", "让测试全绿", "--no-release-boxes"]) == 0
    text = "\n".join(delegation_block(capsys.readouterr().out))
    assert "22 条活跃委托" in text
    assert "--no-release-boxes" in text
    assert "遇到即自动放行" not in text


def test_goal_preview_render_zero_delegations_is_a_distinct_walkaway_warning(
    tmp_path, monkeypatch, capsys
) -> None:
    """零委托是实质不同的走开体验:必须明说没有框会被自动放行。"""
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)

    assert cli.main(["goal", "preview", "--task", "让测试全绿"]) == 0
    text = "\n".join(delegation_block(capsys.readouterr().out))

    assert "无活跃委托" in text
    assert "不会自动放行" in text
    assert "停下来" in text
    assert "条活跃委托,遇到即自动放行" not in text


# 2026-08-01 终审发现:preview 印着"审批预算 N"和"白名单外的 agent 需要审批",
# 但 `goal start` 阶段 1 复用的 `approval approve-plan --confirm` 是**人类批准**,
# 它一次性批准该 plan 的全部 pending 审批,既不看 allowlist 也不看 max_approvals
# (那两条只约束 autonomous **自动**批准,即后续返工轮)。行为是对的——人类批准
# 一向对 allowlist 免疫——错的是那块屏:它印了两条不约束这次确认的界,又完全
# 不印真正决定后续自动批准范围的白名单本身。下面这组用例把"信息完整"钉死。
def test_goal_preview_shows_the_autonomous_allowlist(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys, max_approvals=1)

    payload = preview_json(capsys)

    assert payload["budget"]["allowed_agents"] == ["coder"]
    assert payload["budget"]["max_approvals"] == 1


def test_goal_preview_marks_which_steps_fall_outside_the_allowlist(
    tmp_path, monkeypatch, capsys
) -> None:
    """人类要看得出:这次确认放行的到底有谁,其中谁在 autonomous 集合之外。"""
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys, max_approvals=1)

    payload = preview_json(capsys)

    assert [step["agent_id"] for step in payload["steps"]] == ["planner", "coder", "reviewer"]
    assert [step["in_allowlist"] for step in payload["steps"]] == [False, True, False]
    # 字段清单同步生长,且仍然逐字段精确
    assert set(payload["steps"][0]) == {"step", "agent_id", "role", "task", "in_allowlist"}


def test_goal_preview_render_says_this_confirmation_approves_every_step(
    tmp_path, monkeypatch, capsys
) -> None:
    """屏上必须写明:这一次确认一次性批准全部 N 步,白名单外的也在内。"""
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys, max_approvals=1)

    assert cli.main(["goal", "preview", "--task", "让测试全绿"]) == 0
    rendered = capsys.readouterr().out

    assert "一次性批准全部 3 步" in rendered
    # 白名单本身可见,且点名两个白名单外的 agent
    assert "白名单" in rendered
    assert "coder" in rendered
    planner_line = next(line for line in rendered.splitlines() if "planner" in line)
    reviewer_line = next(line for line in rendered.splitlines() if "reviewer" in line)
    assert "白名单外" in planner_line
    assert "白名单外" in reviewer_line
    coder_line = next(
        line for line in rendered.splitlines() if "coder" in line and line.strip().startswith("2.")
    )
    assert "白名单外" not in coder_line


def test_goal_preview_says_the_budget_bounds_only_later_auto_approvals(
    tmp_path, monkeypatch, capsys
) -> None:
    """`max_approvals` / 白名单只约束**本次确认之后**的自主自动批准(返工轮)。"""
    prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys, max_approvals=1)

    assert cli.main(["goal", "preview", "--task", "让测试全绿"]) == 0
    rendered = capsys.readouterr().out

    assert "本次确认之后" in rendered
    assert "自动批准" in rendered

    payload = preview_json(capsys)
    outside = next(
        item for item in payload["stop_conditions"] if item["kind"] == "approval_outside_allowlist"
    )
    # 旧文案 "白名单外的 agent 需要审批" 不成立于本次确认,必须说清它管的是之后
    assert "本次确认之后" in outside["summary"]


def test_goal_start_still_approves_every_step_including_outside_the_allowlist(
    tmp_path, monkeypatch, capsys
) -> None:
    """行为不变:人类确认一向对 allowlist 免疫。本切片只改屏,不改这一点。"""
    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys, max_approvals=1)
    monkeypatch.setattr(cli, "_spawn_host_process", RecordingSpawn())
    plan_id = preview_json(capsys)["plan_id"]

    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["approved_count"] == 3
    state = StateStore(root).load()
    approved = [a for a in state["approvals"] if a["plan_id"] == plan_id]
    assert {a["agent_id"] for a in approved} == {"planner", "coder", "reviewer"}
    assert {a["status"] for a in approved} == {"approved"}


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


def test_goal_start_refuses_while_a_host_is_already_running(
    tmp_path, monkeypatch, capsys
) -> None:
    """第五道门:已有活宿主时拒绝,且拒绝之前一个字节都不写。

    与另外四道门同标准取证:state.json 逐字节相同、events 序列相同、
    零 spawn、host 记录未被改写。
    """
    from agentdeck.run_loop_host import host_record_path, read_host_record, write_host_record

    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    spawn = RecordingSpawn()
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)
    plan_id = preview_json(capsys)["plan_id"]

    write_host_record(root, {
        "pid": 999_101, "plan_id": "pln_other_host", "wave_count": 2, "max_waves": 40,
        "interval": 10.0, "last_gate": "waiting_for_reply", "last_wave_at": None,
        "stopped_reason": None, "log_path": ".agentdeck/run-loop-host/host.log",
    })
    monkeypatch.setattr(cli, "_host_pid_alive", lambda _pid: True)

    state_path = StateStore(root).state_path
    before = state_path.read_bytes()
    before_events = event_types(root)
    before_record = host_record_path(root).read_bytes()

    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    err = captured.err
    assert "already running" in err
    assert "999101" in err and "pln_other_host" in err
    assert "agentdeck run-loop-host status" in err
    assert "agentdeck run-loop-host stop --confirm" in err

    assert state_path.read_bytes() == before
    assert event_types(root) == before_events
    assert spawn.calls == []
    assert host_record_path(root).read_bytes() == before_record
    assert read_host_record(root)["pid"] == 999_101  # 活宿主一字未动


# 2026-08-01 终审发现:spec("plan 绑定")、goal-schema.md、CLAUDE.md 和
# HISTORY.md 四处都写着 `goal start` 绑定到人类**预览过的**那份计划,而代码
# 只检查该 plan **存在**。取证:`leader plan --task "任意旧目标"` 产出的
# plan_id 喂给 `goal start --confirm`,退 0、批 3 条审批、宿主起来了——一份
# 从未展示过授权屏的计划就这样被走开式执行了。"only the exact confirmed
# preview becomes frozen authority" 是本仓库的承重教条,四份文档断言而代码
# 不做,正是反复咬过本项目的那一类漂移。下面这组用例把第六道门钉死。
def test_goal_preview_stamps_provenance_on_the_plan_record(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)

    plan_id = preview_json(capsys)["plan_id"]

    record = next(p for p in StateStore(root).load()["plans"] if p["plan_id"] == plan_id)
    assert record["source"] == "goal_preview"


def test_leader_plan_records_carry_no_goal_provenance(tmp_path, monkeypatch, capsys) -> None:
    """`leader plan` 行为逐字节不变:它的 plan 记录不带这个字段。"""
    root = prepare_project(tmp_path, monkeypatch)

    assert cli.main(["leader", "plan", "--task", "任意旧目标"]) == 0
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]

    record = next(p for p in StateStore(root).load()["plans"] if p["plan_id"] == plan_id)
    assert "source" not in record


def test_goal_start_refuses_a_plan_that_was_never_previewed(
    tmp_path, monkeypatch, capsys
) -> None:
    """第六道门:未经 `goal preview` 的计划一律拒绝,零写零 spawn。"""
    from agentdeck.run_loop_host import read_host_record

    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    spawn = RecordingSpawn()
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)

    assert cli.main(["leader", "plan", "--task", "任意旧目标"]) == 0
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]

    state_path = StateStore(root).state_path
    before = state_path.read_bytes()
    before_events = event_types(root)

    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    err = captured.err
    assert plan_id in err
    assert "goal preview" in err
    assert "agentdeck goal preview --task" in err

    assert state_path.read_bytes() == before
    assert event_types(root) == before_events
    assert spawn.calls == []
    assert read_host_record(root) is None
    state = StateStore(root).load()
    assert state.get("approvals", []) == []


def test_goal_start_accepts_the_plan_its_own_preview_wrote(
    tmp_path, monkeypatch, capsys
) -> None:
    """反面:预览过的计划照常放行——第六道门只挡没看过的那一种。"""
    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    monkeypatch.setattr(cli, "_spawn_host_process", RecordingSpawn(pid=999_303))
    plan_id = preview_json(capsys)["plan_id"]

    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["host_pid"] == 999_303
    assert "goal_started" in event_types(root)


def test_goal_start_proceeds_past_a_stale_host_record(tmp_path, monkeypatch, capsys) -> None:
    """死 pid 的残留记录不是活宿主,正是 `goal start` 该放行的那一种。

    与 `run-loop-host start` 对 stale 记录的既有处理同源。
    """
    from agentdeck.run_loop_host import read_host_record, write_host_record

    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    spawn = RecordingSpawn(pid=999_202)
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)
    plan_id = preview_json(capsys)["plan_id"]

    write_host_record(root, {
        "pid": 999_201, "plan_id": "pln_dead_host", "wave_count": 5, "max_waves": 40,
        "interval": 10.0, "last_gate": "waiting_for_reply", "last_wave_at": None,
        "stopped_reason": None, "log_path": ".agentdeck/run-loop-host/host.log",
    })
    monkeypatch.setattr(cli, "_host_pid_alive", lambda _pid: False)

    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["host_pid"] == 999_202
    assert len(spawn.calls) == 1
    assert read_host_record(root)["pid"] == 999_202
    assert read_host_record(root)["plan_id"] == plan_id
    assert "goal_started" in event_types(root)


# 2026-08-01 终审发现:`run_loop_host_start_command` 先 spawn 子进程、写 host
# 记录、追加 `run_loop_host_started`,**之后**才跑自己的契约校验。校验失败时
# 它退 1,而宿主是**真的在跑**。`goal start` 接住非 0 后却打印"the host did
# not start"——一句假话,且 `goal_started` 不会被追加,人类据此以为没起来,
# 于是这个走开式运行会在无人知晓的情况下继续跑。本切片只改这句话:不去
# un-spawn 任何东西(那才是真危险),也不动 `run-loop-host start` 的行为。
def test_goal_start_never_claims_the_host_did_not_start_when_it_may_have(
    tmp_path, monkeypatch, capsys
) -> None:
    from agentdeck.run_loop_host import read_host_record

    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    spawn = RecordingSpawn(pid=999_401)
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)
    plan_id = preview_json(capsys)["plan_id"]
    # 让宿主命令在 spawn **之后**的契约校验上失败——正是终审点名的那条路径。
    # 该命令现在还有一道 spawn **之前**的前置投影门(pid 用本进程 pid 占位),
    # 所以注入必须只在真实子进程 pid 上失败,才能仍然抵达同一条路径;下面
    # 每一条断言与本切片落地时逐字节相同。
    monkeypatch.setattr(
        cli,
        "validate_run_loop_host_start_contract",
        lambda payload: (
            {"ok": False, "errors": ["injected contract failure"]}
            if payload.get("pid") == 999_401
            else {"ok": True, "errors": []}
        ),
    )

    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm"]) == 1
    err = capsys.readouterr().err

    # 宿主确实起来了:这句话当时是假的
    assert spawn.calls != []
    assert read_host_record(root)["pid"] == 999_401
    assert "did not start" not in err
    # 必须说"可能已经起来",并指向查证入口
    assert "may have started" in err
    assert "agentdeck run-loop-host status" in err
    # 已批准的审批仍然是已批准,这一条本来就对,保留
    assert "remain approved" in err
    assert "3" in err


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


def test_goal_start_records_the_ledger_entry_before_the_contract_gate(
    tmp_path, monkeypatch, capsys
) -> None:
    """Pins a latent path: `goal_started` is appended *before* the contract
    gate, so a failed print can never erase the only ledger evidence that
    explains why a host is running.

    `validate_goal_start_contract` is tautological on the literal payload
    today, so the failing path is reached by injecting a failing validator --
    the correct way to pin a latent defect.
    """
    from agentdeck.run_loop_host import read_host_record

    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    spawn = RecordingSpawn(pid=999_601)
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)
    plan_id = preview_json(capsys)["plan_id"]
    monkeypatch.setattr(
        cli,
        "validate_goal_start_contract",
        lambda _payload: {"ok": False, "errors": ["injected contract failure"]},
    )

    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm"]) == 1
    err = capsys.readouterr().err

    # 宿主真的在跑,审批真的批了——账本必须留下解释宿主为何存在的那一条
    assert read_host_record(root)["pid"] == 999_601
    assert "goal_started" in event_types(root)
    # 失败路径必须点名已经发生的事,并给出查证入口
    assert "did not start" not in err
    assert "agentdeck run-loop-host status" in err
    assert "999601" in err


def test_goal_preview_contract_failure_names_the_plan_it_recorded(
    tmp_path, monkeypatch, capsys
) -> None:
    """Pins a latent path: `record_plan` + `leader_plan_created` land before
    the contract gate, so a failed preview still leaves a plan carrying the
    `goal_preview` provenance -- exactly what `goal start`'s provenance gate
    accepts. The failure must name that plan rather than read as "preview
    failed".

    `validate_goal_preview_contract` is tautological on today's payload, so
    the failing path is reached by injecting a failing validator -- the
    correct way to pin a latent defect.
    """
    root = prepare_project(tmp_path, monkeypatch)
    enable_autonomous(capsys)
    monkeypatch.setattr(
        cli,
        "validate_goal_preview_contract",
        lambda _payload: {"ok": False, "errors": ["injected contract failure"]},
    )

    assert cli.main(["goal", "preview", "--task", "让测试全绿", "--json"]) == 1
    err = capsys.readouterr().err

    plans = StateStore(root).load()["plans"]
    assert len(plans) == 1
    plan_id = plans[0]["plan_id"]
    assert "already happened" in err
    assert plan_id in err
    assert "agentdeck plan show --plan-id" in err
    assert "goal_preview_contract_failed" in event_types(root)

    # 这份 plan 真的能被 `goal start` 的 provenance 门接受——那句话不是修辞
    monkeypatch.undo()
    monkeypatch.chdir(root)
    monkeypatch.setattr(cli, "_spawn_host_process", RecordingSpawn(pid=999_701))
    assert cli.main(["goal", "start", "--plan-id", plan_id, "--confirm", "--json"]) == 0
