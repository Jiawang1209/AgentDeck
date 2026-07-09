# Golden Demo Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only `agentdeck demo golden` guide that shows the operator the explicit commands needed to run AgentDeck's end-to-end golden demo.

**Architecture:** Add a demo guide contract in `contracts.py`, a small state-derived payload builder in `cli.py`, a new `demo golden` parser branch, contract discovery via `agentdeck contract demo`, and documentation. The guide reuses ProjectView/workbench facts and existing commands; it never mutates state or executes recommended commands.

**Tech Stack:** Python stdlib CLI, existing AgentDeck JSON contracts, pytest, Markdown docs.

---

## File Structure

- Modify `src/agentdeck/contracts.py`: add demo response field constants, example payload, contract discovery helpers, validator, and contract index entry.
- Modify `src/agentdeck/cli.py`: import demo contract helpers, add `_golden_demo_payload`, `demo_golden_command`, `contract_demo_command`, and parser registrations.
- Create `docs/contracts/demo-schema.md`: document payload fields, step fields, safety boundary, commands, and example usage.
- Modify `README.md`: add a concise Golden demo command entry near current capabilities.
- Modify `HISTORY.md`: add the implementation record for the new guide.
- Modify `docs/handoff/current-development-state.md`: note that the golden-demo lane has opened and the first guide slice is implemented.
- Modify `tests/test_contracts.py`: cover reusable contract payload, example drift, and validator rejection cases.
- Modify `tests/test_agent_cli.py`: cover CLI command, read-only behavior, contract discovery, and key state-derived statuses.

---

### Task 1: Demo Contract Shape

**Files:**
- Modify: `src/agentdeck/contracts.py`
- Test: `tests/test_contracts.py`

- [ ] **Step 1: Write failing contract tests**

Add these tests to `tests/test_contracts.py` near the other contract payload tests:

```python
def test_demo_contract_payload_is_reusable_without_cli(tmp_path) -> None:
    from agentdeck.contracts import demo_contract_response, validate_demo_golden_contract

    contract_path = tmp_path / "demo-schema.md"
    payload = demo_contract_response(contract_path, include_example=True)

    assert payload["name"] == "demo"
    assert payload["golden_demo_command"] == "agentdeck demo golden"
    assert payload["contract_path"] == str(contract_path)
    assert payload["response_fields"] == payload["example_response_fields"]
    assert payload["step_fields"] == payload["example_step_fields"]
    assert validate_demo_golden_contract(payload["example_golden_demo"])["ok"] is True


def test_validate_demo_golden_contract_rejects_mutating_safety_claim() -> None:
    from agentdeck.contracts import demo_golden_example, validate_demo_golden_contract

    payload = demo_golden_example()
    payload["safety"] = "delegated"

    result = validate_demo_golden_contract(payload)

    assert result["ok"] is False
    assert "safety must be inspect" in result["errors"]


def test_validate_demo_golden_contract_rejects_bad_step_shape() -> None:
    from agentdeck.contracts import demo_golden_example, validate_demo_golden_contract

    payload = demo_golden_example()
    del payload["steps"][0]["checks"]

    result = validate_demo_golden_contract(payload)

    assert result["ok"] is False
    assert "steps[0].checks is required" in result["errors"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
conda run -n agentdeck pytest tests/test_contracts.py -k demo -q
```

Expected: fails with `ImportError` or missing `demo_contract_response`.

- [ ] **Step 3: Add contract constants and example**

In `src/agentdeck/contracts.py`, add these constants near other response field constants:

```python
DEMO_GOLDEN_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "demo_name",
    "summary",
    "current_status",
    "next_command",
    "recommended_task",
    "steps",
    "inspection_commands",
    "safety",
    "source_command",
)

DEMO_GOLDEN_STEP_FIELDS = (
    "step_id",
    "title",
    "status",
    "command",
    "enabled",
    "blocker",
    "safety",
    "description",
    "checks",
)

DEMO_GOLDEN_STEP_STATUSES = {"ready", "blocked", "waiting_for_input", "done", "inspect"}
DEMO_GOLDEN_STEP_SAFETIES = {"inspect", "explicit_user", "explicit_runtime"}
```

Add this example helper:

```python
def demo_golden_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "golden_demo",
        "demo_name": "golden",
        "summary": "Read-only guide for running the AgentDeck golden demo.",
        "current_status": "provider_setup_required",
        "next_command": "agentdeck doctor",
        "recommended_task": "Add a tiny read-only dashboard or CLI affordance, update tests, and report files changed plus verification.",
        "steps": [
            {
                "step_id": "doctor",
                "title": "Inspect environment",
                "status": "ready",
                "command": "agentdeck doctor",
                "enabled": True,
                "blocker": None,
                "safety": "inspect",
                "description": "Check tmux and configured Leader provider readiness.",
                "checks": ["tmux available", "configured Leader readiness is visible"],
            },
            {
                "step_id": "plan",
                "title": "Create the demo plan",
                "status": "waiting_for_input",
                "command": "agentdeck leader plan --task <task>",
                "enabled": False,
                "blocker": "requires task text",
                "safety": "explicit_user",
                "description": "Ask the Leader to create a plan without dispatching workers.",
                "checks": ["plan is recorded", "approval remains explicit"],
            },
        ],
        "inspection_commands": [
            "agentdeck status",
            "agentdeck workbench",
            "agentdeck dashboard",
            "agentdeck tui",
        ],
        "safety": "inspect",
        "source_command": "agentdeck demo golden",
    }
```

- [ ] **Step 4: Add validator and discovery helpers**

Add:

```python
def validate_demo_golden_contract(payload: dict[str, object]) -> dict[str, object]:
    errors: list[str] = []
    for field in DEMO_GOLDEN_RESPONSE_FIELDS:
        if field not in payload:
            errors.append(f"{field} is required")
    if payload.get("mode") != "golden_demo":
        errors.append("mode must be golden_demo")
    if payload.get("demo_name") != "golden":
        errors.append("demo_name must be golden")
    if payload.get("safety") != "inspect":
        errors.append("safety must be inspect")
    if not isinstance(payload.get("steps"), list):
        errors.append("steps must be a list")
    else:
        for index, step in enumerate(payload["steps"]):
            if not isinstance(step, dict):
                errors.append(f"steps[{index}] must be an object")
                continue
            for field in DEMO_GOLDEN_STEP_FIELDS:
                if field not in step:
                    errors.append(f"steps[{index}].{field} is required")
            if step.get("status") not in DEMO_GOLDEN_STEP_STATUSES:
                errors.append(f"steps[{index}].status is invalid")
            if step.get("safety") not in DEMO_GOLDEN_STEP_SAFETIES:
                errors.append(f"steps[{index}].safety is invalid")
            if not isinstance(step.get("enabled"), bool):
                errors.append(f"steps[{index}].enabled must be bool")
            if step.get("enabled") is False and step.get("blocker") in {None, ""}:
                errors.append(f"steps[{index}].blocker is required when disabled")
            if not isinstance(step.get("checks"), list):
                errors.append(f"steps[{index}].checks must be a list")
    if not isinstance(payload.get("inspection_commands"), list):
        errors.append("inspection_commands must be a list")
    return {"ok": not errors, "errors": errors}


def demo_contract_payload(contract_path: Path) -> dict[str, object]:
    example = demo_golden_example()
    return {
        "name": "demo",
        "golden_demo_command": "agentdeck demo golden",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "response_fields": list(DEMO_GOLDEN_RESPONSE_FIELDS),
        "step_fields": list(DEMO_GOLDEN_STEP_FIELDS),
        "example_response_fields": list(example.keys()),
        "example_step_fields": list(example["steps"][0].keys()),
    }


def demo_contract_response(contract_path: Path, *, include_example: bool = False) -> dict[str, object]:
    payload = demo_contract_payload(contract_path)
    if include_example:
        payload["example_golden_demo"] = demo_golden_example()
    return payload
```

- [ ] **Step 5: Register contract index entry**

Add this item to `CONTRACT_INDEX_SPECS`:

```python
("demo", "agentdeck contract demo", "agentdeck contract demo --example", "demo-schema.md"),
```

- [ ] **Step 6: Run contract tests**

Run:

```bash
conda run -n agentdeck pytest tests/test_contracts.py -k demo -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/agentdeck/contracts.py tests/test_contracts.py
git commit -m "Add golden demo contract shape"
```

---

### Task 2: Golden Demo CLI Payload

**Files:**
- Modify: `src/agentdeck/cli.py`
- Test: `tests/test_agent_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Add tests to `tests/test_agent_cli.py` near command/contract tests:

```python
def test_demo_golden_reports_provider_setup_without_mutating_state(tmp_path, monkeypatch, capsys) -> None:
    root = tmp_path
    monkeypatch.chdir(root)
    assert cli.main(["project", "init"]) == 0
    capsys.readouterr()
    before_state = (root / ".agentdeck" / "state" / "state.json").read_text()
    before_events = (root / ".agentdeck" / "state" / "events.jsonl").read_text()

    exit_code = cli.main(["demo", "golden"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "golden_demo"
    assert payload["current_status"] == "provider_setup_required"
    assert payload["next_command"] == "agentdeck doctor"
    assert payload["safety"] == "inspect"
    assert [step["step_id"] for step in payload["steps"]] == [
        "doctor",
        "leader_provider",
        "workers",
        "plan",
        "approval",
        "dispatch",
        "reply",
        "review_gate",
        "release",
        "inspect",
    ]
    assert (root / ".agentdeck" / "state" / "state.json").read_text() == before_state
    assert (root / ".agentdeck" / "state" / "events.jsonl").read_text() == before_events


def test_demo_golden_with_fake_leader_waits_for_task_input(tmp_path, monkeypatch, capsys) -> None:
    root = tmp_path
    monkeypatch.chdir(root)
    assert cli.main(["project", "init"]) == 0
    capsys.readouterr()
    assert cli.main(["leader", "set-provider", "--provider", "fake", "--model", "fake-plan"]) == 0
    capsys.readouterr()

    assert cli.main(["demo", "golden"]) == 0

    payload = json.loads(capsys.readouterr().out)
    steps = {step["step_id"]: step for step in payload["steps"]}
    assert payload["current_status"] == "ready_to_plan"
    assert payload["next_command"] == "agentdeck leader plan --task <task>"
    assert steps["plan"]["status"] == "waiting_for_input"
    assert steps["plan"]["enabled"] is False
    assert steps["plan"]["blocker"] == "requires task text"
    assert steps["workers"]["command"] == "agentdeck agent spawn-ready --confirm"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k demo_golden -q
```

Expected: argparse error because `demo` command does not exist.

- [ ] **Step 3: Import contract helpers**

In `src/agentdeck/cli.py`, add imports:

```python
    demo_contract_response,
    validate_demo_golden_contract,
```

- [ ] **Step 4: Add payload builder helpers**

Add helpers before command functions:

```python
GOLDEN_DEMO_TASK = (
    "Add a tiny read-only dashboard or CLI affordance, update tests, "
    "and report files changed plus verification."
)


def _golden_demo_step(
    step_id: str,
    title: str,
    status: str,
    command: str | None,
    *,
    enabled: bool,
    blocker: str | None,
    safety: str,
    description: str,
    checks: list[str],
) -> dict[str, object]:
    return {
        "step_id": step_id,
        "title": title,
        "status": status,
        "command": command,
        "enabled": enabled,
        "blocker": blocker,
        "safety": safety,
        "description": description,
        "checks": checks,
    }
```

- [ ] **Step 5: Add `_golden_demo_payload` minimal state-aware implementation**

Add:

```python
def _golden_demo_payload(config: ProjectConfig, store: StateStore) -> dict[str, object]:
    project_view = asdict(store.project_view(config))
    provider_health = _workbench_provider_health(project_view)
    provider_ready = provider_health.get("ready") is True
    review_gate_card = _workbench_review_gate_card(project_view)
    release_ready = review_gate_card.get("can_release") is True
    agents = [item for item in project_view.get("agents", []) if isinstance(item, dict)]
    running_agents = [
        agent
        for agent in agents
        if isinstance(agent.get("runtime"), dict) and agent["runtime"].get("status") == "running"
    ]
    plans = project_view.get("plans") if isinstance(project_view.get("plans"), dict) else {}
    approvals = project_view.get("approvals") if isinstance(project_view.get("approvals"), dict) else {}
    messages = project_view.get("messages") if isinstance(project_view.get("messages"), dict) else {}
    replies = project_view.get("replies") if isinstance(project_view.get("replies"), dict) else {}
    releases = project_view.get("releases") if isinstance(project_view.get("releases"), dict) else {}
    plan_items = _summary_items(plans)
    approval_items = _summary_items(approvals)
    message_items = _summary_items(messages)
    reply_items = _summary_items(replies)
    release_items = _summary_items(releases)
    current_status = "ready_to_plan"
    next_command = "agentdeck leader plan --task <task>"
    if not provider_ready:
        current_status = "provider_setup_required"
        next_command = "agentdeck doctor"
    elif release_items:
        current_status = "released"
        next_command = "agentdeck workbench"
    elif reply_items:
        current_status = "ready_for_review_gate"
        next_command = "agentdeck workbench"
    elif message_items:
        current_status = "waiting_for_reply"
        next_command = "agentdeck capture-reply --agent <agent_id> --message-id <message_id>"
    elif approval_items:
        current_status = "waiting_for_approval"
        next_command = "agentdeck approval list"
    elif plan_items:
        current_status = "plan_ready"
        next_command = f"agentdeck approval create-from-plan --plan-id {plan_items[-1].get('plan_id')}"
    worker_status = "ready" if running_agents else "blocked"
    worker_blocker = None if running_agents else "workers are not running"
    plan_status = "done" if plan_items else "waiting_for_input"
    steps = [
        _golden_demo_step(
            "doctor",
            "Inspect environment",
            "ready",
            "agentdeck doctor",
            enabled=True,
            blocker=None,
            safety="inspect",
            description="Check tmux and configured Leader provider readiness.",
            checks=["tmux readiness", "configured Leader provider readiness"],
        ),
        _golden_demo_step(
            "leader_provider",
            "Make Leader provider usable",
            "ready" if provider_ready else "blocked",
            "agentdeck doctor",
            enabled=True,
            blocker=None if provider_ready else f"configured Leader provider is not ready: {config.leader.provider}",
            safety="inspect",
            description="Use doctor/provider setup output before creating a plan.",
            checks=["provider readiness is visible", "no secret values are printed"],
        ),
        _golden_demo_step(
            "workers",
            "Start worker panes",
            worker_status,
            "agentdeck agent spawn-ready --confirm",
            enabled=not running_agents,
            blocker=worker_blocker,
            safety="explicit_runtime",
            description="Start configured worker panes so approvals can be dispatched.",
            checks=["planner/coder/reviewer runtime bindings", "tmux panes are explicit"],
        ),
        _golden_demo_step(
            "plan",
            "Create the demo plan",
            plan_status,
            "agentdeck leader plan --task <task>",
            enabled=False,
            blocker=None if plan_items else "requires task text",
            safety="explicit_user",
            description="Ask the Leader to create a plan without dispatching workers.",
            checks=["plan is recorded", "approval remains explicit"],
        ),
        _golden_demo_step(
            "approval",
            "Review and approve work",
            "done" if approval_items else "blocked",
            "agentdeck approval list",
            enabled=True,
            blocker=None if approval_items else "requires a saved plan and approval records",
            safety="explicit_user",
            description="Inspect approvals and approve the intended demo step.",
            checks=["human approval is explicit", "approval queue is auditable"],
        ),
        _golden_demo_step(
            "dispatch",
            "Dispatch approved work",
            "done" if message_items else "blocked",
            "agentdeck approval dispatch-ready --confirm",
            enabled=bool(approval_items and running_agents),
            blocker=None if approval_items and running_agents else "requires approved work and running worker panes",
            safety="explicit_runtime",
            description="Dispatch approved work to running worker panes.",
            checks=["message/job/inbox records are created", "worker pane exists"],
        ),
        _golden_demo_step(
            "reply",
            "Record worker reply",
            "done" if reply_items else "blocked",
            "agentdeck capture-reply --agent <agent_id> --message-id <message_id>",
            enabled=False,
            blocker=None if reply_items else "requires dispatched message id and structured worker output",
            safety="explicit_runtime",
            description="Capture or record the worker's structured result.",
            checks=["reply enters ledger", "artifact paths are traceable"],
        ),
        _golden_demo_step(
            "review_gate",
            "Inspect review gate",
            "ready" if reply_items else "blocked",
            "agentdeck leader chat --message \"查看验收门\"",
            enabled=True,
            blocker=None if reply_items else "requires worker/reviewer replies and artifacts",
            safety="inspect",
            description="Use the review gate to decide whether the round can release.",
            checks=["code review state", "round review state"],
        ),
        _golden_demo_step(
            "release",
            "Release accepted round",
            "done" if release_items else ("ready" if release_ready else "blocked"),
            "agentdeck release --confirm",
            enabled=bool(release_ready and not release_items),
            blocker=None if release_ready or release_items else "review gate is not ready",
            safety="explicit_user",
            description="Record the accepted round as an auditable release.",
            checks=["release record is appended", "no merge or push is performed"],
        ),
        _golden_demo_step(
            "inspect",
            "Inspect the cockpit",
            "inspect",
            "agentdeck workbench",
            enabled=True,
            blocker=None,
            safety="inspect",
            description="Open ProjectView/workbench/dashboard/TUI surfaces to inspect the demo state.",
            checks=["workbench shows cards", "dashboard/TUI can consume contracts"],
        ),
    ]
    return {
        "ok": True,
        "mode": "golden_demo",
        "demo_name": "golden",
        "summary": "Read-only guide for running the AgentDeck golden demo.",
        "current_status": current_status,
        "next_command": next_command,
        "recommended_task": GOLDEN_DEMO_TASK,
        "steps": steps,
        "inspection_commands": [
            "agentdeck status",
            "agentdeck workbench",
            "agentdeck dashboard",
            "agentdeck tui",
        ],
        "safety": "inspect",
        "source_command": "agentdeck demo golden",
    }
```

- [ ] **Step 6: Add command function**

Add:

```python
def demo_golden_command(_args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    payload = _golden_demo_payload(config, store)
    validation = validate_demo_golden_contract(payload)
    if not validation["ok"]:
        print("golden demo contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0
```

- [ ] **Step 7: Register parser**

In `build_parser()`, add a top-level `demo` group before `project`:

```python
    demo = subparsers.add_parser("demo", help="Run read-only demo helpers")
    demo_subparsers = demo.add_subparsers(dest="demo_command")
    demo_golden = demo_subparsers.add_parser(
        "golden",
        help="Show the read-only golden-demo guide",
    )
    demo_golden.set_defaults(func=demo_golden_command)
```

- [ ] **Step 8: Run CLI tests**

Run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k demo_golden -q
```

Expected: pass.

- [ ] **Step 9: Commit**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py
git commit -m "Add golden demo CLI guide"
```

---

### Task 3: Demo Contract CLI Discovery

**Files:**
- Modify: `src/agentdeck/cli.py`
- Modify: `tests/test_agent_cli.py`
- Create: `docs/contracts/demo-schema.md`

- [ ] **Step 1: Write failing contract CLI tests**

Add:

```python
def test_contract_demo_discovers_schema_for_gui_clients(capsys) -> None:
    exit_code = cli.main(["contract", "demo"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "demo"
    assert payload["golden_demo_command"] == "agentdeck demo golden"
    assert payload["contract_path"].endswith("docs/contracts/demo-schema.md")
    assert "steps" in payload["response_fields"]
    assert "step_id" in payload["step_fields"]


def test_contract_demo_example_exports_gui_ready_response(capsys) -> None:
    exit_code = cli.main(["contract", "demo", "--example"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["example_golden_demo"]["mode"] == "golden_demo"
    assert payload["example_golden_demo"]["source_command"] == "agentdeck demo golden"


def test_contract_list_includes_demo(capsys) -> None:
    exit_code = cli.main(["contract", "list"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    names = {item["name"] for item in payload["contracts"]}
    assert "demo" in names
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k "contract_demo or contract_list_includes_demo" -q
```

Expected: `contract demo` parser missing.

- [ ] **Step 3: Add contract command import and function**

In `src/agentdeck/cli.py`, import `demo_contract_response` and add:

```python
def contract_demo_command(args: argparse.Namespace) -> int:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "demo-schema.md"
    payload = demo_contract_response(contract_path, include_example=args.example)
    _print_json(payload)
    return 0
```

- [ ] **Step 4: Register contract parser**

In `build_parser()`, add:

```python
    contract_demo = contract_subparsers.add_parser(
        "demo",
        help="Show golden demo guide contract discovery metadata",
    )
    contract_demo.add_argument("--example", action="store_true", help="Include a GUI-ready golden demo example")
    contract_demo.set_defaults(func=contract_demo_command)
```

- [ ] **Step 5: Add contract document**

Create `docs/contracts/demo-schema.md`:

```markdown
# Demo Contract

`agentdeck demo golden` returns a read-only guide for running the AgentDeck golden demo. The command recommends explicit operator commands but never executes them.

Discovery:

```bash
agentdeck contract demo
agentdeck contract demo --example
```

Live command:

```bash
agentdeck demo golden
```

## Response Fields

- `ok`
- `mode`
- `demo_name`
- `summary`
- `current_status`
- `next_command`
- `recommended_task`
- `steps`
- `inspection_commands`
- `safety`
- `source_command`

## Step Fields

- `step_id`
- `title`
- `status`
- `command`
- `enabled`
- `blocker`
- `safety`
- `description`
- `checks`

## Safety

This contract is read-only. Rendering the guide does not create plans, approvals, messages, jobs, replies, releases, or events. It does not call providers, read or write tmux, spawn agents, dispatch work, capture replies, or execute any recommended command.
```

- [ ] **Step 6: Run contract CLI tests**

Run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k "contract_demo or contract_list_includes_demo" -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py docs/contracts/demo-schema.md
git commit -m "Expose golden demo contract discovery"
```

---

### Task 4: State Coverage for Release-Ready and Released Demo

**Files:**
- Modify: `tests/test_agent_cli.py`
- Modify: `src/agentdeck/cli.py`

- [ ] **Step 1: Write release-ready and released tests**

Use existing helpers around `_seed_review_gate_ledger` if available in `tests/test_agent_cli.py`. Add:

```python
def test_demo_golden_surfaces_release_when_review_gate_ready(tmp_path, monkeypatch, capsys) -> None:
    root = tmp_path
    monkeypatch.chdir(root)
    assert cli.main(["project", "init"]) == 0
    capsys.readouterr()
    _seed_review_gate_ledger(root, include_round_review=True)

    assert cli.main(["demo", "golden"]) == 0

    payload = json.loads(capsys.readouterr().out)
    steps = {step["step_id"]: step for step in payload["steps"]}
    assert steps["release"]["command"] == "agentdeck release --confirm"
    assert steps["release"]["safety"] == "explicit_user"
    assert steps["release"]["enabled"] is True


def test_demo_golden_marks_release_done_after_release(tmp_path, monkeypatch, capsys) -> None:
    root = tmp_path
    monkeypatch.chdir(root)
    assert cli.main(["project", "init"]) == 0
    capsys.readouterr()
    _seed_review_gate_ledger(root, include_round_review=True)
    assert cli.main(["release", "--confirm"]) == 0
    capsys.readouterr()

    assert cli.main(["demo", "golden"]) == 0

    payload = json.loads(capsys.readouterr().out)
    steps = {step["step_id"]: step for step in payload["steps"]}
    assert payload["current_status"] == "released"
    assert payload["next_command"] == "agentdeck workbench"
    assert steps["release"]["status"] == "done"
    assert steps["release"]["enabled"] is False
    assert steps["inspect"]["command"] == "agentdeck workbench"
```

- [ ] **Step 2: Run tests**

Run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k "demo_golden_surfaces_release or demo_golden_marks_release" -q
```

Expected: pass if Task 2 logic is sufficient; otherwise fail with release step status mismatch.

- [ ] **Step 3: Confirm release step logic uses review gate readiness**

Verify `_golden_demo_payload` derives release availability from:

```python
review_gate_card = _workbench_review_gate_card(project_view)
release_ready = review_gate_card.get("can_release") is True
```

The release step must use:

```python
"ready" if release_ready else "blocked"
enabled=bool(release_ready and not release_items)
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k demo_golden -q
```

Expected: all golden demo tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py
git commit -m "Refine golden demo release guidance"
```

---

### Task 5: Docs and History

**Files:**
- Modify: `README.md`
- Modify: `HISTORY.md`
- Modify: `docs/handoff/current-development-state.md`

- [ ] **Step 1: Update README command list**

Add `agentdeck demo golden` near the current capabilities list. Use this text:

```markdown
`agentdeck demo golden` is the read-only golden-demo runway: it reports the current blocker and the explicit commands for provider readiness, worker startup, plan, approval, dispatch, reply capture, review gate, release, and inspection. It never executes the commands it recommends.
```

- [ ] **Step 2: Update HISTORY**

At the top of `HISTORY.md` under `## 2026-07-09`, add:

```markdown
### Current - Add read-only golden demo guide

- **类型**: feat
- **动机**: AgentDeck already has the control-plane pieces for a full multi-agent round, but operators still need a single product runway that explains how to run the whole path without reverse-engineering JSON surfaces.
- **What**:
  - Added `agentdeck demo golden`, a read-only guide with current status, next command, recommended tiny code task, explicit steps, inspection commands, and safety metadata.
  - Added `agentdeck contract demo` / `--example` and `docs/contracts/demo-schema.md` for GUI-ready discovery.
  - The guide reuses ProjectView/workbench facts and stays conservative when provider, worker, plan, approval, reply, review gate, or release state is missing.
- **Impact**: The golden demo path is now a product entrypoint instead of chat-only knowledge. Rendering the guide does not call providers, touch tmux, create plans/approvals/messages/replies/releases, append events, or execute recommended commands.
- **验证**: focused demo/contract tests pass; full suite and compileall run before final handoff.
```

- [ ] **Step 3: Update handoff**

At the top of `docs/handoff/current-development-state.md`, add a concise section:

```markdown
## Golden demo lane — first read-only guide slice complete

`agentdeck demo golden` now exposes the first product runway for an end-to-end AgentDeck round. It is JSON/contract-first and read-only: current status, next command, recommended tiny code task, step-by-step explicit commands, and inspection commands. The next likely slice is either a workbench `golden_demo_card` or actually running the documented golden demo once provider/runtime readiness is prepared.
```

- [ ] **Step 4: Run doc grep sanity**

Run:

```bash
rg -n "agentdeck demo golden|contract demo|golden-demo" README.md HISTORY.md docs/handoff/current-development-state.md docs/contracts/demo-schema.md
```

Expected: each file has relevant references.

- [ ] **Step 5: Commit**

```bash
git add README.md HISTORY.md docs/handoff/current-development-state.md
git commit -m "Document golden demo guide"
```

---

### Task 6: Final Verification

**Files:**
- No planned edits unless verification reveals a defect.

- [ ] **Step 1: Run focused suites**

Run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k "demo_golden or contract_demo or contract_list_includes_demo" tests/test_contracts.py -k demo -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full suite**

Run:

```bash
conda run -n agentdeck pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Run compileall**

Run:

```bash
conda run -n agentdeck python -m compileall src tests -q
```

Expected: no output, exit 0.

- [ ] **Step 4: Run diff checks**

Run:

```bash
git diff --check
git status --short --branch --untracked-files=all
```

Expected: no whitespace errors. Status may still show pre-existing `.omc` changes and untracked `AGENTS.md`; do not stage or revert them unless the user explicitly asks.

- [ ] **Step 5: Final stabilization commit**

After any verification-driven edits, stage only the planned files and commit them:

```bash
git add src/agentdeck/cli.py src/agentdeck/contracts.py tests/test_agent_cli.py tests/test_contracts.py README.md HISTORY.md docs/handoff/current-development-state.md docs/contracts/demo-schema.md
git commit -m "Stabilize golden demo guide"
```

If none of these files changed after verification, skip this commit.

---

## Self-Review

- Spec coverage: command surface, step model, state awareness, contract discovery, docs, safety boundary, and tests are all mapped to tasks.
- Placeholder scan: placeholder strings such as `<task>` are intentional command-template examples from the spec; no TBD/TODO implementation gaps remain.
- Type consistency: contract field names match the spec (`mode`, `demo_name`, `current_status`, `steps`, `inspection_commands`, `source_command`); step field names are consistent across tests, validator, and CLI builder.
