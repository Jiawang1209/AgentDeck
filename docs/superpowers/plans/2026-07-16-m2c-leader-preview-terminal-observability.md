# M2c Leader Preview Terminal Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the M2c live harness immediately report the exact durable Leader terminal that occurs before Mission Preview, while requiring one explicit frozen Leader model and retaining no raw terminal or provider output.

**Architecture:** Keep the change inside `tests/test_m2c_live_acceptance.py`: add a pure model-input seal, a strict preflight-v2 model card, a pure durable-terminal projector over validated conversation state plus journal/outbox events, and a process-aware bounded Preview wait. Reuse production conversation lifecycle and Leader diagnostic validators; do not change production provider, ConversationSession, ACP, tmux, daemon, permission, or timeout behavior.

**Tech Stack:** Python 3.12, pytest, standard-library PTY/subprocess/time primitives, AgentDeck `StateStore`, production conversation lifecycle validation, production `LeaderGatewayError` diagnostics, Markdown evidence documents, conda environment `agentdeck`.

---

## File map and change boundary

**Modify:**

- `tests/test_m2c_live_acceptance.py`
  - owns the opt-in live harness, preflight payload, explicit model seal,
    durable terminal projection, bounded Preview wait, deterministic fake-only
    regressions, and live evidence shape;
  - remains the only Python file changed by this slice.
- `docs/validation/phase3-m2c-live-acceptance-sop.md`
  - documents the explicit model input, preflight v2, safe terminal codes, and
    no-live authorization boundary.
- `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
  - preserves historical Task 14 evidence and records the new deterministic
    observability result without retroactive diagnosis.
- `docs/handoff/current-development-state.md`
  - routes the next action to frozen verification/preflight or the next
    evidence-driven repair.
- `HISTORY.md`
  - records the implemented behavior, test evidence, unchanged production
    boundary, and honest M2c status.
- `docs/superpowers/plans/2026-07-16-m2c-leader-preview-terminal-observability.md`
  - tracks completed TDD steps and exact verification evidence.

**Do not modify:**

- `src/agentdeck/**`;
- README or north-star positioning;
- provider timeouts, MCP settings, authentication, global config, ACP/tmux
  transports, daemon scheduling, permission semantics, or ProjectView schema.

## Commit boundary

The reviewed design requires one implementation authority commit, followed by
verification on its unchanged SHA. Therefore this plan uses:

1. one documentation-only plan commit;
2. one implementation commit containing all RED/GREEN harness code, tests,
   SOP, validation, handoff, HISTORY, and checked plan boxes;
3. after frozen verification, one evidence-only commit recording the two full
   suites and the designated preflight result.

The evidence-only commit does not replace the frozen implementation SHA. A
future live authorization must name the implementation SHA, the explicit
model id, and the four sanitized tool versions.

### Task 1: Freeze explicit Leader model authority in preflight v2

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:59-115`
- Modify: `tests/test_m2c_live_acceptance.py:600-795`
- Test: `tests/test_m2c_live_acceptance.py:3300-3710`

- [x] **Step 1: Add RED tests for missing, invalid, and valid model input**

Add these tests near the existing preflight tests. The pure input tests must
not create a project or execute any tool:

```python
@pytest.mark.parametrize(
    ("raw", "blocker"),
    [
        (None, "leader_model_missing"),
        ("", "leader_model_missing"),
        (" gpt-5.5", "leader_model_invalid"),
        ("gpt-5.5 ", "leader_model_invalid"),
        ("/tmp/model", "leader_model_invalid"),
        ("openai/gpt-5.5", "leader_model_invalid"),
        ("gpt-5.5\nSECRET", "leader_model_invalid"),
        ("x" * 97, "leader_model_invalid"),
    ],
)
def test_leader_model_input_fails_closed(raw, blocker) -> None:
    seal, actual = _seal_leader_model_input(raw)

    assert seal is None
    assert actual == blocker
    assert "SECRET" not in repr((seal, actual))
    assert "/tmp/model" not in repr((seal, actual))


def test_leader_model_input_accepts_exact_conservative_identity() -> None:
    seal, blocker = _seal_leader_model_input("gpt-5.5-codex:high")

    assert blocker is None
    assert seal == _LeaderModelSeal(model="gpt-5.5-codex:high")
```

Add a strict preflight-shape test by extending an existing fake-tool ready
case with:

```python
monkeypatch.setenv("AGENTDECK_M2C_LEADER_MODEL", "gpt-5.5")
payload = _live_preflight(project)

assert payload["schema_version"] == "m2c-live-preflight/v2"
assert payload["leader_model"] == {
    "provider": "codex-cli",
    "model": "gpt-5.5",
    "source": "explicit",
    "ready": True,
}
assert payload["ready"] is True
assert payload["blockers"] == []
assert _validate_preflight_payload(payload) == []
```

Add missing/invalid preflight cases that assert `model is None`, `ready` is
false, the exact blocker is present once, the raw value is absent from
`repr(payload)`, and `_validate_preflight_payload(payload) == []`.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_m2c_live_acceptance.py::test_leader_model_input_fails_closed \
  tests/test_m2c_live_acceptance.py::test_leader_model_input_accepts_exact_conservative_identity \
  tests/test_m2c_live_acceptance.py::test_preflight_resolves_path_symlinks_to_canonical_targets \
  -q
```

Expected: FAIL because `_LeaderModelSeal` and `_seal_leader_model_input` do not
exist and the preflight still emits `m2c-live-preflight/v1` without a model
card.

- [x] **Step 3: Implement the minimal immutable model seal**

Add near the harness constants:

```python
LEADER_MODEL_ENV = "AGENTDECK_M2C_LEADER_MODEL"
LEADER_MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}\Z")


@dataclass(frozen=True)
class _LeaderModelSeal:
    model: str


def _seal_leader_model_input(
    raw: object,
) -> tuple[_LeaderModelSeal | None, str | None]:
    if raw is None or raw == "":
        return None, "leader_model_missing"
    if type(raw) is not str or LEADER_MODEL_PATTERN.fullmatch(raw) is None:
        return None, "leader_model_invalid"
    return _LeaderModelSeal(model=raw), None


def _leader_model_from_environment(
) -> tuple[_LeaderModelSeal | None, str | None]:
    return _seal_leader_model_input(os.environ.get(LEADER_MODEL_ENV))
```

Add `leader_model_missing`, `leader_model_invalid`, and `leader_model_drift`
to `BLOCKER_CODES`.

- [x] **Step 4: Upgrade `_live_preflight()` and its validator to v2**

Extend the signature and compute the target seal before tool probing:

```python
def _live_preflight(
    project: Path,
    *,
    require_explicit_paths: bool = False,
    isolation: _ProbeIsolation | None = None,
    leader_model_seal: _LeaderModelSeal | None = None,
) -> dict[str, object]:
    observed_model, model_blocker = _leader_model_from_environment()
    target_model = leader_model_seal or observed_model
    if leader_model_seal is not None and observed_model != leader_model_seal:
        model_blocker = "leader_model_drift"
    blockers = [model_blocker] if model_blocker is not None else []
    # existing sealed, read-only tool probes remain unchanged
```

After probes, re-read the input when a caller supplied a seal. If it no longer
matches, append `leader_model_drift`. Return:

```python
return {
    "schema_version": "m2c-live-preflight/v2",
    "mode": "m2c_live_preflight",
    "ready": not unique_blockers,
    "probe_timeout_seconds": PROBE_TIMEOUT_SECONDS,
    "leader_model": {
        "provider": "codex-cli",
        "model": target_model.model if target_model is not None else None,
        "source": "explicit",
        "ready": target_model is not None and model_blocker is None,
    },
    "tools": tools,
    "blockers": unique_blockers,
}
```

Update `_validate_preflight_payload()` to require the exact v2 top-level keys
and exact model-card keys. It must require `provider == "codex-cli"`,
`source == "explicit"`, a valid model or `None`, exact bool `ready`, and
consistency between the card and the three model blocker codes. It must never
stringify an invalid rejected value.

- [x] **Step 5: Update existing fake preflight tests with explicit model input**

Every fake test that expects `ready=true`, `blockers=[]`, or an exact tool-only
blocker list must explicitly set:

```python
monkeypatch.setenv(LEADER_MODEL_ENV, "gpt-5.5")
```

Do not add an autouse fixture. The designated read-only preflight must continue
to observe the real caller-provided environment, and the missing-model test
must remain meaningful.

- [x] **Step 6: Run the focused preflight matrix and verify GREEN**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  python -m pytest tests/test_m2c_live_acceptance.py \
  -k 'leader_model or preflight' -q
```

Expected: all selected tests PASS; the opt-in live node is not selected.

### Task 2: Bind the same model seal to config, admission, and evidence

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:2510-2580`
- Modify: `tests/test_m2c_live_acceptance.py:2920-3055`
- Modify: `tests/test_m2c_live_acceptance.py:3260-3290`
- Test: `tests/test_m2c_live_acceptance.py:3715-3860`
- Test: `tests/test_m2c_live_acceptance.py:5270-5455`

- [x] **Step 1: Add RED tests for config binding and pre-provider drift**

Add:

```python
def test_live_config_uses_only_explicit_model_seal(tmp_path) -> None:
    root = tmp_path / "project"
    (root / ".agentdeck").mkdir(parents=True)
    paths = {
        name: type("PathOnly", (), {"path": Path("/bin/true")})()
        for name, _env, _help, _version in TOOL_SPECS
    }

    _write_live_config(
        root,
        paths,
        session_name="m2c-model-test",
        leader_model=_LeaderModelSeal("gpt-5.5"),
    )

    config = load_config(root)
    assert config.leader.provider == "codex-cli"
    assert config.leader.model == "gpt-5.5"
```

Add a second invocation with another valid value to prove there is no hidden
fallback. Add a drift test that writes a config for one seal, verifies it
against another, and expects only `leader_model_drift`. Add a live-entry test
that deletes the environment, replaces `tempfile.mkdtemp` with a function that
would fail if called, and asserts `_run_live_acceptance()` stops with
`leader_model_missing` before disposable project creation.

- [x] **Step 2: Run the new model binding tests and verify RED**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  python -m pytest tests/test_m2c_live_acceptance.py \
  -k 'live_config_uses_only_explicit_model_seal or leader_model_drift or live_entry_requires_leader_model' \
  -q
```

Expected: FAIL because `_write_live_config()` has no model argument and still
contains the hardcoded live model.

- [x] **Step 3: Require the seal in `_write_live_config()`**

Change the signature and model line only:

```python
def _write_live_config(
    root: Path,
    paths: dict[str, _ExecutableSeal],
    *,
    session_name: str,
    leader_model: _LeaderModelSeal,
) -> None:
    if not isinstance(leader_model, _LeaderModelSeal):
        raise _live_failure("leader_model_invalid")
    # Retain the existing complete TOML template byte-for-byte except for:
    # model = {_toml_string(leader_model.model)}
```

The actual template remains complete in the function; replace only its current
hardcoded `model = "gpt-5.4"` line with the interpolation shown above.

Update deterministic semantic fixtures to pass an explicit test seal. A
fixture may still name `gpt-5.4`; the production live writer itself must have
no model literal or default.

- [x] **Step 4: Add exact post-write model verification**

Add:

```python
def _verify_live_leader_model(root: Path, seal: _LeaderModelSeal) -> None:
    try:
        leader = load_config(root).leader
    except Exception:
        raise _live_failure("leader_model_drift") from None
    if leader.provider != "codex-cli" or leader.model != seal.model:
        raise _live_failure("leader_model_drift")
```

Do not include either observed value in the failure.

- [x] **Step 5: Thread the immutable seal through the live call graph**

Change the signatures:

```text
_run_live_acceptance()
  -> _run_live_acceptance_in_project(paths, parent, leader_model)
  -> _run_live_acceptance_in_project_guarded(paths, parent, leader_model)
```

At `_run_live_acceptance()` entry, call `_leader_model_from_environment()`
before path discovery or `mkdtemp`; raise the fixed blocker if no seal exists.
Inside the guarded function:

1. pass `leader_model_seal=leader_model` to the internal preflight;
2. write config with the same seal;
3. call `_verify_live_leader_model()` immediately after the write and again
   immediately before `_create_and_confirm_live_mission()`;
4. add `"leader_model": preflight["leader_model"]` to PASS evidence.

Never re-read the environment to choose a model and never substitute another
model.

- [x] **Step 6: Update setup/cleanup tests for the explicit signature**

Every direct call to `_run_live_acceptance_in_project()` or
`_run_live_acceptance_in_project_guarded()` passes
`_LeaderModelSeal("gpt-5.5")`. Tests that intentionally mutate setup continue
to assert the original cleanup code and zero raw-value leakage.

- [x] **Step 7: Run model binding tests and verify GREEN**

Run the Step 2 command again.

Expected: all selected tests PASS and no provider or live subprocess executes.

### Task 3: Project one exact durable Leader terminal

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:20-45` imports
- Modify: `tests/test_m2c_live_acceptance.py:790-860` internal records
- Modify: `tests/test_m2c_live_acceptance.py:1270-1335` failure projection
- Test: `tests/test_m2c_live_acceptance.py:4180-4680`

- [x] **Step 1: Add RED terminal-projection fixtures and tests**

Import production authorities:

```python
from agentdeck.conversation.leader_gateway import (
    LEADER_FAILURE_STAGES,
    LeaderGatewayError,
    leader_gateway_diagnostics,
)
from agentdeck.conversation.lifecycle import validate_conversation_history
```

Add a fixture builder that returns exact conversation sessions, one new turn,
legal transitions to `failed` or `cancelled`, and one exact
`conversation_turn_terminal` event. Parameterize:

```python
@pytest.mark.parametrize(
    ("stage", "state", "expected_code"),
    [
        ("timeout", "failed", "leader_timeout_before_preview"),
        ("nonzero", "failed", "leader_nonzero_before_preview"),
        ("schema", "failed", "leader_schema_before_preview"),
        ("cancelled", "cancelled", "leader_cancelled_before_preview"),
        ("oversize", "failed", "leader_oversize_before_preview"),
    ],
)
def test_closed_leader_terminal_preserves_stage(stage, state, expected_code) -> None:
    durable, events, baseline = _leader_terminal_fixture(stage=stage, state=state)

    observation = _project_leader_terminal(durable, events, baseline)

    assert observation.code == expected_code
    assert observation.diagnostics == {
        "stage": stage,
        "diagnostic_code": None,
        "attempt_count": 1,
        "constraint_mode": "native_json_schema",
    }
```

Add schema/json-parse diagnostic-code cases. Add invalid tests for unknown
stage, bad diagnostic code, bool/3 attempt count, bad constraint mode,
cancelled/state mismatch, missing event, duplicate conflicting event,
malformed journal/outbox collection, extra event/payload field, more than one
new turn, and a hostile value whose `__str__`/`__repr__` raises.

- [x] **Step 2: Run the projector matrix and verify RED**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  python -m pytest tests/test_m2c_live_acceptance.py \
  -k 'closed_leader_terminal or leader_terminal_evidence' -q
```

Expected: FAIL because the observation types and projector do not exist.

- [x] **Step 3: Add immutable observation and fixed code map**

Add:

```python
_LEADER_TERMINAL_CODES = {
    stage: f"leader_{stage}_before_preview"
    for stage in LEADER_FAILURE_STAGES
}


@dataclass(frozen=True)
class _LeaderTerminalObservation:
    code: str
    diagnostics: dict[str, object]


class _LeaderTerminalEvidenceInvalid(ValueError):
    pass
```

Use an explicit literal map instead of the comprehension if a production stage
needs a different public spelling. Tests lock every production stage.

- [x] **Step 4: Reconcile logical journal/outbox events without raw projection**

Implement a pure helper that:

1. requires journal and `conversation_event_outbox` to be exact lists;
2. requires each candidate event used by this path to be an exact dict with
   `event_id`, `event_type`, `created_at`, and `payload`;
3. requires non-empty string identity/type/time and exact-dict payload;
4. deduplicates equal records by `event_id`;
5. raises `_LeaderTerminalEvidenceInvalid` on same-id disagreement;
6. returns records for internal equality only.

The helper must never include a rejected value in an exception message.

```python
def _logical_conversation_events(
    state: object, journal_events: object
) -> list[dict[str, object]]:
    if type(state) is not dict or type(journal_events) is not list:
        raise _LeaderTerminalEvidenceInvalid
    outbox = state.get("conversation_event_outbox")
    if type(outbox) is not list:
        raise _LeaderTerminalEvidenceInvalid
    logical: dict[str, dict[str, object]] = {}
    for collection in (journal_events, outbox):
        for item in collection:
            if (
                type(item) is not dict
                or set(item) != {"event_id", "event_type", "created_at", "payload"}
                or type(item["event_id"]) is not str
                or not item["event_id"]
                or type(item["event_type"]) is not str
                or not item["event_type"]
                or type(item["created_at"]) is not str
                or not item["created_at"]
                or type(item["payload"]) is not dict
            ):
                raise _LeaderTerminalEvidenceInvalid
            prior = logical.get(item["event_id"])
            if prior is not None and prior != item:
                raise _LeaderTerminalEvidenceInvalid
            logical[item["event_id"]] = item
    return list(logical.values())
```

- [x] **Step 5: Implement `_project_leader_terminal()`**

The projector must:

```python
def _project_leader_terminal(
    state: object,
    journal_events: object,
    baseline_turn_ids: frozenset[str],
) -> _LeaderTerminalObservation | None:
    try:
        if type(state) is not dict or type(baseline_turn_ids) is not frozenset:
            raise _LeaderTerminalEvidenceInvalid
        if any(type(item) is not str or not item for item in baseline_turn_ids):
            raise _LeaderTerminalEvidenceInvalid
        base_records = {}
        for key in (
            "conversation_sessions",
            "conversation_turns",
            "conversation_preview_bindings",
        ):
            collection = state.get(key)
            if type(collection) is not list or any(
                type(item) is not dict for item in collection
            ):
                raise _LeaderTerminalEvidenceInvalid
            base_records[key] = collection
        transitions = state.get("conversation_state_transitions")
        if type(transitions) is not list or any(
            type(item) is not dict for item in transitions
        ):
            raise _LeaderTerminalEvidenceInvalid
        projected = validate_conversation_history(base_records, transitions)
        new_turns = [
            item
            for item in base_records["conversation_turns"]
            if item.get("turn_id") not in baseline_turn_ids
        ]
        if not new_turns:
            return None
        if len(new_turns) != 1:
            raise _LeaderTerminalEvidenceInvalid
        turn = new_turns[0]
        turn_id = turn.get("turn_id")
        conversation_id = turn.get("conversation_id")
        if type(turn_id) is not str or type(conversation_id) is not str:
            raise _LeaderTerminalEvidenceInvalid
        terminal_state = projected["turn_states"].get(turn_id)
        if terminal_state not in {"failed", "cancelled"}:
            return None
        events = _logical_conversation_events(state, journal_events)
        matching = [
            item
            for item in events
            if item["event_type"] == "conversation_turn_terminal"
            and item["payload"].get("turn_id") == turn_id
        ]
        if len(matching) != 1:
            raise _LeaderTerminalEvidenceInvalid
        payload = matching[0]["payload"]
        if set(payload) != {
            "conversation_id",
            "turn_id",
            "state",
            "stage",
            "diagnostic_code",
            "attempt_count",
            "constraint_mode",
        }:
            raise _LeaderTerminalEvidenceInvalid
        if (
            payload["conversation_id"] != conversation_id
            or payload["state"] != terminal_state
            or (terminal_state == "cancelled") != (payload["stage"] == "cancelled")
        ):
            raise _LeaderTerminalEvidenceInvalid
        error = LeaderGatewayError(
            payload["stage"],
            payload["diagnostic_code"],
            attempt_count=payload["attempt_count"],
            constraint_mode=payload["constraint_mode"],
        )
        diagnostics = leader_gateway_diagnostics(error)
        return _LeaderTerminalObservation(
            code=_LEADER_TERMINAL_CODES[error.stage],
            diagnostics=diagnostics,
        )
    except _LeaderTerminalEvidenceInvalid:
        raise
    except (KeyError, TypeError, ValueError):
        raise _LeaderTerminalEvidenceInvalid from None
```

`LeaderGatewayError` performs the canonical stage/code/attempt/mode validation.
The projector still performs exact event and payload shape validation before
constructing it. A caught `ValueError`, `TypeError`, or lifecycle validation
error becomes `_LeaderTerminalEvidenceInvalid` with no raw message.

- [x] **Step 6: Extend `_live_failure()` for snapshot-stable terminal evidence**

Add mutually exclusive keyword inputs:

```python
state_snapshot: dict[str, object] | None = None
leader_terminal: object = None
```

When `state_snapshot` is present, use it for `_state_cardinalities_from_state`
and `_live_ledger_diagnostic`; do not call `store.load()`. Reject simultaneous
`store` and `state_snapshot`. Add `leader_terminal` only when it is an exact
four-field dict in this order, every value passes the same production
diagnostic validation, and no extra field exists. Invalid terminal input is
omitted or converted by the caller to `leader_terminal_evidence_invalid`; it
is never stringified.

```python
def _closed_leader_terminal(value: object) -> dict[str, object] | None:
    fields = (
        "stage",
        "diagnostic_code",
        "attempt_count",
        "constraint_mode",
    )
    if type(value) is not dict or tuple(value) != fields:
        return None
    try:
        error = LeaderGatewayError(
            value["stage"],
            value["diagnostic_code"],
            attempt_count=value["attempt_count"],
            constraint_mode=value["constraint_mode"],
        )
    except (TypeError, ValueError):
        return None
    return leader_gateway_diagnostics(error)
```

- [x] **Step 7: Run the terminal projector matrix and verify GREEN**

Run the Step 2 command again.

Expected: all selected tests PASS.

### Task 4: Add the bounded process-aware Mission Preview wait

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:1478-1550`
- Test: `tests/test_m2c_live_acceptance.py` near terminal projector tests

- [x] **Step 1: Add deterministic fake clock, process, store, and drain**

Add test-only helpers:

```python
class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _PreviewProcess:
    def __init__(self, returncodes: list[int | None]) -> None:
        self.returncodes = list(returncodes)

    def poll(self) -> int | None:
        if len(self.returncodes) > 1:
            return self.returncodes.pop(0)
        return self.returncodes[0]


class _PreviewStore:
    def __init__(self, states, events) -> None:
        self.states = list(states)
        self.events = list(events)

    def load(self):
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def all_events(self):
        if len(self.events) > 1:
            return self.events.pop(0)
        return self.events[0]
```

The fake store returns already owned exact dictionaries; the production helper
must not mutate them.

- [x] **Step 2: Add RED wait tests**

Add tests proving:

```text
durable timeout at fake time <120 -> leader_timeout_before_preview
nonzero/schema/cancelled/oversize -> distinct fixed code
valid Preview -> returns the exact observed state
Preview + terminal -> leader_preview_terminal_conflict
invalid terminal -> leader_terminal_evidence_invalid
process exit + final terminal -> terminal code wins
process exit without terminal/Preview -> bare_pty_exited_before_preview
live process + no terminal/Preview through deadline -> mission_preview_timeout
historical terminal only -> cannot satisfy the new request
bounded drain runs on each iteration and final reconciliation
```

For the leakage case, seed the state/event/store objects with
`SECRET prompt /Users/private raw stderr model output` in unrelated fields and
the in-memory `_PtyTail`. Parse the rendered JSON and assert none of those
strings appear; only the PTY digest and exact four-field terminal object may
appear.

- [x] **Step 3: Run wait tests and verify RED**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  python -m pytest tests/test_m2c_live_acceptance.py \
  -k 'wait_for_mission_preview or preview_terminal_conflict or before_preview' \
  -q
```

Expected: FAIL because `_wait_for_mission_preview()` does not exist.

- [x] **Step 4: Implement strict baseline turn-id extraction**

Add a pure helper that accepts one loaded state, requires exact
`conversation_turns` list/dicts with non-empty unique string `turn_id`, and
returns `frozenset[str]`. Any malformed value raises only
`leader_terminal_evidence_invalid` through the caller.

```python
def _conversation_turn_ids(state: object) -> frozenset[str]:
    if type(state) is not dict:
        raise _LeaderTerminalEvidenceInvalid
    turns = state.get("conversation_turns")
    if type(turns) is not list or any(type(item) is not dict for item in turns):
        raise _LeaderTerminalEvidenceInvalid
    identities = [item.get("turn_id") for item in turns]
    if (
        any(type(item) is not str or not item for item in identities)
        or len(identities) != len(set(identities))
    ):
        raise _LeaderTerminalEvidenceInvalid
    return frozenset(identities)
```

- [x] **Step 5: Implement one observation pass**

Add an internal helper or local closure that:

1. loads state exactly once;
2. reads `store.all_events()` exactly once;
3. evaluates the existing exact Preview predicate;
4. calls `_project_leader_terminal()`;
5. converts projector validation failure to
   `leader_terminal_evidence_invalid` using the same state snapshot;
6. detects Preview/terminal coexistence;
7. returns Preview, raises a terminal failure, or reports no result.

The terminal failure call is:

```python
raise _live_failure(
    observation.code,
    state_snapshot=state,
    capture=capture,
    leader_terminal=observation.diagnostics,
)
```

Use this complete observation shape:

```python
def _mission_preview_ready(state: dict[str, object]) -> bool:
    missions = state.get("missions")
    plans = state.get("plans")
    bindings = state.get("conversation_preview_bindings")
    return (
        type(missions) is list
        and len(missions) == 1
        and type(missions[0]) is dict
        and type(plans) is list
        and len(plans) == 1
        and type(plans[0]) is dict
        and type(bindings) is list
        and len(bindings) > 0
        and all(type(item) is dict for item in bindings)
    )


def _observe_mission_preview_or_terminal(
    store: StateStore,
    capture: _PtyTail,
    *,
    baseline_turn_ids: frozenset[str],
) -> tuple[dict[str, object] | None, dict[str, object]]:
    state: object = None
    try:
        state = store.load()
        if type(state) is not dict:
            raise _LeaderTerminalEvidenceInvalid
        journal_events = store.all_events()
        terminal = _project_leader_terminal(
            state, journal_events, baseline_turn_ids
        )
    except _LeaderTerminalEvidenceInvalid:
        raise _live_failure(
            "leader_terminal_evidence_invalid",
            state_snapshot=state if type(state) is dict else None,
            capture=capture,
        ) from None
    except Exception:
        raise _live_failure(
            "leader_terminal_evidence_invalid",
            state_snapshot=state if type(state) is dict else None,
            capture=capture,
        ) from None
    preview_ready = _mission_preview_ready(state)
    if preview_ready and terminal is not None:
        raise _live_failure(
            "leader_preview_terminal_conflict",
            state_snapshot=state,
            capture=capture,
        )
    if terminal is not None:
        raise _live_failure(
            terminal.code,
            state_snapshot=state,
            capture=capture,
            leader_terminal=terminal.diagnostics,
        )
    return (state if preview_ready else None), state
```

The broad final `Exception` catch is safe only because it emits one fixed code
and never includes `str(error)` or `repr(error)`.

- [x] **Step 6: Implement `_wait_for_mission_preview()`**

Use injectable clock/sleep/drain callables for deterministic tests:

```python
def _wait_for_mission_preview(
    store: StateStore,
    process: subprocess.Popen[bytes],
    master: int,
    capture: _PtyTail,
    *,
    baseline_turn_ids: frozenset[str],
    timeout_seconds: float = 180,
    monotonic: Any = time.monotonic,
    sleep: Any = time.sleep,
    drain: Any = _drain_pty,
) -> dict[str, object]:
    deadline = monotonic() + timeout_seconds

    def observe() -> tuple[dict[str, object] | None, dict[str, object]]:
        return _observe_mission_preview_or_terminal(
            store,
            capture,
            baseline_turn_ids=baseline_turn_ids,
        )

    while True:
        drain(master, capture, deadline=deadline)
        preview, last_state = observe()
        if preview is not None:
            return preview
        if process.poll() is not None:
            drain(master, capture, deadline=deadline)
            preview, last_state = observe()
            if preview is not None:
                return preview
            raise _live_failure(
                "bare_pty_exited_before_preview",
                state_snapshot=last_state,
                capture=capture,
            )
        if monotonic() >= deadline:
            drain(master, capture, deadline=deadline)
            preview, last_state = observe()
            if preview is not None:
                return preview
            if process.poll() is not None:
                raise _live_failure(
                    "bare_pty_exited_before_preview",
                    state_snapshot=last_state,
                    capture=capture,
                )
            raise _live_failure(
                "mission_preview_timeout",
                state_snapshot=last_state,
                capture=capture,
            )
        sleep(min(0.1, max(0.0, deadline - monotonic())))
```

The concrete implementation may remove duplication with a local function, but
must preserve the order: bounded drain -> durable observation -> process poll
-> bounded sleep, plus final drain/observation before exit or timeout.

- [x] **Step 7: Run wait tests and verify GREEN**

Run the Step 3 command again.

Expected: all selected tests PASS in well under one second of simulated time.

### Task 5: Integrate the specialized wait at the exact live gate

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:2580-2705`
- Test: `tests/test_m2c_live_acceptance.py:4020-4180`

- [x] **Step 1: Add RED integration tests for baseline capture and no second state load**

Update the existing pre-confirmation real-path test so it monkeypatches
`_wait_for_mission_preview`, not the first generic `_wait_for_state`. Its fake
must assert:

```python
assert process is fake_process
assert master == read_fd
assert capture is the in-process _PtyTail
assert baseline_turn_ids == frozenset(existing_turn_ids)
```

Retain `_wait_for_state` monkeypatching only for the later admission wait.

Add a terminal integration case whose store rejects a second load after the
terminal snapshot. Assert the failure has one terminal object, matching
cardinalities, and no raw state content.

- [x] **Step 2: Run integration tests and verify RED**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  python -m pytest tests/test_m2c_live_acceptance.py \
  -k 'preconfirmation_zero_write or create_and_confirm or snapshot_stable_terminal' \
  -q
```

Expected: FAIL because `_create_and_confirm_live_mission()` still uses the
generic Preview wait and does not capture baseline turn ids.

- [x] **Step 3: Replace only the first generic wait**

Immediately before writing the natural-language request:

```python
baseline_state = store.load()
try:
    baseline_turn_ids = _conversation_turn_ids(baseline_state)
except (TypeError, ValueError):
    raise _live_failure(
        "leader_terminal_evidence_invalid",
        state_snapshot=baseline_state if type(baseline_state) is dict else None,
        capture=capture,
    ) from None
```

After `os.write`, replace the current generic Preview `_wait_for_state` call
whose failure code is `mission_preview_timeout` with:

```python
previewed = _wait_for_mission_preview(
    store,
    process,
    master,
    capture,
    baseline_turn_ids=baseline_turn_ids,
)
```

Do not change later confirmation, admission, permission, completion, or generic
wait behavior.

- [x] **Step 4: Make final-exit diagnostics snapshot-stable**

Ensure every branch in the specialized wait passes the already observed state
snapshot to `_live_failure()`. It must not call `_live_failure(store=store)`
after terminal selection. Add or update the `reject_second_load=True` test to
prove this invariant.

- [x] **Step 5: Run integration tests and verify GREEN**

Run the Step 2 command again.

Expected: all selected tests PASS and confirmation bytes remain absent on every
pre-Preview failure.

- [x] **Step 6: Run the entire non-live M2c harness**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  python -m pytest tests/test_m2c_live_acceptance.py -q
```

Expected: all deterministic tests PASS and exactly one real live node SKIPS.
Do not set `AGENTDECK_M2C_LIVE=1`.

### Task 6: Synchronize SOP, validation, handoff, HISTORY, and plan

**Files:**

- Modify: `docs/validation/phase3-m2c-live-acceptance-sop.md`
- Modify: `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`
- Modify: `docs/superpowers/plans/2026-07-16-m2c-leader-preview-terminal-observability.md`

- [x] **Step 1: Update the SOP preflight command and payload**

Add the required model to the read-only command:

```bash
AGENTDECK_M2C_LEADER_MODEL="<audited-model-id>" \
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py::test_m2c_live_preflight_is_read_only -q -s
```

Document `m2c-live-preflight/v2`, the exact `leader_model` card, the three fixed
model blockers, and that the preflight validates identity only—it does not call
the model or prove availability.

- [x] **Step 2: Update the SOP live command and diagnostic table**

Add the same explicit model variable to the future live command. Document that
a different model requires a new preflight and authorization. Add the stable
before-Preview terminal codes, `bare_pty_exited_before_preview`,
`leader_terminal_evidence_invalid`, `leader_preview_terminal_conflict`, and the
strict meaning of genuine `mission_preview_timeout`.

State plainly that no raw PTY, prompt, stdout, stderr, model output, argv,
environment, or path is retained.

- [x] **Step 3: Preserve historical validation evidence**

Append a new dated subsection to the M2 validation report. It must say:

```text
The 954b868c run remains historically classified only as
mission_preview_timeout. The new harness does not retroactively infer timeout,
MCP, network, model, login, schema, or subprocess failure.
```

Record focused/non-live results available before the implementation commit and
say frozen double-full-suite/preflight evidence is pending.

- [x] **Step 4: Update handoff and HISTORY**

Set the active goal to frozen verification of M2c Leader Preview terminal
observability. Record:

- specialized Preview wait implemented;
- explicit model seal/preflight v2 implemented;
- production paths unchanged;
- no live attempt occurred;
- M2c remains BLOCKED and M3 remains locked;
- next gate is implementation commit -> two full suites -> one explicit-model
  read-only preflight -> separate live authorization.

Add the same facts and exact test counts to `HISTORY.md`.

- [x] **Step 5: Check completed plan boxes and documentation consistency**

Mark Tasks 1-6 complete only after their commands have run. Search all touched
documents for stale preflight v1/current hardcoded-live-model instructions:

```bash
rg -n 'm2c-live-preflight/v1|model = "gpt-5\.4"|AGENTDECK_M2C_LEADER_MODEL|mission_preview_timeout' \
  tests/test_m2c_live_acceptance.py \
  docs/validation/phase3-m2c-live-acceptance-sop.md \
  docs/validation/2026-07-13-phase3-m2-project-daemon.md \
  docs/handoff/current-development-state.md \
  HISTORY.md
```

Expected: v1 appears only as explicitly historical text if retained; the live
config writer has no model literal; fixture-only `gpt-5.4` values remain
clearly unrelated to live defaulting.

### Task 7: Review, commit, freeze, and verify without live execution

**Files:**

- Review: all files listed in the file map
- Modify after verification only: evidence sections in
  `docs/validation/2026-07-13-phase3-m2-project-daemon.md`,
  `docs/handoff/current-development-state.md`, `HISTORY.md`, and this plan

- [x] **Step 1: Run focused production-contract regressions**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_conversation_leader_diagnostics.py \
  tests/test_conversation_state.py \
  tests/test_conversation_session.py \
  -q
```

Expected: PASS. These prove the harness is consuming, not redefining, the
production terminal and outbox contracts.

- [x] **Step 2: Run syntax and diff verification**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  python -m compileall -q src tests
git diff --check
git status --short
```

Expected: compile and diff checks pass; status contains only the planned files.

- [x] **Step 3: Perform self-review against the written spec**

Verify every spec requirement has a test and implementation:

```text
durable terminal priority
all production stages mapped
exact four diagnostic fields
journal/outbox deduplication
same-snapshot failure projection
bounded drain and process poll
process exit distinct from genuine timeout
Preview/terminal conflict fail-closed
raw output/path/prompt leakage negative
explicit immutable model
preflight v2
missing/invalid/drift blockers
no fallback
no production or global behavior change
no live execution
```

Fix any issue with another RED/GREEN cycle before committing.

- [x] **Step 4: Create the single implementation commit**

Stage only the planned files:

```bash
git add \
  tests/test_m2c_live_acceptance.py \
  docs/validation/phase3-m2c-live-acceptance-sop.md \
  docs/validation/2026-07-13-phase3-m2-project-daemon.md \
  docs/handoff/current-development-state.md \
  HISTORY.md \
  docs/superpowers/plans/2026-07-16-m2c-leader-preview-terminal-observability.md
git commit -m "test: expose M2c Leader preview terminal"
```

Record the exact commit with `git rev-parse HEAD`. This is the frozen
implementation authority.

- [x] **Step 5: Run full suite one on the unchanged SHA**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  python -m pytest -q
```

Expected: PASS with only the repository's explicitly skipped live tests. Record
the exact count, skips, duration, and confirm the SHA is unchanged.

- [x] **Step 6: Run full suite two independently on the unchanged SHA**

Run the same command again in a new process.

Expected: the same test count and skips PASS; duration may differ. Confirm the
SHA is still the frozen implementation authority.

- [x] **Step 7: Check for an explicit human model input before preflight**

Run without printing the value:

```bash
test -n "${AGENTDECK_M2C_LEADER_MODEL:-}"
```

If this fails, stop before the designated preflight and report that the code
and double full suite are complete but the human-selected model id is missing.
Do not choose `gpt-5.4`, `gpt-5.5`, or any other value on the user's behalf.

- [ ] **Step 8: Run exactly one designated read-only preflight when model input exists**

Not run in this cycle: `AGENTDECK_M2C_LEADER_MODEL` was absent. Designated
preflight invocation count is zero; no model value was guessed.

Only if Step 7 passes, run:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py::test_m2c_live_preflight_is_read_only -q -s
```

Expected product result for readiness:

```text
schema_version=m2c-live-preflight/v2
ready=true
blockers=[]
leader_model.model=<the explicit audited value>
leader_model.ready=true
four tools ready
```

The pytest contract may pass while product readiness is false. If so, record
the exact fixed blockers and stop. Never rerun the designated preflight in this
cycle.

- [x] **Step 9: Audit zero residuals and unchanged authority**

Confirm no `agentdeck-m2c-live-*` root, controlled launcher, probe process,
M2c tmux session, or changed global config exists. Confirm the frozen SHA and
source/test bytes are unchanged by both full suites and preflight.

- [x] **Step 10: Record verification in one evidence-only commit**

Update only the validation report, handoff, HISTORY, and this plan with the
exact two-suite results, preflight result or explicit missing-model stop, frozen
implementation SHA, zero-residual audit, and `live attempt count = 0`.

Commit:

```bash
git add \
  docs/validation/2026-07-13-phase3-m2-project-daemon.md \
  docs/handoff/current-development-state.md \
  HISTORY.md \
  docs/superpowers/plans/2026-07-16-m2c-leader-preview-terminal-observability.md
git commit -m "docs: record M2c preview observability evidence"
```

- [x] **Step 11: Stop at the live authorization gate**

Do not run:

```text
AGENTDECK_M2C_LIVE=1
test_real_four_stage_m2c_acceptance
```

If preflight is ready, request separate human authorization naming the frozen
implementation SHA and exact model id. If preflight is blocked or the model id
is missing, report that blocker instead. M2c remains BLOCKED and M3 remains
locked in every outcome of this implementation plan.

Verification evidence for this cycle:

- frozen implementation SHA:
  `9db5b476f885cfcf68a55cbf59673a2d908d3fce`;
- full suite 1: `4219 passed, 2 skipped in 185.64s`;
- full suite 2: `4219 passed, 2 skipped in 191.59s`;
- designated preflight count: `0` (`AGENTDECK_M2C_LEADER_MODEL` missing);
- live attempt count: `0`;
- residual matching root/process count: `0`;
- frozen-SHA status after verification: clean.
