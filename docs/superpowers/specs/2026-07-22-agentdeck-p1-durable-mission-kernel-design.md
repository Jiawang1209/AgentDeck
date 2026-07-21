# AgentDeck P1 Durable Mission Kernel Design

Date: 2026-07-22

This spec is the P1 task-level design produced after human P0 exit approval. It
is subordinate to and must not contradict:

- `docs/superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md`
  (program authority, phase ordering, P1 required slices, commit discipline);
- `docs/architecture/agentdeck-v1-kernel-reset.md` (domain model, invariants,
  one-writer boundary, dependency direction);
- `docs/architecture/agentdeck-v1-state-migration.md` (SQLite authority,
  transaction model, schema responsibilities);
- `docs/product/agentdeck-v1-prd.md` (product scope and state precedence).

Where this spec is silent, those documents govern. This spec only decomposes
P1 into an implementable, test-driven vertical-slice design; it does not expand
V1 scope or reorder phases.

## 1. Decisions frozen for P1 (from brainstorming)

Two approach decisions were made with the human before writing this spec:

1. **Integration strategy = new kernel, then converge.** P1 builds a new
   SQLite-backed Mission authority as fresh `domain/`, `storage/`, and `app/`
   packages plus a new dedicated single-writer command loop. The existing
   ~11.6k-line JSON `state.py` and the existing ~9.9k-line JSON `daemon/`
   package are **left untouched** during P1 except that P1 adds nothing that
   makes them write the new SQLite facts. Cutover/convergence of legacy commands
   onto the new authority is later, per-command, tested vertical-slice work
   (P2+), not P1.

2. **Scope = P1 slices 1–8 to the fake Golden exit gate.** Slice 9 (legacy
   JSON→SQLite migration preview/confirm/verify/rollback) and slice 10
   (ProjectView v2 + v1 compatibility) are **deferred to their own separate
   small plans** after P1's fake Golden passes. They are large enough to be
   independent efforts and are not required by P1's exit gate.

### 1.1 No-second-writer safety during coexistence

The "one product-state writer" rule is about **one fact, one writer**. In P1
the new SQLite kernel and the legacy JSON daemon govern **disjoint stores and
disjoint facts**: the new kernel writes only new V1 Mission-kernel facts to
`.agentdeck/state.db`; the legacy daemon continues to write only legacy facts
to JSON/JSONL. No P1 code path lets both stores author the same fact, and the
fake Golden Mission runs **entirely** through the new kernel with fake
adapters. Convergence (making the SQLite kernel the sole authority for a fact
that legacy code once wrote) happens later by per-command cutover. This spec
adds no bridge that reads one store to author the other.

## 2. What P1 delivers

P1's observable result is a **deterministic fake Golden Mission** that runs end
to end through the new public daemon/application API using only fake adapters,
survives client disconnect/reconnect and daemon restart, rejects stale and
unauthorized commands, and requires no real provider, ACP, or tmux.

New packages and files:

```text
src/agentdeck/
  domain/
    mission.py         # Mission, MissionVersion, Task, Attempt, Handoff,
                       # Evidence value types + pure Task/Attempt state machine
    authorization.py   # AuthorizationEnvelope + canonical deterministic digest
    events.py          # KernelEvent + trigger provenance + cursor semantics
    ports.py           # LeaderAdapter / WorkerAdapter / clock / id port protocols
    verification.py    # pure Evidence-vs-criteria grading (pass/fail/unavailable)
    governance.py      # pure envelope/permission/revision gate decisions
  storage/
    sqlite_store.py    # SqliteStore: schema v1, the sole atomic append+apply+bump
    migrations.py      # ordered schema migration runner + schema_migrations table
  app/
    mission_service.py # command DTOs, validation, command-id/revision checks,
                       # translation of client commands into proposed transitions
  daemon/
    mission_kernel.py  # NEW single-writer ProjectDaemon command loop over SQLite
                       # (in-process API + OS project lock; socket IPC is P2)
tests/
  domain/              # pure unit + state-machine invariants (slices 1-3)
  storage/             # atomicity, idempotency, cursor, reconstruct-recovery (1,4)
  integration/         # in-process daemon + fake adapters: fake Golden, reconnect,
                       # restart, stale/unauthorized rejection (slices 5-8)
  support/
    fake_adapters.py   # FakeLeaderAdapter / FakeWorkerAdapter test support only
```

`storage/legacy_import.py` and the `adapters/` package are **not** created in
P1: legacy import is slice 9 (deferred), and real adapters are P3. Fake
adapters live under `tests/support/` so P1 does not prematurely occupy the P3
`adapters/` package.

### 2.1 Daemon scope clarification

P1 delivers the single-writer authority as an **in-process** `ProjectDaemon`
(`daemon/mission_kernel.py`) guarded by an OS-level project lock file under
`.agentdeck/`. This gives P1 genuine single-writer ownership (a second live
writer refuses to mutate) and a real "public daemon API" surface, while keeping
every test deterministic and process-free. "Daemon restart" is modeled
deterministically as: drop the daemon object, reconstruct a fresh one against
the same `.agentdeck/state.db`, run recovery reconciliation. The real
Unix-domain-socket IPC transport (bare `agentdeck` talking to a background
daemon process) is a **P2** concern and is out of P1 scope.

## 3. Domain model (P1 subset)

P1 implements the kernel-reset domain entities needed by the fake Golden:

- `Mission` — stable id, current version pointer, lifecycle state.
- `MissionVersion` — immutable goal, Task DAG, acceptance criteria, provenance,
  embedded `AuthorizationEnvelope`, and authorization `digest`.
- `AuthorizationEnvelope` — goal + semantic/path scope, allowed operation
  classes, Agent allowlist, external-effect policy, budgets, retry bounds,
  acceptance criteria, permitted route order. Digest is a deterministic
  content hash over a canonical serialization.
- `Task` — DAG node: role, scope, dependencies, acceptance contribution, state.
- `Attempt` — one bounded try for a Task/Agent/model/transport/route; retries
  create new Attempts and never overwrite prior ones.
- `AgentSession` — reconnectable worker execution context with provenance,
  lease, takeover state, reconciliation facts.
- `Permission` — decision bound to exact MissionVersion/Task/Attempt/Session +
  operation + scope + outcome.
- `Handoff` — AgentDeck-owned source→dest transfer with artifacts/evidence.
- `Evidence` — durable command/test/review/effect fact with source + hash.
- `VerificationResult` — deterministic `pass`/`fail`/`unavailable` per criterion
  plus aggregate completion decision.

### 3.1 Enforced invariants (P1)

- The `ProjectDaemon` is the only writer; decision services (mission,
  governance, verification) are pure and cannot append/apply/persist.
- Confirmation binds one exact MissionVersion + digest; a digest mismatch on
  confirm is rejected with no mutation.
- Leader output is a proposal, never a state transition.
- Worker "done" text is only an observation; Task completion requires
  Verification acceptance of required Evidence.
- Worker B receives Worker A's completion only through a Handoff.
- Every retry/reassignment creates a new Attempt.
- One Attempt may carry multiple ordered Permissions; permission count is not
  stage identity.
- Append-only events preserve history; current-state tables are recovery-speed
  truth; a monotonic event cursor drives reconnect.

## 4. Storage: SQLite schema v1 and the atomic write

`SqliteStore` is the only object that opens a write connection to
`.agentdeck/state.db`. It exposes exactly one mutation primitive:

```text
append_and_apply(
    expected_revision,          # optimistic concurrency guard
    trigger,                    # command | adapter_event | internal_trigger provenance
    events=[...],               # append-only KernelEvents to record
    state_mutations=[...],      # current-table upserts implied by those events
) -> AppendResult(new_revision, first_event_seq)
```

It records the events, applies the current-table mutations, records/settles the
command idempotency row when the trigger is a client command, and bumps
`projects.current_revision` — **all in one SQLite transaction**. Commit
publishes all facts together or none.

Schema v1 tables (physical layout may be normalized during implementation while
preserving these responsibilities):

- `projects(project_id, authority_generation, current_revision, created_at)`
- `schema_migrations(version, checksum, applied_at)`
- `events(seq PK AUTOINCREMENT, event_id UNIQUE, project_id, trigger_kind,
  trigger_id, mission_id, mission_version_id, task_id, attempt_id, session_id,
  event_type, payload_json, created_at)` — `seq` is the monotonic cursor.
- `commands(command_id PK, project_id, input_hash, expected_revision, status,
  outcome_json, first_event_seq, created_at)` — idempotency + replay/conflict.
- `missions(mission_id PK, project_id, current_version_id, lifecycle_state)`
- `mission_versions(version_id PK, mission_id, goal, envelope_json, digest,
  provenance_json, created_at)`
- `tasks(task_id PK, mission_version_id, role, scope_json, deps_json, state,
  acceptance_json)`
- `attempts(attempt_id PK, task_id, agent, model, transport, route_position,
  state, terminal_class)`
- `sessions(session_id PK, attempt_id, agent, model, transport, lease_json,
  takeover_state, reconcile_json)`
- `permissions(permission_id PK, mission_version_id, task_id, attempt_id,
  session_id, operation, scope_json, decision, created_at)`
- `handoffs(handoff_id PK, mission_version_id, source_task_id, dest_task_id,
  artifacts_json, evidence_json, acceptance_state, provenance_json)`
- `evidence(evidence_id PK, mission_version_id, task_id, attempt_id, kind, hash,
  summary, source_json, created_at)`
- `verification_results(result_id PK, mission_version_id, task_id, criterion,
  grade, reason, created_at)`

Tables owned by later phases (`conversations`, `approvals`, `artifacts`,
`learning`, `suggestions`) are added when their slice arrives, not in P1.

Durability/pragma policy (WAL mode, `synchronous`, checkpoint/close before
"restart", foreign-key enforcement) is selected from the slice-1/slice-8
reconstruct-recovery tests, per the state-migration doc, not by convenience.
P1 proves read-after-reconstruct correctness; it does not implement the
JSON→SQLite migration state machine (that is slice 9, deferred).

## 5. Command, adapter-event, and internal-trigger loop

All three trigger kinds enter one serialized daemon loop and use the single
`append_and_apply` call. None fabricates another kind's provenance fields.

- **Client command**: carries `command_id`, `expected_revision`, actor,
  authorization decision. Replaying a settled `command_id` returns its recorded
  outcome; reusing it with different input fails closed; a stale
  `expected_revision` returns a conflict + current revision, no mutation.
- **Adapter event** (from a fake worker/leader adapter): carries
  `adapter_event_id` + exact Mission/Version/Task/Attempt/Session lineage +
  per-session ordering + kind. The daemon validates identity/lineage/ordering,
  deduplicates replays, and safely holds gaps/out-of-order/conflicting
  identities for reconciliation. Stale terminal/permission/evidence events
  remain audit observations and cannot reactivate terminal work, repeat a
  permission, double-apply an effect, or double-credit Evidence.
- **Internal trigger** (lease expiry, reconciliation, scheduler decision):
  carries `internal_trigger_id` + source revision/snapshot + deterministic
  decision provenance. It derives transitions only from durable facts and
  deterministic policy; it never invents an external fact.

Every external (fake) operation records a durable **intent** before dispatch
and a durable **outcome** after observation. Absence of an outcome is not proof
of no effect; ambiguous outcomes cause zero new dispatch. In P1 the fake worker
adapter is deterministic, so outcomes are always well-defined; the intent/
outcome discipline is still implemented and asserted so recovery is correct.

## 6. Decision services (pure)

- **Mission engine** (`domain/mission.py`): interprets a frozen MissionVersion,
  advances the Task DAG when dependencies/scope/route allow, decides Attempt
  and Handoff transitions, and proposes bounded recovery. Returns typed
  decisions/proposed events; no persistence access.
- **Governance** (`domain/governance.py`): evaluates whether a requested worker
  operation is inside the frozen envelope, validates permission lineage,
  checks expected revision and command authorization, and classifies simple
  pause vs. proceed. Full precedence (Mission>Task>Session) and the complete
  fallback taxonomy are refined in P3/P4; P1 implements the subset the fake
  Golden exercises (one in-envelope permission granted, one out-of-envelope
  request paused/refused).
- **Verification** (`domain/verification.py`): grades durable Evidence against
  each frozen acceptance criterion as `pass`/`fail`/`unavailable` with a reason,
  and returns the aggregate completion decision. A mandatory non-pass blocks
  Task/Mission completion. No worker/reviewer self-attestation can override it.

## 7. Fake adapters and the fake Golden Mission

`tests/support/fake_adapters.py`:

- `FakeLeaderAdapter` — returns a canned, deterministic MissionVersion proposal
  (goal + two-Task DAG A→B + acceptance criteria + envelope) for a given goal.
  It only proposes; it cannot mutate state.
- `FakeWorkerAdapter` — given a `TaskEnvelope`, emits a deterministic ordered
  sequence of adapter events into the daemon inbound port: progress → (optional)
  one permission request → artifact + Evidence → completion. A second configured
  worker emits a Handoff-consuming sequence for Task B.

The fake Golden Mission integration test drives this journey through public
daemon/app APIs only:

1. Initialize a fresh project + SQLite authority (no invented legacy state).
2. Leader proposes a MissionVersion → Mission Preview with envelope + digest.
3. Human confirms the exact version + digest → Mission admitted and frozen.
4. Daemon schedules Task A → dispatches to FakeWorker A → worker events flow in
   → in-envelope permission is granted, an out-of-envelope request is refused/
   paused → Evidence recorded → Verification grades pass → Task A completes.
5. Handoff A→B recorded → dependent Task B scheduled and dispatched → Evidence →
   Verification pass → Task B completes.
6. Verification aggregates → Mission completes with evidence-backed acceptance.

Interleaved robustness assertions (slices 6–8):

- **Client disconnect/reconnect**: a client requests events after its last
  cursor and refreshes one coherent snapshot; no lost or duplicated events.
- **Daemon restart mid-Mission**: reconstruct the daemon against the same DB,
  run recovery (preserve absorbing terminal states, reconcile leases/
  outcomes, resume only proven-safe in-envelope work), and complete correctly.
- **Stale command**: a command with an old `expected_revision` is rejected with
  a conflict and current revision, no mutation.
- **Unauthorized command**: a confirm with a wrong digest, or a mutation
  without required actor provenance, is refused with no mutation.

## 8. Vertical slices and TDD order

Each slice is one RED → minimal GREEN → adjacent regression → HISTORY + commit,
following the program's commit categories (`test:` RED, `feat:` GREEN,
`refactor:` characterized move, `chore:` mechanics).

1. **Schema v1 + atomic event/state transaction.** `storage/migrations.py`
   applies ordered schema; `SqliteStore.append_and_apply` records events +
   state + revision atomically; a mid-transaction failure rolls back fully.
   Tests: `tests/storage/`.
2. **MissionVersion + authorization digest.** Immutable version, canonical
   deterministic digest, digest stability across serialization, digest change
   on any envelope change. Tests: `tests/domain/`.
3. **Task/Attempt state machine.** Legal transitions, absorbing terminals,
   new-Attempt-on-retry, DAG dependency gating. Tests: `tests/domain/`.
4. **Command idempotency + project revisions.** Settle/replay/conflict
   semantics through `app/mission_service` + store; stale revision rejected.
   Tests: `tests/storage/` + `tests/integration/`.
5. **Daemon single-writer ownership.** `daemon/mission_kernel.py` owns the
   serialized loop + OS project lock; a second writer refuses; the three
   trigger kinds share one persistence call. Tests: `tests/integration/`.
6. **Fake Worker dispatch/handoff/evidence.** Envelope dispatch, ordered
   adapter events, intent/outcome, permission lineage, Verification-gated Task
   completion, Handoff A→B. Tests: `tests/integration/`.
7. **Client disconnect/reconnect.** Cursor-based event replay + one coherent
   snapshot after reconnect. Tests: `tests/integration/`.
8. **Daemon crash reconciliation + fake Golden.** Reconstruct + recover +
   resume; absorbing terminals preserved; ambiguous → zero dispatch; the full
   fake Golden Mission passes with restart and stale/unauthorized rejection.
   Tests: `tests/integration/`.

## 9. Verification, risk controls, and stop conditions

P1 uses only verification layers 1–3 from the program (unit/state-machine,
contract/governance/security, deterministic integration with real daemon + fake
adapters). No real adapter smoke and no Golden A/B run in P1. Every P1 test uses
fake/local state and makes no provider, ACP, CLI/PTY, tmux, or network call.

Risk-register rows P1 exercises and how its evidence proves the control:

- *Legacy and new state both claim authority* → disjoint stores/facts, no P1
  bridge authoring across stores, fake Golden runs only through the new kernel.
- *Daemon concurrency repeats effects* → command idempotency, expected-revision
  guard, single-writer lock, and durable intent/outcome events before recovery.
- *Model nondeterminism destabilizes the kernel* → fake deterministic adapters
  propose only; pure domain/governance/verification authorize and advance.

Stop and return to design review (per the program/kernel stop conditions) if any
slice would create a second product-state writer, a second scheduler, an
adapter/provider branch inside kernel logic, recovery that guesses from a
filename/timeout/terminal pixel, automatic fallback without proven-no-effect or
reconciled-idempotent safety, confirmation not bound to an exact digest, or a
test that starts implementing production scheduling/governance semantics.

## 10. Explicitly deferred (not P1)

- Slice 9: legacy JSON→SQLite migration preview/confirm/verify/rollback (the
  full state-migration state machine) — separate small plan after P1.
- Slice 10: ProjectView v2 + v1 compatibility projection — separate small plan.
- Real Unix-socket daemon IPC and bare-`agentdeck` conversation over the daemon
  — P2.
- Real Codex/Claude Leader/Worker adapters and transport taxonomy — P3.
- Full governance precedence and complete fallback taxonomy, real Golden
  Missions — P3/P4.
- Cutover of legacy `state.py`/`daemon/` commands onto the new authority —
  incremental post-P1 per-command work.
