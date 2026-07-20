# Task 15B Project Pause, ACP Cancellation, and Explicit Resume Design

> **Document role:** Historical/execution appendix — not an independent source
> of truth. Canonical authority remains the
> [Product Kernel Rewrite Design](../../specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md)
> and the
> [Product Kernel Rewrite TDD plan](../../plans/2026-07-18-agentdeck-product-kernel-rewrite.md).
> Read this appendix only where those canonical documents explicitly invoke
> the Task 15B correction.

**Status:** Approved execution appendix; non-canonical

**Date:** 2026-07-20

**Authority:** This appendix records the approved narrow Task 15B correction to
sections 5.2, 10.4, and 10.4.3 of the canonical Product Kernel Rewrite Design.
It cannot independently create requirements or override the canonical design
and plan. It does not change the 39-task order, open post-MVP ACP resume work,
or authorize implementation beyond the canonical Task 15B gate.

## 1. Problem and decision

Task 15B must make `/exit` a project-wide safe pause, bind any current Worker
stop to real ACP cancellation, require explicit `/resume`, and run mandatory
recovery before the first foreground prompt. `/exit` does not mean "cancel one
Worker". It means "stop this project's foreground execution and close
AgentDeck without losing durable progress."

The original plan assumed two capabilities that the current Product Kernel
does not have:

1. ACP `session/cancel` is a notification. It has no independent remote
   business acknowledgement.
2. The Store preserves a derived `WorkerHandle.session_id`, not the raw ACP
   session identifier or a validated resume token. The raw session identity
   exists only in the live Worker process.

AgentDeck must not turn either absence into a guessed success. Task 15B adopts
the conservative foreground-first design:

- a durable exit request is a project-wide stop intent and closes the dispatch
  gate before another stage may start;
- a live, exact in-process binding may be cancelled;
- successful stop changes the ProductSession to `paused`;
- restart without the binding never claims ACP reconnection or automatically
  restarts work;
- no-effect abandoned work becomes `interrupted` and the project remains
  `paused`;
- effect-observed abandoned work becomes `outcome_unknown` and the project
  remains `paused`;
- explicit `/resume` starts a new Attempt at the first unclosed stage using
  already committed Handoff and Evidence authority;
- true cross-process ACP resume is a later schema-and-adapter slice.

This is the approved **方案 A**. It closes R2 honestly without pulling a
background/resume subsystem into the MVP.

## 2. Cancellation success and failure

`cancel_task()` succeeds only when both of these bounded operations succeed:

1. the ACP cancel notification is written within the configured deadline;
2. the AgentDeck-owned ACP connection and local adapter process are closed and
   reaped within the configured deadline.

Success means AgentDeck has terminated its locally managed execution channel.
It does not mean the remote Agent returned a second acknowledgement, and it
does not prove that no earlier file or network effect occurred.

The Worker Port exposes one content-free failure value:

```python
class WorkerCancellationError(RuntimeError):
    ALLOWED_CODES = frozenset({
        "cancel_rejected",
        "cancel_timeout",
        "transport_disconnected",
    })

    def __init__(self, *, code: str, outcome_known: bool) -> None: ...
```

It stores only the allowlisted code and exact bool `outcome_known`. It never
stores exception text, stderr, protocol frames, prompts, terminal output,
paths, environment values, credentials, or model prose.

`ACPWorkerConnection` owns bounded notification send and bounded local owner
shutdown. `ACPWorker` maps that transport outcome into
`WorkerCancellationError`; neither component writes SQLite or decides the
Attempt state. A timeout, disconnect, rejected cancellation, or uncertain
shutdown never permits AgentDeck to claim `interrupted`.

## 3. Exact foreground execution binding

`ForegroundExecutionRuntime` owns only the current event-loop-local binding:

```python
@dataclass(frozen=True)
class ActiveExecutionBinding:
    attempt_id: str
    task_id: str
    agent_instance_id: str
    acp_session_id: str
    worker_handle: WorkerHandle
    worker: Worker
```

The runtime binds only after the returned Worker handle and durable ACP
session binding are validated. It releases only after terminal Attempt and
Handoff persistence. Bind rejects duplicate or drifted Attempt, Task, Agent,
ACP session, full typed handle, Worker, and event-loop ownership.

Exact resolution compares every shared lineage field. There is no fallback by
backend, model, role, tmux pane, process name, or latest Worker. Release is
exact; replay is idempotent only for the same already-released handle.

`ExecutionService`, Worker, `AsyncExitCoordinator`, `RecoveryService`, and
`ProductShell` share one foreground event loop. Mission execution is a child
task of that loop. Only the outer product entrypoint may call `asyncio.run()`.

`ProjectLifecycleService` separately owns the durable dispatch gate. A
ProductSession is dispatchable only when its state is exactly `running` and its
pending-exit group is all null. `ExecutionService` checks that gate immediately
before every new Worker start. Persisting an exit request therefore freezes the
whole project even while the current Worker awaits human confirmation; it is
not merely a request to stop one process.

## 4. Async exit transaction

`AsyncExitCoordinator` is the sole project-exit path. Request persists the
project-wide stop intent. A request made between stages, when there is no live
Attempt but a confirmed Mission is incomplete, may pause the ProductSession
without sending ACP. A request made during an active Attempt requires exact
confirmation and cancellation.

`AsyncExitCoordinator.confirm()` is the sole active-confirm path. Its durable
command identity is scoped to the coordinator ProductSession as
`exit:confirm:<session-id>:<request-id>`. Before accepting a replay it also
reconstructs the original completed exit-request command and validates the
canonical request ID, Attempt hash, Attempt ID, request time, and current
session lineage. A command from another ProductSession, a forged Attempt ID,
or any canonical drift fails closed.

Active confirmation follows this exact order:

1. Under the project stop lease, read and validate the session-scoped completed
   confirmation and original request commands. Durable replay never invokes a
   Worker.
2. If no completed confirmation exists, validate the current pending request,
   eight-field Attempt snapshot, and complete `ActiveExitAuthority`.
3. Claim one exact in-process cancellation fence before Worker I/O. The fence
   lease is a non-copyable object-identity capability bound to the exact
   request key, full Worker handle, runtime binding, and foreground event loop;
   only that lease may close or settle it. The runtime owns at most one bounded
   foreground exit fence.
4. Outside a Store transaction, call at most once:

   ```python
   await lease.worker.cancel_task(
       lease.worker_handle,
       reason="product_exit_confirmed",
   )
   ```

5. Close the fence with either success or one allowlisted, content-free outcome.
   `WorkerCancellationError` is preserved only as its validated code and
   `outcome_known`; any other ordinary exception becomes
   `transport_disconnected/outcome_known=false`. On `asyncio.CancelledError`,
   close the fence as that unknown outcome before re-raising. No exception
   message, traceback, prompt, path, frame, output, or credential is retained.
6. After successful bounded cancellation, enter one `execute_once` callback,
   re-read ProductSession, request, Attempt, Agent, ACP session, and full handle
   lineage, and compare the complete durable authority again.
7. Only an exact match may atomically change the Attempt to `interrupted`,
   change the ProductSession to `paused`, clear all five pending-exit fields,
   append `attempt_interrupted`, `project_paused`, and `exit_confirmed`, and
   save the closed completed command result.
8. Only after that durable success may the exact fence settle to an idle
   runtime. `should_exit=true` requires either successful exact settlement or
   a pristine fresh-process runtime with no live owner. Any active, reserved,
   quarantined, cancelling, or fenced-pending owner blocks success.

If authority changed after cancellation, AgentDeck returns
`exit_authority_changed_after_cancel`, keeps the request, never claims the
Attempt was interrupted, and stores only the closed diagnostic command result.
Cancellation rejection, timeout, disconnect, or uncertain owner shutdown does
the same, while the still-present pending-exit group keeps the dispatch gate
closed. After its diagnostic command is durable, the exact fence settles to a
quarantined live owner; `/decline` must reject that request, so cancellation
cannot be followed by reopening dispatch. Recovery of that quarantine belongs
to Task 15B.5.

These first outcomes are replay authority: exact same-process replay consumes
the closed fence outcome and performs no second Worker I/O. If any
`execute_once` callback, event append, or commit fails after cancellation, the
coordinator returns only non-durable `exit_persistence_pending` with
`outcome_known=false` and `should_exit=false`, retains the fence, and exposes
no exception content. Retry repeats persistence only. A successful durable
command whose exact fence cannot settle returns a content-free convergence
failure with `should_exit=false`, retains the fence, and retries settlement
without Worker I/O. Unknown request ID, wrong hash, missing pending Attempt, or
malformed authority creates no command row.

The fence is deliberately in-process rather than durable schema authority. A
crash after cancellation and before command persistence destroys the ACP
process/channel and the fence; mandatory Task 15B.5 startup recovery observes
the running Attempt without an exact current-process binding and conservatively
pauses it. A crash after durable command commit and before settlement replays
the command against a pristine runtime. Task 15B does not claim cross-process
continuation of the old Worker binding.

If the current Worker commits its terminal Handoff before confirmation, the
old Attempt-bound request cannot cancel it or claim interruption. The pending
stop intent still prevents the next stage from starting. A subsequent `/exit`
revalidates the new between-stage state and atomically pauses the project with
all committed Handoff and Evidence intact.

Between-stage pause additionally requires `ForegroundExecutionRuntime` to
report no live owner. Active, reserved, quarantined, cancelling, and
fenced-pending ownership all count as live; bounded history of used/released
objects does not. The coordinator never uses a coarse empty-history predicate
as runtime authority.

## 5. Mandatory restart recovery

Bootstrap constructs an empty `ForegroundExecutionRuntime` and awaits
`RecoveryService.reconcile()` before the first `read_line` and before starting
a new Mission child task.

A fresh process cannot prove an old live ACP binding because the current
schema has no raw resume authority. Therefore every database `running` Attempt
without an exact current-process binding is classified conservatively:

- `effect_observed=False` -> transport `LOST` -> Attempt `interrupted` and
  ProductSession `paused`;
- `effect_observed=True` -> Attempt `outcome_unknown` and ProductSession
  `paused`, even when transport is `LOST`.

Recovery starts no Worker, sends no ACP request, reads no tmux pane, and never
matches by backend, role, process, or latest value. Classification occurs
outside the Store transaction; persistence revalidates the complete durable
fingerprint in an idempotent command transaction. Recovery cannot be a no-op
production default.

A ProductSession found `running` after restart never authorizes automatic
execution. If no exact live binding exists, recovery converges it to `paused`
whether the crash happened during a Worker or immediately after a prior
`/resume` command and before Worker start. The first prompt shows the paused
project and exact recovery point. Only a new explicit `/resume` may reopen the
dispatch gate.

True cross-process ACP resume requires a future independent design that first
defines secure durable raw resume authority, adapter/model/argv provenance,
capability negotiation, expiry, and exact resume validation. Task 15B neither
implements nor claims that behavior.

## 6. Derived execution resume authority

Task 15B adds no cursor table and performs no schema migration. A read-only
`ExecutionResumeSnapshot` is derived from the existing ProductSession,
confirmed Mission, ordered Tasks, Attempts, Handoffs, Evidence, and completed
command facts. It contains:

- ProductSession identity and exact `paused` state;
- frozen Mission identity, version, canonical content hash, and ordered graph;
- each closed stage's terminal Attempt, Handoff, and Evidence references;
- the first Task without a closed stage bundle;
- that Task's maximum prior Attempt ordinal and the next ordinal;
- the exact preceding Handoff, when the graph requires one;
- a canonical hash over the complete projection.

A stage is complete only when its terminal Attempt and required Handoff and
Evidence form one validated closed bundle. Worker prose, a terminal-looking
event, an orphan Attempt, or a Task row alone is not completion. An
`interrupted` Attempt is audit history and permits a higher-ordinal retry.
`outcome_unknown` is a hard resume blocker because repeating the stage could
duplicate an unproven effect. A completed acceptance stage means the Mission
cannot resume.

Mission, task order, dependencies, role/backend/Agent identity, Attempt
ordinal, Handoff target, Evidence lineage, or command authority drift makes the
projection invalid. Invalid projection returns a content-free diagnostic with
zero Worker I/O and zero project-file effects.

The Application `ExecutionResumePlanner` converts the strict projection into
typed prior Attempts, Evidence, Handoffs, authoritative revision facts, the
remaining Task suffix, and the first new ordinal. `ExecutionService` consumes
that plan; it never scans tables or guesses the current stage itself.

## 7. Explicit project resume

`/resume` is a mutating project command, not the existing read-only session
projection named `resume()`. It is accepted only for an exact `paused`
ProductSession with a valid `ExecutionResumeSnapshot` and no pending exit.

One command transaction revalidates the snapshot hash, changes the session to
`running`, appends exactly one `project_resumed` event, and records a closed
result. After commit, `ProductShell` creates one Mission child task on the same
foreground loop. Execution starts with the first unclosed Task and a new
higher-ordinal Attempt. Closed earlier stages are supplied as immutable
context and never re-dispatched.

If the process crashes after the resume transaction but before Worker start,
the next bootstrap sees a running session without a live binding and converges
it to `paused`; it does not infer that the previous command should launch work.
If it crashes after terminal Handoff persistence, that stage remains closed
and the next snapshot advances. If it crashes while effects may have occurred,
recovery produces `outcome_unknown` and explicit `/resume` remains blocked.

Setup, drafting, and awaiting-confirmation sessions have no executing Worker.
Plain `/exit` may close their foreground interface while preserving their
current state; they do not require a synthetic `paused` transition. The
project-level pause rule applies once a confirmed Mission can execute.

## 8. Components and file boundary

The original Task 15B files remain authorized:

- `src/agentdeck/ports/worker.py`
- `src/agentdeck/application/execution_runtime.py`
- `src/agentdeck/application/execution_service.py`
- `src/agentdeck/application/recovery_service.py`
- `src/agentdeck/application/exit_service.py`
- `src/agentdeck/product/shell.py`
- `src/agentdeck/product/shell_projection.py` (approved pure helper extraction
  for the 500-line ProductShell boundary)
- `src/agentdeck/product/bootstrap.py`
- `tests/product_kernel/test_project_resume_replay.py` (focused replay
  generation regression extracted to preserve the 500-line test boundary)
- `tests/product_kernel/test_execution_runtime.py`
- `tests/product_kernel/test_product_exit_acp_integration.py`
- `tests/product_kernel/test_product_reentry.py`
- `tests/product_kernel/test_recovery_service.py`
- `HISTORY.md`

The corrected project-level contract also requires these exact files:

- Modify `src/agentdeck/kernel/session.py` for project pause transitions.
- Modify `src/agentdeck/adapters/acp.py` for Worker cancellation mapping.
- Modify `src/agentdeck/adapters/acp_worker_connection.py` for bounded cancel
  send and owner shutdown.
- Modify `src/agentdeck/ports/store.py` for the typed resume projection query.
- Create `src/agentdeck/ports/execution_resume.py` for bounded immutable resume
  facts.
- Modify `src/agentdeck/adapters/sqlite.py` only to delegate the new read path.
- Create `src/agentdeck/adapters/sqlite_execution_resume.py` for strict
  read-only derivation from existing tables.
- Create `src/agentdeck/application/execution_resume.py` for pure resume-plan
  validation and materialization.
- Create `src/agentdeck/application/project_lifecycle_service.py` for the
  durable pause/resume gate.
- Create `src/agentdeck/application/async_exit_coordinator.py` for the async
  project-stop protocol.
- Create `src/agentdeck/application/exit_cancellation.py` for immutable,
  bounded cancellation keys/outcomes and the opaque runtime lease contract.
- Modify `src/agentdeck/application/exit_records.py` for closed results and
  diagnostics.
- Modify `tests/product_kernel/test_acp_worker_failures.py`.
- Modify `tests/product_kernel/test_acp_worker_connection.py`.
- Modify `tests/product_kernel/test_kernel_session.py`.
- Create `tests/product_kernel/test_sqlite_execution_resume.py`.
- Create `tests/product_kernel/test_execution_resume.py`.
- Create `tests/product_kernel/test_project_lifecycle_service.py`.
- Modify `tests/product_kernel/test_execution_coordinator.py`.
- Modify `tests/product_kernel/test_execution_runtime.py` for fence identity,
  bounded ownership, settlement, and live-owner semantics.
- Modify `tests/product_kernel/test_sqlite_recovery_integrity.py`.
- Modify `tests/product_kernel/test_product_shell.py`.
- Modify `tests/product_kernel/test_product_preview_flow.py`.

Every source and test file remains at most 500 lines. The already-full
`execution_service.py` may contain only thin calls into the runtime, dispatch
gate, and prevalidated resume plan; it may not derive resume authority itself.
Active-binding logic belongs in `execution_runtime.py`, and resume
materialization belongs in `execution_resume.py`. Async exit logic belongs in
the new coordinator rather than expanding `exit_service.py`.

The slice must not import or modify legacy CLI, daemon/background recovery,
ConversationSession, Router, JSON/JSONL authority, tmux/PTY transport, pane
capture, prompt-injection fallback, or the M2c live harness. Any further file
boundary conflict is another STOP and written-plan correction, not implicit
scope expansion.

## 9. Deterministic TDD acceptance

The revised Task 15B plan must prove at least:

- exact successful cancel, single interruption commit, and exact replay;
- cancel rejected, notification timeout, disconnect, and owner-reap
  timeout/failure with no false interruption;
- exception text, paths, frames, prompts, credentials, and raw output never
  enter Port errors, Diagnostics, command results, or events;
- handle, ACP session, Worker, event-loop, and durable-authority drift produce
  zero unintended I/O or writes;
- authority drift after cancel never commits interruption or cancels twice;
- cancellation persistence failure, event failure, commit failure, unexpected
  Worker exception, and caller cancellation retain one closed fence outcome so
  every retry performs zero additional Worker I/O;
- exact success settles only its non-copyable lease; foreign/copied keys,
  handles, reservations, quarantine, and bindings cannot clear the fence;
- replay is ProductSession-scoped, closes against the original canonical
  request snapshot, remains byte-stable under an advancing clock, and rejects
  forged Attempt identity;
- mission versions outside `1..9223372036854775807`, whitespace/control-byte
  Attempt identity, and malformed authority fail before Worker I/O;
- cancel racing normal terminal persistence uses the production atomic terminal
  Attempt/Evidence/Handoff bundle and runtime release, fails the final CAS
  safely, and preserves every artifact and content hash;
- fresh-process empty runtime classifies no-effect work as `interrupted` and
  effect-observed work as `outcome_unknown`, and pauses the project;
- recovery performs no backend/role/pane/latest fallback;
- recovery completes before the first input read;
- shell, Mission child task, Worker, and exit coordinator share one loop;
- no nested `asyncio.run()` exists in Product Kernel code;
- a persisted exit request blocks every later Worker dispatch;
- exit between stages pauses without sending ACP;
- active exit atomically commits Attempt interruption and ProductSession pause;
- paused re-entry starts zero Leader, Worker, or ACP operations before explicit
  `/resume`;
- resume derives the first unclosed stage from SQLite and never repeats a
  closed stage;
- interrupted stage resume creates a higher-ordinal Attempt with exact prior
  Handoff and Evidence;
- `outcome_unknown` and malformed resume projections block with zero Worker
  I/O;
- a crash after resume commit but before Worker start reconverges to `paused`;
- project pause/resume commands and events replay without duplicate Worker,
  Attempt, or event creation.

Completion requires recorded RED evidence, minimal GREEN, focused ACP/exit/
recovery integration, Product Kernel full, legacy full excluding Product
Kernel, compileall, diff check, forbidden-import scan, 500-line gate, clean
worktree, and independent spec and quality approvals. `HISTORY.md` is committed
with implementation. R2 closes only after all evidence passes; only then may
the implementation sequence advance to Task 27.

## 10. Implementation and commit slices

The revised TDD plan must preserve this reviewable order:

1. `feat: add durable project resume projection`
   - ProductSession pause/resume transitions;
   - immutable Store Port facts;
   - strict SQLite derivation;
   - pure resume planner.
2. `feat: close bounded acp project cancellation`
   - content-free cancellation error;
   - bounded notification send;
   - bounded connection/process owner shutdown.
3. `feat: resume execution from committed handoff`
   - foreground runtime binding;
   - dispatch gate;
   - prior closed-stage materialization;
   - higher-ordinal retry at the first unclosed stage.
4. `feat: make exit a project pause transaction`
   - project-wide stop intent;
   - exact active cancellation;
   - between-stage pause;
   - command-atomic Attempt, Session, event, and replay facts.
5. `docs: close task15b exit cancellation gap`
   - non-copyable cancellation fence and settlement semantics;
   - session-scoped canonical replay and stable diagnostics;
   - exact same-process retry and Task 15B.5 crash boundary.
6. `fix: close task15b exit cancellation gap`
   - bounded runtime fence and live-owner convergence;
   - zero-I/O persistence retry and decline quarantine;
   - production terminal-bundle race acceptance.
7. `feat: require explicit resume after product reentry`
   - single async ProductShell loop;
   - mandatory startup recovery;
   - observational paused startup;
   - explicit resume child-task creation and crash convergence.

Each slice records its own RED, reaches focused GREEN, updates `HISTORY.md`,
self-reviews, and creates one local commit. No slice is pushed or merged. The
final integrated HEAD alone is eligible for the two full suites and independent
Task 15B reviews.
