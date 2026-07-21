# P1 Durable Mission Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new SQLite-backed durable Mission kernel (domain + storage + app + a single-writer daemon loop) that runs a deterministic fake Golden Mission end to end through public APIs, survives client and daemon restart, and rejects stale/unauthorized commands — with no real provider, ACP, or tmux.

**Architecture:** New-kernel-then-converge. Fresh `domain/` (pure value types + state machine + digest + pure decision services), `storage/sqlite_store.py` (the sole atomic append+apply+revision writer), `app/mission_service.py` (command DTOs/validation), and `daemon/mission_kernel.py` (in-process single-writer ProjectDaemon guarded by an OS project lock). The legacy JSON `state.py`/`daemon/` are untouched; the new kernel writes only new SQLite facts. Follows `docs/superpowers/specs/2026-07-22-agentdeck-p1-durable-mission-kernel-design.md`.

**Tech Stack:** Python 3.12, conda env `agentdeck`, stdlib `sqlite3`, `dataclasses`, `hashlib`, `json`, `fcntl` file lock, pytest.

**Conventions for every task:**
- Run all commands after `source "$(conda info --base)/etc/profile.d/conda.sh" && conda activate agentdeck`.
- Commit messages: `test:` for a RED-only commit, `feat:` for GREEN, `refactor:`/`chore:` as appropriate. **No `Co-Authored-By` trailer. Do not push.**
- Update `HISTORY.md` in the same commit as the slice that completes a user-visible/authority behavior (at minimum once per slice).
- After each GREEN, run the slice's tests plus `python -m compileall -q src/agentdeck`.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/agentdeck/domain/__init__.py` | Package marker |
| `src/agentdeck/domain/ids.py` | Deterministic id/prefix helpers + `IdFactory` port |
| `src/agentdeck/domain/events.py` | `KernelEvent`, trigger provenance (`CommandTrigger`/`AdapterEventTrigger`/`InternalTrigger`), event kinds |
| `src/agentdeck/domain/authorization.py` | `AuthorizationEnvelope`, canonical serialization + `envelope_digest()` |
| `src/agentdeck/domain/mission.py` | `Mission`, `MissionVersion`, `Task`, `Attempt`, `AgentSession`, `Permission`, `Handoff`, `Evidence`, enums, and the pure Task/Attempt state machine |
| `src/agentdeck/domain/verification.py` | Pure `grade_evidence()` → `VerificationResult` (`pass`/`fail`/`unavailable`) + aggregate |
| `src/agentdeck/domain/governance.py` | Pure envelope/permission/revision gate decisions |
| `src/agentdeck/domain/ports.py` | `LeaderAdapter`, `WorkerAdapter`, `Clock` protocols + `TaskEnvelope` |
| `src/agentdeck/storage/__init__.py` | Package marker |
| `src/agentdeck/storage/migrations.py` | Ordered `SCHEMA_MIGRATIONS`, `apply_migrations(conn)`, `schema_migrations` table |
| `src/agentdeck/storage/sqlite_store.py` | `SqliteStore`: connection/pragmas, `append_and_apply`, idempotency, cursor + snapshot reads |
| `src/agentdeck/app/__init__.py` | Package marker |
| `src/agentdeck/app/mission_service.py` | Command DTOs, validation, command-id/revision checks, transition proposals |
| `src/agentdeck/daemon/mission_kernel.py` | `ProjectDaemon` single-writer loop, OS lock, three trigger intakes, scheduling, recovery |
| `tests/domain/` | Pure unit + state-machine tests (slices 2, 3) |
| `tests/storage/` | Atomicity, idempotency, cursor, reconstruct tests (slices 1, 4) |
| `tests/integration/` | Daemon + fake adapters: ownership, dispatch, reconnect, restart, fake Golden (5–8) |
| `tests/support/fake_adapters.py` | `FakeLeaderAdapter`, `FakeWorkerAdapter` |

---

## Slice 1 — Schema v1 + atomic event/state transaction

### Task 1.1: Schema migration runner

**Files:**
- Create: `src/agentdeck/storage/__init__.py`
- Create: `src/agentdeck/storage/migrations.py`
- Test: `tests/storage/test_migrations.py`

- [ ] **Step 1: Write failing test** — `tests/storage/test_migrations.py`:

```python
import sqlite3
from agentdeck.storage.migrations import apply_migrations, LATEST_SCHEMA_VERSION

def test_apply_migrations_is_idempotent_and_records_version(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    applied = apply_migrations(conn)
    assert applied == LATEST_SCHEMA_VERSION
    # second apply is a no-op returning the same version
    assert apply_migrations(conn) == LATEST_SCHEMA_VERSION
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert [r[0] for r in rows] == list(range(1, LATEST_SCHEMA_VERSION + 1))
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for t in ("projects", "events", "commands", "missions", "mission_versions",
              "tasks", "attempts", "sessions", "permissions", "handoffs",
              "evidence", "verification_results", "schema_migrations"):
        assert t in names
```

- [ ] **Step 2: Run test, expect FAIL** — `pytest tests/storage/test_migrations.py -v` → ImportError.
- [ ] **Step 3: Implement** `migrations.py` — a `SCHEMA_MIGRATIONS: list[tuple[int, str]]` of `(version, sql)` creating the schema-v1 tables from the spec §4; `apply_migrations(conn)` creates `schema_migrations(version PK, checksum, applied_at)` if absent, applies each unapplied version inside a transaction, records it, returns `LATEST_SCHEMA_VERSION`. `events.seq` is `INTEGER PRIMARY KEY AUTOINCREMENT`; `events.event_id` UNIQUE. Enable `PRAGMA foreign_keys=ON`.
- [ ] **Step 4: Run test, expect PASS.**
- [ ] **Step 5: Commit** — `test:`+`feat:` combined is fine here (new module): `git add src/agentdeck/storage tests/storage/test_migrations.py && git commit -m "feat: add SQLite schema v1 migration runner"`.

### Task 1.2: SqliteStore atomic append_and_apply

**Files:**
- Create: `src/agentdeck/storage/sqlite_store.py`
- Create: `src/agentdeck/domain/__init__.py`, `src/agentdeck/domain/events.py`, `src/agentdeck/domain/ids.py`
- Test: `tests/storage/test_sqlite_store.py`

- [ ] **Step 1: Write failing tests** covering:
  - `SqliteStore.create(path)` initializes project row with `current_revision=0`.
  - `append_and_apply(expected_revision=0, trigger=CommandTrigger(...), events=[KernelEvent(...)], state_mutations=[...])` returns `AppendResult(new_revision=1, first_event_seq=1)`, writes the event, upserts the mutated rows, and bumps `projects.current_revision` to 1.
  - A stale `expected_revision` raises `RevisionConflict(current_revision=...)` and writes nothing (event count unchanged).
  - A mutation that raises mid-transaction (inject via a bad `state_mutation` table) leaves revision and event count unchanged (full rollback).
  - `events_after(cursor)` returns events with `seq > cursor` in order; `current_revision()` reads the row.

```python
from agentdeck.storage.sqlite_store import SqliteStore, RevisionConflict
from agentdeck.domain.events import KernelEvent, CommandTrigger

def test_append_and_apply_is_atomic(tmp_path):
    store = SqliteStore.create(tmp_path / "state.db")
    trig = CommandTrigger(command_id="cmd_1", input_hash="h", expected_revision=0, actor="human")
    res = store.append_and_apply(
        expected_revision=0, trigger=trig,
        events=[KernelEvent(event_id="ev_1", event_type="project_initialized", payload={})],
        state_mutations=[])
    assert res.new_revision == 1 and res.first_event_seq == 1
    assert store.current_revision() == 1
    assert len(store.events_after(0)) == 1

def test_stale_revision_conflicts_without_mutation(tmp_path):
    store = SqliteStore.create(tmp_path / "state.db")
    ... # first append to reach revision 1, then attempt expected_revision=0
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** `domain/events.py`: `@dataclass(frozen=True) KernelEvent(event_id, event_type, payload: dict, mission_id=None, mission_version_id=None, task_id=None, attempt_id=None, session_id=None)`; trigger dataclasses `CommandTrigger(command_id, input_hash, expected_revision, actor)`, `AdapterEventTrigger(adapter_event_id, session_id, sequence, kind, lineage: dict)`, `InternalTrigger(internal_trigger_id, source_revision, reason)`. `domain/ids.py`: `new_id(prefix)` using a monotonic counter injected for determinism (accept an `IdFactory`). `sqlite_store.py`: `SqliteStore` opens WAL, `synchronous=FULL`, `foreign_keys=ON`; `append_and_apply` runs one `BEGIN IMMEDIATE`, checks `expected_revision` against `projects.current_revision` (raise `RevisionConflict` + rollback if mismatch), inserts events, applies `state_mutations` (each a typed upsert descriptor), writes/settles the `commands` row for `CommandTrigger`, bumps revision, commits. `state_mutations` are small typed objects like `UpsertRow(table, pk_col, values: dict)`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat: add SqliteStore atomic append_and_apply with revision guard`. Update HISTORY.

---

## Slice 2 — MissionVersion + authorization digest

### Task 2.1: AuthorizationEnvelope canonical digest

**Files:**
- Create: `src/agentdeck/domain/authorization.py`
- Test: `tests/domain/test_authorization.py`, `tests/domain/__init__.py`

- [ ] **Step 1: Write failing tests:**
  - `envelope_digest(env)` is a stable `sha256:<hex>` string.
  - Re-serializing the same envelope (dict key order shuffled) gives the same digest (canonical JSON with sorted keys).
  - Changing any field (goal, scope, an allowed operation, a budget) changes the digest.
  - `envelope_digest` ignores non-authority presentation fields if any are defined (choose: digest covers only the frozen authority fields listed in spec §3).

```python
from agentdeck.domain.authorization import AuthorizationEnvelope, envelope_digest

def test_digest_is_canonical_and_sensitive():
    a = AuthorizationEnvelope(goal="g", semantic_scope=["s"], path_scope=["p"],
        operation_classes=["read","write"], agent_allowlist=["fake"],
        external_effect_policy="none", budgets={"attempts": 4},
        retry_bounds={"per_task": 2}, acceptance_criteria=["tests_pass"],
        route_order=["acp"])
    d1 = envelope_digest(a)
    assert d1.startswith("sha256:")
    b = AuthorizationEnvelope(**{**a.__dict__, "operation_classes": ["read"]})
    assert envelope_digest(b) != d1
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** frozen dataclass + `_canonical(env)` → `json.dumps(sorted-keys, separators)` → `"sha256:" + hashlib.sha256(...).hexdigest()`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat: add authorization envelope canonical digest`.

### Task 2.2: MissionVersion immutability + persistence

**Files:**
- Modify: `src/agentdeck/domain/mission.py` (create)
- Modify: `src/agentdeck/storage/sqlite_store.py` (add `record_mission_version` via append_and_apply helper is NOT added here; persistence stays generic — instead test that a mission_version row round-trips through `append_and_apply` state_mutations)
- Test: `tests/domain/test_mission_version.py`

- [ ] **Step 1: Write failing test** — build a `MissionVersion(mission_id, version_id, goal, tasks=[...], acceptance_criteria, envelope, provenance)`; assert `version.digest == envelope_digest(envelope)`; assert dataclass is frozen (mutating raises); persist via store `append_and_apply` with an `UpsertRow("mission_versions", ...)` and read it back.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `mission.py` value types (frozen dataclasses) for `Mission`, `MissionVersion` (computes/holds digest), and stubs for `Task` (filled in slice 3). `MissionVersion.digest` set from `envelope_digest`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat: add immutable MissionVersion bound to authorization digest`.

---

## Slice 3 — Task/Attempt state machine

### Task 3.1: Task + Attempt enums and legal transitions

**Files:**
- Modify: `src/agentdeck/domain/mission.py`
- Test: `tests/domain/test_state_machine.py`

- [ ] **Step 1: Write failing tests:**
  - `TaskState` enum: `pending, ready, dispatched, awaiting_verification, completed, failed, cancelled`. `completed/failed/cancelled` are absorbing.
  - `AttemptState` enum: `created, running, awaiting_permission, succeeded, failed, abandoned`.
  - `advance_task(task, event)` returns a new Task with the next legal state or raises `IllegalTransition` for an illegal one (e.g. `completed → ready`).
  - `ready_tasks(tasks, completed_ids)` returns tasks whose deps are all completed and state is `pending`.
  - A retry produces a **new** Attempt id; the prior Attempt is retained unchanged.

```python
from agentdeck.domain.mission import (TaskState, advance_task, IllegalTransition, ready_tasks)
import pytest

def test_absorbing_terminal_rejects_reactivation():
    t = _task(state=TaskState.completed)
    with pytest.raises(IllegalTransition):
        advance_task(t, _event("task_ready"))

def test_ready_tasks_respects_dependencies():
    a = _task(id="A", state=TaskState.pending, deps=[])
    b = _task(id="B", state=TaskState.pending, deps=["A"])
    assert [t.task_id for t in ready_tasks([a,b], completed_ids=set())] == ["A"]
    assert [t.task_id for t in ready_tasks([a,b], completed_ids={"A"})] == ["B"]
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** enums, a transition table `{(state, event_type): next_state}`, `advance_task`, `ready_tasks`, `Attempt` frozen dataclass, `new_attempt(task, route_position)` helper. Pure — no persistence.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat: add Task/Attempt state machine with dependency gating`. Update HISTORY.

---

## Slice 4 — Command idempotency + project revisions

### Task 4.1: MissionService command validation + idempotent settle

**Files:**
- Create: `src/agentdeck/app/__init__.py`, `src/agentdeck/app/mission_service.py`
- Test: `tests/storage/test_command_idempotency.py`, `tests/integration/__init__.py`

- [ ] **Step 1: Write failing tests:**
  - `MissionService(store).submit(command)` with a fresh `command_id` applies it and returns `Applied(new_revision, outcome)`.
  - Re-submitting the **same** `command_id` + same input returns the **recorded** outcome without a new event (idempotent replay).
  - Same `command_id` with **different** input raises `CommandConflict`.
  - A command with a stale `expected_revision` returns `RevisionConflict` (surfaced from the store) with the current revision and no mutation.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** command DTOs (`InitializeProject`, `ProposeMissionVersion`, `ConfirmMission`, plus a generic base carrying `command_id`, `expected_revision`, `actor`, and an `input_hash` computed from payload). `MissionService.submit` looks up the `commands` row: if settled+same hash → return recorded outcome; if settled+different hash → `CommandConflict`; else validate + build events/mutations + call `store.append_and_apply`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat: add mission service command idempotency and revision checks`.

### Task 4.2: ConfirmMission binds exact digest

**Files:**
- Modify: `src/agentdeck/app/mission_service.py`
- Test: `tests/integration/test_confirm_digest.py`

- [ ] **Step 1: Write failing tests:** a `ConfirmMission(version_id, digest)` with the **matching** digest admits/freezes the Mission (`missions.lifecycle_state='confirmed'`); a **wrong** digest raises `DigestMismatch` and writes nothing.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** confirm handler comparing supplied digest to the stored `mission_versions.digest`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat: bind mission confirmation to exact authorization digest`. Update HISTORY.

---

## Slice 5 — Daemon single-writer ownership

### Task 5.1: ProjectDaemon + OS project lock

**Files:**
- Create: `src/agentdeck/daemon/mission_kernel.py`
- Test: `tests/integration/test_daemon_ownership.py`

- [ ] **Step 1: Write failing tests:**
  - `ProjectDaemon.open(project_dir)` acquires an exclusive `fcntl.flock` on `.agentdeck/kernel.lock` and exposes `submit_command`, `submit_adapter_event`, `run_internal_tick`.
  - Opening a **second** `ProjectDaemon` on the same dir while the first holds the lock raises `DaemonAlreadyRunning` (or blocks then fails non-blocking); no mutation possible from the second.
  - After `daemon.close()`, a fresh `ProjectDaemon.open` succeeds (models restart).
  - All three trigger intakes route through the one `store.append_and_apply`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `ProjectDaemon` wrapping `SqliteStore` + `MissionService`, taking a non-blocking `flock` (LOCK_EX|LOCK_NB) in `open`, raising `DaemonAlreadyRunning` on failure; `close()` releases. Serialize intake with a threading lock (single writer).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat: add single-writer ProjectDaemon with OS project lock`. Update HISTORY.

---

## Slice 6 — Fake Worker dispatch / handoff / evidence

### Task 6.1: Adapter ports + fake adapters

**Files:**
- Create: `src/agentdeck/domain/ports.py`, `tests/support/__init__.py`, `tests/support/fake_adapters.py`
- Test: `tests/integration/test_fake_worker_dispatch.py`

- [ ] **Step 1: Write failing tests:** dispatching Task A through the daemon builds a `TaskEnvelope` (MissionVersion + authorization lineage + Task/Attempt id + role/agent/model/transport + scoped inputs + acceptance schema), hands it to `FakeWorkerAdapter`, and the adapter's emitted events (progress → permission_request → artifact+evidence → completion) flow back into `submit_adapter_event`. Assert: an intent event precedes dispatch; an in-envelope permission is granted; an out-of-envelope permission request is refused; Evidence is recorded once (replay of the same `adapter_event_id` is idempotent).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `ports.py` Protocols (`LeaderAdapter.propose(goal, context)->MissionVersionProposal`, `WorkerAdapter.run(envelope, emit)`); `TaskEnvelope` dataclass; fake adapters producing deterministic ordered events; daemon dispatch that records intent, calls the worker, validates/dedupes inbound events, applies transitions via governance + store.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat: add adapter ports and fake worker dispatch with permission lineage`.

### Task 6.2: Verification-gated completion + Handoff A→B

**Files:**
- Create: `src/agentdeck/domain/verification.py`, `src/agentdeck/domain/governance.py`
- Test: `tests/domain/test_verification.py`, `tests/integration/test_handoff.py`

- [ ] **Step 1: Write failing tests:**
  - `grade_evidence(criteria, evidence)` returns per-criterion `pass/fail/unavailable`; a mandatory non-pass blocks completion; all-pass completes.
  - Task A cannot complete on worker "completion" text alone — completion requires a Verification pass.
  - A `Handoff` A→B is required before Task B is dispatched with A's artifacts; Task B never reads A's completion except through the Handoff.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** pure `verification.py` + `governance.py`; wire the daemon to grade Evidence before completing a Task and to record/require Handoff before dependent dispatch.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat: gate task completion on verification and cross-worker handoff`. Update HISTORY.

---

## Slice 7 — Client disconnect / reconnect

### Task 7.1: Cursor replay + coherent snapshot

**Files:**
- Modify: `src/agentdeck/daemon/mission_kernel.py`, `src/agentdeck/storage/sqlite_store.py`
- Test: `tests/integration/test_reconnect.py`

- [ ] **Step 1: Write failing tests:** `daemon.events_after(cursor)` returns exactly the events with `seq > cursor` in order; `daemon.snapshot()` returns one coherent projection (mission/tasks/attempts/permissions) at the current revision; after a simulated disconnect, a client that replays from its last cursor and refreshes the snapshot sees no lost or duplicated events and a consistent revision.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `events_after` (already partly present) + a read-only `snapshot()` assembling current-table rows at one revision.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat: add cursor-based reconnect replay and coherent snapshot`. Update HISTORY.

---

## Slice 8 — Daemon crash reconciliation + fake Golden

### Task 8.1: Restart recovery

**Files:**
- Modify: `src/agentdeck/daemon/mission_kernel.py`
- Test: `tests/integration/test_restart_recovery.py`

- [ ] **Step 1: Write failing tests:** mid-Mission, `close()` the daemon and `open()` a fresh one against the same DB; recovery rebuilds materialized state from durable facts, preserves absorbing terminal states, reconciles an accepted command whose outcome event exists (no double-apply), and resumes only proven-safe in-envelope work; an injected ambiguous outcome (intent with no outcome) causes zero new dispatch and a reconciliation pause.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `_recover()` run in `open()`: load revision + current tables, scan for intents lacking outcomes → mark reconciliation pause, verify terminal states are absorbing, do not re-dispatch completed/failed work.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat: add daemon restart recovery and reconciliation pause`.

### Task 8.2: Fake Golden Mission end-to-end

**Files:**
- Test: `tests/integration/test_fake_golden_mission.py`

- [ ] **Step 1: Write the failing end-to-end test** driving the full journey from spec §7 (init → propose → confirm exact digest → schedule/dispatch Task A → permission handling → evidence → verify → complete → handoff → Task B → complete → Mission complete) through public daemon/app APIs only, with: a mid-Mission daemon restart that resumes correctly; a stale-revision command rejected; a wrong-digest confirm rejected. Assert Mission reaches `completed` with evidence-backed acceptance and the event ledger + snapshot are consistent.
- [ ] **Step 2: Run, expect FAIL** (whatever gap remains).
- [ ] **Step 3: Implement** the minimal remaining wiring so the journey passes. No real provider/ACP/tmux.
- [ ] **Step 4: Run full P1 suite** — `pytest tests/domain tests/storage tests/integration -q` all green; `python -m compileall -q src/agentdeck`.
- [ ] **Step 5: Commit** — `feat: pass deterministic fake Golden Mission through durable kernel`. Update HISTORY and mark P1 exit-gate evidence.

---

## Self-review notes

- **Spec coverage:** slices map 1:1 to spec §8; digest (§3/§6) → 2.1/4.2; single-writer (§2.1/§5) → 5.1; intent/outcome (§5) → 6.1/8.1; verification (§6) → 6.2; reconnect (§7) → 7.1; restart (§7) → 8.1; fake Golden (§7) → 8.2. Deferred items (§10) intentionally have no task.
- **Type consistency:** `append_and_apply(expected_revision, trigger, events, state_mutations)`, `KernelEvent`, `CommandTrigger`, `RevisionConflict`, `envelope_digest`, `TaskState`, `advance_task`, `ready_tasks`, `TaskEnvelope`, `ProjectDaemon.open/close/submit_command/submit_adapter_event/events_after/snapshot` are used consistently across tasks.
- **No real adapters/socket/migration** in any task (deferred per spec §2/§10).
