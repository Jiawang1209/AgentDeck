# Task 15B Project Pause, ACP Cancellation, and Explicit Resume Design

**Status:** Revised design approved in conversation; written-spec review pending

**Date:** 2026-07-20

**Authority:** This document is a narrow normative correction to sections
5.2, 10.4, and 10.4.3 of the approved AgentDeck Product Kernel Rewrite Design.
It does not change the 39-task order, open post-MVP ACP resume work, or
authorize implementation before the written spec and revised TDD plan are
reviewed.

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

`AsyncExitCoordinator.confirm()` is the sole active-confirm path:

1. Read a completed `exit:confirm:<request-id>` command first. A closed exact
   result is returned before inspecting a now-cleared pending request.
2. If no completed command exists, validate the current pending request and
   eight-field Attempt snapshot.
3. Resolve the exact `ActiveExecutionBinding`. Drift fails with zero Worker I/O
   and zero Store writes.
4. Outside a Store transaction, call exactly:

   ```python
   await binding.worker.cancel_task(
       binding.worker_handle,
       reason="product_exit_confirmed",
   )
   ```

5. After successful bounded cancellation, enter one `execute_once` callback,
   re-read ProductSession, request, Attempt, Agent, ACP session, and full handle
   lineage, and compare the complete durable authority again.
6. Only an exact match may atomically change the Attempt to `interrupted`,
   change the ProductSession to `paused`, clear all five pending-exit fields,
   append `attempt_interrupted`, `project_paused`, and `exit_confirmed`, and
   save the closed completed command result.

If authority changed after cancellation, AgentDeck returns
`exit_authority_changed_after_cancel`, keeps the request, never claims the
Attempt was interrupted, and stores only the closed diagnostic command result.
Cancellation rejection, timeout, disconnect, or uncertain owner shutdown does
the same, while the still-present pending-exit group keeps the dispatch gate
closed. These first outcomes are replay authority: exact replay performs no
second Worker I/O. Unknown request ID, wrong hash, missing pending Attempt, or
malformed authority creates no command row. Any callback failure rolls back
all database mutations.

If the current Worker commits its terminal Handoff before confirmation, the
old Attempt-bound request cannot cancel it or claim interruption. The pending
stop intent still prevents the next stage from starting. A subsequent `/exit`
revalidates the new between-stage state and atomically pauses the project with
all committed Handoff and Evidence intact.

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
- `src/agentdeck/product/bootstrap.py`
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
- Modify `src/agentdeck/application/exit_records.py` for closed results and
  diagnostics.
- Modify `tests/product_kernel/test_acp_worker_failures.py`.
- Modify `tests/product_kernel/test_acp_worker_connection.py`.
- Modify `tests/product_kernel/test_kernel_session.py`.
- Create `tests/product_kernel/test_sqlite_execution_resume.py`.
- Create `tests/product_kernel/test_execution_resume.py`.
- Create `tests/product_kernel/test_project_lifecycle_service.py`.
- Modify `tests/product_kernel/test_execution_coordinator.py`.
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
- cancel racing normal terminal persistence fails the final CAS safely;
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
5. `feat: require explicit resume after product reentry`
   - single async ProductShell loop;
   - mandatory startup recovery;
   - observational paused startup;
   - explicit resume child-task creation and crash convergence.

Each slice records its own RED, reaches focused GREEN, updates `HISTORY.md`,
self-reviews, and creates one local commit. No slice is pushed or merged. The
final integrated HEAD alone is eligible for the two full suites and independent
Task 15B reviews.
