# Verdict Digest Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind every review verdict to the exact git commit it judged and every artifact to its content hash, so AgentDeck can refuse to auto-merge code that changed after it was reviewed.

**Architecture:** Two additive provenance fields (`worktree_base_commit` on messages, `content_hash`/`byte_count` on artifacts) recorded on write paths, one pure classifier module (`review_digest.py`, zero IO) turning recorded-vs-current into a closed three-state, and one CLI-side gate helper that withholds only the *automatic* merge. Git resolution stays in `cli.py` (the store never shells out); the store never learns about git.

**Tech Stack:** Python 3.12 stdlib only (`hashlib`, `subprocess`), pytest, conda env `agentdeck`.

**Spec:** `docs/superpowers/specs/2026-08-03-review-digest-binding-design.md`

**Run every test with:** `conda run -n agentdeck pytest ... -q`

---

## Deliberate deviation from the spec (record it, don't silently differ)

The spec says staleness projects into "`plan status` 与 `verdict_summary`". Both
of those are built inside `StateStore`, which **must not shell out to git** —
every other store method is IO-free apart from its own JSON/SQLite. So:

- `plan status` (store) projects only the **recorded** fact
  `worktree_base_commit`. No comparison.
- The live three-state comparison appears as a sibling `review_bindings` block
  added **by the CLI** on the read-only surfaces, using the same helper the
  merge gate uses.

Task 6 updates the spec text to match. Do not "fix" this by giving the store a
git call.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/agentdeck/review_digest.py` | **New.** Pure classifier: recorded commit + currently-resolved commit → closed three-state, plus the blocker sentence. Zero IO, no `cli`/`state`/`config` imports. |
| `src/agentdeck/cli.py` | Base-branch selection fix; git resolution; dispatch records the base commit; merge-gate helper; read-only `review_bindings` block. |
| `src/agentdeck/state.py` | Stores `worktree_base_commit` on messages; computes artifact `content_hash`/`byte_count`; conflict detection. |
| `tests/test_review_binding.py` | **New.** Base selection, recording, gate, read-only projection. |
| `tests/test_review_digest.py` | **New.** Pure-module matrix. |
| `tests/test_artifact_digest.py` | **New.** Artifact hash, idempotent re-registration, conflicting digest fails closed. |

---

### Task 1: Review-group members base on the implementation branch, not on each other

**Files:**
- Modify: `src/agentdeck/cli.py:168` (import), `src/agentdeck/cli.py:11026-11057` (`_plan_base_worktree_branch`)
- Test: `tests/test_review_binding.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_review_binding.py`:

```python
"""Review-step worktree bases and the digest bound to them.

Group members must all review the SAME finished implementation: basing member 2
on member 1's branch leaks member 1's review into member 2's tree (any-fail-blocks
aggregation assumes independent judgements) and would make member 1's own commit
look like the reviewed code drifting once a digest is bound to that base.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore


class _Fake:
    def create_session(self, _config) -> None: ...

    def spawn_agent(self, _config, agent, cwd: str) -> str:
        return "%42"

    def apply_visible_layout(self, _config, panes) -> None: ...

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        return "output\n"

    def send_input(self, _config, pane_id: str, text: str) -> None: ...

    def kill_pane(self, _config, pane_id: str) -> None: ...

    def pane_exists(self, _config, pane_id: str) -> bool:
        return True

    def list_panes(self, _config):
        return []


def _run(argv: list[str]):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(argv)
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text else None)


def _prepare(tmp_path: Path, monkeypatch, review_section: str | None = None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    if review_section:
        path = root / ".agentdeck" / "config.toml"
        path.write_text(path.read_text(encoding="utf-8") + review_section, encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setattr(cli, "TmuxBackend", _Fake)
    return root


def _seed(root: Path, task: str) -> str:
    _, plan = _run(["leader", "plan", "--provider", "fake", "--model", "fake-plan", "--task", task])
    plan_id = plan["plan_id"]
    _run(["approval", "create-from-plan", "--plan-id", plan_id])
    return plan_id


def _branch_for_step(root: Path, plan_id: str, step: int) -> str | None:
    """The branch recorded on the message dispatched for `step`."""
    state = StateStore(root).load()
    approval = next(
        a for a in state["approvals"] if a.get("plan_id") == plan_id and a.get("step") == step
    )
    message = next(
        m for m in state["messages"] if m.get("message_id") == approval.get("message_id")
    )
    return message.get("worktree_branch")


def test_group_members_base_on_the_implementation_branch_not_on_each_other(
    tmp_path: Path, monkeypatch
) -> None:
    root = _prepare(tmp_path, monkeypatch, '\n[review]\nreviewers = ["reviewer", "planner"]\n')
    plan_id = _seed(root, "group base")

    first = cli._plan_base_worktree_branch(StateStore(root), plan_id, 3)
    second = cli._plan_base_worktree_branch(StateStore(root), plan_id, 4)

    # Nothing dispatched yet: both resolve to None, and crucially to the SAME
    # thing -- the group shares one base by construction, not by timing.
    assert first == second
```

That first assertion passes trivially, so add the real one — dispatch steps 1-2
so branches exist, then check both members point at step 2's branch:

```python
def test_dispatched_group_members_share_the_implementation_base(
    tmp_path: Path, monkeypatch
) -> None:
    root = _prepare(tmp_path, monkeypatch, '\n[review]\nreviewers = ["reviewer", "planner"]\n')
    # a real git repo so worktrees actually get created
    cli.subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    cli.subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "base"], cwd=root, check=True)
    for agent_id, pane in (("planner", "%41"), ("coder", "%42"), ("reviewer", "%43")):
        store = StateStore(root)
        state = store.load()
        state["agents"][agent_id] = {
            "agent_id": agent_id, "pane_id": pane, "session_name": "agentdeck",
            "cwd": str(root), "status": "running",
        }
        store.save(state)
    plan_id = _seed(root, "group base dispatch")
    state = StateStore(root).load()
    for approval in state["approvals"]:
        if approval.get("plan_id") == plan_id and approval.get("step") in (1, 2):
            approval["status"] = "approved"
    StateStore(root).save(state)
    for step in (1, 2):
        approval_id = next(
            a["approval_id"] for a in StateStore(root).load()["approvals"]
            if a.get("plan_id") == plan_id and a.get("step") == step
        )
        _run(["approval", "dispatch", "--approval-id", approval_id])

    implementation = _branch_for_step(root, plan_id, 2)
    assert implementation is not None

    assert cli._plan_base_worktree_branch(StateStore(root), plan_id, 3) == implementation
    assert cli._plan_base_worktree_branch(StateStore(root), plan_id, 4) == implementation
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_review_binding.py -q`

Expected: `test_dispatched_group_members_share_the_implementation_base` FAILS —
step 4 resolves to step 3's branch (`agentdeck/reviewer/msg_...`), not step 2's.

If the *worktree* was not created (branch is `None`), the repo isn't a real git
repo — fix the fixture, not the assertion.

- [ ] **Step 3: Add the import**

In `src/agentdeck/cli.py:168`, change:

```python
from .review_group import expand_review_group
```

to:

```python
from .review_group import expand_review_group, review_group_numbers
```

- [ ] **Step 4: Implement the sibling skip**

Replace `_plan_base_worktree_branch` (`src/agentdeck/cli.py:11026`) with:

```python
def _plan_base_worktree_branch(store: StateStore, plan_id: object, step: object) -> str | None:
    """Latest earlier-step worktree branch of the same plan (decision D).

    Lets a review-step worktree check out the implementing step's branch so the
    reviewer can run the real artifact without touching the coder's directory.

    A review-group member skips its own siblings: every member reviews the same
    finished implementation. Basing member 2 on member 1's branch would leak
    member 1's review into member 2's tree -- and `any_fail_blocks` aggregation
    is only meaningful over independent judgements -- and, once a digest is
    bound to that base, member 1's own commit would read as the reviewed code
    drifting. Plans without `[review].reviewers` carry no group markers, so this
    resolves to the old behaviour byte for byte.
    """
    if not plan_id:
        return None
    try:
        current_step = int(step or 0)
    except (TypeError, ValueError):
        return None
    groups = review_group_numbers(_plan_body_steps(store, str(plan_id)))
    current_group = groups.get(current_step)
    siblings = (
        {number for number, group in groups.items() if group == current_group}
        if current_group is not None
        else set()
    )
    state = store.load()
    messages_by_id = {
        str(message.get("message_id")): message
        for message in state.get("messages", [])
        if isinstance(message, dict)
    }
    best: tuple[int, str] | None = None
    for approval in state.get("approvals", []):
        if not isinstance(approval, dict) or approval.get("plan_id") != plan_id:
            continue
        try:
            approval_step = int(approval.get("step") or 0)
        except (TypeError, ValueError):
            continue
        if approval_step >= current_step or approval_step in siblings:
            continue
        message = messages_by_id.get(str(approval.get("message_id")))
        branch = (message or {}).get("worktree_branch")
        if branch and (best is None or approval_step > best[0]):
            best = (approval_step, str(branch))
    return best[1] if best else None
```

- [ ] **Step 5: Run the new tests**

Run: `conda run -n agentdeck pytest tests/test_review_binding.py -q`
Expected: PASS

- [ ] **Step 6: Run the zero-behaviour-change pin**

Run: `conda run -n agentdeck pytest tests/test_step_dag_wave.py -q && conda run -n agentdeck pytest -k worktree -q`

Expected: PASS. The linear differential golden must be untouched — a plan
without `[review]` has no group markers, so `siblings` is always empty.

- [ ] **Step 7: Run the full suite**

Run: `conda run -n agentdeck pytest -q`
Expected: all pass (5204 + the new ones).

- [ ] **Step 8: Add the HISTORY entry**

Prepend under `## 2026-08-03` in `HISTORY.md`:

```markdown
### Base review-group members on the implementation branch

- **Type**: fix
- **Motivation**: digest 绑定的前置。组成员 2 今天基于成员 1 的分支——
  一旦把 digest 绑到 base，成员 1 提交自己的审查文档就会被读成“被审代码
  漂移”，每个并行组 plan 都会被扣住自动合并。
- **What**: `_plan_base_worktree_branch` 跳过同组兄弟，取组前最近的
  非组成员 step 分支。
- **Impact**: **顺带修掉一个既有缺陷**——成员 2 原本看得见成员 1 的审查
  意见，而 `any_fail_blocks` 聚合正以独立判断为前提。无 `[review]` 的
  项目没有组标记，逐字节不变。
- **Verification**: 新增两名组员共享实现分支的断言；DAG 差分金样与
  worktree 回归全绿；全量 pytest。
```

- [ ] **Step 9: Commit**

```bash
git add src/agentdeck/cli.py tests/test_review_binding.py HISTORY.md
git commit -m "fix: base review-group members on the implementation branch"
```

---

### Task 2: Record the reviewed commit at dispatch

**Files:**
- Modify: `src/agentdeck/state.py:10438-10463` (`create_dispatch_records`)
- Modify: `src/agentdeck/cli.py:11060` (new helper above `_create_task_worktree`), `src/agentdeck/cli.py:20341-20371` (dispatch site)
- Test: `tests/test_review_binding.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_binding.py`:

```python
def _head_commit(cwd: Path, ref: str = "HEAD") -> str:
    done = cli.subprocess.run(
        ["git", "rev-parse", ref], cwd=cwd, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def test_dispatch_records_the_commit_the_worktree_was_created_from(
    tmp_path: Path, monkeypatch
) -> None:
    root = _prepare(tmp_path, monkeypatch)
    cli.subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    cli.subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "base"], cwd=root, check=True)
    store = StateStore(root)
    state = store.load()
    for agent_id, pane in (("planner", "%41"), ("coder", "%42")):
        state["agents"][agent_id] = {
            "agent_id": agent_id, "pane_id": pane, "session_name": "agentdeck",
            "cwd": str(root), "status": "running",
        }
    store.save(state)
    plan_id = _seed(root, "record base commit")

    state = StateStore(root).load()
    for approval in state["approvals"]:
        if approval.get("plan_id") == plan_id and approval.get("step") in (1, 2):
            approval["status"] = "approved"
    StateStore(root).save(state)
    for step in (1, 2):
        approval_id = next(
            a["approval_id"] for a in StateStore(root).load()["approvals"]
            if a.get("plan_id") == plan_id and a.get("step") == step
        )
        _run(["approval", "dispatch", "--approval-id", approval_id])

    state = StateStore(root).load()
    approval = next(
        a for a in state["approvals"] if a.get("plan_id") == plan_id and a.get("step") == 2
    )
    message = next(
        m for m in state["messages"] if m.get("message_id") == approval.get("message_id")
    )
    base_branch = message["worktree_base_branch"]
    assert base_branch, "step 2 should be based on step 1's branch"
    # The recorded commit is exactly what that base branch pointed at.
    assert message["worktree_base_commit"] == _head_commit(root, base_branch)


def test_a_step_without_a_base_records_no_commit(tmp_path: Path, monkeypatch) -> None:
    root = _prepare(tmp_path, monkeypatch)
    cli.subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    cli.subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "--allow-empty", "-m", "base"], cwd=root, check=True)
    store = StateStore(root)
    state = store.load()
    state["agents"]["planner"] = {
        "agent_id": "planner", "pane_id": "%41", "session_name": "agentdeck",
        "cwd": str(root), "status": "running",
    }
    store.save(state)
    plan_id = _seed(root, "no base")
    state = StateStore(root).load()
    approval = next(
        a for a in state["approvals"] if a.get("plan_id") == plan_id and a.get("step") == 1
    )
    approval["status"] = "approved"
    StateStore(root).save(state)
    _run(["approval", "dispatch", "--approval-id", approval["approval_id"]])

    state = StateStore(root).load()
    message = next(
        m for m in state["messages"]
        if m.get("message_id") == next(
            a["message_id"] for a in state["approvals"]
            if a.get("approval_id") == approval["approval_id"]
        )
    )
    assert message["worktree_base_branch"] is None
    assert message["worktree_base_commit"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n agentdeck pytest tests/test_review_binding.py -q`
Expected: FAIL with `KeyError: 'worktree_base_commit'`.

- [ ] **Step 3: Store the field**

In `src/agentdeck/state.py`, add the parameter to `create_dispatch_records`
(after `worktree_base_branch`, line 10449):

```python
        worktree_base_branch: str | None = None,
        worktree_base_commit: str | None = None,
```

and the key to the `message` dict (after `"worktree_base_branch"`, line 10462):

```python
            "worktree_base_branch": worktree_base_branch,
            # The commit the worktree was created from. Provably what the
            # reviewer's tree contains -- `git worktree add` checks out this
            # ref. Provenance only: it authorizes nothing.
            "worktree_base_commit": worktree_base_commit,
```

- [ ] **Step 4: Resolve the commit in the CLI**

In `src/agentdeck/cli.py`, insert directly above `_create_task_worktree`
(line 11060):

```python
def _resolve_git_commit(root: object, ref: str | None) -> str | None:
    """`git rev-parse <ref>` in the project root, or None when it cannot be read.

    None means "could not resolve", never "unchanged" -- callers must keep the
    two apart.
    """
    if not ref:
        return None
    done = subprocess.run(
        ["git", "rev-parse", str(ref)],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        return None
    resolved = done.stdout.strip()
    return resolved or None
```

- [ ] **Step 5: Record it at dispatch**

In `src/agentdeck/cli.py`, in the `store.create_dispatch_records(...)` call
(line ~20363), add the argument after `worktree_base_branch`:

```python
        worktree_base_branch=base_branch if worktree_info else None,
        worktree_base_commit=(
            _resolve_git_commit(config.root, base_branch) if worktree_info else None
        ),
```

- [ ] **Step 6: Run the tests**

Run: `conda run -n agentdeck pytest tests/test_review_binding.py -q`
Expected: PASS

- [ ] **Step 7: Run the full suite**

Run: `conda run -n agentdeck pytest -q`
Expected: all pass.

- [ ] **Step 8: HISTORY + commit**

Prepend under `## 2026-08-03`:

```markdown
### Record the commit a review worktree was created from

- **Type**: feat
- **Motivation**: verdict 与被判定物之间今天没有任何绑定。
- **What**: dispatch 建 worktree 后 `git rev-parse <base_branch>`，把
  `worktree_base_commit` 记在 message 上。捕获点是**派发时**——worktree
  正是从该 ref 检出的，所以这是 reviewer 目光所及那棵树的可证指纹；收
  verdict 时再解析会把他从未看过的提交当成“他审过的”。
- **Impact**: 纯 provenance，不设门、不改任何 gate。
- **Verification**: 记录值等于 base 分支当时的 tip；无 base 的步记 null。
```

```bash
git add src/agentdeck/state.py src/agentdeck/cli.py tests/test_review_binding.py HISTORY.md
git commit -m "feat: record the commit a review worktree was created from"
```

---

### Task 3: Pure three-state classifier

**Files:**
- Create: `src/agentdeck/review_digest.py`
- Test: `tests/test_review_digest.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_review_digest.py`:

```python
"""Pure classification of a bound review commit against the branch today."""
from __future__ import annotations

from pathlib import Path

from agentdeck.review_digest import (
    REVIEW_DIGEST_STATES,
    UNVERIFIABLE_REASONS,
    classify_review_binding,
    summarize_review_bindings,
)


def test_module_is_pure() -> None:
    text = Path("src/agentdeck/review_digest.py").read_text(encoding="utf-8")
    for forbidden in ("import subprocess", "from .cli", "from .state", "from .config", "open("):
        assert forbidden not in text


def test_same_commit_is_a_match() -> None:
    assert classify_review_binding("abc123", "abc123") == {"state": "match", "reason": None}


def test_moved_branch_is_drift() -> None:
    assert classify_review_binding("abc123", "def456") == {"state": "drift", "reason": None}


def test_unrecorded_commit_is_unverifiable_not_a_match() -> None:
    assert classify_review_binding(None, "def456") == {
        "state": "unverifiable", "reason": "not_recorded",
    }


def test_unresolvable_branch_is_unverifiable() -> None:
    assert classify_review_binding("abc123", None) == {
        "state": "unverifiable", "reason": "branch_missing",
    }


def test_missing_repository_beats_the_other_reasons() -> None:
    assert classify_review_binding("abc123", None, git_available=False) == {
        "state": "unverifiable", "reason": "no_git_repo",
    }


def test_states_and_reasons_are_closed() -> None:
    assert REVIEW_DIGEST_STATES == ("match", "drift", "unverifiable")
    assert UNVERIFIABLE_REASONS == ("not_recorded", "branch_missing", "no_git_repo")


def _item(**kw):
    base = {
        "message_id": "msg_1", "agent_id": "reviewer", "step": 3,
        "base_branch": "agentdeck/coder/msg_0", "base_commit": "abc123",
        "resolved_commit": "abc123",
    }
    base.update(kw)
    return base


def test_summary_counts_and_stays_silent_when_everything_matches() -> None:
    summary = summarize_review_bindings([_item(), _item(message_id="msg_2", step=4)])
    assert summary["count"] == 2
    assert summary["match"] == 2
    assert summary["drift"] == 0
    assert summary["unverifiable"] == 0
    assert summary["blocker"] is None
    assert [b["state"] for b in summary["bindings"]] == ["match", "match"]


def test_summary_blocks_on_drift_and_names_both_commits() -> None:
    summary = summarize_review_bindings([_item(resolved_commit="def4567890")])
    assert summary["drift"] == 1
    assert "agentdeck/coder/msg_0" in summary["blocker"]
    assert "abc123" in summary["blocker"]
    assert "def4567" in summary["blocker"]
    assert "auto-merge withheld" in summary["blocker"]


def test_summary_blocks_when_a_recorded_binding_cannot_be_verified() -> None:
    summary = summarize_review_bindings([_item(resolved_commit=None)])
    assert summary["unverifiable"] == 1
    assert "cannot verify" in summary["blocker"]


def test_summary_does_not_block_on_plans_that_predate_the_binding() -> None:
    # An unrecorded binding is the ONE deliberate fail-open: blocking it would
    # withhold every in-flight plan. It must still read as "not recorded".
    summary = summarize_review_bindings([_item(base_commit=None, resolved_commit="def456")])
    assert summary["unverifiable"] == 1
    assert summary["blocker"] is None
    assert summary["bindings"][0]["reason"] == "not_recorded"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_review_digest.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentdeck.review_digest'`

- [ ] **Step 3: Write the module**

Create `src/agentdeck/review_digest.py`:

```python
"""Classify a bound review commit against what that branch points at today.

A verdict is a judgement about a specific tree. The merge gate has always asked
what the verdict *said* and never what it was *about*, so code that changed
after it was reviewed merged on the strength of a stale pass.

Three states, deliberately not two:

| state | meaning |
| --- | --- |
| `match` | the reviewed commit is still where that branch points |
| `drift` | the branch moved -- reviewed, then changed |
| `unverifiable` | we could not check: nothing recorded, branch gone, no repo |

`unverifiable` must never render as "verified". `drift: false` and
`verified: false` both read like "fine", which is exactly the class of untrue
statement this repository keeps having to fix.

Pure module: zero IO, zero LLM, no git, no tmux; it does not import `cli`,
`state` or `config`. Git resolution happens in the caller and arrives here as
already-resolved values. Nothing here is an authorization -- it only ever
withholds the *automatic* merge, never the human's explicit one.

See docs/superpowers/specs/2026-08-03-review-digest-binding-design.md.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

REVIEW_DIGEST_STATES = ("match", "drift", "unverifiable")
UNVERIFIABLE_REASONS = ("not_recorded", "branch_missing", "no_git_repo")


def _short(commit: object) -> str:
    text = str(commit or "")
    return text[:7] if text else "?"


def classify_review_binding(
    base_commit: str | None,
    resolved_commit: str | None,
    *,
    git_available: bool = True,
) -> dict[str, Any]:
    """One binding's state. `resolved_commit` is what the branch points at now."""
    if not git_available:
        return {"state": "unverifiable", "reason": "no_git_repo"}
    if not base_commit:
        return {"state": "unverifiable", "reason": "not_recorded"}
    if not resolved_commit:
        return {"state": "unverifiable", "reason": "branch_missing"}
    if base_commit == resolved_commit:
        return {"state": "match", "reason": None}
    return {"state": "drift", "reason": None}


def summarize_review_bindings(
    items: Iterable[Mapping[str, Any]], *, git_available: bool = True
) -> dict[str, Any]:
    """Classify every binding and derive the auto-merge blocker, if any.

    Each item carries `message_id`, `agent_id`, `step`, `base_branch`,
    `base_commit` and `resolved_commit`.

    A blocker is raised for `drift` and for a recorded binding we cannot verify.
    It is deliberately NOT raised for `not_recorded`: plans created before this
    feature carry no commit, and blocking them would withhold every in-flight
    plan. That single fail-open is documented in the spec and in the contract.
    """
    bindings: list[dict[str, Any]] = []
    counts = {state: 0 for state in REVIEW_DIGEST_STATES}
    drifted: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    for item in items:
        verdict = classify_review_binding(
            item.get("base_commit"),
            item.get("resolved_commit"),
            git_available=git_available,
        )
        binding = {
            "message_id": item.get("message_id"),
            "agent_id": item.get("agent_id"),
            "step": item.get("step"),
            "base_branch": item.get("base_branch"),
            "base_commit": item.get("base_commit"),
            "current_commit": item.get("resolved_commit"),
            "state": verdict["state"],
            "reason": verdict["reason"],
        }
        counts[verdict["state"]] += 1
        if verdict["state"] == "drift":
            drifted.append(binding)
        elif verdict["state"] == "unverifiable" and verdict["reason"] != "not_recorded":
            unverified.append(binding)
        bindings.append(binding)

    blocker: str | None = None
    if drifted:
        first = drifted[0]
        blocker = (
            f"review verdict is bound to {first['base_branch']}@{_short(first['base_commit'])} "
            f"but that branch is now at {_short(first['current_commit'])}; auto-merge withheld"
        )
    elif unverified:
        first = unverified[0]
        blocker = (
            f"cannot verify the reviewed state of {first['base_branch']} "
            f"({first['reason']}); auto-merge withheld"
        )

    return {
        "count": len(bindings),
        "match": counts["match"],
        "drift": counts["drift"],
        "unverifiable": counts["unverifiable"],
        "bindings": bindings,
        "blocker": blocker,
    }
```

- [ ] **Step 4: Run the tests**

Run: `conda run -n agentdeck pytest tests/test_review_digest.py -q`
Expected: PASS

- [ ] **Step 5: HISTORY + commit**

Prepend under `## 2026-08-03`:

```markdown
### Classify a bound review commit in a pure module

- **Type**: feat
- **Motivation**: 比对必须是确定性的、可单测的，且**三态**——不能把
  “核不了”折叠成“没问题”。
- **What**: 新纯模块 `review_digest.py`（零 IO、不 import cli/state/config）：
  `classify_review_binding` 出 `match`/`drift`/`unverifiable`（闭合原因
  `not_recorded`/`branch_missing`/`no_git_repo`），`summarize_review_bindings`
  聚合并给出 blocker 句子。git 解析留在调用方。
- **Impact**: 尚未接线，行为零变化。
- **Verification**: 纯度断言 + 三态矩阵 + 唯一一处刻意 fail-open
  （`not_recorded` 不阻断）由测试点名。
```

```bash
git add src/agentdeck/review_digest.py tests/test_review_digest.py HISTORY.md
git commit -m "feat: classify a bound review commit in a pure module"
```

---

### Task 4: Withhold the automatic merge on drift

**Files:**
- Modify: `src/agentdeck/cli.py` (new `_plan_review_bindings` + `_stale_review_merge_blocker` next to `_verdict_merge_blocker` at line 12721; both merge sites at 21845-21860 and 22893-22906)
- Test: `tests/test_review_binding.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_binding.py`:

```python
def _drive_plan_to_complete_with_drift(tmp_path: Path, monkeypatch) -> tuple[Path, str, str]:
    """Run a 2-step plan to complete, then push one more commit onto the
    reviewed branch. Returns (root, plan_id, reviewed_branch)."""
    root = _prepare(tmp_path, monkeypatch)
    git = ["-c", "user.email=t@t", "-c", "user.name=t"]
    cli.subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    cli.subprocess.run(["git", *git, "commit", "-q", "--allow-empty", "-m", "base"],
                       cwd=root, check=True)
    store = StateStore(root)
    state = store.load()
    for agent_id, pane in (("planner", "%41"), ("coder", "%42"), ("reviewer", "%43")):
        state["agents"][agent_id] = {
            "agent_id": agent_id, "pane_id": pane, "session_name": "agentdeck",
            "cwd": str(root), "status": "running",
        }
    store.save(state)
    plan_id = _seed(root, "drift gate")
    state = StateStore(root).load()
    for approval in state["approvals"]:
        if approval.get("plan_id") == plan_id:
            approval["status"] = "approved"
    StateStore(root).save(state)

    reviewed_branch = None
    for step in (1, 2, 3):
        approval_id = next(
            a["approval_id"] for a in StateStore(root).load()["approvals"]
            if a.get("plan_id") == plan_id and a.get("step") == step
        )
        _run(["approval", "dispatch", "--approval-id", approval_id])
        message_id = next(
            a["message_id"] for a in StateStore(root).load()["approvals"]
            if a.get("approval_id") == approval_id
        )
        verdict = ""
        if step == 3:
            reviewed_branch = _branch_for_step(root, plan_id, 2)
            verdict = (
                'verdict: {"schema_version": "review-verdict/v1", "criteria": [], '
                '"overall": "pass", "score": 90}\n'
            )
        _run(["reply", "--agent", "planner" if step == 1 else ("coder" if step == 2 else "reviewer"),
              "--message-id", message_id, "--text", f"status: completed\nsummary: done\n{verdict}"])

    # the reviewed branch gains a commit AFTER the verdict
    worktree = root / ".agentdeck" / "worktrees" / "coder"
    target = next(worktree.iterdir())
    (target / "late.txt").write_text("changed after review\n", encoding="utf-8")
    cli.subprocess.run(["git", *git, "add", "late.txt"], cwd=target, check=True)
    cli.subprocess.run(["git", *git, "commit", "-q", "-m", "after review"], cwd=target, check=True)
    return root, plan_id, str(reviewed_branch)


def test_drift_withholds_the_automatic_merge(tmp_path: Path, monkeypatch) -> None:
    root, plan_id, reviewed_branch = _drive_plan_to_complete_with_drift(tmp_path, monkeypatch)
    config = cli.load_config(root)
    store = StateStore(root)

    blocker = cli._stale_review_merge_blocker(config, store, plan_id)

    assert blocker is not None
    assert reviewed_branch in blocker
    assert "auto-merge withheld" in blocker


def test_no_drift_leaves_the_automatic_merge_alone(tmp_path: Path, monkeypatch) -> None:
    root, plan_id, _ = _drive_plan_to_complete_with_drift(tmp_path, monkeypatch)
    # undo the drift: point the reviewed branch back at the reviewed commit
    store = StateStore(root)
    state = store.load()
    approval = next(
        a for a in state["approvals"] if a.get("plan_id") == plan_id and a.get("step") == 3
    )
    message = next(
        m for m in state["messages"] if m.get("message_id") == approval.get("message_id")
    )
    cli.subprocess.run(
        ["git", "update-ref", f"refs/heads/{message['worktree_base_branch']}",
         message["worktree_base_commit"]],
        cwd=root, check=True,
    )

    assert cli._stale_review_merge_blocker(cli.load_config(root), StateStore(root), plan_id) is None


def test_the_human_merge_command_is_never_gated(tmp_path: Path, monkeypatch) -> None:
    root, plan_id, _ = _drive_plan_to_complete_with_drift(tmp_path, monkeypatch)
    code, payload = _run(["worktree", "merge-plan", "--plan-id", plan_id, "--confirm"])
    # The explicit human command runs regardless of staleness. It may fail or
    # skip for ordinary git/gate reasons -- what it must NEVER do is refuse with
    # the staleness blocker, so assert on the absence of that, not on success.
    assert "auto-merge withheld" not in json.dumps(payload or {})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n agentdeck pytest tests/test_review_binding.py -q`
Expected: FAIL with `AttributeError: module 'agentdeck.cli' has no attribute '_stale_review_merge_blocker'`

- [ ] **Step 3: Implement the gate helper**

In `src/agentdeck/cli.py`, insert immediately above `_verdict_merge_blocker`
(line 12721):

```python
def _plan_review_bindings(
    config: ProjectConfig, store: StateStore, plan_id: str
) -> dict[str, object]:
    """Every verdict-bearing step of this plan, bound to the commit it reviewed.

    Git resolution lives here, never in the store. Read-only: it shells out to
    `git rev-parse` and nothing else.
    """
    state = store.load()
    replies_with_verdict = {
        str(reply.get("message_id"))
        for reply in state.get("replies", [])
        if isinstance(reply, dict) and reply.get("verdict")
    }
    messages_by_id = {
        str(message.get("message_id")): message
        for message in state.get("messages", [])
        if isinstance(message, dict)
    }
    git_available = _resolve_git_commit(config.root, "HEAD") is not None
    resolved: dict[str, str | None] = {}
    items: list[dict[str, object]] = []
    for approval in state.get("approvals", []):
        if not isinstance(approval, dict) or approval.get("plan_id") != plan_id:
            continue
        message_id = str(approval.get("message_id") or "")
        if message_id not in replies_with_verdict:
            continue
        message = messages_by_id.get(message_id)
        if message is None or not message.get("worktree_base_branch"):
            continue
        branch = str(message["worktree_base_branch"])
        if branch not in resolved:
            resolved[branch] = _resolve_git_commit(config.root, branch)
        items.append({
            "message_id": message_id,
            "agent_id": approval.get("agent_id"),
            "step": approval.get("step"),
            "base_branch": branch,
            "base_commit": message.get("worktree_base_commit"),
            "resolved_commit": resolved[branch],
        })
    items.sort(key=lambda item: int(item.get("step") or 0))
    return summarize_review_bindings(items, git_available=git_available)


def _stale_review_merge_blocker(
    config: ProjectConfig, store: StateStore, plan_id: str
) -> str | None:
    """Withhold the AUTOMATIC merge when the reviewed code has moved.

    Explicit `worktree merge-plan --confirm` is never gated -- same rule as the
    G5 verdict gate.
    """
    return _plan_review_bindings(config, store, plan_id).get("blocker")  # type: ignore[return-value]
```

Add the import at `src/agentdeck/cli.py:168` area:

```python
from .review_digest import summarize_review_bindings
```

- [ ] **Step 4: Wire the host merge site**

In `src/agentdeck/cli.py:21850`, replace:

```python
        blocker = _verdict_merge_blocker(store, plan_id)
        if blocker:
            append_host_log(root, {
                "plan_id": plan_id, "event": "plan_merge", "mode": "verdict_blocked",
                "blocker": blocker, "wave": wave_count, "at": utc_now(),
            })
```

with:

```python
        blocker = _verdict_merge_blocker(store, plan_id)
        mode = "verdict_blocked"
        if not blocker:
            blocker = _stale_review_merge_blocker(config, store, plan_id)
            mode = "review_stale"
        if blocker:
            append_host_log(root, {
                "plan_id": plan_id, "event": "plan_merge", "mode": mode,
                "blocker": blocker, "wave": wave_count, "at": utc_now(),
            })
```

- [ ] **Step 5: Wire the follow merge site**

In `src/agentdeck/cli.py:22894`, replace:

```python
        verdict_blocker = _verdict_merge_blocker(store, plan_id)
        if verdict_blocker:
```

with:

```python
        verdict_blocker = _verdict_merge_blocker(store, plan_id)
        merge_block_mode = "verdict_blocked"
        if not verdict_blocker:
            verdict_blocker = _stale_review_merge_blocker(config, store, plan_id)
            merge_block_mode = "review_stale"
        if verdict_blocker:
```

and in the payload dict just below it change the literal:

```python
                "mode": merge_block_mode,
```

- [ ] **Step 6: Run the tests**

Run: `conda run -n agentdeck pytest tests/test_review_binding.py -q`
Expected: PASS

- [ ] **Step 7: Mutation-verify the gate**

Temporarily change `classify_review_binding` so drift returns `match`:

```python
    if base_commit == resolved_commit:
        return {"state": "match", "reason": None}
    return {"state": "match", "reason": None}   # MUTANT
```

Run: `conda run -n agentdeck pytest tests/test_review_binding.py::test_drift_withholds_the_automatic_merge -q`
Expected: **FAIL**. If it passes, the test is not exercising the gate — fix the
test, then restore the module before continuing.

Restore: `git checkout src/agentdeck/review_digest.py`

- [ ] **Step 8: Run the full suite**

Run: `conda run -n agentdeck pytest -q`
Expected: all pass.

- [ ] **Step 9: HISTORY + commit**

Prepend under `## 2026-08-03`:

```markdown
### Withhold the automatic merge when reviewed code has moved

- **Type**: feat
- **Motivation**: verdict 是自动合并的唯一放行依据，而它与被放行内容之间
  原本没有绑定——“审查之后又被改动”发现不了。
- **What**: `_plan_review_bindings` 在 CLI 侧解析 git（store 永不 shell out），
  `_stale_review_merge_blocker` 在两处自动合并站点接线，扣住时
  `plan_merge.mode=review_stale` 并交回显式人类命令。
- **Impact**: **只扣自动路径**；人类 `worktree merge-plan --confirm` 永不受
  gate（与 G5 verdict gate 同一规则）。老 plan 无记录不阻断（唯一刻意
  fail-open，spec 与契约都写明理由）。
- **Verification**: 走完整环后往被审分支追加提交 → 出 blocker 且点名分支；
  回退 ref → 无 blocker；人类 merge 命令不含扣留话术。**变异验证**：把
  drift 判定削成恒 match，drift 测试必须红。
```

```bash
git add src/agentdeck/cli.py tests/test_review_binding.py HISTORY.md
git commit -m "feat: withhold the automatic merge when reviewed code has moved"
```

---

### Task 5: Artifact content hashes, idempotent and fail-closed on conflict

**Files:**
- Modify: `src/agentdeck/state.py:10551` (call site), `src/agentdeck/state.py:10603-10621` (`_artifacts_from_reply`)
- Test: `tests/test_artifact_digest.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_artifact_digest.py`:

```python
"""Artifact content digests: the capability CCB already has.

CCB's kernel records digest/actor/job/timestamp when it commits an artifact,
makes re-import idempotent for the same digest, and FAILS CLOSED on a
conflicting one. AgentDeck recorded only path/kind/status, so evidence could be
rewritten afterwards and nothing noticed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from agentdeck.state import StateStore


def _store(tmp_path: Path) -> StateStore:
    root = tmp_path / "repo"
    (root / ".agentdeck" / "state").mkdir(parents=True)
    return StateStore(root)


def _dispatch(store: StateStore) -> str:
    records = store.create_dispatch_records(
        "leader", "coder", "do the thing", "prompt", "%1"
    )
    return str(records["message"]["message_id"])


def _reply_text(path: Path) -> str:
    return f"status: completed\nsummary: done\nfull_output_path: {path}\n"


def test_artifact_records_content_hash_and_byte_count(tmp_path: Path) -> None:
    store = _store(tmp_path)
    message_id = _dispatch(store)
    artifact = tmp_path / "out.md"
    artifact.write_text("hello evidence\n", encoding="utf-8")

    result = store.record_reply("coder", message_id, _reply_text(artifact))

    recorded = result["artifacts"][0]
    assert recorded["digest_status"] == "recorded"
    assert recorded["content_hash"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert recorded["byte_count"] == artifact.stat().st_size


def test_missing_file_is_recorded_as_missing_not_as_hashed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    message_id = _dispatch(store)

    result = store.record_reply("coder", message_id, _reply_text(tmp_path / "absent.md"))

    recorded = result["artifacts"][0]
    assert recorded["digest_status"] == "file_missing"
    assert recorded["content_hash"] is None
    assert recorded["byte_count"] is None


def test_reregistering_the_same_content_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    message_id = _dispatch(store)
    artifact = tmp_path / "out.md"
    artifact.write_text("stable\n", encoding="utf-8")

    store.record_reply("coder", message_id, _reply_text(artifact))
    second = store.record_reply("coder", message_id, _reply_text(artifact))

    assert second["artifacts"] == []
    assert second.get("artifact_conflicts") in (None, [])
    assert len([a for a in store.load()["artifacts"] if a["message_id"] == message_id]) == 1


def test_conflicting_content_fails_closed_and_keeps_the_original(tmp_path: Path) -> None:
    store = _store(tmp_path)
    message_id = _dispatch(store)
    artifact = tmp_path / "out.md"
    artifact.write_text("first\n", encoding="utf-8")
    store.record_reply("coder", message_id, _reply_text(artifact))
    original = store.load()["artifacts"][0]["content_hash"]

    artifact.write_text("rewritten after the fact\n", encoding="utf-8")
    second = store.record_reply("coder", message_id, _reply_text(artifact))

    # rejected: not registered, original untouched, conflict named
    assert second["artifacts"] == []
    conflict = second["artifact_conflicts"][0]
    assert conflict["path"] == str(artifact)
    assert conflict["recorded_hash"] == original
    assert conflict["observed_hash"] != original
    stored = [a for a in store.load()["artifacts"] if a["message_id"] == message_id]
    assert len(stored) == 1
    assert stored[0]["content_hash"] == original
    assert any(e["event_type"] == "artifact_digest_conflict" for e in store.list_events(200))
```

(`StateStore.list_events(limit=20)` exists at `state.py:9282`; pass a larger
limit so the conflict event is inside the window.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n agentdeck pytest tests/test_artifact_digest.py -q`
Expected: FAIL with `KeyError: 'digest_status'`

- [ ] **Step 3: Implement the digest**

In `src/agentdeck/state.py`, replace `_artifacts_from_reply`
(line 10603, the whole `@classmethod`) with an instance method:

```python
    def _artifacts_from_reply(
        self, reply: dict[str, Any], text: str, existing: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(new artifacts, conflicts).

        The digest is computed here, on the WRITE path -- the read-only surfaces
        (`agentdeck artifacts`, `artifacts_card`, trace) still never open an
        artifact file.

        Re-registering the same (message_id, path) with the same content is
        idempotent. A conflicting digest FAILS CLOSED: the entry is rejected,
        the original record is left untouched, and the conflict is named. The
        reply itself still records -- a reply is a fact, registering evidence is
        a judgement (same split as an invalid verdict not blocking its reply).
        """
        output_path = self._structured_reply_value(text, "full_output_path")
        if not output_path:
            return [], []
        message_id = reply.get("message_id")
        content_hash, byte_count, digest_status = self._artifact_digest(output_path)
        previous = next(
            (
                item
                for item in existing
                if item.get("message_id") == message_id and item.get("path") == output_path
            ),
            None,
        )
        if previous is not None:
            if previous.get("content_hash") == content_hash:
                return [], []
            return [], [{
                "message_id": message_id,
                "path": output_path,
                "recorded_hash": previous.get("content_hash"),
                "observed_hash": content_hash,
                "artifact_id": previous.get("artifact_id"),
            }]
        return [
            {
                "artifact_id": new_id("art"),
                "message_id": message_id,
                "attempt_id": reply.get("attempt_id"),
                "job_id": reply.get("job_id"),
                "reply_id": reply.get("reply_id"),
                "from_agent": reply.get("from_agent"),
                "path": output_path,
                "kind": self._artifact_kind(output_path),
                "status": "created",
                "content_hash": content_hash,
                "byte_count": byte_count,
                "digest_status": digest_status,
                "created_at": utc_now(),
            }
        ], []

    @staticmethod
    def _artifact_digest(path: str) -> tuple[str | None, int | None, str]:
        """(sha256, byte count, closed status). Never reports "hashed" on failure."""
        try:
            data = Path(path).read_bytes()
        except FileNotFoundError:
            return None, None, "file_missing"
        except OSError:
            return None, None, "read_failed"
        return hashlib.sha256(data).hexdigest(), len(data), "recorded"
```

- [ ] **Step 4: Update the call site**

In `src/agentdeck/state.py:10551`, replace:

```python
        artifacts = self._artifacts_from_reply(reply, text)
        state.setdefault("artifacts", []).extend(artifacts)
```

with:

```python
        artifacts, artifact_conflicts = self._artifacts_from_reply(
            reply, text, state.setdefault("artifacts", [])
        )
        state["artifacts"].extend(artifacts)
```

and at the `return` of `record_reply` (line ~10601), replace:

```python
        return {**reply, "artifacts": artifacts}
```

with:

```python
        for conflict in artifact_conflicts:
            self.append_event(EventRecord.create("artifact_digest_conflict", dict(conflict)))
        result = {**reply, "artifacts": artifacts}
        if artifact_conflicts:
            result["artifact_conflicts"] = artifact_conflicts
        return result
```

`Path` is already imported (`state.py:12`) and `hashlib` at `state.py:9` — no
new imports needed.

The conflict events go **after** `self.save(state)`, exactly where the
neighbouring `review_verdict_recorded` / `review_verdict_invalid` events sit
(`state.py:10575-10600`). The `return` shown above is already past the save, so
placing them there is correct — do not move them inside the mutation.

- [ ] **Step 5: Run the tests**

Run: `conda run -n agentdeck pytest tests/test_artifact_digest.py -q`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `conda run -n agentdeck pytest -q`
Expected: all pass. Existing tests that assert on artifact dicts may need the
three new keys — update those assertions to include the new keys rather than
weakening them to subset checks.

- [ ] **Step 7: HISTORY + commit**

Prepend under `## 2026-08-03`:

```markdown
### Record artifact content digests, idempotent and fail-closed

- **Type**: feat
- **Motivation**: 容纳 CCB 已有的能力。AgentDeck 的 artifact 只记
  path/kind/status/时间戳——作为证据登记的文件事后被改写，系统一无所知。
- **What**: 登记时记 `content_hash`(sha256)/`byte_count`/闭合
  `digest_status`(`recorded`/`file_missing`/`read_failed`)。同
  `(message_id, path)` 重入：digest 相同幂等；**冲突 fail-closed**——该条
  拒绝入账、原记录不动、点名两个 digest 并记 `artifact_digest_conflict`。
- **Impact**: 计算只在**写路径**；只读面仍一字不读产物文件。拒绝粒度是
  artifact 条目**不是整条 reply**（沿用“无效 verdict 不阻断 reply”的先例）。
- **Verification**: hash/字节数对得上；缺文件记 `file_missing` 而非“算过了”；
  重入幂等；冲突后原 hash 不变且事件已记。
```

```bash
git add src/agentdeck/state.py tests/test_artifact_digest.py HISTORY.md
git commit -m "feat: record artifact content digests, idempotent and fail-closed"
```

---

### Task 6: Make staleness visible before the merge, and sync the contracts

**Files:**
- Modify: `src/agentdeck/state.py` (plan_status step projection ~9819)
- Modify: `src/agentdeck/cli.py:20068` (`plan_status_command`)
- Modify: `docs/contracts/project-view-schema.md`, `docs/contracts/run-loop-schema.md`, `README.md`, `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-08-03-review-digest-binding-design.md` (record the deviation)
- Test: `tests/test_review_binding.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_binding.py`:

```python
def test_plan_status_projects_the_recorded_binding_and_live_state(
    tmp_path: Path, monkeypatch
) -> None:
    root, plan_id, reviewed_branch = _drive_plan_to_complete_with_drift(tmp_path, monkeypatch)
    state_path = root / ".agentdeck" / "state" / "state.json"
    before = state_path.read_bytes()

    code, payload = _run(["plan", "status", "--plan-id", plan_id])

    assert code == 0
    review_step = next(s for s in payload["steps"] if s["step"] == 3)
    assert review_step["worktree_base_commit"]           # recorded fact
    binding = next(b for b in payload["review_bindings"]["bindings"] if b["step"] == 3)
    assert binding["state"] == "drift"                   # live comparison
    assert binding["base_branch"] == reviewed_branch
    assert payload["review_bindings"]["blocker"] is not None
    assert state_path.read_bytes() == before             # read-only


def test_plan_status_says_not_recorded_rather_than_verified(
    tmp_path: Path, monkeypatch
) -> None:
    root, plan_id, _ = _drive_plan_to_complete_with_drift(tmp_path, monkeypatch)
    # simulate a plan created before this feature: drop the recorded commit
    store = StateStore(root)
    state = store.load()
    for message in state["messages"]:
        message["worktree_base_commit"] = None
    store.save(state)

    _, payload = _run(["plan", "status", "--plan-id", plan_id])

    binding = next(b for b in payload["review_bindings"]["bindings"] if b["step"] == 3)
    assert binding["state"] == "unverifiable"
    assert binding["reason"] == "not_recorded"
    # the one deliberate fail-open: not recorded does not block ...
    assert payload["review_bindings"]["blocker"] is None
    # ... but it must never read as verified
    assert binding["state"] != "match"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `conda run -n agentdeck pytest tests/test_review_binding.py -q`
Expected: FAIL with `KeyError: 'worktree_base_commit'` on the step item.

- [ ] **Step 3: Project the recorded fact in the store**

There is no `_message_by_id` helper. Build the index **once**, next to the
existing `step_dependencies` line (`state.py:9794`), so the step loop stays
O(steps) rather than scanning the message list per step:

```python
        step_dependencies = derive_step_dependencies(steps if isinstance(steps, list) else [])
        messages_by_id = {
            str(message.get("message_id")): message
            for message in state.get("messages", [])
            if isinstance(message, dict)
        }
```

Then inside `plan_status`'s `status_item` dict (after `"depends_on"`,
line ~9822) add:

```python
                    # The commit this step's worktree was created from --
                    # recorded provenance only. The live comparison needs git,
                    # which the store never runs; the CLI adds it alongside.
                    "worktree_base_commit": (
                        messages_by_id.get(str((approval or {}).get("message_id")), {})
                        .get("worktree_base_commit")
                    ),
```

- [ ] **Step 4: Add the live block in the CLI**

Replace `plan_status_command` (`src/agentdeck/cli.py:20068`) with:

```python
def plan_status_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if store is None or config is None:
        return exit_code
    try:
        status = store.plan_status(args.plan_id)
    except KeyError:
        print(f"unknown plan: {args.plan_id}", file=sys.stderr)
        return 1
    # Live staleness sits beside the recorded facts, never inside them: the
    # store owns state, git resolution is ours.
    status["review_bindings"] = _plan_review_bindings(config, store, args.plan_id)
    _print_json(status)
    return 0
```

- [ ] **Step 5: Run the tests**

Run: `conda run -n agentdeck pytest tests/test_review_binding.py -q`
Expected: PASS

- [ ] **Step 6: Sync the contract docs**

In `docs/contracts/project-view-schema.md`, extend the `plan status` step
sentence added by the DAG slice (search for `depends_on`) with:

```markdown
`agentdeck plan status` step items also project `worktree_base_commit`: the commit that step's task worktree was created from, recorded at dispatch. It is a recorded fact only — the live comparison against what the branch points at today needs git, which the store never runs, so `agentdeck plan status` carries it as a sibling `review_bindings` block instead (states `match` / `drift` / `unverifiable`, with closed reasons `not_recorded` / `branch_missing` / `no_git_repo`). `unverifiable` must never be rendered as verified. A binding whose commit was never recorded (a plan created before this feature) is reported as `not_recorded` and deliberately does NOT block the automatic merge — blocking it would withhold every in-flight plan. All of it is provenance: it authorizes no dispatch and changes no approval semantics.
```

In `docs/contracts/run-loop-schema.md`, add to the merge bullet:

```markdown
- Reviewed-state binding: `--merge-on-complete` also withholds the automatic merge when the code a verdict judged has moved. Each verdict-bearing step records the commit its worktree was created from; before merging, the branch is re-resolved and compared. Drift, or a recorded binding that cannot be verified, yields `plan_merge.mode=review_stale` with the explicit `agentdeck worktree merge-plan --plan-id <id> --confirm` handed back. The human's explicit merge command is never gated — same rule as the G5 verdict gate.
```

In `README.md`, after the review-group bullet, add:

```markdown
- Reviewed-state binding: a verdict is a judgement about a specific tree, so
  AgentDeck records the commit each review worktree was created from and, before
  an automatic merge, checks that the branch still points there. Drift withholds
  the automatic merge (`plan_merge.mode=review_stale`) and hands back the
  explicit human merge command, which is never gated. The three states are
  `match` / `drift` / `unverifiable` — "could not check" is never shown as
  "checked". Artifacts likewise carry a `content_hash` and `byte_count` recorded
  at registration; re-registering identical content is idempotent and a
  conflicting digest is rejected rather than silently overwriting the record.
```

In `CLAUDE.md`, add one rule bullet next to the existing worktree/merge rules:

```markdown
- Review verdict 必须绑定被判定的那棵树:review step 派发时记 `worktree_base_commit`(该 worktree 正是从此 ref 检出,故为可证指纹);自动合并前重新解析该分支并比对,`drift` 或"记录了却核不了"必须扣住自动合并并出 `plan_merge.mode=review_stale`,人类 `worktree merge-plan --confirm` 永不受 gate。三态 `match`/`drift`/`unverifiable` 必须各自可见,`unverifiable` 绝不渲染成"已验证";未记录(老 plan)不阻断,这是唯一刻意的 fail-open,必须在契约文档写明理由。git 解析只能在 CLI 侧,`StateStore` 永不 shell out。artifact 登记时必须记 `content_hash`/`byte_count`/闭合 `digest_status`,同 `(message_id, path)` 重入 digest 相同则幂等、冲突必须 fail-closed 拒绝该条并记 `artifact_digest_conflict`,绝不静默覆盖;digest 只在写路径计算,只读面不得读取产物文件内容。
```

- [ ] **Step 7: Record the deviation in the spec**

In `docs/superpowers/specs/2026-08-03-review-digest-binding-design.md`, under
"### 呈现:只读面提前可见", append:

```markdown
**实现偏差(2026-08-03,落地时记录)**:原文写"投影进 `plan status` 与
`verdict_summary`"。二者都建在 `StateStore` 内,而 store **不得 shell out
调 git**(其余每个 store 方法都只碰自己的 JSON/SQLite)。因此:`plan status`
(store 侧)只投影**已记录**的 `worktree_base_commit`;三态**实时比对**由
**CLI 侧**以同级 `review_bindings` 块给出,与 merge gate 共用同一个 helper。
`verdict_summary` 保持不变。不要用"给 store 加一个 git 调用"来消除这条偏差。
```

- [ ] **Step 8: Run the full suite**

Run: `conda run -n agentdeck pytest -q`
Expected: all pass.

- [ ] **Step 9: HISTORY + commit**

Prepend under `## 2026-08-03`:

```markdown
### Surface reviewed-state drift before the merge refuses it

- **Type**: feat
- **Motivation**: 只在自动合并被拒那一刻才知道有漂移，太晚——那已是走开段
  的末尾。
- **What**: `plan status` 的 step 投影已记录的 `worktree_base_commit`
  （store 侧，纯事实）；实时三态由 CLI 侧同级 `review_bindings` 块给出，
  与 merge gate 共用同一 helper。契约/README/CLAUDE.md 同步。
- **Impact**: 只读、零写。**记录的实现偏差**：spec 原写"投影进
  verdict_summary"，但那建在 store 内而 store 不得调 git；已在 spec 写明
  正确形态，禁止用"给 store 加 git 调用"来抹平。
- **Verification**: drift 在 `plan status` 可见且 state 字节不变；未记录的
  绑定显示 `not_recorded` 而**不是** `match`。
```

```bash
git add src/agentdeck/state.py src/agentdeck/cli.py tests/test_review_binding.py \
        docs/contracts/project-view-schema.md docs/contracts/run-loop-schema.md \
        docs/superpowers/specs/2026-08-03-review-digest-binding-design.md \
        README.md CLAUDE.md HISTORY.md
git commit -m "feat: surface reviewed-state drift before the merge refuses it"
```

---

### Task 7: Correct the unsupported CCB claim

**Files:**
- Modify: `docs/reference-analysis/2026-08-03-ccb-current-state-and-gap.md:145-147`

- [ ] **Step 1: Verify the claim is still unsupported**

Run:

```bash
grep -ril "custody\|worktree digest\|post-review mutation\|root verification" \
  References/claude_codex_bridge-main/ | head
```

Expected: no output (zero hits). If there ARE hits, read them and correct this
task instead of the document — the point is to make the document match the
source, whichever way that cuts.

- [ ] **Step 2: Rewrite the claim**

Replace the third "该学" item (line ~145):

```markdown
3. **digest 绑定终态**。CCB 以"worktree digest 与绑定 digest 相同"作为
   终态判据,并做 post-review mutation 检测。AgentDeck 目前靠 reply +
   verdict,**无法发现"审查之后又被改动"**——这是一个真实的信任缺口。
```

with:

```markdown
3. **digest 纪律**(2026-08-03 修正)。**原文断言 CCB"以 worktree digest
   作终态判据并做 post-review mutation 检测",该断言在本仓库所持副本中
   无法证实**——`custody` / `worktree digest` / `post-review mutation` /
   `root verification` 全部零命中。CCB 实际把指纹绑在**产物**上:脚本
   commit artifact 时 `record digest / actor / job / timestamp`,同
   `(task, loop, result, report digest)` 重入幂等,**conflicting result or
   digest fails closed**,拒绝理由是闭合枚举且"不应该让 agent 猜测状态"。
   AgentDeck 当时连这一层都没有(artifact 只记 path/kind/status)。
   **该学的是产物级 digest 纪律**;而"绑定 git 终态、检测审后改动"是
   AgentDeck 在其之上多走的一步,不是对标 CCB。
   两者都已于 2026-08-03 落地,见
   `docs/superpowers/specs/2026-08-03-review-digest-binding-design.md`。

   教训与本文档自己要纠正的毛病同类,只是这次是我方在犯:**读一手材料时,
   把"我据此推断的"与"文档确实写了的"分开记。**
```

- [ ] **Step 3: HISTORY + commit**

Prepend under `## 2026-08-03`:

```markdown
### Correct an unsupported claim about CCB

- **Type**: docs
- **Motivation**: 落地 digest 绑定时逐条核对 CCB 副本，发现复研文档
  第 145 行的断言无法证实。
- **What**: 改写该条——CCB 的 digest 绑在**产物**上（commit 时记
  digest/actor/job/timestamp、同 digest 重入幂等、冲突 fail-closed、
  拒绝理由闭合枚举）；"绑定 git 终态 + 检测审后改动"是 AgentDeck 多走的
  一步，不是对标 CCB。
- **Impact**: 纯文档订正；差异化叙事以核实过的版本为准。
- **Verification**: 对 `References/claude_codex_bridge-main/` 检索
  `custody`/`worktree digest`/`post-review mutation`/`root verification`
  零命中。
```

```bash
git add docs/reference-analysis/2026-08-03-ccb-current-state-and-gap.md HISTORY.md
git commit -m "docs: correct an unsupported claim about CCB"
```

---

## Final review (do NOT skip — it has caught a fail-open every time)

- [ ] **Whole-lane review, not per-task.** Read tasks 1-7 together and look for
      seams *between* slices: does the base fix (1) hold for a rework step that
      follows a group? Does the gate (4) see a plan whose review step has no
      worktree at all? Does the artifact conflict (5) interact with the
      file-channel ingestion path in `run-loop`?
- [ ] **Mutation-verify every fail-closed assertion.** For each, weaken the rule
      and confirm the test goes red — and check the data path you chose does not
      let a degenerate rule pass too (the DAG slice's first seam test passed
      under mutation because it replied to the numerically adjacent member).
- [ ] **Grep for claims the code does not make.** Every "must" in the contract
      docs added by task 6: is it enforced, or is it a hope?
- [ ] Run `conda run -n agentdeck pytest -q` and `git diff --check`.
- [ ] Update `docs/handoff/current-development-state.md` with the lane summary
      and update the project memory note.
