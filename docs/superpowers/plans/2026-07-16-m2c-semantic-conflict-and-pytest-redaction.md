# M2c Semantic Conflict and Pytest Redaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make required semantic targets exclusive from Leader proposals, provide one precise same-Leader regeneration for each target-conflict class, and prove that default pytest failure reports cannot expose bounded PTY tail bytes.

**Architecture:** Keep native JSON Schema responsible for structure and keep AgentDeck's semantic validator authoritative for cross-object meaning. Add two closed target-conflict codes plus one shared static guidance helper consumed identically by CLI and API providers; do not repair Candidate JSON locally. Preserve the bounded `_PtyTail` in memory while excluding its raw bytes from dataclass representation, then test the real default pytest report in a nested fake-only subprocess.

**Tech Stack:** Python 3.12, stdlib dataclasses/subprocess/tempfile/json, pytest, AgentDeck semantic planning and Provider contracts, conda environment `agentdeck`.

---

## 0. Authority, scope, and file map

Execute from:

```text
/Users/liuyue/.config/superpowers/worktrees/multi-agent-explore/codex/m2c-leader-preview-observability
```

Approved specification:

```text
docs/superpowers/specs/2026-07-16-m2c-semantic-conflict-and-pytest-redaction-design.md
```

Starting documentation commit:

```text
2f28033458a092c81a66833250a58d1d059cd376
```

The old frozen implementation
`9db5b476f885cfcf68a55cbf59673a2d908d3fce`, its preflight, and its live
authorization are historical evidence only. Do not rerun either node.

### Expected file responsibilities

- `src/agentdeck/semantic_planning.py`
  - authoritative semantic failure domains;
  - required/proposed target ownership validation;
  - shared static regeneration guidance.
- `src/agentdeck/providers/cli_subprocess.py`
  - CLI Leader prompt consumption of shared target rules/guidance;
  - no independent conflict policy.
- `src/agentdeck/providers/openai_compatible.py`
  - API Leader prompt consumption of the same rules/guidance;
  - no independent conflict policy.
- `src/agentdeck/providers/plan_schema.py`
  - already derives the public diagnostic allowlist from
    `SEMANTIC_FAILURE_CODES`; verify rather than duplicate new strings.
- `tests/test_semantic_planning.py`
  - pure target ownership, code domain, persisted-step compatibility, and
    guidance tests.
- `tests/test_leader_cli.py`
  - API and native CLI same-Leader regeneration, parity, deadline, provenance,
    and leakage-negative tests.
- `tests/test_leader_plan_schema.py`
  - schema/hash stability and diagnostic-domain compatibility.
- `tests/test_m2c_live_acceptance.py`
  - closed terminal projection for the new codes;
  - `_PtyTail` safe representation;
  - nested default-pytest report regression.
- `docs/superpowers/specs/2026-07-15-leader-semantic-authority-design.md`
  - synchronize the durable required/proposed target rule and closed codes.
- `docs/validation/phase3-m2c-live-acceptance-sop.md`
  - state that default pytest traceback is part of the transcript-free
    boundary.
- `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
  - later record deterministic/frozen evidence only; never claim live PASS.
- `docs/handoff/current-development-state.md`
  - keep the exact active gate and frozen SHA.
- `HISTORY.md`
  - accompany every implementation/evidence commit.

Do not modify `SemanticAuthorityExtractor`, `WorkerTaskCompiler`, Mission
confirmation, daemon, ACP, tmux, permission, handoff, model, timeout, MCP,
login, or global configuration behavior.

## Task 1: Specify and implement semantic target ownership

**Files:**
- Modify: `tests/test_semantic_planning.py`
- Modify: `src/agentdeck/semantic_planning.py`

- [ ] **Step 1: Extend the closed-domain RED assertion**

Update `test_failure_codes_are_the_fixed_closed_domain()` so the exact expected
set retains `semantic_effect_conflict` and adds:

```python
"semantic_required_target_reproposed",
"semantic_proposal_target_duplicate",
```

Add the same two values to the expected regenerable set assertion near the top
of `tests/test_semantic_planning.py`.

- [ ] **Step 2: Add required-target exclusivity RED tests**

Add:

```python
@pytest.mark.parametrize("operation", ["create", "review", "update", "verify"])
def test_candidate_rejects_required_target_reproposal_independent_of_operation(
    operation: str,
) -> None:
    value = candidate()
    value["steps"][0]["proposed_effects"] = [
        {
            "target": "artifact.txt",
            "operation": operation,
            "sensitivity": "ordinary",
        }
    ]

    with pytest.raises(SemanticPlanningError) as raised:
        _compile(candidate_value=value)

    _assert_closed(raised.value, "semantic_required_target_reproposed")
```

This proves ownership is based on the target, not whether the proposed
operation happens to match one required operation.

- [ ] **Step 3: Add Mission-wide duplicate-proposal RED tests**

Add:

```python
def test_candidate_rejects_duplicate_new_target_across_steps() -> None:
    value = candidate()
    proposal = {
        "target": "notes.md",
        "operation": "create",
        "sensitivity": "ordinary",
    }
    value["steps"][0]["proposed_effects"] = [deepcopy(proposal)]
    value["steps"][1]["proposed_effects"] = [deepcopy(proposal)]

    with pytest.raises(SemanticPlanningError) as raised:
        _compile(candidate_value=value)

    _assert_closed(raised.value, "semantic_proposal_target_duplicate")


def test_required_target_diagnostic_precedes_duplicate_proposal_diagnostic() -> None:
    value = candidate()
    proposal = {
        "target": "artifact.txt",
        "operation": "create",
        "sensitivity": "ordinary",
    }
    value["steps"][0]["proposed_effects"] = [deepcopy(proposal)]
    value["steps"][1]["proposed_effects"] = [deepcopy(proposal)]

    with pytest.raises(SemanticPlanningError) as raised:
        _compile(candidate_value=value)

    _assert_closed(raised.value, "semantic_required_target_reproposed")
```

- [ ] **Step 4: Add valid-new-target and multi-phase-required RED coverage**

Add:

```python
def test_candidate_accepts_distinct_genuinely_new_proposed_targets() -> None:
    value = candidate()
    value["steps"][0]["proposed_effects"] = [
        {"target": "notes.md", "operation": "create", "sensitivity": "ordinary"}
    ]
    value["steps"][1]["proposed_effects"] = [
        {"target": "review.md", "operation": "create", "sensitivity": "ordinary"}
    ]

    plan = _compile(candidate_value=value)

    assert [
        item["target"] for item in plan["semantic_authority"]["proposed_effects"]
    ] == ["notes.md", "review.md"]


def test_multi_phase_required_target_without_proposal_remains_valid() -> None:
    plan = _compile()

    assert {
        item["target"] for item in plan["semantic_authority"]["requirements"]
    } == {"artifact.txt"}
    assert plan["semantic_authority"]["proposed_effects"] == []
```

- [ ] **Step 5: Update persisted-step RED expectations**

Rename/update the existing tests around
`test_public_step_rejects_required_proposal_effect_conflict` and duplicate
proposal tests so they expect:

```text
semantic_required_target_reproposed
semantic_proposal_target_duplicate
```

Keep one explicit compatibility assertion that constructing
`SemanticPlanningError("semantic_effect_conflict")` remains valid and closed.

- [ ] **Step 6: Run the semantic ownership RED set**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_semantic_planning.py::test_failure_codes_are_the_fixed_closed_domain \
  tests/test_semantic_planning.py::test_candidate_rejects_required_target_reproposal_independent_of_operation \
  tests/test_semantic_planning.py::test_candidate_rejects_duplicate_new_target_across_steps \
  tests/test_semantic_planning.py::test_required_target_diagnostic_precedes_duplicate_proposal_diagnostic \
  tests/test_semantic_planning.py::test_candidate_accepts_distinct_genuinely_new_proposed_targets \
  tests/test_semantic_planning.py::test_multi_phase_required_target_without_proposal_remains_valid \
  -q
```

Expected: FAIL because the new codes are absent and current conflicts still
collapse to `semantic_effect_conflict`. The valid-new-target test may already
pass; that does not invalidate RED as long as the changed contract tests fail
for the intended reason.

- [ ] **Step 7: Implement the minimal closed domains**

In `src/agentdeck/semantic_planning.py`, retain the historical code and add:

```python
SEMANTIC_FAILURE_CODES = frozenset(
    {
        "semantic_authority_unresolved",
        "semantic_authority_sensitive_value",
        "semantic_candidate_missing_requirement",
        "semantic_candidate_duplicate_requirement",
        "semantic_candidate_wrong_phase",
        "semantic_candidate_wrong_worker",
        "semantic_transition_incomplete",
        "semantic_effect_conflict",
        "semantic_required_target_reproposed",
        "semantic_proposal_target_duplicate",
        "semantic_scope_addition_blocked",
        "semantic_candidate_schema_invalid",
        "semantic_compilation_failed",
        "semantic_compilation_drift",
        "semantic_confirmation_stale",
    }
)

SEMANTIC_REGENERABLE_FAILURE_CODES = frozenset(
    {
        "semantic_candidate_missing_requirement",
        "semantic_candidate_duplicate_requirement",
        "semantic_candidate_wrong_phase",
        "semantic_candidate_wrong_worker",
        "semantic_transition_incomplete",
        "semantic_effect_conflict",
        "semantic_required_target_reproposed",
        "semantic_proposal_target_duplicate",
    }
)
```

Do not remove or reinterpret the old code.

- [ ] **Step 8: Replace the umbrella helper with target ownership**

Replace `_validate_effect_conflicts()` with:

```python
def _validate_effect_target_ownership(
    required_effects: list[dict[str, Any]],
    proposed_effects: list[dict[str, str]],
) -> None:
    required_targets = {
        requirement["target"] for requirement in required_effects
    }
    proposed_targets: set[str] = set()
    for proposal in proposed_effects:
        target = proposal["target"]
        if target in required_targets:
            _fail("semantic_required_target_reproposed")
        if target in proposed_targets:
            _fail("semantic_proposal_target_duplicate")
        proposed_targets.add(target)
```

Update all three existing call sites. Candidate validation passes complete
Mission requirements plus all Candidate proposals, so it enforces Mission-wide
ownership/uniqueness. Standalone persisted-step validation enforces the same
rule over the step facts it is given; it must not perform filesystem reads or
invent missing Mission context.

- [ ] **Step 9: Run focused GREEN and the full semantic module**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  python -m pytest tests/test_semantic_planning.py -q
```

Expected: all tests PASS with no changed compiler/task/hash behavior outside
the updated closed codes.

Do not commit yet; Task 2 completes the approved semantic change as one
reviewable commit.

## Task 2: Add precise shared regeneration and Provider parity

**Files:**
- Modify: `tests/test_semantic_planning.py`
- Modify: `tests/test_leader_cli.py`
- Modify: `tests/test_leader_plan_schema.py`
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `src/agentdeck/semantic_planning.py`
- Modify: `src/agentdeck/providers/cli_subprocess.py`
- Modify: `src/agentdeck/providers/openai_compatible.py`
- Modify: `docs/superpowers/specs/2026-07-15-leader-semantic-authority-design.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Add shared-guidance RED tests**

Import `semantic_regeneration_guidance` in
`tests/test_semantic_planning.py` and add:

```python
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (
            "semantic_required_target_reproposed",
            (
                "Remove every proposed effect whose target is already represented "
                "by required authority. Keep authority_refs unchanged. Do not add "
                "a replacement proposal for that target.",
            ),
        ),
        (
            "semantic_proposal_target_duplicate",
            (
                "Each proposed target may appear only once. Return one complete "
                "candidate without repeated proposed targets.",
            ),
        ),
        ("semantic_candidate_missing_requirement", ()),
    ],
)
def test_semantic_regeneration_guidance_is_static_and_closed(
    code: str, expected: tuple[str, ...],
) -> None:
    assert semantic_regeneration_guidance(code) == expected


@pytest.mark.parametrize("code", [None, 1, "unknown"])
def test_semantic_regeneration_guidance_rejects_nonclosed_codes(code: object) -> None:
    with pytest.raises(ValueError, match="semantic regeneration diagnostic invalid"):
        semantic_regeneration_guidance(code)  # type: ignore[arg-type]
```

- [ ] **Step 2: Run the guidance RED**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_semantic_planning.py::test_semantic_regeneration_guidance_is_static_and_closed \
  tests/test_semantic_planning.py::test_semantic_regeneration_guidance_rejects_nonclosed_codes \
  -q
```

Expected: collection/import FAIL because the helper does not exist.

- [ ] **Step 3: Implement the shared static helper**

Add below the failure domains in `semantic_planning.py`:

```python
_SEMANTIC_REGENERATION_GUIDANCE = {
    "semantic_required_target_reproposed": (
        "Remove every proposed effect whose target is already represented by "
        "required authority. Keep authority_refs unchanged. Do not add a "
        "replacement proposal for that target.",
    ),
    "semantic_proposal_target_duplicate": (
        "Each proposed target may appear only once. Return one complete "
        "candidate without repeated proposed targets.",
    ),
}


def semantic_regeneration_guidance(code: str) -> tuple[str, ...]:
    if type(code) is not str or code not in SEMANTIC_REGENERABLE_FAILURE_CODES:
        raise ValueError("semantic regeneration diagnostic invalid")
    value = _SEMANTIC_REGENERATION_GUIDANCE.get(code)
    return () if value is None else (value,)
```

The helper receives only a closed code. Never add target, Candidate, prompt, or
exception parameters.

- [ ] **Step 4: Add initial-prompt and exact-regeneration RED tests**

In `tests/test_leader_cli.py`, add helpers:

```python
def semantic_candidate_with_required_target_proposal() -> dict[str, object]:
    value = semantic_cli_candidate()
    value["steps"][0]["proposed_effects"] = [
        {
            "target": "artifact.txt",
            "operation": "create",
            "sensitivity": "ordinary",
        }
    ]
    value["summary"] = "FIRST_CONFLICT_CANDIDATE_SECRET"
    return value


def semantic_candidate_with_duplicate_new_target() -> dict[str, object]:
    value = semantic_cli_candidate()
    proposal = {
        "target": "notes.md",
        "operation": "create",
        "sensitivity": "ordinary",
    }
    value["steps"][0]["proposed_effects"] = [dict(proposal)]
    value["steps"][1]["proposed_effects"] = [dict(proposal)]
    value["summary"] = "FIRST_CONFLICT_CANDIDATE_SECRET"
    return value
```

Add a prompt parity test asserting both
`OpenAICompatibleProvider()._system_prompt(request)` and
`CodexCliProvider()._prompt(request)` contain these exact rules:

```text
Targets already represented by required authority must appear only through authority_refs and must not appear in proposed_effects.
Every proposed_effects target must be genuinely new and appear at most once across the complete candidate.
```

- [ ] **Step 5: Add API regeneration matrix RED**

Parameterize the existing fake HTTP pattern for the two first Candidates and
expected codes/guidance. The test must assert:

```python
assert len(calls) == 2
assert calls[0]["body"]["model"] == calls[1]["body"]["model"]
assert first_authority == second_authority
assert expected_code not in json.dumps(first_messages)
assert expected_code in json.dumps(second_messages)
assert expected_guidance in json.dumps(second_messages)
assert "FIRST_CONFLICT_CANDIDATE_SECRET" not in json.dumps(second_messages)
assert result.semantic_diagnostics == (
    {
        "code": expected_code,
        "attempt_count": 1,
        "regeneration_used": False,
    },
)
assert result.leader_generation["attempt_count"] == 2
```

The second fake response is `semantic_cli_candidate()`.

- [ ] **Step 6: Add native CLI regeneration matrix RED**

Parameterize both `CodexCliProvider` and `ClaudeCliProvider` and both conflict
fixtures. Reuse the existing native fake subprocess envelope pattern. Assert:

```python
assert len(calls) == 2
assert calls[0]["schema"] == calls[1]["schema"]
assert normalized_commands[0] == normalized_commands[1]
assert expected_code in calls[1]["prompt"]
assert expected_guidance in calls[1]["prompt"]
assert "FIRST_CONFLICT_CANDIDATE_SECRET" not in calls[1]["prompt"]
assert result.leader_generation["attempt_count"] == 2
assert result.leader_generation["regeneration_used"] is True
```

Also add two double-failure cases and one “first conflict, second missing
requirement” case. The terminal error must expose the true second code with
`attempt_count == 2`; no test may observe a third call.

- [ ] **Step 7: Add diagnostic-contract and M2c terminal RED**

In `tests/test_leader_plan_schema.py`, verify:

```python
assert "semantic_required_target_reproposed" in LEADER_PLAN_DIAGNOSTIC_CODES
assert "semantic_proposal_target_duplicate" in LEADER_PLAN_DIAGNOSTIC_CODES
assert "semantic_effect_conflict" in LEADER_PLAN_DIAGNOSTIC_CODES
```

Extend `test_closed_leader_terminal_preserves_allowlisted_diagnostic()` in
`tests/test_m2c_live_acceptance.py` with both new schema diagnostics. The
projected terminal must still expose exactly:

```text
stage
diagnostic_code
attempt_count
constraint_mode
```

- [ ] **Step 8: Run the Provider RED set**

Run the exact new tests plus the existing regeneration tests:

```bash
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_semantic_planning.py \
  tests/test_leader_cli.py -k 'semantic and (regener or target or prompt)' \
  tests/test_leader_plan_schema.py -k 'semantic and diagnostic' \
  tests/test_m2c_live_acceptance.py -k 'closed_leader_terminal' \
  -q
```

Expected: FAIL because initial prompts lack the target rules and regenerated
prompts lack the code-specific guidance.

- [ ] **Step 9: Consume shared rules in both Providers**

Import `semantic_regeneration_guidance` in both Provider modules.

In each semantic prompt, insert exactly:

```python
"Targets already represented by required authority must appear only through authority_refs and must not appear in proposed_effects.",
"Every proposed_effects target must be genuinely new and appear at most once across the complete candidate.",
```

In each regeneration branch, after the closed diagnostic line:

```python
lines.extend(semantic_regeneration_guidance(request.regeneration_diagnostic))
```

Keep the existing complete-replacement instruction after the static guidance.
Do not duplicate the guidance strings in Provider modules.

- [ ] **Step 10: Run Provider GREEN and broader semantic regression**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_semantic_planning.py \
  tests/test_leader_cli.py \
  tests/test_leader_plan_schema.py \
  tests/test_cli_structured_output.py \
  -q
```

Expected: all PASS. Existing schema hashes remain unchanged because guidance is
prompt-only and `regeneration_diagnostic` remains excluded from schema bytes.

- [ ] **Step 11: Synchronize semantic authority documentation**

Update the required/proposed and failure sections of
`docs/superpowers/specs/2026-07-15-leader-semantic-authority-design.md`:

- required targets are exclusive across the Candidate;
- proposed targets must be genuinely new and Mission-wide unique;
- add both new codes;
- retain `semantic_effect_conflict` as historical compatibility;
- document one static code-specific same-Leader correction and no local repair.

Add a `HISTORY.md` entry with the exact RED/GREEN commands and counts. State
that no Provider/network/live call occurred.

- [ ] **Step 12: Commit the complete semantic change**

Run:

```bash
git diff --check
git add \
  src/agentdeck/semantic_planning.py \
  src/agentdeck/providers/cli_subprocess.py \
  src/agentdeck/providers/openai_compatible.py \
  tests/test_semantic_planning.py \
  tests/test_leader_cli.py \
  tests/test_leader_plan_schema.py \
  tests/test_m2c_live_acceptance.py \
  docs/superpowers/specs/2026-07-15-leader-semantic-authority-design.md \
  HISTORY.md
git commit -m "feat: enforce semantic proposal target ownership"
```

Do not amend this commit later. Any review correction gets a new commit and a
new eventual frozen SHA.

## Task 3: Make `_PtyTail` representation safe

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`

- [ ] **Step 1: Add direct representation RED**

Import `field` beside `asdict, dataclass` and add:

```python
def test_pty_tail_default_repr_excludes_raw_tail_bytes() -> None:
    capture = _PtyTail()
    capture.add(
        b"PTY_REPR_SECRET prompt /Users/private/raw stderr model output"
    )

    rendered = repr(capture)

    assert "PTY_REPR_SECRET" not in rendered
    assert "/Users/private" not in rendered
    assert "raw stderr" not in rendered
    assert "model output" not in rendered
    assert "tail=" not in rendered
    assert "byte_count=" in rendered
    assert "truncated=False" in rendered
    assert set(capture.diagnostic()) == {"byte_count", "truncated", "sha256"}
```

- [ ] **Step 2: Run the representation RED**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_m2c_live_acceptance.py::test_pty_tail_default_repr_excludes_raw_tail_bytes \
  -q
```

Expected: FAIL because the default dataclass representation contains `tail=`.

- [ ] **Step 3: Apply the minimal representation boundary**

Change only the field declaration:

```python
@dataclass
class _PtyTail:
    byte_count: int = 0
    truncated: bool = False
    tail: bytes = field(default=b"", repr=False)
```

Do not change `add()`, truncation, digest, drain, prompt matching, or
`diagnostic()`.

- [ ] **Step 4: Run direct GREEN and existing PTY tests**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  python -m pytest tests/test_m2c_live_acceptance.py \
  -k 'pty_tail or wait_for_mission_preview' -q
```

Expected: all selected tests PASS; the sole opt-in live node is not selected.

Do not commit until Task 4 proves the actual pytest report path.

## Task 4: Prove default pytest report redaction

**Files:**
- Modify: `tests/test_m2c_live_acceptance.py`
- Modify: `docs/validation/phase3-m2c-live-acceptance-sop.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Add the nested default-pytest RED/GREEN regression**

Add:

```python
def test_default_pytest_report_does_not_render_pty_tail(
    tmp_path: Path,
) -> None:
    sentinel = "PTY_REPORT_SECRET"
    path_marker = "/Users/private/pty-report-secret"
    probe = tmp_path / "test_pty_report_probe.py"
    probe.write_text(
        """
import os
import sys

repo = os.environ["AGENTDECK_M2C_REPORT_REPO"]
sys.path.insert(0, repo + "/tests")
sys.path.insert(0, repo + "/src")

from test_m2c_live_acceptance import (
    _PtyTail,
    _PreviewStore,
    _leader_terminal_fixture,
    _observe_mission_preview_or_terminal,
)

def test_report_probe():
    durable, events, baseline = _leader_terminal_fixture(
        stage="schema",
        diagnostic_code="semantic_required_target_reproposed",
        attempt_count=2,
    )
    capture = _PtyTail()
    capture.add(os.environ["AGENTDECK_M2C_REPORT_PAYLOAD"].encode("utf-8"))
    _observe_mission_preview_or_terminal(
        _PreviewStore([durable], [events]),
        capture,
        baseline_turn_ids=baseline,
    )
""".lstrip(),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["AGENTDECK_M2C_REPORT_REPO"] = str(Path(__file__).resolve().parents[1])
    env["AGENTDECK_M2C_REPORT_PAYLOAD"] = (
        f"{sentinel} {path_marker} prompt raw stderr model output"
    )

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(probe), "-q"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )

    rendered = completed.stdout + completed.stderr
    assert completed.returncode == 1
    assert "leader_schema_before_preview" in rendered
    assert "semantic_required_target_reproposed" in rendered
    assert '"byte_count"' in rendered
    assert '"truncated"' in rendered
    assert '"sha256"' in rendered
    for forbidden in (
        sentinel,
        path_marker,
        "prompt raw stderr model output",
        "tail=",
    ):
        assert forbidden not in rendered
```

The nested source contains no payload sentinel. The assertion concerns injected
PTY data; pytest may identify its own test source path.

- [ ] **Step 2: Prove the report test would have failed before `repr=False`**

Temporarily revert only the local `_PtyTail.tail` declaration to `tail:
bytes = b""`, run:

```bash
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_m2c_live_acceptance.py::test_default_pytest_report_does_not_render_pty_tail \
  -q
```

Expected: FAIL because nested pytest output contains `PTY_REPORT_SECRET` or
`tail=`.

Restore `field(default=b"", repr=False)` immediately. Do not commit the
temporary reversion.

- [ ] **Step 3: Run final report GREEN**

Run the same command again.

Expected: PASS. The nested pytest subprocess itself exits 1 as asserted, while
the outer regression exits 0.

- [ ] **Step 4: Run the complete non-live M2c harness**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  python -m pytest tests/test_m2c_live_acceptance.py -q
```

Expected: all deterministic cases PASS and exactly one opt-in real live case is
skipped. Record the exact count and duration; do not set
`AGENTDECK_M2C_LIVE=1`.

- [ ] **Step 5: Update SOP and history**

In `docs/validation/phase3-m2c-live-acceptance-sop.md`, state:

- default pytest traceback is inside the transcript-free boundary;
- `_PtyTail.tail` is process-local and `repr`-excluded;
- live operators must not depend on `--tb=short` or post-filtering;
- bounded count/truncation/hash remain the only PTY diagnostic projection.

Add a `HISTORY.md` entry with RED proof, GREEN command/count, full non-live
harness count, and explicit no-live/no-provider scope.

- [ ] **Step 6: Commit the reporting boundary**

Run:

```bash
git diff --check
git add \
  tests/test_m2c_live_acceptance.py \
  docs/validation/phase3-m2c-live-acceptance-sop.md \
  HISTORY.md
git commit -m "test: redact PTY tail from pytest reports"
```

## Task 5: Focused verification and scope review

**Files:**
- Inspect: all files changed since `2f280334`
- Modify only if verification exposes a real defect; every correction requires
  tests and a new `HISTORY.md` entry.

- [x] **Step 1: Run semantic and Provider focused suites**

```bash
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_semantic_planning.py \
  tests/test_leader_cli.py \
  tests/test_leader_plan_schema.py \
  tests/test_cli_structured_output.py \
  -q
```

Expected: all PASS.

Evidence: `740 passed in 9.27s`. The command used
`PYTHONPATH="$PWD/src"` because the conda editable install referenced an older
worktree.

- [x] **Step 2: Run Conversation and M2c non-live suites**

```bash
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_conversation_session.py \
  tests/test_conversation_terminal_ui.py \
  tests/test_m2c_live_acceptance.py \
  -q
```

Expected: deterministic tests PASS with exactly the existing opt-in live skip.

Evidence: the session/terminal-CLI/M2c selection exited `0`; collection was
`243` tests with the opt-in live node skipped. The complete M2c file passed
`192 passed, 1 skipped in 46.41s`. The planned `test_conversation_cli.py` name
did not exist; `test_conversation_terminal_ui.py` is the repository's actual
terminal conversation CLI boundary.

- [x] **Step 3: Compile and check the diff**

```bash
conda run --no-capture-output -n agentdeck \
  python -m compileall -q src tests
git diff --check 2f280334..HEAD
```

Expected: both exit 0.

Evidence: both exited `0`.

- [x] **Step 4: Audit scope and leakage**

Run:

```bash
git diff --name-only 2f280334..HEAD
rg -n \
  'PTY_REPORT_[A-Z0-9_]+|PTY_REPR_[A-Z0-9_]+|FIRST_CONFLICT_CANDIDATE_SECRET|REQUIRED_TARGET_VERIFICATION_SECRET|DUPLICATE_TARGET_VERIFICATION_SECRET|API_RAW_ENVELOPE_SECRET|NATIVE_IGNORED_OUTPUT_SECRET|candidate-only-notes-secret\.md' \
  tests HISTORY.md docs/superpowers/plans || true
rg -n \
  'PTY_REPORT_[A-Z0-9_]+|PTY_REPR_[A-Z0-9_]+|FIRST_CONFLICT_CANDIDATE_SECRET|REQUIRED_TARGET_VERIFICATION_SECRET|DUPLICATE_TARGET_VERIFICATION_SECRET|API_RAW_ENVELOPE_SECRET|NATIVE_IGNORED_OUTPUT_SECRET|candidate-only-notes-secret\.md' \
  src/agentdeck docs/validation docs/handoff || true
rg -n \
  'fallback|AGENTDECK_M2C_LIVE=1|test_real_four_stage_m2c_acceptance' \
  src/agentdeck tests/test_leader_cli.py \
  tests/test_m2c_live_acceptance.py
```

Required conclusions:

- synthetic markers occur only in deterministic test source, this
  implementation plan's literal examples, and HISTORY's explicit TDD/mutation
  evidence;
- no synthetic marker occurs in production source, validation evidence, or
  handoff;
- no new fallback exists;
- the live node was not invoked;
- no extractor/compiler/ACP/tmux/daemon/permission code changed.

Evidence: current `PTY_REPORT_*_7E16`, `PTY_REPR_*`, Candidate-only verification,
raw-envelope, and ignored-native-output markers appeared only in deterministic
tests, literal plan examples, and HISTORY's TDD/mutation record; none appeared
in production, validation, or handoff. No new fallback or live invocation
appeared; no extractor, compiler, ACP, tmux, daemon, permission, handoff, model,
login, timeout, or fallback code changed.

- [x] **Step 5: Handle any failure without hiding it**

If any command fails:

1. stop;
2. use `superpowers:systematic-debugging`;
3. add a deterministic RED for the actual defect;
4. make the minimal fix;
5. update `HISTORY.md`;
6. create a new correction commit;
7. rerun Task 5 from Step 1.

Do not amend prior commits and do not proceed with a red suite.

Evidence: the first focused command initially failed collection because the
conda editable install pointed to an older worktree; explicit current-checkout
`PYTHONPATH` identified and removed that environment ambiguity without changing
code. The second planned command named a nonexistent test file; the actual
terminal UI test file was used and the complete M2c file was also run directly.

## Task 6: Freeze the implementation and run two full suites

**Files:**
- Modify after verification:
  `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
- Modify after verification: `docs/handoff/current-development-state.md`
- Modify after verification: `HISTORY.md`
- Modify after verification: this plan
- Modify after verification:
  `docs/superpowers/specs/2026-07-16-m2c-semantic-conflict-and-pytest-redaction-design.md`

- [x] **Step 1: Freeze exact implementation authority**

Run:

```bash
test -z "$(git status --short)"
implementation_sha="$(git rev-parse HEAD)"
test -n "$implementation_sha"
printf '%s\n' "$implementation_sha"
```

Record the 40-character SHA. From this point, any code/test correction creates a
new implementation SHA and restarts Task 5 plus both full suites.

Frozen implementation SHA:
`75f0366d4d5619b29c77f10949365f43d46185b1`.

- [x] **Step 2: Create one detached verification checkout**

Use a path that does not collide with M2c live-root patterns:

```bash
feature_root="$(git rev-parse --show-toplevel)"
implementation_sha="$(git -C "$feature_root" rev-parse HEAD)"
short_sha="$(git rev-parse --short=12 "$implementation_sha")"
verify_root="/tmp/agentdeck-semantic-verify-$short_sha"
test ! -e "$verify_root"
git -C "$feature_root" worktree add --detach "$verify_root" "$implementation_sha"
test -z "$(git -C "$verify_root" status --short)"
```

- [x] **Step 3: Run full suite 1 on the frozen SHA**

```bash
feature_root="$(git rev-parse --show-toplevel)"
implementation_sha="$(git -C "$feature_root" rev-parse HEAD)"
short_sha="$(git -C "$feature_root" rev-parse --short=12 "$implementation_sha")"
verify_root="/tmp/agentdeck-semantic-verify-$short_sha"
cd "$verify_root"
PYTHONPATH="$verify_root/src" \
  conda run --no-capture-output -n agentdeck \
  python -m pytest -q
```

Expected: all tests PASS with only explicit opt-in live skips. Record exact
passed/skipped counts and duration.

Evidence: `4266 passed, 2 skipped in 199.05s`.

- [x] **Step 4: Reconfirm unchanged SHA**

```bash
feature_root="/Users/liuyue/.config/superpowers/worktrees/multi-agent-explore/codex/m2c-leader-preview-observability"
implementation_sha="$(git -C "$feature_root" rev-parse HEAD)"
short_sha="$(git -C "$feature_root" rev-parse --short=12 "$implementation_sha")"
verify_root="/tmp/agentdeck-semantic-verify-$short_sha"
test "$(git -C "$verify_root" rev-parse HEAD)" = "$implementation_sha"
test -z "$(git -C "$verify_root" status --short)"
```

- [x] **Step 5: Run full suite 2 independently**

Run:

```bash
feature_root="/Users/liuyue/.config/superpowers/worktrees/multi-agent-explore/codex/m2c-leader-preview-observability"
implementation_sha="$(git -C "$feature_root" rev-parse HEAD)"
short_sha="$(git -C "$feature_root" rev-parse --short=12 "$implementation_sha")"
verify_root="/tmp/agentdeck-semantic-verify-$short_sha"
cd "$verify_root"
PYTHONPATH="$verify_root/src" \
  conda run --no-capture-output -n agentdeck \
  python -m pytest -q
```

Expected: all tests PASS with the same collection/skip boundary. Record exact
counts and duration.

Evidence: `4266 passed, 2 skipped in 186.08s`.

- [x] **Step 6: Remove verification checkout and audit residuals**

Return to the feature worktree, then:

```bash
feature_root="/Users/liuyue/.config/superpowers/worktrees/multi-agent-explore/codex/m2c-leader-preview-observability"
implementation_sha="$(git -C "$feature_root" rev-parse HEAD)"
short_sha="$(git -C "$feature_root" rev-parse --short=12 "$implementation_sha")"
verify_root="/tmp/agentdeck-semantic-verify-$short_sha"
git -C "$feature_root" worktree remove --force "$verify_root"
git -C "$feature_root" worktree prune
test ! -e "$verify_root"
find /tmp /private/tmp -maxdepth 1 -type d \
  \( -name 'agentdeck-m2c-live-*' -o -name 'agentdeck-m2c-tools-live-*' \) \
  -print 2>/dev/null
ps -axo pid=,command= |
  rg 'pytest.*test_real_four_stage_m2c_acceptance|agentdeck.*daemon' |
  rg -v 'rg ' || true
```

Expected: no current-run live root, staged mirror, live pytest, or AgentDeck
daemon residual. Do not inspect or delete unrelated user tmux sessions.

Evidence: detached checkout removed; no matching current-run live root, live
pytest process, or AgentDeck daemon process was found.

- [x] **Step 7: Record frozen evidence**

Update the five listed documentation files with:

- exact implementation SHA;
- Task 5 focused counts;
- complete non-live M2c count;
- full suite 1 and 2 counts/durations;
- compile/diff/leakage/scope audit;
- zero preflight count for this new SHA;
- zero live count for this new SHA;
- residual audit;
- M2c still BLOCKED and M3 locked;
- next gate is explicit model selection plus authorization for exactly one new
  read-only preflight.

- [x] **Step 8: Commit evidence without changing implementation authority**

```bash
git diff --check
git add \
  docs/validation/2026-07-13-phase3-m2-project-daemon.md \
  docs/handoff/current-development-state.md \
  HISTORY.md \
  docs/superpowers/plans/2026-07-16-m2c-semantic-conflict-and-pytest-redaction.md \
  docs/superpowers/specs/2026-07-16-m2c-semantic-conflict-and-pytest-redaction-design.md
git commit -m "docs: record M2c semantic conflict verification"
```

The evidence commit is not the frozen implementation authority. Continue to
name the SHA from Step 1 when discussing preflight.

## Task 7: Stop at the new preflight authorization gate

**Files:**
- No code or state changes.

- [x] **Step 1: Do not inherit old authority**

Do not reuse:

```text
old frozen SHA 9db5b476f885cfcf68a55cbf59673a2d908d3fce
old gpt-5.5 preflight authorization
old live authorization
```

- [x] **Step 2: Request exact new human authority**

Report:

```text
implementation SHA: the exact 40-character value recorded in Task 6 Step 1
focused/non-live/full-suite evidence: PASS with exact counts
preflight count for new SHA: 0
live count for new SHA: 0
M2c: BLOCKED
M3: locked
```

Ask the human to name the exact Leader model id and authorize exactly one
read-only preflight on that SHA.

- [x] **Step 3: If later authorized, run only the designated preflight once**

This step is intentionally gated and must not execute during ordinary plan
implementation. After the human names the exact values, export them as
`AGENTDECK_M2C_FROZEN_SHA` and `AGENTDECK_M2C_LEADER_MODEL`, then use:

```bash
test -n "${AGENTDECK_M2C_FROZEN_SHA:-}"
test -n "${AGENTDECK_M2C_LEADER_MODEL:-}"
feature_root="/Users/liuyue/.config/superpowers/worktrees/multi-agent-explore/codex/m2c-leader-preview-observability"
preflight_root="/tmp/agentdeck-semantic-preflight-${AGENTDECK_M2C_FROZEN_SHA:0:12}"
test ! -e "$preflight_root"
git -C "$feature_root" worktree add \
  --detach "$preflight_root" "$AGENTDECK_M2C_FROZEN_SHA"
test "$(git -C "$preflight_root" rev-parse HEAD)" = \
  "$AGENTDECK_M2C_FROZEN_SHA"
test -z "$(git -C "$preflight_root" status --short)"
cd "$preflight_root"
PYTHONPATH="$preflight_root/src" \
AGENTDECK_M2C_LEADER_MODEL="$AGENTDECK_M2C_LEADER_MODEL" \
conda run --no-capture-output -n agentdeck \
  python -m pytest \
  tests/test_m2c_live_acceptance.py::test_m2c_live_preflight_is_read_only \
  -q -s
```

Never rerun the designated preflight in the same cycle. `ready=true` and
`blockers=[]` authorize only an evidence update and another stop. After
recording the result, remove the detached checkout exactly once:

```bash
feature_root="/Users/liuyue/.config/superpowers/worktrees/multi-agent-explore/codex/m2c-leader-preview-observability"
preflight_root="/tmp/agentdeck-semantic-preflight-${AGENTDECK_M2C_FROZEN_SHA:0:12}"
git -C "$feature_root" worktree remove --force "$preflight_root"
git -C "$feature_root" worktree prune
test ! -e "$preflight_root"
```

Evidence: the human bound `gpt-5.5` to frozen SHA
`75f0366d4d5619b29c77f10949365f43d46185b1` and authorized one preflight. It
ran exactly once, passed `1 passed in 3.75s`, and returned `ready=true`,
`blockers=[]`. The detached checkout was removed; the feature worktree remained
clean; residual process and live-root counts were both zero. Preflight count is
`1`; live count is `0`.

- [x] **Step 4: Never run live without a second separate authorization**

Do not set:

```text
AGENTDECK_M2C_LIVE=1
```

Do not invoke:

```text
test_real_four_stage_m2c_acceptance
```

until the human separately names the new frozen SHA, exact model, and unique
live attempt after a ready preflight. Any future live result is run once and is
never automatically retried.

Evidence: the human separately authorized frozen SHA
`75f0366d4d5619b29c77f10949365f43d46185b1` with Leader `gpt-5.5`. The real
node ran exactly once, failed `1 failed in 48.26s`, and was not retried. Its
closed result was `stage=live_acceptance`,
`code=native_schema_provenance_missing`, with one plan, one Mission, and zero
attempts, permissions, Worker replies, or handoffs. PTY evidence retained only
`byte_count=1438`, `truncated=false`, and
`sha256=4d261e29ad7cf2b3a5d19b899eb0cc734c8e86f19ec71e55731e39a2c6b706fa`.
The outer checkout/tool mirror were removed and current-run process matches
were zero. Preflight count and live count for this SHA are both `1`; neither may
be rerun.

## Completion checklist

- [x] Required targets are exclusive from proposals.
- [x] New proposed targets are Mission-wide unique.
- [x] Both new codes are closed, retryable once, and historical
  `semantic_effect_conflict` remains readable.
- [x] CLI and API use one shared static guidance helper.
- [x] Same Leader/model/schema/authority regeneration is proven.
- [x] No local Candidate repair or fallback exists.
- [x] `_PtyTail.tail` is excluded from default `repr`.
- [x] Nested default pytest report contains no injected PTY marker.
- [x] Complete non-live M2c harness passes with live skipped.
- [x] Compileall and diff checks pass.
- [x] Two independent full suites pass on one unchanged implementation SHA.
- [x] Residual audit is clean.
- [x] Evidence is committed separately from implementation authority.
- [x] No preflight or live runs without their new explicit authorizations.
- [x] M2c remains BLOCKED and M3 remains locked until a real four-stage PASS.
