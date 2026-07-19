# Task 15B ACP Cancellation and Recovery Design

**Status:** Approved in conversation; written-spec review pending

**Date:** 2026-07-20

**Authority:** This document is a narrow normative correction to sections
10.4 and 10.4.3 of the approved AgentDeck Product Kernel Rewrite Design. It
does not change the 39-task order, open post-MVP ACP resume work, or authorize
implementation before the written spec and revised TDD plan are reviewed.

## 1. Problem and decision

Task 15B must bind product exit to a real ACP Worker cancellation and run
mandatory recovery before the first foreground prompt. The original plan
assumed two capabilities that the current Product Kernel does not have:

1. ACP `session/cancel` is a notification. It has no independent remote
   business acknowledgement.
2. The Store preserves a derived `WorkerHandle.session_id`, not the raw ACP
   session identifier or a validated resume token. The raw session identity
   exists only in the live Worker process.

AgentDeck must not turn either absence into a guessed success. Task 15B adopts
the conservative foreground-first design:

- a live, exact in-process binding may be cancelled;
- restart without that binding never claims ACP reconnection;
- no-effect abandoned work becomes `interrupted`;
- effect-observed abandoned work becomes `outcome_unknown`;
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

## 4. Async exit transaction

`AsyncExitCoordinator.confirm()` is the sole real-confirm path:

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
   clear all five pending-exit fields, append `attempt_interrupted` and
   `exit_confirmed`, and save the closed completed command result.

If authority changed after cancellation, AgentDeck returns
`exit_authority_changed_after_cancel`, keeps the request, never claims the
Attempt was interrupted, and stores only the closed diagnostic command result.
Cancellation rejection, timeout, disconnect, or uncertain owner shutdown does
the same. These first outcomes are replay authority: exact replay performs no
second Worker I/O. Unknown request ID, wrong hash, missing pending Attempt, or
malformed authority creates no command row. Any callback failure rolls back
all database mutations.

## 5. Mandatory restart recovery

Bootstrap constructs an empty `ForegroundExecutionRuntime` and awaits
`RecoveryService.reconcile()` before the first `read_line` and before starting
a new Mission child task.

A fresh process cannot prove an old live ACP binding because the current
schema has no raw resume authority. Therefore every database `running` Attempt
without an exact current-process binding is classified conservatively:

- `effect_observed=False` -> transport `LOST` -> Attempt `interrupted`;
- `effect_observed=True` -> Attempt `outcome_unknown`, even when transport is
  `LOST`.

Recovery starts no Worker, sends no ACP request, reads no tmux pane, and never
matches by backend, role, process, or latest value. Classification occurs
outside the Store transaction; persistence revalidates the complete durable
fingerprint in an idempotent command transaction. Recovery cannot be a no-op
production default.

True cross-process ACP resume requires a future independent design that first
defines secure durable raw resume authority, adapter/model/argv provenance,
capability negotiation, expiry, and exact resume validation. Task 15B neither
implements nor claims that behavior.

## 6. Components and file boundary

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

The corrected contract also requires these exact files:

- Modify `src/agentdeck/adapters/acp.py` for Worker cancellation mapping.
- Modify `src/agentdeck/adapters/acp_worker_connection.py` for bounded cancel
  send and owner shutdown.
- Create `src/agentdeck/application/async_exit_coordinator.py` for the async
  confirmation protocol.
- Modify `src/agentdeck/application/exit_records.py` for closed results and
  diagnostics.
- Modify `tests/product_kernel/test_acp_worker_failures.py`.
- Modify `tests/product_kernel/test_acp_worker_connection.py`.
- Modify `tests/product_kernel/test_execution_coordinator.py`.
- Modify `tests/product_kernel/test_sqlite_recovery_integrity.py`.

Every source and test file remains at most 500 lines. The already-full
`execution_service.py` may contain only thin runtime registration and release
calls; active-binding logic belongs in `execution_runtime.py`. Async exit logic
belongs in the new coordinator rather than expanding `exit_service.py`.

The slice must not import or modify legacy CLI, daemon/background recovery,
ConversationSession, Router, JSON/JSONL authority, tmux/PTY transport, pane
capture, prompt-injection fallback, or the M2c live harness. Any further file
boundary conflict is another STOP and written-plan correction, not implicit
scope expansion.

## 7. Deterministic TDD acceptance

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
  effect-observed work as `outcome_unknown`;
- recovery performs no backend/role/pane/latest fallback;
- recovery completes before the first input read;
- shell, Mission child task, Worker, and exit coordinator share one loop;
- no nested `asyncio.run()` exists in Product Kernel code.

Completion requires recorded RED evidence, minimal GREEN, focused ACP/exit/
recovery integration, Product Kernel full, legacy full excluding Product
Kernel, compileall, diff check, forbidden-import scan, 500-line gate, clean
worktree, and independent spec and quality approvals. `HISTORY.md` is committed
with implementation. R2 closes only after all evidence passes; only then may
the implementation sequence advance to Task 27.
