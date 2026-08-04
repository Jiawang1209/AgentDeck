"""Classify what became of a task branch AgentDeck created.

Walk-away round 1 finding ①: a reviewer merged its own work into `main` and
deleted the task branch. The merge gate landed that same morning --
`worktree_base_commit`, the digest binding, `review_stale` -- guards
*AgentDeck's* merge path. A worker with a shell walks straight around it.

The interesting defect is not that the merge happened. AgentDeck cannot stop a
shell from running `git merge`, and pretending otherwise would be the kind of
claimed-but-absent guarantee this repository keeps deleting. The defect is that
**the merge happened and the ledger never noticed**: AgentDeck's account of the
world quietly became false. That is observable, and observing it is cheap.

Four states, deliberately not a boolean:

| state | meaning |
| --- | --- |
| `held` | the branch is recorded, still exists, AgentDeck has not settled it |
| `settled` | AgentDeck itself merged or abandoned it -- its own record says so |
| `gone_unrecorded` | recorded, never settled by AgentDeck, and no longer there |
| `unverifiable` | we could not check: nothing recorded, or no git repo |

`gone_unrecorded` is the finding. `unverifiable` must never render as "fine" --
a failed probe and a clean check both read like "no problem" if they collapse
into one boolean, and only one of them is a fact the program established. This
is the same discipline as `review_digest`, for the same reason.

Pure module: zero IO, zero LLM, no git, no tmux; it does not import `cli`,
`state` or `config`. Git resolution happens in the caller and arrives here as
already-resolved values -- `branch_exists=None` means "could not resolve",
which is why an unresolvable repo can never be mistaken for a missing branch.

Nothing here is an authorization or a gate. It does not withhold a merge, block
a dispatch, or change any approval; it only lets the ledger say something true
about a branch it created.
"""
from __future__ import annotations

from typing import Any

# 闭合状态:调用方渲染时必须逐个处理,不得回落到"其余情况都算没事"。
CUSTODY_STATES = ("held", "settled", "gone_unrecorded", "unverifiable")

# `unverifiable` 的闭合原因。区分它们是有意义的:`not_recorded` 是老 message
# (那条 plan 跑在本功能之前),`no_git_repo` 是这次探测失败——前者永远不会变,
# 后者下次可能就有答案了。
CUSTODY_UNVERIFIABLE_REASONS = ("not_recorded", "no_git_repo")


def classify_branch_custody(
    *,
    worktree_branch: str | None,
    settled_by_agentdeck: bool,
    branch_exists: bool | None,
) -> dict[str, Any]:
    """判定一条已记录的任务分支现在处于什么状态。

    `branch_exists` 三值:`True` 还在、`False` 已不在、`None` 解析不了。
    最后一档**绝不能**塌进 `False`——那会把一次 git 探测失败报成一次越界
    合并,而越界合并是要给人看的告警。
    """
    branch = (worktree_branch or "").strip() or None
    if branch is None:
        return {"branch": None, "state": "unverifiable", "reason": "not_recorded"}
    if settled_by_agentdeck:
        # AgentDeck 自己经手过它,分支还在不在都不构成"没人知道的处置"。
        return {"branch": branch, "state": "settled", "reason": None}
    if branch_exists is None:
        return {"branch": branch, "state": "unverifiable", "reason": "no_git_repo"}
    if branch_exists:
        return {"branch": branch, "state": "held", "reason": None}
    return {"branch": branch, "state": "gone_unrecorded", "reason": None}
