# AgentDeck History Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `agentdeck history`, a read-only command that renders the existing `events.jsonl` audit ledger into a human-readable, newest-first Markdown timeline (with an optional `--write` to materialize `.agentdeck/HISTORY.md`).

**Architecture:** A new pure module `src/agentdeck/history.py` (mirrors `dashboard.py`): `render_history_markdown(events, project)` + `_humanize_event(event)` map audit events to Markdown. A small read-only `StateStore.all_events()` helper returns the full ledger. A thin `history_command` in `cli.py` wires it up. Everything derives from `events.jsonl`; no new state, no LLM, no new JSON contract.

**Tech Stack:** Python 3.12 stdlib, argparse CLI, pytest. Run tests with `conda run -n agentdeck pytest`.

---

## File Structure

- Create `src/agentdeck/history.py` — pure render + humanize functions.
- Modify `src/agentdeck/state.py` — add `StateStore.all_events()`.
- Modify `src/agentdeck/cli.py` — import `render_history_markdown`, add `history_command`, register the `history` subparser.
- Create `tests/test_history.py` — unit tests (all_events, humanize, render) + command tests.
- Modify `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md` — docs.

---

### Task 1: `StateStore.all_events()` — read the full ledger

**Files:**
- Modify: `src/agentdeck/state.py` (add method to `StateStore`, next to `list_events`)
- Test: `tests/test_history.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_history.py`:

```python
from agentdeck.config import write_default_config
from agentdeck.models import EventRecord
from agentdeck.state import StateStore


def _init_project(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    return root


def test_all_events_returns_full_ledger(tmp_path):
    root = _init_project(tmp_path)
    store = StateStore(root)
    for i in range(25):
        store.append_event(EventRecord.create("task_dispatched", {"agent_id": f"a{i}"}))

    events = store.all_events()

    # list_events(default 20) would cap; all_events returns everything, oldest-first
    assert len(events) == 25
    assert events[0]["payload"]["agent_id"] == "a0"
    assert events[-1]["payload"]["agent_id"] == "a24"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_history.py::test_all_events_returns_full_ledger -q`
Expected: FAIL with `AttributeError: 'StateStore' object has no attribute 'all_events'`

- [ ] **Step 3: Write minimal implementation**

In `src/agentdeck/state.py`, add this method immediately after `list_events`:

```python
    def all_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_history.py::test_all_events_returns_full_ledger -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/state.py tests/test_history.py
git commit -m "Add StateStore.all_events() read-only ledger helper"
```

---

### Task 2: `_humanize_event` — map audit events to human phrases

**Files:**
- Create: `src/agentdeck/history.py`
- Test: `tests/test_history.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_history.py`:

```python
def test_humanize_event_maps_milestones_and_skips_noise():
    from agentdeck.history import _humanize_event

    assert (
        _humanize_event({"event_type": "leader_plan_created", "payload": {"plan_id": "pln_1"}})
        == "Plan created · pln_1"
    )
    assert (
        _humanize_event(
            {"event_type": "approval_decided", "payload": {"status": "approved", "approval_id": "apv_1"}}
        )
        == "Approval approved · apv_1"
    )
    assert _humanize_event({"event_type": "project_initialized", "payload": {}}) == "Project initialized"
    assert (
        _humanize_event({"event_type": "round_released", "payload": {"round": 1}})
        == "Round released · round 1"
    )
    # noise (chat turns) and unknown event types are skipped
    assert _humanize_event({"event_type": "leader_chat_turn", "payload": {}}) is None
    assert _humanize_event({"event_type": "some_future_event", "payload": {}}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_history.py::test_humanize_event_maps_milestones_and_skips_noise -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentdeck.history'`

- [ ] **Step 3: Write minimal implementation**

Create `src/agentdeck/history.py`:

```python
"""Read-only renderer that projects the events.jsonl audit ledger to Markdown.

Deterministic and LLM-free: every line is derived from the audit events alone.
Rendering never mutates state; the optional file write in the CLI materializes a
regenerable projection (mirrors how dashboard.py renders the workbench contract).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


def _detail(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


# event_type -> (action, detail) derived from the payload. Any event_type NOT in
# this map (e.g. leader_chat_turn, internal validation failures, future events) is
# skipped, so new events never break rendering.
_MILESTONES = {
    "project_initialized": lambda p: ("Project initialized", ""),
    "leader_plan_created": lambda p: ("Plan created", _detail(p, "plan_id")),
    "run_started": lambda p: ("Run started", _detail(p, "task")),
    "approvals_created_from_plan": lambda p: ("Approvals created from plan", _detail(p, "plan_id")),
    "approval_created_from_chat": lambda p: ("Approval created (from chat)", ""),
    "approval_decided": lambda p: (f"Approval {_detail(p, 'status') or 'decided'}", _detail(p, "approval_id")),
    "approval_dispatched": lambda p: ("Approval dispatched", _detail(p, "approval_id")),
    "approval_dispatch_ready_completed": lambda p: ("Batch dispatch completed", _detail(p, "dispatched_count", "count")),
    "task_dispatched": lambda p: ("Task dispatched", _detail(p, "agent_id", "to_agent")),
    "task_replied": lambda p: ("Reply captured", _detail(p, "agent_id", "from_agent")),
    "reply_captured": lambda p: ("Reply captured", _detail(p, "agent_id", "from_agent")),
    "round_released": lambda p: ("Round released", f"round {_detail(p, 'round')}" if _detail(p, "round") else ""),
    "round_release_rejected": lambda p: ("Release rejected", _detail(p, "reason")),
    "policy_mode_updated": lambda p: ("Control mode changed", _detail(p, "mode")),
    "policy_mode_rejected": lambda p: ("Control mode change rejected", _detail(p, "mode")),
    "leader_provider_updated": lambda p: ("Leader provider switched", "/".join(x for x in [_detail(p, "provider"), _detail(p, "model")] if x)),
    "leader_provider_update_rejected": lambda p: ("Provider switch rejected", ""),
    "leader_provider_failed": lambda p: ("Provider failed", _detail(p, "detail", "error")),
    "agent_spawned": lambda p: ("Agent spawned", _detail(p, "agent_id")),
    "agent_spawn_ready_completed": lambda p: ("Agents spawned", _detail(p, "spawned_count")),
    "agent_stopped": lambda p: ("Agent stopped", _detail(p, "agent_id")),
    "agent_input_sent": lambda p: ("Input sent", _detail(p, "agent_id")),
    "agent_role_assigned": lambda p: ("Role assigned", "/".join(x for x in [_detail(p, "agent_id"), _detail(p, "role")] if x)),
    "agent_runtime_stale": lambda p: ("Runtime marked stale", _detail(p, "agent_id")),
    "inbox_item_acked": lambda p: ("Inbox acked", _detail(p, "inbox_id")),
    "leader_action_suggested": lambda p: ("Leader action suggested", _detail(p, "kind", "action_kind")),
    "leader_action_applied": lambda p: ("Leader action applied", _detail(p, "kind", "action_kind")),
    "skill_imported": lambda p: ("Skill imported", _detail(p, "name")),
    "skill_loaded": lambda p: ("Skill loaded", _detail(p, "name")),
    "skill_suggested": lambda p: ("Skill suggested", _detail(p, "name")),
    "skill_created": lambda p: ("Skill created", _detail(p, "name")),
    "memory_suggested": lambda p: ("Memory suggested", ""),
    "memory_applied": lambda p: ("Memory applied", ""),
}


def _humanize_event(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("event_type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    render = _MILESTONES.get(event_type)
    if render is None:
        return None
    action, detail = render(payload)
    return f"{action} · {detail}" if detail else action
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_history.py::test_humanize_event_maps_milestones_and_skips_noise -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/history.py tests/test_history.py
git commit -m "Add _humanize_event milestone mapping for history timeline"
```

---

### Task 3: `render_history_markdown` — newest-first, grouped by date

**Files:**
- Modify: `src/agentdeck/history.py`
- Test: `tests/test_history.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_history.py`:

```python
def test_render_history_markdown_is_newest_first_grouped_by_date():
    from agentdeck.history import render_history_markdown

    events = [
        {"event_type": "project_initialized", "created_at": "2026-07-07T09:00:00+00:00", "payload": {}},
        {"event_type": "leader_chat_turn", "created_at": "2026-07-07T09:05:00+00:00", "payload": {}},
        {"event_type": "leader_plan_created", "created_at": "2026-07-08T10:00:00+00:00", "payload": {"plan_id": "pln_1"}},
        {"event_type": "round_released", "created_at": "2026-07-08T11:00:00.5+00:00", "payload": {"round": 1}},
    ]

    md = render_history_markdown(events, "demo")

    assert md.startswith("# AgentDeck History — demo")
    # newest date first
    assert md.index("## 2026-07-08") < md.index("## 2026-07-07")
    # within the newest date, newest event first
    assert md.index("Round released · round 1") < md.index("Plan created · pln_1")
    # timestamps rendered as HH:MM:SS
    assert "11:00:00 · Round released · round 1" in md
    # noise skipped, milestone kept
    assert "leader_chat_turn" not in md
    assert "Project initialized" in md
    # deterministic
    assert render_history_markdown(events, "demo") == md


def test_render_history_markdown_handles_empty_ledger():
    from agentdeck.history import render_history_markdown

    md = render_history_markdown([], "demo")

    assert md.startswith("# AgentDeck History — demo")
    assert "_No recorded activity yet._" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_history.py -k render_history_markdown -q`
Expected: FAIL with `ImportError: cannot import name 'render_history_markdown'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/agentdeck/history.py`:

```python
def _split_timestamp(created_at: str) -> tuple[str, str]:
    if "T" in created_at:
        date, _, rest = created_at.partition("T")
        return date, rest[:8]
    return created_at[:10], ""


def render_history_markdown(events: list[dict[str, Any]], project: str) -> str:
    header = [f"# AgentDeck History — {project}", ""]
    rendered: list[tuple[str, str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        text = _humanize_event(event)
        if text is None:
            continue
        date, time = _split_timestamp(str(event.get("created_at") or ""))
        rendered.append((date, time, text))
    if not rendered:
        return "\n".join(header + ["_No recorded activity yet._"]) + "\n"
    rendered.reverse()  # ledger is oldest-first; reverse for newest-first
    by_date: "OrderedDict[str, list[tuple[str, str]]]" = OrderedDict()
    for date, time, text in rendered:
        by_date.setdefault(date, []).append((time, text))
    lines = list(header)
    for date, entries in by_date.items():
        lines.append(f"## {date}")
        for time, text in entries:
            lines.append(f"- {time} · {text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_history.py -k render_history_markdown -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/history.py tests/test_history.py
git commit -m "Add render_history_markdown newest-first timeline renderer"
```

---

### Task 4: `agentdeck history` command (print) + subparser

**Files:**
- Modify: `src/agentdeck/cli.py` (import, command function near `dashboard_command`, subparser near the `dashboard` parser)
- Test: `tests/test_history.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_history.py`:

```python
def test_history_command_prints_markdown_timeline(tmp_path, monkeypatch, capsys):
    import json

    from agentdeck import cli

    root = _init_project(tmp_path)
    monkeypatch.chdir(root)
    store = StateStore(root)
    store.append_event(EventRecord.create("leader_plan_created", {"plan_id": "pln_1"}))
    events_before = store.all_events()

    exit_code = cli.main(["history"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("# AgentDeck History")
    assert "Plan created · pln_1" in out
    # it is a text timeline, not JSON
    try:
        json.loads(out)
        is_json = True
    except json.JSONDecodeError:
        is_json = False
    assert is_json is False
    # read-only: the ledger is unchanged
    assert StateStore(root).all_events() == events_before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_history.py::test_history_command_prints_markdown_timeline -q`
Expected: FAIL — argparse errors with `invalid choice: 'history'`

- [ ] **Step 3: Write minimal implementation**

In `src/agentdeck/cli.py`, add the import next to the existing dashboard import (find the line `from .dashboard import render_workbench_dashboard`):

```python
from .history import render_history_markdown
```

Add the command function immediately after `dashboard_command` (before `def controls_command`):

```python
def history_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    limit = getattr(args, "limit", None)
    if limit and limit > 0:
        events = store.list_events(limit=limit)
    else:
        events = store.all_events()
    print(render_history_markdown(events, config.name), end="")
    return 0
```

Register the subparser immediately after the `dashboard` subparser block (find `dashboard.set_defaults(func=dashboard_command)` and add after it):

```python
    history = subparsers.add_parser(
        "history",
        help="Render a human-readable Markdown timeline from the audit ledger",
    )
    history.add_argument(
        "--limit", type=int, default=None, help="Only include the most recent N events (default: all)"
    )
    history.set_defaults(func=history_command)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_history.py::test_history_command_prints_markdown_timeline -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_history.py
git commit -m "Add agentdeck history command"
```

---

### Task 5: `--write` — materialize `.agentdeck/HISTORY.md`

**Files:**
- Modify: `src/agentdeck/cli.py` (add `--write` arg + write branch in `history_command`)
- Test: `tests/test_history.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_history.py`:

```python
def test_history_command_write_materializes_file(tmp_path, monkeypatch, capsys):
    from agentdeck import cli

    root = _init_project(tmp_path)
    monkeypatch.chdir(root)
    store = StateStore(root)
    store.append_event(EventRecord.create("round_released", {"round": 1}))
    events_before = store.all_events()

    exit_code = cli.main(["history", "--write"])

    assert exit_code == 0
    target = root / ".agentdeck" / "HISTORY.md"
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert content.startswith("# AgentDeck History")
    assert "Round released · round 1" in content
    # stdout reports the write rather than dumping the markdown
    out = capsys.readouterr().out
    assert "wrote" in out and "HISTORY.md" in out
    assert not out.startswith("# AgentDeck History")
    # writing the projection leaves the audit ledger unchanged
    assert StateStore(root).all_events() == events_before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_history.py::test_history_command_write_materializes_file -q`
Expected: FAIL — argparse errors with `unrecognized arguments: --write`

- [ ] **Step 3: Write minimal implementation**

In `src/agentdeck/cli.py`, add the `--write` argument to the `history` subparser (after the `--limit` line):

```python
    history.add_argument(
        "--write",
        nargs="?",
        const="",
        default=None,
        help="Write the timeline to a file (default .agentdeck/HISTORY.md; pass a path to override)",
    )
```

Update `history_command` to handle the write branch. Replace the final `print(render_history_markdown(events, config.name), end="")` line and its `return 0` with:

```python
    markdown = render_history_markdown(events, config.name)
    write_target = getattr(args, "write", None)
    if write_target is not None:
        target = (Path(store.deck_dir) / "HISTORY.md") if write_target == "" else Path(write_target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
        print(f"wrote {target}")
        return 0
    print(markdown, end="")
    return 0
```

(`Path` and `sys` are already imported in `cli.py`; `store.deck_dir` is the `.agentdeck` directory.)

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_history.py::test_history_command_write_materializes_file -q`
Expected: PASS

- [ ] **Step 5: Run the whole history test file + compile check**

Run: `conda run -n agentdeck pytest tests/test_history.py -q`
Expected: PASS (7 tests)
Run: `conda run -n agentdeck python -m compileall src tests -q`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/agentdeck/cli.py tests/test_history.py
git commit -m "Add --write to agentdeck history (materialize .agentdeck/HISTORY.md)"
```

---

### Task 6: Docs + full-suite verification

**Files:**
- Modify: `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`

- [ ] **Step 1: README paragraph**

In `README.md`, near the `agentdeck dashboard` paragraph, add:

```markdown
`agentdeck history` 是审计账本的只读渲染器:它把 `events.jsonl` 里的事件确定性地渲染成**最新在上、按日期分组**的人类可读 Markdown 时间线(每个里程碑一行,`leader_chat_turn` 等噪声跳过),不用 LLM、不加新契约、不碰 provider/tmux/审批。`agentdeck history` 打到屏幕;`agentdeck history --write` 落成 `.agentdeck/HISTORY.md`(gitignore、幂等、可随时重生成,不撞项目根的人写 HISTORY.md),`--write <path>` 可覆盖路径,`--limit N` 只取最近 N 条。
```

- [ ] **Step 2: HISTORY.md entry**

At the TOP of `HISTORY.md`'s changelog (newest-first), add an entry:

```markdown
### Current - Add agentdeck history timeline renderer

- 新增 `agentdeck history`(北极星"审计/HISTORY 门",autonomous 三块拆分的子项目 1):从已有 `events.jsonl` 账本确定性渲染人类可读 Markdown 时间线,最新在上、按日期分组,每个里程碑事件一行,`leader_chat_turn` 等噪声与未知事件跳过。
- 新增纯模块 `src/agentdeck/history.py`(`render_history_markdown` + `_humanize_event`,约 30 种事件的 humanize 白名单)和只读 `StateStore.all_events()`(读全量账本,因为 `list_events(limit<=0)` 返回空)。
- `agentdeck history` 打到 stdout(纯只读);`agentdeck history --write` 落成 `.agentdeck/HISTORY.md`(幂等投影,不改账本/state),`--write <path>` / `--limit N` 可调。不用 LLM、不加新 JSON 契约、不调 provider、不碰 tmux/审批。
- 设计与实现计划见 `docs/superpowers/specs/2026-07-08-agentdeck-history-timeline-design.md` 与 `docs/superpowers/plans/2026-07-08-agentdeck-history-timeline.md`。
- 验证记录:目标测试 `conda run -n agentdeck pytest tests/test_history.py -q` 7 项通过;`conda run -n agentdeck python -m compileall src tests` 和 `git diff --check` 通过;`conda run -n agentdeck pytest -q` 全量通过。
```

- [ ] **Step 3: Handoff update**

In `docs/handoff/current-development-state.md`, under "Next Best Step", note that sub-project 1 (audit/HISTORY gate) is done and the next is sub-project 2 (autonomous policy switch + allowlist/budget), then sub-project 3 (executing round loop). Reference the spec/plan files.

- [ ] **Step 4: Full verification**

Run: `conda run -n agentdeck pytest -q`
Expected: PASS (all existing tests + 7 new)
Run: `conda run -n agentdeck python -m compileall src tests -q`
Expected: no errors
Run: `git diff --check`
Expected: no whitespace errors

- [ ] **Step 5: Commit**

```bash
git add README.md HISTORY.md docs/handoff/current-development-state.md
git commit -m "Document agentdeck history timeline"
```

---

## Notes for the implementer

- Keep everything read-only w.r.t. audit state: `history` must never append to `events.jsonl` or change `state.json`. `--write` only writes the derived `.agentdeck/HISTORY.md` projection.
- Do NOT push. Commit locally only. Do NOT add a Claude co-author trailer.
- Run all commands in the `agentdeck` conda env.
- This is sub-project 1 of 3. Do not implement the autonomous policy switch or the executing loop here.
