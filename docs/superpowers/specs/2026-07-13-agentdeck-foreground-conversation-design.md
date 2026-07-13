# AgentDeck M1 Foreground Conversation Design

**Date:** 2026-07-13  
**Status:** Human-approved design; implementation requires a separately approved plan  
**Milestone:** Phase 3 M1 of M1 → M2 → M3  
**Branch:** `codex/phase3-conversation-design`  
**Baseline:** `9ab8e604`

## 1. Objective

Running `agentdeck` with no subcommand in a project opens a foreground,
continuous natural-language conversation. The user may use an API LLM or an
Agent CLI as the Leader. The Leader understands the objective and proposes a
frozen Mission; AgentDeck remains the authority for validation, confirmation,
assignment, dispatch, approval, workflow, audit, and recovery.

M1 composes existing AgentDeck primitives into the primary product experience.
It does not replace the Mission, approval, workflow, dispatch, ProjectView,
skill, memory, ACP, or tmux systems.

## 2. Product principles

### 2.1 AgentDeck is the orchestration kernel

ACP is the standard machine communication channel between AgentDeck and an
Agent. It does not perform Mission planning or Agent-to-Agent orchestration.
Workers communicate through AgentDeck's ledger and governed handoffs rather
than opening unmanaged peer connections.

```text
User → AgentDeck conversation → Leader → Mission preview
                                      ↓
                           AgentDeck orchestration
                              ├─ ACP → Agent
                              └─ tmux → legacy Agent
```

### 2.2 ACP control plane plus tmux visibility plane

- ACP is authoritative for structured sessions, prompts, streaming updates,
  permission requests, cancellation, and formal completion.
- tmux remains the visible terminal, debugging, legacy transport, and governed
  human-takeover surface.
- ProjectView and the append-only ledger are the shared source of truth.
- ACP events may be rendered into a read-only tmux live mirror. AgentDeck does
  not infer ACP state by scraping that mirror.
- One session has one writer. Human takeover and return require explicit,
  audited ownership transitions.
- A native TUI may attach to the same session only when the adapter proves that
  capability. A new native session must never be presented as the old session.

### 2.3 Composition over replacement

The new conversation path must reuse existing producers, validators, controls,
and state transitions. It must not create a second Mission, approval, dispatch,
or recovery system. Existing deterministic commands, especially
`agentdeck leader chat --message`, remain compatible script/debug interfaces.

## 3. Primary user experience

Inside an initialized project:

```text
$ agentdeck
AgentDeck · my-project
Leader: Claude Code / Opus
Runtime: foreground
Mission: none
Workers: Codex ready · Claude ready

你 ›
```

If the configured project Leader is ready, AgentDeck reuses it. If no Leader is
configured or it is unavailable, AgentDeck displays detected API LLM and Agent
CLI candidates plus compact blockers. It never silently selects, installs, or
authenticates a backend.

Outside an initialized project, AgentDeck enters a setup conversation. It
shows a `project init` preview and initializes only after the user gives an
exact confirmation bound to that preview.

### 3.1 Leader selection

The Leader is a logical project role backed by either:

- an API LLM provider; or
- an Agent CLI through ACP when available, with a separately identified CLI
  fallback only when the user explicitly chooses it.

`/leader use <backend>` changes only the current foreground session.
`/leader assign <backend>` creates a persistent project-configuration preview
that must be confirmed before writing.

`/model` lists models compatible with the selected Leader backend.
`/model all` is read-only discovery. Selecting an incompatible model returns a
blocker; it never switches Leader, provider, transport, or authentication.

### 3.2 Natural-language Mission flow

```text
User objective
  → deterministic/open-ended intent routing
  → LeaderGateway
  → proposed Mission
  → existing Mission validator and preview
  → exact human confirmation
  → existing approval/workflow/dispatch machinery
```

The Leader may propose Worker, role, model, and transport assignments. It may
not dispatch, approve, install, authenticate, read credentials, or change
configuration without an explicit AgentDeck control.

## 4. Components

### 4.1 `TerminalConversationUI`

Owns TTY input, rendering, prompt/banner display, Ctrl-C, EOF, and exit. It does
not execute arbitrary shell commands or contain business logic.

### 4.2 `ConversationSession`

Owns foreground in-process state:

- conversation and active turn identities;
- project identity;
- temporary Leader/model/role selections;
- bounded in-memory context;
- exact pending preview binding;
- cancellation and ownership state.

M1 does not add durable full-transcript persistence. Complete user and Leader
messages live only in bounded memory for the foreground process. Durable
records contain compact identities, intent, hashes, backend/transport facts,
results, and lineage. Existing leader-chat persistence is not expanded.

### 4.3 `ConversationRouter`

Reuses the existing leader-chat intent producers and validators through a pure,
non-printing application boundary. Registered deterministic intents such as
help, status, approvals, trace, setup, and exit work without an LLM. Unknown or
open-ended requests require a ready Leader; otherwise they return a provider
setup preview rather than guessing.

### 4.4 `LeaderGateway`

Provides one typed boundary for API-backed and Agent-backed Leaders. Each
response records the exact backend kind, provider/Agent, model, transport,
capabilities, and blockers.

Agent-backed Leaders prefer ACP. A CLI fallback is a distinct backend identity,
not an ACP fallback hidden inside a request. A failed turn never silently moves
to another backend.

Every backend maps into one internal `LeaderMissionCandidate`, which is exactly
the existing provider-plan schema: non-empty `goal`, `summary`, and ordered
`steps[]`; every step contains `step`, `agent_id`, `role`, `task`, `risk`, and
`requires_approval=true`. It is not a new public contract.

API responses, ACP message chunks, and CLI output are assembled only in bounded
process memory (at most 2 MiB and 256 fragments; ACP retains Phase 2's 64 KiB
frame limit). The implementation extracts a shared single-write application
primitive, `create_mission_preview_from_candidate(...)`. Its mandatory order is
`validate_provider_plan_schema(raw) → validate_mission_plan(raw) → normalize_mission_plan_metadata(raw) → validate_mission_plan(normalized) → construct plan/Mission/event/public-card candidates in memory → validate_mission_preview_contract(public card) → one locked state commit containing plan, Mission, conversation transitions, and pending EventRecord outbox entries`.
Validation occurs before `record_plan()` or any Mission/approval/dispatch write.
The existing `create_mission_preview()` keeps its provider-call behavior and
then enters this same primitive, preserving compatibility without a second
provider call from the conversation path. Malformed framing, multiple
documents, incomplete schema, or over-limit output terminalizes the turn with
zero plan, Mission, approval, or dispatch writes. Raw assembled text is
discarded.

### 4.5 `WorkerTransportRouter`

Consumes the Mission's frozen Agent and transport provenance. An ACP-configured
Worker uses ACP only when the exact capability/readiness checks pass. A tmux
Worker uses the existing terminal path. Failure never silently changes
transport or assignee; AgentDeck offers explicit reroute/reassignment previews.

### 4.6 tmux live mirror and takeover

ACP updates are projected through the ledger/ProjectView into a bounded,
redacted live view. The mirror is read-only. M1 takeover is allowed only when
the Agent session is `ready`, has no active turn or pending permission, and the
Agent is not executing a dispatched workflow step. An active operation exposes
a separate bounded cancel/wait preview, never immediate takeover.

After exact confirmation, ownership moves
`agentdeck_owned → takeover_pending → human_owned`. While human-owned, Mission
automation cannot dispatch to that Agent; human prompts still use the same
governed ACP turn runner. `/return-control` is a preview enabled only when the
session is ready with no active turn/permission and the capability, session,
and execution digest still match. It moves
`human_owned → return_pending → agentdeck_owned`. If the foreground process
exits while human-owned, persisted ownership remains human-owned and a later
session must explicitly return control. M1 does not promise native TUI
same-session attach.

Pending ownership is fail-closed and recoverable:
`takeover_pending→agentdeck_owned` records failed/cancelled takeover, and
`return_pending→human_owned` records failed/cancelled return. No writer may
send a prompt in either pending state. A disconnected or terminal Agent
projects an ownership blocker and cannot finish takeover/return until the exact
session is re-established and a fresh preview is confirmed.

## 5. Leader and Agent compatibility

### 5.1 Claude

Use the already validated `claude-agent-acp` path and Phase 2 ACP client. M1
must not weaken Phase 2 protocol, permission, privacy, or lifecycle invariants.

### 5.2 Hermes

Hermes `0.17.0` exposes native `hermes acp`; local `hermes acp --check` passes.
M1 may target the foreground `initialize/new/prompt/stream/permission/cancel`
surface after conformance tests. Hermes load/resume behavior is not assumed
compatible: the installed implementation replays on resume and may create a
new session when the requested session is missing, which conflicts with
AgentDeck's strict no-replay/no-fallback resume rules. M1 does not use Hermes
durable resume.

### 5.3 Codex

Codex CLI `0.131.0` has no native `codex acp` command. OpenAI's app-server and
MCP server are different protocols. A Codex ACP adapter may be supported only
through explicit configuration, read-only preflight, pinned provenance, fake
conformance, and human-installed live acceptance. The current machine has no
`codex-acp`; AgentDeck must not install it. Until then, Codex may be chosen as
an explicitly identified CLI-backed Leader or tmux Worker, never mislabeled as
ACP.

### 5.4 API LLMs

DeepSeek, OpenAI-compatible, Anthropic, or other configured providers remain
API-backed Leader options. A model is not an Agent runtime. API provider
selection never implies Worker readiness or execution authority.

## 6. Slash commands

Natural language is primary. M1 includes a small deterministic registry serving
the main workflow:

```text
/help
/leader
/model
/team
/role
/status
/approvals
/trace <id>
/takeover <agent>
/return-control
/quit
```

Unknown slash commands are rejected with help and never forwarded to an LLM or
provider. Commands have stable identity, argument schema, safety, handler,
contract, and audit metadata. They grant no authority beyond the underlying
control. Full Codex/Claude/Hermes slash-command parity is a separate future
slice, not M1.

`/role use <role>` changes only the current session. `/role assign <role>
--agent <agent>` creates a persistent assignment preview that requires exact
confirmation.

## 7. Conversation lifecycle

The durable conversation enum is only
`created|ready|busy|waiting_confirmation|closing|closed`. Routing,
`waiting_leader`, `presenting_preview`, and `executing` are durable **turn**
states, not a second conversation enum. `TerminalConversationUI` derives its
label from `conversation_state + active_turn_state`; it does not persist a
separate UI phase. `conversation-runtime/v1` exposes those two source states
and the derived label explicitly.

Turns end as `completed`, `blocked`, `failed`, `cancelled`, or `ambiguous`.

- Ctrl-C during work cancels the current turn and returns to the prompt.
- Ctrl-C while idle clears input; two consecutive idle Ctrl-C events exit.
- `/quit`, `exit`, `退出`, or EOF exits safely.
- Exiting never stops Workers, approves work, or changes Mission state.
- Uncertain completion is `ambiguous`, never fabricated as success.

## 8. Exact preview binding

Every state-changing or executing action first produces a binding containing:

```text
preview_id, control_id, command, arguments_hash,
execution_digest, expires_at, safety
```

Natural-language confirmation consumes only the unique current binding when:

- it is unexpired;
- the control-specific execution digest still matches;
- command and arguments hashes are unchanged;
- the control remains enabled; and
- the input is an explicit confirmation intent.

Any drift invalidates the binding and requires a new preview. The deterministic
CLI command and natural-language confirmation call the same application
handler. A preview/control identity is not a reusable authorization token.

`execution_digest` is the SHA-256 of canonical JSON produced by the same
control-specific confirmation-facts helper at preview and execution time. It is
not a whole-state hash and ignores unrelated chat/event writes. Mission facts
include project, control and target identity; exact command/arguments;
Mission/plan IDs and `plan_hash`; Mission lifecycle/blockers; frozen selected
Agent/startup provenance; relevant approval statuses; workflow identity; and
the exact config-file bytes hash. Other controls define equally explicit fact
tuples. Confirmation phrases are recognized only by the deterministic router,
never interpreted by an LLM. Natural language and CLI both call
`execute_bound_control(binding_id, expected_digest)`.

## 9. Persistence and contracts

M1 introduces append-only compact base records:

```text
conversation_sessions[]
conversation_turns[]
conversation_preview_bindings[]
conversation_state_transitions[]
```

`conversation_state_transitions[]` uses `entity_type` of
`conversation|turn|preview|ownership` and is the only lifecycle truth. Base
records are never rewritten. Exact allowed edges are:

- conversation: `created→ready`, `ready→busy|waiting_confirmation|closing`,
  `busy→ready|waiting_confirmation|closing`,
  `waiting_confirmation→ready|busy|closing`, `closing→closed`;
- turn: `created→routing`,
  `routing→waiting_leader|presenting_preview|executing|completed|blocked|failed|cancelled`,
  `waiting_leader→presenting_preview|completed|blocked|failed|cancelled|ambiguous`,
  `presenting_preview→completed|failed|cancelled`, and
  `executing→completed|blocked|failed|cancelled|ambiguous`;
- preview: `pending→consumed|expired|invalidated`;
- ownership: the single-writer sequence in section 4.6.

Terminal entities cannot transition again. At most one pending preview and one
active turn exist per conversation; a preview is consumed exactly once.

Every append validates the complete history in O(n), including identity,
reference, legal edge, current state, uniqueness, active binding, and ownership
before writing. One locked `state.json` commit contains domain/base records,
transitions, and pending EventRecord outbox entries. Delivery to `events.jsonl`
occurs afterward through the existing recoverable, idempotent outbox flush; M1
does not claim a cross-file atomic transaction. A flush failure keeps the
durable outbox item, returns an audit/outbox blocker before a success response,
and never repeats the plan or Mission. Invalid identity, stale state, illegal
edge, duplicate ID, unknown reference, failed digest, or invalid Mission
candidate is rejected before the locked commit and is byte-for-byte zero-write,
including pending outbox state.

Before initialization, the setup `ConversationSession` and binding are
memory-only. Running `agentdeck` outside a project creates no `.agentdeck`,
lock, event, or hidden state. Confirmation rechecks canonical cwd,
project-marker absence, and the setup digest; the existing `project init`
transaction is the first filesystem side effect and retains its current,
separately tested recovery semantics. M1 promises byte-for-byte zero-write only
before confirmed init; it does not claim a new multi-file atomic rename.

After init succeeds, AgentDeck appends compact
`project_initialized_from_conversation` lineage and hashes, not the pre-init
transcript. If that audit append fails, initialization remains successful and
is never rolled back or reported as failed; the conversation returns
`initialized_with_audit_blocker`, blocks further actions, and directs the user
to deterministic diagnosis/recovery. An init failure uses existing diagnostics
and never writes a conversation record into an uninitialized directory.

New versioned discovery contracts:

- `conversation-runtime/v1`: lifecycle, current Leader/model, pending binding,
  cancellation, ownership, and controls;
- `leader-backend/v1`: backend kind, identity, readiness, transport,
  capabilities, blockers, and setup/use/assign controls;
- `worker-transport/v1`: explicit transport readiness/capabilities, fallback
  blocker, reroute controls, live-mirror, and takeover affordances.

Contracts are registered in the contract index and reflected in ProjectView
and workbench. Source constants and validators are single-source; CLI code does
not duplicate schemas.

## 10. Failure and privacy rules

- A missing Leader blocks only open-ended requests. Deterministic governance
  intents remain available.
- Provider/Agent failures terminalize the turn and never create a partial
  Mission.
- Invalid Leader output is rejected by existing Mission validators before any
  Mission/approval/dispatch write.
- Project/state drift invalidates confirmation and performs zero execution.
- ACP failure never silently becomes tmux; pane loss never becomes completion.
- Timeout, cancellation, EOF, and disconnect are bounded and fail closed.
- UI and diagnostics are bounded and redacted. State/evidence never stores
  credentials, environment dumps, raw tool input, full adapter stderr, or a new
  full transcript.

## 11. Test strategy

TDD must cover:

- no-subcommand TTY entry and non-TTY non-hanging behavior;
- uninitialized-project preview and exact confirmation;
- configured/missing/unready Leader behavior;
- deterministic intents without an LLM;
- API, ACP Agent, and explicit CLI-backed Leaders;
- valid/invalid Mission generation;
- binding identity/hash/expiry/state-drift and zero-write rejection;
- natural and deterministic confirmation sharing one handler;
- Ctrl-C, EOF, localized exit, timeout, disconnect, and ambiguity;
- bounded in-memory context and absence of new transcript persistence;
- explicit ACP/tmux Worker routing and no silent fallback;
- read-only live mirror and single-writer takeover transitions;
- legacy CLI, Leader chat, Mission, workflow, approval, tmux, skill/memory, and
  Phase 2 ACP regression.

With no subcommand and non-TTY stdin, AgentDeck fails fast with exit code 2,
empty stdout, and one bounded stderr hint directing scripts to deterministic
commands such as `leader chat --message`. It performs zero project, provider,
adapter, runtime, tmux, state, event, lock, or outbox writes. `agentdeck --help`
remains a successful deterministic help path.

Each semantic task requires RED, minimal GREEN, self-review, fresh spec review,
fresh quality review, and a local commit with HISTORY updates.

## 12. Live acceptance

In a disposable project:

1. run `agentdeck` from an uninitialized directory;
2. preview and confirm initialization;
3. select or assign a Leader;
4. complete at least three natural-language turns;
5. generate a Mission assigning implementation and review to different Agents;
6. confirm the exact Mission in natural language;
7. start work through existing governance primitives;
8. exercise at least one ACP Agent and expose explicit legacy transport facts;
9. inspect status, approvals, and trace;
10. cancel one turn without exiting, then exit safely;
11. prove ProjectView, ledger, contracts, counts, hashes, and file effects agree.

Evidence contains versions, commits, internal IDs, backend/transport identity,
states, stop reasons, counts, hashes, commands, and results. It excludes
transcript, credentials, token/email/auth data, environment dumps, raw tool
input, and absolute home paths.

## 13. Out of scope for M1

- project daemon and cross-process full conversation recovery;
- global roaming and Workspace Client;
- remote daemon/execution;
- automatic install, login, or credential access;
- AgentDeck as an ACP Agent;
- full upstream slash-command replication;
- unproven native TUI same-session attach;
- unrelated migration or refactoring.

These remain M2, M3, or separately approved product slices. M1 completion does
not authorize them.
