# Autonomous Mode + `approval auto` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the placeholder `autonomous` control mode into a real bounded delegation: `agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>` stores an agent-allowlist + count budget in config; `agentdeck approval auto --confirm` auto-approves allowlisted pending approvals within budget and dispatches them to running agents, fully audited.

**Architecture:** Config gains an `[autonomous]` section parsed into `ProjectConfig.autonomous` (`AutonomousPolicy`). A pure `select_auto_approvals` in a new `src/agentdeck/autonomy.py` makes the allowlist/budget decision (reused by sub-project 3 later). `policy set-mode` accepts autonomous; `approval auto` reuses the existing dispatch internals (`_dispatch_approved_approval`, `_approval_dispatch_preview_card`). Every auto-action is an audit event, so it flows into `agentdeck history`.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest. Run tests via `conda run -n agentdeck ...`.

**Design spec:** `docs/superpowers/specs/2026-07-08-autonomous-mode-design.md`

---

## File Structure

- Create `src/agentdeck/autonomy.py` — `select_auto_approvals` (pure).
- Modify `src/agentdeck/models.py` — add `AutonomousPolicy` dataclass; add `autonomous` field to `ProjectConfig`.
- Modify `src/agentdeck/config.py` — parse `[autonomous]` in `load_config`; add `update_autonomous_policy`.
- Modify `src/agentdeck/cli.py` — `policy_set_mode_command` accepts autonomous; new `approval_auto_command`; subparser args; `_control_mode_from_approval_mode` + `_workbench_control_mode_card` autonomous lighting.
- Modify `src/agentdeck/history.py` — humanize `approval_auto_completed`.
- Create `tests/test_autonomy.py`; modify `tests/test_agent_cli.py`, `tests/test_leader_cli.py`.
- Modify `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`, `CLAUDE.md`.

---

### Task 1: `AutonomousPolicy` config model + `load_config` parses `[autonomous]`

**Files:**
- Modify: `src/agentdeck/models.py` (add dataclass + field)
- Modify: `src/agentdeck/config.py` (`load_config`)
- Test: `tests/test_autonomy.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_autonomy.py`:

```python
from agentdeck.config import load_config, write_default_config


def _init(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    return root


def test_load_config_defaults_autonomous_policy_empty(tmp_path):
    root = _init(tmp_path)
    config = load_config(root)
    assert config.autonomous.allowed_agents == ()
    assert config.autonomous.max_approvals == 0


def test_load_config_parses_autonomous_section(tmp_path):
    root = _init(tmp_path)
    cfg = (root / ".agentdeck" / "config.toml")
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + '\n[autonomous]\nallowed_agents = ["planner", "coder"]\nmax_approvals = 3\n',
        encoding="utf-8",
    )
    config = load_config(root)
    assert config.autonomous.allowed_agents == ("planner", "coder")
    assert config.autonomous.max_approvals == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_autonomy.py -q`
Expected: FAIL — `AttributeError: 'ProjectConfig' object has no attribute 'autonomous'`

- [ ] **Step 3: Write minimal implementation**

In `src/agentdeck/models.py`, add the dataclass right after `LeaderConfig`:

```python
@dataclass(frozen=True)
class AutonomousPolicy:
    allowed_agents: tuple[str, ...] = ()
    max_approvals: int = 0
```

And add the field to `ProjectConfig` (with a default so existing constructions stay valid):

```python
@dataclass(frozen=True)
class ProjectConfig:
    name: str
    root: str
    leader: LeaderConfig
    agents: tuple[AgentSpec, ...]
    runtime: RuntimeConfig
    autonomous: AutonomousPolicy = AutonomousPolicy()
```

In `src/agentdeck/config.py`, import `AutonomousPolicy` (add to the existing `from .models import ...`), then in `load_config` parse the section and pass it to `ProjectConfig`. After the `agents = tuple(...)` block and before `project_raw = ...`, add:

```python
    autonomous_raw = raw.get("autonomous", {})
    allowed = autonomous_raw.get("allowed_agents", []) if isinstance(autonomous_raw, dict) else []
    autonomous = AutonomousPolicy(
        allowed_agents=tuple(str(a) for a in allowed),
        max_approvals=int(autonomous_raw.get("max_approvals", 0)) if isinstance(autonomous_raw, dict) else 0,
    )
```

Then add `autonomous=autonomous,` to the `return ProjectConfig(...)` call.

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_autonomy.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/models.py src/agentdeck/config.py tests/test_autonomy.py
git commit -m "Add AutonomousPolicy config model and parsing"
```

---

### Task 2: `update_autonomous_policy` config writer

**Files:**
- Modify: `src/agentdeck/config.py`
- Test: `tests/test_autonomy.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_autonomy.py`:

```python
def test_update_autonomous_policy_writes_and_reloads(tmp_path):
    from agentdeck.config import update_autonomous_policy

    root = _init(tmp_path)
    update_autonomous_policy(root, ("planner", "coder"), 5)
    config = load_config(root)
    assert config.autonomous.allowed_agents == ("planner", "coder")
    assert config.autonomous.max_approvals == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_autonomy.py::test_update_autonomous_policy_writes_and_reloads -q`
Expected: FAIL — `ImportError: cannot import name 'update_autonomous_policy'`

- [ ] **Step 3: Write minimal implementation**

**IMPORTANT:** `_dump_config` currently hardcodes only the `project`/`leader`/`agents`/`runtime` sections and reconstructs the file from scratch — it does NOT round-trip an `[autonomous]` section. So writing `raw["autonomous"]` alone would be silently dropped. You MUST extend `_dump_config` first.

In `src/agentdeck/config.py`, in `_dump_config`, after the `runtime` block (just before `return "\n".join(lines) + "\n"`), append:

```python
    autonomous = raw.get("autonomous", {})
    if isinstance(autonomous, dict) and (autonomous.get("allowed_agents") or autonomous.get("max_approvals")):
        allowed = autonomous.get("allowed_agents", []) or []
        allowed_toml = "[" + ", ".join(_quote_toml(str(a)) for a in allowed) + "]"
        lines.extend([
            "",
            "[autonomous]",
            f"allowed_agents = {allowed_toml}",
            f"max_approvals = {int(autonomous.get('max_approvals', 0))}",
        ])
```

Then add the writer after `update_leader_approval_mode`:

```python
def update_autonomous_policy(root: Path, allowed_agents: tuple[str, ...], max_approvals: int) -> AutonomousPolicy:
    path = config_path(root)
    if not path.exists():
        raise FileNotFoundError(f"missing config: {path}")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    raw["autonomous"] = {
        "allowed_agents": list(allowed_agents),
        "max_approvals": int(max_approvals),
    }
    path.write_text(_dump_config(raw), encoding="utf-8")
    return load_config(root).autonomous
```

Check the exact import name used for reading TOML in `config.py` (it may be `tomllib` or an aliased import) and reuse whatever `load_config` uses. Reuse the existing `config_path` / `_quote_toml` helpers already in the module.

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_autonomy.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/config.py tests/test_autonomy.py
git commit -m "Add update_autonomous_policy config writer"
```

---

### Task 3: `select_auto_approvals` pure decision function

**Files:**
- Create: `src/agentdeck/autonomy.py`
- Test: `tests/test_autonomy.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_autonomy.py`:

```python
def test_select_auto_approvals_filters_by_allowlist_and_budget():
    from agentdeck.autonomy import select_auto_approvals

    pending = [
        {"approval_id": "apv_1", "agent_id": "planner"},
        {"approval_id": "apv_2", "agent_id": "reviewer"},
        {"approval_id": "apv_3", "agent_id": "coder"},
        {"approval_id": "apv_4", "agent_id": "planner"},
    ]
    selected, skipped = select_auto_approvals(pending, ("planner", "coder"), max_approvals=2)

    # allowlisted in ledger order, capped at 2
    assert [a["approval_id"] for a in selected] == ["apv_1", "apv_3"]
    reasons = {s["approval_id"]: s["reason"] for s in skipped}
    assert reasons["apv_2"] == "agent not in allowlist"
    assert reasons["apv_4"] == "budget exhausted"


def test_select_auto_approvals_empty_allowlist_selects_nothing():
    from agentdeck.autonomy import select_auto_approvals

    pending = [{"approval_id": "apv_1", "agent_id": "planner"}]
    selected, skipped = select_auto_approvals(pending, (), max_approvals=5)
    assert selected == []
    assert skipped[0]["reason"] == "agent not in allowlist"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_autonomy.py -k select_auto_approvals -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentdeck.autonomy'`

- [ ] **Step 3: Write minimal implementation**

Create `src/agentdeck/autonomy.py`:

```python
"""Pure autonomous-mode decision logic (no I/O).

Given the human-configured allowlist + count budget, decide which pending
approvals AgentDeck may auto-approve. Reused by `agentdeck approval auto` and,
later, by the sub-project 3 execution loop.
"""

from __future__ import annotations

from typing import Any


def select_auto_approvals(
    pending: list[dict[str, Any]],
    allowed_agents: tuple[str, ...],
    max_approvals: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed = set(allowed_agents)
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for approval in pending:
        if not isinstance(approval, dict):
            continue
        agent_id = approval.get("agent_id")
        if agent_id not in allowed:
            skipped.append({**approval, "reason": "agent not in allowlist"})
        elif len(selected) >= max_approvals:
            skipped.append({**approval, "reason": "budget exhausted"})
        else:
            selected.append(approval)
    return selected, skipped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_autonomy.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/autonomy.py tests/test_autonomy.py
git commit -m "Add select_auto_approvals pure decision function"
```

---

### Task 4: `policy set-mode --mode autonomous` accepts (with allowlist + budget)

**Files:**
- Modify: `src/agentdeck/cli.py` (`policy_set_mode_command`, subparser args, import)
- Modify: `tests/test_agent_cli.py` (replace the rejection test)
- Test: `tests/test_agent_cli.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_agent_cli.py`, replace `test_policy_set_mode_rejects_autonomous_without_mutating_config` (around line 7755) with these tests:

```python
def test_policy_set_mode_enables_autonomous_with_confirm_and_allowlist(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main([
        "policy", "set-mode", "--mode", "autonomous", "--confirm",
        "--allow-agent", "planner", "--allow-agent", "coder", "--max-approvals", "3",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "autonomous"
    assert payload["allowed_agents"] == ["planner", "coder"]
    assert payload["max_approvals"] == 3
    from agentdeck.config import load_config
    config = load_config(root)
    assert config.leader.approval_mode == "autonomous"
    assert config.autonomous.allowed_agents == ("planner", "coder")
    assert config.autonomous.max_approvals == 3
    assert StateStore(root).list_events(limit=1)[0]["event_type"] == "policy_mode_updated"


def test_policy_set_mode_autonomous_requires_confirm_and_valid_scope(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    from agentdeck.config import load_config

    # missing --confirm
    assert cli.main(["policy", "set-mode", "--mode", "autonomous", "--allow-agent", "planner", "--max-approvals", "1"]) == 1
    assert "confirm" in capsys.readouterr().err
    # missing allowlist
    assert cli.main(["policy", "set-mode", "--mode", "autonomous", "--confirm", "--max-approvals", "1"]) == 1
    capsys.readouterr()
    # unknown agent
    assert cli.main(["policy", "set-mode", "--mode", "autonomous", "--confirm", "--allow-agent", "ghost", "--max-approvals", "1"]) == 1
    assert "ghost" in capsys.readouterr().err
    # config unchanged (still confirm)
    assert load_config(root).leader.approval_mode == "confirm"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py::test_policy_set_mode_enables_autonomous_with_confirm_and_allowlist -q`
Expected: FAIL — the command currently rejects autonomous / args unrecognized.

- [ ] **Step 3: Write minimal implementation**

In `src/agentdeck/cli.py`, add to the `from .config import (...)` import list: `update_autonomous_policy`.

Add the subparser args (find `policy_set_mode.add_argument("--mode", ...)` and add after it):

```python
    policy_set_mode.add_argument("--allow-agent", action="append", default=None, help="Agent id allowed for autonomous auto-approval (repeatable)")
    policy_set_mode.add_argument("--max-approvals", type=int, default=None, help="Max auto-approvals per autonomous run")
    policy_set_mode.add_argument("--confirm", action="store_true", help="Explicitly confirm autonomous mode")
```

Replace the `if mode == "autonomous":` rejection block in `policy_set_mode_command` with:

```python
    if mode == "autonomous":
        config, store2, exit_code = _load_project_or_error()
        if config is None or store2 is None:
            return exit_code
        allow = args.allow_agent or []
        max_approvals = args.max_approvals
        reason = None
        if not args.confirm:
            reason = "autonomous requires --confirm"
        elif not allow or not max_approvals or max_approvals < 1:
            reason = "autonomous requires --allow-agent and --max-approvals >= 1"
        else:
            known = {a.agent_id for a in config.agents}
            unknown = [a for a in allow if a not in known]
            if unknown:
                reason = f"unknown agent: {unknown[0]}"
        if reason is not None:
            store.append_event(EventRecord.create("policy_mode_rejected", {"mode": mode, "reason": reason}))
            print(reason, file=sys.stderr)
            return 1
        leader = update_leader_approval_mode(project_root(), "autonomous")
        policy = update_autonomous_policy(project_root(), tuple(allow), int(max_approvals))
        store.append_event(EventRecord.create("policy_mode_updated", {
            "mode": "autonomous",
            "approval_mode": leader.approval_mode,
            "allowed_agents": list(policy.allowed_agents),
            "max_approvals": policy.max_approvals,
        }))
        _print_json({
            "ok": True,
            "mode": "autonomous",
            "approval_mode": leader.approval_mode,
            "allowed_agents": list(policy.allowed_agents),
            "max_approvals": policy.max_approvals,
            "auto_command": "agentdeck approval auto --confirm",
        })
        return 0
```

(Keep the existing `ask`/`approve` handling below unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "autonomous" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py
git commit -m "Accept policy set-mode --mode autonomous with allowlist and budget"
```

---

### Task 5: `agentdeck approval auto --confirm`

**Files:**
- Modify: `src/agentdeck/cli.py` (new `approval_auto_command`, subparser, import `select_auto_approvals`)
- Test: `tests/test_agent_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_cli.py` (reuse the existing `prepare_project`, `bind_agent`, `FakeTmuxBackend` helpers):

```python
def _seed_pending_approval(root, approval_id, agent_id):
    store = StateStore(root)
    state = store.load()
    state.setdefault("approvals", []).append({
        "approval_id": approval_id, "plan_id": "pln_x", "step": 1,
        "agent_id": agent_id, "role": "planning", "task": "do work",
        "risk": "low", "status": "pending", "created_at": "2026-07-04T00:00:00+00:00",
    })
    store.save(state)


def test_approval_auto_requires_confirm_and_autonomous_mode(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _seed_pending_approval(root, "apv_1", "planner")
    before = StateStore(root).load()
    # no --confirm
    assert cli.main(["approval", "auto"]) == 1
    assert "confirm" in capsys.readouterr().err
    # confirm but mode is not autonomous
    assert cli.main(["approval", "auto", "--confirm"]) == 1
    assert "autonomous mode is not enabled" in capsys.readouterr().err
    assert StateStore(root).load() == before  # nothing written


def test_approval_auto_approves_and_dispatches_within_policy(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")  # planner running
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["policy", "set-mode", "--mode", "autonomous", "--confirm", "--allow-agent", "planner", "--max-approvals", "5"])
    capsys.readouterr()
    _seed_pending_approval(root, "apv_ok", "planner")     # allowlisted + running -> dispatched
    _seed_pending_approval(root, "apv_skip", "reviewer")  # not allowlisted -> skipped
    _seed_pending_approval(root, "apv_block", "coder")    # not allowlisted -> skipped (coder not allowed)

    exit_code = cli.main(["approval", "auto", "--confirm"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "approval_auto"
    assert payload["auto_approved"] == 1
    assert payload["dispatched"][0]["approval_id"] == "apv_ok"
    skipped_ids = {s["approval_id"] for s in payload["skipped"]}
    assert skipped_ids == {"apv_skip", "apv_block"}
    # apv_ok is now dispatched; auto approval + dispatch are audited
    state = StateStore(root).load()
    apv = next(a for a in state["approvals"] if a["approval_id"] == "apv_ok")
    assert apv["status"] == "dispatched"
    types = [e["event_type"] for e in StateStore(root).list_events(limit=20)]
    assert "approval_auto_completed" in types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py::test_approval_auto_requires_confirm_and_autonomous_mode -q`
Expected: FAIL — `invalid choice: 'auto'`

- [ ] **Step 3: Write minimal implementation**

In `src/agentdeck/cli.py`, add the import near the other local imports: `from .autonomy import select_auto_approvals`.

Add the command function (place it near `approval_dispatch_ready_command`):

```python
def approval_auto_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if not args.confirm:
        print("approval auto requires --confirm", file=sys.stderr)
        return 1
    if config.leader.approval_mode != "autonomous":
        print(
            "autonomous mode is not enabled; run agentdeck policy set-mode --mode autonomous "
            "--confirm --allow-agent <id> --max-approvals <N>",
            file=sys.stderr,
        )
        return 1
    policy = config.autonomous
    pending = [a for a in store.load().get("approvals", []) if isinstance(a, dict) and a.get("status") == "pending"]
    selected, skipped = select_auto_approvals(pending, policy.allowed_agents, policy.max_approvals)
    backend = TmuxBackend()
    dispatched: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []
    for approval in selected:
        approval_id = str(approval.get("approval_id", ""))
        store.decide_approval(approval_id, "approved", reason="autonomous")
        store.append_event(EventRecord.create("approval_decided", {
            "approval_id": approval_id, "status": "approved", "source": "autonomous",
        }))
        approved = store.approval_by_id(approval_id)
        preview = _approval_dispatch_preview_card(approved, config, store)
        if preview.get("blocker"):
            blocked.append({"approval_id": approval_id, "agent_id": approved.get("agent_id"), "blocker": preview.get("blocker")})
            continue
        result = _dispatch_approved_approval(approved, approval_id=approval_id, config=config, store=store, backend=backend)
        dispatched.append({
            "approval_id": approval_id, "agent_id": result["agent_id"],
            "message_id": result["message_id"], "trace_command": result["trace_command"],
        })
    store.append_event(EventRecord.create("approval_auto_completed", {
        "auto_approved": len(selected), "dispatched": len(dispatched),
        "blocked": len(blocked), "skipped": len(skipped),
    }))
    _print_json({
        "ok": True,
        "mode": "approval_auto",
        "requires_explicit_user": True,
        "safety": "delegated",
        "auto_approved": len(selected),
        "dispatched": dispatched,
        "blocked": blocked,
        "skipped": [{"approval_id": s.get("approval_id"), "agent_id": s.get("agent_id"), "reason": s.get("reason")} for s in skipped],
        "policy": {"allowed_agents": list(policy.allowed_agents), "max_approvals": policy.max_approvals},
    })
    return 0
```

Register the `auto` subcommand. The `approval` subparsers group is `approval_subparsers` (defined near `src/agentdeck/cli.py:13074`, alongside `approval_subparsers.add_parser("dispatch", ...)` and `approval_subparsers.add_parser(...)` for dispatch-ready). Add after the dispatch-ready registration:

```python
    approval_auto = approval_subparsers.add_parser("auto", help="Auto-approve allowlisted pending approvals and dispatch them (autonomous mode)")
    approval_auto.add_argument("--confirm", action="store_true", help="Explicitly confirm autonomous auto-approval")
    approval_auto.set_defaults(func=approval_auto_command)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "approval_auto" -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py
git commit -m "Add agentdeck approval auto command"
```

---

### Task 6: control_mode_card lights up autonomous (contract consistency)

**Files:**
- Modify: `src/agentdeck/cli.py` (`_control_mode_from_approval_mode`, `_workbench_control_mode_card` autonomous option)
- Modify: `tests/test_agent_cli.py` (card assertions), `tests/test_leader_cli.py` (natural-language policy)
- Test: `tests/test_agent_cli.py`

- [ ] **Step 1: Update the failing assertions + add a card test**

First fix `_control_mode_from_approval_mode` so autonomous maps to itself. In `tests/test_agent_cli.py`, add:

```python
def test_control_mode_card_enables_autonomous_when_policy_supported(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    exit_code = cli.main(["workbench"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    modes = {m["mode"]: m for m in payload["control_mode_card"]["available_modes"]}
    auto = modes["autonomous"]
    assert auto["enabled"] is True
    assert auto["blocker"] is None
    # the set-mode control is a disabled template requiring explicit args
    ctrls = {c.get("label"): c for c in payload["control_mode_card"]["active_controls"] if c.get("kind") == "set_mode"}
    auto_ctrl = ctrls["Autonomous bounded"]
    assert auto_ctrl["enabled"] is False
    assert "--allow-agent" in auto_ctrl["command"]
    assert auto_ctrl["blocker"] == "requires --allow-agent and --max-approvals"
```

Also update the two existing assertions in `tests/test_agent_cli.py` (around lines ~7395 and ~7429) that expect `"blocker": "autonomous execution policy is not implemented"` — change the expected autonomous option/control to the new enabled option + disabled template control shape (matching the assertions above). And update `tests/test_leader_cli.py::test_leader_chat_suggests_autonomous_policy_command_but_keeps_it_blocked` so it expects the autonomous control to be a suggestable template (command contains `--allow-agent`, blocker `requires --allow-agent and --max-approvals`) rather than `autonomous execution policy is not implemented`; rename it to `test_leader_chat_suggests_autonomous_policy_command_template`.

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py::test_control_mode_card_enables_autonomous_when_policy_supported -q`
Expected: FAIL — autonomous option still `enabled=False` with the old blocker.

- [ ] **Step 3: Implement**

In `src/agentdeck/cli.py`, update `_control_mode_from_approval_mode`:

```python
def _control_mode_from_approval_mode(approval_mode: object) -> str:
    if approval_mode == "autonomous":
        return "autonomous"
    return "approve" if approval_mode in {"auto_approve", "approve"} else "ask"
```

In `_workbench_control_mode_card`, change the autonomous entry in `available_modes` to:

```python
        {
            "mode": "autonomous",
            "label": "Autonomous bounded",
            "description": "Scoped delegation: auto-approve allowlisted pending approvals within a count budget, fully audited.",
            "enabled": True,
            "requires_explicit_user": True,
            "safety": "delegated",
            "blocker": None,
        },
```

In `_control_mode_set_controls`, special-case autonomous so its control is a disabled template. Replace the entire function body loop with (this is the exact current function — only the `command`/autonomous branch is added):

```python
def _control_mode_set_controls(current_mode: str, available_modes: list[dict[str, object]]) -> list[dict[str, object]]:
    controls: list[dict[str, object]] = []
    for option in available_modes:
        mode = str(option.get("mode"))
        enabled = bool(option.get("enabled")) and mode != current_mode
        blocker = "already current mode" if mode == current_mode else option.get("blocker")
        safety = "explicit_user" if mode == "approve" else option.get("safety")
        command = f"agentdeck policy set-mode --mode {mode}"
        if mode == "autonomous":
            command = "agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>"
            if mode != current_mode:
                enabled = False
                blocker = "requires --allow-agent and --max-approvals"
        controls.append(
            _control(
                kind="set_mode",
                label=str(option.get("label", mode)),
                command=command,
                safety=str(safety),
                enabled=enabled,
                blocker=str(blocker) if blocker else None,
            )
        )
    return controls
```

- [ ] **Step 4: Run tests**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "control_mode or autonomous" tests/test_leader_cli.py -k autonomous -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py tests/test_leader_cli.py
git commit -m "Light up autonomous control mode in control_mode_card"
```

---

### Task 7: history humanize + docs + full verification

**Files:**
- Modify: `src/agentdeck/history.py`
- Modify: `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`, `CLAUDE.md`
- Test: `tests/test_history.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_history.py`:

```python
def test_render_history_humanizes_autonomous_auto_run():
    from agentdeck.history import render_history_markdown

    events = [{
        "event_type": "approval_auto_completed",
        "created_at": "2026-07-08T12:00:00+00:00",
        "payload": {"auto_approved": 2, "dispatched": 1, "blocked": 0, "skipped": 1},
    }]
    md = render_history_markdown(events, "demo")
    assert "Auto-approve run · 2 approved, 1 dispatched" in md


def test_render_history_distinguishes_autonomous_approval():
    from agentdeck.history import render_history_markdown

    events = [{
        "event_type": "approval_decided",
        "created_at": "2026-07-08T12:00:00+00:00",
        "payload": {"approval_id": "apv_9", "status": "approved", "source": "autonomous"},
    }]
    md = render_history_markdown(events, "demo")
    assert "Approval auto-approved · apv_9" in md
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_history.py -k "autonomous_auto_run or autonomous_approval" -q`
Expected: FAIL — `approval_auto_completed` not humanized; autonomous `approval_decided` still renders as plain "Approval approved".

- [ ] **Step 3: Implement**

In `src/agentdeck/history.py`, add to `_MILESTONES`:

```python
    "approval_auto_completed": lambda p: ("Auto-approve run", f"{_detail(p, 'auto_approved') or 0} approved, {_detail(p, 'dispatched') or 0} dispatched"),
```

And replace the existing `approval_decided` entry (currently `lambda p: (f"Approval {_detail(p, 'status') or 'decided'}", _detail(p, "approval_id"))`) so autonomous approvals read distinctly:

```python
    "approval_decided": lambda p: (
        "Approval auto-approved" if _detail(p, "source") == "autonomous" else f"Approval {_detail(p, 'status') or 'decided'}",
        _detail(p, "approval_id"),
    ),
```

- [ ] **Step 4: Run test**

Run: `conda run -n agentdeck pytest tests/test_history.py -q`
Expected: PASS

- [ ] **Step 5: Docs**

- README: add a paragraph describing autonomous mode: `agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>` sets the bounded policy; `agentdeck approval auto --confirm` auto-approves allowlisted pending approvals within budget and dispatches them to running agents, fully audited (visible in `agentdeck history`); it does not force-spawn and stops at dispatch.
- `HISTORY.md`: add a top entry summarizing the autonomous mode + approval auto (sub-project 2), referencing the spec/plan.
- `docs/handoff/current-development-state.md`: mark sub-project 2 done; next is sub-project 3 (executing round loop).
- `CLAUDE.md`: update the two rules that state (a) `policy set-mode --mode autonomous` must be rejected and (b) `control_mode_card` autonomous is a disabled unimplemented placeholder — to describe the new bounded, audited autonomous mode and `agentdeck approval auto --confirm`.

- [ ] **Step 6: Full verification**

Run: `conda run -n agentdeck pytest tests/test_autonomy.py tests/test_history.py -q` → PASS
Run: `conda run -n agentdeck pytest -q` → all pass (existing + new)
Run: `conda run -n agentdeck python -m compileall src tests -q` → no errors
Run: `git diff --check` → clean

- [ ] **Step 7: Commit**

```bash
git add src/agentdeck/history.py README.md HISTORY.md docs/handoff/current-development-state.md CLAUDE.md tests/test_history.py
git commit -m "Humanize autonomous auto-run in history and document autonomous mode"
```

---

## Notes for the implementer

- Do NOT push. Commit locally only. No Claude co-author trailer. Run everything in the `agentdeck` conda env.
- Reuse the existing dispatch internals (`_dispatch_approved_approval`, `_approval_dispatch_preview_card`) — do not reinvent dispatch. Read `approval_dispatch_ready_command` for the exact usage.
- If any existing test beyond the ones named here asserts "autonomous ... not implemented", update it to the new behavior (grep for `autonomous` in tests).
- Keep the safety boundary: `approval auto` only auto-approves within the stored allowlist + budget, dispatches only to running agents (no force-spawn), stops at dispatch, and audits every action.
- This is sub-project 2 of 3. Do NOT build the plan→capture→review→release loop (sub-project 3).

## Deferred (tracked, not in this plan)

- Spec §5 bullet 3 (expose `agentdeck approval auto --confirm` as a `control_registry`/operator control so GUI/TUI can render it) is intentionally deferred to the follow-up GUI-lighting pass. Adding a new control kind means touching the strict `control_registry` derivation and `validate_workbench_contract()` per-item rules, which is scope creep for this plan. Spec §25/§81 already flag exhaustive control-registry lighting as out of scope. The command itself is fully functional (Task 5) and audited; only the GUI affordance is deferred. Record this in the handoff's "Next Best Step" as a candidate follow-up.
