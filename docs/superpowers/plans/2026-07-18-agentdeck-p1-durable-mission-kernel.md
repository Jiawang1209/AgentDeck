# AgentDeck P1 Durable Mission Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the SQLite-backed, single-writer Durable Mission Kernel that can execute one deterministic fake Mission through public daemon APIs, survive client and daemon restart, migrate legacy state explicitly, and project coherent `project-view/v2` plus compatible `project-view/v1` snapshots.

**Architecture:** New pure domain services describe Mission, authorization, Task, Attempt, Handoff, Evidence, and transition decisions without persistence access. One `ProjectDaemonService` owns a locked `SQLiteMissionStore`; every accepted command/event/current-state/revision mutation commits in one transaction. Existing JSON/JSONL state remains the legacy source until an explicit preview-bound migration builds, verifies, and activates SQLite; after activation, legacy mutation paths fail closed.

**Tech Stack:** Python 3.12, standard-library `sqlite3`, `fcntl` project writer lock on macOS/Linux, dataclasses, JSON canonicalization, asyncio daemon service, pytest, conda environment `agentdeck`.

---

## Execution guard

- Work only in branch `codex/p1-durable-mission-kernel` and its isolated worktree.
- Use strict RED/GREEN TDD for every production behavior; show the expected failing assertion before implementation.
- Update `HISTORY.md` in every feature commit. Update handoff/contracts when their observable behavior changes.
- Run commands through `conda run --no-capture-output -n agentdeck` with `PYTHONPATH="$PWD/src"` for tests.
- Never call a real provider, ACP adapter, tmux session, preflight, live Mission, or Golden Mission in P1.
- Never install tools, change authentication/global settings, merge, or push.
- At no point may JSON/JSONL and SQLite both accept product-state mutations.
- P2 conversation UX, P3 real adapters, and P4 real Golden composition are explicitly out of scope.

## File map

- `src/agentdeck/domain/`: immutable domain values and pure transitions.
- `src/agentdeck/storage/`: writer lease, SQLite schema/store, authority routing, and legacy import.
- `src/agentdeck/app/`: daemon-owned Mission command/event application services.
- `src/agentdeck/projections/`: v2 and compatibility-v1 ProjectView builders from one SQLite snapshot.
- `src/agentdeck/daemon/service.py`: only the narrow composition seam for the new application service.
- `src/agentdeck/state.py`: legacy guard/import support only; no new Mission authority.
- `tests/domain/`, `tests/storage/`, `tests/integration/`: focused P1 suites.

### Task 1: Freeze domain identities and closed event provenance

**Files:**
- Create: `src/agentdeck/domain/__init__.py`
- Create: `src/agentdeck/domain/events.py`
- Create: `tests/domain/test_events.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED tests for trigger-specific provenance**

```python
def test_client_event_does_not_accept_adapter_fields():
    with pytest.raises(ValueError, match="client command provenance invalid"):
        DomainEvent.client_command(
            event_id="evt_1", command_id="cmd_1", expected_revision=0,
            actor={"kind": "human"}, kind="mission_created", payload={},
            adapter_event_id="ae_1",
        )

def test_event_payload_is_canonical_and_detached():
    payload = {"mission_id": "mis_1"}
    event = DomainEvent.client_command(
        event_id="evt_1", command_id="cmd_1", expected_revision=0,
        actor={"kind": "human"}, kind="mission_created", payload=payload,
    )
    payload["mission_id"] = "changed"
    assert event.payload == {"mission_id": "mis_1"}
```

- [ ] **Step 2: Run RED**

Run: `PYTHONPATH="$PWD/src" conda run --no-capture-output -n agentdeck pytest tests/domain/test_events.py -q`

Expected: FAIL because `agentdeck.domain.events` does not exist.

- [ ] **Step 3: Implement closed event types**

Implement frozen `ClientCommandProvenance`, `AdapterEventProvenance`, `InternalTriggerProvenance`, and `DomainEvent`. Expose only three constructors: `client_command`, `adapter_event`, `internal_trigger`. Canonical JSON must reject floats, non-string keys, oversized payloads, and mixed provenance fields.

```python
TriggerKind = Literal["client_command", "adapter_event", "internal_trigger"]

@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    kind: str
    trigger_kind: TriggerKind
    provenance: Mapping[str, JsonValue]
    payload: Mapping[str, JsonValue]
    created_at: str

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
```

- [ ] **Step 4: Run GREEN and regression**

Run the Task 1 test, then `pytest tests/test_daemon_protocol.py tests/test_contracts.py -q`.

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: define durable Mission event provenance`

### Task 2: Implement immutable MissionVersion and authorization digest

**Files:**
- Create: `src/agentdeck/domain/authorization.py`
- Create: `src/agentdeck/domain/mission.py`
- Create: `tests/domain/test_authorization.py`
- Create: `tests/domain/test_mission.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED tests for canonical digest and DAG closure**

Tests must prove: list order is meaningful where declared; mapping order is not; stale digest fails; duplicate Task ids, missing dependencies, cycles, unbounded retry/budget values, empty acceptance criteria, and a second concurrently mutating Mission are rejected.

```python
def test_confirmation_binds_exact_version_and_envelope():
    version = mission_version(goal="ship", tasks=(task("build"),))
    envelope = AuthorizationEnvelope(operations=("write_project",), max_attempts=2)
    frozen = version.bind_authorization(envelope)
    assert frozen.authorization_digest == authorization_digest(version, envelope)
    with pytest.raises(ValueError, match="authorization digest mismatch"):
        frozen.confirm("sha256:" + "0" * 64)
```

- [ ] **Step 2: Run RED**

Expected: import failure for the new domain modules.

- [ ] **Step 3: Implement the pure models**

Create frozen `TaskSpec`, `MissionVersion`, `AuthorizationEnvelope`, `ConfirmedMissionVersion`, `AttemptState`, and pure `validate_task_dag()` / `authorization_digest()` helpers. Use explicit enums/tuples and canonical JSON; no store, daemon, provider, runtime, or clock access.

- [ ] **Step 4: Run GREEN plus existing Mission tests**

Run new domain tests and `tests/test_mission.py tests/test_mission_orchestration.py -q`.

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: freeze Mission authorization domain`

### Task 3: Establish schema v1 and exclusive daemon writer lease

**Files:**
- Create: `src/agentdeck/storage/__init__.py`
- Create: `src/agentdeck/storage/ownership.py`
- Create: `src/agentdeck/storage/migrations.py`
- Create: `src/agentdeck/storage/sqlite_store.py`
- Create: `tests/storage/test_schema.py`
- Create: `tests/storage/test_writer_lease.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED tests**

Tests require owner-only `.agentdeck/state.db`, foreign keys, schema version 1, explicit authority state, WAL for active stores, and rejection of a second writer lease.

```python
with ProjectWriterLease.acquire(root) as lease:
    store = SQLiteMissionStore.create(root, lease=lease, project_id="prj_1")
    assert store.schema_version == 1
    with pytest.raises(WriterLeaseError, match="another project writer is active"):
        ProjectWriterLease.acquire(root)
```

- [ ] **Step 2: Run RED**

Expected: storage modules do not exist.

- [ ] **Step 3: Implement schema and ownership**

Schema v1 contains `schema_migrations`, `projects`, `commands`, `events`, `missions`, `mission_versions`, `tasks`, `attempts`, `sessions`, `permissions`, `handoffs`, `evidence`, `approvals`, `artifacts`, `learning`, `suggestions`, and `legacy_records`. `ProjectWriterLease` owns an `fcntl.flock` lock file and is required by every mutating store constructor. Readers never receive the writer connection.

- [ ] **Step 4: Run GREEN and reopen tests**

Also prove unsupported newer schema and malformed authority state refuse mutation without modifying bytes.

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: add SQLite Mission schema and writer lease`

### Task 4: Make event, current state, command outcome, and revision atomic

This task owns the P1 command idempotency and project revision slice.

**Files:**
- Modify: `src/agentdeck/storage/sqlite_store.py`
- Create: `tests/storage/test_atomic_mutation.py`
- Create: `tests/storage/test_command_idempotency.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED failure-injection tests**

Cover successful commit, exception before event insert, exception after event insert, exception before revision update, duplicate command replay, same command id/different input, and stale expected revision.

```python
first = store.apply_command(command, decide=create_mission_decision)
assert first.revision == 1
assert store.apply_command(command, decide=explode_if_called) == first
with pytest.raises(CommandConflict, match="command input mismatch"):
    store.apply_command(replace(command, payload={"goal": "different"}), decide=noop)
```

- [ ] **Step 2: Run RED**

Expected: `apply_command` and typed conflicts are missing.

- [ ] **Step 3: Implement one transaction boundary**

Add `CommandEnvelope`, `MutationDecision`, and `MutationOutcome`. `BEGIN IMMEDIATE` validates command identity and expected revision, invokes a pure decision callback on a detached snapshot, writes entity changes plus append-only events plus command outcome, increments revision once, and commits. On any exception, roll back every row.

- [ ] **Step 4: Run GREEN and database integrity checks**

Assert `PRAGMA integrity_check == "ok"` after injected failures.

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: make Mission mutations atomic and idempotent`

### Task 5: Add daemon-owned Mission application service

**Files:**
- Create: `src/agentdeck/app/__init__.py`
- Create: `src/agentdeck/app/mission_service.py`
- Create: `tests/integration/test_mission_service.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED public-service tests**

Cover propose version, confirm exact digest, reject stale revision/digest, create Task rows only after confirmation, and preserve Leader proposal as provenance rather than authority.

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Implement `MissionService`**

```python
class MissionService:
    def __init__(self, store: SQLiteMissionStore) -> None:
        self._store = store

    def propose(self, command: CommandEnvelope, proposal: MissionProposal) -> MutationOutcome:
        return self._store.apply_command(
            command, decide=lambda snapshot: propose_mission(snapshot, proposal)
        )

    def confirm(self, command: CommandEnvelope, *, mission_id: str, version: int, digest: str) -> MutationOutcome:
        return self._store.apply_command(
            command,
            decide=lambda snapshot: confirm_mission(
                snapshot, mission_id=mission_id, version=version, digest=digest
            ),
        )

    def cancel(self, command: CommandEnvelope, *, mission_id: str) -> MutationOutcome:
        return self._store.apply_command(
            command, decide=lambda snapshot: cancel_mission(snapshot, mission_id)
        )
```

The service validates DTOs, delegates pure decisions to `domain`, and delegates the single commit to `SQLiteMissionStore`. It has no provider or transport dependency.

- [ ] **Step 4: Run GREEN and regression**

Run new integration tests plus existing mission/daemon mission snapshot tests.

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: add durable Mission application service`

### Task 6: Implement Task, Attempt, Handoff, Evidence, and Verification transitions

**Files:**
- Modify: `src/agentdeck/domain/mission.py`
- Create: `src/agentdeck/domain/verification.py`
- Modify: `src/agentdeck/app/mission_service.py`
- Create: `tests/domain/test_task_attempt_transitions.py`
- Create: `tests/integration/test_fake_worker_handoff.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED transition-table tests**

Prove dependency gating, distinguish retries, terminal absorption, Worker text not completing a Task, Evidence-required verification, Handoff-before-downstream release, duplicate adapter event idempotency, and safest-state precedence for ambiguous effect/permission conflicts.

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Implement pure transitions and adapter-event ingestion**

Expose `start_attempt`, `record_worker_event`, `record_handoff`, `record_evidence`, `verify_task`, and `release_ready_tasks`. Adapter events carry exact MissionVersion/Task/Attempt/session/order/integrity lineage and enter the same store transaction boundary.

- [ ] **Step 4: Run GREEN and M2c-derived deterministic cases**

Reuse invariants, not private M2c helpers or fixed four-stage/count assertions.

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: persist governed Task attempts and handoffs`

### Task 7: Bind the application service to the sole ProjectDaemon mutation loop

**Files:**
- Create: `src/agentdeck/daemon/mission_runtime.py`
- Modify: `src/agentdeck/daemon/service.py`
- Modify: `src/agentdeck/daemon/protocol.py`
- Create: `tests/integration/test_daemon_mission_commands.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED daemon-boundary tests**

Prove client requests cannot hold a store/connection, mutations fail before daemon start/after close, two daemon writers conflict, read-only observation does not grant mutation, and queued commands revalidate revision/authority at execution time.

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Implement the narrow composition seam**

`DaemonMissionRuntime` acquires `ProjectWriterLease`, opens `SQLiteMissionStore`, owns `MissionService`, and maps closed RPC methods (`mission.propose`, `mission.confirm`, `mission.status`, `events.after`) into `ProjectDaemonService.submit_governed_mutation`. Do not expose arbitrary callback or SQL execution over RPC.

- [ ] **Step 4: Run GREEN and daemon regression**

Run new tests plus daemon protocol/service/acceptance focused suites.

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: route Mission writes through ProjectDaemon`

### Task 8: Add event cursor reconnect and dual ProjectView projections

**Files:**
- Create: `src/agentdeck/projections/__init__.py`
- Create: `src/agentdeck/projections/project_view.py`
- Modify: `src/agentdeck/models.py`
- Modify: `docs/contracts/project-view-schema.md`
- Create: `tests/integration/test_project_view_v2.py`
- Create: `tests/integration/test_event_reconnect.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED projection tests**

Require v1 and v2 to report the same project revision/authority generation, monotonic cursor, Mission/Task/Attempt/Handoff/Evidence summaries, bounded fields, and no raw prompt/transcript/secret. Reconnect after cursor N returns only later events, then a coherent snapshot.

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Implement projections from one read transaction**

Add `PROJECT_VIEW_V2_SCHEMA_VERSION = "project-view/v2"` without changing the existing v1 constant. `ProjectViewProjection.snapshot(version)` and `events_after(cursor, limit)` use read-only SQLite connections and one coherent transaction; compatibility v1 never reads legacy JSON after SQLite activation.

- [ ] **Step 4: Run GREEN and existing contract/workbench tests**

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: project Mission state for reconnecting clients`

### Task 9: Reconcile daemon restart, leases, and ambiguous effects

**Files:**
- Create: `src/agentdeck/app/recovery_service.py`
- Modify: `src/agentdeck/daemon/mission_runtime.py`
- Create: `tests/integration/test_sqlite_recovery.py`
- Create: `tests/integration/test_daemon_restart.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED crash-matrix tests**

Inject crashes before intent, after intent/before dispatch observation, after known outcome, during Handoff, and after terminal acceptance. Prove terminal absorption, safe resume only for zero-effect/idempotent cases, ambiguous effect pause, lease loss not equal failure, and consume-once internal triggers.

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Implement deterministic recovery**

`RecoveryService.reconcile(snapshot)` returns typed decisions only. `DaemonMissionRuntime.start()` runs recovery through the sole mutation loop before accepting commands. It never calls a model or infers truth from process/tmux text.

- [ ] **Step 4: Run GREEN plus existing daemon crash matrix**

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: recover durable Missions after daemon restart`

### Task 10: Implement read-only legacy Migration Preview and verified import

**Files:**
- Create: `src/agentdeck/storage/legacy_import.py`
- Create: `src/agentdeck/storage/authority.py`
- Create: `tests/storage/test_legacy_preview.py`
- Create: `tests/storage/test_legacy_import.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED preview/import tests**

Cover canonical project containment, symlink/non-regular/permission rejection, deterministic inventory digest, malformed JSON/JSONL, duplicate/dangling identity, zero preview writes, full structured collection coverage, and opaque `legacy_records` preservation for P2/P5-owned records.

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Implement preview and off-path importer**

`LegacyMigration.preview(root)` is strictly read-only. `build_candidate(preview, backup, lease)` requires unchanged digest, copies every structured control-plane collection into native tables or `legacy_records`, preserves event order/hash/provenance, and produces a self-contained closed candidate database. It does not activate it.

- [ ] **Step 4: Run GREEN and existing migration tests**

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: import legacy state into SQLite candidate`

### Task 11: Implement fsync cutover, three-state activation, and guarded rollback

**Files:**
- Create: `src/agentdeck/app/migration_service.py`
- Modify: `src/agentdeck/storage/legacy_import.py`
- Modify: `src/agentdeck/storage/authority.py`
- Modify: `src/agentdeck/daemon/mission_runtime.py`
- Create: `tests/storage/test_migration_cutover.py`
- Create: `tests/storage/test_migration_rollback.py`
- Create: `tests/integration/test_daemon_migration_commands.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED filesystem failure-injection tests**

Inject backup file/manifest/directory fsync, candidate checkpoint/close/fsync, atomic replace, containing-directory fsync, activation transaction, restore image, SQLite retirement, and rollback directory fsync failures. Every point must yield exactly one known mutation authority or zero serving writers in quarantine.

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Implement the approved state machine**

Implement `legacy_active -> sqlite_installed_quarantined -> sqlite_active`. The daemon maintenance path seals an owner-only raw backup, installs a self-contained candidate, fsyncs the directory, then performs one identity-bound activation transaction. Rollback is allowed only at the cutover watermark before any product write; it restores off-path, fsyncs, durably retires SQLite, and reactivates exact legacy generation. No force bypass.

`MigrationService` exposes closed `preview`, `confirm(preview_digest)`,
`verify`, and `rollback(cutover_id)` application methods. Preview is read-only;
the other methods run only while `DaemonMissionRuntime` holds the writer lease
and exclusive maintenance mode. The daemon RPC surface maps only those four
methods and never exposes arbitrary filesystem or SQL parameters.

- [ ] **Step 4: Run GREEN on real temporary filesystems**

Do not mock `os.replace`/`fsync` for happy paths; use injectable failure hooks only to stop at exact boundaries.

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: activate and rollback SQLite authority safely`

### Task 12: Fail closed every legacy mutator after SQLite activation

**Files:**
- Modify: `src/agentdeck/state.py`
- Modify: `src/agentdeck/daemon/service.py`
- Create: `tests/integration/test_legacy_mutation_guard.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write RED compatibility tests**

Enumerate every public `StateStore` mutator. Before cutover it behaves byte-for-byte as today; in quarantine it refuses all ordinary reads/writes with migration recovery guidance; after `sqlite_active` every legacy mutator refuses with `sqlite authority requires ProjectDaemon`, while approved compatibility reads are served from SQLite projections.

- [ ] **Step 2: Run RED**

- [ ] **Step 3: Add one central authority guard**

Use `StateAuthority.detect(root)` at the existing mutation wrapper and ProjectView entry seam. Do not patch hundreds of methods individually and do not create a fallback writer.

- [ ] **Step 4: Run GREEN and full legacy focused suites**

Run state, conversation, approval, skills/memory, contracts, and daemon focused tests to prove pre-cutover compatibility.

- [ ] **Step 5: Update HISTORY and commit**

Commit: `feat: guard legacy state after SQLite cutover`

### Task 13: Prove the deterministic fake Golden and freeze the P1 exit

**Files:**
- Create: `tests/integration/test_p1_fake_golden.py`
- Create: `tests/fixtures/fake_mission_worker.py`
- Modify: `src/agentdeck/daemon/mission_runtime.py`
- Create: `docs/validation/2026-07-18-p1-durable-mission-kernel.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `docs/superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md`
- Modify: this plan
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the RED fake Golden**

Through public daemon APIs only: create version, confirm exact digest, dispatch fake Worker, record Evidence/Handoff, close client, reconnect by cursor, crash/restart daemon, reject stale/unauthorized command, resume safely, verify accepted completion, and compare v1/v2 revision/authority. No direct store calls in the journey test.

- [ ] **Step 2: Run RED, then add the deterministic fake adapter composition**

Create a test-only `FakeMissionWorker` that consumes the frozen Task envelope
and submits typed `started`, `evidence`, `handoff`, and `completed` events only
through `DaemonMissionRuntime.submit_adapter_event()`. Add that closed public
method to `mission_runtime.py` if the RED proves it is absent; it must validate
lineage and queue the event through the daemon mutation loop. Do not add
provider, ACP, CLI/PTY, tmux, or live behavior to make the fake Golden pass.

- [ ] **Step 3: Run P1 focused verification**

Run serially: domain, storage, integration, existing mission/daemon/conversation compatibility, compileall, then full default pytest with all live gates unset. Record exact counts/times/skips.

- [ ] **Step 4: Write validation evidence and self-review P1**

Document schema, migration/cutover/rollback failure matrix, fake Golden steps, restart evidence, scope, exact commands, and known P2/P3 locks. Run spec compliance and code quality reviews; fix every finding and re-run affected verification.

- [ ] **Step 5: Update handoff/program/HISTORY and commit**

Mark P1 executed/evidence frozen but leave human P1 exit approval and P2 planning unchecked. Commit: `feat: complete AgentDeck P1 durable Mission kernel`.

## Plan self-review checklist

- [x] Every P1 required slice has a task and named test owner.
- [x] Every production step follows a witnessed RED.
- [x] SQLite has one daemon writer and no client/adapter SQL path.
- [x] Migration is previewed, backup-bound, fsync-safe, explicit, reversible only before new writes, and never dual-write.
- [x] P1 fake Golden uses public daemon APIs and no real adapter/tmux.
- [x] ProjectView v1/v2 share one SQLite revision/authority.
- [x] P2/P3/P4 work, live nodes, merge, and push remain locked.
- [x] Every task updates HISTORY and creates one focused commit.
