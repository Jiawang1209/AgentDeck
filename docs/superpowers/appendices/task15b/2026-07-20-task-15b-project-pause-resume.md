# Task 15B Project Pause and Explicit Resume Implementation Plan

> **Document role:** Historical/execution appendix — not an independent source
> of truth. Canonical authority remains the
> [Product Kernel Rewrite Design](../../specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md)
> and the
> [Product Kernel Rewrite TDD plan](../../plans/2026-07-18-agentdeck-product-kernel-rewrite.md).
> Read this appendix only where the canonical Task 15B section explicitly
> invokes its detailed execution steps.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/exit` pause the whole executing AgentDeck project, cancel the
exact current ACP Worker when necessary, recover conservatively on restart,
and require explicit `/resume` to continue from the first unclosed stage.

**Architecture:** SQLite remains the only durable authority. A new read-only
resume projection derives the closed stage prefix from existing Mission, Task,
Attempt, command, Evidence, and Handoff rows; no cursor table or schema change
is allowed. A foreground runtime owns only the exact live Worker binding, a
project lifecycle service owns the durable dispatch gate, and one async exit
coordinator performs external cancellation before an exact command-atomic
pause commit. ProductShell, recovery, execution, and Worker I/O share one
foreground event loop; paused startup is observational until explicit
`/resume`.

**Tech Stack:** Python 3.12, stdlib `asyncio`, official ACP Python SDK,
project-local SQLite v2, pytest/pytest-asyncio, conda environment `agentdeck`.

**Authority:**

- `docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md`
- `docs/superpowers/plans/2026-07-18-agentdeck-product-kernel-rewrite.md`
- `docs/superpowers/appendices/task15b/2026-07-20-task-15b-acp-cancellation-recovery-design.md`
- `docs/roadmap/product-north-star.md`
- `AGENTS.md`, `CLAUDE.md`, `AGENT.md`, and `HISTORY.md`

**Starting point:** clean commit
`2a752ccda081b7a599a79f27773348802d570029`.

**Hard boundaries:**

- keep the master 39-task order; Task 15B closes R2 before Task 27;
- no schema migration, cursor table, raw ACP resume token, background daemon,
  tmux transport authority, legacy CLI/Router/ConversationSession, JSON state,
  or M2c harness reuse;
- no provider call, authentication change, install, tmux input, push, or merge;
- every source and test file must remain at most 500 lines;
- every behavior commit includes its own `HISTORY.md` entry;
- every external cancellation happens outside a Store transaction;
- every dispatch requires `session.state == "running"` and an all-null pending
  exit group immediately before Worker creation;
- `SessionService.resume()` remains a read-only reload projection; mutating
  slash `/resume` uses only `ProjectLifecycleService`.

---

## File responsibility map

| File | Responsibility in Task 15B |
|---|---|
| `src/agentdeck/ports/execution_resume.py` | Bounded immutable resume facts and content-free projection error |
| `src/agentdeck/ports/store.py` | One typed read method for resume authority |
| `src/agentdeck/adapters/sqlite_execution_resume.py` | Strict no-write derivation from existing v2 rows |
| `src/agentdeck/adapters/sqlite.py` | Two-line delegation only |
| `src/agentdeck/application/execution_resume.py` | Pure conversion from snapshot to typed execution prefix/suffix |
| `src/agentdeck/application/project_lifecycle_service.py` | Durable dispatch gate and exact paused-to-running command |
| `src/agentdeck/application/execution_runtime.py` | Same-loop exact Attempt/Worker binding |
| `src/agentdeck/application/async_exit_coordinator.py` | Project stop request, cancellation, pause transaction, and replay |
| `src/agentdeck/application/execution_service.py` | Thin calls into gate/runtime/prevalidated resume plan |
| `src/agentdeck/application/recovery_service.py` | Conservative async restart convergence to paused |
| `src/agentdeck/adapters/acp_worker_connection.py` | Bounded cancel notification plus bounded owner reap |
| `src/agentdeck/adapters/acp.py` | Map transport cancellation to the closed Worker Port error |
| `src/agentdeck/adapters/acp_task_boundary.py` | Bounded prompt-task cancellation and caller-cancellation detection |
| `src/agentdeck/adapters/acp_worker_cancellation.py` | Content-free Worker cancellation cleanup facts |
| `src/agentdeck/product/shell.py` | Single async input loop and explicit resume child task |
| `src/agentdeck/product/shell_projection.py` | Pure preview/input projection helpers extracted for the shell line budget |
| `src/agentdeck/product/bootstrap.py` | Compose one loop, mandatory recovery, shared services/runtime |

### Mandatory line-budget edits

The implementation must not discover file splits ad hoc. Use these exact
budget moves:

- `sqlite.py` starts at 497 lines. Add the resume adapter import on the existing
  grouped adapter-import line, add the Store and transaction delegates, and
  reclaim the required lines by removing only vertical blank lines adjacent to
  existing one-line delegates. SQL and validation remain entirely in
  `sqlite_execution_resume.py`.
- `execution_service.py` starts at 500 lines. Replace the current nine-line
  local `attempts/evidence/committed_evidence/handoffs/revision_task` setup with
  one call to `initial_execution_state(resume_plan, draft)`, and replace the
  current literal ordinal range with the returned stage iterator. The new
  runtime/lifecycle constructor fields and calls consume only those reclaimed
  lines; no second execution path remains.
- `test_execution_coordinator.py` starts at 499 lines. Modify the existing
  `Harness` constructor in place and add no new test function. All resume tests
  live in new `test_project_lifecycle_service.py`.
- `test_exit_service.py` starts at 500 lines. Replace the existing
  `test_confirm_is_a_deterministic_fail_closed_blocker` with the internal
  `exit_confirmation_ready` assertion; add no test function. All async result,
  replay, race, and redaction cases live in new
  `test_product_exit_acp_integration.py`.
- `test_product_reentry.py` starts at 498 lines. Replace retired synchronous
  re-entry/cancellation cases in place and move the crash-gap matrix into
  `test_sqlite_recovery_integrity.py` and
  `test_product_exit_acp_integration.py`; do not append a second matrix.
- `test_recovery_service.py` starts at 493 lines. Replace old
  `CONFIRMED/RESUMED` and transport-reconcile cases with conservative recovery
  cases of equal or lower line count. New SQLite rollback cases live in
  `test_sqlite_recovery_integrity.py`.
- `acp.py` starts at 498 lines. Replace the existing generic cancellation
  branch in place. Shared cancellation resolution belongs in the focused
  `acp_worker_cancellation.py` helper; notification/owner logic remains in
  `acp_worker_connection.py`.

## Exact stage-closure rule

For a frozen four-stage Mission, stage `i` is closed if and only if all of the
following are true:

```text
frozen Task row matches canonical Mission task[i]
AND highest Attempt for that Task is completed
AND deterministic terminal command exists and is completed
AND command kind is execution_stage_committed
AND command result references that exact Mission/version/Task/Attempt
AND command references one or more exact validated Evidence rows
AND (
  stage is implementation/review/revision
  AND command references exactly one validated Handoff
  AND Handoff target is the direct next frozen Task
  OR
  stage is acceptance
  AND command handoff_id is null
)
```

Task state, Worker prose, events, orphan terminal rows, unreferenced Evidence,
or an unreferenced Handoff never close a stage. Closed stages must form one
continuous prefix.

---

### Task 15B.1: Add the durable project resume projection

**Commit:** `feat: add durable project resume projection`

**Files:**

- Modify: `src/agentdeck/kernel/session.py`
- Create: `src/agentdeck/ports/execution_resume.py`
- Modify: `src/agentdeck/ports/store.py`
- Create: `src/agentdeck/adapters/sqlite_execution_resume.py`
- Modify: `src/agentdeck/adapters/sqlite.py`
- Create: `src/agentdeck/application/execution_resume.py`
- Modify: `tests/product_kernel/test_kernel_session.py`
- Create: `tests/product_kernel/test_sqlite_execution_resume.py`
- Create: `tests/product_kernel/test_execution_resume.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Add deterministic RED tests for pause transitions and projection facts**

Add these exact cases to `test_kernel_session.py`:

```python
@pytest.mark.parametrize(
    "source",
    [SessionState.RUNNING, SessionState.AWAITING_APPROVAL,
     SessionState.NEEDS_ATTENTION],
)
def test_executing_project_can_pause_and_only_paused_project_can_resume(source):
    paused = ProductSession("ses_1", "/project", source).transition(
        SessionState.PAUSED
    )
    assert paused.state is SessionState.PAUSED
    assert paused.transition(SessionState.RUNNING).state is SessionState.RUNNING
```

Create `test_sqlite_execution_resume.py` with fixture helpers that insert one
exact confirmed four-stage Mission and only canonical Product Kernel rows. Add
these tests:

```python
def test_resume_projection_derives_first_unclosed_stage_from_closed_prefix(store):
    seed_closed_stage(store, "implementation", ordinal=1)
    snapshot = store.load_execution_resume("ses_1")
    assert snapshot.closed_stage_count == 1
    assert snapshot.first_unclosed_task_id == "tsk_review"
    assert snapshot.max_prior_attempt_ordinal == 0
    assert snapshot.next_attempt_ordinal == 1
    assert snapshot.preceding_handoff_id == "hnd_implementation_1"


def test_interrupted_stage_projects_higher_ordinal(store):
    seed_closed_stage(store, "implementation", ordinal=1)
    seed_interrupted_attempt(store, "review", ordinal=1)
    seed_interrupted_attempt(store, "review", ordinal=2)
    snapshot = store.load_execution_resume("ses_1")
    assert snapshot.first_unclosed_task_id == "tsk_review"
    assert snapshot.max_prior_attempt_ordinal == 2
    assert snapshot.next_attempt_ordinal == 3


def test_resume_projection_blocks_outcome_unknown(store):
    seed_outcome_unknown_attempt(store, "review", ordinal=1)
    with pytest.raises(
        ExecutionResumeProjectionError, match="resume_outcome_unknown"
    ):
        store.load_execution_resume("ses_1")
```

Add one parametrized mutation matrix covering:

```python
MUTATIONS = (
    "task_only_completed",
    "orphan_completed_attempt",
    "terminal_command_wrong_kind",
    "terminal_command_wrong_attempt",
    "terminal_command_extra_field",
    "missing_evidence",
    "unreferenced_extra_evidence",
    "evidence_hash_drift",
    "handoff_hash_drift",
    "handoff_wrong_target",
    "handoff_evidence_drift",
    "acceptance_has_handoff",
    "non_acceptance_missing_handoff",
    "attempt_ordinal_gap",
    "completed_stage_after_open_stage",
    "second_executing_mission",
    "pending_exit_present",
    "session_not_paused",
)
```

Every mutation must raise one allowlisted
`ExecutionResumeProjectionError` and leave the database byte-for-byte
unchanged according to a read-only table snapshot captured before the call.

Create `test_execution_resume.py` with:

```python
def test_planner_materializes_closed_context_and_remaining_suffix(snapshot):
    plan = ExecutionResumePlanner().materialize(snapshot)
    assert tuple(task.name for task in plan.remaining_tasks) == (
        "review", "revision", "acceptance"
    )
    assert plan.first_attempt_ordinal == 1
    assert tuple(item.attempt_id for item in plan.prior_attempts) == (
        "att_implementation_1",
    )
    assert tuple(item.handoff_id for item in plan.prior_handoffs) == (
        "hnd_implementation_1",
    )


def test_planner_uses_snapshot_next_ordinal_without_store_rescan(snapshot):
    replaced = dataclasses.replace(
        snapshot, max_prior_attempt_ordinal=2, next_attempt_ordinal=3
    )
    replaced = rehash_snapshot(replaced)
    with pytest.raises(
        ExecutionResumeProjectionError, match="resume_projection_malformed"
    ):
        ExecutionResumePlanner().materialize(replaced)
```

Also prove review Evidence rebuilds the exact `AuthoritativeRevisionTask`, a
completed acceptance returns `resume_mission_complete`, a legitimately seeded
interrupted first-open-stage history yields the next ordinal without a Store
rescan, and snapshot hash drift fails before any Worker factory can be supplied.

- [ ] **Step 2: Run RED and record the exact failure**

Run:

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_kernel_session.py \
  tests/product_kernel/test_sqlite_execution_resume.py \
  tests/product_kernel/test_execution_resume.py -q
```

Expected: collection fails because `agentdeck.ports.execution_resume` and
`agentdeck.application.execution_resume` do not exist; after the test imports
are staged independently, assertions fail because `SQLiteStore` has no
`load_execution_resume`.

- [ ] **Step 3: Implement the closed immutable Port values**

Create `ports/execution_resume.py` with these exact public values:

```python
EXECUTION_RESUME_MAX_BYTES: Final = 1_048_576


class ExecutionResumeProjectionError(ValueError):
    ALLOWED_CODES = frozenset({
        "resume_session_not_paused",
        "resume_pending_exit",
        "resume_mission_missing",
        "resume_mission_ambiguous",
        "resume_projection_malformed",
        "resume_outcome_unknown",
        "resume_stage_not_retryable",
        "resume_ordinal_exhausted",
        "resume_mission_complete",
    })

    def __init__(self, *, code: str) -> None:
        if type(code) is not str or code not in self.ALLOWED_CODES:
            raise ValueError("resume projection code is not allowlisted")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ResumeAttemptFacts:
    attempt_id: str
    task_id: str
    agent_instance_id: str | None
    ordinal: int
    state: str
    reason: str | None
    result_summary: str | None
    retryable: bool
    acp_session_id: str | None
    effect_observed: bool
    durable_fingerprint: str


@dataclass(frozen=True)
class ResumeEvidenceFacts:
    evidence_id: str
    task_id: str
    attempt_id: str
    kind: str
    canonical_evidence_facts: str
    content_hash: str


@dataclass(frozen=True)
class ResumeHandoffFacts:
    handoff_id: str
    source_attempt_id: str
    target_task_id: str
    result_summary: str
    canonical_handoff_facts: str
    content_hash: str


@dataclass(frozen=True)
class ResumeStageFacts:
    task_id: str
    task_ordinal: int
    name: str
    role: str
    planned_backend: str
    planned_agent_instance_id: str
    acp_route: str
    task_state: str
    canonical_task_facts: str
    attempts: tuple[ResumeAttemptFacts, ...]
    terminal_command_id: str | None
    terminal_command_hash: str | None
    terminal_attempt_id: str | None
    evidence: tuple[ResumeEvidenceFacts, ...]
    handoff: ResumeHandoffFacts | None


@dataclass(frozen=True)
class ExecutionResumeFacts:
    session_id: str
    session_state: str
    mission_id: str
    mission_version: int
    mission_content_hash: str
    canonical_mission_facts: str
    stages: tuple[ResumeStageFacts, ...]


@dataclass(frozen=True)
class ExecutionResumeSnapshot:
    facts: ExecutionResumeFacts
    closed_stage_count: int
    first_unclosed_task_id: str | None
    max_prior_attempt_ordinal: int
    next_attempt_ordinal: int | None
    preceding_handoff_id: str | None
    content_hash: str
```

Each dataclass `__post_init__` must use exact types, strict UTF-8 byte bounds,
typed identity prefixes, SQLite signed-64 integer bounds, closed optional
groups, and immutable tuples. Implement one `canonical_facts()` method on
`ExecutionResumeSnapshot` and verify:

```python
encoded = json.dumps(
    canonical_facts,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8", "strict")
if not encoded or len(encoded) > EXECUTION_RESUME_MAX_BYTES:
    raise ExecutionResumeProjectionError(code="resume_projection_malformed")
if not compare_digest(sha256(encoded).hexdigest(), self.content_hash):
    raise ExecutionResumeProjectionError(code="resume_projection_malformed")
```

Before accepting the hash, call pure
`derive_resume_cursor(self.facts) -> tuple[int, str | None, int, int | None,
str | None]` and compare it exactly with `closed_stage_count`,
`first_unclosed_task_id`, `max_prior_attempt_ordinal`,
`next_attempt_ordinal`, and `preceding_handoff_id`. The helper derives all five
values from validated stage/Attempt/Handoff facts; callers cannot alter a
cursor and legitimize it by recomputing SHA-256. `validate_hash()` repeats the
same derivation so `dataclasses.replace()` cannot bypass construction checks.

Export only the public facts, snapshot, error, and size constant.

- [ ] **Step 4: Add the Store read Port and strict SQLite derivation**

Add to `Store`:

```python
def load_execution_resume(
    self, session_id: str
) -> ExecutionResumeSnapshot: ...
```

Add the identical read signature to `StoreTransaction`. The SQLite command
transaction delegates it to `sqlite_execution_resume.load_execution_resume()`
with its live transaction connection, never a second read connection:

```python
def load_execution_resume(
    self, session_id: str
) -> ExecutionResumeSnapshot: ...
```

Add a RED command-callback test that changes one resume fact between the
initial read and `ProjectLifecycleService.resume()`: the transaction-level
projection sees a different full hash and rolls back the session/event/command
write together.

The two Protocol declarations use Python's conventional abstract-method
ellipsis; both concrete SQLite delegates are implemented in this step. Add one
import and this delegate to `SQLiteStore`:

```python
def load_execution_resume(self, session_id: str):
    return load_execution_resume(self._read_connection(), session_id)
```

Add the transaction delegate next to `load_aggregate`:

```python
def load_execution_resume(self, session_id: str):
    return load_execution_resume(self._require_mutable(), session_id)
```

Implement `sqlite_execution_resume.load_execution_resume()` with this fixed
read order:

1. exact `product_sessions` row by supplied ID; require `paused` and five null
   pending-exit columns;
2. query executing Missions for that session with `state IN
   ('confirmed','running')`; require exactly one, never `LIMIT 1`;
3. validate Mission canonical content/hash/version and exactly four frozen
   tasks;
4. query ordered Task rows and exact Agent Instance lineage;
5. query all Attempts ordered by Task ordinal and Attempt ordinal;
6. require contiguous ordinals, no active row, and no `outcome_unknown`;
7. for each completed highest Attempt, reconstruct its deterministic terminal
   command ID using `_records.command_id("terminal", confirmed, task,
   ordinal)` equivalent canonical inputs;
8. validate exact completed command result, referenced Evidence, and required
   direct Handoff;
9. require closed stages to form one prefix and all later stages to have no
   execution facts;
10. return the canonically hashed immutable snapshot.

Use these exact query shapes; do not use private latest-state selection:

```sql
SELECT m.mission_id,m.state,m.current_version,v.version,v.content_hash,
       v.canonical_mission_facts,v.confirmed_at
  FROM missions AS m
  JOIN mission_versions AS v
    ON v.mission_id=m.mission_id AND v.version=m.current_version
 WHERE m.session_id=? AND m.state IN ('confirmed','running')
```

```sql
SELECT task_id,ordinal,name,role,planned_backend,
       planned_agent_instance_id,acp_route,state,canonical_task_facts
  FROM tasks WHERE mission_id=? AND mission_version=? ORDER BY ordinal
```

```sql
SELECT a.attempt_id,a.task_id,a.agent_instance_id,a.ordinal,a.state,a.reason,
       a.result_summary,a.retryable,a.acp_session_id,a.effect_observed,
       a.created_at,a.updated_at
  FROM attempts AS a JOIN tasks AS t ON t.task_id=a.task_id
 WHERE t.mission_id=? AND t.mission_version=?
 ORDER BY t.ordinal,a.ordinal
```

For Evidence and Handoff, fetch only the IDs referenced by the validated
terminal command, then separately reject extra rows for the same terminal
Attempt. Hash canonical Evidence and Handoff content with SHA-256 and rebuild
the existing kernel `Evidence` and `Handoff` values before accepting them.

- [ ] **Step 5: Implement the pure resume planner**

Create these exact public values in `application/execution_resume.py`:

```python
@dataclass(frozen=True)
class ExecutionResumePlan:
    snapshot_hash: str
    confirmed: ConfirmedMissionVersion
    draft: MissionDraft
    prior_attempts: tuple[Attempt, ...]
    prior_evidence: tuple[Evidence, ...]
    prior_handoffs: tuple[Handoff, ...]
    revision_task: AuthoritativeRevisionTask
    remaining_tasks: tuple[TaskDefinition, ...]
    first_attempt_ordinal: int


class ExecutionResumePlanner:
    def materialize(
        self, snapshot: ExecutionResumeSnapshot
    ) -> ExecutionResumePlan:
        if type(snapshot) is not ExecutionResumeSnapshot:
            raise TypeError("resume planner requires ExecutionResumeSnapshot")
        snapshot.validate_hash()
        if snapshot.first_unclosed_task_id is None:
            raise ExecutionResumeProjectionError(
                code="resume_mission_complete"
            )
        draft, confirmed = rebuild_confirmed_mission(snapshot.facts)
        prior = rebuild_execution_history(snapshot, draft)
        remaining = draft.tasks[snapshot.closed_stage_count:]
        if not remaining or remaining[0].task_id != snapshot.first_unclosed_task_id:
            raise ExecutionResumeProjectionError(
                code="resume_projection_malformed"
            )
        return ExecutionResumePlan(
            snapshot_hash=snapshot.content_hash,
            confirmed=confirmed,
            draft=draft,
            prior_attempts=prior.attempts,
            prior_evidence=prior.evidence,
            prior_handoffs=prior.handoffs,
            revision_task=prior.revision_task,
            remaining_tasks=remaining,
            first_attempt_ordinal=snapshot.next_attempt_ordinal,
        )
```

`rebuild_confirmed_mission()` must parse the frozen canonical Mission using the
existing strict Leader proposal/domain constructors, then require exact
Mission ID, version, content hash, four tasks, Agent identities, routes, and
dependency chain. `rebuild_execution_history()` must use existing Attempt,
Evidence, Handoff, and `AuthoritativeRevisionTask.from_review()` constructors;
`prior_attempts` includes every Attempt in the closed prefix plus every prior
`interrupted` Attempt of the first unclosed stage. Evidence and Handoffs come
only from closed terminal bundles. It must never read the Store.

- [ ] **Step 6: Run GREEN and projection regression**

Run:

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_kernel_session.py \
  tests/product_kernel/test_sqlite_execution_resume.py \
  tests/product_kernel/test_execution_resume.py \
  tests/product_kernel/test_sqlite_mission.py \
  tests/product_kernel/test_sqlite_execution.py \
  tests/product_kernel/test_sqlite_quality.py -q
```

Expected: all pass; schema fingerprint and table list remain v2 and unchanged.

- [ ] **Step 7: Enforce line, diff, and no-migration boundaries**

Run:

```bash
conda run -n agentdeck python -c \
  'from pathlib import Path; files=[Path(p) for p in ("src/agentdeck/kernel/session.py","src/agentdeck/ports/execution_resume.py","src/agentdeck/ports/store.py","src/agentdeck/adapters/sqlite_execution_resume.py","src/agentdeck/adapters/sqlite.py","src/agentdeck/application/execution_resume.py","tests/product_kernel/test_kernel_session.py","tests/product_kernel/test_sqlite_execution_resume.py","tests/product_kernel/test_execution_resume.py")]; over={str(p):len(p.read_text().splitlines()) for p in files if len(p.read_text().splitlines())>500}; assert not over, over'
git diff --check
git diff --name-only -- src/agentdeck/adapters/sqlite_schema.py \
  src/agentdeck/adapters/sqlite_migrations.py
```

Expected: no oversized file, clean diff check, and no schema/migration diff.

- [ ] **Step 8: Update HISTORY and commit**

Document the no-new-schema resume authority, exact closed-stage bundle, and
RED/GREEN counts in `HISTORY.md`, then run:

```bash
git add HISTORY.md \
  src/agentdeck/kernel/session.py \
  src/agentdeck/ports/execution_resume.py src/agentdeck/ports/store.py \
  src/agentdeck/adapters/sqlite_execution_resume.py \
  src/agentdeck/adapters/sqlite.py \
  src/agentdeck/application/execution_resume.py \
  tests/product_kernel/test_kernel_session.py \
  tests/product_kernel/test_sqlite_execution_resume.py \
  tests/product_kernel/test_execution_resume.py
git commit -m "feat: add durable project resume projection"
```

---

### Task 15B.2: Close bounded ACP project cancellation

**Commit:** `feat: close bounded acp project cancellation`

**Files:**

- Modify: `src/agentdeck/ports/worker.py`
- Modify: `src/agentdeck/adapters/acp_worker_connection.py`
- Modify: `src/agentdeck/adapters/acp.py`
- Modify: `src/agentdeck/adapters/acp_task_boundary.py`
- Create: `src/agentdeck/adapters/acp_worker_cancellation.py`
- Modify: `tests/product_kernel/test_acp_worker_connection.py`
- Modify: `tests/product_kernel/test_acp_worker_failures.py`
- Create: `tests/product_kernel/test_product_exit_real_acp_cancellation.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED tests for the closed error and two-phase success**

Add to `test_acp_worker_connection.py`:

```python
@pytest.mark.asyncio
async def test_cancel_succeeds_only_after_notification_and_owner_reap(owner):
    connection = owner.connection()
    await connection.initialize(PROTOCOL_VERSION)
    await connection.new_session(cwd=owner.project_root)
    await connection.cancel("raw_session")
    assert owner.calls[-2:] == [
        ("cancel", "raw_session"),
        ("owner_reaped",),
    ]
    assert connection.closed is True


@pytest.mark.asyncio
async def test_cancel_notification_timeout_is_closed_and_content_free(owner):
    owner.cancel_blocks = True
    connection = owner.connection(timeout_seconds=0.01)
    await connection.initialize(PROTOCOL_VERSION)
    await connection.new_session(cwd=owner.project_root)
    with pytest.raises(WorkerCancellationError) as captured:
        await connection.cancel("raw_session")
    assert captured.value.code == "cancel_timeout"
    assert captured.value.outcome_known is False
    assert "raw_session" not in str(captured.value)
    assert connection.closed is True


@pytest.mark.asyncio
async def test_owner_reap_timeout_never_reports_cancel_success(owner):
    owner.reap_blocks = True
    connection = owner.connection(timeout_seconds=0.01)
    await connection.initialize(PROTOCOL_VERSION)
    await connection.new_session(cwd=owner.project_root)
    with pytest.raises(WorkerCancellationError) as captured:
        await connection.cancel("raw_session")
    assert captured.value.code == "cancel_timeout"
    assert captured.value.outcome_known is False
    assert owner.cancel_count == 1
    assert connection.closed is True
```

Parametrize disconnect, unexpected SDK exception, remotely raised
`CancelledError`, and hostile exception text containing a credential-shaped
string. Assert that `str`, `repr`, `args`, `__dict__`, and all public
properties contain only the allowlisted code and exact bool. Separately cancel
the real caller Task and assert its `CancelledError` propagates only after the
connection owner is reaped.

Add to `test_acp_worker_failures.py`:

```python
def test_worker_cancellation_error_is_closed_and_content_free():
    error = WorkerCancellationError(
        code="transport_disconnected", outcome_known=False
    )
    assert error.code == "transport_disconnected"
    assert error.outcome_known is False
    assert error.args == ("transport_disconnected", False)
    assert set(error.__dict__) == {"code", "outcome_known"}
    assert error.__cause__ is None
    assert error.__context__ is None


def test_acp_worker_preserves_exact_cancellation_failure(harness):
    harness.agent.cancel_error = WorkerCancellationError(
        code="cancel_timeout", outcome_known=False
    )
    with pytest.raises(WorkerCancellationError) as captured:
        asyncio.run(harness.cancel())
    assert (captured.value.code, captured.value.outcome_known) == (
        "cancel_timeout", False
    )
    assert harness.worker_event_kinds().count("cancelled") == 0
```

- [ ] **Step 2: Run RED and record the exact failure**

Run:

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_acp_worker_connection.py \
  tests/product_kernel/test_acp_worker_failures.py -q
```

Expected: import fails because `WorkerCancellationError` is absent; after its
test import is isolated, the current `finally: await self.aclose()` path cannot
distinguish notification timeout from owner-reap completion.

- [ ] **Step 3: Add the exact content-free Worker Port error**

Add this implementation to `ports/worker.py` and export it:

```python
class WorkerCancellationError(RuntimeError):
    ALLOWED_CODES = frozenset({
        "cancel_rejected",
        "cancel_timeout",
        "transport_disconnected",
    })

    def __init__(self, *, code: str, outcome_known: bool) -> None:
        if type(code) is not str or code not in self.ALLOWED_CODES:
            raise ValueError("cancellation code is not allowlisted")
        if type(outcome_known) is not bool:
            raise TypeError("outcome_known must be an exact bool")
        self.code = code
        self.outcome_known = outcome_known
        super().__init__(code, outcome_known)
```

Do not construct this error inside an `except` block that caught a raw
transport failure. `raise ... from None` only suppresses display; it does not
clear `__context__`. Capture only `(code, outcome_known)`, leave the `except`
block, then construct and raise the sanitized error. The original exception is
neither stored as cause/context nor copied into a Diagnostic.

- [ ] **Step 4: Split notification send from bounded owner shutdown**

Refactor `ACPWorkerConnection.cancel()` to this control shape:

```python
async def cancel(self, *args: object, **kwargs: Any) -> None:
    connection = self._connection_or_none()
    notification_failure: tuple[str, bool] | None = None
    if connection is None:
        notification_failure = ("cancel_rejected", True)
    try:
        if connection is not None:
            await asyncio.wait_for(
                connection.cancel(*args, **kwargs),
                timeout=self.timeout_seconds,
            )
    except TimeoutError:
        notification_failure = ("cancel_timeout", False)
    except (BrokenPipeError, ConnectionError, EOFError):
        notification_failure = ("transport_disconnected", False)
    except asyncio.CancelledError as error:
        if caller_cancellation_pending():
            caller_cancelled = error
        else:
            notification_failure = ("cancel_timeout", False)
    except BaseException:
        notification_failure = ("transport_disconnected", False)

    shutdown_failure = await self._bounded_shutdown_facts()
    if caller_cancelled is not None:
        raise caller_cancelled
    failure = notification_failure or shutdown_failure
    if failure is not None:
        code, outcome_known = failure
        raise WorkerCancellationError(
            code=code, outcome_known=outcome_known
        ) from None
```

`cancel_rejected/outcome_known=True` is the production local precondition for
an already-closed or never-connected owner; ACP cancel is a notification and
does not invent a remote rejection acknowledgement. Implement
`_bounded_shutdown_facts()` so it atomically takes `_manager`, clears
connection references, sets `_closed=True`, and awaits exactly one
`manager.__aexit__(None, None, None)` under `asyncio.wait_for`. Return
only `(code, outcome_known)` or `None`, constructing no Port exception inside
its raw `except` blocks. Return `cancel_timeout/False` on timeout and
`transport_disconnected/False` on any other exception. Existing initialize/
session/prompt/error close paths continue to use `aclose()` and remain
terminal; pre-start close and synchronous spawn failure tests must stay green.
Caller cancellation must not skip the bounded owner reap, and must be
re-raised rather than normalized after that cleanup.

- [ ] **Step 5: Map the ACP Worker cancellation without losing its code**

Change the `_Run.error` union to include `WorkerCancellationError`. Resolve
notification and prompt cleanup as content-free facts. Caller cancellation is
retained separately from transport failure and re-raised only after the prompt
task is done or both cleanup bounds have expired:

```python
failure: tuple[str, bool] | None = None
try:
    await self._agent.cancel(run.raw_session_id)
except WorkerCancellationError as caught:
    failure = (caught.code, caught.outcome_known)
except asyncio.CancelledError as error:
    caller_cancelled = error if caller_cancellation_pending() else None
    if caller_cancelled is None:
        failure = ("cancel_timeout", False)
except BaseException:
    failure = ("transport_disconnected", False)
cleanup_pending = await self._cancel_prompt(run)
if caller_cancelled is not None:
    self._close_cancellation(run, None)
    raise caller_cancelled
if cleanup_pending:
    failure = ("cancel_timeout", False)
if failure is not None:
    error = WorkerCancellationError(
        code=failure[0], outcome_known=failure[1]
    )
    self._fail_cancellation(run, error)
    raise error from None
```

`_fail_cancellation()` closes the event stream exactly once, stores the typed
error, emits no `cancelled` event, and never embeds the original exception or
raw session ID. The successful path still cancels the local prompt task and
emits one terminal `cancelled` Worker result with only the validated reason.

- [ ] **Step 6: Run GREEN and all existing ACP adapter regression**

Run:

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_acp_worker_connection.py \
  tests/product_kernel/test_acp_worker_failures.py \
  tests/product_kernel/test_acp_worker_contract.py \
  tests/product_kernel/test_acp_transport.py \
  tests/product_kernel/test_product_exit_real_acp_cancellation.py \
  tests/product_kernel/test_real_adapter_preflight_contract.py -q
```

Expected: all pass; no real adapter process or provider is started.

- [ ] **Step 7: Enforce line, content, and diff gates**

Run:

```bash
conda run -n agentdeck python -c \
  'from pathlib import Path; files=[Path(p) for p in ("src/agentdeck/ports/worker.py","src/agentdeck/adapters/acp_worker_connection.py","src/agentdeck/adapters/acp.py","tests/product_kernel/test_acp_worker_connection.py","tests/product_kernel/test_acp_worker_failures.py")]; over={str(p):len(p.read_text().splitlines()) for p in files if len(p.read_text().splitlines())>500}; assert not over, over'
git diff --check
```

Search the changed tests and implementation to confirm no credential-shaped
fixture text appears in a persisted/result assertion. `acp.py` is already near
the line limit, so replace its old cancellation branch rather than adding a
parallel path.

- [ ] **Step 8: Update HISTORY and commit**

Record the two-phase success definition, content-free failures, RED/GREEN
counts, and zero-real-process boundary, then run:

```bash
git add HISTORY.md src/agentdeck/ports/worker.py \
  src/agentdeck/adapters/acp_worker_connection.py \
  src/agentdeck/adapters/acp.py \
  tests/product_kernel/test_acp_worker_connection.py \
  tests/product_kernel/test_acp_worker_failures.py
git commit -m "feat: close bounded acp project cancellation"
```

---

### Task 15B.3: Resume execution from the committed Handoff prefix

**Commit:** `feat: resume execution from committed handoff`

**Files:**

- Create: `src/agentdeck/application/execution_runtime.py`
- Create: `src/agentdeck/application/project_lifecycle_service.py`
- Modify: `src/agentdeck/application/execution_service.py`
- Create: `tests/product_kernel/test_execution_runtime.py`
- Create: `tests/product_kernel/test_project_lifecycle_service.py`
- Modify: `tests/product_kernel/test_execution_coordinator.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED tests for exact live binding and dispatch exclusion**

Create `test_execution_runtime.py` with async tests using two distinct event
loops and exact `WorkerHandle` values:

```python
@pytest.mark.asyncio
async def test_exact_runtime_binding_resolves_every_shared_lineage_field():
    runtime = ForegroundExecutionRuntime()
    binding = binding_for("att_1", "tsk_1", "agt_1", "ses_acp_1")
    runtime.bind(binding)
    assert runtime.resolve_exact(exit_snapshot(binding)) is binding
    runtime.release(binding.attempt_id, binding.worker_handle)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["attempt_id", "task_id", "agent_instance_id", "acp_session_id",
     "worker_handle", "worker"],
)
async def test_runtime_rejects_binding_drift_without_worker_io(field):
    runtime = ForegroundExecutionRuntime()
    runtime.bind(binding_for("att_1", "tsk_1", "agt_1", "ses_acp_1"))
    with pytest.raises(ExecutionBindingError):
        runtime.bind(drifted_binding(field))
    assert all(worker.calls == [] for worker in all_workers())
```

Also assert cross-loop resolve/release, wrong release handle, duplicate active
Attempt, reused Worker, and reused full handle are rejected. Exact release
replay for the same already-released pair succeeds without mutation; a
different pair fails.

Create `test_project_lifecycle_service.py` with:

```python
@pytest.mark.asyncio
async def test_dispatch_lease_requires_running_and_null_pending_exit(harness):
    harness.set_session(state="running", pending_exit=None)
    async with harness.lifecycle.dispatch_lease():
        harness.lifecycle.require_dispatchable()
    harness.set_pending_exit()
    async with harness.lifecycle.dispatch_lease():
        with pytest.raises(ProjectDispatchBlocked):
            harness.lifecycle.require_dispatchable()


@pytest.mark.asyncio
async def test_stop_lease_and_dispatch_lease_are_mutually_exclusive(harness):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def dispatch():
        async with harness.lifecycle.dispatch_lease():
            entered.set()
            await release.wait()

    task = asyncio.create_task(dispatch())
    await entered.wait()
    stop_acquired = asyncio.Event()

    async def acquire_stop():
        async with harness.lifecycle.stop_lease():
            stop_acquired.set()

    stop = asyncio.create_task(acquire_stop())
    await asyncio.sleep(0)
    assert stop.done() is False
    assert stop_acquired.is_set() is False
    release.set()
    await task
    await stop
    assert stop_acquired.is_set() is True
```

Add exact resume command tests: only paused/null-pending accepts; transaction
re-reads the same `ExecutionResumeSnapshot.content_hash`; it writes session
`running`, one `project_resumed` event, and a closed result; replay returns the
same result and never creates a Worker or Attempt. Hash drift, outcome unknown,
completed Mission, wrong session state, or pending exit yields zero writes.

- [ ] **Step 2: Write RED execution tests for closed-prefix skipping**

Extend the existing `Harness` constructor in
`test_execution_coordinator.py` to inject one runtime and lifecycle; keep the
file at or below 500 lines by moving all new behavior tests to
`test_execution_runtime.py` and `test_project_lifecycle_service.py`.

Add an integration harness in `test_project_lifecycle_service.py`:

```python
@pytest.mark.asyncio
async def test_resume_skips_closed_stages_and_retries_interrupted_stage(harness):
    plan = harness.resume_plan(
        closed=("implementation",), interrupted=("review", 2)
    )
    result = await harness.execution.run_confirmed_mission(
        session_id="ses_1",
        confirmed=plan.confirmed,
        draft=plan.draft,
        permission_scope=harness.permission_scope,
        resume_plan=plan,
    )
    assert harness.worker_starts == [
        ("review", 3), ("revision", 1), ("acceptance", 1)
    ]
    assert result.attempts[0].attempt_id == "att_implementation_1"
    assert result.handoffs[0].handoff_id == "hnd_implementation_1"


@pytest.mark.asyncio
async def test_persisted_exit_request_blocks_next_worker_dispatch(harness):
    harness.worker_after_terminal = harness.persist_exit_request
    result = await harness.run()
    assert harness.worker_starts == [("implementation", 1)]
    assert result.diagnostic.code == "project_dispatch_paused"
```

Also test snapshot drift starts zero Workers, closed review reconstructs the
authoritative Revision Task, and resume never repeats implementation/review
side effects.

- [ ] **Step 3: Run RED and record the missing boundaries**

Run:

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_execution_runtime.py \
  tests/product_kernel/test_project_lifecycle_service.py \
  tests/product_kernel/test_execution_coordinator.py -q
```

Expected: collection fails for missing runtime/lifecycle modules; after import
skeletons, `ExecutionService.__init__` rejects `runtime`/`lifecycle` and
`run_confirmed_mission()` rejects `resume_plan`.

- [ ] **Step 4: Implement the exact same-loop runtime**

Create:

```python
@dataclass(frozen=True)
class ActiveExecutionBinding:
    attempt_id: str
    task_id: str
    agent_instance_id: str
    acp_session_id: str
    worker_handle: WorkerHandle
    worker: Worker


class ExecutionBindingError(RuntimeError):
    pass


class ForegroundExecutionRuntime:
    def __init__(self) -> None:
        self._binding: ActiveExecutionBinding | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._released: tuple[str, WorkerHandle] | None = None
        self._used_worker_ids: set[int] = set()
        self._used_handles: list[WorkerHandle] = []

    def is_empty(self) -> bool:
        return (
            self._binding is None
            and self._loop is None
            and self._released is None
            and not self._used_worker_ids
            and not self._used_handles
        )

    def bind(self, binding: ActiveExecutionBinding) -> None:
        loop = asyncio.get_running_loop()
        validate_binding(binding)
        if self._binding is not None:
            raise ExecutionBindingError("execution binding is not available")
        if self._loop is not None and self._loop is not loop:
            raise ExecutionBindingError("execution loop identity drifted")
        if (
            id(binding.worker) in self._used_worker_ids
            or binding.worker_handle in self._used_handles
        ):
            raise ExecutionBindingError("execution binding identity was reused")
        if self._released is not None:
            if self._released[0] == binding.attempt_id:
                raise ExecutionBindingError("released attempt cannot be rebound")
            self._released = None
        self._loop = loop
        self._used_worker_ids.add(id(binding.worker))
        self._used_handles.append(binding.worker_handle)
        self._binding = binding

    def resolve_exact(
        self, snapshot: ExitAttemptSnapshot
    ) -> ActiveExecutionBinding:
        self._require_loop()
        binding = self._binding
        if binding is None or not binding_matches_snapshot(binding, snapshot):
            raise ExecutionBindingError("exact execution binding is unavailable")
        return binding

    def release(self, attempt_id: str, worker_handle: WorkerHandle) -> None:
        self._require_loop()
        pair = (attempt_id, worker_handle)
        if self._binding is None:
            if self._released == pair:
                return
            raise ExecutionBindingError("execution release lineage drifted")
        if pair != (self._binding.attempt_id, self._binding.worker_handle):
            raise ExecutionBindingError("execution release lineage drifted")
        self._binding = None
        self._released = pair
```

`validate_binding()` requires exact typed IDs, `worker_handle` type and full
Attempt/Task/Agent/session/transport match, a conforming Worker, and one Worker
object per binding. Starting the next distinct Attempt clears only the exact
previous released marker; stale release replay cannot affect the new binding.
The runtime also stores bounded in-memory `used_worker_ids: set[int]` and
`used_handles: list[WorkerHandle]` for the current Mission. `bind()` rejects a
Worker object identity or equal full handle already used by an earlier
Attempt; a distinct binding records both before it becomes visible. Once a new
binding exists, replaying the prior release fails against the current pair.

- [ ] **Step 5: Implement the project lifecycle gate and resume transaction**

Create:

```python
@dataclass(frozen=True)
class ProjectLifecycleResult:
    mode: str
    session_id: str
    should_start: bool
    snapshot_hash: str | None = None
    diagnostic: Diagnostic | None = None


class ProjectDispatchBlocked(RuntimeError):
    pass


class ProjectLifecycleService:
    def __init__(self, *, store: Store, clock: Clock, session_id: str) -> None:
        self._store = store
        self._clock = clock
        self._session_id = _session_identity(session_id)
        self._barrier = asyncio.Lock()

    @asynccontextmanager
    async def dispatch_lease(self):
        async with self._barrier:
            yield

    @asynccontextmanager
    async def stop_lease(self):
        async with self._barrier:
            yield

    def require_dispatchable(self) -> None:
        session = self._store.load_aggregate(
            "product_sessions", self._session_id
        )
        if not exact_running_session_with_null_exit(session):
            raise ProjectDispatchBlocked("project dispatch is paused")

    def pause_between_stages(self) -> ProjectLifecycleResult:
        authority = self._load_between_stage_authority()
        result = self._store.execute_once(
            f"project:pause:{self._session_id}:{authority.content_hash[:16]}",
            "pause_project_between_stages",
            lambda transaction: self._persist_exact_between_stage_pause(
                transaction, authority
            ),
        )
        return lifecycle_result_from_closed_pause(result, authority)

    async def resume(
        self, snapshot: ExecutionResumeSnapshot
    ) -> ProjectLifecycleResult:
        async with self._barrier:
            validate_snapshot_for_session(snapshot, self._session_id)
            command_id = (
                f"project:resume:{self._session_id}:"
                f"{snapshot.content_hash[:16]}"
            )
            replay = self._store.lookup_command(command_id, "resume_project")
            if replay is not None:
                return lifecycle_result_from_closed_resume(replay, snapshot)
            result = self._store.execute_once(
                command_id,
                "resume_project",
                lambda transaction: persist_exact_resume(
                    transaction, snapshot, self._clock.now()
                ),
            )
            return lifecycle_result_from_closed_resume(result, snapshot)
```

The transaction calls the same Store resume projection against its live
connection before any write, compares the full hash, saves the exact session
with state `running` and null pending exit, appends one `project_resumed`, and
returns exactly `{mode, session_id, snapshot_hash, should_start}`. Add a
transaction-level projection method rather than opening a second connection;
do not perform SQL outside the Store adapter.

`pause_between_stages()` is invoked only under `stop_lease()`. Its bounded
authority hash includes the exact Session state/updated_at/pending group and
the exact session-scoped active Attempt set, so a later resume/pause cycle has
a distinct command identity. Its transaction requires either an exact
`running` Session with zero session-scoped active Attempts and either a null
pending group or one exact terminal-bound stale request, or a non-executing
Session. The first case clears any exact stale group, writes `paused` plus one
`project_paused` event and returns
`mode="project_paused"`; the second is a read-only
`mode="project_not_executing"`. Ambiguous, partial, or changed authority fails
closed. Exact replay is valid only for the same authority hash and must never
hide a newly active Attempt.

- [ ] **Step 6: Add thin runtime/gate/resume-plan calls to ExecutionService**

Change the constructor and method signature exactly:

```python
def __init__(
    self, *, store: Store, clock: Clock, approval_service: ApprovalService,
    worker_factory: Callable[[TaskDefinition], Worker],
    runtime: ForegroundExecutionRuntime,
    lifecycle: ProjectLifecycleService,
) -> None:


async def run_confirmed_mission(
    self, *, session_id: str, confirmed: ConfirmedMissionVersion,
    draft: MissionDraft, permission_scope: PermissionScope,
    resume_plan: ExecutionResumePlan | None = None,
) -> ExecutionResult:
```

Use a pure `execution_resume.initial_execution_state()` helper to initialize
prior Attempts/Evidence/Handoffs/revision authority and the remaining Task
suffix. For each Task:

```python
async with self._lifecycle.dispatch_lease():
    self._lifecycle.require_dispatchable()
    persist_started_attempt()
    self._lifecycle.require_dispatchable()
    worker = self._worker_factory(task)
    handle = await worker.start_task(request)
    validate_full_handle(handle, request)
    attempt = persist_acp_session_binding(handle.session_id)
    self._runtime.bind(ActiveExecutionBinding(
        attempt.attempt_id, task.task_id, task.agent_instance_id,
        handle.session_id, handle, worker,
    ))
```

Release the dispatch lease only after the live binding exists. Release the
runtime only after the terminal Attempt and required Handoff/Evidence bundle
are durable. Before the next stage, `await asyncio.sleep(0)` and reacquire the
dispatch lease; this lets a persisted stop intent win before Worker creation.

Attempt ordinal and retry count are separate. A project-interrupted Attempt
increases ordinal but does not consume a failure retry. Use the snapshot's
`next_attempt_ordinal` for the first resumed attempt and count only prior
failed retryable attempts against the frozen Mission retry budget.

Because `execution_service.py` is already 500 lines, replace existing local
initialization/selection blocks with calls into `execution_resume.py`; do not
add a second implementation or raise the limit.

- [ ] **Step 7: Run GREEN and the complete R4 execution surface**

Run:

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_execution_runtime.py \
  tests/product_kernel/test_project_lifecycle_service.py \
  tests/product_kernel/test_execution_coordinator.py \
  tests/product_kernel/test_execution_budgets.py \
  tests/product_kernel/test_execution_command_authority.py \
  tests/product_kernel/test_review_revision_semantics.py \
  tests/product_kernel/test_sqlite_execution.py -q
```

Expected: all pass; existing fresh four-stage behavior is byte-for-byte
unchanged when `resume_plan is None`.

- [ ] **Step 8: Enforce line and architecture gates**

Run the line gate over every file in this commit, `git diff --check`, and the
existing Product Kernel architecture/forbidden-import suite. Confirm
`execution_service.py <= 500` and `test_execution_coordinator.py <= 500`.

- [ ] **Step 9: Update HISTORY and commit**

Record exact live binding, dispatch lease, interrupted ordinal semantics, and
RED/GREEN counts, then run:

```bash
git add HISTORY.md \
  src/agentdeck/application/execution_runtime.py \
  src/agentdeck/application/project_lifecycle_service.py \
  src/agentdeck/application/execution_service.py \
  tests/product_kernel/test_execution_runtime.py \
  tests/product_kernel/test_project_lifecycle_service.py \
  tests/product_kernel/test_execution_coordinator.py
git commit -m "feat: resume execution from committed handoff"
```

---

### Task 15B.4: Make exit one project-pause transaction

**Corrective commit:** `fix: close task15b exit cancellation gap`

The prior `feat: make exit a project pause transaction` commit is retained as
history and must not be amended. First commit this corrected spec/plan/HISTORY
boundary as `docs: close task15b exit cancellation gap`; then run every RED
before changing production code.

**Files:**

- Create: `src/agentdeck/ports/exit_authority.py`
- Modify: `src/agentdeck/ports/store.py`
- Create: `src/agentdeck/adapters/sqlite_exit_authority.py`
- Modify: `src/agentdeck/adapters/sqlite.py`
- Create: `src/agentdeck/application/async_exit_coordinator.py`
- Create: `src/agentdeck/application/exit_cancellation.py`
- Modify: `src/agentdeck/application/execution_runtime.py`
- Modify: `src/agentdeck/application/exit_records.py`
- Modify: `src/agentdeck/application/exit_service.py`
- Modify: `src/agentdeck/application/project_lifecycle_service.py`
- Create: `tests/product_kernel/test_sqlite_exit_authority.py`
- Create: `tests/product_kernel/test_product_exit_acp_integration.py`
- Create: `tests/product_kernel/test_product_exit_terminal_race.py`
- Create: `tests/product_kernel/test_product_exit_replay_failures.py`
- Create: `tests/product_kernel/test_exit_decline_sessions.py`
- Modify: `tests/product_kernel/test_exit_service.py`
- Modify: `tests/product_kernel/test_product_reentry.py`
- Modify: `tests/product_kernel/test_execution_runtime.py`
- Modify: `tests/product_kernel/test_execution_coordinator.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Freeze the closed exit-result vocabulary with RED tests**

Add result-shape tests in `test_exit_service.py`. Active confirmation results
must contain exactly these fields:

```python
ACTIVE_EXIT_RESULT_FIELDS = {
    "attempt_hash", "attempt_id", "diagnostic_code", "mode",
    "outcome_known", "request_id", "should_exit",
}


def assert_closed_active_result(result, *, mode, code, should_exit):
    assert set(result) == ACTIVE_EXIT_RESULT_FIELDS
    assert result["mode"] == mode
    assert result["diagnostic_code"] == code
    assert result["should_exit"] is should_exit
    assert type(result["outcome_known"]) is bool
```

The successful active result is `mode="project_paused"`,
`diagnostic_code=None`, `outcome_known=True`, and `should_exit=True`.
Cancellation or post-cancel authority failure uses `mode="diagnostic"`, an
allowlisted content-free code, and `should_exit=False`. A between-stage pause
uses the same closed shape with both Attempt fields null. No result may contain
exception text, ACP frames, model output, prompts, paths, environment values,
or credentials.

Update `exit_records.py` so its diagnostic allowlist contains exactly the old
authority codes plus:

```python
{
    "cancel_rejected", "cancel_timeout", "transport_disconnected",
    "exit_binding_drift", "exit_authority_changed_after_cancel",
    "project_dispatch_paused", "exit_persistence_pending",
    "exit_runtime_convergence_failed",
}
```

Keep `ExitResult` as the presenter-facing value, but add pure converters that
validate a completed command result before reconstructing it. A malformed
stored result raises `ValueError`; it never degrades into a fresh cancel.

- [ ] **Step 2: Write the real in-process RED integration matrix**

Create `test_product_exit_acp_integration.py` with one in-process conforming
Worker and a SQLite-backed harness. It exposes only exact typed facts and call
counts. Add these async tests:

```python
@pytest.mark.asyncio
async def test_confirm_cancels_exact_worker_once_and_atomically_pauses_project(runtime):
    request = runtime.request_exit_for_running_attempt()
    first = await runtime.coordinator.confirm(
        request.request_id, request.attempt_hash
    )
    second = await runtime.coordinator.confirm(
        request.request_id, request.attempt_hash
    )
    assert first == second
    assert runtime.worker.cancel_calls == [
        (runtime.exact_worker_handle, "product_exit_confirmed")
    ]
    assert runtime.attempt_state() == "interrupted"
    assert runtime.session_state() == "paused"
    assert runtime.pending_exit_fields() == (None,) * 5
    assert runtime.event_kinds()[-3:] == (
        "attempt_interrupted", "project_paused", "exit_confirmed",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("worker_code", "known"),
    [("cancel_rejected", True), ("cancel_timeout", False),
     ("transport_disconnected", False)],
)
async def test_cancel_failure_closes_replay_without_false_interruption(
    runtime, worker_code, known
):
    runtime.worker.cancel_error(worker_code, outcome_known=known)
    request = runtime.request_exit_for_running_attempt()
    first = await runtime.coordinator.confirm(request.request_id, request.attempt_hash)
    second = await runtime.coordinator.confirm(request.request_id, request.attempt_hash)
    assert first == second
    assert runtime.worker.cancel_count == 1
    assert runtime.attempt_state() == "running"
    assert runtime.session_state() == "running"
    assert runtime.pending_exit_fields()[0] == request.request_id
```

Also prove:

- Worker/full-handle/ACP-session/event-loop drift performs zero cancel I/O and
  zero writes;
- invalid or cross-session coordinator identity fails before Worker I/O and
  before any Store write;
- durable authority drift before cancel performs zero cancel I/O and zero
  writes;
- durable authority drift after successful cancel stores one closed
  `exit_authority_changed_after_cancel` replay result, does not interrupt the
  Attempt, preserves the pending request, and never cancels twice;
- a Worker terminal commit winning the race cannot be overwritten by a false
  interruption;
- after that terminal-win result, the next awaited `/exit` clears only the
  exact now-terminal stale request and pauses between stages with zero second
  cancel;
- an exit requested between stages pauses the session and clears the pending
  group with zero Worker cancellation;
- the pending exit group blocks the next Worker even while confirmation is
  unresolved;
- decline clears only the exact pending request and reopens dispatch without
  touching the Worker;
- after cancellation has claimed a matching fence or quarantined owner,
  decline rejects and cannot reopen dispatch;
- callback, event, and commit failure after cancellation return only
  `exit_persistence_pending/outcome_known=false`, retain the fence, and exact
  retry performs zero additional Worker I/O;
- an unexpected ordinary Worker exception maps to the fixed content-free
  `transport_disconnected/outcome_known=false`; caller `asyncio.CancelledError`
  closes the fence to the same unknown outcome before propagating;
- a success replay settles only the exact fence or accepts a pristine
  fresh-process runtime; unrelated active/reserved/quarantined ownership
  returns `exit_runtime_convergence_failed` and never claims exit;
- first result and replay are equal under an advancing Clock because
  `Diagnostic.occurred_at` is reconstructed from the original request time;
- confirmation replay is scoped by ProductSession and validates the original
  request command's request ID, Attempt hash, Attempt ID, and requested time;
- setup/drafting/awaiting-confirmation `/exit` closes the interface without a
  synthetic `paused` transition.

Before the coordinator tests, add the SQLite authority RED in
`test_sqlite_exit_authority.py`. Define one immutable, bounded,
content-free `ActiveExitAuthority` value in `ports/exit_authority.py`, and add
the same read operation to `Store` and `StoreTransaction`:

```python
load_active_exit_authority(session_id: str) -> ActiveExitAuthority
```

The projection is the single complete CAS source for active exit. It contains
only exact ProductSession state and pending five-field request authority;
Attempt identity, immutable lineage, ordinal, state, effect flag and durable
fingerprint; Task identity, planned Agent identity, Mission identity and
version; Mission-to-ProductSession linkage and state; Agent Instance identity,
ProductSession linkage, ACP transport/session and state; and the full derived
typed `WorkerHandle` lineage. It contains no provider/model, prompt, path,
environment, credential, frame, terminal text, or Worker prose.

Both public Store and command-transaction reads must delegate to one helper in
`sqlite_exit_authority.py`; the transaction method must use its live command
connection and must not call the public/read connection. RED must prove public
and transaction-local projection equality, transaction-local visibility of
drift, row drift changes the projection hash, missing/partial/duplicate lineage
fails closed, malformed or oversized facts fail closed, and no cross-connection
read is possible while a command transaction owns the writer. Application code
must consume this typed projection and must not scatter SQL or infer omitted
lineage. `ActiveExitAuthority` requires mission versions in
`1..9223372036854775807`; Attempt identity rejects every whitespace/control
character, not only an empty value.

- [ ] **Step 3: Run RED and record why it fails**

Run:

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_sqlite_exit_authority.py \
  tests/product_kernel/test_exit_service.py \
  tests/product_kernel/test_product_exit_acp_integration.py \
  tests/product_kernel/test_product_exit_terminal_race.py \
  tests/product_kernel/test_product_exit_replay_failures.py \
  tests/product_kernel/test_exit_decline_sessions.py \
  tests/product_kernel/test_product_reentry.py -q
```

Expected for the corrective RED: assertions fail because the current
coordinator re-enters Worker cancellation after a persistence exception,
replay is not ProductSession-scoped, diagnostic time is resampled, runtime
history is confused with live ownership, decline can clear a cancellation in
progress, and the existing terminal-win fixture bypasses production terminal
bundle persistence/release.

- [ ] **Step 4: Split durable request authority from async execution**

Keep `ExitService` synchronous and limited to four public methods:
`request_exit`, `decline`, `confirm`, and `input_closed`. Its `confirm()` now
validates only identity and exposes a typed pending decision projection for
the coordinator; it must never call a Worker. Preserve byte-for-byte request
and decline behavior.

Create the coordinator contract:

```python
class AsyncExitCoordinator:
    def __init__(
        self, *, exit_service: ExitService, store: Store, clock: Clock,
        runtime: ForegroundExecutionRuntime,
        lifecycle: ProjectLifecycleService,
        session_id: str,
    ) -> None:
        for dependency, methods in (
            (exit_service, ("request_exit", "decline", "confirm", "input_closed")),
            (store, (
                "lookup_command", "execute_once", "load_active_exit_authority",
            )),
            (clock, ("now",)),
            (runtime, (
                "has_live_owner", "claim_exit_cancellation",
                "close_exit_cancellation", "settle_exit_cancellation",
            )),
            (lifecycle, ("stop_lease", "pause_between_stages")),
        ):
            if any(not callable(getattr(dependency, name, None)) for name in methods):
                raise TypeError("async exit dependency is invalid")
        self._exit_service = exit_service
        self._store = store
        self._clock = clock
        self._runtime = runtime
        self._lifecycle = lifecycle
        self._session_id = _session_identity(session_id)

    async def request_exit(self) -> ExitResult:
        async with self._lifecycle.stop_lease():
            result = self._exit_service.request_exit()
            if result.mode != "exit_ready":
                return result
            if self._runtime.has_live_owner():
                return content_free_dispatch_paused(result)
            paused = self._lifecycle.pause_between_stages()
            return exit_result_from_lifecycle(paused, default=result)

    async def decline(self, request_id: str, attempt_hash: str) -> ExitResult:
        async with self._lifecycle.stop_lease():
            return self._exit_service.decline(request_id, attempt_hash)

    async def confirm(self, request_id: str, attempt_hash: str) -> ExitResult:
        async with self._lifecycle.stop_lease():
            return await self._confirm_locked(request_id, attempt_hash)

    async def input_closed(self) -> ExitResult:
        return await self.request_exit()
```

`_confirm_locked()` is specified completely in Steps 5 and 6. The coordinator
also rejects `decline()` whenever the matching request owns a cancelling,
fenced-pending, or cancellation-quarantined runtime owner.
Constructor validation accepts only the listed Port/service methods and stores
no terminal/process text. The explicit, strictly validated `session_id` is the
only ProductSession identity used for active-exit projection reads; the
coordinator must not hard-code it, infer it from a pending request or Worker
handle, or add a fifth public `ExitService` method. Invalid identity fails at
composition, while a valid cross-session identity closes before Worker I/O or
Store writes. `ExitService.confirm()` returns the exact validated
pending request as `mode="exit_confirmation_ready"`; it never calls a Worker
and ProductShell never presents that internal mode directly.

- [ ] **Step 5: Implement exact active cancellation outside SQLite**

Inside `confirm()` acquire `lifecycle.stop_lease()` and perform this order:

```python
replay = load_and_validate_session_scoped_completed_confirm(
    session_id, request_id, attempt_hash
)
if replay is not None:
    return converge_runtime_before_replay(replay)
decision = self._exit_service.confirm(request_id, attempt_hash)
if decision.mode != "exit_confirmation_ready":
    return decision
pending = decision.request
if pending is None:
    raise ValueError("exit confirmation authority is missing")
authority = store.load_active_exit_authority(session_id)
lease = runtime.claim_exit_cancellation(
    cancellation_key(pending, authority), authority.worker_handle
)
try:
    if lease.needs_worker_io:
        await lease.worker.cancel_task(
            lease.worker_handle, reason="product_exit_confirmed"
        )
except WorkerCancellationError as error:
    runtime.close_exit_cancellation(lease, cancellation_failure(error))
except asyncio.CancelledError:
    runtime.close_exit_cancellation(lease, unknown_transport_failure())
    raise
except Exception:
    runtime.close_exit_cancellation(lease, unknown_transport_failure())
else:
    if lease.needs_worker_io:
        runtime.close_exit_cancellation(lease, cancellation_success())
return persist_then_settle_exact_cancellation(pending, lease)
```

Create `application/exit_cancellation.py` with bounded immutable
`ExitCancellationKey` and `ExitCancellationOutcome` values. Runtime creates an
opaque, non-copyable `ExitCancellationLease`; `close` and `settle` require the
same Python object identity plus exact key and handle. At most one foreground
fence exists. `has_live_owner()` is true for active, reserved, quarantined,
cancelling, and fenced-pending state, but false for bounded used-object history
and released markers. The lease rejects both `copy.copy()` and
`copy.deepcopy()`; a newly constructed lookalike is never accepted.

The single external `await cancel_task()` happens before `execute_once`; never
hold a SQLite transaction open across it. If callback, event append, or commit
raises, catch it at the coordinator boundary and return only non-durable
`exit_persistence_pending/outcome_known=false/should_exit=false`; retain the
closed fence and never include exception content. Same-process retry consumes
the fence outcome and repeats only persistence. The durable command ID is
`exit:confirm:<session-id>:<request-id>` for success and failure.

- [ ] **Step 6: Implement the atomic project pause commit**

Before Worker I/O, the coordinator loads one typed `ActiveExitAuthority` and
requires its exact pending request, Attempt snapshot, ACP session, and derived
full Worker handle to match the runtime binding. The success callback then
calls `transaction.load_active_exit_authority(session_id)` on the live command
connection and compares the complete projection hash with that pre-cancel
value. This one projection is the complete CAS authority for ProductSession,
pending five-field group, Attempt, Task, Mission, Agent Instance, ACP session,
and full Worker-handle lineage; the Application layer must not reconstruct it
from scattered reads. On exact match it performs only:

```python
transaction.save_attempt(interrupted_attempt_snapshot)
transaction.save_session(paused_session_with_null_pending_exit)
transaction.append_event(attempt_interrupted_event)
transaction.append_event(project_paused_event)
transaction.append_event(exit_confirmed_event)
return closed_project_paused_result
```

If terminal persistence won the race or any fact changed, record only a
closed `exit_authority_changed_after_cancel` command result. Do not mutate the
Attempt, Session, Handoff, Evidence, or next Task. Exact command replay is
resolved before pending-state inspection, so a cleared request never causes a
second cancellation.

After a durable success, settle the exact lease to idle before returning
`should_exit=true`. Settlement failure returns only
`exit_runtime_convergence_failed/should_exit=false` and retains the fence for
zero-I/O replay. A durable success may replay in a fresh process only when the
runtime is pristine; active, reserved, quarantined, or foreign fenced ownership
cannot be treated as success. After a durable cancellation failure, settle the
exact fence into quarantine and retain the pending request. `/decline` must
reject it. Task 15B.5 owns later recovery; Task 15B.4 never invents a live
Worker restoration path.

Command replay first reconstructs the original completed exit request and
validates its canonical request ID, Attempt hash, Attempt ID, requested time,
and current ProductSession lineage. Stored active results must match that
request exactly. `exit_result_from_command()` derives
`Diagnostic.occurred_at` from the original request's `requested_at`, never from
the current Clock, so advancing-clock replay is byte-stable.

For no-live-Attempt authority, awaited `request_exit()` already owns the stop
lease and first requires `runtime.has_live_owner() is False`.
`ProjectLifecycleService.pause_between_stages()` transactionally
revalidates an exact `running` ProductSession, empty session-scoped active
Attempt set, and either a null pending group or one exact stale pending request
whose bound Attempt is now terminal. It clears that group, pauses the
ProductSession, and appends `project_paused` plus `exit_confirmed` with zero
runtime lookup and zero Worker I/O. The coordinator's pure
`exit_result_from_lifecycle()` converts that closed
result to `ExitResult`. If the Session is setup, ready, drafting,
awaiting-confirmation, completed, failed, or cancelled, the lifecycle result is
`project_not_executing`; the converter preserves the original plain
`exit_ready` and state. No synthetic pending group or confirmation is created
between stages.

The terminal-win integration test must run the production execution completion
path: one real atomic command persists the terminal Attempt plus required
Evidence and Handoff, then production runtime release runs. The test may use a
barrier-controlled conforming Worker but may not manually update SQLite or call
`runtime.release()` to manufacture the race. It asserts every Evidence,
Handoff, canonical fact, and content hash is unchanged, the pending exit blocks
the next Task, and a later between-stage `/exit` sends zero cancellation I/O.

Final re-review also requires session-scoped decline commands validated against
the original canonical request, stable content-free lookup failure replay from
an exact cancellation fence, callback/pre-COMMIT/post-COMMIT persistence fault
coverage, and cancellation of the real caller Task while `cancel_task` is
blocked. These regressions live in the two focused recovery/session files above
instead of pushing the primary integration file over the 500-line gate.

- [ ] **Step 7: Run GREEN, race tests, and quality gates**

Run:

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_sqlite_exit_authority.py \
  tests/product_kernel/test_exit_service.py \
  tests/product_kernel/test_product_exit_acp_integration.py \
  tests/product_kernel/test_product_exit_terminal_race.py \
  tests/product_kernel/test_product_exit_replay_failures.py \
  tests/product_kernel/test_exit_decline_sessions.py \
  tests/product_kernel/test_product_reentry.py \
  tests/product_kernel/test_project_lifecycle_service.py \
  tests/product_kernel/test_execution_runtime.py -q
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_architecture.py \
  tests/product_kernel/test_sqlite_transactions.py -q
git diff --check
```

Expected: all pass. Run the 500-line gate for every changed source/test file;
if `exit_service.py` would exceed 500 lines, move pure result parsing into
`exit_records.py` rather than weakening the gate.

- [ ] **Step 8: Update HISTORY and commit**

Record RED/GREEN counts, the stop-lease ordering, exact replay boundary,
between-stage zero-I/O pause, and the no-false-interruption rule. Then run:

```bash
git add HISTORY.md \
  src/agentdeck/application/exit_cancellation.py \
  src/agentdeck/application/execution_runtime.py \
  src/agentdeck/ports/exit_authority.py \
  src/agentdeck/ports/store.py \
  src/agentdeck/adapters/sqlite_exit_authority.py \
  src/agentdeck/adapters/sqlite.py \
  src/agentdeck/application/async_exit_coordinator.py \
  src/agentdeck/application/exit_records.py \
  src/agentdeck/application/exit_service.py \
  src/agentdeck/application/project_lifecycle_service.py \
  tests/product_kernel/test_sqlite_exit_authority.py \
  tests/product_kernel/test_product_exit_acp_integration.py \
  tests/product_kernel/test_product_exit_terminal_race.py \
  tests/product_kernel/test_exit_service.py \
  tests/product_kernel/test_product_reentry.py \
  tests/product_kernel/test_execution_runtime.py \
  tests/product_kernel/test_execution_coordinator.py
git commit -m "fix: close task15b exit cancellation gap"
```

- [ ] **Step 9: Close final replay, decline, persistence, and terminal-race review gaps**

Run the two-session decline RED, lookup-failure RED, three-boundary persistence
matrix, real caller-Task cancellation, and public execution terminal race. The
terminal test must invoke only `ExecutionService.run_confirmed_mission()`; a
runtime subclass may observe the production `release()` call, but the test may
not invoke runtime release or private execution persistence itself. Run the
complete Task 15B.4 and Task 15B.3 R4 gates, the architecture/transaction gate,
and five consecutive focused race runs. Then commit without amend:

```bash
git add HISTORY.md \
  docs/superpowers/appendices/task15b/2026-07-20-task-15b-project-pause-resume.md \
  src/agentdeck/application/async_exit_coordinator.py \
  src/agentdeck/application/exit_service.py \
  tests/product_kernel/test_exit_service.py \
  tests/product_kernel/test_product_exit_acp_integration.py \
  tests/product_kernel/test_product_exit_terminal_race.py \
  tests/product_kernel/test_product_exit_replay_failures.py \
  tests/product_kernel/test_exit_decline_sessions.py
git commit -m "fix: close task15b exit replay gaps"
```

---

### Task 15B.5: Require explicit resume after Product re-entry

**Commit:** `feat: require explicit resume after product reentry`

**Files:**

- Modify: `src/agentdeck/application/recovery_service.py`
- Modify: `src/agentdeck/product/shell.py`
- Create: `src/agentdeck/product/shell_projection.py`
- Modify: `src/agentdeck/product/bootstrap.py`
- Modify: `tests/product_kernel/test_recovery_service.py`
- Modify: `tests/product_kernel/test_sqlite_recovery_integrity.py`
- Modify: `tests/product_kernel/test_product_shell.py`
- Create: `tests/product_kernel/test_product_shell_cleanup.py`
- Modify: `tests/product_kernel/test_product_preview_flow.py`
- Modify: `tests/product_kernel/test_product_reentry.py`
- Create: `tests/product_kernel/test_project_resume_replay.py`
- Modify: `HISTORY.md`

Review closure extends this task with three mandatory fail-closed boundaries:
`execution_service=None` must emit the stable
`execution_adapter_unavailable` code before any paused-to-running resume
transaction; a committed resume followed by recovery must allocate a bounded
new resume command generation instead of accepting a stale replay while the
Session is paused; and the Store cleanup scope must begin before recovery,
projection, initial rendering, or SIGINT-handler installation. The focused
replay regression lives in `test_project_resume_replay.py` so the primary
lifecycle test remains within the unchanged 500-line gate.
Shell cleanup regressions live in `test_product_shell_cleanup.py`; an owned
Mission child cancellation must preserve `CancelledError` while a nested
cleanup `finally` still closes the Store exactly once.

- [ ] **Step 1: Write mandatory startup-recovery RED tests**

Replace the old transport `CONFIRMED -> RESUMED` expectations in
`test_recovery_service.py`. Fresh-process recovery owns an empty runtime and
must never claim ACP resume:

```python
@pytest.mark.asyncio
async def test_fresh_process_pauses_no_effect_abandoned_attempt(harness):
    harness.running_attempt(effect_observed=False)
    report = await harness.recovery.reconcile()
    assert report.interrupted == ("att_1",)
    assert report.outcome_unknown == ()
    assert harness.attempt_state("att_1") == "interrupted"
    assert harness.session_state("ses_1") == "paused"
    assert harness.transport.calls == []


@pytest.mark.asyncio
async def test_fresh_process_pauses_effect_observed_as_outcome_unknown(harness):
    harness.running_attempt(effect_observed=True)
    report = await harness.recovery.reconcile()
    assert report.outcome_unknown == ("att_1",)
    assert harness.attempt_state("att_1") == "outcome_unknown"
    assert harness.session_state("ses_1") == "paused"
    assert harness.transport.calls == []
```

Also cover a `running` ProductSession with no Attempt (crash after resume
commit), replayed recovery, a concurrent durable fingerprint change, duplicate
Attempt identity, two distinct active Attempts for the same session, partial
pending-exit authority, and rollback on event write failure. Bootstrap must
assert its newly constructed `ForegroundExecutionRuntime` is empty before
calling recovery; a non-empty runtime is a composition error, not
cross-process resume evidence. Every converged project is paused; multiple
active Attempts fail closed in one rollback, and no backend/model/role/pane/
latest fallback or Worker/Leader/ACP call is allowed.

- [ ] **Step 2: Write RED shell tests for one event loop and explicit `/resume`**

Convert ProductShell tests to async and inject exact fakes:

```python
@pytest.mark.asyncio
async def test_recovery_finishes_before_first_input_read(shell_harness):
    await shell_harness.shell.run_async()
    assert shell_harness.calls[:2] == ["recovery.reconcile", "read_line"]


@pytest.mark.asyncio
async def test_paused_reentry_starts_nothing_before_explicit_resume(shell_harness):
    shell_harness.inputs("/status", "/exit")
    await shell_harness.shell.run_async()
    assert shell_harness.leader_calls == []
    assert shell_harness.worker_starts == []
    assert shell_harness.acp_calls == []


@pytest.mark.asyncio
async def test_explicit_resume_starts_one_same_loop_mission_child(shell_harness):
    shell_harness.inputs("/resume", "/exit")
    await shell_harness.shell.run_async()
    assert shell_harness.lifecycle.resume_calls == 1
    assert shell_harness.execution.start_count == 1
    assert shell_harness.loop_ids == {id(asyncio.get_running_loop())}


@pytest.mark.asyncio
async def test_waiting_for_terminal_input_does_not_block_worker_child(shell_harness):
    shell_harness.block_next_input()
    task = asyncio.create_task(shell_harness.shell.run_async())
    await shell_harness.worker_progress.wait()
    assert shell_harness.worker_events == ["attempt_progressed"]
    shell_harness.release_input("/exit")
    await task


@pytest.mark.asyncio
async def test_sigint_callback_enters_the_same_project_exit_path(shell_harness):
    task = asyncio.create_task(shell_harness.shell.run_async())
    await shell_harness.reader_waiting.wait()
    shell_harness.raise_registered_sigint()
    await shell_harness.exit_requested.wait()
    assert shell_harness.coordinator.request_count == 1
    shell_harness.release_input("/exit")
    await task


@pytest.mark.asyncio
async def test_fresh_confirmation_uses_the_same_singleton_child_guard(shell_harness):
    shell_harness.confirm_fresh_mission_then_exit()
    await shell_harness.shell.run_async()
    assert shell_harness.execution.start_count == 1
    assert shell_harness.loop_ids == {id(asyncio.get_running_loop())}
```

Add failure cases: invalid resume projection, `outcome_unknown`, pending exit,
completed Mission, and state/hash drift all start zero Workers. A second
`/resume` while the Session is already running returns `already_running` and
observes the existing child; it does not load a paused-only snapshot or create
a second child. Test command replay directly by calling
`ProjectLifecycleService.resume()` twice with the same retained snapshot.

Add one transcript RED: after recovery and before the first prompt, the shell
renders the exact first-unclosed Task, `next_attempt_ordinal`, and preceding
Handoff identity from the same `ExecutionResumeSnapshot`; it never rescans
tables or exposes Handoff/Evidence content.

- [ ] **Step 3: Add the crash-gap RED integration cases**

In `test_product_reentry.py` and `test_sqlite_recovery_integrity.py`, prove:

1. crash after `project_resumed` commit and before Worker start -> next
   bootstrap pauses again with no Worker;
2. crash after terminal Handoff command -> snapshot advances to the next
   stage and never repeats the closed one;
3. crash with possible effects -> `outcome_unknown`, `/resume` blocked;
4. paused startup preserves committed Handoffs/Evidence byte-for-byte;
5. EOF during active work routes through the async project-exit policy rather
   than closing the Store underneath a live task.

- [ ] **Step 4: Run RED and preserve the evidence in HISTORY notes**

Run:

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_recovery_service.py \
  tests/product_kernel/test_sqlite_recovery_integrity.py \
  tests/product_kernel/test_product_shell.py \
  tests/product_kernel/test_product_shell_cleanup.py \
  tests/product_kernel/test_product_preview_flow.py \
  tests/product_kernel/test_product_reentry.py -q
```

Expected: failures show synchronous `RecoveryService.reconcile()`, synchronous
`ProductShell.run()`, missing lifecycle resume wiring, and old fake transport
resume semantics.

- [ ] **Step 5: Make RecoveryService asynchronous and conservative**

Change the public method to:

```python
async def reconcile(self) -> RecoveryReport:
    attempts = self._store.list_active_exit_attempts(self._session_id)
    if len(attempts) > 1:
        raise RecoveryError("recovery authority is ambiguous")
    for attempt in attempts:
        outcome = (
            RecoveryOutcome.OUTCOME_UNKNOWN
            if attempt.effect_observed
            else RecoveryOutcome.INTERRUPTED
        )
        persist_attempt_and_session_pause_exactly_once(attempt, outcome)
    pause_running_session_without_attempt_if_required()
    return closed_report()
```

Initialize `RecoveryService` with exactly `store`, `clock`, `session_id`, and
`recovery_run_id`; remove the transport dependency. Recovery uses only
`list_active_exit_attempts(session_id)` and exact aggregate reloads, never the
global `list_running_attempts()` fallback. The private helpers shown above are
extracted from the existing implementation. It must validate `len(attempts) <=
1` before entering any command transaction or writing any Attempt, Session,
event, or command row; two distinct active Attempts fail closed with zero
writes. Recovery persists the sole Attempt outcome, Session pause, and recovery
events in one exact command transaction. Remove
`RecoveryOutcome.RESUMED` and the `ReconnectStatus.CONFIRMED` success path.
Keep allowlisted, content-free recovery reason codes.

- [ ] **Step 6: Give ProductShell one async foreground loop**

Replace `run()` with exactly one public async entry and add a cancellable
terminal reader in the same file:

```python
class AsyncTerminalReader:
    def __init__(self, stream: TextIO, prompt_stream: TextIO) -> None:
        self._stream = stream
        self._prompt_stream = prompt_stream

    async def __call__(self, prompt: str) -> str:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        descriptor = self._stream.fileno()
        self._prompt_stream.write(prompt)
        self._prompt_stream.flush()

        def ready() -> None:
            if future.done():
                return
            try:
                line = self._stream.readline()
                if line == "":
                    future.set_exception(EOFError())
                else:
                    future.set_result(line.rstrip("\r\n"))
            except BaseException as error:
                future.set_exception(error)

        loop.add_reader(descriptor, ready)
        try:
            return await future
        finally:
            loop.remove_reader(descriptor)
```

The production shell receives this async callable; tests inject an async fake.
Then implement:

```python
async def run_async(self) -> int:
    await self._recovery_service.reconcile()
    self._render_restored_project()
    loop = asyncio.get_running_loop()
    interrupted = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, interrupted.set)
    try:
        while True:
            read_task = asyncio.create_task(self._read_line("agentdeck> "))
            signal_task = asyncio.create_task(interrupted.wait())
            done, _ = await asyncio.wait(
                (read_task, signal_task), return_when=asyncio.FIRST_COMPLETED
            )
            if signal_task in done:
                read_task.cancel()
                await _consume_cancelled(read_task)
                interrupted.clear()
                result = await self._exit_coordinator.request_exit()
                await self._present_exit(result)
                if result.should_exit:
                    return 0
                continue
            signal_task.cancel()
            await _consume_cancelled(signal_task)
            try:
                line = read_task.result()
            except EOFError:
                return await self._handle_input_closed()
            result = await self._handle_line(line)
            if result.should_exit:
                await self._finish_child_task_for_exit()
                return 0
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        await self._settle_owned_child()
        self._close()
```

`_consume_cancelled()` awaits the cancelled task and suppresses only
`asyncio.CancelledError`. The loop-level SIGINT callback replaces
`asyncio.Runner`'s default main-task cancellation while the shell is alive, so
SIGINT is normalized to the same awaited project-exit path as `/exit` and the
handler is removed before loop shutdown. Waiting for terminal input never
blocks the Mission child, Worker stream, permission bridge, or exit
coordinator, and there is no orphan executor thread.

`_handle_input_closed()` awaits `input_closed()`. If there is no active child,
it returns the closed result. If a pending active request is created, it
presents that stop intent, awaits the already-bounded Mission child until its
current stage commits or fails, then calls `input_closed()` once more; the
second call atomically clears the exact now-terminal stale request and pauses
between stages. It never auto-confirms active cancellation and never closes the
Store while the child still owns it.

`_handle_line()` remains deterministic for help/status/setup/preview. `/exit`
awaits `AsyncExitCoordinator`; `/resume` loads one
`ExecutionResumeSnapshot`, materializes one `ExecutionResumePlan`, calls
and awaits `ProjectLifecycleService.resume()`, and only after that commit calls
`_start_mission_child(confirmed, draft, permission_scope, resume_plan)` on the
same loop. Fresh exact Mission confirmation uses the same private
`_start_mission_child()` singleton guard immediately after its confirmation
commit. These are the only two child-creation paths. Keep the child task
reference until terminal persistence or safe project pause; never detach a
daemon/background task.

- [ ] **Step 7: Compose the exact shared services before reading input**

In `bootstrap.py`, build and inject, in dependency order:

```text
SQLiteStore -> SessionService -> ForegroundExecutionRuntime
-> ProjectLifecycleService -> RecoveryService
-> ExecutionService -> AsyncExitCoordinator -> ProductShell
```

Extend `build_product_shell()` with injected factories for
`runtime`, `lifecycle`, `recovery`, `approval`, `execution`, and
`exit_coordinator`, plus optional already-classified
`adapter_readiness: Mapping[str, AdapterReadiness] | None`. Construct exactly:

```python
runtime = runtime_factory()
if not runtime.is_empty():
    raise RuntimeError("fresh Product composition requires an empty runtime")
lifecycle = lifecycle_factory(
    store=store, clock=clock, session_id=service.current().session_id
)
recovery = recovery_factory(
    store=store, clock=clock, session_id=service.current().session_id,
    recovery_run_id=recovery_run_id_factory(),
)
execution = None
if adapter_readiness is not None:
    adapters = adapter_composition_factory(
        readiness=adapter_readiness, project_root=project_root, clock=clock
    )
    approval = approval_service_factory(store=store, clock=clock)
    execution = execution_service_factory(
        store=store, clock=clock, approval_service=approval,
        worker_factory=lambda task: adapters.worker(task.backend),
        runtime=runtime, lifecycle=lifecycle,
    )
exit_coordinator = exit_coordinator_factory(
    exit_service=exit_service, store=store, clock=clock,
    runtime=runtime, lifecycle=lifecycle,
)
```

Change the `read_line` dependency to an async callable. When none is injected,
bootstrap constructs `AsyncTerminalReader(sys.stdin, sys.stdout)`; tests pass
their async fake. Do not wrap `input()` in an executor.

Task 15B does not invent readiness facts: production `None` keeps confirmation
usable but returns the closed `execution_adapter_unavailable` diagnostic before
creating a child. Integration tests inject the sealed Task 26 readiness and an
in-process Worker owner. Task 35 later supplies the authorized real preflight
facts without changing this lifecycle composition.

For a fresh confirmation, ProductShell retains the exact
`MissionPreviewView.draft` immediately before calling `MissionService.confirm`.
It verifies the returned `ConfirmedMissionVersion` equals that draft's exact
preview/version/hash, derives
`PermissionScope.for_profile(draft.permission_profile)`, and calls the same
singleton `_start_mission_child(confirmed, draft, scope, resume_plan=None)` as
resume. Resume obtains all four values from `ExecutionResumePlan`. No change to
`MissionService` or new mutable draft cache is required.

All components receive the same restored session ID, Store, Clock, runtime,
and foreground loop. `build_product_shell()` remains side-effect free with
respect to Worker start; recovery begins only in `run_async()`. The only
production `asyncio.run()` in the Product Kernel is the outer entrypoint:

```python
def run_product_dev(*, diagnostic: bool = False) -> int:
    if diagnostic:
        print("AgentDeck Product Kernel development entry: ready")
        return 0
    shell = build_product_shell(project_root=str(Path.cwd()))
    return asyncio.run(shell.run_async())
```

No Application, adapter, Worker, coordinator, or shell method may call
`asyncio.run()`.

- [ ] **Step 8: Run GREEN and all focused Task 15B gates**

Run:

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_recovery_service.py \
  tests/product_kernel/test_sqlite_recovery_integrity.py \
  tests/product_kernel/test_product_shell.py \
  tests/product_kernel/test_product_shell_cleanup.py \
  tests/product_kernel/test_product_preview_flow.py \
  tests/product_kernel/test_product_reentry.py \
  tests/product_kernel/test_project_resume_replay.py \
  tests/product_kernel/test_product_exit_acp_integration.py \
  tests/product_kernel/test_project_lifecycle_service.py \
  tests/product_kernel/test_execution_runtime.py -q
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_architecture.py \
  tests/product_kernel/test_dev_entry.py -q
test -z "$(rg -n 'asyncio\.run\(' src/agentdeck/application \
  src/agentdeck/product/shell.py src/agentdeck/adapters/acp.py \
  src/agentdeck/adapters/acp_worker_connection.py || true)"
test "$(rg -n 'asyncio\.run\(' src/agentdeck/product/bootstrap.py | wc -l | tr -d ' ')" = 1
git diff --check
```

Expected: all tests pass; Task 15B Application/Shell/Worker files contain no
`asyncio.run`, and bootstrap contains exactly the outer `run_product_dev`
match. The independent Codex ACP server executable retains its already-reviewed
outer `_main()` runner and is outside this gate. Run the 500-line gate on every
modified source and test file.

- [ ] **Step 9: Update HISTORY and commit**

Record mandatory pre-input recovery, observational paused startup, explicit
same-loop resume, crash convergence, RED/GREEN counts, and the exact one-match
`asyncio.run` evidence. Then run:

```bash
git add HISTORY.md \
  src/agentdeck/application/recovery_service.py \
  src/agentdeck/product/shell.py \
  src/agentdeck/product/shell_projection.py \
  src/agentdeck/product/bootstrap.py \
  docs/superpowers/appendices/task15b/2026-07-20-task-15b-project-pause-resume.md \
  docs/superpowers/appendices/task15b/2026-07-20-task-15b-acp-cancellation-recovery-design.md \
  tests/product_kernel/test_recovery_service.py \
  tests/product_kernel/test_sqlite_recovery_integrity.py \
  tests/product_kernel/test_product_shell.py \
  tests/product_kernel/test_product_shell_cleanup.py \
  tests/product_kernel/test_product_preview_flow.py \
  tests/product_kernel/test_product_reentry.py \
  tests/product_kernel/test_project_resume_replay.py
git commit -m "feat: require explicit resume after product reentry"
```

---

## Integrated Task 15B verification and R2 exit gate

- [ ] **Step 1: Run the complete Task 15B focused suite**

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_kernel_session.py \
  tests/product_kernel/test_sqlite_execution_resume.py \
  tests/product_kernel/test_execution_resume.py \
  tests/product_kernel/test_acp_worker_connection.py \
  tests/product_kernel/test_acp_worker_failures.py \
  tests/product_kernel/test_execution_runtime.py \
  tests/product_kernel/test_project_lifecycle_service.py \
  tests/product_kernel/test_execution_coordinator.py \
  tests/product_kernel/test_execution_budgets.py \
  tests/product_kernel/test_execution_command_authority.py \
  tests/product_kernel/test_review_revision_semantics.py \
  tests/product_kernel/test_sqlite_execution.py \
  tests/product_kernel/test_exit_service.py \
  tests/product_kernel/test_product_exit_acp_integration.py \
  tests/product_kernel/test_product_exit_real_acp_cancellation.py \
  tests/product_kernel/test_product_reentry.py \
  tests/product_kernel/test_project_resume_replay.py \
  tests/product_kernel/test_product_shell_cleanup.py \
  tests/product_kernel/test_recovery_service.py \
  tests/product_kernel/test_sqlite_recovery_integrity.py \
  tests/product_kernel/test_product_shell.py \
  tests/product_kernel/test_product_preview_flow.py -q
```

Expected: all pass with no deselection or xfail introduced by this slice.

- [ ] **Step 2: Run the Product Kernel full suite**

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest tests/product_kernel -q
```

Expected: all pass.

- [ ] **Step 3: Run the legacy suite without double-running Product Kernel**

```bash
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest tests \
  --ignore=tests/product_kernel -q
```

Expected: all pass.

- [ ] **Step 4: Run compile, formatting, architecture, and line gates**

```bash
conda run -n agentdeck python -m compileall -q src tests/product_kernel
git diff --check
conda run -n agentdeck env PYTHONPATH="$PWD/src" pytest \
  tests/product_kernel/test_architecture.py -q
```

```bash
violations="$(git diff --name-only \
  2a752ccda081b7a599a79f27773348802d570029 -- '*.py' | \
  while IFS= read -r file; do
    test -f "$file" || continue
    lines="$(wc -l < "$file" | tr -d ' ')"
    test "$lines" -le 500 || printf '%s:%s\n' "$file" "$lines"
  done)"
test -z "$violations"
test -z "$(git diff 2a752ccda081b7a599a79f27773348802d570029 -- \
  src/agentdeck | rg \
  '^\+.*(ConversationSession|tmux|PTY|jsonl state|raw exception|prompt output)' \
  || true)"
```

Expected: no changed source/test file exceeds 500 lines and the Task 15B diff
contains no forbidden legacy authority in production code, nested event loop,
raw exception persistence, or credential-shaped fixture. Negative regression
tests may name a forbidden authority while proving that production does not use
it; therefore the lexical authority gate intentionally scans `src/agentdeck`
rather than test descriptions or assertion data.

- [ ] **Step 5: Perform independent two-stage review**

Dispatch a fresh spec-compliance reviewer against the revised design, this
plan, and the full five-commit diff. Fix every Critical or Important finding
in a small HISTORY-bearing review-fix commit, then repeat the review. Dispatch
a different code-quality reviewer only after spec approval; fix and re-review
until approved. Rerun Steps 1-4 after the last fix.

- [ ] **Step 6: Close R2 and advance only to Task 27**

Record the exact HEAD, focused/full/legacy counts, review approvals, line-gate
result, and clean-worktree proof in
`docs/handoff/current-development-state.md`. Mark R2 complete only if every
gate above is green. Do not begin Task 27 in the same commit; Task 27 starts as
the next separately reviewed numerical task. Do not push or merge.
