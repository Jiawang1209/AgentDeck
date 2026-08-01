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
    assert store.plan_verdict_summary("pln_g") is None
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
