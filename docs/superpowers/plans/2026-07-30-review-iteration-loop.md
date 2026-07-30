# Review Iteration Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Review verdict `fail`/`needs_changes` 自动向同一 plan 追加确定性
rework + re-review step(普通 pending 审批,预算 `max_review_rounds` 有界),
run-loop 引擎与显式 `agentdeck plan rework --confirm` 双面共享同一实现。

**Architecture:** 新纯模块 `src/agentdeck/review_iteration.py` 只做推导
(触发条件 + 模板拼装,零 IO、零 LLM);`StateStore.append_review_iteration()`
是唯一 state 写点(追加 steps、创建 approvals、`plan_rework_appended` 事件);
run-loop 单 wave 引擎在摄入之后、派发之前调用它,`plan rework` CLI 命令调用
同一 writer。run-loop"绝不调用 provider"不变量保持;审批/派发/merge gate
语义零变化。

**Tech Stack:** Python 3.12 stdlib, pytest, conda env `agentdeck`.

**Spec:** `docs/superpowers/specs/2026-07-30-review-iteration-loop-design.md`
(frozen + 2026-07-30 amendments: `needs_changes` 同触发、plan-rework 契约)

**Discipline:** All commands via `conda run -n agentdeck …`. Strict TDD.
No `git push`, no co-author trailer, nothing under `.omc/` staged. Each task
is one commit and carries its own `HISTORY.md` top entry under `## 2026-07-30`
(Type/Motivation/What/Impact/Verification, matching neighbours).

**Key existing seams (read, do not rewrite):**
- `src/agentdeck/review_verdict.py` — verdict schema:
  `overall ∈ {pass, fail, needs_changes}`, `criteria[].verdict ∈ {pass, fail, unknown}`.
- `StateStore.plan_verdict_summary` (state.py:9565) — latest-verdict selection
  idiom: plan approvals' `message_id` set → iterate `state["replies"]` in
  order, keep last with dict `verdict`.
- `StateStore._create_approvals_from_plan_state` (state.py:9906) — approval
  record shape;注意它对已有 approvals 的 plan 短路返回,**不能**复用来给
  追加 step 造审批,writer 必须自己造(同形状)。
- `_run_loop_single_wave` (cli.py:20718) — hook 插入点在
  `captured_replies = _ingest_plan_reply_files(...)`(cli.py:20744)之后、
  `# 3) dispatch` 之前。
- `_run_loop_all` 的每计划循环里同名调用在 cli.py:20183 附近。
- `AutonomousPolicy` 构造在 config.py:325 附近(`allowed_agents`,
  `max_approvals`);dataclass 定义在同文件。
- ProjectView plan item 投影 staticmethod 在 state.py:10260 附近(构建
  `planner_brief`/`step_count` 的那个)。
- `PROJECT_VIEW_PLAN_ITEM_FIELDS` (contracts.py:562);`TRACE_PLAN_FIELDS`
  由它派生,不需单独改。
- Contract index:`CONTRACT_INDEX_SPECS` (contracts.py:55);今天 42 项之前
  是 41 项——`tests/test_contracts.py`(count 41 与 name 列表)和
  `tests/test_agent_cli.py::test_contract_list_discovers_all_gui_contracts`
  (name 列表)都锁了索引,新增条目必须同步。

---

## Task 1: config `[autonomous] max_review_rounds`

**Files:**
- Modify: `src/agentdeck/config.py`
- Create: `tests/test_review_iteration.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing tests** — create `tests/test_review_iteration.py`:

```python
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


@pytest.mark.parametrize("bad", ['max_review_rounds = -1\n', 'max_review_rounds = "two"\n'])
def test_max_review_rounds_invalid_fails_closed(tmp_path: Path, bad: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_config(root, bad)
    with pytest.raises(ValueError):
        load_config(root)
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_review_iteration.py -q`
Expected: FAIL — `AutonomousPolicy` has no attribute `max_review_rounds`
(default test), and invalid-value tests fail because no validation exists.

- [ ] **Step 3: Implement** — in `src/agentdeck/config.py`:

1. Add the field to the `AutonomousPolicy` dataclass (keep existing fields
   untouched):

```python
    max_review_rounds: int = 2
```

2. Where the `AutonomousPolicy(...)` is constructed (~line 325), parse
   fail-closed:

```python
    if isinstance(autonomous_raw, dict):
        rounds_raw = autonomous_raw.get("max_review_rounds", 2)
    else:
        rounds_raw = 2
    if isinstance(rounds_raw, bool) or not isinstance(rounds_raw, int):
        raise ValueError("autonomous max_review_rounds must be an integer >= 0")
    if rounds_raw < 0:
        raise ValueError("autonomous max_review_rounds must be an integer >= 0")
```

and pass `max_review_rounds=rounds_raw` into the constructor. (TOML 整数天然
是 int;字符串/浮点/负数一律拒绝——比 `int(...)` 强转更严,符合 spec 的
fail-closed。)

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_review_iteration.py tests/test_autonomy.py -q`
Expected: all pass (autonomy 回归证明既有 policy 解析不变)

- [ ] **Step 5: HISTORY + commit**

`HISTORY.md` top entry under `## 2026-07-30`, Type: feat, title
"Add [autonomous] max_review_rounds config (review iteration budget)".
Motivation cites the spec path and the frozen default 2. Then:

```bash
git add src/agentdeck/config.py tests/test_review_iteration.py HISTORY.md
git commit -m "feat: add autonomous max_review_rounds config"
```

---

## Task 2: pure module `review_iteration.py`

**Files:**
- Create: `src/agentdeck/review_iteration.py`
- Test: `tests/test_review_iteration.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_review_iteration.py`):

```python
from agentdeck.review_iteration import (
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
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_review_iteration.py -q`
Expected: FAIL at import (`ModuleNotFoundError: agentdeck.review_iteration`)

- [ ] **Step 3: Implement** — create `src/agentdeck/review_iteration.py`:

```python
"""Pure derivation for the review-driven iteration loop.

review verdict `fail`/`needs_changes` 时,从既有 plan/replies 推导应追加的
rework + re-review step 对。只做推导:不碰 IO、不 import cli/state、零 LLM
(回炉任务是确定性模板)。唯一写点是 StateStore.append_review_iteration。
See docs/superpowers/specs/2026-07-30-review-iteration-loop-design.md.
"""
from __future__ import annotations

from typing import Any

REVIEW_ITERATION_ORIGIN = "review_iteration"
REWORK_TRIGGER_OVERALLS = frozenset({"fail", "needs_changes"})
MAX_REWORK_TASK_CHARS = 4000

# 闭合拒绝原因:调用方按原因决定沉默跳过还是如实报告。
REVIEW_ITERATION_SKIP_REASONS = (
    "no_plan",
    "no_verdict",
    "verdict_pass",
    "already_triggered",
    "rounds_exhausted",
    "no_implementation_step",
)


def _refuse(reason: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason}


def plan_review_rounds(steps: list[dict[str, Any]]) -> int:
    """已追加的迭代轮数 = step 上 round 标记的最大值(无标记 = 0)。"""
    rounds = 0
    for step in steps:
        if not isinstance(step, dict) or step.get("origin") != REVIEW_ITERATION_ORIGIN:
            continue
        value = step.get("round")
        if isinstance(value, int) and value > rounds:
            rounds = value
    return rounds


def _consumed_reply_ids(steps: list[dict[str, Any]]) -> set[str]:
    return {
        str(step["triggered_by_reply"])
        for step in steps
        if isinstance(step, dict)
        and step.get("origin") == REVIEW_ITERATION_ORIGIN
        and step.get("triggered_by_reply")
    }


def _latest_verdict_reply(
    state: dict[str, Any], plan_id: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(reply, 该 reply 所属 review step 的 approval);与 plan_verdict_summary
    同源的最新有效 verdict 选取(replies 按入账顺序,取最后一条)。"""
    approvals_by_message = {
        str(approval.get("message_id")): approval
        for approval in state.get("approvals", [])
        if isinstance(approval, dict)
        and approval.get("plan_id") == plan_id
        and approval.get("message_id")
    }
    latest: tuple[dict[str, Any], dict[str, Any]] | None = None
    for reply in state.get("replies", []):
        if not isinstance(reply, dict) or not isinstance(reply.get("verdict"), dict):
            continue
        approval = approvals_by_message.get(str(reply.get("message_id")))
        if approval is not None:
            latest = (reply, approval)
    return latest


def _implementation_approval(
    state: dict[str, Any], plan_id: str, review_step: int
) -> dict[str, Any] | None:
    """被审查的实现 step:优先取 review step 之前、message 带 worktree_branch
    的最大 step(base branch 直接来源);无 worktree 运行时回退到之前任一
    已派发 step 的最大者;都没有则 None(fail-closed 不追加)。"""
    messages_by_id = {
        str(message.get("message_id")): message
        for message in state.get("messages", [])
        if isinstance(message, dict)
    }
    best_branch: tuple[int, dict[str, Any]] | None = None
    best_dispatched: tuple[int, dict[str, Any]] | None = None
    for approval in state.get("approvals", []):
        if not isinstance(approval, dict) or approval.get("plan_id") != plan_id:
            continue
        try:
            step = int(approval.get("step") or 0)
        except (TypeError, ValueError):
            continue
        if step >= review_step or not approval.get("message_id"):
            continue
        message = messages_by_id.get(str(approval.get("message_id")), {})
        if message.get("worktree_branch"):
            if best_branch is None or step > best_branch[0]:
                best_branch = (step, approval)
        if best_dispatched is None or step > best_dispatched[0]:
            best_dispatched = (step, approval)
    chosen = best_branch or best_dispatched
    return chosen[1] if chosen else None


def build_rework_task(
    *,
    round_number: int,
    original_task: str,
    verdict: dict[str, Any],
    reply_id: str,
    reply_text: str,
) -> str:
    """确定性模板:fail 标准原文 + reviewer 回复原文,超长截断并附 trace
    指引;绝不调用 provider、不读产物文件。"""
    overall = str(verdict.get("overall"))
    score = verdict.get("score")
    header = [
        f"Review 第 {round_number} 轮未通过,按审查意见返工。",
        f"原任务: {original_task}",
        "审查判定: " + overall + (f" (score {score})" if isinstance(score, int) else ""),
    ]
    failed = [
        item
        for item in verdict.get("criteria", [])
        if isinstance(item, dict) and item.get("verdict") == "fail"
    ]
    if failed:
        header.append("未通过的验收标准:")
        for item in failed:
            entry = f"- {item.get('criterion')}"
            if item.get("evidence"):
                entry += f" (证据: {item['evidence']})"
            header.append(entry)
    footer = "修复后 commit 到任务分支。"
    text = "\n".join([*header, "审查意见原文:", reply_text, footer])
    if len(text) > MAX_REWORK_TASK_CHARS:
        marker = (
            f"\n[审查意见已截断,全文见 agentdeck trace --id {reply_id}]\n{footer}"
        )
        text = text[: MAX_REWORK_TASK_CHARS - len(marker)] + marker
    return text


def derive_review_iteration(
    state: dict[str, Any], plan_id: str, max_review_rounds: int
) -> dict[str, Any]:
    """纯触发判定 + step 对推导;任何条件不满足都返回 {ok: False, reason}。"""
    plan = next(
        (
            item
            for item in state.get("plans", [])
            if isinstance(item, dict) and item.get("plan_id") == plan_id
        ),
        None,
    )
    if plan is None:
        return _refuse("no_plan")
    body = plan.get("plan")
    steps = body.get("steps", []) if isinstance(body, dict) else []
    latest = _latest_verdict_reply(state, plan_id)
    if latest is None:
        return _refuse("no_verdict")
    reply, review_approval = latest
    if reply["verdict"].get("overall") not in REWORK_TRIGGER_OVERALLS:
        return _refuse("verdict_pass")
    reply_id = str(reply.get("reply_id"))
    if reply_id in _consumed_reply_ids(steps):
        return _refuse("already_triggered")
    rounds = plan_review_rounds(steps)
    if rounds >= max_review_rounds:
        return _refuse("rounds_exhausted")
    try:
        review_step_number = int(review_approval.get("step") or 0)
    except (TypeError, ValueError):
        return _refuse("no_implementation_step")
    implementation = _implementation_approval(state, plan_id, review_step_number)
    if implementation is None:
        return _refuse("no_implementation_step")
    round_number = rounds + 1
    numbers = [int(step.get("step") or 0) for step in steps if isinstance(step, dict)]
    next_number = (max(numbers) if numbers else 0) + 1
    provenance = {
        "origin": REVIEW_ITERATION_ORIGIN,
        "round": round_number,
        "triggered_by_reply": reply_id,
    }
    rework_step = {
        "step": next_number,
        "agent_id": implementation.get("agent_id"),
        "role": implementation.get("role"),
        "task": build_rework_task(
            round_number=round_number,
            original_task=str(implementation.get("task") or ""),
            verdict=reply["verdict"],
            reply_id=reply_id,
            reply_text=str(reply.get("text") or ""),
        ),
        "risk": implementation.get("risk") or "low",
        "requires_approval": True,
        **provenance,
    }
    review_step = {
        "step": next_number + 1,
        "agent_id": review_approval.get("agent_id"),
        "role": review_approval.get("role"),
        "task": str(review_approval.get("task") or ""),
        "risk": review_approval.get("risk") or "low",
        "requires_approval": True,
        **provenance,
    }
    return {
        "ok": True,
        "round": round_number,
        "triggered_by_reply": reply_id,
        "rework_step": rework_step,
        "review_step": review_step,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_review_iteration.py -q`
Expected: all pass

- [ ] **Step 5: HISTORY + commit**

Top entry, Type: feat, "Add review-iteration pure derivation module
(trigger matrix + deterministic rework template)".

```bash
git add src/agentdeck/review_iteration.py tests/test_review_iteration.py HISTORY.md
git commit -m "feat: add review iteration derivation module"
```

---

## Task 3: `StateStore.append_review_iteration` writer

**Files:**
- Modify: `src/agentdeck/state.py`
- Test: `tests/test_review_iteration.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_review_iteration.py`):

```python
def _seed_store(tmp_path, overall: str = "fail"):
    from agentdeck.state import StateStore

    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    write_default_config(root)
    store = StateStore(root)
    state = store.load()
    seed = _state(overall)
    for key in ("plans", "approvals", "messages", "replies"):
        state[key] = seed[key]
    store.save(state)
    return root, store


def test_writer_appends_steps_approvals_and_event(tmp_path) -> None:
    root, store = _seed_store(tmp_path, "fail")
    result = store.append_review_iteration("pln_1", 2, source="explicit")
    assert result["ok"] is True
    assert result["round"] == 1
    assert result["steps"] == [3, 4]
    assert len(result["approval_ids"]) == 2

    state = store.load()
    steps = state["plans"][0]["plan"]["steps"]
    assert [s["step"] for s in steps] == [1, 2, 3, 4]
    assert steps[2]["origin"] == REVIEW_ITERATION_ORIGIN
    new_approvals = [a for a in state["approvals"] if a["approval_id"] in result["approval_ids"]]
    assert [a["step"] for a in new_approvals] == [3, 4]
    assert all(a["status"] == "pending" and a["plan_id"] == "pln_1" for a in new_approvals)
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "plan_rework_appended"' in events
    assert '"source": "explicit"' in events


def test_writer_refusal_is_zero_write(tmp_path) -> None:
    root, store = _seed_store(tmp_path, "pass")
    before = store.load()
    events_before = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    result = store.append_review_iteration("pln_1", 2, source="run_loop")
    assert result == {"ok": False, "reason": "verdict_pass"}
    assert store.load() == before
    assert (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8") == events_before


def test_writer_is_idempotent_per_reply(tmp_path) -> None:
    _root, store = _seed_store(tmp_path, "fail")
    assert store.append_review_iteration("pln_1", 2, source="run_loop")["ok"] is True
    second = store.append_review_iteration("pln_1", 2, source="run_loop")
    assert second == {"ok": False, "reason": "already_triggered"}
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_review_iteration.py -q -k writer or idempotent`
Expected: FAIL — `StateStore` has no `append_review_iteration`

- [ ] **Step 3: Implement** — in `src/agentdeck/state.py`, next to
`create_approvals_from_plan` (~line 9882). Import at top of state.py:
`from .review_iteration import derive_review_iteration, plan_review_rounds`
(plan_review_rounds 供 Task 4 投影复用,一次引入)。

```python
    def append_review_iteration(
        self, plan_id: str, max_review_rounds: int, source: str
    ) -> dict[str, Any]:
        """Sole write path of the review iteration loop: append the derived
        rework/re-review step pair to the plan, create their pending
        approvals, and audit. Refusals are zero-write and returned as-is."""
        state = self.load()
        derived = derive_review_iteration(state, plan_id, max_review_rounds)
        if not derived.get("ok"):
            return derived
        plan_record = next(
            plan for plan in state["plans"] if plan.get("plan_id") == plan_id
        )
        new_steps = [derived["rework_step"], derived["review_step"]]
        plan_record["plan"]["steps"].extend(new_steps)
        approvals = []
        for step in new_steps:
            approvals.append(
                {
                    "approval_id": new_id("apv"),
                    "plan_id": plan_id,
                    "step": step["step"],
                    "agent_id": step["agent_id"],
                    "role": step["role"],
                    "task": step["task"],
                    "risk": step["risk"],
                    "status": "pending",
                    "created_at": utc_now(),
                }
            )
        state.setdefault("approvals", []).extend(approvals)
        self.save(state)
        self.append_event(
            EventRecord.create(
                "plan_rework_appended",
                {
                    "plan_id": plan_id,
                    "round": derived["round"],
                    "source": source,
                    "triggered_by_reply": derived["triggered_by_reply"],
                    "steps": [step["step"] for step in new_steps],
                    "approval_count": len(approvals),
                },
            )
        )
        return {
            "ok": True,
            "round": derived["round"],
            "triggered_by_reply": derived["triggered_by_reply"],
            "steps": [step["step"] for step in new_steps],
            "approval_ids": [approval["approval_id"] for approval in approvals],
        }
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_review_iteration.py -q`
Expected: all pass

- [ ] **Step 5: HISTORY + commit**

Top entry, Type: feat, "Add append_review_iteration single-point writer
(steps + approvals + audit)".

```bash
git add src/agentdeck/state.py tests/test_review_iteration.py HISTORY.md
git commit -m "feat: add review iteration state writer"
```

---

## Task 4: ProjectView `review_rounds` + step provenance projection

**Files:**
- Modify: `src/agentdeck/state.py`, `src/agentdeck/contracts.py`
- Modify: `docs/contracts/project-view-schema.md`
- Test: `tests/test_review_iteration.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing tests** (append):

```python
def test_project_view_plan_item_exposes_review_rounds(tmp_path) -> None:
    _root, store = _seed_store(tmp_path, "fail")
    view = store.project_view()
    item = view["plans"]["items"][0]
    assert item["review_rounds"] == 0
    store.append_review_iteration("pln_1", 2, source="explicit")
    item = store.project_view()["plans"]["items"][0]
    assert item["review_rounds"] == 1
    assert item["step_count"] == 4


def test_plan_status_steps_carry_iteration_provenance(tmp_path) -> None:
    _root, store = _seed_store(tmp_path, "fail")
    store.append_review_iteration("pln_1", 2, source="explicit")
    steps = store.plan_status("pln_1")["steps"]
    assert steps[0]["origin"] is None and steps[0]["round"] is None
    appended = steps[2]
    assert appended["origin"] == REVIEW_ITERATION_ORIGIN
    assert appended["round"] == 1
    assert appended["triggered_by_reply"] == "rep_new"


def test_project_view_contract_accepts_review_rounds() -> None:
    from agentdeck.contracts import (
        PROJECT_VIEW_PLAN_ITEM_FIELDS,
        project_view_example,
        validate_project_view_contract,
    )

    assert "review_rounds" in PROJECT_VIEW_PLAN_ITEM_FIELDS
    assert validate_project_view_contract(project_view_example())["ok"] is True
```

Notes for the engineer: `store.project_view()` 若真实入口名不同(如
`build_project_view`/`status` payload helper),用
`grep -n "plans" src/agentdeck/state.py | grep items` 找到 ProjectView 构建
入口并调整测试调用——断言内容不变。`project_view_example` 同理以
contracts.py 中实际 example 工厂名为准。

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_review_iteration.py -q -k project_view or provenance`
Expected: FAIL — plan item has no `review_rounds`, plan_status steps lack the
three keys, field list lacks the entry.

- [ ] **Step 3: Implement**

1. state.py plan item projection staticmethod(state.py:10260 附近,构建
   `planner_brief`/`step_count` 的那个)加一行(`steps` 变量已在作用域):

```python
            "review_rounds": plan_review_rounds(steps if isinstance(steps, list) else []),
```

2. `plan_status` 的 steps 构建处:每个 step item 增加三个键,从 plan body
   对应 step 记录取值、缺省 None:

```python
                "origin": plan_step.get("origin"),
                "round": plan_step.get("round"),
                "triggered_by_reply": plan_step.get("triggered_by_reply"),
```

   (`plan_status` 若以 approvals 为主构建 steps,先按 step 编号建
   `{step_number: plan_step_record}` 映射再取值。)

3. contracts.py:`PROJECT_VIEW_PLAN_ITEM_FIELDS` 加 `"review_rounds"`
   (加到 `"step_count"` 之前);所有 ProjectView example fixture 中的 plan
   item 加 `"review_rounds": 0`。`validate_project_view_contract` 若逐字段
   校验 plan item,会因 example 同步自动通过;跑
   `conda run -n agentdeck pytest tests/test_contracts.py -q` 让 validator
   报错点名遗漏的 fixture。

4. `docs/contracts/project-view-schema.md`:plan item 字段表加
   `review_rounds`(只读,已追加的 review 迭代轮数,普通 plan 为 0);
   步骤级 `origin`/`round`/`triggered_by_reply` provenance 说明(普通 step
   为 null;不是权限或执行授权)。

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_review_iteration.py tests/test_contracts.py tests/test_agent_cli.py -q`
Expected: all pass(agent_cli 覆盖 status/plan CLI 输出路径)

- [ ] **Step 5: HISTORY + commit**

Top entry, Type: feat, "Expose review_rounds and iteration step provenance in
ProjectView/plan status".

```bash
git add src/agentdeck/state.py src/agentdeck/contracts.py \
  docs/contracts/project-view-schema.md tests/test_review_iteration.py HISTORY.md
git commit -m "feat: project review iteration provenance into ProjectView"
```

---

## Task 5: `agentdeck plan rework --confirm` + plan-rework contract

**Files:**
- Modify: `src/agentdeck/cli.py`, `src/agentdeck/contracts.py`
- Create: `docs/contracts/plan-rework-schema.md`
- Modify: `tests/test_contracts.py`, `tests/test_agent_cli.py`
- Create: `tests/test_plan_rework_cli.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing tests** — create `tests/test_plan_rework_cli.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore

# pytest 默认 importmode=prepend 会把 tests/ 加进 sys.path,同目录直接导入
from test_review_iteration import _state, _verdict


def prepare_seeded_project(tmp_path: Path, monkeypatch, overall: str = "fail") -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    store = StateStore(root)
    state = store.load()
    seed = _state(overall)
    for key in ("plans", "approvals", "messages", "replies"):
        state[key] = seed[key]
    store.save(state)
    monkeypatch.chdir(root)
    return root


def test_rework_gate_matrix_refuses_with_zero_writes(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_seeded_project(tmp_path, monkeypatch, "fail")
    store = StateStore(root)
    before = store.load()

    assert cli.main(["plan", "rework", "--plan-id", "pln_1"]) == 1
    assert "confirm" in capsys.readouterr().err
    assert cli.main(["plan", "rework", "--plan-id", "pln_ghost", "--confirm"]) == 1
    assert "no_plan" in capsys.readouterr().err
    assert store.load() == before


def test_rework_refuses_on_pass_verdict(tmp_path, monkeypatch, capsys) -> None:
    prepare_seeded_project(tmp_path, monkeypatch, "pass")
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 1
    assert "verdict_pass" in capsys.readouterr().err


def test_rework_appends_and_reports(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_seeded_project(tmp_path, monkeypatch, "fail")
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan_rework"
    assert payload["ok"] is True
    assert payload["plan_id"] == "pln_1"
    assert payload["round"] == 1
    assert payload["steps"] == [3, 4]
    assert len(payload["approval_ids"]) == 2
    assert payload["next_command"] == "agentdeck approval list"
    assert payload["requires_explicit_user"] is True
    assert payload["safety"] == "explicit_user"
    steps = StateStore(root).load()["plans"][0]["plan"]["steps"]
    assert len(steps) == 4
    # 同一 reply 第二次拒绝
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 1
    assert "already_triggered" in capsys.readouterr().err


def test_rework_respects_budget(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_seeded_project(tmp_path, monkeypatch, "fail")
    config_path = root / ".agentdeck" / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n[autonomous]\nmax_review_rounds = 0\n",
        encoding="utf-8",
    )
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 1
    assert "rounds_exhausted" in capsys.readouterr().err
```

Also append to `tests/test_review_iteration.py`:

```python
def test_plan_rework_contract_shapes() -> None:
    from agentdeck.contracts import (
        PLAN_REWORK_RESPONSE_FIELDS,
        plan_rework_example,
        validate_plan_rework_contract,
    )

    for field in ("ok", "mode", "plan_id", "round", "steps", "approval_ids",
                  "triggered_by_reply", "next_command", "requires_explicit_user", "safety"):
        assert field in PLAN_REWORK_RESPONSE_FIELDS
    assert validate_plan_rework_contract(plan_rework_example())["ok"] is True
    broken = dict(plan_rework_example())
    broken.pop("round")
    assert validate_plan_rework_contract(broken)["ok"] is False
    assert validate_plan_rework_contract({**plan_rework_example(), "mode": "nope"})["ok"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_plan_rework_cli.py tests/test_review_iteration.py -q -k rework`
Expected: FAIL — argparse 无 `plan rework` 子命令,contract 符号缺失

- [ ] **Step 3: Implement**

1. contracts.py(放在 run-loop-host 契约代码之后):

```python
PLAN_REWORK_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "plan_id",
    "round",
    "steps",
    "approval_ids",
    "triggered_by_reply",
    "next_command",
    "requires_explicit_user",
    "safety",
)


def validate_plan_rework_contract(payload: dict[str, object]) -> dict[str, object]:
    errors = _validate_fields(payload, PLAN_REWORK_RESPONSE_FIELDS, "plan_rework")
    if payload.get("mode") != "plan_rework":
        errors.append(f"plan_rework.mode must be plan_rework, got {payload.get('mode')}")
    if not isinstance(payload.get("round"), int) or payload.get("round") < 1:
        errors.append("plan_rework.round must be an int >= 1")
    for list_field in ("steps", "approval_ids"):
        value = payload.get(list_field)
        if not isinstance(value, list) or len(value) != 2:
            errors.append(f"plan_rework.{list_field} must be a list of exactly 2 items")
    if payload.get("safety") != "explicit_user":
        errors.append("plan_rework.safety must be explicit_user")
    if payload.get("requires_explicit_user") is not True:
        errors.append("plan_rework.requires_explicit_user must be true")
    return {"ok": not errors, "errors": errors}


def plan_rework_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "plan_rework",
        "plan_id": "pln_example",
        "round": 1,
        "steps": [3, 4],
        "approval_ids": ["apv_rework", "apv_rereview"],
        "triggered_by_reply": "rep_example",
        "next_command": "agentdeck approval list",
        "requires_explicit_user": True,
        "safety": "explicit_user",
    }


def plan_rework_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "rework_command_template": "agentdeck plan rework --plan-id <plan_id> --confirm",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(PLAN_REWORK_RESPONSE_FIELDS),
        "skip_reasons": list(REVIEW_ITERATION_SKIP_REASONS),
        "trigger_overalls": sorted(REWORK_TRIGGER_OVERALLS),
        "run_loop_contract": "agentdeck contract run-loop",
    }


def plan_rework_contract_response(
    contract_path: Path, include_example: bool = False
) -> dict[str, object]:
    payload = plan_rework_contract_payload(contract_path)
    if include_example:
        payload["example"] = plan_rework_example()
    return payload
```

   Import at contracts.py top(与 run_loop_host 导入并列):
   `from .review_iteration import REVIEW_ITERATION_SKIP_REASONS, REWORK_TRIGGER_OVERALLS`。
   `CONTRACT_INDEX_SPECS` 在 `("run-loop-host", …)` 之后注册:

```python
    (
        "plan-rework",
        "agentdeck contract plan-rework",
        "agentdeck contract plan-rework --example",
        "plan-rework-schema.md",
    ),
```

2. cli.py 命令(放在 plan board 命令附近):

```python
def plan_rework_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if not args.confirm:
        print("plan rework requires --confirm", file=sys.stderr)
        return 1
    result = store.append_review_iteration(
        str(args.plan_id), config.autonomous.max_review_rounds, source="explicit"
    )
    if not result.get("ok"):
        print(f"plan rework refused: {result.get('reason')}", file=sys.stderr)
        return 1
    payload = {
        "ok": True,
        "mode": "plan_rework",
        "plan_id": str(args.plan_id),
        "round": result["round"],
        "steps": result["steps"],
        "approval_ids": result["approval_ids"],
        "triggered_by_reply": result["triggered_by_reply"],
        "next_command": "agentdeck approval list",
        "requires_explicit_user": True,
        "safety": "explicit_user",
    }
    validation = validate_plan_rework_contract(payload)
    if not validation["ok"]:
        print("plan rework contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def contract_plan_rework_command(args: argparse.Namespace) -> int:
    contract_path = (
        Path(__file__).resolve().parents[2] / "docs" / "contracts" / "plan-rework-schema.md"
    )
    _print_json(plan_rework_contract_response(contract_path, include_example=args.example))
    return 0
```

   注意 `no_plan`:writer 的 derive 对未知 plan 返回 `no_plan`,stderr 输出
   `plan rework refused: no_plan` 满足测试。cli.py 顶部 contracts 导入块补
   `plan_rework_contract_response, validate_plan_rework_contract`。argparse:
   在 plan 子命令组(`plan list/show/status/board` 所在)加:

```python
    plan_rework = plan_subparsers.add_parser(
        "rework", help="Append a review-driven rework + re-review step pair (explicit)"
    )
    plan_rework.add_argument("--plan-id", required=True, help="Plan with a failing review verdict")
    plan_rework.add_argument("--confirm", action="store_true", help="Explicitly confirm the append")
    plan_rework.set_defaults(func=plan_rework_command)
```

   contract 子命令(`contract run-loop-host` 注册之后):

```python
    contract_plan_rework = contract_subparsers.add_parser("plan-rework", help="Show plan rework contract metadata")
    contract_plan_rework.add_argument("--example", action="store_true", help="Include a GUI-ready plan rework example")
    contract_plan_rework.set_defaults(func=contract_plan_rework_command)
```

   plan 子命令组若当前叫别的变量名,以 `grep -n '"board"' src/agentdeck/cli.py`
   找到同组 add_parser 位置为准。

3. `docs/contracts/plan-rework-schema.md`:发现入口、响应字段表、闭合
   `skip_reasons` 枚举(六个值 + 各自含义与后续动作)、trigger_overalls
   (`fail`/`needs_changes`)、安全边界(explicit_user、只追加 step 与
   pending approvals、不派发、不 auto-approve、不调 provider、不读 tmux、
   同 reply 幂等、预算 `[autonomous] max_review_rounds` 约束、越过预算须
   人工改配置)。

4. 契约索引测试同步(41→42):`tests/test_contracts.py` 的
   `assert payload["count"] == 41` 改 42、name 列表和 docs 文件名集合各加
   `"plan-rework"` / `"plan-rework-schema.md"`;`tests/test_agent_cli.py::
   test_contract_list_discovers_all_gui_contracts` name 列表在
   `"run-loop-host"` 后加 `"plan-rework"`。

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_plan_rework_cli.py tests/test_review_iteration.py tests/test_contracts.py tests/test_agent_cli.py -q`
Expected: all pass

- [ ] **Step 5: HISTORY + commit**

Top entry, Type: feat, "Add explicit plan rework command with plan-rework
contract".

```bash
git add src/agentdeck/cli.py src/agentdeck/contracts.py \
  docs/contracts/plan-rework-schema.md tests/test_plan_rework_cli.py \
  tests/test_review_iteration.py tests/test_contracts.py tests/test_agent_cli.py HISTORY.md
git commit -m "feat: add plan rework command and contract"
```

---

## Task 6: run-loop 引擎钩子 + `--max-review-rounds` 贯通 + 契约字段

**Files:**
- Modify: `src/agentdeck/cli.py`, `src/agentdeck/contracts.py`
- Modify: `docs/contracts/run-loop-schema.md`, `docs/contracts/run-loop-all-schema.md`
- Test: `tests/test_plan_rework_cli.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_plan_rework_cli.py`):

```python
def _enable_autonomous(capsys) -> None:
    cli.main([
        "policy", "set-mode", "--mode", "autonomous", "--confirm",
        "--allow-agent", "coder", "--allow-agent", "reviewer", "--max-approvals", "8",
    ])
    capsys.readouterr()


def test_run_loop_wave_appends_iteration_on_fail_verdict(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_seeded_project(tmp_path, monkeypatch, "fail")
    _enable_autonomous(capsys)
    assert cli.main(["run-loop", "--plan-id", "pln_1", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    iterations = payload["review_iterations"]
    assert iterations[0]["round"] == 1
    assert iterations[0]["steps"] == [3, 4]
    state = StateStore(root).load()
    assert len(state["plans"][0]["plan"]["steps"]) == 4
    # 追加的审批当 wave 未被 auto-approve(选取先于追加),下一 wave 接手
    new_pending = [a for a in state["approvals"] if a["status"] == "pending"]
    assert len(new_pending) == 2


def test_run_loop_wave_reports_rounds_exhausted(tmp_path, monkeypatch, capsys) -> None:
    prepare_seeded_project(tmp_path, monkeypatch, "fail")
    _enable_autonomous(capsys)
    assert cli.main([
        "run-loop", "--plan-id", "pln_1", "--confirm", "--max-review-rounds", "0",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "review_iterations" not in payload  # 0 = 关闭,逐字节同现状
    assert cli.main(["run-loop", "--plan-id", "pln_1", "--confirm"]) == 0
    capsys.readouterr()
    # 第一次已消费 reply;造第二个 fail reply 并把预算压到 1 → exhausted
    store = StateStore(Path.cwd())
    state = store.load()
    state["approvals"].append({
        "approval_id": "apv_4", "plan_id": "pln_1", "step": 4, "agent_id": "reviewer",
        "role": "review", "task": "review the widget", "risk": "low",
        "status": "dispatched", "message_id": "msg_rev2",
    })
    state["messages"].append({"message_id": "msg_rev2", "worktree_branch": None})
    state["replies"].append({
        "reply_id": "rep_round2", "message_id": "msg_rev2", "from_agent": "reviewer",
        "text": "still failing", "verdict": _verdict("fail"),
    })
    store.save(state)
    assert cli.main([
        "run-loop", "--plan-id", "pln_1", "--confirm", "--max-review-rounds", "1",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["review_iterations"] == [{"skipped": "rounds_exhausted"}]


def test_run_loop_wave_without_verdict_is_byte_stable(tmp_path, monkeypatch, capsys) -> None:
    prepare_seeded_project(tmp_path, monkeypatch, "pass")
    _enable_autonomous(capsys)
    assert cli.main(["run-loop", "--plan-id", "pln_1", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "review_iterations" not in payload


def test_run_loop_all_plan_item_carries_review_iterations(tmp_path, monkeypatch, capsys) -> None:
    prepare_seeded_project(tmp_path, monkeypatch, "fail")
    _enable_autonomous(capsys)
    assert cli.main(["run-loop", "--all", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_all"
    item = next(p for p in payload["plans"] if p["plan_id"] == "pln_1")
    assert item["review_iterations"][0]["round"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_plan_rework_cli.py -q -k run_loop`
Expected: FAIL — payload 无 `review_iterations`,argparse 无
`--max-review-rounds`

- [ ] **Step 3: Implement**

1. `_run_loop_single_wave` 签名加参(默认 None 保持既有调用不变):

```python
def _run_loop_single_wave(
    config: ProjectConfig, store: StateStore, plan_id: str,
    max_review_rounds: int | None = None,
) -> dict[str, object] | None:
```

   在 `captured_replies = _ingest_plan_reply_files(config, store, plan_id)`
   之后、`# 3) dispatch` 之前插入:

```python
    # 2b) review-iteration hook: a latest fail/needs_changes verdict appends a
    # deterministic rework + re-review step pair (bounded by max_review_rounds,
    # idempotent per reply). Appended approvals stay pending this wave — the
    # next wave's existing auto-approve + step-order guard take over.
    effective_rounds = (
        config.autonomous.max_review_rounds
        if max_review_rounds is None
        else max_review_rounds
    )
    review_iterations: list[dict[str, object]] = []
    if effective_rounds > 0:
        appended = store.append_review_iteration(
            plan_id, effective_rounds, source="run_loop"
        )
        if appended.get("ok"):
            review_iterations.append({
                "round": appended["round"],
                "steps": appended["steps"],
                "approval_ids": appended["approval_ids"],
                "triggered_by_reply": appended["triggered_by_reply"],
            })
        elif appended.get("reason") == "rounds_exhausted":
            review_iterations.append({"skipped": "rounds_exhausted"})
```

   payload 构建处(`if captured_replies:` 旁)加:

```python
    if review_iterations:
        payload["review_iterations"] = review_iterations
```

2. run-loop 命令:argparse 加
   `run_loop.add_argument("--max-review-rounds", type=int, default=None, help="Override [autonomous] max_review_rounds for this run (0 disables iteration)")`;
   `run_loop_command` 开头校验
   `if args.max_review_rounds is not None and args.max_review_rounds < 0: 拒绝退 1`;
   单 wave、`--follow` 循环、`_run_loop_all` 的每计划调用全部把
   `max_review_rounds=args.max_review_rounds` 传给 `_run_loop_single_wave`
   或(`_run_loop_all` 独立实现时)在其每计划 ingest 调用后插入与 1) 相同
   的钩子代码块,结果记入该计划 item 的可选 `review_iterations`。

3. run-loop-host:`host_start` 加同名参数并在 argv 构建处
   `if args.max_review_rounds is not None: argv += ["--max-review-rounds", str(args.max_review_rounds)]`;
   `host_serve` 加同名参数(default None),serve 调用
   `_run_loop_single_wave(config, store, plan_id, max_review_rounds=args.max_review_rounds)`。
   host start/status 响应与 host.json 记录**不加**该字段(spec:host 契约
   零变化)。

4. contracts.py:`validate_run_loop_contract` 加:

```python
    review_iterations = payload.get("review_iterations")
    if review_iterations is not None and not isinstance(review_iterations, list):
        errors.append("run_loop.review_iterations must be a list when present")
```

   `validate_run_loop_all_contract` 的 plan item 循环加同样的可选检查
   (`run_loop_all.plans[{index}].review_iterations must be a list when present`)。
   follow validator 逐 wave 复用单 wave validator,无需改。

5. docs:`run-loop-schema.md` 加 `review_iterations[]`(可选;出现即为
   本 wave 追加/跳过记录,item 字段 round/steps/approval_ids/
   triggered_by_reply 或 skipped)与 `--max-review-rounds` 说明、指向
   plan-rework contract;`run-loop-all-schema.md` plan item 加同名可选字段。

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_plan_rework_cli.py tests/test_run_loop_follow.py tests/test_run_loop_host_cli.py tests/test_autonomy.py tests/test_contracts.py -q`
Expected: all pass(follow/host/autonomy 回归证明既有 wave 行为不变)

- [ ] **Step 5: HISTORY + commit**

Top entry, Type: feat, "Wire review iteration hook into run-loop waves with
--max-review-rounds".

```bash
git add src/agentdeck/cli.py src/agentdeck/contracts.py \
  docs/contracts/run-loop-schema.md docs/contracts/run-loop-all-schema.md \
  tests/test_plan_rework_cli.py HISTORY.md
git commit -m "feat: wire review iteration into run-loop waves"
```

---

## Task 7: CLAUDE.md 规则、README、全量阶梯、handoff

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `HISTORY.md`
- Modify: `docs/handoff/current-development-state.md`

- [ ] **Step 1: CLAUDE.md** — 在 run-loop-host 规则 bullet 之后加一条规则
bullet,覆盖:纯模块/writer 单点結構;触发条件闭合(有效 verdict 且
overall ∈ {fail, needs_changes}、criterion 级 unknown 与无效 verdict 不触发、
同 reply 幂等、最新 verdict 才算);确定性模板(fail 标准 + reviewer 回复
原文,4000 字符截断附 trace 指引,零 LLM);预算
`[autonomous] max_review_rounds`(默认 2)+ `--max-review-rounds` 覆盖、
0=关闭且逐字节不变、预算耗尽绝不静默越过;追加 step 走普通 pending 审批
(下一 wave 由既有 auto-approve + step 顺序守卫接手);
`plan rework --confirm` 为显式触发面(拒绝路径零写);
`plan_rework_appended` 审计;ProjectView `review_rounds` 与 step
origin/round/triggered_by_reply 只是 provenance 不是授权;merge gate 语义
不变;非目标(round_reviewer、多 reviewer、Leader 精修、跨 plan);修改
字段/触发条件/validator 须同步 plan-rework + run-loop 契约、contract
index、README、HISTORY 和测试。常用命令块加
`agentdeck plan rework --plan-id pln_xxx --confirm`。

- [ ] **Step 2: README** — feature bullet 列表(run-loop-host bullet 之后)
加 review iteration bullet:fail/needs_changes verdict 自动追加确定性
rework+re-review step、预算 `max_review_rounds`、普通审批、
`plan rework --confirm` 手动面、发现入口 `agentdeck contract plan-rework`。

- [ ] **Step 3: Full verification ladder**(报告精确计数)

1. `conda run -n agentdeck pytest tests/test_review_iteration.py tests/test_plan_rework_cli.py -q`
2. `conda run -n agentdeck pytest tests/test_run_loop_follow.py tests/test_run_loop_host_cli.py tests/test_autonomy.py tests/test_contracts.py tests/test_agent_cli.py -q`
3. `conda run -n agentdeck python -m compileall src tests`
4. `git diff --check`
5. `conda run -n agentdeck pytest tests/ -q`(全量,~5 分钟;预期 ~4800+
   passed, 3 skipped)
6. `git diff f65b9b10..HEAD -- src/agentdeck/daemon/` 必须零输出

- [ ] **Step 4: handoff + HISTORY + commit**

`docs/handoff/current-development-state.md` 顶部段落更新:review 迭代闭环
落地(commit 范围、触发/预算/审批语义一句话、live 验证待下轮 round:真实
reviewer 打 fail → 自动回炉 → coder 修 → re-review pass → 自动 merge)。
HISTORY top entry, Type: feat, "Land review-driven iteration loop docs and
full-suite baseline"(记录阶梯全部计数)。

```bash
git add CLAUDE.md README.md HISTORY.md docs/handoff/current-development-state.md
git commit -m "docs: record review iteration loop landing"
```

---

## Post-plan notes

- Live 验证搭下轮 Line 1 round:预期链路 fail verdict → wave 追加(事件
  `plan_rework_appended` source=run_loop)→ 下一 wave auto-approve + 按
  step 顺序派发 rework → 文件通道回复 → 派发 re-review → pass verdict →
  gate complete → merge gate 以最新 pass 放行自动合并。
- Non-goals restated: round_reviewer 独立角色、多 reviewer 聚合、Leader
  精修回炉任务(二期显式命令)、跨 plan 迭代、`plan rework` 的只读
  preview 卡与 leader-chat 意图(后续切片)。
