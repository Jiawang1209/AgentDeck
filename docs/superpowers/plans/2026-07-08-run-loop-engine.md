# Executing Round Loop (`agentdeck run-loop`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `agentdeck run-loop --plan-id <id> --confirm` — the write counterpart to the read-only `agentdeck loop`. It performs one sanctioned autonomous wave for a plan (auto-approve allowlisted pending approvals within budget + dispatch approved-and-ready ones to running panes), then reuses `leader review` to diagnose the resulting human gate and stops there with an explicit next command. Fully audited.

**Architecture:** A pure gate-diagnosis function in `autonomy.py` maps `leader review` state → `(stopped_reason, next_command)`. The engine in `cli.py` reuses `select_auto_approvals` (sub-project 2) + the existing dispatch internals (`_approval_dispatch_preview_card`, `_dispatch_approved_approval`) for the wave, then the pure function for the stop. A contract (`validate_run_loop_contract` + `agentdeck contract run-loop`) guards the output, per project convention.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest. Run all commands via `conda run -n agentdeck ...`.

**Design spec:** `docs/superpowers/specs/2026-07-08-run-loop-engine-design.md`

---

## File Structure

- Modify `src/agentdeck/autonomy.py` — add pure `run_loop_gate(review, has_error, plan_id)`.
- Modify `src/agentdeck/contracts.py` — `RUN_LOOP_RESPONSE_FIELDS`, `run_loop_example`, `run_loop_contract_payload/response`, `validate_run_loop_contract`, `CONTRACT_INDEX_SPECS` entry.
- Modify `src/agentdeck/cli.py` — `run_loop_command`, `contract_run_loop_command`, both subparsers.
- Modify `src/agentdeck/history.py` — humanize `run_loop_advanced`.
- Create `docs/contracts/run-loop-schema.md`.
- Modify `tests/test_autonomy.py`, `tests/test_agent_cli.py`, `tests/test_contracts.py`, `tests/test_history.py`.
- Modify `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`, `CLAUDE.md`.

---

### Task 1: Pure gate diagnosis `run_loop_gate`

Maps the post-wave situation to a `(stopped_reason, next_command)`. Reuses `store.leader_review(plan_id)`'s `next_action` values: `dispatch_approved` (an approved step could not be dispatched → agent not running → blocked), `wait_for_approval`, `wait_for_reply`, `summarize`.

**Files:**
- Modify: `src/agentdeck/autonomy.py`
- Test: `tests/test_autonomy.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_autonomy.py`:

```python
def test_run_loop_gate_maps_review_next_action_to_stop_and_command():
    from agentdeck.autonomy import run_loop_gate

    # error takes priority over everything
    assert run_loop_gate({"next_action": "wait_for_reply"}, True, "pln_1")[0] == "error"
    assert run_loop_gate({"next_action": "wait_for_reply"}, True, "pln_1")[1] == "agentdeck plan status --plan-id pln_1"

    # approved-but-undispatched after the wave == blocked on a non-running agent -> go spawn it
    reason, cmd = run_loop_gate({"next_action": "dispatch_approved", "agent_id": "coder"}, False, "pln_1")
    assert reason == "blocked"
    assert cmd == "agentdeck agent spawn --agent coder"

    # non-auto pending approval remains -> human approval
    reason, cmd = run_loop_gate({"next_action": "wait_for_approval"}, False, "pln_1")
    assert reason == "needs_human_approval"
    assert cmd == "agentdeck approval list"

    # dispatched, no reply yet -> stop and hand the explicit capture-reply command
    reason, cmd = run_loop_gate({"next_action": "wait_for_reply", "agent_id": "planner", "message_id": "msg_7"}, False, "pln_1")
    assert reason == "waiting_for_reply"
    assert cmd == "agentdeck capture-reply --agent planner --message-id msg_7"

    # all dispatched steps have replies -> complete
    reason, cmd = run_loop_gate({"next_action": "summarize"}, False, "pln_1")
    assert reason == "complete"
    assert cmd == "agentdeck leader summary --plan-id pln_1"

    # nothing actionable
    reason, cmd = run_loop_gate({"next_action": "unknown"}, False, "pln_1")
    assert reason == "idle"
    assert cmd == "agentdeck run --plan-id pln_1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_autonomy.py -k run_loop_gate -q`
Expected: FAIL — `ImportError: cannot import name 'run_loop_gate'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/agentdeck/autonomy.py`:

```python
def run_loop_gate(
    review: dict[str, Any],
    has_error: bool,
    plan_id: str,
) -> tuple[str, str]:
    """Diagnose where a plan is stuck after one run-loop wave.

    Returns (stopped_reason, next_command) — a read-only, explicit next step
    for the human. Priority: error first, then the leader_review next_action.
    """
    if has_error:
        return "error", f"agentdeck plan status --plan-id {plan_id}"
    next_action = review.get("next_action")
    if next_action == "dispatch_approved":
        # an approved step survived the wave -> its agent has no running pane
        return "blocked", f"agentdeck agent spawn --agent {review.get('agent_id')}"
    if next_action == "wait_for_approval":
        return "needs_human_approval", "agentdeck approval list"
    if next_action == "wait_for_reply":
        return (
            "waiting_for_reply",
            f"agentdeck capture-reply --agent {review.get('agent_id')} --message-id {review.get('message_id')}",
        )
    if next_action == "summarize":
        return "complete", f"agentdeck leader summary --plan-id {plan_id}"
    return "idle", f"agentdeck run --plan-id {plan_id}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_autonomy.py -q`
Expected: PASS (all autonomy tests, including the new one)

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/autonomy.py tests/test_autonomy.py
git commit -m "Add pure run_loop_gate diagnosis for the executing loop"
```

---

### Task 2: Run-loop contract (fields, example, payload/response, validator)

Mirror the existing loop/run contract scaffolding in `src/agentdeck/contracts.py`.

**Files:**
- Modify: `src/agentdeck/contracts.py`
- Test: `tests/test_contracts.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_contracts.py`:

```python
def test_run_loop_contract_payload_and_validator_accept_example():
    from pathlib import Path
    from agentdeck.contracts import (
        run_loop_contract_response,
        run_loop_example,
        validate_run_loop_contract,
    )

    path = Path("docs/contracts/run-loop-schema.md")
    payload = run_loop_contract_response(path, include_example=True)
    assert payload["run_loop_command"] == "agentdeck run-loop --plan-id <id> --confirm"
    assert "run_loop_response_fields" in payload
    assert payload["example_run_loop"]["mode"] == "run_loop"

    result = validate_run_loop_contract(run_loop_example())
    assert result["ok"], result["errors"]


def test_validate_run_loop_contract_rejects_bad_mode_and_reason():
    from agentdeck.contracts import run_loop_example, validate_run_loop_contract

    bad = dict(run_loop_example())
    bad["mode"] = "run"
    assert not validate_run_loop_contract(bad)["ok"]

    bad2 = dict(run_loop_example())
    bad2["stopped_reason"] = "made_up"
    assert not validate_run_loop_contract(bad2)["ok"]

    bad3 = dict(run_loop_example())
    bad3["safety"] = "inspect"
    assert not validate_run_loop_contract(bad3)["ok"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_contracts.py -k run_loop -q`
Expected: FAIL — `ImportError: cannot import name 'run_loop_contract_response'`

- [ ] **Step 3: Write minimal implementation**

In `src/agentdeck/contracts.py`:

(a) Add the fields tuple near `RUN_START_RESPONSE_FIELDS`:

```python
RUN_LOOP_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "plan_id",
    "requires_explicit_user",
    "safety",
    "auto_approved",
    "dispatched",
    "blocked",
    "skipped",
    "stopped_reason",
    "next_command",
    "policy",
)

RUN_LOOP_STOP_REASONS = (
    "error",
    "blocked",
    "needs_human_approval",
    "waiting_for_reply",
    "complete",
    "idle",
)
```

(b) Add the example + payload/response (near `run_start_contract_payload`):

```python
def run_loop_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "run_loop",
        "plan_id": "pln_example",
        "requires_explicit_user": True,
        "safety": "delegated",
        "auto_approved": 1,
        "dispatched": [
            {
                "approval_id": "apv_example",
                "agent_id": "planner",
                "message_id": "msg_example",
                "trace_command": "agentdeck trace --id msg_example",
            }
        ],
        "blocked": [],
        "skipped": [
            {"approval_id": "apv_other", "agent_id": "reviewer", "reason": "agent not in allowlist"}
        ],
        "stopped_reason": "waiting_for_reply",
        "next_command": "agentdeck capture-reply --agent planner --message-id msg_example",
        "policy": {"allowed_agents": ["planner"], "max_approvals": 3},
    }


def run_loop_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "run_loop_command": "agentdeck run-loop --plan-id <id> --confirm",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "run_loop_response_fields": list(RUN_LOOP_RESPONSE_FIELDS),
        "stop_reasons": list(RUN_LOOP_STOP_REASONS),
        "loop_contract": "agentdeck contract loop",
        "approvals_contract": "agentdeck contract approvals",
        "project_view_contract": "agentdeck contract project-view",
    }


def run_loop_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = run_loop_contract_payload(contract_path)
    if include_example:
        example = run_loop_example()
        payload["example"] = True
        payload["example_run_loop_response_fields"] = list(example)
        payload["example_run_loop"] = example
    return payload
```

(c) Add the validator (near `validate_run_start_contract`):

```python
def validate_run_loop_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in RUN_LOOP_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing run_loop field: {field}")
    if payload.get("mode") != "run_loop":
        errors.append(f"run_loop.mode must be run_loop, got {payload.get('mode')}")
    if payload.get("safety") != "delegated":
        errors.append("run_loop.safety must be delegated")
    if payload.get("requires_explicit_user") is not True:
        errors.append("run_loop.requires_explicit_user must be true")
    if payload.get("stopped_reason") not in RUN_LOOP_STOP_REASONS:
        errors.append(f"run_loop.stopped_reason must be one of {RUN_LOOP_STOP_REASONS}")
    if not isinstance(payload.get("next_command"), str) or not payload.get("next_command"):
        errors.append("run_loop.next_command must be a non-empty string")
    for list_field in ("dispatched", "blocked", "skipped"):
        if not isinstance(payload.get(list_field), list):
            errors.append(f"run_loop.{list_field} must be a list")
    return {"ok": not errors, "errors": errors}
```

(d) Register in `CONTRACT_INDEX_SPECS` (add a tuple alongside the `"run"` entry):

```python
    (
        "run-loop",
        "agentdeck contract run-loop",
        "agentdeck contract run-loop --example",
        "run-loop-schema.md",
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_contracts.py -k run_loop -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/contracts.py tests/test_contracts.py
git commit -m "Add run-loop response contract, example, and validator"
```

---

### Task 3: `agentdeck contract run-loop` discovery command

**Files:**
- Modify: `src/agentdeck/cli.py` (command + subparser + import)
- Test: `tests/test_agent_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_cli.py`:

```python
def test_contract_run_loop_discovers_schema_for_gui_clients(tmp_path, monkeypatch, capsys):
    prepare_project(tmp_path, monkeypatch)
    assert cli.main(["contract", "run-loop"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_loop_command"] == "agentdeck run-loop --plan-id <id> --confirm"
    assert "run_loop_response_fields" in payload

    assert cli.main(["contract", "run-loop", "--example"]) == 0
    example_payload = json.loads(capsys.readouterr().out)
    assert example_payload["example_run_loop"]["mode"] == "run_loop"


def test_contract_list_includes_run_loop(tmp_path, monkeypatch, capsys):
    prepare_project(tmp_path, monkeypatch)
    assert cli.main(["contract", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    names = {c["name"] for c in payload["contracts"]}
    assert "run-loop" in names
```

Note: if `test_contract_list_includes_run_loop`'s field access (`c["name"]`) doesn't match the real `contract list` item shape, read `contract_list_command`'s output and adjust the assertion to the real key.

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k run_loop -q`
Expected: FAIL — `invalid choice: 'run-loop'`

- [ ] **Step 3: Write minimal implementation**

In `src/agentdeck/cli.py`, add `run_loop_contract_response` to the `from .contracts import (...)` block. Add the command near `contract_run_command` (cli.py:4187):

```python
def contract_run_loop_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "run-loop-schema.md"
    payload = run_loop_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0
```

Register the subparser next to `contract_run` (cli.py:12928):

```python
    contract_run_loop = contract_subparsers.add_parser(
        "run-loop",
        help="Show run-loop executing-engine contract discovery metadata",
    )
    contract_run_loop.add_argument("--example", action="store_true", help="Include a GUI-ready run-loop example")
    contract_run_loop.set_defaults(func=contract_run_loop_command)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k run_loop -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py
git commit -m "Add agentdeck contract run-loop discovery command"
```

---

### Task 4: The engine — `agentdeck run-loop`

**Files:**
- Modify: `src/agentdeck/cli.py` (command + subparser + imports)
- Test: `tests/test_agent_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_cli.py` (reuse existing `prepare_project`, `bind_agent`, `FakeTmuxBackend`, `StateStore`, and the `_seed_pending_approval` helper added in the autonomous-mode work):

```python
def _enable_autonomous(root, monkeypatch, capsys, allow, budget):
    cli.main(["policy", "set-mode", "--mode", "autonomous", "--confirm",
              *sum((["--allow-agent", a] for a in allow), []), "--max-approvals", str(budget)])
    capsys.readouterr()


def test_run_loop_requires_confirm_mode_and_plan(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    before = StateStore(root).load()
    # not autonomous yet, and no --confirm
    assert cli.main(["run-loop", "--plan-id", "pln_x"]) == 1
    assert "confirm" in capsys.readouterr().err
    # confirm but not autonomous
    assert cli.main(["run-loop", "--plan-id", "pln_x", "--confirm"]) == 1
    assert "autonomous mode is not enabled" in capsys.readouterr().err
    # autonomous but unknown plan
    _enable_autonomous(root, monkeypatch, capsys, ["planner"], 3)
    assert cli.main(["run-loop", "--plan-id", "pln_missing", "--confirm"]) == 1
    assert "unknown plan" in capsys.readouterr().err.lower()
    assert StateStore(root).load()["approvals"] == before.get("approvals", [])


def test_run_loop_auto_approves_dispatches_and_stops_at_reply_gate(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    _enable_autonomous(root, monkeypatch, capsys, ["planner"], 5)

    # a plan with one planner step + its pending approval
    plan_id = _seed_plan_with_pending_approval(root, agent_id="planner")  # see helper below

    exit_code = cli.main(["run-loop", "--plan-id", plan_id, "--confirm"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop"
    assert payload["plan_id"] == plan_id
    assert payload["auto_approved"] == 1
    assert payload["dispatched"][0]["agent_id"] == "planner"
    assert payload["stopped_reason"] == "waiting_for_reply"
    assert payload["next_command"].startswith("agentdeck capture-reply --agent planner")

    types = [e["event_type"] for e in StateStore(root).list_events(limit=30)]
    assert "approval_decided" in types
    assert "approval_dispatched" in types
    assert "run_loop_advanced" in types


def test_run_loop_stops_at_human_approval_for_non_allowlisted(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    _enable_autonomous(root, monkeypatch, capsys, ["planner"], 5)  # planner allowed, reviewer not

    plan_id = _seed_plan_with_pending_approval(root, agent_id="reviewer")

    exit_code = cli.main(["run-loop", "--plan-id", plan_id, "--confirm"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["auto_approved"] == 0
    assert payload["stopped_reason"] == "needs_human_approval"
    assert payload["next_command"] == "agentdeck approval list"
    assert any(s["agent_id"] == "reviewer" for s in payload["skipped"])
```

Add a helper near the other seed helpers in `tests/test_agent_cli.py`. It must create a saved plan record AND a pending approval carrying that `plan_id`, `step`, `agent_id`, and matching `role`, so `store.plan_status`/`leader_review` resolve. Read how an existing test seeds a plan + approval (grep `create_approvals_from_plan` / `plan_status` usage in the test file) and mirror it. If the project already exposes `store.create_approvals_from_plan(plan_id)` after saving a plan record, prefer that path:

```python
def _seed_plan_with_pending_approval(root, agent_id):
    """Save a one-step plan for `agent_id` and materialize its pending approval.
    Return the plan_id. Mirror the plan-record shape used by existing plan tests
    (grep `plan_status(` or `create_approvals_from_plan(` in this file for the
    exact fields: goal/summary/steps[].step/agent_id/role/task/risk/requires_approval)."""
    store = StateStore(root)
    state = store.load()
    plan_id = "pln_loop_1"
    role = next(a.role for a in load_config(root).agents if a.agent_id == agent_id)
    state.setdefault("plans", []).append({
        "plan_id": plan_id, "goal": "g", "summary": "s",
        "steps": [{"step": 1, "agent_id": agent_id, "role": role, "task": "do", "risk": "low", "requires_approval": True}],
        "created_at": "2026-07-04T00:00:00+00:00",
    })
    store.save(state)
    store.create_approvals_from_plan(plan_id)
    return plan_id
```

If the real plan-record/approval shape differs, adjust this helper to match the fields `plan_status`/`create_approvals_from_plan` actually require (read `src/agentdeck/state.py` `plan_status` and `create_approvals_from_plan`). The test intent — one planner/reviewer step with a pending approval — must hold.

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "run_loop_requires or run_loop_auto or run_loop_stops" -q`
Expected: FAIL — `invalid choice: 'run-loop'`

- [ ] **Step 3: Write minimal implementation**

In `src/agentdeck/cli.py`, add `run_loop_gate` to the `from .autonomy import ...` line (currently imports `select_auto_approvals`) and `validate_run_loop_contract` to the contracts import. Add the command near `approval_auto_command`:

```python
def run_loop_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if not args.confirm:
        print("run-loop requires --confirm", file=sys.stderr)
        return 1
    if config.leader.approval_mode != "autonomous":
        print(
            "autonomous mode is not enabled; run agentdeck policy set-mode --mode autonomous "
            "--confirm --allow-agent <id> --max-approvals <N>",
            file=sys.stderr,
        )
        return 1
    plan_id = args.plan_id
    try:
        store.plan_status(plan_id)
    except KeyError:
        print(f"unknown plan: {plan_id}", file=sys.stderr)
        return 1

    policy = config.autonomous
    plan_approvals = [
        a for a in store.load().get("approvals", [])
        if isinstance(a, dict) and a.get("plan_id") == plan_id
    ]
    pending = [a for a in plan_approvals if a.get("status") == "pending"]
    selected, skipped = select_auto_approvals(pending, policy.allowed_agents, policy.max_approvals)

    # 1) auto-approve the allowlisted, budget-bounded pending approvals
    for approval in selected:
        approval_id = str(approval.get("approval_id", ""))
        store.decide_approval(approval_id, "approved", reason="autonomous")
        store.append_event(EventRecord.create("approval_decided", {
            "approval_id": approval_id, "status": "approved", "source": "autonomous",
        }))

    # 2) dispatch every approved-and-ready approval for this plan (auto- or human-approved)
    backend = TmuxBackend()
    dispatched: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []
    has_error = False
    approved_now = [
        a for a in store.load().get("approvals", [])
        if isinstance(a, dict) and a.get("plan_id") == plan_id and a.get("status") == "approved"
    ]
    for approval in approved_now:
        approval_id = str(approval.get("approval_id", ""))
        preview = _approval_dispatch_preview_card(approval, config, store)
        if preview.get("blocker"):
            blocked.append({
                "approval_id": approval_id,
                "agent_id": approval.get("agent_id"),
                "blocker": preview.get("blocker"),
            })
            continue
        try:
            result = _dispatch_approved_approval(
                approval, approval_id=approval_id, config=config, store=store, backend=backend
            )
            dispatched.append({
                "approval_id": approval_id,
                "agent_id": result["agent_id"],
                "message_id": result["message_id"],
                "trace_command": result["trace_command"],
            })
        except Exception as exc:  # dispatch failed — stop at the error gate
            has_error = True
            store.append_event(EventRecord.create("run_loop_dispatch_failed", {
                "approval_id": approval_id, "detail": str(exc),
            }))

    # 3) diagnose the resulting gate via leader review (single source of truth)
    review = store.leader_review(plan_id)
    stopped_reason, next_command = run_loop_gate(review, has_error, plan_id)

    store.append_event(EventRecord.create("run_loop_advanced", {
        "plan_id": plan_id,
        "auto_approved": len(selected),
        "dispatched": len(dispatched),
        "blocked": len(blocked),
        "skipped": len(skipped),
        "stopped_reason": stopped_reason,
    }))

    payload = {
        "ok": True,
        "mode": "run_loop",
        "plan_id": plan_id,
        "requires_explicit_user": True,
        "safety": "delegated",
        "auto_approved": len(selected),
        "dispatched": dispatched,
        "blocked": blocked,
        "skipped": [
            {"approval_id": s.get("approval_id"), "agent_id": s.get("agent_id"), "reason": s.get("reason")}
            for s in skipped
        ],
        "stopped_reason": stopped_reason,
        "next_command": next_command,
        "policy": {"allowed_agents": list(policy.allowed_agents), "max_approvals": policy.max_approvals},
    }
    validation = validate_run_loop_contract(payload)
    if not validation["ok"]:
        print("run-loop contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        store.append_event(EventRecord.create("run_loop_contract_failed", {"errors": validation["errors"]}))
        return 1
    _print_json(payload)
    return 0
```

Register the subparser near the `run` parser (cli.py:12670):

```python
    run_loop = subparsers.add_parser("run-loop", help="Drive one plan forward within the autonomous policy, then stop at the next human gate")
    run_loop.add_argument("--plan-id", required=True, help="Plan to drive forward")
    run_loop.add_argument("--confirm", action="store_true", help="Explicitly confirm the autonomous wave")
    run_loop.set_defaults(func=run_loop_command)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "run_loop" -q`
Expected: PASS (the three engine tests + the two discovery tests from Task 3)

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py
git commit -m "Add agentdeck run-loop executing engine"
```

---

### Task 5: History humanize `run_loop_advanced`

**Files:**
- Modify: `src/agentdeck/history.py`
- Test: `tests/test_history.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_history.py`:

```python
def test_render_history_humanizes_run_loop_advance():
    from agentdeck.history import render_history_markdown

    events = [{
        "event_type": "run_loop_advanced",
        "created_at": "2026-07-08T12:00:00+00:00",
        "payload": {"plan_id": "pln_1", "auto_approved": 1, "dispatched": 1,
                    "blocked": 0, "skipped": 1, "stopped_reason": "waiting_for_reply"},
    }]
    md = render_history_markdown(events, "demo")
    assert "Run-loop advanced · 1 dispatched, stopped: waiting_for_reply" in md
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_history.py -k run_loop_advance -q`
Expected: FAIL — line absent.

- [ ] **Step 3: Implement**

In `src/agentdeck/history.py`, add to `_MILESTONES`:

```python
    "run_loop_advanced": lambda p: (
        "Run-loop advanced",
        f"{_detail(p, 'dispatched') or 0} dispatched, stopped: {_detail(p, 'stopped_reason') or 'unknown'}",
    ),
```

- [ ] **Step 4: Run test**

Run: `conda run -n agentdeck pytest tests/test_history.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/history.py tests/test_history.py
git commit -m "Humanize run_loop_advanced in history timeline"
```

---

### Task 6: Contract doc + README + HISTORY + handoff + CLAUDE.md + full verification

**Files:**
- Create: `docs/contracts/run-loop-schema.md`
- Modify: `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`, `CLAUDE.md`

- [ ] **Step 1: Write the contract doc**

Create `docs/contracts/run-loop-schema.md` mirroring `docs/contracts/run-schema.md`'s structure. It must document: the discovery command `agentdeck contract run-loop`, `RUN_LOOP_RESPONSE_FIELDS`, the `stop_reasons` enum (error/blocked/needs_human_approval/waiting_for_reply/complete/idle), the safety boundary (requires `--confirm` + autonomous mode; only auto-approves within allowlist+budget; dispatches only to running panes; never captures replies or infers completion; stops at the human gate), and that `run-loop` is the write counterpart to the read-only `agentdeck loop`. Keep prose factual and consistent with the spec.

- [ ] **Step 2: Docs updates**

- README: add a `agentdeck run-loop --plan-id <id> --confirm` entry — it drives one plan forward within the autonomous policy (auto-approve allowlisted pending + dispatch to running panes), then stops at the next human gate with an explicit next command; requires autonomous mode; never captures replies or force-spawns; fully audited into `agentdeck history`. Note the distinction from the read-only `agentdeck loop`.
- `HISTORY.md`: add a top (newest-first) entry for sub-project 3 (the executing round loop), referencing the spec/plan.
- `docs/handoff/current-development-state.md`: update the "Next Best Step" — all three autonomous-mode sub-projects are done; the autonomous-mode goal (audit/HISTORY gate + bounded autonomous switch + executing loop) is complete. List the remaining deferred follow-ups (workbench control-registry lighting for `approval auto` and `run-loop`; a `leader chat` intent for `run-loop`; `learn review` workbench card; `dashboard --watch`) as candidate next slices. Keep it to a single authoritative "Next Best Step" section (do not append a duplicate).
- `CLAUDE.md`: add a rule describing `agentdeck run-loop` and its safety boundary, and add the `run-loop` contract to the contract-discovery rules list (alongside the `run` contract rule). State that `run-loop` requires `--confirm` + autonomous mode, only combines sanctioned auto-approve + dispatch scoped to a plan, never captures replies or force-spawns, stops at the human gate, and is audited; and that `agentdeck contract run-loop` is its discovery entry.

- [ ] **Step 3: Full verification**

Run: `conda run -n agentdeck pytest tests/test_autonomy.py tests/test_contracts.py tests/test_history.py -q` → PASS
Run: `conda run -n agentdeck pytest -q` → all pass (baseline 660 + new tests)
Run: `conda run -n agentdeck python -m compileall src tests -q` → no errors
Run: `git diff --check` → clean

- [ ] **Step 4: Commit**

```bash
git add docs/contracts/run-loop-schema.md README.md HISTORY.md docs/handoff/current-development-state.md CLAUDE.md
git commit -m "Document run-loop executing engine and mark autonomous-mode goal complete"
```

---

## Notes for the implementer

- Do NOT push. Commit locally only. No Claude co-author trailer. Run everything in the `agentdeck` conda env.
- Reuse — do not reinvent: `select_auto_approvals` (autonomy.py), `_approval_dispatch_preview_card` + `_dispatch_approved_approval` (cli.py; read `approval_auto_command` / `approval_dispatch_ready_command` for exact usage), and `store.leader_review(plan_id)` (state.py:457) for gate diagnosis.
- Safety boundary is the whole point: `run-loop` requires `--confirm` AND `config.leader.approval_mode == "autonomous"`; it only auto-approves within the stored allowlist+budget, dispatches only to running panes (no force-spawn), never captures replies or infers completion, and stops at the first human gate. Keep every read surface contract-gated.
- Where a snippet references a helper you must confirm (the ProjectView gate in Task 4; the plan/approval seed shape in Task 4's test helper; the `contract list` item key in Task 3), read the real code and adapt minimally while preserving the test intent.
- This completes sub-project 3 and the whole autonomous-mode goal. The deferred GUI-lighting items are follow-ups, not part of this plan.
