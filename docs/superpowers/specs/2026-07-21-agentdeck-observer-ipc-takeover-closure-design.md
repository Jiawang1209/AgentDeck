# AgentDeck Observer IPC and Takeover Closure Design

**Date:** 2026-07-21  
**Status:** Approved  
**Scope:** Product Kernel Rewrite Task 29 review closure  
**Authority:**

1. `docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md`
2. `docs/superpowers/plans/2026-07-18-agentdeck-product-kernel-rewrite.md`
3. `docs/roadmap/product-north-star.md`
4. the active Task 29–39 `/goal`

## 1. Problem

Task 28 defined faithful cursor-safe rendering, and Task 29 added takeover and
return-control. Independent Task 29 specification review found that production
composition had no real Observer delivery and acknowledgement channel. The
only real `ObserverStream` instances existed in tests, while tmux panes were
planned to run an `agentdeck observer` command that did not yet exist.

Execution-side consumption of a `WorkerEvent` is not proof that an Observer
displayed it. Advancing an Observer cursor from `ApprovalService` or a Worker
wrapper without successful sink delivery would violate Task 28's irreversible
ordering:

```text
render -> emit -> acknowledge -> advance local cursor
```

Task 29 also needs to close five coupled authority defects: post-commit error
reconciliation, typed live project evidence, gated automatic cancellation,
Store-backed ownership-cycle idempotency, and takeover-aware explicit exit.

## 2. Decision

Implement a new Product Kernel-native, project-isolated local Observer channel.
It uses a bounded versioned Unix-socket protocol and does not import the legacy
`agentdeck.daemon` implementation.

The complete event path is:

```text
ACP Worker
  -> decoded and redacted WorkerEvent
  -> Application Observer Broker
  -> project-isolated Unix socket
  -> agentdeck observer in the bound tmux pane
  -> ObserverStream
  -> TmuxObservationSink
  -> acknowledgement to the Broker
  -> Application cursor writer
```

The channel exists only for human observation. It cannot transport tasks,
permissions, results, completion, recovery decisions, or lifecycle authority.

## 3. Boundaries and components

### 3.1 Shared Observer channel Port

A new Ports module owns immutable, bounded values for:

- project/session/Agent subscription identity;
- Broker-provided Task/Attempt binding;
- exact event cursor identity and fingerprint;
- acknowledgement messages;
- read-only subscription and Application-side acknowledgement capabilities.

The Task 28 Product Observer and Task 29 takeover controller use this one
cursor type. Product code must not duplicate a structurally similar cursor.

### 3.2 Application Observer Broker

The Broker accepts only already-decoded `WorkerEvent` values. It validates the
full session/Agent/Task/Attempt/transport/sequence identity, derives the exact
event fingerprint, and publishes a bounded protocol value to the matching
subscriber.

Publication is observational. A missing or failed Observer records observation
degradation but does not change the Worker result or Mission lifecycle.

After receiving an acknowledgement, the Broker validates every identity field,
sequence, event identity, and fingerprint against the emitted event. Only then
may it invoke the foreground Application cursor writer. Invalid, foreign,
duplicate-conflicting, regressed, or future acknowledgements fail closed.

### 3.3 Unix-socket Adapter

The Adapter provides the server and client sides of a versioned bounded JSON
Lines protocol. It uses a project-namespaced endpoint with a private parent
directory and restrictive socket permissions. Endpoint identity, symlink,
ownership, and replacement checks fail closed.

The protocol admits only handshake, binding, decoded event, acknowledgement,
degradation, and orderly-close messages. Unknown fields, excessive bytes,
excessive nesting, malformed UTF-8, identity drift, and protocol-version drift
are rejected without echoing input.

No protocol value contains raw ACP frames, full prompts, stderr, hidden
reasoning, credentials, source contents, or unredacted paths.

### 3.4 Product Observer command

The existing deterministic tmux pane argv becomes executable through a real
`agentdeck observer --mode event-subscription --read-only ...` Product command.
The command:

1. connects to the exact project endpoint;
2. subscribes by project/session/Agent identity;
3. receives the current exact Task/Attempt binding from the Broker;
4. constructs one exact `ObserverSubscription` and `ObserverStream`;
5. emits records to the pane through `TmuxObservationSink`;
6. returns an acknowledgement only after sink emission succeeds.

The command cannot write SQLite, state files, or cursor files. Reconnect must
resume from the Application-owned acknowledged cursor and must not mix a new
Task or Attempt into the old stream.

### 3.5 Production composition

The Product composition root creates the Broker and Application cursor writer,
binds decoded Worker event publication, and supplies real cursor/project/
permission/ACP-session proof sources to takeover control.

The Observer channel does not make execution wait for terminal display. If no
trusted acknowledgement exists, execution may continue, but `/takeover` and
`/return-control` cannot claim Observer-cursor consistency and therefore fail
closed.

## 4. Takeover transaction and reconciliation

Both `/takeover` and `/return-control` are durable Application Commands with
unique command identities.

### 4.1 Takeover

The controller validates one exact Mission/Task/Attempt/Agent/ACP-session
lineage, collects the four proof sources, and closes that Attempt's automatic
input gate before the durable transition.

One SQLite transaction records:

- Attempt state `human_controlled`;
- owner `human`;
- a new ownership-cycle identity;
- typed project evidence, permission snapshot, ACP-session identity, and
  acknowledged Observer cursor baseline;
- the closed command result;
- one full-lineage `human_takeover` event.

If the transaction reports an error after SQLite may have committed, the
controller reads the durable command result and Attempt aggregate. It reopens
the gate only when durable evidence proves that no takeover committed. Unknown
outcome keeps the gate closed and returns a sanitized diagnostic.

### 4.2 Return-control

Return-control loads the ownership cycle and baseline from the Store, never
from process-local caches. It recaptures project evidence, permission state,
ACP session, Observer cursor, and current Attempt state. Any missing source,
type mismatch, identity mismatch, or drift retains human ownership.

One transaction restores `running`, clears human ownership, consumes the exact
cycle, stores the command result, and appends the return-control audit event.
Only a confirmed transaction reopens automatic input.

### 4.3 Idempotency

- Replaying a command identity returns the same durable result with no new
  effects.
- Rebuilding the controller does not change replay behavior.
- A consumed ownership cycle cannot be reopened by replaying an old takeover.
- Each new takeover uses a new durable cycle identity.
- Command/Attempt/runtime disagreement triggers reconciliation, not inferred
  mutation.

## 5. Typed project evidence

`ProjectEvidence` is a closed, provenance-bearing value. A Git Adapter captures
the exact project root identity, HEAD, index state, tracked worktree state, and
untracked-name state through injected argv-list commands. It hashes bounded
command output internally and exposes only typed provenance and digests.

It never publishes raw paths, diffs, file contents, or Git output. Non-Git
projects, unsafe roots, command failures, output beyond the byte budget, and
incomplete evidence fail closed. A Mission or Preview content hash is
explicitly rejected even if it is syntactically a valid SHA-256 value.

## 6. Cancellation and explicit exit

Worker cancellation is separated into two capabilities:

- automatic cancellation from approval or orchestration is ownership-gated;
- explicit exit cancellation is available only to the confirmed Task 15B exit
  path for the exact Attempt and ACP session.

Human ownership blocks automatic permission responses and automatic
cancellation. It does not prevent a human-confirmed `/exit`.

After confirmed exit, the durable Attempt becomes `interrupted`, the raw
runtime is released, and an exact owner-change/release signal wakes execution
waiters. No polling and no terminal-output inference are allowed. Re-entry
continues to require explicit `/resume`; return-control cannot revive a
terminal or interrupted Attempt.

## 7. Failure semantics and redaction

Stable diagnostics cover:

- Observer endpoint unavailable or replaced;
- subscription/binding mismatch;
- malformed or invalid acknowledgement;
- unavailable, conflicting, or drifted cursor;
- unavailable or drifted project evidence;
- takeover outcome unknown;
- Attempt/session/permission drift;
- automatic cancellation blocked by human ownership;
- confirmed exit interruption.

Diagnostics return only allowlisted code, stage, outcome-known status, and
bounded lineage. They do not include socket paths, Git output, source paths,
prompts, stderr, raw exceptions, protocol bodies, or credentials.

Observer degradation never implies Worker or Mission failure. Conversely,
Observer output never proves completion, approval, result, recovery, or
lifecycle state.

## 8. TDD and verification

Implementation proceeds through deterministic RED tests for:

1. handshake/binding and strict protocol rejection;
2. real sink emission before acknowledgement;
3. Application-only cursor persistence and reconnect;
4. real SQLite commit-then-raise reconciliation;
5. Mission-hash rejection and exact Git drift;
6. ownership-gated automatic cancellation;
7. explicit takeover-to-exit settlement without polling;
8. reconstructed-controller replay and multiple ownership cycles;
9. Product Shell to real ExecutionService composition.

Then run Task 27–29, Task 15B exit, ACP permission/failure, SQLite transaction,
architecture/context firewall, Product Kernel full, and legacy full suites,
followed by compileall, diff checks, 500-line limits, and static checks against
legacy daemon imports, pane reply extraction, raw frames, send-keys automatic
communication, direct Observer persistence, and nested `asyncio.run`.

Task 29 closes only after independent specification review and then independent
code-quality review report no Critical or Important issue. HISTORY and handoff
are updated in the closure commit. No live tmux, provider, ACP Mission, or
Golden Product Mission is authorized by this design.

## 9. Explicit non-goals

- no reuse or admission of legacy daemon code;
- no general-purpose message broker;
- no remote or network Observer;
- no Observer-controlled scheduling, approval, completion, or recovery;
- no SQLite writes from Product Observer or tmux Adapter;
- no terminal output as authority;
- no Task 30 work in this closure.
