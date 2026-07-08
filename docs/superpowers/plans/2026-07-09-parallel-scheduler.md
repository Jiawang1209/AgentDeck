# Parallel scheduler (`agentdeck run-loop --all`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `agentdeck run-loop --all --confirm` — one round-robin wave over all active plans: auto-approve allowlisted pending within ONE shared budget, dispatch approved-and-ready to running panes, SKIP any step whose agent is already busy (dispatched-unreplied), then stop with a per-plan board report. Approval-gated, audited, no force-spawn.

**Architecture:** The scheduler is **additive** — single-plan `run-loop --plan-id` stays unchanged. `_run_loop_all` has its own round-robin wave that reuses the low-level primitives (`select_auto_approvals`, `_approval_dispatch_preview_card`, `_dispatch_approved_approval`, `run_loop_gate`) plus a `_busy_agents` set and a shared budget.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest. Run all via `conda run -n agentdeck ...`.

**Design spec:** `docs/superpowers/specs/2026-07-09-parallel-scheduler-design.md`

---

## File Structure
- Modify `src/agentdeck/contracts.py` — fields, example, payload/response, `validate_run_loop_all_contract`, `CONTRACT_INDEX_SPECS`.
- Modify `src/agentdeck/cli.py` — `_busy_agents`, `_run_loop_all`, `run_loop_command` branch, `--all` subparser, `contract_run_loop_all_command` + subparser, `history` humanize, imports.
- Modify `src/agentdeck/history.py` — humanize `run_loop_all_advanced`.
- Create `docs/contracts/run-loop-all-schema.md`.
- Modify `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`, `tests/test_contracts.py`, `tests/test_agent_cli.py`, `tests/test_history.py`.

---

### Task 1: run-loop-all contract (fields, example, validator)

**Files:** Modify `src/agentdeck/contracts.py`; Test `tests/test_contracts.py`

- [ ] **Step 1: failing test** — append to `tests/test_contracts.py`:

```python
def test_run_loop_all_contract_and_validator_accept_example():
    from pathlib import Path
    from agentdeck.contracts import (
        run_loop_all_contract_response, run_loop_all_example, validate_run_loop_all_contract,
    )
    path = Path("docs/contracts/run-loop-all-schema.md")
    payload = run_loop_all_contract_response(path, include_example=True)
    assert payload["run_loop_all_command"] == "agentdeck run-loop --all --confirm"
    assert "run_loop_all_response_fields" in payload
    assert payload["example_run_loop_all"]["mode"] == "run_loop_all"
    assert validate_run_loop_all_contract(run_loop_all_example())["ok"]


def test_validate_run_loop_all_contract_rejects_bad_budget_and_counts():
    from agentdeck.contracts import run_loop_all_example, validate_run_loop_all_contract
    bad = dict(run_loop_all_example()); bad["active_count"] = 99
    assert not validate_run_loop_all_contract(bad)["ok"]
    bad2 = dict(run_loop_all_example()); bad2["budget"] = {"max_approvals": 5, "used": 1, "remaining": 1}
    assert not validate_run_loop_all_contract(bad2)["ok"]  # used+remaining != max
```

- [ ] **Step 2: run** `conda run -n agentdeck pytest tests/test_contracts.py -k run_loop_all -q` → FAIL (ImportError).

- [ ] **Step 3: implement** in `src/agentdeck/contracts.py`:

```python
RUN_LOOP_ALL_RESPONSE_FIELDS = (
    "ok", "mode", "requires_explicit_user", "safety",
    "plan_count", "active_count", "budget", "totals", "plans",
)

RUN_LOOP_ALL_PLAN_FIELDS = (
    "plan_id", "task", "auto_approved", "dispatched", "blocked",
    "skipped", "skipped_contention", "gate", "next_command",
)


def run_loop_all_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "run_loop_all",
        "requires_explicit_user": True,
        "safety": "delegated",
        "plan_count": 2,
        "active_count": 2,
        "budget": {"max_approvals": 5, "used": 1, "remaining": 4},
        "totals": {"auto_approved": 1, "dispatched": 1, "blocked": 0, "skipped_contention": 1},
        "plans": [
            {
                "plan_id": "pln_a", "task": "demoA", "auto_approved": 1,
                "dispatched": [{"approval_id": "apv_a", "agent_id": "planner",
                                "message_id": "msg_a", "trace_command": "agentdeck trace --id msg_a"}],
                "blocked": [], "skipped": [], "skipped_contention": [],
                "gate": "waiting_for_reply",
                "next_command": "agentdeck capture-reply --agent planner --message-id msg_a",
            },
            {
                "plan_id": "pln_b", "task": "demoB", "auto_approved": 0,
                "dispatched": [], "blocked": [],
                "skipped": [], "skipped_contention": [
                    {"approval_id": "apv_b", "agent_id": "planner", "blocker": "agent busy this wave"}],
                "gate": "needs_human_approval", "next_command": "agentdeck approval list",
            },
        ],
    }


def run_loop_all_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "run_loop_all_command": "agentdeck run-loop --all --confirm",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "run_loop_all_response_fields": list(RUN_LOOP_ALL_RESPONSE_FIELDS),
        "run_loop_all_plan_fields": list(RUN_LOOP_ALL_PLAN_FIELDS),
        "gates": list(RUN_LOOP_STOP_REASONS),
        "run_loop_contract": "agentdeck contract run-loop",
        "plans_contract": "agentdeck contract plans",
    }


def run_loop_all_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = run_loop_all_contract_payload(contract_path)
    if include_example:
        example = run_loop_all_example()
        payload["example"] = True
        payload["example_run_loop_all_response_fields"] = list(example)
        payload["example_run_loop_all_plan_fields"] = list(example["plans"][0])
        payload["example_run_loop_all"] = example
    return payload


def validate_run_loop_all_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in RUN_LOOP_ALL_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing run_loop_all field: {field}")
    if payload.get("mode") != "run_loop_all":
        errors.append(f"run_loop_all.mode must be run_loop_all, got {payload.get('mode')}")
    if payload.get("safety") != "delegated":
        errors.append("run_loop_all.safety must be delegated")
    if payload.get("requires_explicit_user") is not True:
        errors.append("run_loop_all.requires_explicit_user must be true")
    budget = payload.get("budget")
    if not isinstance(budget, dict) or any(k not in budget for k in ("max_approvals", "used", "remaining")):
        errors.append("run_loop_all.budget must have max_approvals/used/remaining")
    elif budget.get("used", 0) + budget.get("remaining", 0) != budget.get("max_approvals"):
        errors.append("run_loop_all.budget used+remaining must equal max_approvals")
    totals = payload.get("totals")
    if not isinstance(totals, dict) or any(
        k not in totals for k in ("auto_approved", "dispatched", "blocked", "skipped_contention")
    ):
        errors.append("run_loop_all.totals must have auto_approved/dispatched/blocked/skipped_contention")
    plans = payload.get("plans")
    if not isinstance(plans, list):
        errors.append("run_loop_all.plans must be a list")
        return {"ok": not errors, "errors": errors}
    if payload.get("active_count") != len(plans):
        errors.append("run_loop_all.active_count must equal len(plans)")
    for index, item in enumerate(plans):
        if not isinstance(item, dict):
            errors.append(f"run_loop_all.plans[{index}] must be an object"); continue
        for field in RUN_LOOP_ALL_PLAN_FIELDS:
            if field not in item:
                errors.append(f"run_loop_all.plans[{index}] missing field: {field}")
        if item.get("gate") not in RUN_LOOP_STOP_REASONS:
            errors.append(f"run_loop_all.plans[{index}].gate invalid")
        for list_field in ("dispatched", "blocked", "skipped", "skipped_contention"):
            if not isinstance(item.get(list_field), list):
                errors.append(f"run_loop_all.plans[{index}].{list_field} must be a list")
    return {"ok": not errors, "errors": errors}
```

Register `CONTRACT_INDEX_SPECS` entry `("run-loop-all", "agentdeck contract run-loop-all", "agentdeck contract run-loop-all --example", "run-loop-all-schema.md")` next to `run-loop`.

- [ ] **Step 4: run** `conda run -n agentdeck pytest tests/test_contracts.py -k run_loop_all -q` → PASS.
- [ ] **Step 5: commit** `git commit -m "Add run-loop-all response contract, example, and validator"`

---

### Task 2: `agentdeck contract run-loop-all` discovery command

**Files:** Modify `src/agentdeck/cli.py`; Test `tests/test_agent_cli.py`

- [ ] **Step 1: failing test** — append:

```python
def test_contract_run_loop_all_discovers_and_is_in_index(tmp_path, monkeypatch, capsys):
    prepare_project(tmp_path, monkeypatch)
    assert cli.main(["contract", "run-loop-all"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_loop_all_command"] == "agentdeck run-loop --all --confirm"
    assert cli.main(["contract", "run-loop-all", "--example"]) == 0
    assert json.loads(capsys.readouterr().out)["example_run_loop_all"]["mode"] == "run_loop_all"
    assert cli.main(["contract", "list"]) == 0
    assert "run-loop-all" in {c["name"] for c in json.loads(capsys.readouterr().out)["contracts"]}
```

- [ ] **Step 2: run** → FAIL (`invalid choice`).
- [ ] **Step 3: implement** — add `run_loop_all_contract_response` to the contracts import. Add near `contract_run_loop_command`:

```python
def contract_run_loop_all_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "run-loop-all-schema.md"
    _print_json(run_loop_all_contract_response(contract_path, include_example=args.example))
    return 0
```

Register next to `contract_run_loop`:

```python
    contract_run_loop_all = contract_subparsers.add_parser("run-loop-all", help="Show run-loop --all parallel-scheduler contract metadata")
    contract_run_loop_all.add_argument("--example", action="store_true", help="Include a GUI-ready run-loop-all example")
    contract_run_loop_all.set_defaults(func=contract_run_loop_all_command)
```

- [ ] **Step 4: run** → PASS. **Step 5: commit** `git commit -m "Add agentdeck contract run-loop-all discovery command"`

---

### Task 3: `--all` wiring + `_busy_agents` + `_run_loop_all` engine

**Files:** Modify `src/agentdeck/cli.py`; Test `tests/test_agent_cli.py`

- [ ] **Step 1: failing tests** — append (reuse `prepare_project`, `bind_agent`, `FakeTmuxBackend`, `StateStore`, `_seed_named_plan`, `_enable_autonomous`):

```python
def test_run_loop_all_requires_confirm_mode_and_one_of_plan_or_all(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    assert cli.main(["run-loop", "--all"]) == 1          # no --confirm
    assert "confirm" in capsys.readouterr().err
    assert cli.main(["run-loop", "--all", "--confirm"]) == 1   # not autonomous
    assert "autonomous mode is not enabled" in capsys.readouterr().err
    _enable_autonomous(root, monkeypatch, capsys, ["planner"], 5)
    assert cli.main(["run-loop", "--confirm"]) == 1       # neither plan-id nor all
    assert "plan-id" in capsys.readouterr().err.lower()
    assert cli.main(["run-loop", "--all", "--plan-id", "pln_x", "--confirm"]) == 1  # both
    assert "not both" in capsys.readouterr().err.lower() or "either" in capsys.readouterr().err.lower()


def test_run_loop_all_round_robin_dispatches_and_skips_on_contention(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    _enable_autonomous(root, monkeypatch, capsys, ["planner"], 5)
    # two plans whose first step targets the SAME agent (planner)
    _seed_named_plan(root, "pln_a", "demoA", agent_id="planner")
    _seed_named_plan(root, "pln_b", "demoB", agent_id="planner")

    assert cli.main(["run-loop", "--all", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_all"
    assert payload["active_count"] == 2
    # only the first plan (creation order) dispatched to planner; the second skipped on contention
    assert payload["totals"]["dispatched"] == 1
    assert payload["totals"]["skipped_contention"] == 1
    a = next(p for p in payload["plans"] if p["plan_id"] == "pln_a")
    b = next(p for p in payload["plans"] if p["plan_id"] == "pln_b")
    assert len(a["dispatched"]) == 1
    assert len(b["skipped_contention"]) == 1
    # only one message actually sent to the pane this wave
    assert len(fake.sent) == 1


def test_run_loop_all_shares_budget_across_plans(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    _enable_autonomous(root, monkeypatch, capsys, ["planner"], 1)   # budget = 1 across the wave
    _seed_named_plan(root, "pln_a", "demoA", agent_id="planner")
    _seed_named_plan(root, "pln_b", "demoB", agent_id="planner")

    assert cli.main(["run-loop", "--all", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["auto_approved"] == 1       # only one auto-approval spent
    assert payload["budget"] == {"max_approvals": 1, "used": 1, "remaining": 0}
```

- [ ] **Step 2: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k run_loop_all -q` → FAIL.

- [ ] **Step 3: implement** in `src/agentdeck/cli.py` — add `validate_run_loop_all_contract` to the contracts import. Add helpers near `run_loop_command`:

```python
def _busy_agents(store: StateStore) -> set[str]:
    state = store.load()
    replied = {r.get("message_id") for r in state.get("replies", []) if isinstance(r, dict)}
    busy: set[str] = set()
    for plan in store.list_plans():
        if not isinstance(plan, dict):
            continue
        try:
            status = store.plan_status(str(plan.get("plan_id", "")))
        except KeyError:
            continue
        for step in status.get("steps", []):
            if (
                isinstance(step, dict)
                and step.get("approval_status") == "dispatched"
                and step.get("message_id")
                and step.get("message_id") not in replied
            ):
                busy.add(step.get("agent_id"))
    return busy


def _run_loop_all(config: ProjectConfig, store: StateStore) -> int:
    policy = config.autonomous
    backend = TmuxBackend()
    busy = _busy_agents(store)
    budget_remaining = policy.max_approvals
    plan_results: list[dict[str, object]] = []
    for plan in store.list_plans():
        if not isinstance(plan, dict):
            continue
        plan_id = str(plan.get("plan_id", ""))
        gate0, _ = run_loop_gate(store.leader_review(plan_id), False, plan_id)
        if gate0 == "complete":
            continue
        pending = [
            a for a in store.load().get("approvals", [])
            if isinstance(a, dict) and a.get("plan_id") == plan_id and a.get("status") == "pending"
        ]
        selected, skipped = select_auto_approvals(pending, policy.allowed_agents, budget_remaining)
        for approval in selected:
            aid = str(approval.get("approval_id", ""))
            store.decide_approval(aid, "approved", reason="autonomous")
            store.append_event(EventRecord.create("approval_decided", {
                "approval_id": aid, "status": "approved", "source": "autonomous"}))
        budget_remaining -= len(selected)
        dispatched: list[dict[str, object]] = []
        blocked: list[dict[str, object]] = []
        skipped_contention: list[dict[str, object]] = []
        has_error = False
        approved_now = [
            a for a in store.load().get("approvals", [])
            if isinstance(a, dict) and a.get("plan_id") == plan_id and a.get("status") == "approved"
        ]
        for approval in approved_now:
            aid = str(approval.get("approval_id", ""))
            agent_id = approval.get("agent_id")
            if agent_id in busy:
                skipped_contention.append({"approval_id": aid, "agent_id": agent_id, "blocker": "agent busy this wave"})
                continue
            preview = _approval_dispatch_preview_card(approval, config, store)
            if preview.get("blocker"):
                blocked.append({"approval_id": aid, "agent_id": agent_id, "blocker": preview.get("blocker")})
                continue
            try:
                result = _dispatch_approved_approval(approval, approval_id=aid, config=config, store=store, backend=backend)
                dispatched.append({"approval_id": aid, "agent_id": result["agent_id"],
                                   "message_id": result["message_id"], "trace_command": result["trace_command"]})
                busy.add(agent_id)
            except Exception as exc:
                has_error = True
                store.append_event(EventRecord.create("run_loop_dispatch_failed", {"approval_id": aid, "detail": str(exc)}))
        gate, next_command = run_loop_gate(store.leader_review(plan_id), has_error, plan_id)
        plan_results.append({
            "plan_id": plan_id, "task": plan.get("task"),
            "auto_approved": len(selected), "dispatched": dispatched,
            "blocked": blocked,
            "skipped": [{"approval_id": s.get("approval_id"), "agent_id": s.get("agent_id"), "reason": s.get("reason")} for s in skipped],
            "skipped_contention": skipped_contention,
            "gate": gate, "next_command": next_command,
        })
    used = policy.max_approvals - budget_remaining
    totals = {
        "auto_approved": sum(int(p["auto_approved"]) for p in plan_results),
        "dispatched": sum(len(p["dispatched"]) for p in plan_results),
        "blocked": sum(len(p["blocked"]) for p in plan_results),
        "skipped_contention": sum(len(p["skipped_contention"]) for p in plan_results),
    }
    store.append_event(EventRecord.create("run_loop_all_advanced", {"plans_advanced": len(plan_results), **totals}))
    payload = {
        "ok": True, "mode": "run_loop_all", "requires_explicit_user": True, "safety": "delegated",
        "plan_count": len([p for p in store.list_plans() if isinstance(p, dict)]),
        "active_count": len(plan_results),
        "budget": {"max_approvals": policy.max_approvals, "used": used, "remaining": budget_remaining},
        "totals": totals, "plans": plan_results,
    }
    validation = validate_run_loop_all_contract(payload)
    if not validation["ok"]:
        print("run-loop --all contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0
```

Then branch `run_loop_command` right after the existing `--confirm` + autonomous-mode checks, BEFORE `plan_id = args.plan_id`:

```python
    if getattr(args, "all", False) and args.plan_id:
        print("run-loop takes either --plan-id or --all, not both", file=sys.stderr)
        return 1
    if getattr(args, "all", False):
        return _run_loop_all(config, store)
    if not args.plan_id:
        print("run-loop requires --plan-id or --all", file=sys.stderr)
        return 1
```

Update the subparser (cli.py ~13126): change `--plan-id` to `required=False` (default `None`) and add `run_loop.add_argument("--all", action="store_true", help="Drive every active plan one round-robin wave")`.

- [ ] **Step 4: run** `conda run -n agentdeck pytest tests/test_agent_cli.py -k "run_loop_all or run_loop" -q` → PASS (new + existing single-plan run-loop tests all green).
- [ ] **Step 5: commit** `git commit -m "Add agentdeck run-loop --all parallel scheduler"`

---

### Task 4: history humanize + docs + full verification

**Files:** Modify `src/agentdeck/history.py`, docs; Test `tests/test_history.py`

- [ ] **Step 1: failing test** — append to `tests/test_history.py`:

```python
def test_render_history_humanizes_run_loop_all():
    from agentdeck.history import render_history_markdown
    events = [{"event_type": "run_loop_all_advanced", "created_at": "2026-07-09T12:00:00+00:00",
               "payload": {"plans_advanced": 2, "auto_approved": 1, "dispatched": 1, "blocked": 0, "skipped_contention": 1}}]
    md = render_history_markdown(events, "demo")
    assert "Parallel wave · 2 plans, 1 dispatched" in md
```

- [ ] **Step 2: run** → FAIL. **Step 3: implement** — add to `_MILESTONES` in `src/agentdeck/history.py`:

```python
    "run_loop_all_advanced": lambda p: (
        "Parallel wave",
        f"{_detail(p, 'plans_advanced') or 0} plans, {_detail(p, 'dispatched') or 0} dispatched",
    ),
```

- [ ] **Step 4: docs**
- Create `docs/contracts/run-loop-all-schema.md` (mirror `run-loop-schema.md`): document `agentdeck run-loop --all`, `RUN_LOOP_ALL_RESPONSE_FIELDS`, `RUN_LOOP_ALL_PLAN_FIELDS`, round-robin + shared budget + skip-on-contention + one-wave, and the safety envelope (same as run-loop).
- `CLAUDE.md`: add a rule for `agentdeck run-loop --all --confirm` (round-robin one wave over active plans, shared `max_approvals` budget, skip agents busy = dispatched-unreplied, requires `--confirm` + autonomous mode, never force-spawns/captures, audited via `run_loop_all_advanced`; `agentdeck contract run-loop-all` is its discovery entry).
- `README.md`: one line.
- `HISTORY.md`: newest-first top entry (multi-plan lane final slice: parallel scheduler).
- `docs/handoff/current-development-state.md`: **remove the top ⏸ 需要你决策 section** (the fork is now resolved and built); update the single "Next Best Step" — the whole multi-plan lane (visibility + parallel scheduler) is complete; the next direction is a fresh product fork (GUI client / Skill marketplace / remote-MCP) that needs the human.

- [ ] **Step 5: full verification**
- `conda run -n agentdeck pytest tests/test_contracts.py -k run_loop_all tests/test_agent_cli.py -k "run_loop_all or contract_run_loop_all" tests/test_history.py -k run_loop_all -q` → PASS
- `conda run -n agentdeck pytest -q` → all pass (baseline 698 + new)
- `conda run -n agentdeck python -m compileall src tests -q` → clean
- `git diff --check` → clean
- [ ] **Step 6: commit** `git commit -m "Document run-loop --all parallel scheduler; resolve multi-plan fork"`

---

## Notes for the implementer
- Do NOT push. Commit locally only. No Claude co-author trailer. conda `agentdeck` env.
- **Single-plan `run-loop --plan-id` must stay byte-for-byte unchanged** — the scheduler is separate additive code. Do NOT route single-plan through the new engine. Existing run-loop tests must stay green untouched.
- Reuse the primitives only: `select_auto_approvals`, `_approval_dispatch_preview_card`, `_dispatch_approved_approval`, `run_loop_gate`, `store.leader_review` — all already in cli.py/imported.
- `_seed_named_plan` (added in the plan-board slice) takes an `agent_id`; both contention-test plans use `agent_id="planner"` so they collide on the same agent. Confirm `FakeTmuxBackend` records sends in `.sent`; if the attribute differs, read the class and adjust the assertion.
- Safety envelope is identical to `run-loop`: `--confirm` + autonomous required; only allowlisted+budget auto-approve; only running, non-busy panes; no force-spawn; no reply capture; one wave then stop; every action audited.
- This completes the multi-plan lane. Do NOT start the next big fork (GUI/skill-market/remote) — leave it for the human.
