# AgentDeck P0 Product Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the approved AgentDeck V1 product route, classify the current implementation and M2c evidence, record the deterministic baseline, and leave one unambiguous P1 Durable Mission Kernel handoff without changing product code.

**Architecture:** P0 is a documentation, inventory, and evidence phase. It turns the approved ten-part architecture reset into authoritative PRD, architecture, persistence, migration, and validation documents; it does not edit `src/agentdeck/**`, rerun a real M2c Mission, begin P1 implementation, or create empty target-package scaffolding. Every document is checked by deterministic content assertions, reviewed against the north star, and committed as a small reversible unit.

**Tech Stack:** Markdown, Python 3.12 read-only document assertions, `rg`, Git, pytest baseline in conda environment `agentdeck`.

---

## Execution guard

P0 starts from the documentation-only architecture-reset decision on branch
`codex/m2c-leader-preview-observability`. Before executing Task 1, record the
actual starting commit with `git rev-parse HEAD`; do not assume the SHA printed
when this plan was authored is still current.

P0 may modify only:

- `docs/product/**`
- `docs/architecture/**`
- `docs/migrations/**`
- `docs/validation/**`
- `docs/roadmap/product-north-star.md`
- `docs/roadmap/ultimate-goal-roadmap.md`
- `docs/handoff/current-development-state.md`
- `docs/superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md`
- this plan
- `HISTORY.md`

P0 must not modify:

- `src/agentdeck/**`
- `tests/**`
- `.agentdeck/**`
- global Codex/Claude configuration or authentication
- package installation or lock files

No real provider call, ACP session, tmux session, daemon process, preflight,
Golden Mission, merge, or push is authorized by this plan.

## File map

- Create `docs/product/agentdeck-v1-prd.md`: V1 user, promise, journey,
  acceptance, non-goals, and product decisions.
- Create `docs/architecture/agentdeck-v1-kernel-reset.md`: authoritative
  component/domain/daemon/adapter/governance/learning design.
- Create `docs/architecture/agentdeck-v1-state-migration.md`: SQLite authority,
  legacy import, ProjectView compatibility, backup, rollback, and cutover.
- Create `docs/migrations/2026-07-17-legacy-capability-inventory.md`: current
  retain/refactor/compat/archive/remove/missing classification.
- Create `docs/migrations/2026-07-17-m2c-test-migration-matrix.md`: map old M2c
  evidence into the new validation layers.
- Create `docs/validation/agentdeck-v1-validation-strategy.md`: deterministic,
  adapter-smoke, Golden A/B, release, diagnostic, and evidence gates.
- Create `docs/validation/2026-07-17-p0-baseline.md`: actual commands, results,
  worktree status, and scope audit from P0.
- Modify `docs/roadmap/product-north-star.md`: replace the active M2c delivery
  route with the approved P0-P5 route while preserving historical facts.
- Modify `docs/roadmap/ultimate-goal-roadmap.md`: point new work to the reset
  program and label M1/M2/M2c/M3 as historical capability labels.
- Modify `docs/handoff/current-development-state.md`: make P0 the active goal
  and preserve the old M2c section as superseded evidence.
- Modify `HISTORY.md`: record every P0 planning/audit/evidence commit.

### Task 1: Route authoritative project status to P0

**Files:**

- Modify: `docs/roadmap/product-north-star.md:1-20,130-165`
- Modify: `docs/roadmap/ultimate-goal-roadmap.md:1-12`
- Modify: `docs/handoff/current-development-state.md:1-18`
- Modify: `HISTORY.md:5`

- [ ] **Step 1: Record the starting repository facts**

Run:

```bash
git status --short --branch
git rev-parse HEAD
git log -3 --oneline --decorate
```

Expected: the intended architecture-reset worktree is active; no unexpected
user-owned changes are present. If unrelated changes exist, stop and do not
overwrite them.

- [ ] **Step 2: Run the routing assertion as RED**

Run:

```bash
conda run --no-capture-output -n agentdeck python - <<'PY'
from pathlib import Path

handoff = Path("docs/handoff/current-development-state.md").read_text()
north = Path("docs/roadmap/product-north-star.md").read_text()
roadmap = Path("docs/roadmap/ultimate-goal-roadmap.md").read_text()
assert "Active goal — AgentDeck P0 Product Reset" in handoff
assert "## Active architecture-reset route" in north
assert "P0 Product Reset" in roadmap
PY
```

Expected: FAIL because the three active-route markers are not yet aligned.

- [ ] **Step 3: Replace only the active routing text**

At the top of the handoff, add this exact authority statement before the old
M2c material and rename the old `Active goal` heading to `Superseded M2c
evidence`:

```markdown
## Active goal — AgentDeck P0 Product Reset

The approved 2026-07-17 architecture-reset program supersedes the historical
"close M2c, then begin M3" route. P0 is documentation, inventory, migration,
and baseline work only. No product source, real provider, ACP/tmux session,
preflight, live Mission, merge, or push is authorized.

Authoritative plans:

- `docs/superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md`
- `docs/superpowers/plans/2026-07-17-agentdeck-p0-product-reset.md`

The next product phase is P1 Durable Mission Kernel, but it remains locked
until every P0 exit criterion is recorded and reviewed.
```

In the north star, add `## Active architecture-reset route` and list P0-P5 in
strict order. Preserve prior M2 and M2c status as historical evidence and state
that old M2c is not a release veto or retry authority.

In the ultimate roadmap, add a top-level note that new development follows the
architecture-reset program; historical M1/M2/M2c/M3 labels remain evidence,
not current scheduling gates.

- [ ] **Step 4: Record the route change in HISTORY**

Add under `## 2026-07-17`:

```markdown
### Start the AgentDeck V1 architecture-reset program

- Replaced the historical close-M2c-then-M3 active route with the approved
  strictly ordered P0-P5 program. M2c remains preserved as legacy evidence but
  is no longer a release veto or an authorized live retry target.
- Made P0 documentation, inventory, migration design, and baseline evidence the
  sole current scope; P1 product code, real providers, ACP/tmux sessions,
  preflight/live runs, merge, and push remain out of scope.
```

- [ ] **Step 5: Run the routing assertion as GREEN**

Run the Step 2 command again.

Expected: PASS with exit code 0.

- [ ] **Step 6: Verify scope and commit**

Run:

```bash
git diff --check
git diff --name-only
```

Expected: only the four Task 1 documentation files changed.

Commit:

```bash
git add docs/roadmap/product-north-star.md \
  docs/roadmap/ultimate-goal-roadmap.md \
  docs/handoff/current-development-state.md HISTORY.md
git commit -m "docs: start AgentDeck V1 product reset"
```

### Task 2: Write the V1 product requirements

**Files:**

- Create: `docs/product/agentdeck-v1-prd.md`
- Modify: `HISTORY.md:5`

- [ ] **Step 1: Run the PRD contract as RED**

Run:

```bash
conda run --no-capture-output -n agentdeck python - <<'PY'
from pathlib import Path

path = Path("docs/product/agentdeck-v1-prd.md")
assert path.exists()
text = path.read_text()
for heading in (
    "## Product promise",
    "## Primary user journey",
    "## V1 functional requirements",
    "## Completion and pause semantics",
    "## Learning and self-improvement",
    "## Non-goals",
    "## V1 product acceptance",
):
    assert heading in text, heading
PY
```

Expected: FAIL because the PRD does not exist.

- [ ] **Step 2: Create the PRD with the approved product contract**

Create the file with these sections and concrete statements:

```markdown
# AgentDeck V1 Product Requirements

## Product promise
One natural-language software Mission, one exact confirmation, governed
Codex/Claude collaboration, background continuation, recovery, and evidence.

## Target user and problem
Local software developer; current pain is manual CLI choreography, repeated
approval babysitting, terminal-bound execution, and untrustworthy completion.

## Primary user journey
Bare `agentdeck`; explicit Leader selection; natural-language goal; Mission
Preview/edit/confirm; background execution; reconnect; activity; result.

## V1 functional requirements
Conversation, Leader choice, Mission version/digest, Task graph, automatic
Worker assignment with user constraints, Handoff, Evidence, graded acceptance,
bounded recovery, takeover/return, learning suggestions.

## Completion and pause semantics
Verification owns completion. New authority, drift, destructive/external
effect, exhausted budget, ambiguous side effect, or unrecoverable failure owns
pause. A generic BLOCKED response is insufficient.

## Learning and self-improvement
Learning Review may suggest memory, skill, or an Improvement Mission. Durable
application remains previewed, explicit, auditable, and reversible.

## Non-goals
No GUI, terminal emulator, A2A, third official Agent, provider matrix, parallel
mutating Missions, marketplace, remote execution, or silent self-modification.

## V1 product acceptance
Golden A, Golden B, disconnect/reconnect, daemon recovery, permission refusal,
takeover/return, fresh install, and no silent fallback.
```

Expand each section with the exact decisions in the program plan. Do not add
new providers, UI clients, or permissions.

- [ ] **Step 3: Record the PRD in HISTORY**

Add a `### Freeze the AgentDeck V1 product requirements` entry stating the
user journey, Codex/Claude scope, one-confirmation autonomy, background
continuation, graded acceptance, learning suggestions, and explicit non-goals.

- [ ] **Step 4: Run the PRD contract as GREEN**

Run the Step 1 command again.

Expected: PASS with exit code 0.

- [ ] **Step 5: Review against the north star and commit**

Run:

```bash
rg -n "one confirmation|Codex|Claude|background|Handoff|Evidence|Non-goals" \
  docs/product/agentdeck-v1-prd.md
git diff --check
```

Expected: every concept is present and the diff is clean.

Commit:

```bash
git add docs/product/agentdeck-v1-prd.md HISTORY.md
git commit -m "docs: define AgentDeck V1 product requirements"
```

### Task 3: Write the kernel architecture

**Files:**

- Create: `docs/architecture/agentdeck-v1-kernel-reset.md`
- Modify: `HISTORY.md:5`

- [ ] **Step 1: Run the architecture contract as RED**

Run:

```bash
conda run --no-capture-output -n agentdeck python - <<'PY'
from pathlib import Path

path = Path("docs/architecture/agentdeck-v1-kernel-reset.md")
assert path.exists()
text = path.read_text()
required = (
    "## Component boundary",
    "## Domain model and invariants",
    "## ProjectDaemon and recovery",
    "## Leader and Worker adapters",
    "## ACP, CLI/PTY, and tmux",
    "## Governance and verification",
    "## Learning lifecycle",
    "## Target dependency direction",
)
for heading in required:
    assert heading in text, heading
PY
```

Expected: FAIL because the architecture document does not exist.

- [ ] **Step 2: Create the architecture document**

Write the component diagram and entity chain from the program plan, then state
these invariants verbatim:

```markdown
- The ProjectDaemon is the only product-state writer.
- Confirmation binds one exact Mission version and authorization digest.
- Leader output is a proposal, never state-transition authority.
- Worker completion text is not Task completion authority.
- Workers learn upstream completion only through AgentDeck Handoffs.
- ACP, CLI/PTY, and tmux do not own Mission semantics.
- Skills and memory are context, never permission authority.
- Recovery never asks a model to guess whether an external effect happened.
```

Include the target responsibility map, but explicitly prohibit empty package
scaffolding or a big-bang file move.

- [ ] **Step 3: Record the architecture in HISTORY**

Add `### Define the evolutionary V1 kernel reset` and record the single daemon,
Mission/Task/Attempt/Permission/Handoff/Evidence model, adapter boundaries,
ACP-first transport, tmux observation, governance/verification separation,
and safe learning lifecycle.

- [ ] **Step 4: Run the architecture contract as GREEN**

Run the Step 1 command again.

Expected: PASS with exit code 0.

- [ ] **Step 5: Check dependency language and commit**

Run:

```bash
rg -n "only product-state writer|proposal|Handoffs|never permission|big-bang" \
  docs/architecture/agentdeck-v1-kernel-reset.md
git diff --check
```

Expected: all five boundaries are explicit.

Commit:

```bash
git add docs/architecture/agentdeck-v1-kernel-reset.md HISTORY.md
git commit -m "docs: define AgentDeck V1 kernel architecture"
```

### Task 4: Write the authoritative state-migration design

**Files:**

- Create: `docs/architecture/agentdeck-v1-state-migration.md`
- Modify: `HISTORY.md:5`

- [ ] **Step 1: Run the migration-design contract as RED**

Run:

```bash
conda run --no-capture-output -n agentdeck python - <<'PY'
from pathlib import Path

path = Path("docs/architecture/agentdeck-v1-state-migration.md")
assert path.exists()
text = path.read_text()
for token in (
    ".agentdeck/state.db",
    "WAL",
    "Migration Preview",
    "explicit confirmation",
    "backup",
    "temporary database",
    "atomic",
    "rollback",
    "project-view/v1",
    "project-view/v2",
):
    assert token in text, token
PY
```

Expected: FAIL because the migration design does not exist.

- [ ] **Step 2: Create the migration design**

Specify:

- one SQLite file and standard-library `sqlite3`;
- schema-migration table and monotonically increasing versions;
- event plus current-state update in one transaction;
- one daemon writer and no direct client database writes;
- artifact path/hash/summary storage rather than artifact bodies;
- preview, explicit confirm, backup, temp-write, verify, atomic switch;
- failure leaves legacy files unchanged;
- rollback restrictions after new writes;
- v1/v2 ProjectView projections from one authority;
- legacy CLI facade cutover and removal criteria.

Include an exact proposed command surface:

```bash
agentdeck migrate --preview
agentdeck migrate --confirm
agentdeck migrate --verify
agentdeck migrate --rollback
```

State that command spelling is a P1 design input, not implemented by P0.

- [ ] **Step 3: Record the migration decision in HISTORY**

Add `### Choose SQLite for the new Mission authority` and state the reversible
legacy migration, dual ProjectView projection, and no-silent-migration rules.

- [ ] **Step 4: Run the migration-design contract as GREEN**

Run the Step 1 command again.

Expected: PASS with exit code 0.

- [ ] **Step 5: Commit**

Run:

```bash
git diff --check
git add docs/architecture/agentdeck-v1-state-migration.md HISTORY.md
git commit -m "docs: design AgentDeck V1 state migration"
```

Expected: the documentation commit succeeds without source or test changes.

### Task 5: Classify the current implementation

**Files:**

- Create: `docs/migrations/2026-07-17-legacy-capability-inventory.md`
- Modify: `HISTORY.md:5`

- [ ] **Step 1: Capture the reproducible inventory inputs**

Run:

```bash
find src/agentdeck -maxdepth 2 -type f ! -path '*/__pycache__/*' | sort
find tests -maxdepth 1 -type f | sort
wc -l src/agentdeck/*.py tests/test_m2c_live_acceptance.py | sort -nr | head -n 30
rg -n "class (ConversationSession|ProjectDaemonService|StateStore)|PROJECT_VIEW_SCHEMA_VERSION" \
  src/agentdeck
```

Expected: concrete module paths, test paths, large-file pressure points, and
current authority classes are visible. Save only summarized facts in the
document, not generated absolute paths.

- [ ] **Step 2: Run the inventory contract as RED**

Run:

```bash
conda run --no-capture-output -n agentdeck python - <<'PY'
from pathlib import Path

path = Path("docs/migrations/2026-07-17-legacy-capability-inventory.md")
assert path.exists()
text = path.read_text()
for status in ("retain", "refactor", "compat", "archive", "remove", "missing"):
    assert f"`{status}`" in text, status
for component in (
    "Conversation",
    "ProjectDaemon",
    "ProjectView",
    "StateStore",
    "ACP",
    "tmux",
    "Learning Review",
    "M2c",
):
    assert component in text, component
PY
```

Expected: FAIL because the inventory document does not exist.

- [ ] **Step 3: Write the classification table**

Use columns:

```markdown
| Capability/module | Current authority | Status | Target owner | Migration phase | Characterization evidence | Removal gate |
```

At minimum classify:

- conversation package: `refactor`;
- daemon package: `retain` plus focused `refactor`;
- ProjectView v1: `compat`;
- `StateStore`: `refactor` into legacy importer/projection support;
- approval, ledger, permission lineage: `retain` and converge;
- existing providers/Leader gateway: `refactor` into adapters;
- ACP mapping/runtime: `retain` behind transport interface;
- tmux runtime: `retain` as view/fallback, remove authority assumptions;
- skill/memory/learning review: `retain` and integrate in P5;
- legacy command handlers in `cli.py`: `compat` then extract;
- public contract aggregation in `contracts.py`: `refactor` incrementally;
- `tests/test_m2c_live_acceptance.py`: `archive` after migration coverage;
- fixed-phase/count/one-shot authority assertions: `remove`;
- SQLite Mission store, v2 domain, adapter conformance, Golden A/B: `missing`.

No row may say only "keep" or "rewrite"; each row requires target owner,
phase, evidence, and removal gate.

- [ ] **Step 4: Record the inventory in HISTORY**

Add `### Classify the AgentDeck implementation for V1 migration` and summarize
the retained foundations, refactoring pressure points, archived mega-harness,
and missing P1-P4 capabilities.

- [ ] **Step 5: Run the inventory contract as GREEN and commit**

Run the Step 2 command again, then:

```bash
git diff --check
git add docs/migrations/2026-07-17-legacy-capability-inventory.md HISTORY.md
git commit -m "docs: classify AgentDeck V1 migration assets"
```

Expected: assertion and commit pass.

### Task 6: Map M2c evidence into the new test layers

**Files:**

- Create: `docs/migrations/2026-07-17-m2c-test-migration-matrix.md`
- Modify: `HISTORY.md:5`

- [ ] **Step 1: Capture the M2c test groups without executing live code**

Run:

```bash
rg -n '^def test_|^class Test' tests/test_m2c_live_acceptance.py > /tmp/agentdeck-m2c-test-names.txt
wc -l /tmp/agentdeck-m2c-test-names.txt
rg -n 'permission|handoff|preflight|redact|cleanup|leader|schema|takeover|tmux|acp' \
  /tmp/agentdeck-m2c-test-names.txt | head -n 120
rm /tmp/agentdeck-m2c-test-names.txt
```

Expected: only test names and counts are inspected; no pytest node, provider,
daemon, ACP, or tmux process runs.

- [ ] **Step 2: Run the matrix contract as RED**

Run:

```bash
conda run --no-capture-output -n agentdeck python - <<'PY'
from pathlib import Path

path = Path("docs/migrations/2026-07-17-m2c-test-migration-matrix.md")
assert path.exists()
text = path.read_text()
for layer in (
    "unit/state machine",
    "contract/security",
    "deterministic integration",
    "adapter conformance",
    "real adapter smoke",
    "Golden Mission",
    "archive",
):
    assert layer in text, layer
assert "not a release veto" in text
PY
```

Expected: FAIL because the matrix does not exist.

- [ ] **Step 3: Write the migration matrix**

Use columns:

```markdown
| Legacy concern | Useful invariant | New layer | New owner/test target | Preserve evidence | Delete old assertion when |
```

Map these groups explicitly:

- Leader structured output and schema diagnostics -> adapter conformance/smoke;
- semantic authority and confirmation digest -> domain/contract tests;
- permission lineage and sequential requests -> governance/integration tests;
- daemon disconnect/reconnect/crash -> deterministic integration;
- ACP and tmux behavior -> transport conformance and focused smoke;
- Handoff ordering -> domain/integration;
- redaction -> contract/security and adapter conformance;
- process cleanup -> shared live-smoke infrastructure;
- four fixed stages -> replace with dynamic Task graph plus Golden A/B;
- exact permission cardinality -> delete, preserve lineage invariant;
- frozen SHA/model/digest one-shot ceremony -> archive;
- global PATH/tool discovery -> readiness conformance;
- pytest-output parsing -> delete as product authority;
- takeover/return -> deterministic integration plus one Golden exercise.

State that the old file is removed only after every retained invariant has a
new owner and both Golden Missions have passed.

- [ ] **Step 4: Record the migration matrix in HISTORY**

Add `### Replace the M2c mega-gate with layered verification` and state that
useful failure cases are preserved, brittle harness-internal assertions are
retired, and old M2c is not a release veto or retry target.

- [ ] **Step 5: Run the matrix contract as GREEN and commit**

Run the Step 2 command again, then:

```bash
git diff --check
git add docs/migrations/2026-07-17-m2c-test-migration-matrix.md HISTORY.md
git commit -m "docs: map M2c evidence to V1 validation"
```

Expected: assertion and commit pass.

### Task 7: Write the V1 validation strategy

**Files:**

- Create: `docs/validation/agentdeck-v1-validation-strategy.md`
- Modify: `HISTORY.md:5`

- [ ] **Step 1: Run the validation-strategy contract as RED**

Run:

```bash
conda run --no-capture-output -n agentdeck python - <<'PY'
from pathlib import Path

path = Path("docs/validation/agentdeck-v1-validation-strategy.md")
assert path.exists()
text = path.read_text()
for heading in (
    "## Deterministic commit gate",
    "## Adapter conformance",
    "## Real smoke boundary",
    "## Golden A",
    "## Golden B",
    "## Failure taxonomy",
    "## Release gate",
):
    assert heading in text, heading
for forbidden in ("exactly two permissions", "exactly four stages"):
    assert forbidden not in text
PY
```

Expected: FAIL because the strategy does not exist.

- [ ] **Step 2: Create the validation strategy**

Define the five layers and exact responsibility of each. State these Golden
requirements:

```text
Golden A: Codex Leader, Codex implementation, Claude independent review.
Golden B: Claude Leader, Claude implementation, Codex independent review.
Both: bare agentdeck, explicit Leader, natural-language goal, exact one-time
Mission confirmation, background continuation, reconnect, governed permission
lineage, Handoff, tests, peer review, Evidence, readable final result.
```

Define `BLOCKED` only for missing environment prerequisites. Define stable
execution failures for Leader proposal, Worker start, Task execution, Handoff,
Verification, transport, permission, and recovery stages. Require leak-free
diagnostics to state side-effect uncertainty and retry safety.

State that live smoke is opt-in and isolated, but it is rerunnable after a
root-cause fix; no single-use authorization ceremony is part of product
correctness.

- [ ] **Step 3: Record validation strategy in HISTORY**

Add `### Define the AgentDeck V1 verification pyramid` and summarize the five
layers, Golden A/B, precise BLOCKED/FAILED split, and semantic—not textual—live
assertions.

- [ ] **Step 4: Run the strategy contract as GREEN and commit**

Run the Step 1 command again, then:

```bash
git diff --check
git add docs/validation/agentdeck-v1-validation-strategy.md HISTORY.md
git commit -m "docs: define AgentDeck V1 validation strategy"
```

Expected: assertion and commit pass.

### Task 8: Record the P0 deterministic baseline

**Files:**

- Create: `docs/validation/2026-07-17-p0-baseline.md`
- Modify: `HISTORY.md:5`

- [ ] **Step 1: Verify the P0 source scope before tests**

Run:

```bash
git diff --name-only HEAD~7..HEAD -- src/agentdeck tests
```

Expected: no output. If source or tests changed during P0, stop and investigate
before running the baseline.

- [ ] **Step 2: Run compile and focused deterministic baselines**

Run serially:

```bash
conda run --no-capture-output -n agentdeck \
  python -m compileall -q src tests

PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck \
  pytest \
    tests/test_conversation_acceptance.py \
    tests/test_daemon_acceptance.py \
    tests/test_daemon_crash_matrix.py \
    tests/test_mission.py \
    tests/test_mission_orchestration.py -q
```

Expected: compile exit 0 and focused tests PASS. No opt-in live node executes.

- [ ] **Step 3: Run the complete default suite once**

Run:

```bash
PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck pytest -q
```

Expected: the complete default suite exits 0; opt-in real ACP/preflight/live
nodes remain skipped. Record the exact pass/skip count and elapsed time. If it
fails, record the exact first failure and classify it; do not edit production
code under P0.

- [ ] **Step 4: Create the baseline evidence document**

Record:

- exact starting and ending commit;
- conda environment and Python version;
- compile result;
- focused result and elapsed time;
- full-suite result and elapsed time;
- exact skip reasons;
- confirmation that no real provider/ACP/tmux/daemon/live node ran;
- `git status --short`;
- source/test zero-change audit;
- existing known M2c legacy status without reclassifying it as PASS.

Do not paste prompts, credentials, absolute home paths, raw model output, or
complete terminal transcripts.

- [ ] **Step 5: Record baseline in HISTORY and commit**

Add `### Record the P0 architecture-reset baseline` with the exact observed
counts and scope facts, then run:

```bash
git diff --check
git add docs/validation/2026-07-17-p0-baseline.md HISTORY.md
git commit -m "docs: record AgentDeck P0 baseline"
```

Expected: commit succeeds; no live resources were created.

### Task 9: Self-review P0 and freeze the P1 handoff

**Files:**

- Modify: `docs/handoff/current-development-state.md`
- Modify: `docs/superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md`
- Modify: `docs/superpowers/plans/2026-07-17-agentdeck-p0-product-reset.md`
- Modify: `HISTORY.md:5`

- [ ] **Step 1: Run the complete P0 document contract**

Run:

```bash
conda run --no-capture-output -n agentdeck python - <<'PY'
from pathlib import Path

required = {
    "docs/product/agentdeck-v1-prd.md": ("Product promise", "V1 product acceptance"),
    "docs/architecture/agentdeck-v1-kernel-reset.md": ("Domain model", "Learning lifecycle"),
    "docs/architecture/agentdeck-v1-state-migration.md": ("state.db", "rollback"),
    "docs/migrations/2026-07-17-legacy-capability-inventory.md": ("retain", "missing"),
    "docs/migrations/2026-07-17-m2c-test-migration-matrix.md": ("Golden Mission", "not a release veto"),
    "docs/validation/agentdeck-v1-validation-strategy.md": ("Golden A", "Golden B"),
    "docs/validation/2026-07-17-p0-baseline.md": ("Full suite", "Scope audit"),
}
for name, tokens in required.items():
    path = Path(name)
    assert path.exists(), name
    text = path.read_text()
    for token in tokens:
        assert token in text, (name, token)
PY
```

Expected: PASS.

- [ ] **Step 2: Perform the scope and contradiction audit**

Run:

```bash
git log --name-only --format= HEAD~8..HEAD | \
  rg '^(src/agentdeck|tests|\.agentdeck)/' && exit 1 || true

rg -n "M2c remains.*only next gate|M3 remains locked.*M2c|retry.*M2c live" \
  docs/handoff/current-development-state.md \
  docs/roadmap/product-north-star.md \
  docs/roadmap/ultimate-goal-roadmap.md

rg -n "GUI|A2A|third Agent|marketplace|remote execution" \
  docs/product/agentdeck-v1-prd.md \
  docs/superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md
```

Expected: first command finds no source/test/runtime changes. The contradiction
scan may find only clearly labeled historical quotations/evidence, never the
active route. Future features appear only as non-goals or post-V1 work.

- [ ] **Step 3: Run the writing-plans self-review**

Check all P0 requirements against Tasks 1-8, then run:

```bash
rg -n "T[B]D|T[O]DO|implement lat[e]r|fill i[n]|appropriate error handlin[g]|similar to Tas[k]" \
  docs/superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md \
  docs/superpowers/plans/2026-07-17-agentdeck-p0-product-reset.md
```

Expected: no unresolved placeholder language. If a phrase appears as a quoted
forbidden example, rewrite it so the scan is empty.

- [ ] **Step 4: Freeze the P1 handoff without starting P1**

Update the handoff with:

```markdown
## P0 exit status

P0 documentation, inventory, migration design, validation strategy, and
baseline are complete. No product source, tests, real providers, ACP/tmux
sessions, preflight, live Mission, merge, or push occurred.

## Next gate

Review P0 evidence, then use `writing-plans` to create the separate P1 Durable
Mission Kernel task-level TDD plan. P1 implementation is not authorized by P0.
```

Check P0 in the program progress checklist and check completed steps in this
plan using the exact observed evidence.

- [ ] **Step 5: Record P0 completion in HISTORY**

Add `### Complete AgentDeck P0 Product Reset` and list the exact seven durable
documents, baseline result, source/test zero-change proof, and P1 planning-only
next gate.

- [ ] **Step 6: Run final verification and commit**

Run:

```bash
git diff --check
git status --short
conda run --no-capture-output -n agentdeck \
  python -m compileall -q src tests
```

Expected: clean diff, only Task 9 documentation changes before commit, and
compile exit 0.

Commit:

```bash
git add docs/handoff/current-development-state.md \
  docs/superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md \
  docs/superpowers/plans/2026-07-17-agentdeck-p0-product-reset.md \
  HISTORY.md
git commit -m "docs: freeze AgentDeck P0 product reset"
```

### Task 10: Present the P0 exit gate for human review

**Files:** None.

- [ ] **Step 1: Verify the final repository state**

Run:

```bash
git status --short --branch
git log -10 --oneline --decorate
```

Expected: clean worktree and the ordered P0 documentation commits.

- [ ] **Step 2: Report the exact P0 outcome**

The handoff must state:

- what documents were created;
- exact test/compile results;
- confirmation that product source and tests did not change;
- confirmation that no live/provider/ACP/tmux/daemon action ran;
- the P1 planning-only next gate;
- no merge or push occurred.

- [ ] **Step 3: Stop for review**

Do not write or execute the P1 plan until the user approves the P0 written
baseline and explicitly asks to continue.

## P0 definition of done

- [ ] Active roadmap and handoff route to P0-P5 rather than old M2c/M3 gates
- [ ] V1 PRD exists and matches the approved user journey/non-goals
- [ ] Kernel architecture exists with one authority and explicit invariants
- [ ] SQLite/legacy migration design is reversible and explicit
- [ ] Every major current capability has a migration classification
- [ ] Every useful M2c concern has a new validation owner or archive decision
- [ ] V1 validation strategy defines deterministic gates, smoke, Golden A/B
- [ ] Actual deterministic baseline is recorded without a live run
- [ ] `src/agentdeck/**`, `tests/**`, and `.agentdeck/**` are unchanged by P0
- [ ] HISTORY and current handoff agree on the next gate
- [ ] Worktree is clean and no merge/push occurred
