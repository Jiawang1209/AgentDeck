# Multi-plan overview (`agentdeck plan board`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `agentdeck plan board` that lists every plan with its derived gate + explicit per-plan next command, so the operator can see and drive multiple plans. Reuses `leader_review` + the pure `run_loop_gate`.

**Architecture:** For each `store.list_plans()` entry, compute `run_loop_gate(store.leader_review(plan_id), False, plan_id)` → `(gate, next_command)`. A contract (`validate_plan_board_contract`) guards the output. No provider/tmux/state writes.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest. Run all via `conda run -n agentdeck ...`.

**Design spec:** `docs/superpowers/specs/2026-07-09-plan-board-design.md`

---

## File Structure

- Modify `src/agentdeck/contracts.py` — fields tuples, `plan_board_example`, `plan_board_contract_payload/response`, `validate_plan_board_contract`, `CONTRACT_INDEX_SPECS` entry.
- Modify `src/agentdeck/cli.py` — `plan_board_command` + `plan board` subparser; `contract_plans_command` + `contract plans` subparser; imports.
- Create `docs/contracts/plans-schema.md`.
- Modify `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.
- Modify `tests/test_contracts.py`, `tests/test_agent_cli.py`.

---

### Task 1: Plan-board contract (fields, example, payload/response, validator)

**Files:** Modify `src/agentdeck/contracts.py`; Test `tests/test_contracts.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_contracts.py`:

```python
def test_plan_board_contract_and_validator_accept_example():
    from pathlib import Path
    from agentdeck.contracts import (
        plan_board_contract_response, plan_board_example, validate_plan_board_contract,
    )
    path = Path("docs/contracts/plans-schema.md")
    payload = plan_board_contract_response(path, include_example=True)
    assert payload["board_command"] == "agentdeck plan board"
    assert "plan_board_response_fields" in payload
    assert payload["example_plan_board"]["mode"] == "plan_board"
    assert validate_plan_board_contract(plan_board_example())["ok"]


def test_validate_plan_board_contract_rejects_bad_counts_and_gate():
    from agentdeck.contracts import plan_board_example, validate_plan_board_contract
    bad = dict(plan_board_example()); bad["active_count"] = 99
    assert not validate_plan_board_contract(bad)["ok"]
    bad2 = dict(plan_board_example())
    bad2["plans"] = [dict(bad2["plans"][0], gate="made_up")]
    bad2["plan_count"] = 1; bad2["active_count"] = 1
    assert not validate_plan_board_contract(bad2)["ok"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_contracts.py -k plan_board -q`
Expected: FAIL — `ImportError: cannot import name 'plan_board_contract_response'`

- [ ] **Step 3: Implement**

In `src/agentdeck/contracts.py`:

(a) Fields near `RUN_LOOP_RESPONSE_FIELDS`:

```python
PLAN_BOARD_RESPONSE_FIELDS = (
    "ok", "mode", "board_command", "plan_count", "active_count", "plans",
)

PLAN_BOARD_ITEM_FIELDS = (
    "plan_id", "task", "provider_backend", "created_at", "status",
    "gate", "next_command", "active", "counts",
)

PLAN_BOARD_GATES = (
    "blocked", "needs_human_approval", "waiting_for_reply", "complete", "idle",
)
```

(b) Example + payload/response near `run_loop_example`:

```python
def plan_board_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "plan_board",
        "board_command": "agentdeck plan board",
        "plan_count": 2,
        "active_count": 1,
        "plans": [
            {
                "plan_id": "pln_a", "task": "demoA", "provider_backend": "local",
                "created_at": "2026-07-04T00:00:00+00:00", "status": "planned",
                "gate": "needs_human_approval", "next_command": "agentdeck approval list",
                "active": True, "counts": {"steps": 1, "approvals": 1},
            },
            {
                "plan_id": "pln_b", "task": "demoB", "provider_backend": "local",
                "created_at": "2026-07-04T00:00:00+00:00", "status": "completed",
                "gate": "complete", "next_command": "agentdeck leader summary --plan-id pln_b",
                "active": False, "counts": {"steps": 1, "approvals": 1},
            },
        ],
    }


def plan_board_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "board_command": "agentdeck plan board",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "plan_board_response_fields": list(PLAN_BOARD_RESPONSE_FIELDS),
        "plan_board_item_fields": list(PLAN_BOARD_ITEM_FIELDS),
        "gates": list(PLAN_BOARD_GATES),
        "project_view_contract": "agentdeck contract project-view",
        "run_loop_contract": "agentdeck contract run-loop",
    }


def plan_board_contract_response(contract_path: Path, include_example: bool = False) -> dict[str, object]:
    payload = plan_board_contract_payload(contract_path)
    if include_example:
        example = plan_board_example()
        payload["example"] = True
        payload["example_plan_board_response_fields"] = list(example)
        payload["example_plan_board_item_fields"] = list(example["plans"][0])
        payload["example_plan_board"] = example
    return payload
```

(c) Validator near `validate_run_loop_contract`:

```python
def validate_plan_board_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in PLAN_BOARD_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"missing plan_board field: {field}")
    if payload.get("mode") != "plan_board":
        errors.append(f"plan_board.mode must be plan_board, got {payload.get('mode')}")
    if payload.get("board_command") != "agentdeck plan board":
        errors.append("plan_board.board_command must be agentdeck plan board")
    plans = payload.get("plans")
    if not isinstance(plans, list):
        errors.append("plan_board.plans must be a list")
        return {"ok": not errors, "errors": errors}
    if payload.get("plan_count") != len(plans):
        errors.append("plan_board.plan_count must equal len(plans)")
    active = 0
    for index, item in enumerate(plans):
        if not isinstance(item, dict):
            errors.append(f"plan_board.plans[{index}] must be an object")
            continue
        for field in PLAN_BOARD_ITEM_FIELDS:
            if field not in item:
                errors.append(f"plan_board.plans[{index}] missing field: {field}")
        if item.get("gate") not in PLAN_BOARD_GATES:
            errors.append(f"plan_board.plans[{index}].gate must be one of {PLAN_BOARD_GATES}")
        if not isinstance(item.get("next_command"), str) or not item.get("next_command"):
            errors.append(f"plan_board.plans[{index}].next_command must be a non-empty string")
        if item.get("active") is True:
            active += 1
    if payload.get("active_count") != active:
        errors.append("plan_board.active_count must equal the number of active plans")
    return {"ok": not errors, "errors": errors}
```

(d) Register in `CONTRACT_INDEX_SPECS` (alongside `run-loop`):

```python
    (
        "plans",
        "agentdeck contract plans",
        "agentdeck contract plans --example",
        "plans-schema.md",
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n agentdeck pytest tests/test_contracts.py -k plan_board -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/contracts.py tests/test_contracts.py
git commit -m "Add plan board response contract, example, and validator"
```

---

### Task 2: `agentdeck plan board` command

**Files:** Modify `src/agentdeck/cli.py`; Test `tests/test_agent_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_cli.py` (reuse `prepare_project`, `StateStore`; use `cli.load_config`):

```python
def _seed_named_plan(root, plan_id, task, agent_id="planner"):
    store = StateStore(root)
    state = store.load()
    role = next(a.role for a in cli.load_config(root).agents if a.agent_id == agent_id)
    state.setdefault("plans", []).append({
        "plan_id": plan_id, "task": task, "status": "planned",
        "provider": "fake", "model": "fake-plan", "provider_backend": "local",
        "plan": {"goal": task, "summary": "s", "steps": [
            {"step": 1, "agent_id": agent_id, "role": role, "task": "do", "risk": "low", "requires_approval": True}]},
        "created_at": "2026-07-04T00:00:00+00:00",
    })
    store.save(state)
    store.create_approvals_from_plan(plan_id)
    return plan_id


def test_plan_board_lists_all_plans_with_gate_and_next_command(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    _seed_named_plan(root, "pln_a", "demoA")
    _seed_named_plan(root, "pln_b", "demoB")
    before = StateStore(root).load()

    assert cli.main(["plan", "board"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan_board"
    assert payload["plan_count"] == 2
    ids = {p["plan_id"] for p in payload["plans"]}
    assert ids == {"pln_a", "pln_b"}
    # both are pending approval -> needs_human_approval, active
    for p in payload["plans"]:
        assert p["gate"] == "needs_human_approval"
        assert p["next_command"] == "agentdeck approval list"
        assert p["active"] is True
    assert payload["active_count"] == 2
    # read-only
    assert StateStore(root).load() == before


def test_plan_board_empty_project_is_valid(tmp_path, monkeypatch, capsys):
    prepare_project(tmp_path, monkeypatch)
    assert cli.main(["plan", "board"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan_count"] == 0
    assert payload["plans"] == []
    assert payload["active_count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "plan_board" -q`
Expected: FAIL — `invalid choice: 'board'`

- [ ] **Step 3: Implement**

In `src/agentdeck/cli.py`, add `validate_plan_board_contract` to the `from .contracts import (...)` block (`run_loop_gate` is already imported from `.autonomy`). Add the command near `plan_list_command` (cli.py:12418):

```python
def plan_board_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    items: list[dict[str, object]] = []
    for plan in store.list_plans():
        if not isinstance(plan, dict):
            continue
        plan_id = str(plan.get("plan_id", ""))
        review = store.leader_review(plan_id)
        gate, next_command = run_loop_gate(review, False, plan_id)
        items.append({
            "plan_id": plan_id,
            "task": plan.get("task"),
            "provider_backend": plan.get("provider_backend"),
            "created_at": plan.get("created_at"),
            "status": plan.get("status"),
            "gate": gate,
            "next_command": next_command,
            "active": gate != "complete",
            "counts": review.get("counts") or {},
        })
    payload = {
        "ok": True,
        "mode": "plan_board",
        "board_command": "agentdeck plan board",
        "plan_count": len(items),
        "active_count": sum(1 for item in items if item["active"]),
        "plans": items,
    }
    validation = validate_plan_board_contract(payload)
    if not validation["ok"]:
        print("plan board contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0
```

Register the subparser after `plan_status` (cli.py:13488):

```python
    plan_board = plan_subparsers.add_parser("board", help="Read-only overview of every plan with its gate and next command")
    plan_board.set_defaults(func=plan_board_command)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "plan_board" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py
git commit -m "Add agentdeck plan board multi-plan overview command"
```

---

### Task 3: `agentdeck contract plans` discovery command

**Files:** Modify `src/agentdeck/cli.py`; Test `tests/test_agent_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_cli.py`:

```python
def test_contract_plans_discovers_schema_and_is_in_index(tmp_path, monkeypatch, capsys):
    prepare_project(tmp_path, monkeypatch)
    assert cli.main(["contract", "plans"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["board_command"] == "agentdeck plan board"
    assert "plan_board_response_fields" in payload

    assert cli.main(["contract", "plans", "--example"]) == 0
    example_payload = json.loads(capsys.readouterr().out)
    assert example_payload["example_plan_board"]["mode"] == "plan_board"

    assert cli.main(["contract", "list"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert "plans" in {c["name"] for c in listing["contracts"]}
```

(If `contract list` items key differs from `name`, read `contract_list_command` output and adjust.)

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "contract_plans" -q`
Expected: FAIL — `invalid choice: 'plans'`

- [ ] **Step 3: Implement**

In `src/agentdeck/cli.py`, add `plan_board_contract_response` to the contracts import. Add the command near `contract_run_loop_command` (cli.py:4240):

```python
def contract_plans_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "plans-schema.md"
    payload = plan_board_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0
```

Register the subparser next to `contract_run_loop` (cli.py:13247):

```python
    contract_plans = contract_subparsers.add_parser("plans", help="Show plan board contract discovery metadata")
    contract_plans.add_argument("--example", action="store_true", help="Include a GUI-ready plan board example")
    contract_plans.set_defaults(func=contract_plans_command)
```

- [ ] **Step 4: Run tests**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "contract_plans" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py
git commit -m "Add agentdeck contract plans discovery command"
```

---

### Task 4: Docs + full verification

**Files:** Create `docs/contracts/plans-schema.md`; Modify `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`

- [ ] **Step 1: Contract doc**

Create `docs/contracts/plans-schema.md` mirroring `docs/contracts/run-loop-schema.md`: document `agentdeck contract plans`, `PLAN_BOARD_RESPONSE_FIELDS`, `PLAN_BOARD_ITEM_FIELDS`, the `gates` enum, and that `agentdeck plan board` is a read-only multi-plan overview reusing `leader_review` + `run_loop_gate` (no provider/tmux/state writes).

- [ ] **Step 2: Docs updates**

- README: add `agentdeck plan board` — read-only overview of every plan with its gate + per-plan next command; discovery `agentdeck contract plans`.
- `HISTORY.md`: newest-first top entry (multi-plan lane slice 1).
- `docs/handoff/current-development-state.md`: single authoritative "Next Best Step" — plan board done; remaining multi-plan lane slices: workbench `plan_board_card` + dashboard **Plans** section + TUI plans view + multi-plan recovery + NL "查看所有计划" intent; then the parallel scheduler (auto-advance across plans + agent contention).

- [ ] **Step 3: Full verification**

Run: `conda run -n agentdeck pytest tests/test_contracts.py -k plan_board tests/test_agent_cli.py -k "plan_board or contract_plans" -q` → PASS
Run: `conda run -n agentdeck pytest -q` → all pass (baseline 687 + new)
Run: `conda run -n agentdeck python -m compileall src tests -q` → no errors
Run: `git diff --check` → clean

- [ ] **Step 4: Commit**

```bash
git add docs/contracts/plans-schema.md README.md HISTORY.md docs/handoff/current-development-state.md
git commit -m "Document plan board multi-plan overview"
```

---

## Notes for the implementer

- Do NOT push. Commit locally only. No Claude co-author trailer. Run everything in the `agentdeck` conda env.
- Reuse — do not reinvent: `store.leader_review(plan_id)` (state.py) and the pure `run_loop_gate` (`agentdeck.autonomy`, already imported in cli.py). The board is a read-only aggregation; no provider, no tmux, no state writes, no events.
- `_seed_named_plan` mirrors the real plan-record shape (`plan.steps` nested, role must match the agent's configured role) — the same shape `_seed_plan_with_pending_approval` uses; if a field is off, read `state.py` `plan_status`/`create_approvals_from_plan` and match.
- This is slice 1 of the multi-plan lane. Do NOT build the workbench/dashboard/TUI wiring, multi-plan recovery, the NL intent, or the parallel scheduler — those are later slices.
```
