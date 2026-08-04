from __future__ import annotations

import pytest

from agentdeck.branch_custody import (
    CUSTODY_STATES,
    CUSTODY_UNVERIFIABLE_REASONS,
    classify_branch_custody,
)


def test_recorded_branch_that_still_exists_and_was_never_settled_is_held() -> None:
    result = classify_branch_custody(
        worktree_branch="agentdeck/coder/msg_1",
        settled_by_agentdeck=False,
        branch_exists=True,
    )

    assert result["state"] == "held"
    assert result["reason"] is None


def test_branch_agentdeck_itself_settled_is_settled() -> None:
    # AgentDeck 自己合并/放弃过它——分支还在不在都不改变这个判定。
    for exists in (True, False):
        result = classify_branch_custody(
            worktree_branch="agentdeck/coder/msg_1",
            settled_by_agentdeck=True,
            branch_exists=exists,
        )
        assert result["state"] == "settled"


def test_branch_gone_without_agentdeck_settling_it_is_gone_unrecorded() -> None:
    # 这就是 round 1 的 ①:reviewer 自行 git merge 并清理分支,而账本毫不知情。
    result = classify_branch_custody(
        worktree_branch="agentdeck/coder/msg_1",
        settled_by_agentdeck=False,
        branch_exists=False,
    )

    assert result["state"] == "gone_unrecorded"
    assert result["reason"] is None


def test_no_recorded_branch_is_unverifiable_not_held() -> None:
    result = classify_branch_custody(
        worktree_branch=None,
        settled_by_agentdeck=False,
        branch_exists=None,
    )

    assert result["state"] == "unverifiable"
    assert result["reason"] == "not_recorded"


def test_unresolvable_git_is_unverifiable_not_gone() -> None:
    # 解析不了绝不能塌成"分支没了"——那会把一次探测失败报成一次越界合并。
    result = classify_branch_custody(
        worktree_branch="agentdeck/coder/msg_1",
        settled_by_agentdeck=False,
        branch_exists=None,
    )

    assert result["state"] == "unverifiable"
    assert result["reason"] == "no_git_repo"


def test_states_and_reasons_are_closed_enums() -> None:
    assert set(CUSTODY_STATES) == {"held", "settled", "gone_unrecorded", "unverifiable"}
    assert set(CUSTODY_UNVERIFIABLE_REASONS) == {"not_recorded", "no_git_repo"}


def test_unverifiable_never_renders_as_a_clean_result() -> None:
    # 与 review_digest 同一条纪律:`unverifiable` 绝不能读成"没事"。
    # 没有任何一个布尔字段会让"查不了"和"查过了没问题"看起来一样。
    unverifiable = classify_branch_custody(
        worktree_branch=None, settled_by_agentdeck=False, branch_exists=None
    )
    held = classify_branch_custody(
        worktree_branch="b", settled_by_agentdeck=False, branch_exists=True
    )

    assert unverifiable["state"] != held["state"]
    assert not any(value is False for value in unverifiable.values())


# ---- CLI 侧:git 解析 + 投影(store 一次都不 shell out)----

import subprocess

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore


def _repo_with_task_branch(tmp_path, monkeypatch, branch: str):
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
    subprocess.run(["git", "branch", branch], cwd=root, check=True)

    store = StateStore(root)
    state = store.load()
    state["messages"] = [{"message_id": "msg_1", "worktree_branch": branch}]
    state["approvals"] = [{"approval_id": "apv_1", "plan_id": "pln_1", "message_id": "msg_1"}]
    store.save(state)
    return root, store


def test_plan_status_reports_a_branch_that_still_exists_as_held(tmp_path, monkeypatch) -> None:
    root, store = _repo_with_task_branch(tmp_path, monkeypatch, "agentdeck/coder/msg_1")
    config = cli.load_config(root)

    custody = cli._plan_branch_custody(config, store, "pln_1")

    assert custody["count"] == 1
    assert custody["items"][0]["state"] == "held"
    assert custody["gone_unrecorded_count"] == 0


def test_plan_status_flags_a_branch_deleted_out_of_band(tmp_path, monkeypatch) -> None:
    # round 1 的 ①:reviewer 自行合并并清理分支,AgentDeck 从未经手。
    branch = "agentdeck/coder/msg_1"
    root, store = _repo_with_task_branch(tmp_path, monkeypatch, branch)
    subprocess.run(["git", "branch", "-D", branch], cwd=root, check=True)
    config = cli.load_config(root)

    custody = cli._plan_branch_custody(config, store, "pln_1")

    assert custody["items"][0]["state"] == "gone_unrecorded"
    assert custody["gone_unrecorded_count"] == 1


def test_plan_status_does_not_flag_a_branch_agentdeck_merged_itself(tmp_path, monkeypatch) -> None:
    branch = "agentdeck/coder/msg_1"
    root, store = _repo_with_task_branch(tmp_path, monkeypatch, branch)
    store.mark_worktree_merged("msg_1")
    subprocess.run(["git", "branch", "-D", branch], cwd=root, check=True)
    config = cli.load_config(root)

    custody = cli._plan_branch_custody(config, store, "pln_1")

    assert custody["items"][0]["state"] == "settled"
    assert custody["gone_unrecorded_count"] == 0
