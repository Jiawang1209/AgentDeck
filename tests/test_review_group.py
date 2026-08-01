from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentdeck.config import load_config, update_leader_approval_mode, write_default_config


def _config_with(root: Path, block: str) -> None:
    write_default_config(root)
    path = root / ".agentdeck" / "config.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n" + block, encoding="utf-8")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def test_review_config_defaults_are_empty(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_default_config(root)
    review = load_config(root).review
    assert review.round_reviewer is None
    assert review.reviewers == ()


def test_review_config_parses_both_keys(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _config_with(root, '[review]\nround_reviewer = "planner"\nreviewers = ["reviewer", "planner"]\n')
    review = load_config(root).review
    assert review.round_reviewer == "planner"
    assert review.reviewers == ("reviewer", "planner")


@pytest.mark.parametrize(
    "block",
    [
        '[review]\nround_reviewer = "ghost"\n',            # unknown agent
        '[review]\nreviewers = ["reviewer", "ghost"]\n',   # unknown member
        '[review]\nround_reviewer = ""\n',                 # empty string
        '[review]\nreviewers = ["reviewer", "reviewer"]\n',# duplicate member
        '[review]\nreviewers = "reviewer"\n',              # not a list
        '[review]\nreviewers = [""]\n',                    # empty member
        '[review]\nround_reviewer = 7\n',                  # wrong type
    ],
)
def test_review_config_fails_closed(tmp_path: Path, block: str) -> None:
    root = _root(tmp_path)
    _config_with(root, block)
    with pytest.raises(ValueError):
        load_config(root)


def test_empty_reviewers_list_is_allowed_and_means_off(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _config_with(root, "[review]\nreviewers = []\n")
    assert load_config(root).review.reviewers == ()


def test_review_section_survives_config_writer_round_trip(tmp_path: Path) -> None:
    """`_dump_config` 是白名单式序列化;任何经它回写的 writer(例如
    `policy set-mode` 背后的 `update_leader_approval_mode`)都必须保留
    它不认识的配置段,否则 `[review]` 活不过一次模式切换。"""
    root = _root(tmp_path)
    _config_with(
        root,
        '[review]\nround_reviewer = "planner"\nreviewers = ["reviewer", "planner"]\n',
    )
    assert load_config(root).review.round_reviewer == "planner"

    update_leader_approval_mode(root, "approve")

    review = load_config(root).review
    assert review.round_reviewer == "planner"
    assert review.reviewers == ("reviewer", "planner")


def test_config_writers_preserve_leader_subroles_and_daemon(tmp_path: Path) -> None:
    """同一个白名单缺口此前已在吞掉 G2 的 `[leader.planner]` /
    `[leader.orchestrator]` 与 `[daemon]`——已落地功能的静默数据丢失。"""
    from agentdeck.config import update_autonomous_policy, update_leader_provider

    root = _root(tmp_path)
    _config_with(
        root,
        '[leader.planner]\nprovider = "deepseek"\nmodel = "deepseek-v4-pro"\n\n'
        '[leader.orchestrator]\nprovider = "claude-cli"\nmodel = "claude-fable-5"\n\n'
        "[daemon]\nidle_grace_seconds = 42\n",
    )
    before = load_config(root)
    assert before.leader.planner is not None
    assert before.daemon.idle_grace_seconds == 42

    update_leader_approval_mode(root, "approve")
    update_autonomous_policy(root, ("coder",), 3)
    update_leader_provider(root, "deepseek", "deepseek-v4-pro")

    after = load_config(root)
    assert after.leader.planner == before.leader.planner
    assert after.leader.orchestrator == before.leader.orchestrator
    assert after.daemon.idle_grace_seconds == 42


def test_config_writers_preserve_unknown_sections(tmp_path: Path) -> None:
    """未知/未来配置段也必须原样保留(白名单不该成为数据丢失面)。"""
    import tomllib

    root = _root(tmp_path)
    _config_with(root, '[future_thing]\nflag = true\ncount = 7\nnames = ["a", "b"]\n')
    update_leader_approval_mode(root, "approve")
    raw = tomllib.loads((root / ".agentdeck" / "config.toml").read_text(encoding="utf-8"))
    assert raw["future_thing"] == {"flag": True, "count": 7, "names": ["a", "b"]}


def test_config_writers_preserve_unknown_value_shapes(tmp_path: Path) -> None:
    """保留必须覆盖真实 TOML 的全部形态:浮点、unicode/含引号字符串、
    嵌套子表,以及**数组表**(`[[x]]`——既有 `[[agents]]` 就是这个形态,
    未知段用它同样不能丢)。"""
    import tomllib

    root = _root(tmp_path)
    _config_with(
        root,
        "[weird]\n"
        "ratio = 1.5\n"
        'name = "unicode 中文 and \\"quoted\\" text"\n'
        "flag = false\n"
        "empty_list = []\n"
        '\n[weird.nested]\ndeep = "yes"\n'
        '\n[[weird.rows]]\nid = "n1"\n'
        '\n[[weird_items]]\nid = "a"\n'
        '\n[[weird_items]]\nid = "b"\ncount = 2\n',
    )
    path = root / ".agentdeck" / "config.toml"
    before = tomllib.loads(path.read_text(encoding="utf-8"))

    update_leader_approval_mode(root, "approve")

    after = tomllib.loads(path.read_text(encoding="utf-8"))
    assert after["weird"] == before["weird"]
    assert after["weird_items"] == before["weird_items"] == [
        {"id": "a"},
        {"id": "b", "count": 2},
    ]


from agentdeck.review_group import (
    REVIEW_GROUP_ORIGIN,
    aggregate_group_verdicts,
    expand_review_group,
    latest_complete_group,
    review_group_numbers,
)


def _plan(review_agent: str = "reviewer") -> dict:
    return {
        "goal": "g",
        "summary": "s",
        "steps": [
            {"step": 1, "agent_id": "coder", "role": "implementation",
             "task": "build", "risk": "low", "requires_approval": True},
            {"step": 2, "agent_id": review_agent, "role": "review",
             "task": "review it", "risk": "low", "requires_approval": True},
        ],
    }


# (agent_id, role) pairs — the pure module never sees ProjectConfig
REVIEWERS_2 = (("reviewer", "review"), ("planner", "planning"))


def test_expand_is_noop_without_reviewers() -> None:
    plan = _plan()
    assert expand_review_group(plan, ()) == plan


def test_expand_duplicates_review_step_per_reviewer() -> None:
    expanded = expand_review_group(_plan(), REVIEWERS_2)
    steps = expanded["steps"]
    assert [s["step"] for s in steps] == [1, 2, 3]
    assert [s["agent_id"] for s in steps] == ["coder", "reviewer", "planner"]
    assert [s["role"] for s in steps] == ["implementation", "review", "planning"]
    # 任务文本逐字节复制
    assert steps[1]["task"] == steps[2]["task"] == "review it"
    for index, member in enumerate(steps[1:]):
        assert member["origin"] == REVIEW_GROUP_ORIGIN
        assert member["review_group"] == 1
        assert member["review_group_member"] == index
        assert member["requires_approval"] is True


def test_expand_does_not_touch_planning_steps_in_cross_role_group() -> None:
    """识别谓词只认 reviewers[0] 的 role;跨角色组不得误伤 planning step。"""
    plan = {
        "goal": "g", "summary": "s",
        "steps": [
            {"step": 1, "agent_id": "planner", "role": "planning",
             "task": "plan it", "risk": "low", "requires_approval": True},
            {"step": 2, "agent_id": "coder", "role": "implementation",
             "task": "build", "risk": "low", "requires_approval": True},
            {"step": 3, "agent_id": "reviewer", "role": "review",
             "task": "review it", "risk": "low", "requires_approval": True},
        ],
    }
    steps = expand_review_group(plan, REVIEWERS_2)["steps"]
    assert [s["agent_id"] for s in steps] == ["planner", "coder", "reviewer", "planner"]
    assert steps[0].get("origin") is None  # planning step 原样
    assert steps[3]["review_group"] == 1


def test_expand_numbers_multiple_groups() -> None:
    plan = _plan()
    plan["steps"].append({"step": 3, "agent_id": "coder", "role": "implementation",
                          "task": "fix", "risk": "low", "requires_approval": True})
    plan["steps"].append({"step": 4, "agent_id": "reviewer", "role": "review",
                          "task": "review again", "risk": "low", "requires_approval": True})
    steps = expand_review_group(plan, REVIEWERS_2)["steps"]
    assert [s["step"] for s in steps] == [1, 2, 3, 4, 5, 6]
    groups = [s.get("review_group") for s in steps]
    assert groups == [None, 1, 1, None, 2, 2]


def test_expand_does_not_mutate_input() -> None:
    plan = _plan()
    snapshot = json.loads(json.dumps(plan))
    expand_review_group(plan, REVIEWERS_2)
    assert plan == snapshot


def test_review_group_numbers_maps_steps() -> None:
    steps = expand_review_group(_plan(), REVIEWERS_2)["steps"]
    assert review_group_numbers(steps) == {2: 1, 3: 1}


def test_latest_complete_group_requires_every_member() -> None:
    steps = expand_review_group(_plan(), REVIEWERS_2)["steps"]
    approvals = [
        {"plan_id": "p", "step": 2, "agent_id": "reviewer", "message_id": "m2"},
        {"plan_id": "p", "step": 3, "agent_id": "planner", "message_id": "m3"},
    ]
    only_first = [{"reply_id": "r2", "message_id": "m2", "verdict": {"overall": "fail"}}]
    assert latest_complete_group(steps, approvals, only_first, "p") is None
    both = only_first + [
        {"reply_id": "r3", "message_id": "m3", "verdict": {"overall": "pass"}}
    ]
    group = latest_complete_group(steps, approvals, both, "p")
    assert group is not None
    assert [m["agent_id"] for m in group["members"]] == ["reviewer", "planner"]
    assert group["last_reply_id"] == "r3"


def test_aggregate_any_fail_blocks() -> None:
    def member(overall):
        return {"verdict": {"schema_version": "review-verdict/v1", "overall": overall,
                            "criteria": [{"criterion": "c", "verdict":
                                          "pass" if overall == "pass" else "fail"}]}}

    assert aggregate_group_verdicts([member("pass"), member("pass")])["overall"] == "pass"
    assert aggregate_group_verdicts([member("pass"), member("needs_changes")])["overall"] == "needs_changes"
    assert aggregate_group_verdicts([member("needs_changes"), member("fail")])["overall"] == "fail"


def test_generated_plan_expands_review_group(tmp_path, monkeypatch, capsys) -> None:
    from agentdeck import cli
    from agentdeck.state import StateStore

    root = _root(tmp_path)
    (root / ".git").mkdir()
    _config_with(root, '[review]\nreviewers = ["reviewer", "planner"]\n')
    monkeypatch.chdir(root)

    assert cli.main([
        "leader", "plan", "--task", "demo work",
        "--provider", "fake", "--model", "fake-plan",
    ]) == 0
    capsys.readouterr()
    plan = StateStore(root).load()["plans"][-1]["plan"]
    review_steps = [s for s in plan["steps"] if s.get("origin") == "review_group"]
    assert len(review_steps) == 2
    assert [s["agent_id"] for s in review_steps] == ["reviewer", "planner"]
    assert [s["step"] for s in plan["steps"]] == list(range(1, len(plan["steps"]) + 1))


def test_generated_plan_is_unchanged_without_review_config(tmp_path, monkeypatch, capsys) -> None:
    from agentdeck import cli
    from agentdeck.state import StateStore

    root = _root(tmp_path)
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    assert cli.main([
        "leader", "plan", "--task", "demo work",
        "--provider", "fake", "--model", "fake-plan",
    ]) == 0
    capsys.readouterr()
    plan = StateStore(root).load()["plans"][-1]["plan"]
    assert all(s.get("origin") is None for s in plan["steps"])


def _seed_group_store(tmp_path, verdicts: list[str]):
    """plan pln_g: step1 coder(replied) + 组(reviewer, planner);
    verdicts 为组成员的 overall(长度 1 = 只有第一个成员回复)。"""
    from agentdeck.state import StateStore

    root = _root(tmp_path)
    _config_with(root, '[review]\nreviewers = ["reviewer", "planner"]\n')
    store = StateStore(root)
    state = store.load()
    steps = expand_review_group(_plan(), REVIEWERS_2)["steps"]
    state["plans"] = [{
        "plan_id": "pln_g", "task": "t", "status": "planned",
        "provider": "fake", "model": "fake-plan",
        "plan": {"goal": "g", "summary": "s", "steps": steps},
    }]
    state["approvals"] = [
        {"approval_id": "apv_1", "plan_id": "pln_g", "step": 1, "agent_id": "coder",
         "role": "implementation", "task": "build", "risk": "low",
         "status": "dispatched", "message_id": "m1"},
        {"approval_id": "apv_2", "plan_id": "pln_g", "step": 2, "agent_id": "reviewer",
         "role": "review", "task": "review it", "risk": "low",
         "status": "dispatched", "message_id": "m2"},
        {"approval_id": "apv_3", "plan_id": "pln_g", "step": 3, "agent_id": "planner",
         "role": "planning", "task": "review it", "risk": "low",
         "status": "dispatched", "message_id": "m3"},
    ]
    state["messages"] = [
        {"message_id": "m1", "worktree_branch": "agentdeck/m1"},
        {"message_id": "m2", "worktree_branch": None},
        {"message_id": "m3", "worktree_branch": None},
    ]

    def verdict(overall):
        return {"schema_version": "review-verdict/v1", "overall": overall,
                "criteria": [{"criterion": "c1",
                              "verdict": "pass" if overall == "pass" else "fail"}]}

    replies = [{"reply_id": "r1", "message_id": "m1", "from_agent": "coder",
                "text": "done"}]
    for index, overall in enumerate(verdicts):
        replies.append({
            "reply_id": f"rg{index}", "message_id": f"m{index + 2}",
            "from_agent": ["reviewer", "planner"][index],
            "text": "review", "verdict": verdict(overall),
        })
    state["replies"] = replies
    store.save(state)
    return root, store


def test_incomplete_group_does_not_trigger_iteration(tmp_path) -> None:
    _root_dir, store = _seed_group_store(tmp_path, ["fail"])  # 只有 reviewer 回了
    # 触发面以"整组完成"为界:半个组绝不开迭代轮(先 fail 的成员不能在
    # 其余成员还在审旧代码时开一轮)。展示/gate 面的行为见
    # test_incomplete_group_still_blocks_merge_with_known_fail。
    result = store.append_review_iteration("pln_g", 2, source="explicit")
    assert result["ok"] is False
    assert result["reason"] == "no_verdict"


def test_complete_group_aggregates_any_fail_blocks(tmp_path) -> None:
    _root_dir, store = _seed_group_store(tmp_path, ["pass", "fail"])
    summary = store.plan_verdict_summary("pln_g")
    assert summary["overall"] == "fail"
    assert summary["group"]["size"] == 2
    assert summary["group"]["complete"] is True
    assert summary["group"]["rule"] == "any_fail_blocks"
    assert [m["agent_id"] for m in summary["group"]["members"]] == ["reviewer", "planner"]


def test_incomplete_group_still_blocks_merge_with_known_fail(tmp_path) -> None:
    """终审 Critical:组内一人的 verdict 无效/缺失时,另一人的有效 fail
    绝不能因此失效——单 reviewer 下这个 fail 会扣住自动合并,配了组之后
    也必须扣住,否则启用多 reviewer 反而放开了 merge gate。"""
    from agentdeck import cli

    _root_dir, store = _seed_group_store(tmp_path, ["fail"])  # planner 尚未给出有效 verdict
    summary = store.plan_verdict_summary("pln_g")
    assert summary is not None                      # 不再塌缩成"无 verdict"
    assert summary["overall"] == "fail"             # 已知成员里最严的判定
    assert summary["group"]["complete"] is False
    assert [m["overall"] for m in summary["group"]["members"]] == ["fail", None]
    assert cli._verdict_merge_blocker(store, "pln_g") is not None

    # 但触发器仍以"组完成"为界:绝不因半个组开一轮
    assert store.append_review_iteration("pln_g", 2, source="explicit")["reason"] == "no_verdict"


def test_incomplete_group_without_any_verdict_stays_silent(tmp_path) -> None:
    """一个 verdict 都还没有时维持既有语义(无判定 = 无 summary)。"""
    _root_dir, store = _seed_group_store(tmp_path, [])
    assert store.plan_verdict_summary("pln_g") is None


def test_incomplete_group_all_pass_so_far_still_withholds_merge(tmp_path) -> None:
    """组没审完就不该自动合并:已知全 pass 也因 complete=false 扣住,
    人类显式 `worktree merge-plan --confirm` 永不受 gate。"""
    from agentdeck import cli

    _root_dir, store = _seed_group_store(tmp_path, ["pass"])
    summary = store.plan_verdict_summary("pln_g")
    assert summary["overall"] == "pass"
    assert summary["group"]["complete"] is False
    assert cli._verdict_merge_blocker(store, "pln_g") is not None


def test_config_writers_preserve_native_datetimes(tmp_path: Path) -> None:
    """TOML 原生日期/时间也必须保留(此前会被当成不可表示而丢弃)。"""
    import tomllib

    root = _root(tmp_path)
    _config_with(
        root,
        "[stamps]\nday = 2026-08-01\nmoment = 2026-08-01T10:20:30\nclock = 10:20:30\n",
    )
    before = tomllib.loads((root / ".agentdeck" / "config.toml").read_text(encoding="utf-8"))
    update_leader_approval_mode(root, "approve")
    after = tomllib.loads((root / ".agentdeck" / "config.toml").read_text(encoding="utf-8"))
    assert after["stamps"] == before["stamps"]


def test_config_writers_preserve_control_characters(tmp_path: Path) -> None:
    """保留式回写的字符串同样要转义控制字符(与 role_prompt 同源修复);
    否则未知段里的一个换行就能砖掉整份配置。"""
    import tomllib

    root = _root(tmp_path)
    write_default_config(root)
    path = root / ".agentdeck" / "config.toml"
    path.write_text(
        path.read_text(encoding="utf-8") + '\n[notes]\ntext = "a\\nb\\tc"\n',
        encoding="utf-8",
    )
    before = tomllib.loads(path.read_text(encoding="utf-8"))
    assert before["notes"]["text"] == "a\nb\tc"

    update_leader_approval_mode(root, "approve")

    after = tomllib.loads(path.read_text(encoding="utf-8"))
    assert after["notes"] == before["notes"]


def test_config_writer_refuses_unrepresentable_value(tmp_path: Path) -> None:
    """不可表示的值必须**报错**而不是静默消失——白名单回写永远不该
    成为数据丢失面(2026-08-01 终审 follow-up)。"""
    import pytest as _pytest

    from agentdeck.config import _dump_config

    with _pytest.raises(ValueError, match="cannot be preserved"):
        _dump_config({"project": {"name": "x"}, "odd": {"mixed": [{"a": 1}, "str"]}})


def test_single_element_reviewers_replaces_review_agent(tmp_path) -> None:
    """spec 测试要点:单元素 reviewers 等价于替换 review agent(组=1)。
    识别谓词是 reviewers[0] 的 **role**,所以替换者必须与 plan 的 review
    step 同角色;跨角色的单元素列表是文档化的 no-op(见下一断言)。"""
    steps = expand_review_group(_plan(), (("auditor", "review"),))["steps"]
    assert [s["agent_id"] for s in steps] == ["coder", "auditor"]
    assert steps[1]["review_group"] == 1
    assert steps[1]["review_group_member"] == 0
    assert review_group_numbers(steps) == {2: 1}

    # 跨角色单元素:谓词不匹配任何 step,plan 原样不动(不是缺陷,是
    # reviewers[0] 角色签名谓词的既定锐边,CLAUDE.md 已记录)。
    untouched = expand_review_group(_plan(), (("planner", "planning"),))["steps"]
    assert [s["agent_id"] for s in untouched] == ["coder", "reviewer"]
    assert review_group_numbers(untouched) == {}


def test_rework_template_merges_and_truncates_multi_reviewer(tmp_path) -> None:
    """spec 测试要点:多 reviewer 回炉模板署名合并 + 截断附各成员 trace。"""
    from agentdeck.review_iteration import (
        MAX_REWORK_TASK_CHARS,
        build_group_review_text,
        build_rework_task,
    )

    members = [
        {"agent_id": "reviewer", "verdict": {"overall": "fail", "criteria": [
            {"criterion": "tests pass", "verdict": "fail", "evidence": "2 failing"}]},
         "text": "R1 " + "x" * 3000},
        {"agent_id": "planner", "verdict": {"overall": "needs_changes", "criteria": [
            {"criterion": "docs synced", "verdict": "fail"}]},
         "text": "R2 " + "y" * 3000},
    ]
    merged = build_group_review_text(members)
    assert "### reviewer reviewer" in merged and "### reviewer planner" in merged
    assert "tests pass" in merged and "docs synced" in merged

    text = build_rework_task(
        round_number=1,
        original_task="build the widget",
        verdict={"overall": "fail", "criteria": []},
        reply_id="rep_last",
        reply_text=merged,
        trace_ids=["rep_a", "rep_b"],
    )
    assert len(text) <= MAX_REWORK_TASK_CHARS
    # 截断标记逐个成员给出 trace 指引(单成员时退化为原单条形态)
    assert "agentdeck trace --id rep_a / agentdeck trace --id rep_b" in text
    assert text.rstrip().endswith("修复后 commit 到任务分支。")


def test_aggregate_clamps_unrecognized_overall_to_fail() -> None:
    """损坏/手改 state 里的非法 overall 必须 fail-closed 夹到 fail,
    而不是原样外泄成契约非法值。"""
    merged = aggregate_group_verdicts([
        {"verdict": {"overall": "bogus", "criteria": []}},
        {"verdict": {"overall": "pass", "criteria": []}},
    ])
    assert merged["overall"] == "fail"


def test_display_face_survives_missing_first_member_approval(tmp_path) -> None:
    """展示面不依赖 approval:首个成员的 approval 记录缺失时,summary
    绝不能塌缩(那是回到 Critical 形态的最后一条结构性路径)。"""
    from agentdeck import cli

    _root_dir, store = _seed_group_store(tmp_path, ["fail", "pass"])
    state = store.load()
    state["approvals"] = [a for a in state["approvals"] if a.get("step") != 2]
    store.save(state)
    summary = store.plan_verdict_summary("pln_g")
    # 首成员 approval 缺失 → 它的回复无从关联,该成员降级为"未报到",
    # 但 summary 绝不塌缩:组标 complete=false,自动合并照样扣住。
    assert summary is not None
    assert summary["group"]["complete"] is False
    assert cli._verdict_merge_blocker(store, "pln_g") is not None


def test_group_aggregate_merges_criteria_any_fail_wins(tmp_path) -> None:
    """spec 头条合并规则(终审点名未钉):任一 fail 胜出、unknown 胜 pass。"""
    def member(*verdicts):
        return {"verdict": {"overall": "fail", "criteria": [
            {"criterion": "c", "verdict": v} for v in verdicts]}}

    merged = aggregate_group_verdicts([member("pass"), member("fail")])
    assert merged["criteria"] == [{"criterion": "c", "verdict": "fail"}]
    assert aggregate_group_verdicts([member("fail"), member("pass")])["criteria"] == [
        {"criterion": "c", "verdict": "fail"}
    ]
    assert aggregate_group_verdicts([member("pass"), member("unknown")])["criteria"] == [
        {"criterion": "c", "verdict": "unknown"}
    ]


def test_reviewers_config_beats_round_reviewer(tmp_path) -> None:
    """spec:两者同时配置时 reviewers 优先(组语义强于单人替换)。"""
    _root_dir, store = _seed_group_store(tmp_path, ["fail", "pass"])
    binding = {
        "round_reviewer": ("coder", "implementation"),
        "reviewers": (("reviewer", "review"), ("planner", "planning")),
    }
    assert store.append_review_iteration(
        "pln_g", 2, source="explicit", review_binding=binding
    )["ok"] is True
    steps = store.load()["plans"][0]["plan"]["steps"]
    appended = [s for s in steps if s.get("origin") == "review_iteration"]
    assert [s["agent_id"] for s in appended] == ["coder", "reviewer", "planner"]


def test_complete_group_all_pass(tmp_path) -> None:
    _root_dir, store = _seed_group_store(tmp_path, ["pass", "pass"])
    assert store.plan_verdict_summary("pln_g")["overall"] == "pass"


def test_complete_failing_group_triggers_one_iteration(tmp_path) -> None:
    _root_dir, store = _seed_group_store(tmp_path, ["fail", "pass"])
    first = store.append_review_iteration("pln_g", 2, source="explicit")
    assert first["ok"] is True
    # 组级幂等:同一组绝不重复触发
    second = store.append_review_iteration("pln_g", 2, source="explicit")
    assert second["ok"] is False and second["reason"] == "already_triggered"
    steps = store.load()["plans"][0]["plan"]["steps"]
    appended = [s for s in steps if s.get("origin") == "review_iteration"]
    # 回炉 1 步 + 复审组 2 步
    assert [s["agent_id"] for s in appended] == ["coder", "reviewer", "planner"]
    # 回炉模板合并了失败成员的意见
    assert "reviewer" in appended[0]["task"]


def test_round_reviewer_replaces_single_rereview(tmp_path) -> None:
    """无 reviewers 组、只配 round_reviewer 时,迭代复审步换人。"""
    from test_review_iteration import _seed_store

    root, store = _seed_store(tmp_path, "fail")
    path = root / ".agentdeck" / "config.toml"
    path.write_text(path.read_text(encoding="utf-8") + '\n[review]\nround_reviewer = "planner"\n',
                    encoding="utf-8")
    assert store.append_review_iteration("pln_1", 2, source="explicit")["ok"] is True
    steps = store.load()["plans"][0]["plan"]["steps"]
    appended = [s for s in steps if s.get("origin") == "review_iteration"]
    assert [s["agent_id"] for s in appended] == ["coder", "planner"]


def test_verdict_summary_contract_accepts_group() -> None:
    from agentdeck.contracts import (
        REVIEW_VERDICT_SUMMARY_FIELDS,
        leader_review_example,
        validate_leader_review_contract,
    )
    from agentdeck.review_group import REVIEW_GROUP_RULE

    assert "group" in REVIEW_VERDICT_SUMMARY_FIELDS
    example = leader_review_example()
    assert validate_leader_review_contract(example)["ok"] is True

    example["verdict_summary"] = {
        "criteria_total": 1,
        "passed": 0,
        "failed": 1,
        "unknown": 0,
        "overall": "fail",
        "score": 40,
        "unverified": [],
        "extra": [],
        "group": {
            "size": 2,
            "complete": True,
            "rule": REVIEW_GROUP_RULE,
            "members": [
                {"agent_id": "reviewer", "step": 2, "overall": "pass", "reply_id": "rep_a"},
                {"agent_id": "planner", "step": 3, "overall": "fail", "reply_id": "rep_b"},
            ],
        },
    }
    assert validate_leader_review_contract(example)["ok"] is True

    # rule 不是 any_fail_blocks、size 与成员数不符一律拒绝(不打印半坏 JSON)。
    example["verdict_summary"]["group"]["rule"] = "majority"
    assert validate_leader_review_contract(example)["ok"] is False
    example["verdict_summary"]["group"]["rule"] = REVIEW_GROUP_RULE
    example["verdict_summary"]["group"]["size"] = 3
    assert validate_leader_review_contract(example)["ok"] is False


def test_plan_status_steps_expose_review_group(tmp_path) -> None:
    _root_dir, store = _seed_group_store(tmp_path, ["pass", "fail"])
    steps = store.plan_status("pln_g")["steps"]
    assert steps[0]["review_group"] is None
    assert steps[0]["review_group_member"] is None
    assert steps[1]["review_group"] == 1
    assert steps[1]["review_group_member"] == 0
    assert steps[2]["review_group"] == 1
    assert steps[2]["review_group_member"] == 1
