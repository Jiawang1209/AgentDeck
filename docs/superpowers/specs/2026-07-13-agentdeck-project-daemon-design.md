# AgentDeck Phase 3 M2 Project Daemon Design

**Date:** 2026-07-13
**Status:** Implemented; deterministic validation and two-step real transport PASS, full M2c live acceptance BLOCKED
**Milestone:** Phase 3 M2 (`M2a → M2b → M2c`)
**Depends on:** Phase 3 M1 foreground conversation at commit `cd8fd655`

## 1. Purpose

M2 makes one confirmed, frozen Mission continue safely after the interactive
`agentdeck` client disconnects. A user can describe a multi-Agent task, review
one Mission, confirm it once, close the terminal, and later reconnect to a
completed or safely paused result.

M2 is not a replacement for tmux. The responsibilities are complementary:

- the Project Daemon keeps Mission coordination, policy, recovery, and audit
  alive;
- ACP provides structured AgentDeck-to-Worker communication;
- tmux keeps compatible CLI Workers visible, attachable, and available for
  explicit human takeover;
- future A2A will connect AgentDeck horizontally to independently governed
  Agent systems, but is outside M2.

The defining product rule is:

> Worker completion is event-driven and mediated by AgentDeck. Workers do not
> directly observe, wake, authorize, or schedule one another. AgentDeck
> validates completion, persists it to the ledger, creates a compact handoff,
> and activates the next eligible Worker. ACP is used for managed Worker
> communication; future A2A is reserved for independently governed Agent
> systems.

## 2. User experience

### 2.1 Primary entry

The normal entry remains:

```bash
agentdeck
```

The client resolves the project, connects to its Project Daemon, and starts it
on demand when no healthy daemon exists. Users do not need to run a daemon
command before starting a conversation.

After a Mission preview is confirmed, the response explicitly states that the
Mission can continue after the terminal closes and lists the classes of change
that will pause it.

### 2.2 Reconnection

When the user runs `agentdeck` again, the client connects to the same project
daemon and deterministically renders a compact recovery summary from
ProjectView and the ledger. The summary includes:

- Mission identity and frozen goal summary;
- completed versus total steps;
- recent validated results;
- the active Worker or current wait condition;
- pending approval, permission, blocker, or ambiguity;
- exact inspect and decision controls;
- an optional command to open the live tmux workspace.

This summary must remain usable without a configured or ready LLM. An
open-ended explanatory request may call the configured Leader with compact
facts, but full transcripts are not injected automatically.

### 2.3 Diagnostic commands

Advanced commands remain available for operators and tests:

```bash
agentdeck daemon status
agentdeck daemon start
agentdeck daemon stop
agentdeck daemon logs
```

`daemon stop` refuses while work is active. A force path requires an exact
preview and explicit confirmation, attempts a safe cancellation, records
interrupted or ambiguous work, and never silently treats an incomplete action
as completed.

## 3. Architecture and authority

### 3.1 Topology

```text
Terminal / future TUI / future Desktop
                 |
        versioned local RPC
                 |
                 v
        ProjectDaemon (one authority)
        +-- ClientRegistry
        +-- ControllerLease
        +-- ConversationCoordinator
        +-- MissionScheduler
        +-- WorkerSupervisor
        +-- RecoveryCoordinator
                 |
                 v
 Mission / Approval / Workflow / Ledger / ProjectView
                 |
          +------+------+
          v             v
         ACP        tmux visible plane
```

### 3.2 Authority rules

- Each project has at most one authoritative daemon.
- The daemon is the runtime scheduling authority; durable StateStore records,
  append-only events, and their ProjectView projection remain the persistent
  facts.
- The local RPC connection is a control and observation channel, not a second
  business ledger.
- Clients do not own ACP sessions, advance Missions, or send automated tmux
  input directly.
- The daemon composes existing Mission, approval, workflow, dispatch, ledger,
  trace, ACP, tmux, and ProjectView primitives. It does not reimplement them as
  a parallel control plane.
- Daemon liveness never implies Mission authority. Only an explicitly
  confirmed frozen Mission may be advanced.
- Prompt text, role prompts, Skills, Memory, compact handoffs, Leader plans,
  Worker replies, ACP capabilities, future A2A Agent Cards, and tmux text are
  context, never authorization.

### 3.3 Module boundaries

M2 introduces focused modules under `src/agentdeck/daemon/`:

- `protocol.py`: bounded request/response/event envelopes, version negotiation,
  capability discovery, and sanitized errors;
- `lifecycle.py`: project identity, startup lock, daemon metadata, socket
  ownership, idle shutdown, and safe cleanup;
- `server.py`: Unix domain socket server, client registry, request dispatch,
  subscriptions, and backpressure;
- `client.py`: connect, on-demand start, handshake, bounded retry, and CLI-facing
  client operations;
- `lease.py`: observer/controller state, lease generation, renewal, expiry, and
  explicit takeover preview/confirmation;
- `scheduler.py`: pure next-transition selection and the one-transition-at-a-
  time scheduler driver;
- `recovery.py`: restart reconciliation and classification as resumable,
  waiting-human, ambiguous, blocked, or terminal;
- `supervisor.py`: bounded ACP/tmux Worker attempt lifecycle and cancellation.

`cli.py` wires commands and renders responses. It must not become the daemon
implementation. Existing domain modules remain the owners of their current
contracts and state transitions.

## 4. Daemon and client lifecycle

### 4.1 Platform scope

M2 supports macOS and Linux using a project-scoped Unix domain socket. The
client/server interface remains transport-neutral so a future Windows named
pipe implementation can reuse the protocol. Local TCP is not used in M2.

### 4.2 On-demand startup

The client performs this bounded sequence:

1. resolve and canonicalize the project root;
2. inspect daemon endpoint metadata;
3. attempt a socket connection and version handshake;
4. if no healthy daemon exists, compete for a startup lock;
5. the lock winner spawns one detached project daemon;
6. other clients wait for the bounded readiness result;
7. every client connects to the same verified daemon.

A PID file alone is never proof of identity or health. A connection is accepted
only when the daemon proves the expected project-root hash, process start nonce,
and compatible RPC protocol. Stale metadata or a stale socket can be removed
only when no matching live daemon owns them. AgentDeck never kills a process it
cannot prove belongs to the current project daemon instance.

### 4.3 Task-driven lifetime

The daemon remains alive while any of these facts exists:

- one or more connected clients;
- an active Mission or Worker turn;
- a pending approval or permission;
- a reply, recovery, or ambiguous decision waiting for a human;
- a non-empty durable event outbox;
- an active recovery or safe-shutdown operation.

When none exists, the daemon enters an idle grace period, defaulting to ten
minutes and configurable within safe bounds. New work or a new client cancels
the timer. At expiry the daemon stops accepting mutations, finishes the current
atomic write, safely flushes retryable outboxes, closes transports, removes only
its own endpoint metadata, releases the daemon lock, and exits.

### 4.4 Multiple clients and controller lease

Multiple clients may inspect and subscribe concurrently. Exactly one client may
hold the controller lease required for mutations. The compact lease record
contains `lease_id`, `client_id`, `issued_at`, `expires_at`,
`last_renewed_at`, and monotonically increasing `generation`.

Every write request carries the current lease id and generation. The daemon
revalidates them immediately before the domain mutation. Disconnect or missed
renewals allow the lease to expire. Explicit takeover uses preview then exact
confirmation, is audited, and changes client control only; it does not change
Worker ownership or Mission scope.

### 4.5 Protocol compatibility

Handshake facts include RPC protocol version, ProjectView schema version,
client version, daemon version, project identity, and capabilities. An
incompatible client may request only minimal status and compatibility
diagnostics. All Mission, approval, permission, takeover, transport, and daemon
mutation requests fail closed. A client never automatically restarts an
incompatible daemon with active work.

## 5. Frozen Mission scheduling

### 5.1 Confirmation snapshot

One exact Mission confirmation freezes:

- goal and ordered steps;
- assigned Workers and roles;
- configured transport per Worker;
- compact runtime identity hash over Worker command, adapter argv, role prompt, and project runtime backend/session/socket identity;
- allowed project/file scope and action classes;
- approval and policy snapshot;
- compact Skill and Memory provenance;
- declared tests and acceptance criteria;
- timeout, retry, and budget limits;
- stable Mission and execution hashes.

The daemon may advance ordinary work inside this snapshot. A Leader or Worker
cannot silently add a step, Worker, tool class, transport, external destination,
or permission. Drift produces a new preview and pauses the Mission.

### 5.2 One bounded transition per scheduler iteration

Each iteration:

1. reads authoritative state;
2. validates state and frozen-scope hashes;
3. selects one legal next transition using a pure gate;
4. persists the intent/attempt before external effects when required;
5. performs at most one bounded effect;
6. persists receipt, result, blocker, or ambiguity;
7. emits compact audited events through the recoverable outbox.

The scheduler does not ask an LLM to infer current state. LLMs may plan or
summarize, while Python state machines own sequencing, locks, retries, timeout,
approval, ownership, recovery, and stopping conditions.

### 5.3 Automatic work allowed by one Mission confirmation

Within the frozen snapshot the daemon may:

- persist one exact tmux Worker start claim and start that frozen Worker;
- dispatch the assigned Worker;
- wait for and capture a reply;
- validate structured reply, artifact, and declared tests;
- construct a compact handoff;
- activate the next eligible Worker;
- run declared local verification;
- record trace, artifacts, stop reasons, and summaries;
- continue an approved review-and-revision sequence within its fixed bounds.

### 5.4 Mandatory pause conditions

The daemon pauses for:

- a new permission or tool class;
- destructive, publishing, deployment, push, or external-send activity;
- credential or sensitive-data access;
- project, file, Worker, step, or transport scope drift;
- unverifiable Worker results or artifacts;
- an unknown external-effect outcome;
- timeout, retry, policy, or budget exhaustion;
- Worker loss or protocol inconsistency;
- explicit human takeover, pause, or cancellation.

### 5.5 Worker-mediated completion and handoff

Workers never poll another Worker's pane/session and never authorize or schedule
one another. Completion enters the system only after AgentDeck validates the
transport result and persists a terminal attempt/reply/artifact fact. The next
Worker receives a compact handoff containing only validated completion summary,
artifact paths and hashes, test summary, known risks, next-step input, and trace
references. It excludes full transcripts, reasoning, credentials, and unrelated
context.

## 6. Worker transports and ownership

### 6.1 Explicit transport routing

The configured transport is the effective transport unless a human confirms a
new exact reroute preview. ACP failure never silently falls back to tmux. A
blocked ACP Worker pauses the Mission with readiness facts and explicit repair
or reroute controls.

### 6.2 ACP path

For ACP Workers the daemon owns create/load/resume, prompt, streamed update,
permission, cancellation, and formal stop-reason handling. Only compact mapped
updates enter durable state. Permission requests are admitted atomically within
turn bounds and bridged through AgentDeck policy.

### 6.3 tmux path

For legacy tmux Workers the daemon uses readiness, explicit dispatch, structured
reply tokens, bounded capture, and existing ledger primitives. Pane text alone
is never completion evidence. tmux remains the read-only live mirror and
explicit takeover surface.

### 6.4 Human takeover

Takeover waits for a safe boundary or explicit cancellation, presents an exact
preview, and changes Worker ownership to `human_owned`. Automation stops sending
input to that Worker and blocks conflicting steps. Returning control requires a
reconciliation preview and validation of session, worktree, artifacts, and
human-reported changes; unverifiable outcomes become ambiguous.

## 7. Approval, permission, and policy

Every effect passes three independent gates:

1. frozen Mission scope;
2. policy and permission;
3. runtime safety and ownership.

An ACP permission recommendation is context, not authorization. In-scope
ordinary permissions may follow the frozen policy snapshot; a new class or
high-risk action creates a pending exact-bound PermissionRecord and pauses.
tmux text claiming user approval is ignored unless a matching valid approval
exists in the AgentDeck ledger.

Approval and permission confirmation binds to the current record content,
scope, risk, generation, and expiry. Any drift invalidates the old preview.
Client controller status does not grant Worker ownership, and Worker ownership
does not grant Mission scope.

## 8. Persistence and ProjectView

### 8.1 Compact persistence

M2 persists daemon lifecycle summaries, frozen Mission execution facts,
attempt/receipt state, Worker session/ownership, compact reply and handoff,
artifact path/hash, test summary, permission/approval, stop reason, blocker,
and recovery classification.

It does not persist full conversation transcripts, model reasoning, raw ACP
payloads, raw tool I/O, credentials, auth state, complete tmux history, or full
Worker context by default.

### 8.2 ProjectView additions

ProjectView remains the single client observation model and gains compact
summaries:

- `daemon`: state, health, client count, controller presence, idle-exit state,
  and protocol compatibility without raw PID/socket/home-path disclosure;
- `scheduler`: active Mission, current step, current transition, next legal
  transition, and blockers;
- `recovery`: resumable/waiting-human/ambiguous/blocked/terminal classification,
  reason, and safe controls.

Socket events are only refresh notifications. Missing events never loses
business facts; clients can rebuild their view from ProjectView and the ledger.

### 8.3 Contracts and workbench

M2 publishes and registers:

- `daemon-runtime/v1`;
- `mission-scheduler/v1`;
- `client-session/v1`.

Workbench embeds same-source `daemon_runtime_card`,
`mission_scheduler_card`, and `client_session_card`. Every control continues to
carry `kind`, `label`, `command`, `safety`, `enabled`, and `blocker`; rendering a
control never authorizes it, and the daemon revalidates every mutation.

### 8.4 Existing project migration

M2 inspects old state without mutation, presents a migration preview, backs up
affected state, and migrates only after exact confirmation. A historical
Mission without a complete frozen execution snapshot remains inspect-only or
requires a new Mission confirmation; it is never silently resumed in the
background.

## 9. Recovery and duplicate-effect prevention

### 9.1 Attempt lifecycle

An external action is recorded before dispatch and progresses through explicit
states such as `prepared`, `submitted`, `running`, and a terminal state. Stable
`mission_id`, `step_id`, `attempt_id`, and `dispatch_key` identify it.

These keys prevent AgentDeck from creating a known duplicate, but M2 does not
assume every CLI or external effect is end-to-end idempotent.

### 9.2 Restart reconciliation

Before scheduling new effects, a restarted daemon performs read-only
reconciliation across Mission, attempt, turn, reply, artifact, approval,
permission, Worker session/pane, ownership, and outbox facts. Each active
Mission becomes exactly one of:

- `resumable`: evidence proves the next internal transition is safe;
- `waiting_human`: approval or permission is pending;
- `ambiguous`: an external outcome cannot be proven;
- `blocked`: transport, Worker, or state is invalid;
- `terminal`: completed, failed, cancelled, or safely interrupted.

Reconciliation persists its classification and audit before the scheduler may
act. It never sends Worker input by itself.

### 9.3 Retry boundary

Safe automatic retries include read-only projection, event subscription,
recoverable outbox flush, an unsent deterministic internal transition,
receipt-backed protocol query, and bounded readiness checks.

Unknown Worker dispatch, file-changing turn, permission decision, publish/push,
external send, human-owned Worker input, and any ambiguous action cannot be
automatically retried.

## 10. Failure and shutdown semantics

All failures resolve to a visible state: `retryable`, `waiting_human`,
`ambiguous`, `blocked`, `failed`, `interrupted`, or `completed`. Exceptions
cannot be swallowed as success. A failed step cannot be skipped to activate a
later Worker.

Normal daemon stop is rejected when active work exists. The exact-confirmed
force path stops new scheduling, requests cancellation where the transport
supports it, sends no new tmux input, records incomplete attempts as
interrupted or ambiguous, flushes safe audit records, and exits. Restart always
reconciles before resuming.

## 11. Delivery slices

### M2a: Background coordination foundation

Deliver on-demand one-per-project daemon lifecycle, versioned local RPC,
multi-client observation, one controller lease, idle exit, safe stop, compact
ProjectView surfaces, and the three discovery contracts. Existing deterministic
read-only CLI behavior remains available and compatible.

### M2b: Recoverable background Mission

Move confirmed Mission advancement into the daemon; add frozen execution
snapshot, deterministic scheduler, attempt/receipt lifecycle, pause/resume,
approval/permission bridge, crash reconciliation, ambiguity handling, compact
reconnection summary, and migration preview.

### M2c: Real multi-Agent acceptance

Run one disposable implementation-review-revision-acceptance Mission with real
Codex and Claude, at least one real ACP Worker, explicit transport facts, tmux
visibility/takeover, client disconnect/reconnect, one safe permission pause,
and full ProjectView/ledger/contract agreement.

Each slice receives its own TDD tasks, HISTORY update, focused/full regression,
local commits, and a human review gate before the next slice.

## 12. Test strategy

### 12.1 Pure state tests

Test daemon lifecycle gates, scheduler transitions, controller lease,
confirmation scope, pause/resume/cancel, attempts/receipts, recovery
classification, idle exit, protocol compatibility, illegal-transition zero
write, and deterministic controls without real processes.

### 12.2 Real local process tests

In temporary projects test concurrent startup producing one daemon, stale
metadata recovery, project identity mismatch, observer write rejection,
controller expiry/takeover, incompatible-client read-only mode, idle exit,
active-work stop refusal, exact-confirmed force stop, bounded frames,
backpressure, disconnects, and cleanup.

### 12.3 Scheduler integration tests

With fake Leader, fake ACP Agent, and fake tmux backend, confirm one Mission,
disconnect the client, complete Worker A, prove AgentDeck—not Worker A—activates
Worker B with a compact handoff, pause on permission, reconnect a controller,
resolve the decision, and complete. Assert no duplicate dispatch, no transcript
or credential persistence, and agreement among state, ledger, events,
ProjectView, and contracts.

### 12.4 Crash matrix

Terminate the daemon before preparation, after preparation/before dispatch,
after dispatch/before receipt, after receipt/before reply, after reply/before
handoff, after handoff/before next dispatch, during permission wait, during
outbox flush, and during shutdown. Each restart must deterministically resume,
wait, block, or become ambiguous without repeating unknown external effects.

### 12.5 Live acceptance

The final disposable rehearsal must:

1. start from bare `agentdeck` in a fresh project;
2. create and confirm one exact Mission in natural language;
3. use distinct implementation and review Workers;
4. use at least one installed/authenticated real ACP adapter;
5. expose the other Worker's explicit ACP or tmux transport;
6. prove Worker A completion is mediated by AgentDeck before Worker B starts;
7. close the interactive client while work remains active;
8. prove the daemon continues;
9. reconnect through bare `agentdeck` and render deterministic recovery;
10. exercise one permission or safety pause and explicit decision;
11. complete or safely pause the Mission;
12. prove ProjectView, ledger, artifacts, trace, hashes, contracts, and file
    effects agree with no duplicate dispatch or unauthorized action.

Evidence is sanitized: no raw transcript, prompt/tool content, credential,
token/email/auth data, environment dump, native opaque session id, or absolute
home path.

## 13. Out of scope

M2 does not implement:

- A2A Client or Server;
- remote daemon or multi-machine scheduling;
- global project roaming or system notifications;
- Desktop/IDE Workspace Client;
- Windows named pipes;
- default full-transcript persistence;
- automatic Agent installation, login, trust, or credential access;
- AgentDeck as an ACP Agent;
- Skill marketplace or remote dependencies;
- a terminal emulator or native same-session TUI attach.

These require separate brainstorming, written specs, plans, and human approval.

## 14. Completion gate

M2 is complete only when M2a, M2b, and M2c are locally committed; focused and
full tests, compileall, diff checks, contract validation, crash matrix, and the
sanitized real multi-Agent rehearsal pass; client disconnect does not stop a
confirmed Mission; unsafe or uncertain work pauses; Worker B starts only after
AgentDeck validates Worker A completion; no M2 out-of-scope system enters the
diff; and the human reviews the evidence before any merge or push.
