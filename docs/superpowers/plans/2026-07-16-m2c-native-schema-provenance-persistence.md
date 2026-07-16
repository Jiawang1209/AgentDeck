# M2c Native-Schema Provenance Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the exact already-validated `leader_generation` envelope on semantic Mission preview plans so the real M2c native-schema provenance gate can advance without changing plan authority or runtime behavior.

**Architecture:** Keep `validate_leader_generation_provenance()` as the sole Provider-envelope admission gate and `StateStore._plan_leader_generation()` as the sole durable normalizer. Remove the stale semantic discard, then make StateStore strictly distinguish the existing ordinary nine-field shape from the already-defined semantic eleven-field shape and revalidate the latter against the semantic plan authority; do not reconstruct provenance or alter plan hashing, confirmation, Provider, daemon, ACP/tmux, permission, or live behavior.

**Tech Stack:** Python 3.12, dataclasses, pytest, AgentDeck semantic Mission orchestration, ConversationSession, StateStore, conda environment `agentdeck`.

---

## Authority and stop rules

- Approved spec:
  `docs/superpowers/specs/2026-07-16-m2c-native-schema-provenance-persistence-design.md`
- Failure authority:
  `75f0366d4d5619b29c77f10949365f43d46185b1`
- Failure evidence commit: `e2a0f980`
- Old preflight count: `1`
- Old live count: `1`
- Never rerun either old authority.
- Do not set `AGENTDECK_M2C_LIVE=1`.
- Do not run a Provider, ACP adapter, tmux Worker, daemon, login, install, or
  global configuration command while implementing this plan.
- Any production change beyond preserving the validated envelope is a STOP and
  requires human review.

## File map

- Modify `tests/test_mission_orchestration.py`
  - native + semantic direct-preview RED;
  - ProjectView equality and plan-hash stability;
  - semantic malformed-generation zero-write regression.
- Modify `tests/test_conversation_session.py`
  - natural-language semantic preview persists the exact gateway envelope.
- Modify `src/agentdeck/mission_orchestration.py`
  - remove the stale semantic `leader_generation = None` discard only.
- Modify `src/agentdeck/state.py`
  - strict ordinary-nine / semantic-eleven field selection;
  - semantic authority version/hash and semantic schema-version validation.
- Modify `HISTORY.md`
  - RED/GREEN and verification evidence.
- Modify `docs/handoff/current-development-state.md`
  - active gate and frozen verification facts.
- Modify `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
  - deterministic closure evidence; no M2c PASS claim.
- Modify this plan
  - checkbox and exact command evidence.

## Task 1: Prove direct semantic native provenance is discarded

**Files:**
- Modify: `tests/test_mission_orchestration.py`

- [x] **Step 1: Add a semantic native-generation helper**

Place beside `semantic_candidate_fixture()`:

```python
def semantic_native_generation(
    config, candidate: LeaderMissionCandidate
) -> dict[str, object]:
    authority = deepcopy(candidate.semantic_authority)
    authority["proposed_effects"] = []
    request = LeaderPlanRequest(
        task=candidate.user_message,
        config=config,
        model=config.leader.model,
        selected_agent_ids=candidate.selected_agent_ids,
        step_count=candidate.step_count,
        timeout_seconds=candidate.timeout_seconds,
        semantic_authority=authority,
    )
    return build_leader_generation_provenance(
        request=request,
        provider="fake",
        constraint_mode="native_json_schema",
        schema=build_leader_plan_schema(request),
        attempt_count=2,
    )
```

Reuse existing `deepcopy`, `LeaderPlanRequest`,
`build_leader_generation_provenance`, and `build_leader_plan_schema` imports.

- [x] **Step 2: Add the exact RED**

Add after
`test_semantic_preview_persists_exact_authority_steps_and_compact_mission`:

```python
def test_semantic_native_preview_persists_exact_generation_without_hash_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, config, store, _path = project(tmp_path)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which",
        lambda command: f"/bin/{command}",
    )
    candidate = semantic_candidate_fixture(config)
    generation = semantic_native_generation(config, candidate)
    candidate = replace(candidate, leader_generation=generation)

    preview = create_mission_preview_from_candidate(
        config=config,
        store=store,
        candidate=candidate,
    )

    stored = store.plan_by_id(preview["plan_id"])
    projected = store.project_view(config).plans["items"][-1]
    assert stored["leader_generation"] == generation
    assert stored["leader_generation"] is not generation
    assert projected["leader_generation"] == generation

    state = store.load()
    state["approvals"] = [
        {"message_id": "msg_semantic_trace", "plan_id": stored["plan_id"]}
    ]
    traced = StateStore._trace_plan_for_message(
        state, "msg_semantic_trace"
    )
    assert traced is not None
    assert traced["leader_generation"] == generation

    without_generation = deepcopy(stored)
    without_generation.pop("leader_generation")
    assert canonical_workflow_plan_hash(stored) == canonical_workflow_plan_hash(
        without_generation
    )
```

Ensure `replace` is imported from `dataclasses`; it is already available in
this module or add the exact import.

- [x] **Step 3: Run RED and verify the intended failure**

```bash
PYTHONPATH="$PWD/src" \
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_mission_orchestration.py::test_semantic_native_preview_persists_exact_generation_without_hash_drift \
  -q
```

Expected: FAIL because the stored semantic plan has no
`leader_generation`. A schema, fixture, or authority-validation error is the
wrong RED and must be fixed before continuing.

## Task 2: Prove natural-language semantic preview loses the gateway envelope

**Files:**
- Modify: `tests/test_conversation_session.py`

- [x] **Step 1: Add exact gateway-observation RED**

Add after `test_exact_live_shaped_request_reaches_one_semantic_preview`:

```python
def test_exact_live_shaped_preview_persists_exact_gateway_generation(
    tmp_path: Path,
) -> None:
    _config, store = _project(tmp_path)
    inner = LeaderGateway(provider_factory=lambda _name: FakeLeaderProvider())
    observed: dict[str, object] = {}

    class RecordingGateway:
        def generate_mission(self, request, cancel):
            candidate = inner.generate_mission(request, cancel)
            observed["leader_generation"] = deepcopy(
                candidate.leader_generation
            )
            return candidate

    response = ConversationSession(
        root=tmp_path,
        leader_gateway=RecordingGateway(),
    ).handle(SEMANTIC_MESSAGE)

    assert response.kind == "mission_preview"
    stored = store.load()["plans"][0]
    assert stored["leader_generation"] == observed["leader_generation"]
    assert stored["leader_generation"] is not observed["leader_generation"]
```

- [x] **Step 2: Run both RED nodes**

```bash
PYTHONPATH="$PWD/src" \
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_mission_orchestration.py::test_semantic_native_preview_persists_exact_generation_without_hash_drift \
  tests/test_conversation_session.py::test_exact_live_shaped_preview_persists_exact_gateway_generation \
  -q
```

Expected: both FAIL at missing stored `leader_generation`.

## Task 3: Preserve fail-closed semantic-generation behavior

**Files:**
- Modify: `tests/test_mission_orchestration.py`

- [x] **Step 1: Add semantic malformed-generation regression before production code**

```python
def test_semantic_native_generation_with_forbidden_key_is_zero_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, config, store, _path = project(tmp_path)
    monkeypatch.setattr(
        "agentdeck.mission_orchestration.shutil.which",
        lambda command: f"/bin/{command}",
    )
    candidate = semantic_candidate_fixture(config)
    generation = semantic_native_generation(config, candidate)
    generation["raw_prompt"] = "SEMANTIC_GENERATION_SECRET"

    with pytest.raises(
        MissionPreviewError, match="^mission preview generation invalid$"
    ) as raised:
        create_mission_preview_from_candidate(
            config=config,
            store=store,
            candidate=replace(candidate, leader_generation=generation),
        )

    assert store.load()["plans"] == []
    assert store.load().get("missions", []) == []
    assert "SEMANTIC_GENERATION_SECRET" not in str(raised.value)
    assert "SEMANTIC_GENERATION_SECRET" not in repr(raised.value)
```

- [x] **Step 2: Run the security regression with the RED nodes**

```bash
PYTHONPATH="$PWD/src" \
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_mission_orchestration.py::test_semantic_native_preview_persists_exact_generation_without_hash_drift \
  tests/test_mission_orchestration.py::test_semantic_native_generation_with_forbidden_key_is_zero_write \
  tests/test_conversation_session.py::test_exact_live_shaped_preview_persists_exact_gateway_generation \
  -q
```

Expected: the security node PASSes; the two persistence nodes remain RED.

- [x] **Step 3: Add StateStore dual-shape RED coverage**

Add focused tests in `tests/test_mission_orchestration.py`:

```python
@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: {
            key: item
            for key, item in value.items()
            if key != "semantic_authority_schema_version"
        },
        lambda value: {
            **value,
            "semantic_authority_schema_version": "mission-semantic-authority/v0",
        },
        lambda value: {
            **value,
            "semantic_authority_hash": "sha256:" + "f" * 64,
        },
    ],
)
def test_semantic_plan_generation_shape_or_authority_drift_is_zero_write(
    tmp_path: Path,
    mutation,
) -> None:
    _root, config, store, _path = project(tmp_path)
    candidate = semantic_candidate_fixture(config)
    generation = mutation(semantic_native_generation(config, candidate))

    with pytest.raises(ValueError, match="^plan leader generation invalid$"):
        store.build_plan_record(
            candidate.user_message,
            candidate.provider,
            candidate.model,
            candidate.plan,
            leader_generation=generation,
        )

    assert store.load()["plans"] == []
```

Extend the existing ordinary-plan invalid-generation mutation table with one
eleven-field semantic envelope mutation and retain its zero-write assertion.
The existing natural-language semantic RED supplies valid local semantic
eleven-field coverage with null schema fields.

- [x] **Step 4: Run the expanded StateStore RED**

```bash
PYTHONPATH="$PWD/src" \
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_mission_orchestration.py \
  -k 'semantic_native or semantic_plan_generation_shape_or_authority_drift or candidate_generation' \
  -q
```

Expected before the StateStore correction: valid semantic provenance fails
because only the ordinary nine-field shape is accepted; drift/forbidden cases
remain rejected.

## Task 4: Make the minimal production correction

**Files:**
- Modify: `src/agentdeck/mission_orchestration.py`
- Modify: `src/agentdeck/state.py`

- [x] **Step 1: Remove only the stale discard branch**

Delete:

```python
        if validated_candidate_authority is not None:
            # Task 7 validates semantic generation transiently. Task 8 owns its
            # durable plan/Mission representation and confirmation binding.
            leader_generation = None
```

Do not change the Provider validator, request, semantic authority, plan body,
hash function, confirmation facts, or any runtime code. StateStore changes are
limited to Step 2's strict dual-shape normalizer.

- [x] **Step 2: Add the strict StateStore dual-shape normalizer**

In `src/agentdeck/state.py`:

1. split the existing field tuple into the ordinary base fields and the two
   semantic fields;
2. detect semantic shape from the already validated plan body;
3. require exact nine or eleven keys accordingly;
4. for semantic plans, require both authority fields to match
   `plan["semantic_authority"]["schema_version"]` and
   `semantic_authority_hash(plan["semantic_authority"])`;
5. for native mode, require `leader-plan/v1` on ordinary plans and
   `SEMANTIC_LEADER_PLAN_SCHEMA_VERSION` on semantic plans;
6. return a deep copy in the selected deterministic field order.

Import `SEMANTIC_LEADER_PLAN_SCHEMA_VERSION` from
`agentdeck.providers.semantic_plan_schema`. Preserve the legacy missing-
generation projection unchanged for historical records.

- [x] **Step 3: Run GREEN for the focused nodes**

```bash
PYTHONPATH="$PWD/src" \
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_mission_orchestration.py::test_semantic_native_preview_persists_exact_generation_without_hash_drift \
  tests/test_mission_orchestration.py::test_semantic_native_generation_with_forbidden_key_is_zero_write \
  tests/test_conversation_session.py::test_exact_live_shaped_preview_persists_exact_gateway_generation \
  -q
```

Expected: all focused persistence, dual-shape, and security nodes PASS.

- [x] **Step 4: Run semantic Mission and Conversation regression**

```bash
PYTHONPATH="$PWD/src" \
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_mission_orchestration.py \
  tests/test_conversation_session.py \
  tests/test_conversation_bindings.py \
  tests/test_conversation_acceptance.py \
  -q
```

Expected: all PASS.

- [x] **Step 5: Run Provider/provenance and complete non-live M2c regression**

```bash
PYTHONPATH="$PWD/src" \
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_cli_structured_output.py \
  tests/test_leader_plan_schema.py \
  tests/test_conversation_leader_gateway.py \
  tests/test_m2c_live_acceptance.py \
  -q
```

Expected: all deterministic tests PASS with exactly the existing opt-in live
skip. Do not set `AGENTDECK_M2C_LIVE`.

- [x] **Step 6: Update implementation history**

Add one `HISTORY.md` entry containing:

- live blocker and root cause;
- exact RED failure;
- one-line production correction;
- focused and non-live GREEN counts;
- explicit statement that no preflight/live/provider/ACP/tmux ran.

- [x] **Step 7: Commit the implementation**

```bash
git add \
  src/agentdeck/mission_orchestration.py \
  src/agentdeck/state.py \
  tests/test_mission_orchestration.py \
  tests/test_conversation_session.py \
  HISTORY.md
git diff --cached --check
git commit -m "fix: persist semantic leader generation provenance"
```

## Task 5: Independent review and focused verification

**Files:**
- Inspect all files changed since `17e5f5c3`
- Modify only for a verified defect; any correction requires a new RED and a
  new commit.

- [x] **Step 1: Request spec-compliance review**

Reviewer checks:

- exact validated envelope is persisted;
- no reconstruction or duplicate authority;
- hash and confirmation facts unchanged;
- malformed semantic generation remains zero-write;
- no Provider/runtime/live scope.

- [x] **Step 2: Request code-quality review**

Reviewer checks:

- one minimal production deletion;
- tests cross semantic + native and natural-language paths;
- no fixture-only assertion;
- no secret marker enters production/docs evidence.

- [x] **Step 3: Run compile, diff, and scope audit**

```bash
PYTHONPATH="$PWD/src" \
conda run --no-capture-output -n agentdeck \
  python -m compileall -q src tests
git diff --check 17e5f5c3..HEAD
git diff --name-only 17e5f5c3..HEAD
git diff 17e5f5c3..HEAD -- src/agentdeck |
  rg -n '^[+-]' || true
```

Required production diff: deletion of the stale semantic
`leader_generation = None` block plus the bounded StateStore dual-shape
normalizer. No other production subsystem may change.

- [x] **Step 4: Audit synthetic marker boundary**

```bash
rg -n 'SEMANTIC_GENERATION_SECRET' \
  src/agentdeck docs/validation docs/handoff || true
```

Expected: no output.

## Task 6: Freeze and run two full suites

**Files:**
- No code modification during verification.

- [x] **Step 1: Freeze exact implementation SHA**

```bash
test -z "$(git status --short)"
implementation_sha="$(git rev-parse HEAD)"
printf '%s\n' "$implementation_sha"
```

- [x] **Step 2: Create detached verification checkout**

```bash
feature_root="$(git rev-parse --show-toplevel)"
implementation_sha="$(git rev-parse HEAD)"
short_sha="$(git rev-parse --short=12 "$implementation_sha")"
verify_root="/tmp/agentdeck-provenance-verify-$short_sha"
test ! -e "$verify_root"
git worktree add --detach "$verify_root" "$implementation_sha"
test -z "$(git -C "$verify_root" status --short)"
```

- [x] **Step 3: Run full suite 1**

```bash
(
  cd "$verify_root"
  PYTHONPATH="$verify_root/src" \
  conda run --no-capture-output -n agentdeck \
    python -m pytest -q
)
```

Record exact counts and duration.

- [x] **Step 4: Reconfirm unchanged SHA and run full suite 2**

```bash
test "$(git -C "$verify_root" rev-parse HEAD)" = "$implementation_sha"
test -z "$(git -C "$verify_root" status --short)"
(
  cd "$verify_root"
  PYTHONPATH="$verify_root/src" \
  conda run --no-capture-output -n agentdeck \
    python -m pytest -q
)
```

Record exact counts and duration.

- [x] **Step 5: Remove checkout and audit residuals**

```bash
git worktree remove --force "$verify_root"
git worktree prune
test ! -e "$verify_root"
ps -axo pid=,command= |
  rg 'pytest.*test_real_four_stage_m2c_acceptance|agentdeck.*daemon' |
  rg -v 'rg ' || true
find /tmp /private/tmp -maxdepth 1 -type d \
  \( -name 'agentdeck-m2c-live-*' -o -name 'agentdeck-m2c-tools.*' \) \
  -print 2>/dev/null
```

Do not inspect or delete unrelated user tmux sessions or the pre-existing
`agentdeck-m2c-path-verify-954b868c` directory.

## Task 7: Record frozen evidence and stop

**Files:**
- Modify: `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`
- Modify: this plan

- [x] **Step 1: Record evidence**

Record:

- exact implementation SHA;
- RED/GREEN counts;
- focused/non-live M2c counts;
- both full-suite counts/durations;
- review results;
- compile/diff/scope/leakage audit;
- old SHA preflight/live counts remain `1/1`;
- new SHA preflight/live counts are `0/0`;
- M2c remains BLOCKED and M3 locked;
- next gate is new explicit exact-model binding plus one read-only preflight
  authorization.

Evidence:

- implementation SHA:
  `7a76ada81938be3ba0720a7c2f5a540b4beebb3e`;
- original RED: `2 failed, 1 passed`;
- StateStore A2 RED: `1 failed, 22 passed, 100 deselected`;
- ProjectView/trace contract RED: `2 failed, 7 passed`;
- discovery RED: `6 failed, 1 passed`;
- fresh Mission/Conversation GREEN: `211 passed in 5.77s`;
- fresh Provider/contracts/non-live M2c GREEN:
  `1125 passed, 1 skipped in 56.32s`;
- independent spec review: compliant;
- independent quality review: Ready, no findings;
- full suite 1: `4283 passed, 2 skipped in 194.36s`;
- full suite 2: `4283 passed, 2 skipped in 203.12s`;
- compile/diff/scope/marker/cleanup/residual checks: PASS;
- old `75f0366d...` preflight/live counts: `1/1`, never rerun;
- new `7a76ada...` preflight/live counts: `0/0`;
- M2c: **BLOCKED**; M3: locked.

- [x] **Step 2: Commit evidence separately**

```bash
git add \
  docs/validation/2026-07-13-phase3-m2-project-daemon.md \
  docs/handoff/current-development-state.md \
  HISTORY.md \
  docs/superpowers/specs/2026-07-16-m2c-native-schema-provenance-persistence-design.md \
  docs/superpowers/plans/2026-07-16-m2c-native-schema-provenance-persistence.md
git diff --cached --check
git commit -m "docs: record M2c provenance persistence verification"
```

The evidence commit is not implementation authority.
Recorded as `40e900b3` after the frozen implementation and verification
evidence were complete.

- [x] **Step 3: Stop at new authorization gate**

Do not run preflight or live. Ask the human to bind an exact model to the new
implementation SHA and authorize exactly one read-only preflight.

## Completion checklist

- [x] Direct semantic native preview persists exact validated provenance.
- [x] Natural-language semantic preview persists exact gateway provenance.
- [x] ProjectView and trace use the same compact envelope.
- [x] StateStore enforces ordinary nine-field and semantic eleven-field shapes.
- [x] Semantic authority version/hash and semantic schema family are revalidated.
- [x] Canonical plan hash is unchanged.
- [x] Malformed/forbidden semantic provenance is zero-write and transcript-safe.
- [x] Production diff only removes the stale discard and adds the bounded
  StateStore dual-shape normalizer.
- [x] Focused and complete non-live M2c suites pass.
- [x] Independent spec and quality reviews pass.
- [x] Two full suites pass on one unchanged new implementation SHA.
- [x] New SHA has zero preflight/live attempts.
- [x] M2c remains BLOCKED and M3 locked until real four-stage PASS.
