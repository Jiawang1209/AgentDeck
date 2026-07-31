from __future__ import annotations

from pathlib import Path

import pytest

from agentdeck.config import load_config, write_default_config


def _write_config(root: Path, autonomous_block: str) -> None:
    write_default_config(root)
    path = root / ".agentdeck" / "config.toml"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n[autonomous]\n" + autonomous_block,
        encoding="utf-8",
    )


def test_max_review_rounds_defaults_to_2(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)
    assert config.autonomous.max_review_rounds == 2


def test_max_review_rounds_parses_from_config(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_config(root, 'allowed_agents = ["coder"]\nmax_approvals = 3\nmax_review_rounds = 5\n')
    config = load_config(root)
    assert config.autonomous.max_review_rounds == 5


def test_max_review_rounds_zero_is_valid(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_config(root, "max_review_rounds = 0\n")
    assert load_config(root).autonomous.max_review_rounds == 0


@pytest.mark.parametrize(
    "bad",
    [
        'max_review_rounds = -1\n',
        'max_review_rounds = "two"\n',
        'max_review_rounds = true\n',
    ],
)
def test_max_review_rounds_invalid_fails_closed(tmp_path: Path, bad: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_config(root, bad)
    with pytest.raises(ValueError):
        load_config(root)


from agentdeck.review_iteration import (  # noqa: E402
    MAX_REWORK_TASK_CHARS,
    REVIEW_ITERATION_ORIGIN,
    REWORK_TRIGGER_OVERALLS,
    build_rework_task,
    derive_review_iteration,
    plan_review_rounds,
)


def _verdict(overall: str = "fail", criteria=None, score=None) -> dict:
    payload = {
        "schema_version": "review-verdict/v1",
        "criteria": criteria
        or [
            {"criterion": "tests pass", "verdict": "fail", "evidence": "2 failing"},
            {"criterion": "a11y kept", "verdict": "pass"},
        ],
        "overall": overall,
    }
    if score is not None:
        payload["score"] = score
    return payload


def _state(overall: str = "fail", *, consumed: bool = False, rounds: int = 0) -> dict:
    """Plan pln_1: step1 coder(implementation, dispatched msg_impl with
    worktree branch) → step2 reviewer(review, dispatched msg_rev, reply with
    verdict). Optional prior iteration steps bump the round counter."""
    steps = [
        {"step": 1, "agent_id": "coder", "role": "implementation",
         "task": "build the widget", "risk": "low", "requires_approval": True},
        {"step": 2, "agent_id": "reviewer", "role": "review",
         "task": "review the widget", "risk": "low", "requires_approval": True},
    ]
    approvals = [
        {"approval_id": "apv_1", "plan_id": "pln_1", "step": 1, "agent_id": "coder",
         "role": "implementation", "task": "build the widget", "risk": "low",
         "status": "dispatched", "message_id": "msg_impl"},
        {"approval_id": "apv_2", "plan_id": "pln_1", "step": 2, "agent_id": "reviewer",
         "role": "review", "task": "review the widget", "risk": "low",
         "status": "dispatched", "message_id": "msg_rev"},
    ]
    next_step = 3
    for round_number in range(1, rounds + 1):
        steps.append({"step": next_step, "agent_id": "coder", "role": "implementation",
                      "task": "rework", "risk": "low", "requires_approval": True,
                      "origin": REVIEW_ITERATION_ORIGIN, "round": round_number,
                      "triggered_by_reply": f"rep_old_{round_number}"})
        steps.append({"step": next_step + 1, "agent_id": "reviewer", "role": "review",
                      "task": "review the widget", "risk": "low", "requires_approval": True,
                      "origin": REVIEW_ITERATION_ORIGIN, "round": round_number,
                      "triggered_by_reply": f"rep_old_{round_number}"})
        next_step += 2
    reply_id = "rep_old_1" if consumed else "rep_new"
    return {
        "plans": [{"plan_id": "pln_1", "task": "build the widget", "status": "planned",
                   "plan": {"goal": "g", "summary": "s", "steps": steps}}],
        "approvals": approvals,
        "messages": [
            {"message_id": "msg_impl", "worktree_branch": "agentdeck/msg_impl"},
            {"message_id": "msg_rev", "worktree_branch": None},
        ],
        "replies": [
            {"reply_id": reply_id, "message_id": "msg_rev", "from_agent": "reviewer",
             "text": "status: completed\nfindings...\nverdict: {...}",
             "verdict": _verdict(overall)},
        ],
    }


def test_trigger_overalls_are_fail_and_needs_changes() -> None:
    assert REWORK_TRIGGER_OVERALLS == frozenset({"fail", "needs_changes"})


def test_plan_review_rounds_counts_iteration_markers() -> None:
    assert plan_review_rounds(_state(rounds=0)["plans"][0]["plan"]["steps"]) == 0
    assert plan_review_rounds(_state(rounds=2)["plans"][0]["plan"]["steps"]) == 2


def test_derive_appends_rework_and_review_pair_on_fail() -> None:
    result = derive_review_iteration(_state("fail"), "pln_1", 2)
    assert result["ok"] is True
    assert result["round"] == 1
    assert result["triggered_by_reply"] == "rep_new"
    rework, review = result["rework_step"], result["review_step"]
    assert (rework["step"], review["step"]) == (3, 4)
    assert rework["agent_id"] == "coder" and rework["requires_approval"] is True
    assert review["agent_id"] == "reviewer"
    assert review["task"] == "review the widget"  # re-review 任务逐字节复用
    for step in (rework, review):
        assert step["origin"] == REVIEW_ITERATION_ORIGIN
        assert step["round"] == 1
        assert step["triggered_by_reply"] == "rep_new"
    # 模板包含 fail 标准原文与 reviewer 回复原文
    assert "tests pass" in rework["task"]
    assert "findings..." in rework["task"]
    assert "build the widget" in rework["task"]


def test_derive_triggers_on_needs_changes() -> None:
    assert derive_review_iteration(_state("needs_changes"), "pln_1", 2)["ok"] is True


def test_derive_refusal_matrix() -> None:
    assert derive_review_iteration({}, "pln_1", 2)["reason"] == "no_plan"
    state = _state("fail")
    state["replies"] = []
    assert derive_review_iteration(state, "pln_1", 2)["reason"] == "no_verdict"
    assert derive_review_iteration(_state("pass"), "pln_1", 2)["reason"] == "verdict_pass"
    assert (
        derive_review_iteration(_state("fail", consumed=True, rounds=1), "pln_1", 2)["reason"]
        == "already_triggered"
    )
    assert (
        derive_review_iteration(_state("fail", rounds=2), "pln_1", 2)["reason"]
        == "rounds_exhausted"
    )
    assert derive_review_iteration(_state("fail"), "pln_1", 0)["reason"] == "rounds_exhausted"
    no_impl = _state("fail")
    no_impl["messages"][0]["worktree_branch"] = None
    no_impl["approvals"][0].pop("message_id")
    assert derive_review_iteration(no_impl, "pln_1", 2)["reason"] == "no_implementation_step"


def test_derive_uses_latest_verdict_not_stale_fail() -> None:
    state = _state("fail")
    state["replies"].append(
        {"reply_id": "rep_newer", "message_id": "msg_rev", "from_agent": "reviewer",
         "text": "verdict pass", "verdict": _verdict("pass")}
    )
    assert derive_review_iteration(state, "pln_1", 2)["reason"] == "verdict_pass"


def test_second_round_targets_first_round_rework_step() -> None:
    """round-2 回炉必须瞄准 round-1 的 rework step(最新任务分支),
    而不是最初的 step 1——这是迭代闭环能真正收敛的核心语义。"""
    state = _state("fail", rounds=1)
    state["approvals"] += [
        {"approval_id": "apv_3", "plan_id": "pln_1", "step": 3, "agent_id": "coder",
         "role": "implementation", "task": "rework", "risk": "low",
         "status": "dispatched", "message_id": "msg_rework1"},
        {"approval_id": "apv_4", "plan_id": "pln_1", "step": 4, "agent_id": "reviewer",
         "role": "review", "task": "review the widget", "risk": "low",
         "status": "dispatched", "message_id": "msg_rev2"},
    ]
    state["messages"] += [
        {"message_id": "msg_rework1", "worktree_branch": "agentdeck/msg_rework1"},
        {"message_id": "msg_rev2", "worktree_branch": None},
    ]
    state["replies"] = [
        {"reply_id": "rep_round2", "message_id": "msg_rev2", "from_agent": "reviewer",
         "text": "still broken", "verdict": _verdict("fail")},
    ]
    result = derive_review_iteration(state, "pln_1", 2)
    assert result["ok"] is True
    assert result["round"] == 2
    assert (result["rework_step"]["step"], result["review_step"]["step"]) == (5, 6)
    # 原任务取自 round-1 rework approval(step 3),不是 step 1
    assert "原任务: rework" in result["rework_step"]["task"]


def test_derive_falls_back_to_dispatched_step_without_worktrees() -> None:
    state = _state("fail")
    state["messages"][0]["worktree_branch"] = None  # 无任何 worktree 分支
    result = derive_review_iteration(state, "pln_1", 2)
    assert result["ok"] is True
    assert result["rework_step"]["agent_id"] == "coder"
    assert "原任务: build the widget" in result["rework_step"]["task"]


def test_rework_task_template_truncates_with_trace_pointer() -> None:
    text = build_rework_task(
        round_number=1,
        original_task="build the widget",
        verdict=_verdict("fail", score=41),
        reply_id="rep_new",
        reply_text="x" * (MAX_REWORK_TASK_CHARS * 2),
    )
    assert len(text) <= MAX_REWORK_TASK_CHARS
    assert "agentdeck trace --id rep_new" in text
    assert text.rstrip().endswith("修复后 commit 到任务分支。")
    short = build_rework_task(
        round_number=2,
        original_task="build the widget",
        verdict=_verdict("needs_changes"),
        reply_id="rep_new",
        reply_text="short findings",
    )
    assert "short findings" in short and "score" not in short
    assert "(score 41)" in text
