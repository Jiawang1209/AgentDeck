# Review Group + Round Reviewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 可选 `[review]` 配置让 review 环节确定性展开为多个串行 reviewer
step(any-fail-blocks 聚合、组完成才触发迭代),并让迭代回炉的复审步换成
独立 `round_reviewer`;缺省无配置时所有路径逐字节不变。

**Architecture:** 新纯模块 `src/agentdeck/review_group.py` 承担两件事:
`expand_review_group()`(plan dict → 展开后的 plan dict,纯函数)与组感知
verdict 选取/聚合(`latest_complete_group`、`aggregate_group_verdicts`)。
展开在 `cli._generate_leader_plan` 的两个 return 点应用(`record_plan`
零改动);聚合被 `StateStore.plan_verdict_summary` 与
`review_iteration.derive_review_iteration` 共用(单一来源)。执行引擎
(线性 plan、step 顺序守卫、worktree 链式检出、文件通道、审批预算)
**零改动**。

**Tech Stack:** Python 3.12 stdlib, pytest, conda env `agentdeck`.

**Spec:** `docs/superpowers/specs/2026-08-01-review-group-round-reviewer-design.md`
(frozen;含两条实现期修正:展开点接线、识别谓词收紧)

**Discipline:** All commands via `conda run -n agentdeck …`. Strict TDD.
No `git push`, no co-author trailer, nothing under `.omc/` staged. Each task
is one commit and carries its own `HISTORY.md` top entry under `## 2026-08-01`
(Type/Motivation/What/Impact/Verification, matching neighbours).

**Hard compatibility promise (every task must preserve):** with no `[review]`
section configured, plan generation, verdict summary, iteration trigger,
merge gate and all contracts are **byte-identical** to today. The existing
full suite (4830 passed) is the regression baseline.

**Key existing seams (verify with grep before editing):**
- `src/agentdeck/models.py:85` `ProjectConfig` dataclass (fields: leader,
  agents, runtime, daemon, autonomous, skills) — add `review: ReviewConfig`.
- `src/agentdeck/config.py` — `AutonomousPolicy` parsing (~line 325) and
  `_validated_max_review_rounds` are the fail-closed idiom to copy.
- `src/agentdeck/cli.py:12151` `_generate_leader_plan` — **two** return
  points (non-split at the end of the `if not use_split:` branch; split at
  cli.py:12216 `return result.plan, …`). Both must apply the expansion.
- `src/agentdeck/state.py:9814` `plan_verdict_summary` — currently: plan
  approvals' message_ids minus `rework_step_numbers`, iterate replies, keep
  last with dict verdict, `align_verdict_with_criteria`.
- `src/agentdeck/review_iteration.py:72` `_latest_verdict_reply` — same
  selection idiom; `derive_review_iteration` (line 168) consumes it.
- `src/agentdeck/review_iteration.py:43` `rework_step_numbers` — the
  provenance-based exclusion helper to mirror.
- `src/agentdeck/contracts.py:9997` `_validate_verdict_summary` — enforces
  `set(value) == set(REVIEW_VERDICT_SUMMARY_FIELDS)` (exact key set), so
  adding a `group` key REQUIRES updating that tuple + every fixture.
- `REVIEW_VERDICT_SUMMARY_FIELDS` and the three surfaces that embed it
  (leader review / leader summary / run_progress) — grep `verdict_summary`
  in contracts.py (hits at 3055, 3091, 3431, 9861).

---

## Task 1: `[review]` config section

**Files:**
- Modify: `src/agentdeck/models.py`, `src/agentdeck/config.py`
- Create: `tests/test_review_group.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing tests** — create `tests/test_review_group.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from agentdeck.config import load_config, write_default_config


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
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_review_group.py -q`
Expected: FAIL — `ProjectConfig` has no attribute `review`.

- [ ] **Step 3: Implement**

`src/agentdeck/models.py` — add next to `AutonomousPolicy`:

```python
@dataclass(frozen=True)
class ReviewConfig:
    round_reviewer: str | None = None
    reviewers: tuple[str, ...] = ()
```

and add the field to `ProjectConfig` (after `autonomous`):

```python
    review: ReviewConfig = ReviewConfig()
```

`src/agentdeck/config.py` — import `ReviewConfig`, and parse right after the
`AutonomousPolicy(...)` construction (mirroring its fail-closed style; the
agents tuple is already built at that point, so validate membership against
it):

```python
    review_raw = raw.get("review", {})
    if not isinstance(review_raw, dict):
        raise ValueError("review section must be a table")
    known_agents = {agent.agent_id for agent in agents}

    round_reviewer_raw = review_raw.get("round_reviewer")
    if round_reviewer_raw is None:
        round_reviewer = None
    else:
        if not isinstance(round_reviewer_raw, str) or not round_reviewer_raw.strip():
            raise ValueError("review round_reviewer must be a non-empty agent id")
        if round_reviewer_raw not in known_agents:
            raise ValueError(f"review round_reviewer is not a configured agent: {round_reviewer_raw}")
        round_reviewer = round_reviewer_raw

    reviewers_raw = review_raw.get("reviewers", [])
    if not isinstance(reviewers_raw, list):
        raise ValueError("review reviewers must be a list of agent ids")
    reviewers: list[str] = []
    for item in reviewers_raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("review reviewers entries must be non-empty agent ids")
        if item not in known_agents:
            raise ValueError(f"review reviewer is not a configured agent: {item}")
        if item in reviewers:
            raise ValueError(f"review reviewers must be unique: {item}")
        reviewers.append(item)
    review = ReviewConfig(round_reviewer=round_reviewer, reviewers=tuple(reviewers))
```

and pass `review=review` into the `ProjectConfig(...)` construction.

Note: `bool` is a subclass of `int`, but here the checks are `isinstance(...,
str)` so booleans are rejected by type already — no extra guard needed.

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_review_group.py tests/test_autonomy.py tests/test_leader_subrole_config.py -q`
Expected: all pass (report exact counts; the two existing config suites prove
no regression in sibling parsing).

- [ ] **Step 5: HISTORY + commit**

Type: feat, "Add [review] config section (round_reviewer, reviewers)".

```bash
git add src/agentdeck/models.py src/agentdeck/config.py tests/test_review_group.py HISTORY.md
git commit -m "feat: add review config section"
```

---

## Task 2: pure module `review_group.py` (expansion + aggregation)

**Files:**
- Create: `src/agentdeck/review_group.py`
- Test: `tests/test_review_group.py` (append)
- Modify: `HISTORY.md`

- [ ] **Step 1: Append the failing tests**

```python
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
```

Add `import json` at the top of the test file if not already present.

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_review_group.py -q`
Expected: FAIL at import (`ModuleNotFoundError: agentdeck.review_group`).

- [ ] **Step 3: Implement** — create `src/agentdeck/review_group.py`:

```python
"""Deterministic review-group expansion and any-fail-blocks aggregation.

`[review].reviewers` 让一个 review 环节确定性展开为 N 个串行 review step
(执行引擎零改动:仍是线性 plan + step 顺序守卫)。聚合按 user 拍板的
any-fail-blocks,且**组完成才判定**——组未齐绝不触发迭代,否则先 fail 的
成员会开一轮、后审旧代码的成员再开一轮,预算双烧。
纯模块:不碰 IO、不 import cli/state/config 对象(reviewers 以
(agent_id, role) 纯数据传入)。
See docs/superpowers/specs/2026-08-01-review-group-round-reviewer-design.md.
"""
from __future__ import annotations

import copy
from typing import Any

REVIEW_GROUP_ORIGIN = "review_group"
REVIEW_GROUP_RULE = "any_fail_blocks"
# 最严优先:fail > needs_changes > pass
_SEVERITY = {"pass": 0, "needs_changes": 1, "fail": 2}


def expand_review_group(
    plan: dict[str, Any], reviewers: tuple[tuple[str, str], ...]
) -> dict[str, Any]:
    """把每个 review step 展开为 N 个连续 step(编号顺延重排)。

    识别谓词:step 的 role 等于 reviewers[0] 的 role——首位 reviewer 是
    主 reviewer,其角色签名定义什么算 review 环节(跨角色组时不会误伤
    planning step)。reviewers 为空即原样返回。输入永不被修改。
    """
    if not reviewers:
        return plan
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return plan
    signature_role = reviewers[0][1]
    expanded: list[dict[str, Any]] = []
    group_number = 0
    number = 0
    for step in steps:
        if not isinstance(step, dict):
            return plan
        if step.get("role") != signature_role:
            number += 1
            item = copy.deepcopy(step)
            item["step"] = number
            expanded.append(item)
            continue
        group_number += 1
        for member_index, (agent_id, role) in enumerate(reviewers):
            number += 1
            item = copy.deepcopy(step)
            item.update({
                "step": number,
                "agent_id": agent_id,
                "role": role,
                "origin": REVIEW_GROUP_ORIGIN,
                "review_group": group_number,
                "review_group_member": member_index,
            })
            expanded.append(item)
    result = copy.deepcopy(plan)
    result["steps"] = expanded
    return result


def review_group_numbers(steps: list[dict[str, Any]]) -> dict[int, int]:
    """{step 编号: 组号};非组成员不出现(单 reviewer plan 返回空 dict)。"""
    mapping: dict[int, int] = {}
    for step in steps:
        if not isinstance(step, dict) or step.get("origin") != REVIEW_GROUP_ORIGIN:
            continue
        number = step.get("step")
        group = step.get("review_group")
        if (
            isinstance(number, int)
            and not isinstance(number, bool)
            and isinstance(group, int)
            and not isinstance(group, bool)
        ):
            mapping[number] = group
    return mapping


def latest_complete_group(
    steps: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    replies: list[dict[str, Any]],
    plan_id: str,
) -> dict[str, Any] | None:
    """最新一个**完整**组(每个成员 step 都有带 verdict 的回复)。

    返回 {group, members: [{step, agent_id, reply_id, verdict}],
    last_reply_id}。组未齐返回 None(调用方据此不触发迭代);plan 无
    组标记时也返回 None(调用方回落单 reviewer 路径)。
    """
    groups = review_group_numbers(steps)
    if not groups:
        return None
    steps_by_number = {
        step.get("step"): step for step in steps if isinstance(step, dict)
    }
    approval_by_step = {
        approval.get("step"): approval
        for approval in approvals
        if isinstance(approval, dict) and approval.get("plan_id") == plan_id
    }
    reply_by_message: dict[str, dict[str, Any]] = {}
    reply_order: dict[str, int] = {}
    for index, reply in enumerate(replies):
        if not isinstance(reply, dict) or not isinstance(reply.get("verdict"), dict):
            continue
        message_id = str(reply.get("message_id"))
        reply_by_message[message_id] = reply
        reply_order[message_id] = index
    by_group: dict[int, list[int]] = {}
    for number, group in groups.items():
        by_group.setdefault(group, []).append(number)
    for group in sorted(by_group, reverse=True):
        members: list[dict[str, Any]] = []
        last_index = -1
        for number in sorted(by_group[group]):
            approval = approval_by_step.get(number)
            message_id = str((approval or {}).get("message_id"))
            reply = reply_by_message.get(message_id)
            if reply is None:
                members = []
                break
            members.append({
                "step": number,
                "agent_id": (steps_by_number.get(number) or {}).get("agent_id"),
                "reply_id": reply.get("reply_id"),
                "verdict": reply["verdict"],
            })
            last_index = max(last_index, reply_order.get(message_id, -1))
        if members:
            return {
                "group": group,
                "members": members,
                "last_reply_id": members[-1]["reply_id"],
                "last_reply_index": last_index,
            }
    return None


def aggregate_group_verdicts(members: list[dict[str, Any]]) -> dict[str, Any]:
    """any-fail-blocks:overall 取最严;criteria 逐条合并(任一 fail 即
    fail,否则任一 unknown 即 unknown,全 pass 才 pass)。"""
    overall = "pass"
    merged: dict[str, str] = {}
    order: list[str] = []
    scores: list[int] = []
    for member in members:
        verdict = member.get("verdict") or {}
        value = str(verdict.get("overall"))
        if _SEVERITY.get(value, 2) > _SEVERITY.get(overall, 0):
            overall = value
        score = verdict.get("score")
        if isinstance(score, int) and not isinstance(score, bool):
            scores.append(score)
        for item in verdict.get("criteria") or []:
            if not isinstance(item, dict):
                continue
            criterion = str(item.get("criterion"))
            current = merged.get(criterion)
            incoming = str(item.get("verdict"))
            if criterion not in merged:
                order.append(criterion)
                merged[criterion] = incoming
            elif current != "fail" and (incoming == "fail" or current == "pass"):
                merged[criterion] = incoming
    return {
        "overall": overall,
        "rule": REVIEW_GROUP_RULE,
        "criteria": [
            {"criterion": criterion, "verdict": merged[criterion]}
            for criterion in order
        ],
        "score": min(scores) if scores else None,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_review_group.py -q`
Expected: all pass.

- [ ] **Step 5: HISTORY + commit**

Type: feat, "Add review-group pure module (deterministic expansion +
any-fail-blocks aggregation)".

```bash
git add src/agentdeck/review_group.py tests/test_review_group.py HISTORY.md
git commit -m "feat: add review group derivation module"
```

---

## Task 3: wire expansion into plan generation

**Files:**
- Modify: `src/agentdeck/cli.py`
- Test: `tests/test_review_group.py` (append)
- Modify: `HISTORY.md`

- [ ] **Step 1: Append the failing tests**

```python
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
```

Note: if the fake provider's plan has no review-role step, the first test's
expansion count will be 0 — inspect the fake plan first
(`grep -n "fake-plan\|class FakeProvider" -A 30 src/agentdeck/providers/*.py`)
and, if needed, adjust the assertion to whatever the fake plan's review step
count implies (do NOT weaken the numbering/continuity assertions). Report
what you found.

- [ ] **Step 2: RED** — `conda run -n agentdeck pytest tests/test_review_group.py -q -k generated`

- [ ] **Step 3: Implement** — in `cli.py`:

Import at the top: `from .review_group import expand_review_group`.

Add a small helper next to `_generate_leader_plan`:

```python
def _review_group_reviewers(config: ProjectConfig) -> tuple[tuple[str, str], ...]:
    """(agent_id, role) 纯数据;未配置或成员未知时返回空元组(不展开)。"""
    roles = {agent.agent_id: agent.role for agent in config.agents}
    pairs = [
        (agent_id, roles[agent_id])
        for agent_id in config.review.reviewers
        if agent_id in roles
    ]
    return tuple(pairs)
```

Then wrap **both** return points of `_generate_leader_plan` so the returned
plan is already expanded. The non-split branch currently returns the
single-stage plan; the split branch ends with
`return result.plan, orchestrator_provider_name, orchestrator_model, split_provenance`
(cli.py:12216). Change each to expand first, e.g.:

```python
    reviewers = _review_group_reviewers(config)
    return (
        expand_review_group(result.plan, reviewers),
        orchestrator_provider_name,
        orchestrator_model,
        split_provenance,
    )
```

and the analogous change at the non-split return. Do not touch
`record_plan`; do not touch the provider prompts.

- [ ] **Step 4: GREEN**

Run: `conda run -n agentdeck pytest tests/test_review_group.py tests/test_agent_cli.py tests/test_contracts.py -q`
Expected: all pass (report exact counts).

- [ ] **Step 5: HISTORY + commit**

Type: feat, "Expand review groups at plan generation".

```bash
git add src/agentdeck/cli.py tests/test_review_group.py HISTORY.md
git commit -m "feat: expand review group at plan generation"
```

---

## Task 4: group-aware aggregation + round_reviewer

**Files:**
- Modify: `src/agentdeck/state.py`, `src/agentdeck/review_iteration.py`,
  `src/agentdeck/cli.py`
- Test: `tests/test_review_group.py` (append)
- Modify: `HISTORY.md`

- [ ] **Step 1: Append the failing tests**

```python
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
```

- [ ] **Step 2: RED** — `conda run -n agentdeck pytest tests/test_review_group.py -q -k "group or round_reviewer"`

- [ ] **Step 3: Implement**

The writer must now know the review config. `StateStore` does not take
`ProjectConfig`; follow the established pattern of passing pure data in:
extend `append_review_iteration` and the derivation with an optional
`review_binding` parameter carrying the pure config data:

```python
# review_iteration.py
def derive_review_iteration(
    state: dict[str, Any],
    plan_id: str,
    max_review_rounds: int,
    review_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

where `review_binding` is
`{"round_reviewer": (agent_id, role) | None, "reviewers": ((agent_id, role), …)}`.
`StateStore.append_review_iteration(plan_id, max_review_rounds, source,
review_binding=None)` forwards it; `cli` builds it from config at the two
call sites (`plan_rework_command`, the run-loop hook) with a helper mirroring
`_review_group_reviewers`. When `review_binding` is None everything behaves
exactly as today (byte-identical default path).

In `review_iteration.py`:

1. `_latest_verdict_reply` gains group awareness — when
   `review_group.review_group_numbers(steps)` is non-empty, use
   `latest_complete_group(...)`: no complete group → return None
   (`no_verdict`, so an incomplete group cannot trigger); complete group →
   build a synthetic verdict via `aggregate_group_verdicts(members)` and use
   the group's **last** member reply id as `triggered_by_reply` (group-level
   idempotence), and the group's review approval (the first member's
   approval) as the review approval for step derivation. Keep the existing
   single-reply path untouched when there are no group markers.
2. Rework template: when aggregating a group, merge every non-pass member's
   failed criteria and reply text into per-reviewer sections
   (`### reviewer <agent_id>` headings) before the existing truncation.
3. Re-review step derivation: if `review_binding["reviewers"]` is non-empty,
   append one re-review step per reviewer (all carrying
   `origin="review_iteration"`, the same `round`, plus `review_group` =
   next group number and `review_group_member` = index, so the group-aware
   selection also covers appended groups); elif
   `review_binding["round_reviewer"]` is set, use that agent/role for the
   single re-review step; else today's clone behavior.

In `state.py` `plan_verdict_summary`: when the plan has group markers, use
the same `latest_complete_group` + `aggregate_group_verdicts`, then run the
existing `align_verdict_with_criteria` on the aggregate, and attach:

```python
        summary["group"] = {
            "size": len(group["members"]),
            "complete": True,
            "rule": REVIEW_GROUP_RULE,
            "members": [
                {"agent_id": m["agent_id"], "step": m["step"],
                 "overall": m["verdict"].get("overall"), "reply_id": m["reply_id"]}
                for m in group["members"]
            ],
        }
```

For the **single-reviewer path**, attach the same shape with `size=1` and the
single member (spec: GUI single/multi isomorphism). Incomplete group → return
None (no summary), which the merge gate already treats as "no verdict".

- [ ] **Step 4: GREEN**

Run: `conda run -n agentdeck pytest tests/test_review_group.py tests/test_review_iteration.py tests/test_plan_rework_cli.py tests/test_review_verdict_ingestion.py -q`
Expected: all pass. If existing single-reviewer tests break because
`verdict_summary` gained a `group` key, that is expected only where the
contract validator enforces the exact key set — fix it in Task 5, and note
here which tests are red for that reason (do not weaken them).

- [ ] **Step 5: HISTORY + commit**

Type: feat, "Aggregate review groups any-fail-blocks and honor round_reviewer".

```bash
git add src/agentdeck/state.py src/agentdeck/review_iteration.py src/agentdeck/cli.py tests/test_review_group.py HISTORY.md
git commit -m "feat: aggregate review groups and honor round reviewer"
```

---

## Task 5: contracts, docs, full ladder

**Files:**
- Modify: `src/agentdeck/contracts.py`, `src/agentdeck/state.py`
  (plan step projection), `docs/contracts/project-view-schema.md`,
  `docs/contracts/leader-review-schema.md` (and the summary/run contracts if
  they document verdict_summary fields — grep first), `CLAUDE.md`,
  `README.md`, `HISTORY.md`
- Test: `tests/test_review_group.py`

- [ ] **Step 1: Append the failing tests**

```python
def test_verdict_summary_contract_accepts_group() -> None:
    from agentdeck.contracts import (
        REVIEW_VERDICT_SUMMARY_FIELDS,
        validate_leader_review_contract,
        leader_review_example,
    )

    assert "group" in REVIEW_VERDICT_SUMMARY_FIELDS
    example = leader_review_example()
    assert validate_leader_review_contract(example)["ok"] is True


def test_plan_status_steps_expose_review_group(tmp_path) -> None:
    _root_dir, store = _seed_group_store(tmp_path, ["pass", "fail"])
    steps = store.plan_status("pln_g")["steps"]
    assert steps[0]["review_group"] is None
    assert steps[1]["review_group"] == 1
    assert steps[1]["review_group_member"] == 0
```

- [ ] **Step 2: RED** — run the two tests.

- [ ] **Step 3: Implement**

1. `REVIEW_VERDICT_SUMMARY_FIELDS` gains `"group"`; `_validate_verdict_summary`
   validates it: must be a dict with `size` (int ≥ 1), `complete` (bool),
   `rule` (`"any_fail_blocks"`), `members` (list of dicts with
   `agent_id`/`step`/`overall`/`reply_id`; `overall` in the verdict enum).
   Update **every** fixture that embeds a verdict summary (grep
   `verdict_summary` in contracts.py and tests/) with a `size=1` group.
2. `plan_status` step items gain `review_group` / `review_group_member`
   passthrough (None on normal steps), next to the existing
   `origin`/`round`/`triggered_by_reply` passthrough.
3. Docs: project-view-schema.md (step provenance list gains the two keys),
   leader-review-schema.md + any other contract doc that tables
   verdict_summary fields (add `group`), CLAUDE.md (extend the review
   iteration bullet with: `[review]` section semantics, deterministic
   expansion at plan generation with the reviewers[0]-role predicate,
   any-fail-blocks aggregation, **组完成才触发** guard, group-level
   idempotence, round_reviewer, and that defaults are byte-identical),
   README (one feature bullet).

- [ ] **Step 4: Full verification ladder** (report exact counts)

1. `conda run -n agentdeck pytest tests/test_review_group.py tests/test_review_iteration.py tests/test_plan_rework_cli.py -q`
2. `conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py tests/test_review_verdict_ingestion.py tests/test_run_loop_follow.py -q`
3. `conda run -n agentdeck python -m compileall src tests`
4. `git diff --check`
5. `conda run -n agentdeck pytest tests/ -q` — full suite (wait for it;
   expect ~4860+ passed, 3 skipped, 0 failed)
6. `git diff d84cca6a..HEAD -- src/agentdeck/daemon/` must be empty

- [ ] **Step 5: HISTORY + commit**

Type: feat, "Expose review group provenance in contracts and docs".

```bash
git add src/agentdeck/contracts.py src/agentdeck/state.py docs/contracts CLAUDE.md README.md tests/test_review_group.py HISTORY.md
git commit -m "feat: expose review group contracts and docs"
```

---

## Post-plan notes

- Update `docs/handoff/current-development-state.md` (review group +
  round_reviewer landed; live validation rides the next Line 1 round:
  configure `[review] reviewers = ["reviewer", "planner"]` on scratch and
  confirm the group expands, both reviewers run serially, an any-fail
  aggregate triggers exactly one iteration round, and the appended
  re-review is itself a group).
- Non-goals restated: parallel dispatch of one review step, majority /
  weighted aggregation, cross-group arbitration, Leader-chosen reviewers,
  partial-group early trigger.
