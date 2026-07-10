# Sequential Workflow Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a foreground, resumable, one-confirmation linear workflow runner that automatically hands a completed worker reply to the next ordered Leader-plan step.

**Architecture:** Put pure authorization, correlation, parsing, prompt, and runner logic in a focused `workflow.py` module. Persist workflow records through `StateStore`, expose additive `agentdeck workflow preview|run|status|resume` commands and a discoverable contract, and leave all existing run-loop/approval/capture behavior unchanged.

**Tech Stack:** Python 3.12 stdlib, AgentDeck JSON/JSONL state, tmux runtime backend protocol, pytest, Markdown contracts/docs.

---

## File Structure

- Create `src/agentdeck/workflow.py`: pure plan hashing/reply parsing/handoff helpers plus the bounded sequential runner.
- Create `tests/test_workflow.py`: focused pure-helper and runner tests.
- Modify `src/agentdeck/state.py`: workflow record CRUD/update methods.
- Modify `src/agentdeck/contracts.py`: workflow field constants, examples, discovery helpers, validators, and contract-index entry.
- Modify `src/agentdeck/cli.py`: workflow commands, contract discovery command, and parsers.
- Create `docs/contracts/workflow-schema.md`: public command/response/safety contract.
- Modify `tests/test_contracts.py`: workflow contract drift/validator tests.
- Modify `tests/test_agent_cli.py`: preview/read-only, confirmation, status, and two-step CLI integration tests.
- Modify `README.md`, `CLAUDE.md`, `AGENT.md`, `HISTORY.md`, and `docs/handoff/current-development-state.md`: behavior and safety documentation.

### Task 1: Pure workflow authorization and correlated reply helpers

**Files:**
- Create: `src/agentdeck/workflow.py`
- Create: `tests/test_workflow.py`

- [ ] **Step 1: Write failing helper tests**

Create `tests/test_workflow.py` with tests for deterministic hashing, token correlation, invalid matching replies, and compact handoff:

```python
from __future__ import annotations

import pytest

from agentdeck.workflow import (
    authorized_steps,
    build_compact_handoff,
    parse_correlated_reply,
    workflow_plan_hash,
)


PLAN = {
    "plan_id": "pln_demo",
    "plan": {
        "steps": [
            {
                "step": 1,
                "agent_id": "planner",
                "role": "planning",
                "task": "Prepare evidence",
                "requires_approval": True,
            },
            {
                "step": 2,
                "agent_id": "reviewer",
                "role": "review",
                "task": "Review evidence",
                "requires_approval": True,
            },
        ]
    },
}


def test_workflow_plan_hash_is_deterministic_and_task_sensitive() -> None:
    first = workflow_plan_hash(PLAN)
    second = workflow_plan_hash(PLAN)
    changed = {**PLAN, "plan": {"steps": [{**PLAN["plan"]["steps"][0], "task": "Changed"}, PLAN["plan"]["steps"][1]]}}

    assert first == second
    assert first.startswith("sha256:")
    assert workflow_plan_hash(changed) != first
    assert [item["task_hash"] for item in authorized_steps(PLAN)] != []


def test_parse_correlated_reply_ignores_stale_token_and_accepts_matching_block() -> None:
    output = """handoff_token: old\nstatus: completed\nsummary: stale\nverification: old\nrisks: none\nnext_steps: none\n\nhandoff_token: wfr_demo_step_1\nstatus: completed\nsummary: fresh\nverification: pytest\nrisks: none\nnext_steps: review\nfull_output_path: docs/result.md"""

    reply = parse_correlated_reply(output, "wfr_demo_step_1")

    assert reply["status"] == "completed"
    assert reply["summary"] == "fresh"
    assert reply["full_output_path"] == "docs/result.md"


def test_parse_correlated_reply_rejects_matching_invalid_block() -> None:
    with pytest.raises(ValueError, match="missing workflow reply field: verification"):
        parse_correlated_reply(
            "handoff_token: wfr_demo_step_1\nstatus: completed\nsummary: incomplete",
            "wfr_demo_step_1",
        )


def test_build_compact_handoff_excludes_full_reply_text() -> None:
    handoff = build_compact_handoff(
        step=1,
        agent_id="planner",
        reply={
            "status": "completed",
            "summary": "done",
            "verification": "pytest",
            "risks": "none",
            "next_steps": "review",
            "full_output_path": "docs/result.md",
        },
        reply_id="rep_demo",
        artifact_paths=["docs/result.md"],
    )

    assert handoff == {
        "step": 1,
        "agent_id": "planner",
        "status": "completed",
        "summary": "done",
        "verification": "pytest",
        "risks": "none",
        "next_steps": "review",
        "artifact_paths": ["docs/result.md"],
        "trace_command": "agentdeck trace --id rep_demo",
    }
    assert "text" not in handoff
```

- [ ] **Step 2: Run tests to verify RED**

```bash
conda run -n agentdeck pytest tests/test_workflow.py -q
```

Expected: collection fails because `agentdeck.workflow` does not exist.

- [ ] **Step 3: Implement the pure helpers**

Create `src/agentdeck/workflow.py` with:

```python
from __future__ import annotations

import hashlib
import json
from typing import Any

REPLY_FIELDS = ("handoff_token", "status", "summary", "verification", "risks", "next_steps")
REPLY_STATUSES = {"completed", "blocked", "failed"}


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def authorized_steps(plan_record: dict[str, Any]) -> list[dict[str, Any]]:
    plan = plan_record.get("plan") if isinstance(plan_record.get("plan"), dict) else {}
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    result = []
    for item in steps:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task") or "")
        result.append(
            {
                "step": int(item.get("step") or 0),
                "agent_id": str(item.get("agent_id") or ""),
                "role": str(item.get("role") or ""),
                "task": task,
                "task_hash": _sha256_text(task),
            }
        )
    return result


def workflow_plan_hash(plan_record: dict[str, Any]) -> str:
    canonical = {
        "plan_id": str(plan_record.get("plan_id") or ""),
        "steps": authorized_steps(plan_record),
    }
    return _sha256_text(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _reply_blocks(output: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("handoff_token:"):
            if current is not None:
                blocks.append(current)
            current = {"handoff_token": line.split(":", 1)[1].strip()}
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {*REPLY_FIELDS, "full_output_path"}:
            current[key] = value.strip()
    if current is not None:
        blocks.append(current)
    return blocks


def parse_correlated_reply(output: str, token: str) -> dict[str, str] | None:
    matching = [item for item in _reply_blocks(output) if item.get("handoff_token") == token]
    if not matching:
        return None
    reply = matching[-1]
    for field in REPLY_FIELDS:
        if not reply.get(field):
            raise ValueError(f"missing workflow reply field: {field}")
    if reply["status"] not in REPLY_STATUSES:
        raise ValueError(f"invalid workflow reply status: {reply['status']}")
    return reply


def build_compact_handoff(
    *,
    step: int,
    agent_id: str,
    reply: dict[str, str],
    reply_id: str,
    artifact_paths: list[str],
) -> dict[str, Any]:
    return {
        "step": step,
        "agent_id": agent_id,
        "status": reply["status"],
        "summary": reply["summary"],
        "verification": reply["verification"],
        "risks": reply["risks"],
        "next_steps": reply["next_steps"],
        "artifact_paths": list(artifact_paths),
        "trace_command": f"agentdeck trace --id {reply_id}",
    }
```

- [ ] **Step 4: Run helper tests to verify GREEN**

```bash
conda run -n agentdeck pytest tests/test_workflow.py -q
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit helpers**

```bash
git add src/agentdeck/workflow.py tests/test_workflow.py
git commit -m "Add sequential workflow pure helpers"
```

### Task 2: Persist workflow records in StateStore

**Files:**
- Modify: `src/agentdeck/state.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Add failing StateStore lifecycle test**

Append:

```python
from agentdeck.state import StateStore


def test_state_store_records_and_updates_workflow_run(tmp_path) -> None:
    store = StateStore(tmp_path)
    record = store.create_workflow_run(
        plan_id="pln_demo",
        plan_hash="sha256:plan",
        timeout_seconds=30,
        authorized_steps=[{"step": 1, "agent_id": "planner", "role": "planning", "task": "Do", "task_hash": "sha256:task"}],
    )

    assert record["run_id"].startswith("wfr_")
    assert record["status"] == "running"
    updated = store.update_workflow_run(record["run_id"], status="stopped", stop_reason="timed_out")
    assert updated["status"] == "stopped"
    assert store.workflow_run_by_id(record["run_id"])["stop_reason"] == "timed_out"
```

- [ ] **Step 2: Run the failing lifecycle test**

```bash
conda run -n agentdeck pytest tests/test_workflow.py::test_state_store_records_and_updates_workflow_run -q
```

Expected: fails because the StateStore methods do not exist.

- [ ] **Step 3: Add minimal workflow StateStore methods**

Add methods to `StateStore` near other record helpers:

```python
    def create_workflow_run(self, *, plan_id: str, plan_hash: str, timeout_seconds: int, authorized_steps: list[dict[str, Any]]) -> dict[str, Any]:
        state = self.load()
        now = utc_now()
        record = {
            "run_id": new_id("wfr"),
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "status": "running",
            "current_step": 1,
            "step_count": len(authorized_steps),
            "timeout_seconds": timeout_seconds,
            "authorized_steps": authorized_steps,
            "turns": [],
            "stop_reason": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        state.setdefault("workflow_runs", []).append(record)
        self.save(state)
        return record

    def workflow_run_by_id(self, run_id: str) -> dict[str, Any]:
        for item in self.load().get("workflow_runs", []):
            if item.get("run_id") == run_id:
                return item
        raise KeyError(run_id)

    def update_workflow_run(self, run_id: str, **changes: Any) -> dict[str, Any]:
        state = self.load()
        record = next((item for item in state.setdefault("workflow_runs", []) if item.get("run_id") == run_id), None)
        if record is None:
            raise KeyError(run_id)
        record.update(changes)
        record["updated_at"] = utc_now()
        if changes.get("status") == "completed" and not record.get("completed_at"):
            record["completed_at"] = record["updated_at"]
        self.save(state)
        return record
```

- [ ] **Step 4: Run workflow tests**

```bash
conda run -n agentdeck pytest tests/test_workflow.py -q
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit persistence**

```bash
git add src/agentdeck/state.py tests/test_workflow.py
git commit -m "Persist sequential workflow runs"
```

### Task 3: Add workflow contracts and discovery

**Files:**
- Modify: `src/agentdeck/contracts.py`
- Modify: `tests/test_contracts.py`
- Create: `docs/contracts/workflow-schema.md`

- [ ] **Step 1: Write failing contract tests**

Add tests that import `workflow_contract_response`, validate the example preview/status, require `safety=inspect` for preview/status, and require `safety=delegated` plus explicit confirmation provenance for run results.

```python
def test_workflow_contract_response_exposes_examples(tmp_path) -> None:
    from agentdeck.contracts import workflow_contract_response, validate_workflow_preview_contract, validate_workflow_status_contract

    payload = workflow_contract_response(tmp_path / "workflow-schema.md", include_example=True)
    assert payload["name"] == "workflow"
    assert payload["preview_command"] == "agentdeck workflow preview --plan-id <id>"
    assert validate_workflow_preview_contract(payload["example_preview"])["ok"] is True
    assert validate_workflow_status_contract(payload["example_status"])["ok"] is True
```

- [ ] **Step 2: Run contract test to verify RED**

```bash
conda run -n agentdeck pytest tests/test_contracts.py -k workflow -q
```

Expected: fails with missing workflow contract helpers.

- [ ] **Step 3: Implement contract constants/examples/validators/discovery**

Add focused field constants for preview steps and workflow status/run turns, example builders, validators, `workflow_contract_payload/response`, and register:

```python
("workflow", "agentdeck contract workflow", "agentdeck contract workflow --example", "workflow-schema.md"),
```

Validators must require exact mode/safety, positive timeout/step counts, list shapes, `can_run=false` when blockers exist, and `can_resume` only for stopped/interrupted runs.

- [ ] **Step 4: Document the workflow contract**

Create `docs/contracts/workflow-schema.md` covering all four commands, response fields, state/status values, correlation token, one-confirmation authorization, stop reasons, audit events, and the unchanged run-loop boundary.

- [ ] **Step 5: Run contract tests**

```bash
conda run -n agentdeck pytest tests/test_contracts.py -k "workflow or contract_index" -q
```

Expected: pass.

- [ ] **Step 6: Commit contract slice**

```bash
git add src/agentdeck/contracts.py tests/test_contracts.py docs/contracts/workflow-schema.md
git commit -m "Add sequential workflow contract"
```

### Task 4: Add read-only preview and status CLI

**Files:**
- Modify: `src/agentdeck/cli.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Write failing preview/status CLI tests**

Cover a two-step saved plan with running bindings, state equality before/after preview, blockers for missing panes, unknown plan/run failures, and status projection from a saved workflow record.

- [ ] **Step 2: Run focused tests to verify RED**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k "workflow_preview or workflow_status" -q
```

Expected: parser rejects the unknown workflow command.

- [ ] **Step 3: Implement `_workflow_preview_payload`**

Use `authorized_steps`, `workflow_plan_hash`, configured agents, and stored runtime bindings only. Do not instantiate `TmuxBackend`. Return validated preview fields and exact confirm command.

- [ ] **Step 4: Implement workflow preview/status commands and parsers**

Add `workflow` subparsers, positive timeout validation, unknown id errors with no JSON output, and `contract workflow` discovery wiring.

- [ ] **Step 5: Run focused CLI tests**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k "workflow_preview or workflow_status or contract_workflow" -q
```

Expected: pass.

- [ ] **Step 6: Commit read-only CLI slice**

```bash
git add src/agentdeck/cli.py tests/test_agent_cli.py
git commit -m "Add sequential workflow preview and status"
```

### Task 5: Implement bounded run/resume engine with fake runtime

**Files:**
- Modify: `src/agentdeck/workflow.py`
- Modify: `src/agentdeck/cli.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Write failing two-step runner test**

Create a fake backend whose `send_input` stores prompts and whose `capture_output` extracts the active `handoff_token` from the latest prompt and returns a matching completed structured block. Assert two dispatches, compact handoff in Step 2, completed workflow state, message/reply lineage, and ordered workflow events.

- [ ] **Step 2: Run runner test to verify RED**

```bash
conda run -n agentdeck pytest tests/test_workflow.py -k runner -q
```

Expected: fails because the runner does not exist.

- [ ] **Step 3: Implement prompt builder and runner**

Add `build_workflow_prompt(...)` and `run_sequential_workflow(...)` with injected backend, clock, and poll function. The runner must:

- create or resume one active turn
- never duplicate an existing dispatched turn
- check binding and pane existence
- create message/attempt/job/inbox records
- send the correlated prompt
- poll until matching reply or timeout
- record reply/artifacts via StateStore
- persist turn state and compact handoff
- append workflow audit events
- continue only on completed
- stop on blocked/failed/invalid/timeout/runtime loss

- [ ] **Step 4: Add run/resume CLI confirmation and integration tests**

Cover missing `--confirm`, preview blockers, successful two-step run, blocked reply, timeout, resume without duplicate send, and plan drift before runtime access. Monkeypatch `cli.TmuxBackend`, clock, and polling so tests do not sleep.

- [ ] **Step 5: Run focused workflow suite**

```bash
conda run -n agentdeck pytest tests/test_workflow.py tests/test_agent_cli.py -k workflow -q
```

Expected: pass.

- [ ] **Step 6: Run existing safety regressions**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k "run_loop or capture_reply" -q
```

Expected: pass unchanged.

- [ ] **Step 7: Commit execution engine**

```bash
git add src/agentdeck/workflow.py src/agentdeck/cli.py tests/test_workflow.py tests/test_agent_cli.py
git commit -m "Run bounded sequential workflows"
```

### Task 6: Document the delivered slice and verify

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `AGENT.md`
- Modify: `HISTORY.md`
- Modify: `docs/handoff/current-development-state.md`

- [ ] **Step 1: Document commands and safety boundaries**

Add concise user guidance and durable governance rules: one confirmation, frozen plan hash, foreground behavior, correlation tokens, stop/resume semantics, no spawn/provider/ack, and ordinary run-loop unchanged.

- [ ] **Step 2: Update HISTORY and handoff**

Record the core engine as delivered and explicitly defer the built-in skill, live recitation test, DAG, and cycles.

- [ ] **Step 3: Run full verification**

```bash
conda run -n agentdeck pytest tests/test_workflow.py tests/test_contracts.py tests/test_agent_cli.py -k workflow -q
conda run -n agentdeck pytest tests/test_agent_cli.py -k "run_loop or capture_reply" -q
conda run -n agentdeck pytest -q
conda run -n agentdeck python -m compileall src tests -q
conda run -n agentdeck agentdeck contract workflow --example
git diff --check
```

Expected: all tests pass, compileall exits 0, contract smoke emits validated JSON, and diff check is clean.

- [ ] **Step 4: Audit scope**

Verify no `.agentdeck/`, `.omc/`, untracked `AGENTS.md`, remote/marketplace code, built-in skill, or recitation content is staged.

- [ ] **Step 5: Commit docs**

```bash
git add README.md CLAUDE.md AGENT.md HISTORY.md docs/handoff/current-development-state.md
git commit -m "Document sequential workflow engine"
```
