# AgentDeck Observer IPC and Takeover Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every Critical and Important Task 29 specification-review issue by wiring a real tmux Observer acknowledgement channel and making takeover, return-control, automatic cancellation, and explicit exit durably correct.

**Architecture:** A new Product Kernel-native Application Broker publishes only decoded `WorkerEvent` values over a project-isolated Unix socket. The `agentdeck observer` process renders through the existing Task 28 `ObserverStream` and returns an acknowledgement only after sink emission; an Application writer alone persists the shared Ports cursor. Takeover uses Store-backed ownership cycles, typed Git project evidence, outcome reconciliation, and an exact runtime release signal.

**Tech Stack:** Python 3.12 standard library, asyncio Unix sockets, SQLite aggregate/command Store, dataclasses/Protocols, pytest in the `agentdeck` conda environment, tmux argv plans.

---

## Preconditions and authority

- Worktree: `/Users/liuyue/.config/superpowers/worktrees/multi-agent-explore/codex/product-kernel-rewrite`
- Branch: `codex/product-kernel-rewrite`
- Initial Task 29 implementation: `f917426fd6ed3ef915cc51f92f59d05e693bc4e5`
- Approved closure spec: `docs/superpowers/specs/2026-07-21-agentdeck-observer-ipc-takeover-closure-design.md`
- Spec commit: `6b6cd26f`
- Do not import `agentdeck.daemon`, legacy runtime/state/orchestration, pane capture, reply extraction, or tmux send-keys task transport.
- Do not run real tmux, provider, ACP Mission, preflight, or Golden Product Mission.
- Every modified or created Python/test file must remain at or below 500 lines.
- Keep shared-file edits serial. All review fixes return to the same Task 29 implementation agent.

## Locked file map

**New Ports and Application units**

- `src/agentdeck/ports/observer.py`: shared immutable subscription, binding, cursor, acknowledgement, publication and channel interfaces.
- `src/agentdeck/ports/project_evidence.py`: closed `ProjectEvidence` value and read-only source Port.
- `src/agentdeck/application/observer_records.py`: closed cursor aggregate/command codecs and deterministic identities.
- `src/agentdeck/application/observer_broker.py`: decoded-event publication, pending-delivery validation, acknowledgement verification, and Application-only cursor writer.
- `src/agentdeck/application/takeover_records.py`: closed ownership-cycle and takeover command-result codecs.
- `src/agentdeck/application/takeover_wait.py`: exact gate-or-runtime-release wait helper.

**New Adapters and Product units**

- `src/agentdeck/adapters/observer_ipc.py`: bounded project-local Unix-socket server/client; no business authority.
- `src/agentdeck/adapters/observer_protocol.py`: strict versioned JSON Lines encoding/decoding.
- `src/agentdeck/adapters/project_evidence.py`: argv-only bounded Git evidence collector.
- `src/agentdeck/product/observer_command.py`: real read-only `agentdeck observer` process.
- `src/agentdeck/product/observer_lifecycle.py`: foreground Broker start/close hook.
- `src/agentdeck/entrypoint.py`: thin observer-command router; all other argv delegate unchanged to `agentdeck.cli.main`.

**Existing files modified**

- `src/agentdeck/product/observer.py`: consume shared Ports types without changing Task 28 rendering/redaction.
- `src/agentdeck/application/approval_service.py`: publish each decoded event through an injected observational publisher.
- `src/agentdeck/application/execution_runtime.py`: exact release signal.
- `src/agentdeck/application/takeover_control.py`: delegate codecs/waits, reconcile durable authority, gate automatic cancellation.
- `src/agentdeck/application/execution_service.py`: compose the corrected takeover boundary only.
- `src/agentdeck/product/bootstrap.py`: production proof-source/Broker/Observer composition.
- `src/agentdeck/product/shell.py`: lifecycle hook only; extract code if line limit would be exceeded.
- `src/agentdeck/adapters/tmux_observer.py`: retain argv-only/non-authority behavior; change only if shared types require it.
- `src/agentdeck/__main__.py`, `pyproject.toml`: route through the thin entrypoint without changing bare legacy behavior before Task 39.
- `HISTORY.md`, `docs/handoff/current-development-state.md`: Task 29 evidence and closure.

**New tests**

- `tests/product_kernel/test_observer_port.py`
- `tests/product_kernel/test_observer_broker.py`
- `tests/product_kernel/test_observer_ipc.py`
- `tests/product_kernel/test_observer_command.py`
- `tests/product_kernel/test_project_evidence.py`
- `tests/product_kernel/test_takeover_recovery.py`
- `tests/product_kernel/test_takeover_exit.py`
- `tests/product_kernel/test_takeover_composition.py`

## Task 1: Move Observer identity into a shared Port

**Files:**
- Create: `src/agentdeck/ports/observer.py`
- Modify: `src/agentdeck/product/observer.py`
- Create: `tests/product_kernel/test_observer_port.py`
- Modify: `tests/product_kernel/test_observer_fidelity.py`
- Modify: `tests/product_kernel/test_observer_redaction.py`

- [ ] **Step 1: Write the failing shared-type tests**

```python
def test_product_observer_exports_the_shared_cursor_type() -> None:
    from agentdeck.ports.observer import ObserverCursor
    from agentdeck.product.observer import ObserverCursor as ProductCursor
    assert ProductCursor is ObserverCursor


def test_acknowledgement_requires_the_exact_cursor_and_project_binding() -> None:
    cursor = ObserverCursor(
        "prj_1", "ses_1", "agt_1", "tsk_1", "att_1", "acp",
        1, "evt_1", "a" * 64,
    )
    assert ObserverAcknowledgement(cursor=cursor).cursor is cursor
    with pytest.raises(ValueError, match="project"):
        replace(cursor, project_id="prj_other")
```

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_observer_port.py -q
```

Expected: FAIL because `agentdeck.ports.observer` does not exist.

- [ ] **Step 3: Add closed shared values**

```python
@dataclass(frozen=True)
class ObserverSubscription:
    project_id: str
    session_id: str
    agent_id: str

@dataclass(frozen=True)
class ObserverBinding:
    project_id: str
    session_id: str
    agent_id: str
    task_id: str
    attempt_id: str
    transport: str

@dataclass(frozen=True)
class ObserverCursor:
    project_id: str
    session_id: str
    agent_id: str
    task_id: str
    attempt_id: str
    transport: str
    sequence: int
    event_id: str
    fingerprint: str

@dataclass(frozen=True)
class ObserverAcknowledgement:
    cursor: ObserverCursor
```

Validate exact prefixes, UTF-8/ASCII budgets, `transport == "acp"`, sequence
range, and lowercase SHA-256 fingerprint. Define Protocols only for read-only
subscription, event publication, cursor load, and Application acknowledgement.
Do not expose task dispatch, completion, approval, result, recovery, Store, or
terminal capture methods.

- [ ] **Step 4: Replace Task 28 duplicate types**

Import `ObserverSubscription`, `ObserverCursor`, and cursor/sink Protocols from
the Port. Preserve `ObserverStream`'s existing render → emit → acknowledge →
local-cursor ordering and all redaction behavior byte-for-byte.

- [ ] **Step 5: Run GREEN and Task 28 regression**

```bash
conda run -n agentdeck pytest \
  tests/product_kernel/test_observer_port.py \
  tests/product_kernel/test_observer_fidelity.py \
  tests/product_kernel/test_observer_redaction.py -q
```

Expected: PASS; existing Task 28 tests remain green with the shared exact type.

- [ ] **Step 6: Commit**

```bash
git add src/agentdeck/ports/observer.py src/agentdeck/product/observer.py \
  tests/product_kernel/test_observer_port.py \
  tests/product_kernel/test_observer_fidelity.py \
  tests/product_kernel/test_observer_redaction.py
git commit -m "refactor: share exact observer cursor authority"
```

## Task 2: Add the Application cursor writer and decoded-event Broker

**Files:**
- Create: `src/agentdeck/application/observer_records.py`
- Create: `src/agentdeck/application/observer_broker.py`
- Create: `tests/product_kernel/test_observer_broker.py`
- Modify: `src/agentdeck/application/approval_service.py`

- [ ] **Step 1: Write RED cursor and publication tests**

```python
def test_cursor_is_written_only_after_exact_acknowledgement(store, broker) -> None:
    event = worker_event(sequence=1)
    broker.publish(event)
    assert store.load_aggregate("observer_cursors", broker.cursor_id(event)) is None
    broker.acknowledge(ack_for(event))
    assert broker.current_cursor(event.attempt_id).sequence == 1


def test_invalid_ack_never_writes_or_advances(store, broker) -> None:
    event = worker_event(sequence=1)
    broker.publish(event)
    with pytest.raises(ObserverBrokerError, match="observer_ack_conflict"):
        broker.acknowledge(replace(ack_for(event), cursor=future_cursor(event)))
    assert store.command_count == 0


def test_approval_service_publishes_real_worker_event_before_interpreting_it() -> None:
    # Use a real WorkerEvent and a recording publisher, not mock text.
    assert publisher.events == [started, permission_requested, completed]
```

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_observer_broker.py -q
```

Expected: FAIL because Broker/writer and ApprovalService injection are absent.

- [ ] **Step 3: Implement closed cursor persistence**

`observer_records.py` must define deterministic cursor aggregate identity and
command identity from the full lineage and event fingerprint. The writer uses:

```python
result = store.execute_once(command_id, "observer_cursor_acknowledged", commit)
```

The transaction compare-checks the current cursor, saves one closed
`observer_cursors` aggregate, and appends one compact acknowledgement event.
Replay returns the exact closed result. Sequence rollback, gap, identity drift,
or conflicting replay writes nothing.

- [ ] **Step 4: Implement the Broker**

The Broker snapshots only exact `WorkerEvent`, retains a bounded pending window,
and calls an injected channel Adapter. `publish()` catches only the channel's
stable observation-degraded result and never changes Worker/Mission state.
`acknowledge()` must match a pending exact fingerprint before invoking the
Application writer.

- [ ] **Step 5: Inject observational publication into ApprovalService**

Add an optional no-op publisher dependency with a closed result shape. In
`bridge_attempt`, publish every decoded event before permission interpretation.
Publication degradation is recorded by the Broker and does not skip permission
handling or result collection. Arbitrary publisher exceptions fail through a
stable Observer-degraded path without exposing the exception.

- [ ] **Step 6: Run GREEN and permission regression**

```bash
conda run -n agentdeck pytest \
  tests/product_kernel/test_observer_broker.py \
  tests/product_kernel/test_approval_service.py \
  tests/product_kernel/test_acp_worker_failures.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agentdeck/application/observer_records.py \
  src/agentdeck/application/observer_broker.py \
  src/agentdeck/application/approval_service.py \
  tests/product_kernel/test_observer_broker.py
git commit -m "feat: persist acknowledged observer delivery"
```

## Task 3: Deliver decoded events over project-local IPC

**Files:**
- Create: `src/agentdeck/adapters/observer_protocol.py`
- Create: `src/agentdeck/adapters/observer_ipc.py`
- Create: `src/agentdeck/product/observer_command.py`
- Create: `src/agentdeck/entrypoint.py`
- Modify: `src/agentdeck/__main__.py`
- Modify: `pyproject.toml`
- Create: `tests/product_kernel/test_observer_ipc.py`
- Create: `tests/product_kernel/test_observer_command.py`

- [ ] **Step 1: Write strict protocol RED tests**

```python
@pytest.mark.parametrize("mutation", [
    "unknown_field", "oversize", "bad_utf8", "wrong_version",
    "foreign_project", "foreign_session", "future_ack", "raw_frame_field",
])
def test_protocol_rejects_non_closed_frames_without_echo(mutation) -> None:
    with pytest.raises(ObserverProtocolError) as raised:
        decode_frame(hostile_frame(mutation))
    assert str(raised.value) in ALLOWLISTED_MESSAGES
    assert hostile_secret not in str(raised.value)


def test_real_socket_ack_occurs_after_sink_emit(tmp_path) -> None:
    assert timeline == ["server_event", "sink_emit", "client_ack", "cursor_write"]
```

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_observer_ipc.py \
  tests/product_kernel/test_observer_command.py -q
```

Expected: FAIL because protocol, Adapter, command, and entrypoint are absent.

- [ ] **Step 3: Implement the protocol**

Use a schema version such as `observer-ipc/v1` and exact field sets for:

```text
handshake(project_id, session_id, agent_id, mode=read_only)
binding(project_id, session_id, agent_id, task_id, attempt_id, transport, cursor)
event(binding fields, sequence, event_id, timestamp, kind, payload, fingerprint)
ack(cursor fields)
degraded(code, stage)
close(reason)
```

Encode one compact JSON object plus newline. Enforce per-frame byte, nesting,
collection, string, and integer limits before copying. Never accept raw protocol,
prompt, stderr, hidden-reasoning, credential, path, task-dispatch, approval, or
result fields.

- [ ] **Step 4: Implement the Unix-socket Adapter**

Create the endpoint beneath a project-local private runtime directory. Validate
canonical project root, parent mode, socket type/identity, no symlink, and mode
`0600`. Server publication never waits indefinitely for a subscriber and uses a
bounded per-subscriber queue. The client verifies endpoint identity before and
after connect. All reads/writes and shutdowns are bounded.

- [ ] **Step 5: Implement the real Observer command**

Parse only the argv emitted by `TmuxObserver`. Connect read-only, accept the
Broker binding, instantiate the real `ObserverStream`, and pass a
`TmuxObservationSink(writer=print)` plus an acknowledgement proxy. The proxy
sends the cursor back to the Broker; it never writes Store or files.

- [ ] **Step 6: Add the thin entrypoint without Task 39 cutover**

```python
def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["observer"]:
        return run_observer_command(args[1:])
    from agentdeck.cli import main as legacy_main
    return legacy_main(args)
```

Point `pyproject.toml` and `__main__.py` at this router. Prove ordinary legacy
argv and bare behavior are delegated unchanged; do not implement Task 39.

- [ ] **Step 7: Run GREEN and tmux argv regression**

```bash
conda run -n agentdeck pytest \
  tests/product_kernel/test_observer_ipc.py \
  tests/product_kernel/test_observer_command.py \
  tests/product_kernel/test_tmux_layout.py \
  tests/product_kernel/test_observer_fidelity.py \
  tests/product_kernel/test_observer_redaction.py -q
```

Expected: PASS with an actual local socket and emit-before-ack timeline.

- [ ] **Step 8: Commit**

```bash
git add src/agentdeck/adapters/observer_protocol.py \
  src/agentdeck/adapters/observer_ipc.py \
  src/agentdeck/product/observer_command.py src/agentdeck/entrypoint.py \
  src/agentdeck/__main__.py pyproject.toml \
  tests/product_kernel/test_observer_ipc.py \
  tests/product_kernel/test_observer_command.py
git commit -m "feat: stream decoded events to tmux observers"
```

## Task 4: Capture typed exact Git project evidence

**Files:**
- Create: `src/agentdeck/ports/project_evidence.py`
- Create: `src/agentdeck/adapters/project_evidence.py`
- Create: `tests/product_kernel/test_project_evidence.py`

- [ ] **Step 1: Write RED evidence tests**

```python
def test_git_evidence_changes_for_head_index_tracked_and_untracked_drift(repo) -> None:
    baseline = source.capture()
    for mutation in (change_head, change_index, change_tracked, add_untracked):
        mutation(repo)
        assert source.capture() != baseline
        repo.restore()


def test_mission_hash_cannot_construct_project_evidence(confirmed) -> None:
    with pytest.raises(ValueError, match="provenance"):
        ProjectEvidence.from_untyped_digest(confirmed.content_hash)
```

Also assert argv lists, output budgets, non-Git rejection, symlink/root drift,
no raw path/diff/content in the value, and content-free failures.

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_project_evidence.py -q
```

Expected: FAIL because the Port and Adapter are absent.

- [ ] **Step 3: Implement Port and argv-only Adapter**

`ProjectEvidence` has a closed schema/provenance tag, project identity, root
identity digest, HEAD digest, index digest, tracked-worktree digest,
untracked-name digest, and final digest. The Adapter uses injected argv-list
runner calls equivalent to `git rev-parse`, `git write-tree` or read-only index
facts, `git diff --no-ext-diff`, and `git ls-files --others --exclude-standard`.
Hash bounded bytes internally; never export raw output.

- [ ] **Step 4: Run GREEN**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_project_evidence.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/ports/project_evidence.py \
  src/agentdeck/adapters/project_evidence.py \
  tests/product_kernel/test_project_evidence.py
git commit -m "feat: bind takeover to typed git evidence"
```

## Task 5: Make ownership cycles Store-backed and outcome-safe

**Files:**
- Create: `src/agentdeck/application/takeover_records.py`
- Modify: `src/agentdeck/application/takeover_control.py`
- Modify: `tests/product_kernel/test_takeover.py`
- Create: `tests/product_kernel/test_takeover_recovery.py`

- [ ] **Step 1: Write RED recovery regressions**

```python
def test_sqlite_commit_then_raise_keeps_human_gate_closed(real_sqlite_harness) -> None:
    result = run(real_sqlite_harness.takeover_with_post_commit_failure())
    assert result.accepted is True  # reconciled from durable command result
    assert real_sqlite_harness.attempt_state == "human_controlled"
    assert real_sqlite_harness.automatic_input_enabled is False


def test_reconstructed_controller_replays_active_cycle_without_new_effects(harness) -> None:
    first = run(harness.control.takeover(harness.attempt_id))
    rebuilt = harness.rebuild_control()
    replay = run(rebuilt.takeover(harness.attempt_id))
    assert replay == first
    assert harness.effect_counts() == (1, 1, 1)


def test_consumed_old_cycle_cannot_reclose_gate(harness) -> None:
    run(harness.takeover_then_return())
    rebuilt = harness.rebuild_control()
    assert rebuilt.automatic_input_enabled is True
    assert harness.attempt_state == "running"
```

Add a second legitimate takeover cycle test and explicit Mission-hash rejection.

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_takeover.py \
  tests/product_kernel/test_takeover_recovery.py -q
```

Expected: FAIL for post-commit reopening, volatile baseline/cycle state, duplicate
cursor type, and untyped project hash acceptance.

- [ ] **Step 3: Add closed ownership records**

Persist a per-Attempt ownership authority aggregate containing generation,
cycle identity, state (`automatic`, `human`, `returned`, `interrupted`), exact
baseline, takeover command identity, and return command identity. Codecs reject
unknown/missing fields and validate every reference against the exact Attempt.

- [ ] **Step 4: Reconcile takeover and return commands**

Before mutation, load the authority aggregate and relevant command result. On
any `execute_once` exception, read the command result and Attempt/authority
aggregates. Keep the gate closed unless durable facts prove no human takeover
committed. Return-control reopens only after durable returned state is proven.
Do not keep baseline or transition authority solely in `_ActiveAuthority`.

- [ ] **Step 5: Consume shared cursor and typed project evidence**

Remove `TakeoverCursor`. Require exact `ObserverCursor` and `ProjectEvidence`.
Persist compact typed evidence fields and reject any untyped SHA-256 source.

- [ ] **Step 6: Run GREEN including real SQLite**

```bash
conda run -n agentdeck pytest \
  tests/product_kernel/test_takeover.py \
  tests/product_kernel/test_takeover_recovery.py \
  tests/product_kernel/test_sqlite_transactions.py \
  tests/product_kernel/test_sqlite_execution.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agentdeck/application/takeover_records.py \
  src/agentdeck/application/takeover_control.py \
  tests/product_kernel/test_takeover.py \
  tests/product_kernel/test_takeover_recovery.py
git commit -m "fix: persist exact takeover ownership cycles"
```

## Task 6: Gate automatic cancellation and preserve explicit exit

**Files:**
- Create: `src/agentdeck/application/takeover_wait.py`
- Modify: `src/agentdeck/application/takeover_control.py`
- Modify: `src/agentdeck/application/execution_runtime.py`
- Modify: `src/agentdeck/application/execution_service.py`
- Create: `tests/product_kernel/test_takeover_exit.py`
- Modify: `tests/product_kernel/test_product_exit_acp_integration.py`

- [ ] **Step 1: Write RED cancellation and exit tests**

```python
def test_approval_denial_cannot_cancel_during_human_ownership(harness) -> None:
    run(harness.takeover())
    harness.release_denial()
    assert harness.raw_worker.cancellations == []


def test_confirmed_exit_uses_exact_raw_cancellation_and_wakes_waiters(harness) -> None:
    run(harness.takeover())
    result = run(harness.confirm_exit())
    assert result.should_exit is True
    assert harness.raw_worker.cancellations == ["product_exit_confirmed"]
    assert harness.attempt_state == "interrupted"
    assert harness.execution_task.done()


def test_reentry_after_takeover_exit_still_requires_resume(harness) -> None:
    run(harness.takeover_then_exit())
    reopened = harness.reopen_shell()
    assert reopened.requires_explicit_resume is True
```

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_takeover_exit.py \
  tests/product_kernel/test_product_exit_acp_integration.py -q
```

Expected: FAIL because automatic cancel bypasses the gate and result waiters do
not wake after explicit exit release.

- [ ] **Step 3: Add the exact release signal**

`ForegroundExecutionRuntime` exposes an awaitable tied to the exact
Attempt/handle and completes it only on exact `release()` or settled exit
cancellation. It must be same-loop, one-shot, lineage-validated, and require no
polling.

- [ ] **Step 4: Race automatic gate against release**

`takeover_wait.py` awaits the gate and exact release signal concurrently,
cancels/awaits the losing Task, and raises one stable internal
`AutomaticAuthorityReleased` outcome when exit wins. `_ControlledWorker` gates
permission responses and automatic cancellation. Explicit exit continues to
use the raw Worker held by `ExitCancellationLease`.

- [ ] **Step 5: Settle execution without a second terminal write**

When the exact exit release wins, the execution coordinator reads the durable
interrupted Attempt and stops without collecting, interpreting, or persisting a
new Worker result. Return-control rejects interrupted/terminal authority.

- [ ] **Step 6: Run GREEN and Task 15B regression**

```bash
conda run -n agentdeck pytest \
  tests/product_kernel/test_takeover_exit.py \
  tests/product_kernel/test_product_exit_acp_integration.py \
  tests/product_kernel/test_product_exit_real_acp_cancellation.py \
  tests/product_kernel/test_product_exit_terminal_race.py \
  tests/product_kernel/test_sqlite_exit_authority.py \
  tests/product_kernel/test_product_reentry.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agentdeck/application/takeover_wait.py \
  src/agentdeck/application/takeover_control.py \
  src/agentdeck/application/execution_runtime.py \
  src/agentdeck/application/execution_service.py \
  tests/product_kernel/test_takeover_exit.py \
  tests/product_kernel/test_product_exit_acp_integration.py
git commit -m "fix: preserve explicit exit under human takeover"
```

## Task 7: Wire the production composition and lifecycle

**Files:**
- Create: `src/agentdeck/product/observer_lifecycle.py`
- Modify: `src/agentdeck/product/bootstrap.py`
- Modify: `src/agentdeck/product/shell.py`
- Create: `tests/product_kernel/test_takeover_composition.py`
- Modify: `tests/product_kernel/test_product_preview_flow.py`

- [ ] **Step 1: Write RED production-composition tests**

```python
def test_shell_to_real_execution_service_has_all_takeover_proof_sources(tmp_path) -> None:
    shell = build_product_shell_with_fake_acp(tmp_path)
    assert shell.execution_service.takeover_control.sources == (
        "typed_project_evidence", "permission_snapshot",
        "active_acp_session", "acknowledged_observer_cursor",
    )


def test_real_observer_ack_enables_takeover_and_missing_ack_fails_closed(tmp_path) -> None:
    product = product_composition(tmp_path)
    assert run(product.takeover_before_observer_ack()).diagnostic.code \
        == "takeover_observer_cursor_unavailable"
    run(product.emit_through_real_observer())
    assert run(product.takeover()).accepted is True
```

- [ ] **Step 2: Run RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_takeover_composition.py \
  tests/product_kernel/test_product_preview_flow.py -q
```

Expected: FAIL because production bootstrap has no Broker/lifecycle/proof wiring.

- [ ] **Step 3: Compose real sources**

Build the Git evidence Adapter, Application cursor writer/Broker, IPC Adapter,
permission source, and exact runtime/session source in `build_product_shell`.
Inject the Broker publisher into `ApprovalService` and configure one
`TakeoverControl` on the real `ExecutionService`. No default source may be a
test-only lambda or untyped digest.

- [ ] **Step 4: Start and close the Broker with the Product Shell**

Use `ProductObserverLifecycle` so `run_async()` starts the project endpoint
before accepting Mission execution and closes it during every normal/error/
cancellation exit path before Store close. If `shell.py` would exceed 500 lines,
move lifecycle mechanics into the new helper rather than compressing code.

- [ ] **Step 5: Run GREEN and composition regression**

```bash
conda run -n agentdeck pytest \
  tests/product_kernel/test_takeover_composition.py \
  tests/product_kernel/test_product_preview_flow.py \
  tests/product_kernel/test_product_shell.py \
  tests/product_kernel/test_observer_command.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agentdeck/product/observer_lifecycle.py \
  src/agentdeck/product/bootstrap.py src/agentdeck/product/shell.py \
  tests/product_kernel/test_takeover_composition.py \
  tests/product_kernel/test_product_preview_flow.py
git commit -m "fix: wire real task29 product authority"
```

## Task 8: Close Task 29 verification and reviews

**Files:**
- Modify: `HISTORY.md`
- Modify: `docs/handoff/current-development-state.md`

- [ ] **Step 1: Run the focused R5 and coupled regressions**

```bash
conda run -n agentdeck pytest \
  tests/product_kernel/test_tmux_layout.py \
  tests/product_kernel/test_observer_*.py \
  tests/product_kernel/test_project_evidence.py \
  tests/product_kernel/test_takeover*.py \
  tests/product_kernel/test_product_exit*.py \
  tests/product_kernel/test_acp_worker_failures.py \
  tests/product_kernel/test_approval_service.py \
  tests/product_kernel/test_sqlite_transactions.py -q
```

Expected: PASS.

- [ ] **Step 2: Run architecture and context firewalls**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_architecture.py \
  tests/product_kernel/test_context_firewall.py -q
```

Expected: PASS with no legacy module admission.

- [ ] **Step 3: Run both full suites**

```bash
conda run -n agentdeck pytest tests/product_kernel -q
conda run -n agentdeck pytest tests -q --ignore=tests/product_kernel
```

Expected: PASS; record exact counts and durations. Any failure triggers
`superpowers:systematic-debugging`; do not rerun blindly.

- [ ] **Step 4: Run static gates**

```bash
conda run -n agentdeck python -m compileall -q src tests/product_kernel
git diff --check 50f79a767644b7b3b4a775529c4f193036c9e82e..HEAD
find src/agentdeck tests/product_kernel -name '*.py' -print0 | \
  xargs -0 wc -l | sort -nr | head -n 40
rg -n "agentdeck\.daemon|capture-pane|reply extraction|raw ACP|raw_protocol|send-keys|sqlite3|StateStore" \
  src/agentdeck/ports/observer.py src/agentdeck/application/observer_*.py \
  src/agentdeck/adapters/observer_*.py src/agentdeck/product/observer*.py
```

Expected: compile and diff pass; every changed/new Python/test file is at most
500 lines; no forbidden import/API or direct Observer persistence; any matches
are inspected and justified as assertions/documentation rather than behavior.

- [ ] **Step 5: Update HISTORY and request independent specification review**

Record all RED/GREEN evidence, first failure reasons, commits, regression
counts, and absence of live actions. The controller dispatches an independent
specification reviewer over base `50f79a...` through current HEAD. All Critical
and Important issues return to the same implementation agent for new RED/GREEN
fixes and re-review.

- [ ] **Step 6: Request independent code-quality review**

Only after specification approval, dispatch a separate quality reviewer. Return
all Critical and Important findings to the same implementation agent, rerun the
owning tests, and repeat review until approved.

- [ ] **Step 7: Rerun every gate after final review fixes**

Repeat Steps 1–4 from the final reviewed HEAD. Older green output is not closure
evidence.

- [ ] **Step 8: Update handoff and create the Task 29 closure commit**

The handoff must state exact reviewed HEAD, test counts, R5 closure, immutable
Observer/takeover/exit invariants, and Task 30 as the sole next numerical Task.

```bash
git add HISTORY.md docs/handoff/current-development-state.md
git commit -m "docs: close task29 observer takeover authority"
git status --short
```

Expected: clean worktree. Stop before Task 30 unless the active `/goal` remains
authorized and the controller has recorded Task 29 complete; then dispatch a
fresh Task 30 implementer according to the main Product Kernel Rewrite plan.

## Plan self-review

- Every requirement in the approved closure spec maps to Tasks 1–8.
- Observer delivery is proven only by real sink emission and acknowledgement;
  execution-side stream consumption never advances the cursor.
- Cursor, project evidence, ownership cycle, and release types are defined once
  and used consistently in later tasks.
- Production composition, not only tests, supplies all proof sources.
- `agentdeck observer` becomes executable without modifying the 19,000-line
  legacy `cli.py` or performing the later bare-entry cutover.
- No Task 30 behavior, live tmux/provider/ACP action, legacy admission, direct
  Observer Store write, terminal authority, blind retry, polling, skip, xfail,
  fallback, or timeout relaxation is included.
- No TBD, TODO, placeholder implementation, or unspecified test step remains.
