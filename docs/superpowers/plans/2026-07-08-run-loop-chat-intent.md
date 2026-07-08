# Natural-language `run-loop` preview intent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `agentdeck leader chat` intent: "推进计划 pln_xxx" → `mode=run_loop_preview`, embeds a `run_loop_preview_card`, and hands back the explicit `agentdeck run-loop --plan-id pln_xxx --confirm` command. Chat NEVER executes run-loop.

**Architecture:** Mirror the existing read-only `run_progress` chat handler and the `role_assign` "disabled template in the palette + concrete filled command in next_command" pattern. Reuse the `scope=autonomous` control-registry group built in the previous slice. The intent_card is auto-derived by `_leader_chat_intent_card`; we feed it via `leader_explanation` (safety=explicit_runtime, requires_explicit_user=True) and small per-card branches.

**Tech Stack:** Python 3.12 stdlib, argparse, pytest. Run all commands via `conda run -n agentdeck ...`.

**Design spec:** `docs/superpowers/specs/2026-07-08-run-loop-chat-intent-design.md`

**Key invariant:** `leader chat` never executes a runtime/dispatch action. This intent only *suggests* the explicit command; the human runs it.

---

## File Structure

- Modify `src/agentdeck/cli.py` — detectors, `_run_loop_preview_card`, chat routing block, `_leader_chat_explanation` branch, intent_card machinery (`card_names`, secondary rule, inspect command/label, blocker, next-control label), `_print_leader_chat_payload_or_error` setdefault.
- Modify `src/agentdeck/contracts.py` — `LEADER_CHAT_RUN_LOOP_PREVIEW_CARD_FIELDS`, known-card-name list, contract discovery field, example fixture, `validate_leader_chat_contract` mode check.
- Modify `docs/contracts/leader-chat-schema.md`, `CLAUDE.md`, `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.
- Modify `tests/test_leader_cli.py` (or `tests/test_agent_cli.py` — use whichever already holds leader-chat tests), `tests/test_contracts.py`.

---

### Task 1: Detectors + read-only card builder

**Files:**
- Modify: `src/agentdeck/cli.py`
- Modify: `src/agentdeck/contracts.py` (field tuple)
- Test: `tests/test_agent_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_cli.py`:

```python
def test_run_loop_preview_card_builder_reflects_autonomous_mode(tmp_path, monkeypatch):
    root = prepare_project(tmp_path, monkeypatch)
    from agentdeck.cli import _run_loop_preview_card, load_config
    from agentdeck.state import StateStore

    store = StateStore(root)
    config = load_config(root)  # default approval_mode == "confirm"
    card = _run_loop_preview_card(store, config, "pln_1")
    assert card["mode"] == "run_loop_preview"
    assert card["plan_id"] == "pln_1"
    assert card["command"] == "agentdeck run-loop --plan-id pln_1 --confirm"
    assert card["autonomous_enabled"] is False
    assert card["blocker"] == "autonomous mode is not enabled"
    assert card["enable_command"].startswith("agentdeck policy set-mode --mode autonomous")
    kinds = {c["kind"] for c in card["controls"]}
    assert kinds == {"run_loop", "inspect"}
    run_loop_ctrl = next(c for c in card["controls"] if c["kind"] == "run_loop")
    assert run_loop_ctrl["enabled"] is False
    assert run_loop_ctrl["safety"] == "delegated"


def test_chat_run_loop_preview_detector_needs_verb_and_plan_id():
    from agentdeck.cli import _chat_wants_run_loop_preview, _chat_run_loop_preview_plan_id

    assert _chat_wants_run_loop_preview("推进计划 pln_abc") is True
    assert _chat_run_loop_preview_plan_id("推进计划 pln_abc") == "pln_abc"
    assert _chat_wants_run_loop_preview("run-loop pln_x") is True
    # a plain progress query must NOT be captured by run-loop preview
    assert _chat_wants_run_loop_preview("查看运行进度 pln_x") is False
    # run-loop verb but no plan id → wants=True, plan_id=None (caller rejects)
    assert _chat_wants_run_loop_preview("推进计划") is True
    assert _chat_run_loop_preview_plan_id("推进计划") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "run_loop_preview_card_builder or run_loop_preview_detector" -q`
Expected: FAIL — `ImportError` / `AttributeError` for the new names.

- [ ] **Step 3: Implement**

In `src/agentdeck/cli.py`, add the detectors next to `_chat_wants_run_progress` (cli.py ~8765):

```python
def _chat_wants_run_loop_preview(message: str) -> bool:
    text = message.strip()
    return bool(
        re.search(r"(推进计划|推进这个计划|往前推|驱动计划|推进 ?pln|run-loop|run loop)", text, re.IGNORECASE)
    )


def _chat_run_loop_preview_plan_id(message: str) -> str | None:
    if not _chat_wants_run_loop_preview(message):
        return None
    match = re.search(r"\bpln_[A-Za-z0-9_-]+\b", message.strip())
    return match.group(0) if match else None
```

Add the card builder near `_run_progress_payload` (cli.py ~6145). Reuse the existing `_control(...)` helper:

```python
def _run_loop_preview_card(store: StateStore, config: ProjectConfig, plan_id: str) -> dict[str, object]:
    autonomous = config.leader.approval_mode == "autonomous"
    command = f"agentdeck run-loop --plan-id {plan_id} --confirm"
    blocker = None if autonomous else "autonomous mode is not enabled"
    enable_command = (
        None if autonomous
        else "agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>"
    )
    return {
        "mode": "run_loop_preview",
        "plan_id": plan_id,
        "command": command,
        "autonomous_enabled": autonomous,
        "safety": "delegated",
        "requires_explicit_user": True,
        "blocker": blocker,
        "enable_command": enable_command,
        "controls": [
            _control(kind="run_loop", label="Run-loop (autonomous)", command=command,
                     safety="delegated", enabled=autonomous, blocker=blocker),
            _control(kind="inspect", label="Inspect run progress",
                     command=f"agentdeck run --plan-id {plan_id}", safety="inspect"),
        ],
    }
```

In `src/agentdeck/contracts.py`, add the field tuple near `LEADER_CHAT_CAPTURE_CARD_FIELDS`:

```python
LEADER_CHAT_RUN_LOOP_PREVIEW_CARD_FIELDS = (
    "mode",
    "plan_id",
    "command",
    "autonomous_enabled",
    "safety",
    "requires_explicit_user",
    "blocker",
    "enable_command",
    "controls",
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "run_loop_preview_card_builder or run_loop_preview_detector" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py src/agentdeck/contracts.py tests/test_agent_cli.py
git commit -m "Add run_loop_preview card builder and chat intent detectors"
```

---

### Task 2: Wire the chat routing + intent_card machinery

**Files:**
- Modify: `src/agentdeck/cli.py` (routing block, explanation branch, intent_card helpers, setdefault)
- Modify: `src/agentdeck/contracts.py` (known-card-name list)
- Test: `tests/test_agent_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_cli.py`:

```python
def test_leader_chat_run_loop_preview_is_read_only_and_hands_back_explicit_command(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = _seed_plan_with_pending_approval(root, agent_id="planner")
    before = StateStore(root).load()

    assert cli.main(["leader", "chat", "--message", f"推进计划 {plan_id}"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_preview"
    card = payload["run_loop_preview_card"]
    assert card["command"] == f"agentdeck run-loop --plan-id {plan_id} --confirm"
    assert payload["next_command"] == card["command"]
    intent = payload["intent_card"]
    assert intent["embedded_card"] == "run_loop_preview_card"
    assert intent["requires_explicit_user"] is True
    next_ctrl = next(c for c in intent["controls"] if c["kind"] == "next")
    assert next_ctrl["safety"] == "explicit_runtime"  # not inspect
    # autonomous off → next control disabled with the blocker
    assert next_ctrl["enabled"] is False
    assert next_ctrl["blocker"] == "autonomous mode is not enabled"
    assert "control_registry_card" in intent["secondary_embedded_cards"]

    # read-only: no plan/approval/message/event mutation beyond the chat turn + its audit event
    after = StateStore(root).load()
    assert after["approvals"] == before["approvals"]
    assert after.get("messages", []) == before.get("messages", [])


def test_leader_chat_run_loop_preview_enables_next_control_in_autonomous_mode(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = _seed_plan_with_pending_approval(root, agent_id="planner")
    cli.main(["policy", "set-mode", "--mode", "autonomous", "--confirm", "--allow-agent", "planner", "--max-approvals", "3"])
    capsys.readouterr()

    assert cli.main(["leader", "chat", "--message", f"推进计划 {plan_id}"]) == 0
    payload = json.loads(capsys.readouterr().out)
    intent = payload["intent_card"]
    next_ctrl = next(c for c in intent["controls"] if c["kind"] == "next")
    assert next_ctrl["enabled"] is True
    assert next_ctrl["blocker"] is None
    # the embedded registry card is filtered to scope=autonomous and selects the run_loop template
    reg = payload["control_registry_card"]
    assert reg["filters"]["scope"] == "autonomous"


def test_leader_chat_run_loop_preview_rejects_missing_plan_id(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    before = StateStore(root).load()
    assert cli.main(["leader", "chat", "--message", "推进计划"]) == 1
    err = capsys.readouterr().err
    assert "plan" in err.lower()
    assert StateStore(root).load() == before  # nothing written, no chat turn
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "run_loop_preview_is_read_only or run_loop_preview_enables or run_loop_preview_rejects" -q`
Expected: FAIL — message routes elsewhere / KeyError on `run_loop_preview_card`.

- [ ] **Step 3: Implement**

(a) In `src/agentdeck/cli.py`, add the routing block in the chat dispatch **immediately before** the `if _chat_wants_run_progress(args.message):` block (cli.py ~9019). Mirror run_progress, but build the `scope=autonomous` registry card and require a plan id:

```python
    if _chat_wants_run_loop_preview(args.message):
        run_loop_plan_id = _chat_run_loop_preview_plan_id(args.message)
        if run_loop_plan_id is None:
            print("run-loop preview requires a plan id (e.g. 推进计划 pln_xxx)", file=sys.stderr)
            return 1
        try:
            store.plan_status(run_loop_plan_id)
        except KeyError:
            print(f"unknown plan: {run_loop_plan_id}", file=sys.stderr)
            return 1
        run_loop_preview_card = _run_loop_preview_card(store, config, run_loop_plan_id)
        control_mode_card = _workbench_control_mode_card(_project_view_payload_or_error(config, store) or {})
        registry_items = _workbench_control_registry({"control_mode_card": control_mode_card})
        run_loop_template_command = "agentdeck run-loop --plan-id <id> --confirm"
        run_loop_control_id = next(
            (
                item.get("control_id")
                for item in registry_items
                if isinstance(item, dict)
                and item.get("scope") == "autonomous"
                and item.get("kind") == "run_loop"
                and item.get("command") == run_loop_template_command
            ),
            None,
        )
        control_registry_card = leader_chat_control_registry_card(
            {"control_registry": registry_items},
            scope="autonomous",
            card="control_mode_card",
            control_id=str(run_loop_control_id) if run_loop_control_id else None,
        )
        turn = store.record_chat_turn(
            mode="run_loop_preview",
            message=args.message,
            plan_id=run_loop_plan_id,
            next_command=run_loop_preview_card["command"],
            review=None,
            action_id=None,
            action_kind="run_loop_preview",
        )
        store.append_event(
            EventRecord.create(
                "leader_chat_turn",
                {
                    "turn_id": turn["turn_id"],
                    "mode": "run_loop_preview",
                    "plan_id": run_loop_plan_id,
                    "message_length": len(args.message),
                },
            )
        )
        refreshed_project_view = _project_view_payload_or_error(config, store)
        if refreshed_project_view is None:
            return 1
        payload = {
            "ok": True,
            "turn_id": turn["turn_id"],
            "mode": "run_loop_preview",
            "message": args.message,
            "project_view": refreshed_project_view,
            "leader_actions": refreshed_project_view.get("leader_actions"),
            "leader_explanation": _leader_chat_explanation(
                "run_loop_preview",
                next_command=run_loop_preview_card["command"],
                project_view=refreshed_project_view,
                result=run_loop_preview_card,
            ),
            "plan_id": run_loop_plan_id,
            "review": None,
            "recovery": refreshed_project_view.get("recovery"),
            "next_command": run_loop_preview_card["command"],
            "leader_action": None,
            "continue_card": None,
            "run_loop_preview_card": run_loop_preview_card,
            "inbox_card": None,
            "approval_card": None,
            "runtime_card": None,
            "queue_card": None,
            "operator_card": None,
            "role_card": None,
            "ledger_card": None,
            "control_registry_card": control_registry_card,
            "workbench_card": None,
        }
        return _print_leader_chat_payload_or_error(payload, store, task=args.message)
```

(b) Add the explanation branch in `_leader_chat_explanation` (next to the `run_progress` branch, cli.py ~8090):

```python
    if mode == "run_loop_preview":
        card = result if isinstance(result, dict) else {}
        return {
            "mode": mode,
            "summary": "Leader is previewing the explicit run-loop command without executing it.",
            "reason": "human asked to drive a plan forward within the autonomous policy",
            "next_command": next_command,
            "recommended_action_id": card.get("plan_id"),
            "action_kind": "run_loop_preview",
            "action_status": "autonomous" if card.get("autonomous_enabled") else "autonomous_disabled",
            "safety": "explicit_runtime",
            "requires_explicit_user": True,
        }
```

(c) In `_leader_chat_intent_card`: add `"run_loop_preview_card"` to the `card_names` tuple (immediately after `"run_progress_card"`), and add the secondary-card rule (next to the run_progress one, ~line 227):

```python
    if embedded_card == "run_loop_preview_card" and payload.get("control_registry_card") is not None:
        secondary_embedded_cards.append("control_registry_card")
```

(d) In `_leader_chat_intent_card_blocker`, add before the final `return None`:

```python
    if embedded_card == "run_loop_preview_card":
        card = payload.get("run_loop_preview_card")
        blocker = card.get("blocker") if isinstance(card, dict) else None
        return str(blocker) if blocker else None
```

(e) In `_leader_chat_intent_inspect_command`, add a branch:

```python
    if embedded_card == "run_loop_preview_card":
        card = payload.get("run_loop_preview_card")
        plan_id = card.get("plan_id") if isinstance(card, dict) else None
        return f"agentdeck run --plan-id {plan_id}" if plan_id else None
```

(f) In `_leader_chat_next_control_label`, add before the fallthrough:

```python
    if re.fullmatch(r"agentdeck run-loop --plan-id [^\s]+ --confirm", command):
        return "Drive plan forward"
```

(g) In `_print_leader_chat_payload_or_error`, add:

```python
    payload.setdefault("run_loop_preview_card", None)
```

(h) In `src/agentdeck/contracts.py`, add `"run_loop_preview_card"` to the leader-chat known-card-name list (the tuple containing `"run_progress_card"`, `"capture_card"`, ... near line 973) so the validator recognizes the payload key.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "run_loop_preview" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py src/agentdeck/contracts.py tests/test_agent_cli.py
git commit -m "Route the run_loop_preview chat intent read-only"
```

---

### Task 3: Contract discovery + validator mode check

**Files:**
- Modify: `src/agentdeck/contracts.py`
- Test: `tests/test_contracts.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_contracts.py`:

```python
def test_leader_chat_contract_exposes_run_loop_preview_card_fields():
    from pathlib import Path
    from agentdeck.contracts import leader_chat_contract_response, LEADER_CHAT_RUN_LOOP_PREVIEW_CARD_FIELDS

    path = Path("docs/contracts/leader-chat-schema.md")
    payload = leader_chat_contract_response(path)
    assert payload["run_loop_preview_card_fields"] == list(LEADER_CHAT_RUN_LOOP_PREVIEW_CARD_FIELDS)
```

(If the contract-discovery accessor is named differently than `leader_chat_contract_response`, grep `def leader_chat_contract` in `src/agentdeck/contracts.py` and use the real name.)

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_contracts.py -k run_loop_preview -q`
Expected: FAIL — `run_loop_preview_card_fields` not in payload.

- [ ] **Step 3: Implement**

In `src/agentdeck/contracts.py`, in the leader-chat contract payload dict (where `run_progress_card_fields` is set, ~line 2939), add:

```python
        "run_loop_preview_card_fields": list(LEADER_CHAT_RUN_LOOP_PREVIEW_CARD_FIELDS),
```

Add a targeted mode check inside `validate_leader_chat_contract` (mirror how it validates a mode's card — near the run_progress/release handling): when `payload.get("mode") == "run_loop_preview"`, require `run_loop_preview_card` present with all `LEADER_CHAT_RUN_LOOP_PREVIEW_CARD_FIELDS`, require `payload.get("next_command") == run_loop_preview_card.get("command")`, and require `intent_card.get("embedded_card") == "run_loop_preview_card"`. Read the function's existing mode-check style and match it. Example shape:

```python
    if payload.get("mode") == "run_loop_preview":
        card = payload.get("run_loop_preview_card")
        if not isinstance(card, dict):
            errors.append("run_loop_preview mode requires run_loop_preview_card")
        else:
            for field in LEADER_CHAT_RUN_LOOP_PREVIEW_CARD_FIELDS:
                if field not in card:
                    errors.append(f"missing run_loop_preview_card field: {field}")
            if payload.get("next_command") != card.get("command"):
                errors.append("run_loop_preview.next_command must match run_loop_preview_card.command")
```

If the leader-chat `--example` fixture is required to include every discovered card and a drift test fails, add a stable `run_loop_preview_card` to that example fixture (grep for the leader-chat example builder).

- [ ] **Step 4: Run tests**

Run: `conda run -n agentdeck pytest tests/test_contracts.py -k "leader_chat" tests/test_agent_cli.py -k "run_loop_preview or contract_leader_chat" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/contracts.py tests/test_contracts.py
git commit -m "Expose run_loop_preview card in leader-chat contract and validator"
```

---

### Task 4: Docs + full verification

**Files:**
- Modify: `docs/contracts/leader-chat-schema.md`, `CLAUDE.md`, `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`

- [ ] **Step 1: Docs**

- `docs/contracts/leader-chat-schema.md`: document `mode=run_loop_preview` and `run_loop_preview_card` fields.
- `CLAUDE.md`: add the rule — `agentdeck leader chat --message "推进计划 pln_xxx"` enters read-only `mode=run_loop_preview`, embeds `run_loop_preview_card` + a `scope=autonomous` `control_registry_card` (selection on the disabled `run_loop` template), hands back explicit `agentdeck run-loop --plan-id <id> --confirm` (next control `safety=explicit_runtime`, disabled with `autonomous mode is not enabled` when autonomous is off), requires a plan id (no guessing), records only the chat turn + audit event, and does NOT call a provider, read/write tmux, auto-approve, dispatch, or mutate approval/runtime/plan state.
- `README.md`: note the new natural-language entry (read-only; suggests the explicit command).
- `HISTORY.md`: newest-first top entry.
- `docs/handoff/current-development-state.md`: single authoritative "Next Best Step" — the autonomous-mode goal and its full GUI-mainline surfacing (command palette + natural language) are done; suggest the next candidate is a fresh roadmap direction (this is a product fork — the loop should ask the human).

- [ ] **Step 2: Full verification**

Run: `conda run -n agentdeck pytest tests/test_agent_cli.py -k "run_loop_preview" tests/test_contracts.py -k "leader_chat or run_loop_preview" -q` → PASS
Run: `conda run -n agentdeck pytest -q` → all pass (baseline 672 + new tests)
Run: `conda run -n agentdeck python -m compileall src tests -q` → no errors
Run: `git diff --check` → clean

- [ ] **Step 3: Commit**

```bash
git add docs/contracts/leader-chat-schema.md CLAUDE.md README.md HISTORY.md docs/handoff/current-development-state.md
git commit -m "Document run_loop_preview natural-language intent"
```

---

## Notes for the implementer

- Do NOT push. Commit locally only. No Claude co-author trailer. Run everything in the `agentdeck` conda env.
- The invariant is absolute: this chat mode is READ-ONLY. It records only a chat turn + `leader_chat_turn` audit event; it must NOT call a provider, read/write tmux, auto-approve, dispatch, or mutate approval/runtime/plan state. The recommended next step is an explicit command the human runs.
- Mirror the `run_progress` handler (cli.py ~9019) for structure and the `role_assign` pattern for "disabled template in the registry, concrete filled command in next_command / intent next control." The intent_card is auto-built by `_leader_chat_intent_card` — you feed it via `leader_explanation` (safety=explicit_runtime, requires_explicit_user=True) and the small per-card branches in Task 2c–2f.
- Put the `run_loop_preview` routing branch BEFORE `run_progress`; the detectors don't overlap ("推进/run-loop" vs "进度/progress"), and the test `test_chat_run_loop_preview_detector_needs_verb_and_plan_id` asserts the boundary.
- If a leader-chat `--example`/drift test fails, add a stable `run_loop_preview_card` to the example fixture — do not weaken the validator.
- This is the final GUI-mainline slice of the autonomous-mode goal. After it, the next direction is a genuine product fork.
```
