# M2c Bounded Sequential Permission Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the M2c live harness's exact-two-permissions assumption with an attempt-scoped, bounded driver that explicitly confirms every sequential Claude ACP permission and advances only after a validated reply and canonical handoff.

**Architecture:** Keep production `src/agentdeck/**` unchanged unless a deterministic RED proves a separately approved product defect. Add pure permission-lineage/effective-state projection, one bounded attempt driver, exact public preview/confirm binding, a shared four-stage completion validator, and a closed `permission_progress` diagnostic inside `tests/test_m2c_live_acceptance.py`; then integrate those units into the existing one-shot real Mission. Preserve the permission authority seal, takeover/return-control gate, cleanup authority, strict preflight v6, and separate preflight/live human authorizations.

**Tech Stack:** Python 3.12, pytest, AgentDeck `StateStore`, append-only ACP protocol records, daemon governance CLI, tmux observation, conda environment `agentdeck`, Git detached-worktree verification.

---

## Execution guard

The starting implementation authority `e83dcc482d2403f613485d06eff75ff99ffe733f`
and authority digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`
have consumed both their designated preflight and live nodes. They are never
rerun. Tasks 1-10 are deterministic and local. Task 11 performs read-only
installed-input discovery. Task 12 stops twice for explicit human authority:
once before the new designated preflight and again before the new real Mission.

No task may increase timeout values, auto-approve permissions, batch authority,
retry a live node, change a global Claude setting, install a tool, merge, push,
or begin M3 before real M2c PASS.

## File map

- Modify `tests/test_m2c_live_acceptance.py`: acceptance-only constants, pure
  lineage projection, bounded driver, exact public confirmation, closed
  diagnostics, shared completion validation, deterministic fixtures, and real
  flow integration.
- Modify `docs/superpowers/specs/2026-07-17-m2c-bounded-sequential-permission-acceptance-design.md`:
  implementation status and final evidence only; semantic changes require a
  new human design decision.
- Modify `docs/validation/phase3-m2c-live-acceptance-sop.md`: operational order,
  4/8 acceptance bounds, effective-state evidence, and one-shot gates.
- Modify `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`: immutable
  prior failure plus new candidate, preflight, and live evidence.
- Modify `docs/handoff/current-development-state.md`: active blocker, frozen
  SHA, verification, authority lifecycle, and final M2c/M3 status.
- Modify `HISTORY.md`: every RED, GREEN, documentation, freeze, preflight, and
  live result in the same commit as the corresponding change.
- Modify this plan: check boxes and record exact observed commands/results; do
  not pre-check a step.
- Do not modify `src/agentdeck/**` in this plan. A failing production contract
  test is evidence for a new scope decision, not implicit authority to edit it.

### Task 1: Commit the effective-state and lineage RED

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:9186-9415`
- Modify: `HISTORY.md:6-30`

- [x] **Step 1: Add a complete sequential-permission state fixture**

Place this fixture immediately after `_StaticLiveStore`. It must produce real
Mission/attempt/session/turn/binding/update/transition shapes and must retain
secret target/prompt sentinels so later leakage tests prove they are excluded.

```python
def _sequential_permission_state(
    *,
    step_position: int = 1,
    effective_states: tuple[str, ...] = ("pending",),
    attempt_state: str = "running",
    include_completion: bool = False,
) -> dict[str, object]:
    mission_id = "mis_0123456789ab"
    attempt_id = f"mat_00000000000{step_position}"
    session_id = f"ags_session{step_position}"
    turn_id = f"trn_turn{step_position}"
    state = _live_diagnostic_state(
        mission_status="running",
        attempt_state=attempt_state,
        reply_state="received",
        handoff_state="pending",
        handoff_status="blocked",
        permissions=[],
    )
    state["missions"][0]["mission_id"] = mission_id
    attempt = state["mission_attempts"][0]
    attempt.update(
        {
            "mission_id": mission_id,
            "attempt_id": attempt_id,
            "step_id": f"step_{step_position}",
            "step_position": step_position,
            "agent_id": "claude-worker",
            "configured_transport": "acp",
            "dispatch_key": f"dsp_step_{step_position}",
            "state": attempt_state,
        }
    )
    state["agent_sessions"] = [
        {"session_id": session_id, "agent_id": "claude-worker"}
    ]
    state["protocol_turns"] = [
        {
            "turn_id": turn_id,
            "session_id": session_id,
            "message_id": attempt["dispatch_key"],
        }
    ]
    state["transport_updates"] = []
    state["permission_requests"] = []
    state["mission_permission_bindings"] = []
    state["protocol_state_transitions"] = []
    for sequence, effective_state in enumerate(effective_states):
        permission_id = f"prm_step{step_position}_{sequence}"
        state["permission_requests"].append(
            {
                "permission_id": permission_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "tool_name": "edit",
                "target": "SECRET /private/project/artifact.txt",
                "risk": "write",
                "status": "pending",
            }
        )
        state["mission_permission_bindings"].append(
            {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "permission_id": permission_id,
            }
        )
        state["transport_updates"].append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "sequence": sequence,
                "kind": "permission_request",
                "payload": {
                    "permission_id": permission_id,
                    "tool_call_id": f"tool_{sequence}",
                    "prompt": "SECRET prompt",
                },
            }
        )
        if effective_state != "pending":
            state["protocol_state_transitions"].append(
                {
                    "entity_type": "permission",
                    "entity_id": permission_id,
                    "from_state": "pending",
                    "to_state": effective_state,
                }
            )
    if include_completion:
        reply = state["mission_worker_replies"][0]
        handoff = state["mission_handoffs"][0]
        reply.update(
            {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "dispatch_key": attempt["dispatch_key"],
                "state": "validated",
            }
        )
        handoff.update(
            {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "reply_id": reply["reply_id"],
                "state": "recorded",
                "canonical_handoff": {
                    "status": "completed",
                    "summary": "closed",
                    "verification": "closed",
                    "risks": "none",
                    "next_steps": "continue",
                },
            }
        )
    else:
        state["mission_worker_replies"] = []
        state["mission_handoffs"] = []
    return state
```

- [x] **Step 2: Add RED tests for transition-derived state and exact lineage**

```python
def test_attempt_permission_facts_derive_approved_then_pending() -> None:
    state = _sequential_permission_state(
        effective_states=("approved", "pending")
    )
    attempt = state["mission_attempts"][0]

    facts = _attempt_permission_facts(state, attempt)

    assert [(item.sequence, item.effective_state) for item in facts] == [
        (0, "approved"),
        (1, "pending"),
    ]
    assert all(item.permission_id.startswith("prm_") for item in facts)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("two_pending", "permission_order_ambiguous"),
        ("cross_attempt", "permission_lineage_invalid"),
        ("cross_session", "permission_lineage_invalid"),
        ("duplicate_sequence", "permission_order_ambiguous"),
        ("conflicting_transition", "permission_transition_invalid"),
    ),
)
def test_attempt_permission_facts_fail_closed(
    mutation: str, expected_code: str,
) -> None:
    state = _sequential_permission_state(
        effective_states=("approved", "pending")
    )
    if mutation == "two_pending":
        state["protocol_state_transitions"].clear()
    elif mutation == "cross_attempt":
        state["mission_permission_bindings"][1]["attempt_id"] = "mat_crossed"
    elif mutation == "cross_session":
        state["permission_requests"][1]["session_id"] = "ags_crossed"
    elif mutation == "duplicate_sequence":
        state["transport_updates"][1]["sequence"] = 0
    else:
        state["protocol_state_transitions"].append(
            {
                "entity_type": "permission",
                "entity_id": "prm_step1_0",
                "from_state": "pending",
                "to_state": "denied",
            }
        )

    with pytest.raises(_PermissionContractError) as error:
        _attempt_permission_facts(state, state["mission_attempts"][0])

    assert error.value.code == expected_code
    assert "SECRET" not in str(error.value)
    assert "/private" not in str(error.value)
```

- [x] **Step 3: Run RED and verify the exact missing unit**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'attempt_permission_facts' -q
```

Expected: FAIL because `_attempt_permission_facts` and
`_PermissionContractError` do not exist. No existing product or harness test
may fail first.

Observed: `6 failed, 339 deselected in 1.49s`; every failure was the expected
missing `_attempt_permission_facts` or `_PermissionContractError` symbol.

- [x] **Step 4: Record and commit RED**

Add the failing command and causal failure to `HISTORY.md`, then run:

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: require bounded M2c permission lineage"
```

### Task 2: Implement pure permission facts and effective state

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:170-220,2460-2750`
- Modify: `HISTORY.md:6-40`

- [x] **Step 1: Add acceptance-only bounds and closed types**

```python
_LIVE_MAX_PERMISSIONS_PER_CLAUDE_ATTEMPT = 4
_LIVE_MAX_PERMISSIONS_PER_MISSION = 8
_LIVE_CLAUDE_STEP_POSITIONS = frozenset({1, 3})
_LIVE_PERMISSION_EFFECTIVE_STATES = frozenset(
    {"pending", "approved", "denied", "expired"}
)


class _PermissionContractError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _LivePermissionFact:
    permission_id: str
    session_id: str
    turn_id: str
    sequence: int
    effective_state: str
```

- [x] **Step 2: Add a pure effective-state projector**

The implementation must accept exactly one immutable `pending` base record and
zero or one legal terminal transition. It must never use raw transition details
or exception text as a diagnostic.

```python
def _permission_effective_state(
    permission: Mapping[str, object],
    transitions: list[dict[str, object]],
) -> str:
    permission_id = permission.get("permission_id")
    if type(permission_id) is not str or permission.get("status") != "pending":
        raise _PermissionContractError("permission_transition_invalid")
    matches = [
        item
        for item in transitions
        if item.get("entity_type") == "permission"
        and item.get("entity_id") == permission_id
    ]
    if not matches:
        return "pending"
    if (
        len(matches) != 1
        or matches[0].get("from_state") != "pending"
        or matches[0].get("to_state") not in {"approved", "denied", "expired"}
    ):
        raise _PermissionContractError("permission_transition_invalid")
    return str(matches[0]["to_state"])
```

- [x] **Step 3: Implement exact attempt permission projection**

Implement `_attempt_permission_facts(state, attempt)` with these explicit
checks, in this order:

```python
def _attempt_permission_facts(
    state: object, attempt: object,
) -> tuple[_LivePermissionFact, ...]:
    if type(state) is not dict or type(attempt) is not dict:
        raise _PermissionContractError("permission_lineage_invalid")
    names = (
        "mission_permission_bindings",
        "permission_requests",
        "agent_sessions",
        "protocol_turns",
        "transport_updates",
        "protocol_state_transitions",
    )
    collections: dict[str, list[dict[str, object]]] = {}
    for name in names:
        value = state.get(name)
        if type(value) is not list or any(type(item) is not dict for item in value):
            raise _PermissionContractError("permission_lineage_invalid")
        collections[name] = value
    mission_id = attempt.get("mission_id")
    attempt_id = attempt.get("attempt_id")
    agent_id = attempt.get("agent_id")
    dispatch_key = attempt.get("dispatch_key")
    if any(type(value) is not str or not value for value in (
        mission_id, attempt_id, agent_id, dispatch_key,
    )):
        raise _PermissionContractError("permission_lineage_invalid")
    bindings = [
        item for item in collections["mission_permission_bindings"]
        if item.get("attempt_id") == attempt_id
    ]
    facts: list[_LivePermissionFact] = []
    sequences: set[int] = set()
    for binding in bindings:
        if binding.get("mission_id") != mission_id:
            raise _PermissionContractError("permission_lineage_invalid")
        permission_id = binding.get("permission_id")
        permissions = [
            item for item in collections["permission_requests"]
            if item.get("permission_id") == permission_id
        ]
        if type(permission_id) is not str or len(permissions) != 1:
            raise _PermissionContractError("permission_lineage_invalid")
        permission = permissions[0]
        sessions = [
            item for item in collections["agent_sessions"]
            if item.get("session_id") == permission.get("session_id")
        ]
        turns = [
            item for item in collections["protocol_turns"]
            if item.get("turn_id") == permission.get("turn_id")
        ]
        if (
            len(sessions) != 1
            or sessions[0].get("agent_id") != agent_id
            or len(turns) != 1
            or turns[0].get("session_id") != permission.get("session_id")
            or turns[0].get("message_id") != dispatch_key
        ):
            raise _PermissionContractError("permission_lineage_invalid")
        updates = [
            item for item in collections["transport_updates"]
            if item.get("kind") == "permission_request"
            and type(item.get("payload")) is dict
            and item["payload"].get("permission_id") == permission_id
        ]
        if (
            len(updates) != 1
            or updates[0].get("session_id") != permission.get("session_id")
            or updates[0].get("turn_id") != permission.get("turn_id")
            or type(updates[0].get("sequence")) is not int
            or updates[0]["sequence"] in sequences
        ):
            raise _PermissionContractError("permission_order_ambiguous")
        sequence = int(updates[0]["sequence"])
        sequences.add(sequence)
        effective = _permission_effective_state(
            permission, collections["protocol_state_transitions"]
        )
        facts.append(
            _LivePermissionFact(
                permission_id,
                str(permission["session_id"]),
                str(permission["turn_id"]),
                sequence,
                effective,
            )
        )
    facts.sort(key=lambda item: item.sequence)
    if sum(item.effective_state == "pending" for item in facts) > 1:
        raise _PermissionContractError("permission_order_ambiguous")
    return tuple(facts)
```

Insert these closed set/cardinality checks before returning `facts`:

```python
    bound_ids = [item.get("permission_id") for item in bindings]
    if (
        any(type(item) is not str for item in bound_ids)
        or len(bound_ids) != len(set(bound_ids))
    ):
        raise _PermissionContractError("permission_lineage_invalid")
    bound_id_set = set(bound_ids)
    attempt_turn_ids = {
        item.get("turn_id")
        for item in collections["protocol_turns"]
        if item.get("message_id") == dispatch_key
    }
    permission_updates = [
        item
        for item in collections["transport_updates"]
        if item.get("kind") == "permission_request"
        and item.get("turn_id") in attempt_turn_ids
    ]
    update_permission_ids = [
        item.get("payload", {}).get("permission_id")
        if type(item.get("payload")) is dict
        else None
        for item in permission_updates
    ]
    if (
        len(update_permission_ids) != len(bound_ids)
        or set(update_permission_ids) != bound_id_set
    ):
        raise _PermissionContractError("permission_lineage_invalid")
    permission_transitions = [
        item
        for item in collections["protocol_state_transitions"]
        if item.get("entity_type") == "permission"
        and item.get("entity_id") in bound_id_set
    ]
    if any(
        sum(item.get("entity_id") == permission_id for item in permission_transitions)
        > 1
        for permission_id in bound_id_set
    ):
        raise _PermissionContractError("permission_transition_invalid")
```

These checks return only the fixed codes and never include raw IDs or values.

- [x] **Step 4: Run focused GREEN and product governance regression**

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'attempt_permission_facts' -q
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_daemon_governance.py \
  -k 'permission_state_for_attempt or multiple_attempt_permissions' -q
```

Expected: both commands PASS. The product test must still prove that the latest
transport-ordered permission is authoritative.

Observed: lineage `6 passed, 339 deselected in 0.75s`; daemon governance `1
passed, 39 deselected in 0.31s`.

- [x] **Step 5: Commit GREEN**

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: derive M2c permission lineage"
```

### Task 3: Commit the bounded attempt-driver RED

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:9186-9550`
- Modify: `HISTORY.md`

- [x] **Step 1: Add a scripted store that advances only after confirmation**

```python
class _ScriptedPermissionStore:
    def __init__(self, snapshots: list[dict[str, object]]):
        self.snapshots = snapshots
        self.index = 0

    def load(self) -> dict[str, object]:
        return copy.deepcopy(self.snapshots[self.index])

    def advance(self) -> None:
        if self.index + 1 >= len(self.snapshots):
            raise AssertionError("permission script exhausted")
        self.index += 1
```

- [x] **Step 2: Add the exact regression for the real failure**

```python
def test_bounded_claude_attempt_confirms_two_permissions_before_handoff() -> None:
    first = _sequential_permission_state(effective_states=("pending",))
    second = _sequential_permission_state(
        effective_states=("approved", "pending")
    )
    completed = _sequential_permission_state(
        effective_states=("approved", "approved"),
        attempt_state="succeeded",
        include_completion=True,
    )
    store = _ScriptedPermissionStore([first, second, completed])
    confirmations: list[tuple[str, str, str]] = []

    def confirm(mission_id: str, attempt_id: str, permission_id: str) -> None:
        confirmations.append((mission_id, attempt_id, permission_id))
        store.advance()

    result = _drive_bounded_claude_attempt(
        store,
        mission_id="mis_0123456789ab",
        attempt_id="mat_000000000001",
        step_position=1,
        confirm_permission=confirm,
        timeout=0.5,
    )

    assert result["mission_attempts"][0]["state"] == "succeeded"
    assert [item[2] for item in confirmations] == [
        "prm_step1_0",
        "prm_step1_1",
    ]
```

This is the causal RED: after the first confirmation the driver must stay on
step 1, discover the second step-1 permission, confirm it independently, and
return only after reply plus handoff.

- [x] **Step 3: Add bounds, handoff, bridge, terminal, and ordering RED cases**

Add parameterized tests that assert these exact failures:

```python
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("fifth_attempt_permission", "permission_limit_exceeded"),
        ("ninth_mission_permission", "permission_limit_exceeded"),
        ("no_permission_completion", "permission_bridge_missing"),
        ("later_attempt_before_handoff", "next_stage_started_before_handoff"),
        ("failed_attempt", "attempt_terminal_before_handoff"),
    ),
)
def test_bounded_claude_attempt_fails_closed(
    mutation: str, expected_code: str,
) -> None:
    store, kwargs = _bounded_driver_failure_fixture(mutation)
    with pytest.raises(_LiveHarnessFailure) as error:
        _drive_bounded_claude_attempt(store, timeout=0.1, **kwargs)
    rendered = str(error.value)
    assert json.loads(rendered)["code"] == expected_code
    assert "SECRET" not in rendered
    assert "/private" not in rendered
```

Implement the failure fixture with explicit state mutations:

```python
def _bounded_driver_failure_fixture(
    mutation: str,
) -> tuple[_StaticLiveStore, dict[str, object]]:
    permission_count = 1
    attempt_state = "running"
    include_completion = False
    if mutation == "fifth_attempt_permission":
        permission_count = 5
    elif mutation == "ninth_mission_permission":
        permission_count = 4
    elif mutation == "failed_attempt":
        attempt_state = "failed"
    elif mutation == "no_permission_completion":
        permission_count = 0
        attempt_state = "succeeded"
        include_completion = True
    effective_states = tuple("approved" for _ in range(permission_count))
    if mutation == "fifth_attempt_permission":
        effective_states = ("approved", "approved", "approved", "approved", "pending")
    state = _sequential_permission_state(
        effective_states=effective_states,
        attempt_state=attempt_state,
        include_completion=include_completion,
    )
    if mutation == "later_attempt_before_handoff":
        later = dict(state["mission_attempts"][0])
        later.update(
            {
                "attempt_id": "mat_000000000002",
                "step_id": "step_2",
                "step_position": 2,
                "agent_id": "codex-worker",
                "configured_transport": "tmux",
            }
        )
        state["mission_attempts"].append(later)
    elif mutation == "ninth_mission_permission":
        revision = _sequential_permission_state(
            step_position=3,
            effective_states=("approved", "approved", "approved", "pending"),
        )
        for name in (
            "agent_sessions",
            "protocol_turns",
            "transport_updates",
            "permission_requests",
            "mission_permission_bindings",
            "protocol_state_transitions",
        ):
            state[name].extend(revision[name])
        extra = _sequential_permission_state(
            step_position=3, effective_states=("pending",)
        )
        for name in (
            "transport_updates",
            "permission_requests",
            "mission_permission_bindings",
        ):
            item = copy.deepcopy(extra[name][0])
            if name == "transport_updates":
                item["sequence"] = 4
                item["payload"]["permission_id"] = "prm_step3_4"
            elif name == "permission_requests":
                item["permission_id"] = "prm_step3_4"
            else:
                item["permission_id"] = "prm_step3_4"
            state[name].append(item)
    return _StaticLiveStore(state), {
        "mission_id": "mis_0123456789ab",
        "attempt_id": "mat_000000000001",
        "step_position": 1,
        "confirm_permission": lambda *_args: None,
    }
```

For `ninth_mission_permission`, initialize step 1 with four permissions before
adding the five step-3 records; the expected failure remains
`permission_limit_exceeded` before any confirmation call.

- [x] **Step 4: Run RED and commit**

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'bounded_claude_attempt' -q
```

Expected: FAIL because `_drive_bounded_claude_attempt` does not exist.

Observed: `6 failed, 345 deselected in 1.40s`; all six failures were the exact
missing `_drive_bounded_claude_attempt` symbol.

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: require bounded M2c permission progression"
```

### Task 4: Implement the bounded attempt state machine

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:3213-3345`
- Modify: `HISTORY.md`

- [x] **Step 1: Add a closed boundary value**

```python
@dataclass(frozen=True)
class _LiveAttemptBoundary:
    kind: str
    permission: _LivePermissionFact | None
    attempt_permission_count: int
    mission_permission_count: int
    effective_permission_states: tuple[str, ...]
```

Allowed `kind` values are `waiting`, `waiting_handoff`, `permission`,
`completed`, and the fixed diagnostic codes from the design. No arbitrary
string from state may become a kind.

- [x] **Step 2: Implement one coherent-snapshot boundary classifier**

Add `_attempt_permission_boundary(state, mission_id, attempt_id,
step_position)` with this closed classifier:

1. resolve exactly one Mission and exact current attempt;
2. require the expected Claude agent, ACP transport, and step position;
3. call `_attempt_permission_facts`;
4. reject a later attempt when the current reply/handoff is not complete;
5. return the unique pending permission while the attempt is active;
6. return `completed` only for `succeeded` plus exactly one validated reply and
   one recorded canonical completed handoff linked by reply ID and dispatch key;
7. return `waiting_handoff` for succeeded without those durable facts;
8. map failed/cancelled/interrupted/ambiguous to
   `attempt_terminal_before_handoff`;
9. return `waiting` for an active attempt with no new permission.

```python
def _attempt_permission_boundary(
    state: object,
    *,
    mission_id: str,
    attempt_id: str,
    step_position: int,
) -> _LiveAttemptBoundary:
    if type(state) is not dict or step_position not in _LIVE_CLAUDE_STEP_POSITIONS:
        raise _PermissionContractError("permission_lineage_invalid")
    attempts = state.get("mission_attempts")
    replies = state.get("mission_worker_replies")
    handoffs = state.get("mission_handoffs")
    bindings = state.get("mission_permission_bindings")
    if any(
        type(value) is not list or any(type(item) is not dict for item in value)
        for value in (attempts, replies, handoffs, bindings)
    ):
        raise _PermissionContractError("permission_lineage_invalid")
    exact = [
        item for item in attempts
        if item.get("mission_id") == mission_id
        and item.get("attempt_id") == attempt_id
    ]
    if len(exact) != 1:
        raise _PermissionContractError("permission_lineage_invalid")
    attempt = exact[0]
    if (
        attempt.get("step_id") != f"step_{step_position}"
        or attempt.get("step_position") != step_position
        or attempt.get("agent_id") != "claude-worker"
        or attempt.get("configured_transport") != "acp"
    ):
        raise _PermissionContractError("permission_lineage_invalid")
    facts = _attempt_permission_facts(state, attempt)
    mission_permission_count = sum(
        item.get("mission_id") == mission_id for item in bindings
    )
    effective = tuple(item.effective_state for item in facts)
    pending = [item for item in facts if item.effective_state == "pending"]
    reply_matches = [
        item for item in replies
        if item.get("mission_id") == mission_id
        and item.get("attempt_id") == attempt_id
        and item.get("dispatch_key") == attempt.get("dispatch_key")
    ]
    reply_id = (
        reply_matches[0].get("reply_id") if len(reply_matches) == 1 else None
    )
    handoff_matches = [
        item for item in handoffs
        if item.get("mission_id") == mission_id
        and item.get("attempt_id") == attempt_id
        and item.get("reply_id") == reply_id
    ]
    completed = (
        attempt.get("state") == "succeeded"
        and len(reply_matches) == 1
        and reply_matches[0].get("state") == "validated"
        and len(handoff_matches) == 1
        and handoff_matches[0].get("state") == "recorded"
        and type(handoff_matches[0].get("canonical_handoff")) is dict
        and handoff_matches[0]["canonical_handoff"].get("status") == "completed"
    )
    later_attempt = any(
        item.get("mission_id") == mission_id
        and type(item.get("step_position")) is int
        and item["step_position"] > step_position
        for item in attempts
    )
    base = {
        "attempt_permission_count": len(facts),
        "mission_permission_count": mission_permission_count,
        "effective_permission_states": effective,
    }
    if (
        len(facts) > _LIVE_MAX_PERMISSIONS_PER_CLAUDE_ATTEMPT
        or mission_permission_count > _LIVE_MAX_PERMISSIONS_PER_MISSION
    ):
        return _LiveAttemptBoundary("permission_limit_exceeded", None, **base)
    if later_attempt and not completed:
        return _LiveAttemptBoundary(
            "next_stage_started_before_handoff", None, **base
        )
    if completed:
        return _LiveAttemptBoundary("completed", None, **base)
    if attempt.get("state") == "succeeded":
        return _LiveAttemptBoundary("waiting_handoff", None, **base)
    if attempt.get("state") in {"failed", "cancelled", "interrupted", "ambiguous"}:
        return _LiveAttemptBoundary(
            "attempt_terminal_before_handoff", None, **base
        )
    if len(pending) == 1:
        return _LiveAttemptBoundary("permission", pending[0], **base)
    return _LiveAttemptBoundary("waiting", None, **base)
```

- [x] **Step 3: Implement one bounded wait loop with terminal-specific timeout**

```python
def _wait_for_attempt_boundary(
    store: StateStore,
    *,
    mission_id: str,
    attempt_id: str,
    step_position: int,
    timeout: float = 180,
    capture: _PtyTail | None = None,
) -> tuple[_LiveAttemptBoundary, dict[str, object]]:
    deadline = time.monotonic() + timeout
    last_kind = "waiting"
    while time.monotonic() < deadline:
        state = store.load()
        try:
            boundary = _attempt_permission_boundary(
                state,
                mission_id=mission_id,
                attempt_id=attempt_id,
                step_position=step_position,
            )
        except _PermissionContractError as error:
            raise _live_failure(
                error.code, state_snapshot=state, capture=capture
            ) from None
        last_kind = boundary.kind
        if boundary.kind not in {"waiting", "waiting_handoff"}:
            return boundary, state
        time.sleep(0.1)
    code = (
        "handoff_missing_after_attempt_success"
        if last_kind == "waiting_handoff"
        else "permission_wait_timeout"
    )
    raise _live_failure(code, store=store, capture=capture)
```

This is the only timer for one boundary wait. Do not call `_wait_for_state`
inside it and do not add a sleep outside it.

- [x] **Step 4: Implement the driver with injectable exact confirmation**

```python
def _drive_bounded_claude_attempt(
    store: StateStore,
    *,
    mission_id: str,
    attempt_id: str,
    step_position: int,
    confirm_permission: Any,
    timeout: float = 180,
    capture: _PtyTail | None = None,
    initial_boundary: tuple[_LiveAttemptBoundary, dict[str, object]] | None = None,
    verify_authority: Any = None,
) -> dict[str, object]:
    handled: set[str] = set()
    current = initial_boundary
    while True:
        boundary, state = current or _wait_for_attempt_boundary(
            store,
            mission_id=mission_id,
            attempt_id=attempt_id,
            step_position=step_position,
            timeout=timeout,
            capture=capture,
        )
        current = None
        if boundary.kind == "completed":
            if not handled and boundary.attempt_permission_count == 0:
                raise _live_failure(
                    "permission_bridge_missing",
                    state_snapshot=state,
                    capture=capture,
                )
            return state
        if boundary.kind != "permission" or boundary.permission is None:
            raise _live_failure(
                boundary.kind, state_snapshot=state, capture=capture
            )
        permission = boundary.permission
        if (
            permission.permission_id in handled
            or boundary.attempt_permission_count
            > _LIVE_MAX_PERMISSIONS_PER_CLAUDE_ATTEMPT
            or boundary.mission_permission_count
            > _LIVE_MAX_PERMISSIONS_PER_MISSION
        ):
            raise _live_failure(
                "permission_limit_exceeded",
                state_snapshot=state,
                capture=capture,
            )
        if verify_authority is not None:
            verify_authority()
        confirm_permission(mission_id, attempt_id, permission.permission_id)
        if verify_authority is not None:
            verify_authority()
        handled.add(permission.permission_id)
```

Immediately after `confirm_permission(...)`, add this post-confirmation check
before adding the ID to `handled`:

```python
        post_state = store.load()
        post_attempts = [
            item
            for item in post_state.get("mission_attempts", [])
            if type(item) is dict and item.get("attempt_id") == attempt_id
        ]
        try:
            post_facts = (
                _attempt_permission_facts(post_state, post_attempts[0])
                if len(post_attempts) == 1
                else ()
            )
        except _PermissionContractError:
            post_facts = ()
        post_matches = [
            item
            for item in post_facts
            if item.permission_id == permission.permission_id
            and item.effective_state == "approved"
        ]
        if len(post_matches) != 1:
            raise _live_failure(
                "permission_confirmation_invalid",
                state_snapshot=post_state,
                capture=capture,
            )
```

This closes a confirmation function that returns without committing the exact
approved transition.

- [x] **Step 5: Run driver GREEN and first-permission regressions**

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'bounded_claude_attempt or first_permission_wait' -q
```

Expected: PASS, including the old terminal-aware first-permission tests. No
timeout constant changes.

Observed: `23 passed, 328 deselected in 0.74s`; no timeout constant changed.

- [x] **Step 6: Commit GREEN**

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: drive bounded M2c permission progression"
```

### Task 5: Commit exact preview/confirm and diagnostic RED

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:2490-2720,5044-5072,9415-9805`
- Modify: `HISTORY.md`

- [x] **Step 1: Add exact public-control binding tests**

Monkeypatch `_json_project_command` and the ProjectView decision so the test
can independently drift the control command, preview response, and confirmation
response. Each of these fields must match the caller's exact identities:
`mission_id`, `attempt_id`, `permission_id`, `decision=approved`, preview ID,
and confirmation handle.

```python
@pytest.mark.parametrize(
    ("drift", "expected_code"),
    (
        ("control_permission", "permission_preview_invalid"),
        ("preview_attempt", "permission_preview_invalid"),
        ("preview_permission", "permission_preview_invalid"),
        ("confirm_mission", "permission_confirmation_invalid"),
        ("confirm_attempt", "permission_confirmation_invalid"),
        ("confirm_permission", "permission_confirmation_invalid"),
        ("confirm_preview", "permission_confirmation_invalid"),
        ("confirm_handle", "permission_confirmation_invalid"),
    ),
)
def test_confirm_pending_permission_requires_exact_lineage(
    tmp_path, monkeypatch, drift: str, expected_code: str,
) -> None:
    root, store = _permission_confirmation_fixture(tmp_path, monkeypatch, drift)
    with pytest.raises(_LiveHarnessFailure) as error:
        _confirm_pending_permission(
            root,
            store,
            mission_id="mis_0123456789ab",
            attempt_id="mat_000000000001",
            permission_id="prm_step1_0",
        )
    assert json.loads(str(error.value))["code"] == expected_code
```

Implement the fixture used above as follows. It provides a valid closed control,
then changes exactly one field selected by `drift`:

```python
@dataclass
class _PermissionProjectViewFixture:
    mission_recovery: dict[str, object]


def _permission_confirmation_fixture(tmp_path, monkeypatch, drift: str):
    root = tmp_path / "project"
    root.mkdir()
    store = StateStore(root)
    mission_id = "mis_0123456789ab"
    attempt_id = "mat_000000000001"
    permission_id = "prm_step1_0"
    preview = {
        "mode": "daemon_permission_preview",
        "mission_id": mission_id,
        "attempt_id": attempt_id,
        "permission_id": permission_id,
        "decision": "approved",
        "preview_id": "gov_0123456789ab",
        "confirmation_handle": "pcf_" + "2" * 24,
        "expires_at": "2026-07-17T12:00:00+00:00",
        "confirm_command": (
            "agentdeck daemon permission-confirm --handle pcf_" + "2" * 24
        ),
    }
    confirmed = {
        "mode": "daemon_permission_confirmed",
        "mission_id": mission_id,
        "attempt_id": attempt_id,
        "permission_id": permission_id,
        "decision": "approved",
        "preview_id": preview["preview_id"],
        "confirmation_handle": preview["confirmation_handle"],
        "state": "approved",
    }
    if drift == "preview_attempt":
        preview["attempt_id"] = "mat_crossed"
    elif drift == "preview_permission":
        preview["permission_id"] = "prm_crossed"
    elif drift == "confirm_mission":
        confirmed["mission_id"] = "mis_crossed"
    elif drift == "confirm_attempt":
        confirmed["attempt_id"] = "mat_crossed"
    elif drift == "confirm_permission":
        confirmed["permission_id"] = "prm_crossed"
    elif drift == "confirm_preview":
        confirmed["preview_id"] = "gov_crossed"
    elif drift == "confirm_handle":
        confirmed["confirmation_handle"] = "pcf_" + "3" * 24
    control_permission = (
        "prm_crossed" if drift == "control_permission" else permission_id
    )
    command = (
        "agentdeck daemon permission-preview "
        f"--mission-id {mission_id} --attempt-id {attempt_id} "
        f"--permission-id {control_permission} --decision approved"
    )
    view = _PermissionProjectViewFixture(
        mission_recovery={
            "decision": {
                "attempt_id": attempt_id,
                "controls": [
                    {
                        "kind": "permission_preview",
                        "command": command,
                        "enabled": True,
                    }
                ],
            }
        }
    )
    monkeypatch.setattr(store, "project_view", lambda _config: view)
    monkeypatch.setattr(sys.modules[__name__], "load_config", lambda _root: object())
    responses = iter((preview, confirmed))
    monkeypatch.setattr(
        sys.modules[__name__],
        "_json_project_command",
        lambda *_args, **_kwargs: next(responses),
    )
    return root, store
```

- [x] **Step 2: Add effective diagnostic RED**

```python
def test_permission_progress_projects_effective_states_without_leakage() -> None:
    state = _sequential_permission_state(
        effective_states=("approved", "pending")
    )
    rendered = str(
        _live_failure(
            "permission_wait_timeout",
            state_snapshot=state,
            permission_attempt_id="mat_000000000001",
            permission_step_position=1,
        )
    )
    diagnostic = json.loads(rendered)
    assert diagnostic["permission_progress"] == {
        "diagnostic_code": "permission_wait_timeout",
        "step_position": 1,
        "attempt_state": "running",
        "attempt_permission_count": 2,
        "mission_permission_count": 2,
        "effective_permission_states": ["approved", "pending"],
        "reply_count": 0,
        "handoff_count": 0,
    }
    assert diagnostic["ledger"]["permission_states"] == ["approved", "pending"]
    for forbidden in ("SECRET", "/private", "tool_", "prompt", "target"):
        assert forbidden not in rendered
```

- [x] **Step 3: Run RED and commit**

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'confirm_pending_permission_requires_exact_lineage or permission_progress' -q
```

Expected: FAIL because the current confirmation helper accepts no exact
identities and `_live_failure` has no closed permission projection.

Observed: `9 failed, 351 deselected in 4.14s`; eight exact-lineage cases failed
at the old helper signature and the projection case failed at the old
`_live_failure` signature.

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: require exact M2c permission evidence"
```

### Task 6: Bind public confirmation and close permission diagnostics

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:2490-2720,2995-3055,5044-5072`
- Modify: `HISTORY.md`

- [x] **Step 1: Change confirmation to require exact caller identities**

Use this signature:

```python
def _confirm_pending_permission(
    root: Path,
    store: StateStore,
    *,
    mission_id: str,
    attempt_id: str,
    permission_id: str,
) -> None:
```

Before running the preview command, require the ProjectView decision to name
the exact attempt and its sole enabled `kind=permission_preview` control to
parse to the exact Mission/attempt/permission/approved argv. Require the preview
payload to equal its exact nine-field shape and exact caller values.
Require the confirmation payload to equal:

```python
{
    "mode": "daemon_permission_confirmed",
    "mission_id": mission_id,
    "attempt_id": attempt_id,
    "permission_id": permission_id,
    "decision": "approved",
    "preview_id": preview["preview_id"],
    "confirmation_handle": preview["confirmation_handle"],
    "state": "approved",
}
```

Map preview/control drift to `permission_preview_invalid` and confirmation
drift to `permission_confirmation_invalid`. Keep lease IDs and raw command
output excluded.

Use exact equality rather than partial `.get()` checks:

```python
    expected_preview_identity = {
        "mission_id": mission_id,
        "attempt_id": attempt_id,
        "permission_id": permission_id,
        "decision": "approved",
    }
    if (
        type(decision) is not dict
        or decision.get("attempt_id") != attempt_id
        or len(controls) != 1
        or controls[0].get("kind") != "permission_preview"
        or controls[0].get("enabled") is not True
    ):
        raise _live_failure("permission_preview_invalid", store=store)
    try:
        preview_argv = shlex.split(str(controls[0].get("command", "")))
    except ValueError:
        raise _live_failure("permission_preview_invalid", store=store) from None
    expected_preview_argv = [
        "agentdeck", "daemon", "permission-preview",
        "--mission-id", mission_id,
        "--attempt-id", attempt_id,
        "--permission-id", permission_id,
        "--decision", "approved",
    ]
    if preview_argv != expected_preview_argv:
        raise _live_failure("permission_preview_invalid", store=store)
    preview = _json_project_command(
        preview_argv, cwd=root
    )
    if (
        set(preview) != {
            "mode", "mission_id", "attempt_id", "permission_id", "decision",
            "preview_id", "confirmation_handle", "expires_at", "confirm_command",
        }
        or preview.get("mode") != "daemon_permission_preview"
        or any(preview.get(key) != value for key, value in expected_preview_identity.items())
        or type(preview.get("confirmation_handle")) is not str
        or not str(preview["confirmation_handle"]).startswith("pcf_")
    ):
        raise _live_failure("permission_preview_invalid", store=store)
    confirmed = _json_project_command(
        shlex.split(str(preview["confirm_command"])), cwd=root
    )
    expected_confirmation = {
        "mode": "daemon_permission_confirmed",
        **expected_preview_identity,
        "preview_id": preview["preview_id"],
        "confirmation_handle": preview["confirmation_handle"],
        "state": "approved",
    }
    if confirmed != expected_confirmation or "lse_" in repr(confirmed):
        raise _live_failure("permission_confirmation_invalid", store=store)
```

- [x] **Step 2: Add exact `permission_progress` construction**

Add `_permission_progress_diagnostic(state, code, attempt_id,
step_position)`. It calls `_attempt_permission_facts`, counts only replies and
handoffs linked to the exact attempt, and returns exactly:

```python
_LIVE_PERMISSION_PROGRESS_KEYS = {
    "diagnostic_code",
    "step_position",
    "attempt_state",
    "attempt_permission_count",
    "mission_permission_count",
    "effective_permission_states",
    "reply_count",
    "handoff_count",
}
```

Use this implementation shape:

```python
def _permission_progress_diagnostic(
    state: object,
    *,
    code: str,
    attempt_id: str,
    step_position: int,
) -> dict[str, object]:
    closed = {
        "diagnostic_code": code,
        "step_position": step_position if step_position in {1, 3} else 0,
        "attempt_state": "unknown",
        "attempt_permission_count": -1,
        "mission_permission_count": -1,
        "effective_permission_states": [],
        "reply_count": -1,
        "handoff_count": -1,
    }
    if type(state) is not dict:
        return closed
    attempts = state.get("mission_attempts")
    bindings = state.get("mission_permission_bindings")
    replies = state.get("mission_worker_replies")
    handoffs = state.get("mission_handoffs")
    if any(
        type(value) is not list or any(type(item) is not dict for item in value)
        for value in (attempts, bindings, replies, handoffs)
    ):
        return closed
    exact = [item for item in attempts if item.get("attempt_id") == attempt_id]
    if len(exact) != 1:
        return closed
    attempt = exact[0]
    try:
        facts = _attempt_permission_facts(state, attempt)
    except _PermissionContractError:
        return closed
    mission_id = attempt.get("mission_id")
    closed.update(
        {
            "attempt_state": _closed_enum(
                attempt.get("state"), _LIVE_ATTEMPT_STATES
            ),
            "attempt_permission_count": len(facts),
            "mission_permission_count": sum(
                item.get("mission_id") == mission_id for item in bindings
            ),
            "effective_permission_states": [
                item.effective_state for item in facts
            ],
            "reply_count": sum(
                item.get("mission_id") == mission_id
                and item.get("attempt_id") == attempt_id
                for item in replies
            ),
            "handoff_count": sum(
                item.get("mission_id") == mission_id
                and item.get("attempt_id") == attempt_id
                for item in handoffs
            ),
        }
    )
    assert set(closed) == _LIVE_PERMISSION_PROGRESS_KEYS
    return closed
```

Extend `_live_failure` with keyword-only `permission_attempt_id` and
`permission_step_position`. Both must be supplied together. On valid input add
`permission_progress`; on invalid projection replace only that component with
the same eight keys using `unknown`, `-1`, and empty lists. Never include the
caught exception.

- [x] **Step 3: Make legacy permission states effective when lineage is valid**

In `_live_ledger_diagnostic`, replace the current raw-state comprehension with:

```python
    try:
        permission_facts = _attempt_permission_facts(durable, attempt)
    except _PermissionContractError:
        permission_facts = ()
    permission_states = [item.effective_state for item in permission_facts]
    if permission_records is not None and permissions and not permission_facts:
        permission_states = ["unknown" for _item in permissions]
```

Do not delete the already-delivered classification, terminal-stage, or handoff
fields.

Update `test_live_failure_prioritizes_any_permission_as_inconsistent` and the
malformed-lineage cases to expect `unknown` when they intentionally provide a
permission without session/turn/binding/update lineage. Keep the new
`test_permission_progress_projects_effective_states_without_leakage` as the
positive proof that fully linked records project `approved` then `pending`.

- [x] **Step 4: Run diagnostic, confirmation, and leakage GREEN**

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'confirm_pending_permission or permission_progress or live_failure' -q
```

Expected: PASS. `_LIVE_LEDGER_KEYS` remains closed and the new
`_LIVE_PERMISSION_PROGRESS_KEYS` is separately exact.

Observed: `46 passed, 314 deselected in 0.78s`; both closed key sets and
leakage assertions passed.

- [x] **Step 5: Commit GREEN**

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: bind exact M2c permission evidence"
```

### Task 7: Commit the shared four-stage completion RED

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:5450-5635,9805-10150`
- Modify: `HISTORY.md`

- [x] **Step 1: Add a complete four-stage state fixture**

Build four succeeded attempts with agents
`claude-worker/codex-worker/claude-worker/codex-worker`, four validated replies,
four canonical handoffs, three correctly ordered handoff-before-submit event
pairs, and configurable Claude permission counts. Use this fixture:

```python
def _four_stage_completion_fixture(
    *,
    implementation_permissions: int,
    revision_permissions: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    mission_id = "mis_0123456789ab"
    state = _sequential_permission_state(
        step_position=1,
        effective_states=tuple(
            "approved" for _ in range(implementation_permissions)
        ),
        attempt_state="succeeded",
        include_completion=True,
    )
    state["missions"][0]["status"] = "completed"
    implementation_protocol = {
        name: copy.deepcopy(state[name])
        for name in (
            "agent_sessions",
            "protocol_turns",
            "transport_updates",
            "permission_requests",
            "mission_permission_bindings",
            "protocol_state_transitions",
        )
    }
    revision = _sequential_permission_state(
        step_position=3,
        effective_states=tuple("approved" for _ in range(revision_permissions)),
        attempt_state="succeeded",
        include_completion=True,
    )
    agents = ("claude-worker", "codex-worker", "claude-worker", "codex-worker")
    transports = ("acp", "tmux", "acp", "tmux")
    attempts: list[dict[str, object]] = []
    replies: list[dict[str, object]] = []
    handoffs: list[dict[str, object]] = []
    for position, (agent_id, transport) in enumerate(
        zip(agents, transports, strict=True), start=1
    ):
        attempt_id = f"mat_00000000000{position}"
        dispatch_key = f"dsp_step_{position}"
        reply_id = f"mrp_step_{position}"
        attempts.append(
            {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "step_id": f"step_{position}",
                "step_position": position,
                "agent_id": agent_id,
                "configured_transport": transport,
                "dispatch_key": dispatch_key,
                "state": "succeeded",
                "receipt_summary": "closed",
            }
        )
        replies.append(
            {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "reply_id": reply_id,
                "dispatch_key": dispatch_key,
                "state": "validated",
            }
        )
        handoffs.append(
            {
                "mission_id": mission_id,
                "attempt_id": attempt_id,
                "reply_id": reply_id,
                "state": "recorded",
                "canonical_handoff": {
                    "status": "completed",
                    "summary": "closed",
                    "verification": "closed",
                    "risks": "none",
                    "next_steps": "continue",
                },
            }
        )
    state["mission_attempts"] = attempts
    state["mission_worker_replies"] = replies
    state["mission_handoffs"] = handoffs
    for name, values in implementation_protocol.items():
        state[name] = values + copy.deepcopy(revision[name])
    events: list[dict[str, object]] = []
    for index in range(3):
        events.extend(
            [
                {
                    "event_type": "mission_handoff_evidence_recorded",
                    "payload": {"attempt_id": attempts[index]["attempt_id"]},
                },
                {
                    "event_type": "mission_attempt_submitted",
                    "payload": {"attempt_id": attempts[index + 1]["attempt_id"]},
                },
            ]
        )
    return state, events


def _four_stage_completion_failure_fixture(
    mutation: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    counts = {
        "implementation_zero": (0, 1),
        "revision_zero": (1, 0),
        "implementation_five": (5, 1),
        "revision_five": (1, 5),
        "total_nine": (4, 5),
    }
    implementation, revision = counts.get(mutation, (1, 1))
    state, events = _four_stage_completion_fixture(
        implementation_permissions=implementation,
        revision_permissions=revision,
    )
    if mutation == "reply_missing":
        state["mission_worker_replies"].pop()
    elif mutation == "handoff_missing":
        state["mission_handoffs"].pop()
    elif mutation == "submit_before_handoff":
        events[0], events[1] = events[1], events[0]
    return state, events
```

All Claude permission records therefore use the exact lineage fixture from Task
1. A zero-count Claude phase has an exact ACP attempt but no permission binding.

- [x] **Step 2: Add completion tests that reject exact-two hard-coding**

```python
@pytest.mark.parametrize(
    ("implementation_permissions", "revision_permissions"),
    ((1, 1), (2, 1), (1, 3), (4, 4)),
)
def test_four_stage_completion_accepts_bounded_permission_counts(
    implementation_permissions: int,
    revision_permissions: int,
) -> None:
    state, events = _four_stage_completion_fixture(
        implementation_permissions=implementation_permissions,
        revision_permissions=revision_permissions,
    )
    evidence = _validate_four_stage_completion(state, events)
    assert evidence.permission_count == (
        implementation_permissions + revision_permissions
    )
    assert evidence.attempt_count == 4
    assert evidence.handoff_count == 4


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("implementation_zero", "permission_bridge_missing"),
        ("revision_zero", "permission_bridge_missing"),
        ("implementation_five", "permission_limit_exceeded"),
        ("revision_five", "permission_limit_exceeded"),
        ("total_nine", "permission_limit_exceeded"),
        ("reply_missing", "reply_facts_invalid"),
        ("handoff_missing", "canonical_handoff_facts_invalid"),
        ("submit_before_handoff", "next_stage_started_before_handoff"),
    ),
)
def test_four_stage_completion_fails_closed(
    mutation: str, expected_code: str,
) -> None:
    state, events = _four_stage_completion_failure_fixture(mutation)
    with pytest.raises(_LiveHarnessFailure) as error:
        _validate_four_stage_completion(state, events)
    assert json.loads(str(error.value))["code"] == expected_code
```

- [x] **Step 3: Run RED and commit**

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'four_stage_completion' -q
```

Expected: FAIL because `_validate_four_stage_completion` does not exist.

Observed: `12 failed, 360 deselected in 2.10s`; every failure was the missing
shared completion validator.

```bash
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: require bounded M2c completion evidence"
```

### Task 8: Integrate both Claude phases and shared completion validation

**Files:**

- Modify: `tests/test_m2c_live_acceptance.py:5350-5635`
- Modify: `HISTORY.md`

- [x] **Step 1: Implement the shared completion validator**

Add a frozen `_LiveCompletionEvidence` with attempt, reply, handoff,
inter-stage-link, and permission counts:

```python
@dataclass(frozen=True)
class _LiveCompletionEvidence:
    attempt_count: int
    reply_count: int
    handoff_count: int
    inter_stage_link_count: int
    permission_count: int


def _validate_four_stage_completion(
    state: object,
    events: object,
) -> _LiveCompletionEvidence:
    if type(state) is not dict or type(events) is not list:
        raise _live_failure("attempt_terminal_facts_invalid", state_snapshot={})
    attempts = state.get("mission_attempts")
    replies = state.get("mission_worker_replies")
    handoffs = state.get("mission_handoffs")
    if (
        type(attempts) is not list
        or len(attempts) != 4
        or any(type(item) is not dict for item in attempts)
        or [item.get("step_position") for item in attempts] != [1, 2, 3, 4]
        or [item.get("agent_id") for item in attempts]
        != ["claude-worker", "codex-worker", "claude-worker", "codex-worker"]
        or any(item.get("state") != "succeeded" for item in attempts)
    ):
        raise _live_failure("attempt_terminal_facts_invalid", state_snapshot=state)
    if (
        type(replies) is not list
        or len(replies) != 4
        or any(type(item) is not dict or item.get("state") != "validated" for item in replies)
    ):
        raise _live_failure("reply_facts_invalid", state_snapshot=state)
    if (
        type(handoffs) is not list
        or len(handoffs) != 4
        or any(
            type(item) is not dict
            or item.get("state") != "recorded"
            or type(item.get("canonical_handoff")) is not dict
            or item["canonical_handoff"].get("status") != "completed"
            for item in handoffs
        )
    ):
        raise _live_failure(
            "canonical_handoff_facts_invalid", state_snapshot=state
        )
    claude_facts = [
        _attempt_permission_facts(state, attempts[index]) for index in (0, 2)
    ]
    counts = [len(items) for items in claude_facts]
    if any(count == 0 for count in counts):
        raise _live_failure("permission_bridge_missing", state_snapshot=state)
    if (
        any(count > _LIVE_MAX_PERMISSIONS_PER_CLAUDE_ATTEMPT for count in counts)
        or sum(counts) > _LIVE_MAX_PERMISSIONS_PER_MISSION
    ):
        raise _live_failure("permission_limit_exceeded", state_snapshot=state)
    if any(
        item.effective_state != "approved"
        for facts in claude_facts
        for item in facts
    ):
        raise _live_failure("permission_transition_invalid", state_snapshot=state)
    inter_stage_links = 0
    for index in range(3):
        predecessor = attempts[index]["attempt_id"]
        successor = attempts[index + 1]["attempt_id"]
        handoff_positions = [
            position
            for position, event in enumerate(events)
            if type(event) is dict
            and event.get("event_type") == "mission_handoff_evidence_recorded"
            and event.get("payload", {}).get("attempt_id") == predecessor
        ]
        submit_positions = [
            position
            for position, event in enumerate(events)
            if type(event) is dict
            and event.get("event_type") == "mission_attempt_submitted"
            and event.get("payload", {}).get("attempt_id") == successor
        ]
        if (
            len(handoff_positions) != 1
            or len(submit_positions) != 1
            or handoff_positions[0] >= submit_positions[0]
        ):
            raise _live_failure(
                "next_stage_started_before_handoff", state_snapshot=state
            )
        inter_stage_links += 1
    return _LiveCompletionEvidence(
        attempt_count=4,
        reply_count=4,
        handoff_count=4,
        inter_stage_link_count=inter_stage_links,
        permission_count=sum(counts),
    )
```

After the reply/handoff cardinality blocks and before permission projection,
add these exact linkage checks:

```python
    mission_id = attempts[0].get("mission_id")
    for index, attempt in enumerate(attempts):
        reply = replies[index]
        handoff = handoffs[index]
        if (
            attempt.get("mission_id") != mission_id
            or reply.get("mission_id") != mission_id
            or reply.get("attempt_id") != attempt.get("attempt_id")
            or reply.get("dispatch_key") != attempt.get("dispatch_key")
        ):
            raise _live_failure("reply_facts_invalid", state_snapshot=state)
        if (
            handoff.get("mission_id") != mission_id
            or handoff.get("attempt_id") != attempt.get("attempt_id")
            or handoff.get("reply_id") != reply.get("reply_id")
        ):
            raise _live_failure(
                "canonical_handoff_facts_invalid", state_snapshot=state
            )
```

A mismatch therefore uses a fixed code without printing the mismatched values.

- [x] **Step 2: Replace the first-stage exact-one flow**

After daemon admission:

1. wait for the exact step-1 Claude ACP attempt;
2. call `_drive_bounded_claude_attempt` with exact Mission/attempt IDs;
3. pass an exact confirmation closure that calls
   `_confirm_pending_permission(root, store, mission_id=..., attempt_id=...,
   permission_id=...)`;
4. pass a permission-settings/executable-seal authority verifier;
5. remove the direct single `_confirm_pending_permission` call;
6. remove the predicate that assumes permission count 2 and attempt count 3.

The replacement call site is:

```python
        first_pending = _wait_for_first_permission_or_terminal_attempt(store)
        first_attempt = first_pending["mission_attempts"][0]

        def confirm_exact(
            exact_mission_id: str,
            exact_attempt_id: str,
            exact_permission_id: str,
        ) -> None:
            _confirm_pending_permission(
                root,
                store,
                mission_id=exact_mission_id,
                attempt_id=exact_attempt_id,
                permission_id=exact_permission_id,
            )

        _drive_bounded_claude_attempt(
            store,
            mission_id=mission_id,
            attempt_id=str(first_attempt["attempt_id"]),
            step_position=1,
            confirm_permission=confirm_exact,
            capture=capture,
            initial_boundary=(
                _attempt_permission_boundary(
                    first_pending,
                    mission_id=mission_id,
                    attempt_id=str(first_attempt["attempt_id"]),
                    step_position=1,
                ),
                first_pending,
            ),
            verify_authority=lambda: (
                _verify_live_claude_permission_settings(root, permission_settings),
                _verify_all_executable_seals(all_seals),
            ),
        )
```

- [x] **Step 3: Establish the revision takeover window without confirmation**

Wait for the exact step-3 attempt and its first pending permission only after
step-2's validated reply and canonical handoff. Preserve workbench validation,
tmux pane observation, takeover, and return-control. Capture the pending
permission's effective state before takeover and prove it remains `pending`
while human-owned. After return-control, re-run exact authority projection and
fail `takeover_authority_drift` on any attempt/session/turn/permission change.

Resolve `revision_attempt` from one bounded state wait before the boundary
snippet:

```python
        revision_started = _wait_for_state(
            store,
            lambda state: any(
                type(item) is dict
                and item.get("mission_id") == mission_id
                and item.get("step_position") == 3
                and item.get("agent_id") == "claude-worker"
                and item.get("configured_transport") == "acp"
                for item in state.get("mission_attempts", [])
            ),
            code="revision_attempt_start_timeout",
            capture=capture,
        )
        revision_matches = [
            item
            for item in revision_started["mission_attempts"]
            if item.get("mission_id") == mission_id
            and item.get("step_position") == 3
        ]
        if len(revision_matches) != 1:
            raise _live_failure(
                "permission_lineage_invalid", state_snapshot=revision_started
            )
        revision_attempt = revision_matches[0]
```

Pass that already-observed boundary into `_drive_bounded_claude_attempt` as
`initial_boundary`; only then may the first revision permission be confirmed.
The driver continues through any later revision permissions.

Use an exact snapshot tuple and compare the first permission fact before and
after human ownership:

```python
        revision_boundary, revision_state = _wait_for_attempt_boundary(
            store,
            mission_id=mission_id,
            attempt_id=str(revision_attempt["attempt_id"]),
            step_position=3,
            capture=capture,
        )
        if revision_boundary.kind != "permission":
            raise _live_failure(
                revision_boundary.kind,
                state_snapshot=revision_state,
                capture=capture,
            )
        authority_before = revision_boundary.permission
        # Existing pane observation and worker.takeover execute here.
        while_owned = store.load()
        while_facts = _attempt_permission_facts(
            while_owned,
            next(
                item for item in while_owned["mission_attempts"]
                if item.get("attempt_id") == revision_attempt["attempt_id"]
            ),
        )
        if authority_before not in while_facts:
            raise _live_failure(
                "takeover_authority_drift", state_snapshot=while_owned
            )
        # Existing worker.return-control executes here.
        returned_state = store.load()
        returned_boundary = _attempt_permission_boundary(
            returned_state,
            mission_id=mission_id,
            attempt_id=str(revision_attempt["attempt_id"]),
            step_position=3,
        )
        if returned_boundary.permission != authority_before:
            raise _live_failure(
                "takeover_authority_drift", state_snapshot=returned_state
            )
        _drive_bounded_claude_attempt(
            store,
            mission_id=mission_id,
            attempt_id=str(revision_attempt["attempt_id"]),
            step_position=3,
            confirm_permission=confirm_exact,
            capture=capture,
            initial_boundary=(returned_boundary, returned_state),
            verify_authority=lambda: (
                _verify_live_claude_permission_settings(root, permission_settings),
                _verify_all_executable_seals(all_seals),
            ),
        )
```

- [x] **Step 4: Replace final exact-two assertions**

After Mission completion, call `_validate_four_stage_completion(completed,
events)`. Use its counts in sanitized PASS evidence. Delete:

```python
len(permissions) == 2
and all(item.get("status") == "approved" for item in permissions)
```

Do not replace it with raw `status == approved`; base records remain pending.
The shared validator requires effective `approved` transitions.

- [x] **Step 5: Run integration GREEN**

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'bounded_claude_attempt or four_stage_completion or takeover or permission_progress or confirm_pending_permission' -q
```

Expected: PASS. The real preflight/live nodes remain skipped because their
explicit environment gates are absent.

Observed: `27 passed, 345 deselected in 2.22s`; gated real nodes did not run.

- [x] **Step 6: Prove harness-only scope and commit**

```bash
test -z "$(git diff ba083d92..HEAD --name-only -- src/agentdeck)"
git diff --check
git add tests/test_m2c_live_acceptance.py HISTORY.md
git commit -m "test: accept bounded sequential M2c permissions"
```

Expected: source-scope command prints nothing; commit succeeds.

Observed: source-scope output was empty, `git diff --check` passed, and conda
Python compiled `tests/test_m2c_live_acceptance.py` successfully.

### Task 9: Synchronize durable SOP, validation, handoff, and history

**Files:**

- Modify: `docs/validation/phase3-m2c-live-acceptance-sop.md`
- Modify: `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `docs/superpowers/specs/2026-07-17-m2c-bounded-sequential-permission-acceptance-design.md`
- Modify: `HISTORY.md`
- Modify: `docs/superpowers/plans/2026-07-17-m2c-bounded-sequential-permission-acceptance.md`

- [x] **Step 1: Update the SOP with operationally exact semantics**

Document:

- each Claude attempt can request 1-4 sequential permissions;
- each permission receives its own public preview and exact confirm;
- total Mission permission count is 2-8;
- base records remain pending and evidence uses transition-derived state;
- takeover pauses confirmation until return-control and authority revalidation;
- reply plus handoff gates the next stage;
- limit, lineage, transition, preview, confirmation, handoff, terminal, timeout,
  and takeover failures use the fixed codes from the spec;
- no timeout change, retry, auto-approval, global settings change, merge, or
  push is permitted.

- [x] **Step 2: Preserve old failure evidence and add the new candidate section**

Do not edit the facts of the exhausted `e83dcc48...` live node. Append the RED,
GREEN, focused, non-live, product, scope, and cleanup results from Tasks 1-8.
Mark the new implementation unfrozen until Task 10 records its exact SHA.

- [x] **Step 3: Update handoff and design status**

The handoff must say that the previous blocker is explained by a harness
cardinality assumption, not a product ACP defect. The design status becomes
`Implemented; deterministic verification pending` only after Task 8 passes.

- [x] **Step 4: Run documentation consistency checks**

```bash
rg -n 'third_stage_safe_window_timeout|len\(permissions\) == 2|exactly two|permission_limit_exceeded|permission_progress|M3' \
  tests/test_m2c_live_acceptance.py \
  docs/superpowers/specs/2026-07-17-m2c-bounded-sequential-permission-acceptance-design.md \
  docs/validation/phase3-m2c-live-acceptance-sop.md \
  docs/validation/2026-07-17-phase3-m2c-four-stage-live.md \
  docs/handoff/current-development-state.md \
  HISTORY.md
git diff --check
```

Expected: exact-two language appears only as historical rejected behavior;
`third_stage_safe_window_timeout` remains only in immutable historical
evidence; M3 remains locked.

Observed: `len(permissions) == 2` is absent from the live harness; the only
in-scope `exactly two` wording rejects the historical assumption;
`third_stage_safe_window_timeout` appears only in preserved historical
evidence; current implementation and documentation expose
`permission_limit_exceeded` / `permission_progress`; M3 remains locked; and
`git diff --check` passed.

- [x] **Step 5: Commit documentation**

```bash
git add HISTORY.md \
  docs/handoff/current-development-state.md \
  docs/superpowers/specs/2026-07-17-m2c-bounded-sequential-permission-acceptance-design.md \
  docs/superpowers/plans/2026-07-17-m2c-bounded-sequential-permission-acceptance.md \
  docs/validation/phase3-m2c-live-acceptance-sop.md \
  docs/validation/2026-07-17-phase3-m2c-four-stage-live.md
git commit -m "docs: record bounded M2c permission progression"
```

### Task 10: Verify, review, and freeze one unchanged candidate

**Files:**

- Review: every file in the file map
- Modify after commands: `HISTORY.md`, `docs/handoff/current-development-state.md`,
  `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`, and this plan

- [x] **Step 1: Run focused sequential-permission verification**

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py \
  -k 'attempt_permission_facts or bounded_claude_attempt or four_stage_completion or confirm_pending_permission or permission_progress or first_permission_wait or takeover' -q
```

Expected: PASS with zero failures and no real node execution.

Observed: `50 passed, 322 deselected in 0.46s`; no real node executed.

- [x] **Step 2: Run complete non-live M2c**

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py -q
```

Expected: zero failures; designated preflight and real live tests are skipped.

Observed: `370 passed, 2 skipped in 102.28s`; the two skips were the designated
preflight and real live nodes.

- [x] **Step 3: Run product/Conversation/contract/provider regressions**

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest tests/test_conversation_session.py tests/test_conversation_terminal_ui.py \
  tests/test_conversation_contracts.py tests/test_contracts.py \
  tests/test_cli_structured_output.py tests/test_dashboard.py \
  tests/test_provider_openai_compatible.py -q
```

Expected baseline: `851 passed`; any count change must be explained by current
branch collection rather than ignored.

Observed: `851 passed in 4.51s`; the baseline is unchanged.

- [x] **Step 4: Run syntax, diff, scope, leakage, and residue audits**

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  python -m compileall -q src tests
git diff --check
test -z "$(git diff ba083d92..HEAD --name-only -- src/agentdeck)"
test -z "$(git status --short | rg '(^|/)(\.agentdeck|\.omc/sessions)(/|$)' || true)"
pgrep -af 'agentdeck.*daemon|claude-agent-acp|agentdeck-m2c-live' || true
tmux list-sessions 2>/dev/null | rg 'agentdeck-m2c' || true
git worktree list --porcelain
```

Expected: compile and diff pass; no product source change; no tracked runtime
state or M2c process/session residue; only the main checkout and intended
development worktree remain.

Observed: all gates passed. No `src/agentdeck/**` or tracked runtime-state
change exists; no M2c tmux session or durable process remains; the transient
`pgrep` self-match disappeared on exact process inspection; only the main
checkout and intended development worktree exist.

- [x] **Step 5: Review against every spec requirement**

Use `superpowers:requesting-code-review` if execution mode permits it. Review
must explicitly verify:

- one permission equals one exact preview/confirm/effect authority;
- multiple sequential permissions stay in one attempt;
- 4/8 bounds are test-only;
- reply plus handoff gates stage progression;
- takeover prevents confirmation until return-control;
- effective state comes from transitions;
- all new diagnostics are closed and path-free;
- no timeout, retry, fallback, global setting, or product source change.

Resolve every finding before freezing. Any implementation edit after the two
full suites begins invalidates those suites and restarts this task.

Observed: local requirement review found no issues. The review skill's
subagent path was unavailable under the current no-unsolicited-delegation
execution rule, so the same checklist was inspected directly against the
diff, implementation, and focused tests. Every listed property is satisfied.

- [x] **Step 6: Record results, commit, and freeze**

Write exact counts/timings and audit results to the four evidence files, then:

```bash
git add HISTORY.md docs/handoff/current-development-state.md \
  docs/validation/2026-07-17-phase3-m2c-four-stage-live.md \
  docs/superpowers/plans/2026-07-17-m2c-bounded-sequential-permission-acceptance.md
git commit -m "docs: freeze bounded M2c permission acceptance"
FROZEN_SHA="$(git rev-parse HEAD)"
printf '%s\n' "$FROZEN_SHA"
git status --short
```

Expected: one full SHA and a clean worktree. From this point no file may change
until both full suites complete. Evidence-document edits after the suites create
a later documentation commit; the implementation identity recorded for
preflight remains the exact frozen implementation SHA defined by the SOP.

### Task 11: Run two serial full suites and reconstruct installed authority

**Files:**

- Modify after commands: `HISTORY.md`, `docs/handoff/current-development-state.md`,
  `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`, and this plan

- [x] **Step 1: Create two detached worktrees at the exact frozen SHA**

```bash
FROZEN_SHA="$(git rev-parse HEAD)"
SUITE_A="$(mktemp -d /tmp/agentdeck-m2c-suite-a.XXXXXX)"
SUITE_B="$(mktemp -d /tmp/agentdeck-m2c-suite-b.XXXXXX)"
rmdir "$SUITE_A" "$SUITE_B"
git worktree add --detach "$SUITE_A" "$FROZEN_SHA"
git worktree add --detach "$SUITE_B" "$FROZEN_SHA"
```

Observed: created two detached worktrees at exact frozen SHA
`df25532d0bd4fb9c8dd57fd119607a05411d11db`.

- [x] **Step 2: Run full suite A, then suite B serially**

```bash
cd "$SUITE_A"
PYTHONPATH="$SUITE_A/src" conda run --no-capture-output -n agentdeck pytest -q
cd "$SUITE_B"
PYTHONPATH="$SUITE_B/src" conda run --no-capture-output -n agentdeck pytest -q
```

Expected: both complete suites PASS with the same collected/pass/skip counts.
They must not run in parallel and must not use a direct interpreter whose child
PATH omits the conda `agentdeck` command.

Observed: suite A passed `4461 passed, 3 skipped in 250.94s`; only after it
completed, suite B passed `4461 passed, 3 skipped in 245.06s`. Both used the
`agentdeck` conda environment and identical collection counts.

- [x] **Step 3: Remove both worktrees and prove zero residue**

```bash
cd /Users/liuyue/.config/superpowers/worktrees/multi-agent-explore/codex/m2c-leader-preview-observability
git worktree remove "$SUITE_A"
git worktree remove "$SUITE_B"
git worktree prune
test ! -e "$SUITE_A"
test ! -e "$SUITE_B"
pgrep -af 'agentdeck.*daemon|claude-agent-acp|agentdeck-m2c' || true
tmux list-sessions 2>/dev/null | rg 'agentdeck-m2c' || true
```

Expected: no suite checkout, M2c process, daemon, ACP adapter, or tmux residue.

Observed: both paths were removed and pruned; process, daemon, ACP, tmux,
worktree, and repository-status audits were empty.

- [x] **Step 4: Reconstruct exact installed inputs read-only**

Use the existing explicit-authority loader without PATH search. The values below
are the last accepted explicit identities and must be checked as-is; if one no
longer exists, stop rather than substitute another executable:

```bash
export AGENTDECK_M2C_LEADER_MODEL='gpt-5.5'
export AGENTDECK_M2C_CODEX='/Users/liuyue/.local/bin/codex'
export AGENTDECK_M2C_CLAUDE='/Users/liuyue/.local/share/claude/versions/2.1.211'
export AGENTDECK_M2C_CLAUDE_ACP='/Users/liuyue/.local/lib/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js'
export AGENTDECK_M2C_CLAUDE_ACP_PACKAGE='/Users/liuyue/.local/lib/node_modules/@agentclientprotocol/claude-agent-acp'
export AGENTDECK_M2C_NODE='/Users/liuyue/.hermes/node/bin/node'
export AGENTDECK_M2C_TMUX='/opt/homebrew/Cellar/tmux/3.6a/bin/tmux'
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck python - <<'PY'
import json
import os
import runpy
import subprocess

ns = runpy.run_path("tests/test_m2c_live_acceptance.py")
authority, failures = ns["_load_explicit_tool_authority"](os.environ)
failure_cards = [
    {"tool": item.tool, "probe": item.probe, "code": item.code}
    for item in failures
]
auth_ready = False
if authority is not None and not failure_cards:
    completed = subprocess.run(
        [str(authority.claude.path), "auth", "status", "--json"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
        env=dict(os.environ),
    )
    if completed.returncode == 0:
        try:
            auth_ready = json.loads(completed.stdout).get("loggedIn") is True
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            auth_ready = False
print(json.dumps({
    "authority_schema": ns["AUTHORITY_SCHEMA_VERSION"],
    "strict_preflight_schema": ns["STRICT_PREFLIGHT_SCHEMA_VERSION"],
    "leader_model": os.environ["AGENTDECK_M2C_LEADER_MODEL"],
    "authority_digest": authority.digest if authority is not None else None,
    "loader_failures": failure_cards,
    "claude_auth_ready": auth_ready,
}, sort_keys=True))
PY
```

The command prints no paths, executable hashes, auth payload, prompt, stderr,
or credential. Record only:

```text
authority_schema=m2c-tool-authority/v3
strict_preflight_schema=m2c-live-preflight/v6
leader_model=gpt-5.5
authority_digest=the `authority_digest` printed by the audit command above
loader_failures=[]
claude_auth_ready=true
```

If the digest or any installed identity differs from the last accepted audit,
stop and report the new exact closed blocker. Do not substitute another PATH
candidate.

Observed: exact identities were unchanged. The audit returned
`m2c-tool-authority/v3`, `m2c-live-preflight/v6`, Leader `gpt-5.5`, digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`,
`loader_failures=[]`, and `claude_auth_ready=true`.

- [x] **Step 5: Commit verification evidence before requesting authority**

Record both suite results, cleanup, input audit, exact frozen SHA, model, and
digest. Then:

```bash
git add HISTORY.md docs/handoff/current-development-state.md \
  docs/validation/2026-07-17-phase3-m2c-four-stage-live.md \
  docs/superpowers/plans/2026-07-17-m2c-bounded-sequential-permission-acceptance.md
git commit -m "docs: verify bounded M2c permission acceptance"
git status --short
```

Expected: clean worktree. Do not execute designated preflight yet.

### Task 12: Execute the two separately authorized real gates

**Files:**

- Modify after each gate: `HISTORY.md`, `docs/handoff/current-development-state.md`,
  `docs/validation/2026-07-17-phase3-m2c-four-stage-live.md`, and this plan

- [x] **Step 1: Stop and request the unique read-only preflight authority**

Ask the human to name the exact frozen implementation SHA, Leader model
`gpt-5.5`, and reconstructed authority digest. Do not infer authorization from
spec approval, plan approval, test approval, or old live authority.

Observed: the human explicitly authorized one read-only strict v6 preflight on
frozen `df25532d0bd4fb9c8dd57fd119607a05411d11db`, Leader `gpt-5.5`, and
digest `sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`.

- [x] **Step 2: Run designated strict v6 preflight exactly once**

Only after the exact authorization, export the already-audited explicit tool
paths/package root/Node/model/digest. Recover the Task 10 frozen commit by its
unique commit subject, create a fresh detached checkout at that exact commit,
and run only the designated preflight test:

```bash
export AGENTDECK_M2C_CODEX='/Users/liuyue/.local/bin/codex'
export AGENTDECK_M2C_CLAUDE='/Users/liuyue/.local/share/claude/versions/2.1.211'
export AGENTDECK_M2C_CLAUDE_ACP='/Users/liuyue/.local/lib/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js'
export AGENTDECK_M2C_CLAUDE_ACP_PACKAGE='/Users/liuyue/.local/lib/node_modules/@agentclientprotocol/claude-agent-acp'
export AGENTDECK_M2C_NODE='/Users/liuyue/.hermes/node/bin/node'
export AGENTDECK_M2C_TMUX='/opt/homebrew/Cellar/tmux/3.6a/bin/tmux'
DEVELOPMENT_WORKTREE="$PWD"
FROZEN_SHA="$(git log -1 --format=%H --grep='^docs: freeze bounded M2c permission acceptance$')"
test -n "$FROZEN_SHA"
PREFLIGHT_WORKTREE="$(mktemp -d /tmp/agentdeck-m2c-preflight.XXXXXX)"
rmdir "$PREFLIGHT_WORKTREE"
git worktree add --detach "$PREFLIGHT_WORKTREE" "$FROZEN_SHA"
cd "$PREFLIGHT_WORKTREE"
set +e
AGENTDECK_M2C_STRICT_PREFLIGHT=1 \
AGENTDECK_M2C_LEADER_MODEL="gpt-5.5" \
AGENTDECK_M2C_AUTHORITY_DIGEST="$AUTHORITY_DIGEST" \
PYTHONPATH="$PREFLIGHT_WORKTREE/src" \
conda run --no-capture-output -n agentdeck \
pytest tests/test_m2c_live_acceptance.py::test_m2c_explicit_authority_preflight_is_read_only \
  -q -s
PREFLIGHT_RC=$?
set -e
cd "$DEVELOPMENT_WORKTREE"
git worktree remove "$PREFLIGHT_WORKTREE"
git worktree prune
test ! -e "$PREFLIGHT_WORKTREE"
test "$PREFLIGHT_RC" -eq 0
```

The exact explicit `AGENTDECK_M2C_CODEX`, `AGENTDECK_M2C_CLAUDE`,
`AGENTDECK_M2C_CLAUDE_ACP`, `AGENTDECK_M2C_CLAUDE_ACP_PACKAGE`,
`AGENTDECK_M2C_NODE`, and `AGENTDECK_M2C_TMUX` values come only from Task 11's
sealed audit and must also be exported. They are not rediscovered or replaced
inside this command.

Expected: one test PASS with `ready=true`, `blockers=[]`, and `failures=[]`.
Any other result consumes this preflight authority and blocks live.

Observed: the designated node ran exactly once and passed `1 passed in
17.39s`; its closed response was `ready=true`, `blockers=[]`, `failures=[]`,
schema `m2c-live-preflight/v6`, ready authority `m2c-tool-authority/v3`, exact
Leader `gpt-5.5`, and the authorized digest. The checkout and all audited
residue were removed. This preflight authority is consumed.

- [x] **Step 3: Record preflight and request separate live authority**

Commit the exact closed preflight result and cleanup audit. Then stop and ask
for a new sentence authorizing one real four-stage Mission on the same frozen
implementation SHA, `gpt-5.5`, and exact authority digest. Preflight approval is
not live approval.

- [ ] **Step 4: Execute the real four-stage Mission exactly once**

Only after separate exact live authority:

```bash
export AGENTDECK_M2C_CODEX='/Users/liuyue/.local/bin/codex'
export AGENTDECK_M2C_CLAUDE='/Users/liuyue/.local/share/claude/versions/2.1.211'
export AGENTDECK_M2C_CLAUDE_ACP='/Users/liuyue/.local/lib/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js'
export AGENTDECK_M2C_CLAUDE_ACP_PACKAGE='/Users/liuyue/.local/lib/node_modules/@agentclientprotocol/claude-agent-acp'
export AGENTDECK_M2C_NODE='/Users/liuyue/.hermes/node/bin/node'
export AGENTDECK_M2C_TMUX='/opt/homebrew/Cellar/tmux/3.6a/bin/tmux'
DEVELOPMENT_WORKTREE="$PWD"
FROZEN_SHA="$(git log -1 --format=%H --grep='^docs: freeze bounded M2c permission acceptance$')"
test -n "$FROZEN_SHA"
LIVE_WORKTREE="$(mktemp -d /tmp/agentdeck-m2c-live-gate.XXXXXX)"
rmdir "$LIVE_WORKTREE"
git worktree add --detach "$LIVE_WORKTREE" "$FROZEN_SHA"
cd "$LIVE_WORKTREE"
set +e
AGENTDECK_M2C_LIVE=1 \
AGENTDECK_M2C_LEADER_MODEL="gpt-5.5" \
AGENTDECK_M2C_AUTHORITY_DIGEST="$AUTHORITY_DIGEST" \
PYTHONPATH="$LIVE_WORKTREE/src" \
conda run --no-capture-output -n agentdeck \
pytest tests/test_m2c_live_acceptance.py::test_real_four_stage_m2c_acceptance \
  -q
LIVE_RC=$?
set -e
cd "$DEVELOPMENT_WORKTREE"
git worktree remove "$LIVE_WORKTREE"
git worktree prune
test ! -e "$LIVE_WORKTREE"
test "$LIVE_RC" -eq 0
```

Export the same six sealed explicit tool variables used by preflight. Run no
second attempt regardless of result.

- [ ] **Step 5: Close M2c only on real PASS**

PASS requires four succeeded attempts, four validated replies, four canonical
handoffs, three handoff-before-submit links, both Claude phases with 1-4
effective approved permissions, total 2-8 permissions, successful
takeover/return-control, exact artifact bytes, valid trace/ProjectView/workbench
facts, and zero cleanup residue.

On PASS:

1. update the roadmap/validation/handoff/HISTORY to mark M2c complete;
2. commit the closure locally without merge or push;
3. mark the active M2c goal complete only after no required work remains;
4. begin M3 with a new brainstorming → spec → writing-plans cycle.

On failure:

1. record the one-shot closed result and consumed authority;
2. keep M2c blocked and M3 locked;
3. do not retry, inflate timeout, rewrite evidence, or expand scope;
4. start only the smallest evidence-led brainstorming cycle for the new exact
   terminal fact.

## Plan self-review checklist

Coverage map:

- spec sections 1-4 map to Tasks 1-4 and the harness-only scope audit in Task
  8;
- section 5 maps to the pure projection RED/GREEN in Tasks 1-2;
- section 6 maps to the bounded state-machine RED/GREEN in Tasks 3-4;
- section 7 maps to takeover-aware live integration in Task 8;
- section 8 maps to shared completion RED/GREEN in Tasks 7-8;
- section 9 maps to exact confirmation and diagnostics in Tasks 5-6;
- sections 10-11 map to Tasks 1-10;
- sections 12-13 map to authority/audit gates in Tasks 11-12;
- the north-star and non-goal constraints are rechecked in Tasks 9-12.

- [x] Every design requirement maps to a named test or implementation step.
- [x] The current real failure is reproduced by a two-permission single-attempt
  RED, not by a synthetic timeout.
- [x] Effective permission states come only from immutable bases plus
  append-only transitions.
- [x] Confirmation binds exact Mission, attempt, permission, preview, and
  confirmation identities.
- [x] Implementation and revision each require at least one permission; 4/8
  are acceptance-only maxima.
- [x] Reply plus canonical handoff gates every successor attempt.
- [x] Takeover blocks confirmation until return-control and authority
  revalidation.
- [x] Diagnostic keys and values are finite and exclude raw content.
- [x] No step edits `src/agentdeck/**`, changes timeout, retries live, installs a
  tool, modifies global settings, merges, pushes, or enters M3 early.
- [x] Two full suites are serial and tied to one unchanged implementation SHA.
- [x] Preflight and live each have a separate exact human-authorization stop.
