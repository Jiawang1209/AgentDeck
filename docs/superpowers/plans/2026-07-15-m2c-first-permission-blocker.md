# AgentDeck M2c First Permission Blocker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the real M2c acceptance fail early when the Leader loses exact artifact authority, and otherwise emit transcript-free durable evidence that explains why the first Claude ACP permission request did not appear.

**Architecture:** Keep the change inside the opt-in M2c acceptance harness. Add one pure fixed-scenario task-authority checker before Mission confirmation and one pure closed-set ledger diagnostic projector used by `_live_failure()`; neither helper writes state, authorizes an effect, changes ACP/tmux behavior, or retains model text. Freeze and verify the resulting commit before allowing one new real four-stage attempt.

**Tech Stack:** Python 3.12 standard library, pytest, JSON/JSONL state, SHA-256, AgentDeck `StateStore`, Codex CLI, Claude Code, Claude Agent ACP, tmux, conda environment `agentdeck`.

---

## Scope discipline

Run every command in the existing isolated worktree:

```text
/Users/liuyue/.config/superpowers/worktrees/multi-agent-explore/codex/m2c-probe-readonly
```

and in the `agentdeck` conda environment. Do not create another worktree. Do
not modify production permission, scheduler, ACP, tmux, provider, or Mission
code. Do not fabricate a permission, infer approval from Worker prose, add a
test-only authorization route, retain transcripts, reinstall tools, change
authentication, merge, or push.

The reviewed design is section 15 of:

```text
docs/superpowers/specs/2026-07-14-agentdeck-m2c-closure-design.md
```

The verified starting commits are:

```text
ced9a50e  Make M2c capability preflight zero-write
abf64b2e  Specify first live permission blocker diagnostics
```

M2c remains BLOCKED until a real four-stage run and cleanup audit both pass.
M3 remains closed throughout this plan.

## Locked file map

### Implementation and tests

- Modify `tests/test_m2c_live_acceptance.py` only. This file owns the opt-in
  real harness, fixed acceptance task, compact failure evidence, and all new
  deterministic regressions. Do not move these helpers into `src/agentdeck/`:
  they validate one fixed live scenario and are not product semantics.

### Documentation

- Modify `docs/validation/phase3-m2c-live-acceptance-sop.md` to document the
  new pre-confirmation authority gate and closed diagnostic categories.
- Modify `HISTORY.md` in each semantic commit.
- Modify `docs/handoff/current-development-state.md` only after the next real
  result exists.
- Modify `docs/validation/2026-07-13-phase3-m2-project-daemon.md` only after the
  next real result exists.
- Modify `docs/roadmap/product-north-star.md`, `README.md`, and
  `README.zh-CN.md` only if the real result is PASS.

## Locked helper and payload names

Use these names exactly:

```python
_LIVE_TASK_AUTHORITY_FIELDS = (
    "phase_order",
    "worker_order",
    "artifact_all_steps",
    "implementation_draft",
    "review_target",
    "revision_transition",
    "acceptance_target",
)

_LIVE_DIAGNOSTIC_CLASSIFICATIONS = frozenset(
    {
        "leader_task_authority_missing",
        "worker_effect_not_requested",
        "worker_attempt_failed",
        "worker_attempt_active",
        "permission_state_inconsistent",
    }
)
```

The ledger diagnostic has this exact closed shape:

```json
{
  "classification": "worker_effect_not_requested",
  "mission_status": "running",
  "step_position": 1,
  "agent_id": "claude-worker",
  "configured_transport": "acp",
  "attempt_state": "succeeded",
  "reply_state": "validated",
  "handoff_state": "recorded",
  "handoff_status": "completed",
  "permission_count": 0,
  "permission_states": []
}
```

Unknown or malformed values become `"unknown"`; they are never copied into
diagnostics. IDs, task text, summaries, blockers, terminal reasons, paths,
prompts, PTY text, ACP updates, and provider output are never included.

### Task 1: Gate Mission confirmation on exact fixed-task authority

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py:695-745`
- Modify: `tests/test_m2c_live_acceptance.py:1948-2050`
- Modify: `tests/test_m2c_live_acceptance.py:2671-2840`
- Modify: `HISTORY.md`

- [ ] **Step 1: Add the RED tests for the accepted four-stage task**

Add these helpers and tests near the existing preflight/helper regressions:

```python
def _exact_live_steps() -> list[dict[str, object]]:
    return [
        {
            "agent_id": "claude-worker",
            "task": "implementation: create artifact.txt with draft-v1",
        },
        {
            "agent_id": "codex-worker",
            "task": "review artifact.txt and require accepted-v2",
        },
        {
            "agent_id": "claude-worker",
            "task": "revision: change artifact.txt from draft-v1 to accepted-v2",
        },
        {
            "agent_id": "codex-worker",
            "task": "acceptance: verify artifact.txt contains accepted-v2",
        },
    ]


def test_live_task_authority_accepts_exact_fixed_scenario() -> None:
    assert _live_task_authority_checks(_exact_live_steps()) == {
        "phase_order": True,
        "worker_order": True,
        "artifact_all_steps": True,
        "implementation_draft": True,
        "review_target": True,
        "revision_transition": True,
        "acceptance_target": True,
    }


@pytest.mark.parametrize(
    ("index", "replacement", "failed_check"),
    [
        (0, "implementation: create result.txt with draft-v1", "artifact_all_steps"),
        (0, "implementation: create artifact.txt", "implementation_draft"),
        (1, "review artifact.txt", "review_target"),
        (2, "revision: change artifact.txt to accepted-v2", "revision_transition"),
        (3, "acceptance: inspect artifact.txt", "acceptance_target"),
    ],
)
def test_live_task_authority_rejects_lost_effect_semantics(
    index: int, replacement: str, failed_check: str,
) -> None:
    steps = _exact_live_steps()
    steps[index]["task"] = replacement

    checks = _live_task_authority_checks(steps)

    assert checks[failed_check] is False
    assert set(checks) == set(_LIVE_TASK_AUTHORITY_FIELDS)


def test_live_task_authority_rejects_phase_and_worker_drift() -> None:
    phase_drift = _exact_live_steps()
    phase_drift[1]["task"] = "inspect artifact.txt and require accepted-v2"
    worker_drift = _exact_live_steps()
    worker_drift[3]["agent_id"] = "claude-worker"

    assert _live_task_authority_checks(phase_drift)["phase_order"] is False
    assert _live_task_authority_checks(worker_drift)["worker_order"] is False
```

- [ ] **Step 2: Run the RED tests and verify the missing helper failure**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py::test_live_task_authority_accepts_exact_fixed_scenario \
         tests/test_m2c_live_acceptance.py::test_live_task_authority_rejects_lost_effect_semantics \
         tests/test_m2c_live_acceptance.py::test_live_task_authority_rejects_phase_and_worker_drift -q
```

Expected: collection or execution fails because
`_live_task_authority_checks` and `_LIVE_TASK_AUTHORITY_FIELDS` do not exist.
Do not accept a failure caused by fixture setup or an unrelated import.

- [ ] **Step 3: Add the minimal pure authority checker**

Place this code immediately before `_state_cardinalities()`:

```python
_LIVE_TASK_AUTHORITY_FIELDS = (
    "phase_order",
    "worker_order",
    "artifact_all_steps",
    "implementation_draft",
    "review_target",
    "revision_transition",
    "acceptance_target",
)


def _live_task_authority_checks(steps: object) -> dict[str, bool]:
    items = steps if type(steps) is list else []
    tasks = [
        item.get("task", "").lower()
        if type(item) is dict and type(item.get("task")) is str
        else ""
        for item in items
    ]
    agents = [
        item.get("agent_id") if type(item) is dict else None
        for item in items
    ]
    exact_length = len(items) == 4
    phases = ("implementation", "review", "revision", "acceptance")
    checks = {
        "phase_order": exact_length
        and all(phase in task for phase, task in zip(phases, tasks, strict=True)),
        "worker_order": exact_length
        and agents
        == ["claude-worker", "codex-worker", "claude-worker", "codex-worker"],
        "artifact_all_steps": exact_length
        and all("artifact.txt" in task for task in tasks),
        "implementation_draft": exact_length and "draft-v1" in tasks[0],
        "review_target": exact_length and "accepted-v2" in tasks[1],
        "revision_transition": exact_length
        and "draft-v1" in tasks[2]
        and "accepted-v2" in tasks[2],
        "acceptance_target": exact_length and "accepted-v2" in tasks[3],
    }
    assert tuple(checks) == _LIVE_TASK_AUTHORITY_FIELDS
    return checks
```

Do not normalize synonyms, infer intent, or repair a task. These checks are
only for the fixed disposable M2c request.

- [ ] **Step 4: Add a RED zero-write failure test**

Add:

```python
def test_live_task_authority_failure_is_preconfirmation_and_zero_write(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    before = _tree_snapshot(tmp_path)
    steps = _exact_live_steps()
    steps[2]["task"] = "revision: inspect artifact.txt"

    with pytest.raises(_LiveHarnessFailure) as error:
        _require_live_task_authority(steps, store=store, capture=_PtyTail())

    payload = json.loads(str(error.value))
    assert payload["code"] == "native_schema_task_authority_invalid"
    assert payload["task_authority"]["revision_transition"] is False
    assert _tree_snapshot(tmp_path) == before
    assert store.load().get("mission_attempts", []) == []
    assert store.load().get("permission_requests", []) == []
```

- [ ] **Step 5: Run the zero-write test and verify RED**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py::test_live_task_authority_failure_is_preconfirmation_and_zero_write -q
```

Expected: FAIL because `_require_live_task_authority` does not exist or because
`_live_failure()` does not accept and project `task_authority`.

- [ ] **Step 6: Add the closed task-authority failure path**

Extend `_live_failure()` with a keyword-only
`task_authority: dict[str, bool] | None = None`. Before adding it to the JSON,
require exact keys, exact order, and bool values:

```python
def _closed_task_authority(value: object) -> dict[str, bool] | None:
    if (
        type(value) is not dict
        or tuple(value) != _LIVE_TASK_AUTHORITY_FIELDS
        or any(type(value[field]) is not bool for field in _LIVE_TASK_AUTHORITY_FIELDS)
    ):
        return None
    return {field: value[field] for field in _LIVE_TASK_AUTHORITY_FIELDS}


def _require_live_task_authority(
    steps: object,
    *,
    store: StateStore,
    capture: _PtyTail,
) -> dict[str, bool]:
    checks = _live_task_authority_checks(steps)
    if not all(checks.values()):
        raise _live_failure(
            "native_schema_task_authority_invalid",
            store=store,
            capture=capture,
            task_authority=checks,
        )
    return checks
```

Inside `_live_failure()` add `task_authority` only when
`_closed_task_authority()` returns a value. Never stringify a rejected mapping.

- [ ] **Step 7: Insert the gate before confirmation**

In `_create_and_confirm_live_mission()`, after the existing exact phase/Worker
checks and before `_wait_for_pty_prompt(..., 2)` and the second `os.write()`,
call:

```python
_require_live_task_authority(steps, store=store, capture=capture)
```

Do not move confirmation earlier. Do not mutate the plan or Mission.

- [ ] **Step 8: Run Task 1 GREEN and focused regression**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
    -k 'task_authority or live_preflight or sanitized_version' -q
```

Expected: all selected tests pass; the opt-in live node is not selected.

- [ ] **Step 9: Update HISTORY and commit Task 1**

Add a 2026-07-15 HISTORY entry recording the RED failure, the exact fixed-task
checks, pre-confirmation zero-write behavior, focused command, and pass count.
Then run `git diff --check` and commit:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "Gate M2c live confirmation on task authority"
```

### Task 2: Add transcript-free durable failure classification

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py:695-745`
- Modify: `tests/test_m2c_live_acceptance.py:2840-3020`
- Modify: `docs/validation/phase3-m2c-live-acceptance-sop.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Add a static diagnostic-state fixture**

Add this test-only fixture near the new Task 1 tests:

```python
class _StaticLiveStore:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    def load(self) -> dict[str, object]:
        return self.state


def _live_diagnostic_state(
    *,
    attempt_state: str = "succeeded",
    reply_state: str = "validated",
    handoff_state: str = "recorded",
    permission_status: str | None = None,
) -> dict[str, object]:
    attempt_id = "mat_111111111111"
    state: dict[str, object] = {
        "plans": [{"plan": {"goal": "SECRET leader text"}}],
        "missions": [{"status": "running", "current_step": 1}],
        "mission_attempts": [
            {
                "attempt_id": attempt_id,
                "step_id": "step_1",
                "agent_id": "claude-worker",
                "configured_transport": "acp",
                "state": attempt_state,
                "blocker": "SECRET /Users/example/private",
                "terminal_reason": "SECRET provider output",
            }
        ],
        "mission_worker_replies": [
            {
                "attempt_id": attempt_id,
                "state": reply_state,
                "canonical_handoff": {
                    "status": "completed",
                    "summary": "SECRET Worker summary",
                    "verification": "SECRET verification",
                },
            }
        ],
        "mission_handoffs": [
            {"attempt_id": attempt_id, "state": handoff_state}
        ],
        "permission_requests": [],
    }
    if permission_status is not None:
        state["permission_requests"] = [
            {
                "status": permission_status,
                "target": "/Users/example/private/artifact.txt",
                "tool_name": "SECRET tool",
            }
        ]
    return state
```

- [ ] **Step 2: Add RED classification tests**

Add:

```python
def _failure_payload(state: dict[str, object]) -> dict[str, object]:
    error = _live_failure(
        "first_permission_timeout",
        store=_StaticLiveStore(state),  # type: ignore[arg-type]
    )
    return json.loads(str(error))


def test_live_failure_classifies_completed_handoff_without_permission() -> None:
    payload = _failure_payload(_live_diagnostic_state())
    assert payload["ledger"] == {
        "classification": "worker_effect_not_requested",
        "mission_status": "running",
        "step_position": 1,
        "agent_id": "claude-worker",
        "configured_transport": "acp",
        "attempt_state": "succeeded",
        "reply_state": "validated",
        "handoff_state": "recorded",
        "handoff_status": "completed",
        "permission_count": 0,
        "permission_states": [],
    }


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (_live_diagnostic_state(attempt_state="running"), "worker_attempt_active"),
        (_live_diagnostic_state(attempt_state="failed"), "worker_attempt_failed"),
        (
            _live_diagnostic_state(permission_status="pending"),
            "permission_state_inconsistent",
        ),
    ],
)
def test_live_failure_classification_is_state_specific(
    state: dict[str, object], expected: str,
) -> None:
    assert _failure_payload(state)["ledger"]["classification"] == expected


def test_live_failure_diagnostic_never_retains_model_or_path_text() -> None:
    payload = _failure_payload(_live_diagnostic_state(permission_status="pending"))
    encoded = json.dumps(payload, sort_keys=True)
    assert "SECRET" not in encoded
    assert "/Users/" not in encoded
    assert "mat_" not in encoded
    assert "dsp_" not in encoded
    assert "summary" not in payload["ledger"]
    assert "blocker" not in payload["ledger"]
    assert set(payload["ledger"]) == {
        "classification",
        "mission_status",
        "step_position",
        "agent_id",
        "configured_transport",
        "attempt_state",
        "reply_state",
        "handoff_state",
        "handoff_status",
        "permission_count",
        "permission_states",
    }


def test_live_failure_classifies_missing_leader_task_authority() -> None:
    checks = {field: True for field in _LIVE_TASK_AUTHORITY_FIELDS}
    checks["revision_transition"] = False
    error = _live_failure(
        "native_schema_task_authority_invalid",
        store=_StaticLiveStore(_live_diagnostic_state()),  # type: ignore[arg-type]
        task_authority=checks,
    )

    payload = json.loads(str(error))
    assert payload["ledger"]["classification"] == "leader_task_authority_missing"
    assert payload["task_authority"] == checks
```

- [ ] **Step 3: Run classification tests and verify RED**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
    -k 'live_failure_classif or diagnostic_never_retains' -q
```

Expected: FAIL because `_live_failure()` does not yet contain `ledger`.

- [ ] **Step 4: Add the minimal closed-set projector**

Place these constants and helpers before `_state_cardinalities()`:

```python
_LIVE_DIAGNOSTIC_CLASSIFICATIONS = frozenset(
    {
        "leader_task_authority_missing",
        "worker_effect_not_requested",
        "worker_attempt_failed",
        "worker_attempt_active",
        "permission_state_inconsistent",
    }
)
_LIVE_MISSION_STATUSES = frozenset(
    {"pending_confirmation", "preparing", "running", "completed", "stopped", "interrupted"}
)
_LIVE_ATTEMPT_STATES = frozenset(
    {"prepared", "admitting", "submitted", "running", "completed", "succeeded", "failed", "cancelled", "interrupted", "ambiguous"}
)
_LIVE_REPLY_STATES = frozenset({"received", "validated", "invalid"})
_LIVE_HANDOFF_STATES = frozenset({"pending", "recorded"})
_LIVE_HANDOFF_STATUSES = frozenset({"completed", "blocked", "failed"})
_LIVE_PERMISSION_STATES = frozenset({"pending", "approved", "denied", "expired"})
_LIVE_AGENT_IDS = frozenset({"claude-worker", "codex-worker"})
_LIVE_TRANSPORTS = frozenset({"acp", "tmux"})


def _closed_enum(value: object, allowed: frozenset[str]) -> str:
    return value if type(value) is str and value in allowed else "unknown"


def _dict_records(value: object) -> list[dict[str, object]]:
    return [item for item in value if type(item) is dict] if type(value) is list else []


def _live_ledger_diagnostic(
    state: object,
    *,
    code: str,
    task_authority: dict[str, bool] | None,
) -> dict[str, object]:
    source = state if type(state) is dict else {}
    missions = _dict_records(source.get("missions"))
    attempts = _dict_records(source.get("mission_attempts"))
    permissions = _dict_records(source.get("permission_requests"))
    attempt = attempts[0] if len(attempts) == 1 else {}
    attempt_id = attempt.get("attempt_id")
    replies = [
        item for item in _dict_records(source.get("mission_worker_replies"))
        if attempt_id is not None and item.get("attempt_id") == attempt_id
    ]
    handoffs = [
        item for item in _dict_records(source.get("mission_handoffs"))
        if attempt_id is not None and item.get("attempt_id") == attempt_id
    ]
    reply = replies[0] if len(replies) == 1 else {}
    handoff = handoffs[0] if len(handoffs) == 1 else {}
    canonical = reply.get("canonical_handoff")
    canonical = canonical if type(canonical) is dict else {}
    attempt_state = _closed_enum(attempt.get("state"), _LIVE_ATTEMPT_STATES)
    reply_state = _closed_enum(reply.get("state"), _LIVE_REPLY_STATES)
    handoff_state = _closed_enum(handoff.get("state"), _LIVE_HANDOFF_STATES)
    permission_states = sorted(
        {
            _closed_enum(item.get("status"), _LIVE_PERMISSION_STATES)
            for item in permissions
        }
    )
    if (
        code == "native_schema_task_authority_invalid"
        and task_authority is not None
        and not all(task_authority.values())
    ):
        classification = "leader_task_authority_missing"
    elif permissions:
        classification = "permission_state_inconsistent"
    elif attempt_state in {"prepared", "admitting", "submitted", "running"}:
        classification = "worker_attempt_active"
    elif attempt_state in {"failed", "cancelled", "interrupted"}:
        classification = "worker_attempt_failed"
    elif (
        attempt_state in {"completed", "succeeded"}
        and reply_state == "validated"
        and handoff_state == "recorded"
    ):
        classification = "worker_effect_not_requested"
    else:
        classification = "permission_state_inconsistent"
    assert classification in _LIVE_DIAGNOSTIC_CLASSIFICATIONS
    step_id = attempt.get("step_id")
    match = re.fullmatch(r"step_([1-4])", step_id) if type(step_id) is str else None
    return {
        "classification": classification,
        "mission_status": _closed_enum(
            missions[0].get("status") if len(missions) == 1 else None,
            _LIVE_MISSION_STATUSES,
        ),
        "step_position": int(match.group(1)) if match is not None else 0,
        "agent_id": _closed_enum(attempt.get("agent_id"), _LIVE_AGENT_IDS),
        "configured_transport": _closed_enum(
            attempt.get("configured_transport"), _LIVE_TRANSPORTS
        ),
        "attempt_state": attempt_state,
        "reply_state": reply_state,
        "handoff_state": handoff_state,
        "handoff_status": _closed_enum(
            canonical.get("status"), _LIVE_HANDOFF_STATUSES
        ),
        "permission_count": len(permissions),
        "permission_states": permission_states,
    }
```

Do not expose raw IDs or arbitrary strings. `"unknown"` and step position `0`
are the only malformed-input projections.

- [ ] **Step 5: Attach the projector to `_live_failure()`**

Replace `_state_cardinalities()` with this pure same-snapshot helper:

```python
def _state_cardinalities_from_state(state: object) -> dict[str, int]:
    source = state if type(state) is dict else {}
    fields = (
        "plans",
        "missions",
        "mission_attempts",
        "permission_requests",
        "mission_handoffs",
        "mission_worker_replies",
    )
    return {
        field: len(source.get(field, []))
        if type(source.get(field, [])) in {list, dict}
        else -1
        for field in fields
    }
```

Then replace `_live_failure()` with the same existing PTY/output handling plus
one state load and the closed task/ledger projections:

```python
def _live_failure(
    code: str,
    *,
    store: StateStore | None = None,
    capture: _PtyTail | None = None,
    output: bytes | None = None,
    task_authority: dict[str, bool] | None = None,
) -> _LiveHarnessFailure:
    diagnostic: dict[str, object] = {"stage": "live_acceptance", "code": code}
    closed_task_authority = _closed_task_authority(task_authority)
    if closed_task_authority is not None:
        diagnostic["task_authority"] = closed_task_authority
    if store is not None:
        state = store.load()
        diagnostic["cardinalities"] = _state_cardinalities_from_state(state)
        diagnostic["ledger"] = _live_ledger_diagnostic(
            state,
            code=code,
            task_authority=closed_task_authority,
        )
    if capture is not None:
        diagnostic["pty"] = capture.diagnostic()
    if output is not None:
        diagnostic["output"] = {
            "byte_count": len(output),
            "truncated": False,
            "sha256": hashlib.sha256(output).hexdigest(),
        }
    return _LiveHarnessFailure(json.dumps(diagnostic, sort_keys=True))
```

Do not keep a second store-loading cardinality path. The output shape and counts
remain unchanged, while classification and cardinalities now share one state
snapshot.

- [ ] **Step 6: Run Task 2 GREEN and the entire non-live harness**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py -q
```

Expected: every non-live test passes and exactly one live test is skipped.
Record the actual pass/skip count; do not predict it in HISTORY.

- [ ] **Step 7: Update the SOP**

In `docs/validation/phase3-m2c-live-acceptance-sop.md`, add a section stating:

- exact artifact/task tokens are checked before confirmation;
- a failed authority check has zero daemon/Worker effects;
- failure JSON may contain only the locked ledger fields;
- `worker_effect_not_requested` means succeeded attempt + validated reply +
  recorded handoff + zero permission records;
- classification is evidence, never authorization;
- another live attempt requires a new frozen commit and passing preflight.

- [ ] **Step 8: Update HISTORY and commit Task 2**

Record RED/GREEN evidence, the exact diagnostic field allowlist, leak-negative
tests, the non-live harness count, and unchanged permission behavior. Then:

```bash
git diff --check
git add tests/test_m2c_live_acceptance.py \
  docs/validation/phase3-m2c-live-acceptance-sop.md HISTORY.md
git commit -m "Classify M2c live permission failures safely"
```

### Task 3: Freeze and verify the new implementation commit

**Files:**
- Inspect only: every tracked file
- Modify only if a deterministic regression exposes a defect; any fix requires
  a new RED/GREEN semantic commit with `HISTORY.md`

- [ ] **Step 1: Run the focused M2c harness**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py -q
```

Expected: zero failures and exactly one explicit live skip.

- [ ] **Step 2: Run the complete suite once, with a retained summary**

```bash
log=$(mktemp /tmp/m2c-permission-full.XXXXXX)
conda run --no-capture-output -n agentdeck pytest -q > "$log" 2>&1
code=$?
tail -40 "$log"
rm -f "$log"
exit $code
```

Expected: zero failures; only explicitly opt-in tests may skip. Do not start a
second pytest while this command is running.

- [ ] **Step 3: Run static and residual checks**

```bash
conda run --no-capture-output -n agentdeck \
  python -m compileall -q src tests
git diff --check
git status --short
ps -axo pid=,command= | rg 'agentdeck.*daemon|pytest -q$' | rg -v 'rg ' || true
find /tmp /private/tmp -maxdepth 1 -type d -name 'agentdeck-m2c-*' -print 2>/dev/null
```

Expected: compileall and diff check exit zero; no test-created daemon, pytest,
or M2c directory remains. Stop and diagnose any residual before proceeding.

- [ ] **Step 4: Freeze the exact commit**

```bash
git rev-parse HEAD
git status --short
```

Expected: a 40-character commit SHA and no tracked modifications. Record this
SHA for Task 4. Do not amend after preflight begins.

### Task 4: Run read-only preflight, then one real four-stage attempt

**Files:**
- No repository files are modified during preflight or live execution
- Temporary tool mirrors live only under `/tmp/agentdeck-m2c-tools.*`

- [ ] **Step 1: Build one exact, disposable tool mirror**

Run on the current macOS machine:

```bash
stage=$(mktemp -d /tmp/agentdeck-m2c-tools.XXXXXX)
mkdir -p "$stage/bin"
cp -c /Users/liuyue/.codex-hud/native/openai-codex/codex-rs/target/release/codex "$stage/bin/codex"
cp -c "$(realpath /Users/liuyue/.local/bin/claude)" "$stage/bin/claude"
cp -cR /Users/liuyue/.local/lib/node_modules/@agentclientprotocol/claude-agent-acp "$stage/claude-agent-acp-package"
cp -c "$stage/claude-agent-acp-package/dist/index.js" "$stage/claude-agent-acp-package/dist/claude-agent-acp"
cp -c "$(realpath "$(command -v node)")" "$stage/claude-agent-acp-package/dist/node"
chmod 700 "$stage/bin/codex" "$stage/bin/claude" \
  "$stage/claude-agent-acp-package/dist/claude-agent-acp" \
  "$stage/claude-agent-acp-package/dist/node"
echo "$stage" > /tmp/agentdeck-m2c-tools-path
```

This mirror satisfies no-symlink/basename sealing without changing global
installations. If any source path is missing, stop with setup BLOCKED; do not
install or upgrade anything.

- [ ] **Step 2: Run the frozen read-only preflight**

```bash
stage=$(sed -n '1p' /tmp/agentdeck-m2c-tools-path)
AGENTDECK_M2C_CODEX="$stage/bin/codex" \
AGENTDECK_M2C_CLAUDE="$stage/bin/claude" \
AGENTDECK_M2C_CLAUDE_ACP="$stage/claude-agent-acp-package/dist/claude-agent-acp" \
AGENTDECK_M2C_TMUX="$(realpath "$(command -v tmux)")" \
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py::test_m2c_live_preflight_is_read_only -q -s
```

Required output: `ready=true`, `blockers=[]`, and `1 passed`. If any blocker
exists, do not run live. Record BLOCKED evidence in Task 5.

- [ ] **Step 3: Reconfirm the frozen boundary**

```bash
git rev-parse HEAD
git status --short
```

Required: the SHA exactly matches Task 3 and the tracked tree is clean.

- [ ] **Step 4: Run exactly one live attempt only after Step 2 passes**

```bash
stage=$(sed -n '1p' /tmp/agentdeck-m2c-tools-path)
AGENTDECK_M2C_LIVE=1 \
AGENTDECK_M2C_CODEX="$stage/bin/codex" \
AGENTDECK_M2C_CLAUDE="$stage/bin/claude" \
AGENTDECK_M2C_CLAUDE_ACP="$stage/claude-agent-acp-package/dist/claude-agent-acp" \
AGENTDECK_M2C_TMUX="$(realpath "$(command -v tmux)")" \
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py::test_real_four_stage_m2c_acceptance -q -s
```

Do not retry automatically. PASS requires `1 passed` plus the harness's full
four-stage, permission, reconnect, takeover, handoff, artifact, and cleanup
assertions. On failure, retain only the emitted compact JSON/classification and
continue to cleanup and Task 5.

- [ ] **Step 5: Clean and audit all outer resources**

```bash
stage=$(sed -n '1p' /tmp/agentdeck-m2c-tools-path 2>/dev/null || true)
if [ -n "$stage" ] && [ -d "$stage" ]; then rm -rf "$stage"; fi
rm -f /tmp/agentdeck-m2c-tools-path
ps -axo pid=,command= | rg '/tmp/agentdeck-m2c-tools|agentdeck.*daemon|pytest.*test_real_four_stage' | rg -v 'rg ' || true
find /tmp /private/tmp -maxdepth 1 -type d -name 'agentdeck-m2c-*' -print 2>/dev/null
```

Expected: no output from the process/directory checks. If the harness reports
cleanup failure, record it even when manual bounded cleanup later succeeds.

### Task 5: Record PASS or BLOCKED evidence and enforce the M3 gate

**Files:**
- Modify: `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`
- Modify on PASS only: `docs/roadmap/product-north-star.md`
- Modify on PASS only: `README.md`
- Modify on PASS only: `README.zh-CN.md`

- [ ] **Step 1: Record the exact observed result**

For PASS, record the frozen SHA, sanitized tool versions, four succeeded
attempts, four canonical handoffs, three predecessor links, permission
preview/confirm, disconnect/reconnect, takeover/return-control, final artifact
byte count/hash, ProjectView/ledger/events/trace agreement, and complete cleanup.

For BLOCKED, record the frozen SHA, preflight readiness, exact fixed failure
code, exact `ledger.classification`, allowlisted ledger states/counts, reached
and not-reached phases, cleanup result, and zero residuals after bounded manual
cleanup. Do not include model text or a partial PASS.

- [ ] **Step 2: Update the active handoff**

Set Active Goal to the next evidence-backed action. On PASS, close M2c and make
M3 brainstorming the next gate. On BLOCKED, keep M2c active and name the exact
new classification as the sole next blocker.

- [ ] **Step 3: Update product-facing files only on PASS**

If and only if the live node passed, mark the M2c north-star delivery gate
complete and add one concise, semantically aligned English/Chinese README
statement. A BLOCKED result must not change those claims.

- [ ] **Step 4: Verify documentation and commit the evidence boundary**

Run:

```bash
git diff --check
rg -n 'M2c|first_permission|worker_effect_not_requested|permission_state_inconsistent' \
  docs/validation/2026-07-13-phase3-m2-project-daemon.md \
  docs/handoff/current-development-state.md HISTORY.md
```

Then stage only files that actually changed:

```bash
git add docs/validation/2026-07-13-phase3-m2-project-daemon.md \
  docs/handoff/current-development-state.md HISTORY.md
```

On PASS only, additionally stage:

```bash
git add docs/roadmap/product-north-star.md README.md README.zh-CN.md
```

Commit:

```bash
git commit -m "Record real M2c permission acceptance result"
```

- [ ] **Step 5: Final gate statement**

Run `git status --short` and `git log -5 --oneline`. Report one of exactly two
outcomes:

```text
M2c PASS — M3 brainstorming unlocked
M2c BLOCKED — M3 remains closed
```

Do not merge or push without a separate user instruction.
