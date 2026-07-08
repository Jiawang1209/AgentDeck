# Light Autonomous Commands into the Control Registry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface `agentdeck approval auto --confirm` and `agentdeck run-loop --plan-id <id> --confirm` as read-only command-palette controls under a new `scope=autonomous`, so `agentdeck workbench`'s `control_registry[]` and `agentdeck controls` expose them for GUI/TUI rendering. Rendering is not authorization.

**Architecture:** Extend the existing `control_mode_card` with an `autonomous_actions[]` field (derived from `approval_mode`), then add one generic `_append_workbench_control_registry_items(scope="autonomous", ...)` call so the two controls flow into the registry, `agentdeck controls`, filters, and groups — all of which are already generic.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest. Run all commands via `conda run -n agentdeck ...`.

**Design spec:** `docs/superpowers/specs/2026-07-08-autonomous-controls-lighting-design.md`

---

## File Structure

- Modify `src/agentdeck/cli.py` — `_workbench_control_mode_card` (add `autonomous_actions`), `_workbench_control_registry` (add `scope=autonomous` append).
- Modify `src/agentdeck/contracts.py` — `WORKBENCH_CONTROL_MODE_CARD_FIELDS` (+field), `validate_workbench_contract` (validate `autonomous_actions`), workbench example fixture (`workbench_example`, add `autonomous_actions`).
- Modify `docs/contracts/workbench-schema.md`, `docs/contracts/controls-schema.md`, `CLAUDE.md`, `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.
- Modify `tests/test_agent_cli.py`, `tests/test_contracts.py`.

---

### Task 1: `control_mode_card.autonomous_actions[]`

**Files:**
- Modify: `src/agentdeck/cli.py` (`_workbench_control_mode_card`)
- Modify: `src/agentdeck/contracts.py` (fields tuple + validator + example fixture)
- Test: `tests/test_agent_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_cli.py`:

```python
def test_control_mode_card_exposes_autonomous_action_controls(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)

    # default (confirm) mode: approval_auto disabled with a blocker, run_loop template disabled
    assert cli.main(["workbench"]) == 0
    card = json.loads(capsys.readouterr().out)["control_mode_card"]
    actions = {c["kind"]: c for c in card["autonomous_actions"]}
    assert set(actions) == {"approval_auto", "run_loop"}
    assert actions["approval_auto"]["command"] == "agentdeck approval auto --confirm"
    assert actions["approval_auto"]["safety"] == "delegated"
    assert actions["approval_auto"]["enabled"] is False
    assert actions["approval_auto"]["blocker"] == "autonomous mode is not enabled"
    assert actions["run_loop"]["command"] == "agentdeck run-loop --plan-id <id> --confirm"
    assert actions["run_loop"]["enabled"] is False
    assert actions["run_loop"]["blocker"] == "requires --plan-id"

    # enable autonomous mode: approval_auto becomes enabled with no blocker
    cli.main(["policy", "set-mode", "--mode", "autonomous", "--confirm",
              "--allow-agent", "planner", "--max-approvals", "3"])
    capsys.readouterr()
    assert cli.main(["workbench"]) == 0
    card2 = json.loads(capsys.readouterr().out)["control_mode_card"]
    actions2 = {c["kind"]: c for c in card2["autonomous_actions"]}
    assert actions2["approval_auto"]["enabled"] is True
    assert actions2["approval_auto"]["blocker"] is None
    assert actions2["run_loop"]["enabled"] is False  # still a template
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py::test_control_mode_card_exposes_autonomous_action_controls -q`
Expected: FAIL — `KeyError: 'autonomous_actions'`

- [ ] **Step 3: Implement**

(a) In `src/agentdeck/cli.py`, in `_workbench_control_mode_card`, build the actions from `approval_mode` (already computed as `approval_mode` in that function) and add the field to the returned dict. Insert before the `return {`:

```python
    autonomous_actions = [
        _control(
            kind="approval_auto",
            label="Auto-approve (autonomous)",
            command="agentdeck approval auto --confirm",
            safety="delegated",
            enabled=approval_mode == "autonomous",
            blocker=None if approval_mode == "autonomous" else "autonomous mode is not enabled",
        ),
        _control(
            kind="run_loop",
            label="Run-loop (autonomous)",
            command="agentdeck run-loop --plan-id <id> --confirm",
            safety="delegated",
            enabled=False,
            blocker="requires --plan-id",
        ),
    ]
```

Then add to the returned dict (next to `"active_controls": [...]`):

```python
        "autonomous_actions": autonomous_actions,
```

(b) In `src/agentdeck/contracts.py`, add `"autonomous_actions"` to `WORKBENCH_CONTROL_MODE_CARD_FIELDS` (after `"active_controls"`):

```python
WORKBENCH_CONTROL_MODE_CARD_FIELDS = (
    "mode",
    "title",
    "current_mode",
    "approval_mode",
    "default_safety",
    "available_modes",
    "active_controls",
    "autonomous_actions",
    "set_mode_command_template",
    "policy_source",
)
```

(c) In `validate_workbench_contract`, right after the `active_controls` validation block (after the line `errors.append("control_mode_card.active_controls must be a list")`, before `elif "control_mode_card" in payload:`), add a mirror block reusing `WORKBENCH_CONTROL_MODE_CONTROL_FIELDS`:

```python
        autonomous_actions = control_mode_card.get("autonomous_actions")
        if isinstance(autonomous_actions, list):
            for control in autonomous_actions:
                if not isinstance(control, dict):
                    errors.append("autonomous action controls must be objects")
                    continue
                for field in WORKBENCH_CONTROL_MODE_CONTROL_FIELDS:
                    if field not in control:
                        errors.append(f"missing autonomous action control field: {field}")
                if "enabled" in control and not isinstance(control.get("enabled"), bool):
                    errors.append("autonomous action control enabled must be a boolean")
                if control.get("enabled") is False and not control.get("blocker"):
                    errors.append("disabled autonomous action control requires blocker")
        elif "autonomous_actions" in control_mode_card:
            errors.append("control_mode_card.autonomous_actions must be a list")
```

(d) In `src/agentdeck/contracts.py`, in the `workbench_example()` fixture's `control_mode_card` (its `approval_mode` is `"confirm"`), add `autonomous_actions` right after the `active_controls` list closes. Since the example is not autonomous, `approval_auto` is disabled:

```python
            "autonomous_actions": [
                {
                    "kind": "approval_auto",
                    "label": "Auto-approve (autonomous)",
                    "command": "agentdeck approval auto --confirm",
                    "safety": "delegated",
                    "enabled": False,
                    "blocker": "autonomous mode is not enabled",
                },
                {
                    "kind": "run_loop",
                    "label": "Run-loop (autonomous)",
                    "command": "agentdeck run-loop --plan-id <id> --confirm",
                    "safety": "delegated",
                    "enabled": False,
                    "blocker": "requires --plan-id",
                },
            ],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py::test_control_mode_card_exposes_autonomous_action_controls tests/test_contracts.py -k workbench -q`
Expected: PASS (the new test + the workbench contract/example drift tests still green)

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py src/agentdeck/contracts.py tests/test_agent_cli.py
git commit -m "Add autonomous_actions controls to control_mode_card"
```

---

### Task 2: Surface `scope=autonomous` in the control registry

**Files:**
- Modify: `src/agentdeck/cli.py` (`_workbench_control_registry`)
- Test: `tests/test_agent_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_cli.py`:

```python
def test_control_registry_and_controls_expose_autonomous_scope(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["policy", "set-mode", "--mode", "autonomous", "--confirm",
              "--allow-agent", "planner", "--max-approvals", "3"])
    capsys.readouterr()

    # workbench control_registry carries the two autonomous items
    assert cli.main(["workbench"]) == 0
    registry = json.loads(capsys.readouterr().out)["control_registry"]
    auto = [i for i in registry if i["scope"] == "autonomous"]
    kinds = {i["kind"] for i in auto}
    assert kinds == {"approval_auto", "run_loop"}
    assert all(i["card"] == "control_mode_card" for i in auto)
    assert all(isinstance(i["control_id"], str) and i["control_id"] for i in auto)

    # agentdeck controls --scope autonomous returns exactly those two
    assert cli.main(["controls", "--scope", "autonomous"]) == 0
    card = json.loads(capsys.readouterr().out)
    assert card["item_count"] == 2
    assert {i["kind"] for i in card["items"]} == {"approval_auto", "run_loop"}

    # --enabled-only leaves just approval_auto (run_loop template is disabled)
    assert cli.main(["controls", "--scope", "autonomous", "--enabled-only"]) == 0
    enabled = json.loads(capsys.readouterr().out)
    assert {i["kind"] for i in enabled["items"]} == {"approval_auto"}
```

Note: if `agentdeck controls` output uses different key names than `item_count` / `items`, read `controls_command`'s payload (it comes from `leader_chat_control_registry_card`) and adjust the assertions to the real keys.

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py::test_control_registry_and_controls_expose_autonomous_scope -q`
Expected: FAIL — no `scope == "autonomous"` items in the registry.

- [ ] **Step 3: Implement**

In `src/agentdeck/cli.py`, in `_workbench_control_registry`, right after the `policy` / `control_mode_card` append block (the one that passes `controls=control_mode_card.get("active_controls")`), add:

```python
    _append_workbench_control_registry_items(
        registry,
        scope="autonomous",
        card="control_mode_card",
        agent_id=None,
        controls=control_mode_card.get("autonomous_actions"),
    )
```

(`control_mode_card` is already bound in that function from the `policy` append; reuse it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py::test_control_registry_and_controls_expose_autonomous_scope tests/test_contracts.py -k "workbench or controls" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py
git commit -m "Surface autonomous commands under scope=autonomous in the control registry"
```

---

### Task 3: Docs + full verification

**Files:**
- Modify: `docs/contracts/workbench-schema.md`, `docs/contracts/controls-schema.md`, `CLAUDE.md`, `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`

- [ ] **Step 1: Docs**

- `docs/contracts/workbench-schema.md`: document `control_mode_card.autonomous_actions[]` (the two controls, their kinds/commands/safety, the enabled/blocker rules) and the `scope=autonomous` `control_registry` group.
- `docs/contracts/controls-schema.md`: note `scope=autonomous` as a discoverable group carrying `kind=approval_auto` (enabled only in autonomous mode) and a disabled `kind=run_loop` template.
- `CLAUDE.md`: extend the control-registry rule to require a `scope=autonomous` group derived from `control_mode_card.autonomous_actions[]`, with `kind=approval_auto` (`safety=delegated`, enabled only when `approval_mode=autonomous`, blocker `autonomous mode is not enabled` otherwise) and a disabled `kind=run_loop` template (command `agentdeck run-loop --plan-id <id> --confirm`, blocker `requires --plan-id`); state that rendering these controls is inspect-only and is NOT execution authorization, and that `control_mode_card` now carries `autonomous_actions[]`.
- `README.md`: note that `agentdeck workbench` / `agentdeck controls --scope autonomous` now surface the autonomous commands as read-only palette controls (still explicit-run-only).
- `HISTORY.md`: newest-first top entry for this slice.
- `docs/handoff/current-development-state.md`: update the single "Next Best Step" — this GUI-mainline follow-up is done; the remaining candidate is the natural-language `leader chat` intent for `run-loop`.

- [ ] **Step 2: Full verification**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "autonomous or control_registry or control_mode" tests/test_contracts.py -k "workbench or controls" -q` → PASS
Run: `conda run -n agentdeck pytest -q` → all pass (baseline 670 + new tests)
Run: `conda run -n agentdeck python -m compileall src tests -q` → no errors
Run: `git diff --check` → clean

- [ ] **Step 3: Commit**

```bash
git add docs/contracts/workbench-schema.md docs/contracts/controls-schema.md CLAUDE.md README.md HISTORY.md docs/handoff/current-development-state.md
git commit -m "Document scope=autonomous control-registry group"
```

---

## Notes for the implementer

- Do NOT push. Commit locally only. No Claude co-author trailer. Run everything in the `agentdeck` conda env.
- This is a read-only surfacing slice: no provider calls, no tmux, no state writes, no execution. A `control_id` / enabled control is NOT authorization — both commands still require the human to run them with `--confirm` (run-loop also needs autonomous mode).
- The registry, `agentdeck controls` filters, `selection`, and `groups[]` are all generic (grouped by scope/card) — a new `scope=autonomous` needs no enum/allowlist change; only the `control_mode_card.autonomous_actions` field + its validator (Task 1) and the one append call (Task 2).
- If `agentdeck contract workbench --example` / `agentdeck contract controls --example` drift tests fail, it is because an example fixture is missing `autonomous_actions` (Task 1d) — fix the fixture, do not weaken the validator. If `controls_example()` independently enumerates registry items and a drift test compares, add the two autonomous items there too.
- Keep it to these three tasks. The natural-language `leader chat` run-loop intent is a separate follow-up.
