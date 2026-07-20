# AgentDeck Product Kernel Rewrite Design

**Status:** Approved active design and Rewrite Context Firewall; implementation
follows the reviewed TDD plan and its explicitly approved corrections

**Date:** 2026-07-18

**Authority:** This document is the active implementation authority. The
Product North Star protects long-term product intent. P0/P1/M2/M2c remain
recoverable historical evidence but cannot create requirements, dictate task
order, or veto the rewrite.

**Normative Task 15B correction:** The conversation-approved, written-review
pending [Task 15B Project Pause, ACP Cancellation, and Explicit Resume Design](2026-07-20-task-15b-acp-cancellation-recovery-design.md)
defines the exact project-wide exit, foreground cancellation, explicit resume,
and restart-recovery semantics for sections 5.2, 10.4, and 10.4.3. It narrows
no product promise: current ACP cancel is a bounded notification plus local
owner shutdown; a fresh process without durable raw resume authority recovers
conservatively; and paused work starts again only after explicit `/resume`.

**Branch:** `codex/product-kernel-rewrite`

## 1. Decision summary

AgentDeck will perform a clean product-kernel rewrite alongside the existing
implementation. The rewrite is not a deletion-first exercise and is not an
extension of the historical M2c acceptance harness. It creates a new product
authority from first principles, selectively reuses existing ACP, provider,
tmux, testing, and domain knowledge through explicit adapters, and switches the
bare `agentdeck` entrypoint only after a real Golden Product Gate passes.

The MVP product promise is:

> Run `agentdeck`, select a real Leader and one of three Codex-style permission
> profiles, describe a development goal in natural language, review one
> human-readable frozen Mission, confirm once, and watch Codex and Claude carry
> out implementation, review, revision, and acceptance through ACP while tmux
> shows each Agent's real work stream. Exit and re-enter without losing state.

The MVP is deliberately foreground-first. Closing the terminal does not yet
promise continued daemon execution. It does promise durable state, safe
interruption, and deterministic re-entry. Background daemon execution is a
post-MVP enhancement.

The MVP does not include memory, skill learning, Hermes-style self-evolution,
or a Hive-style browser GUI. Those remain approved post-MVP directions and
must build on the same Mission, permission, ACP, event, and ProjectView
authority rather than introduce another execution system.

## 2. Why a rewrite is necessary

The existing repository contains valuable capabilities, but the user-visible
product path is not coherent:

- bare `agentdeck` can expose raw JSON instead of a product conversation;
- first-run setup can default to an unavailable DeepSeek provider even when
  local Codex and Claude CLIs are ready;
- a user's original natural-language goal can be lost across project setup;
- generic `backend_failure`, `BLOCKED`, and timeout labels hide the real stage;
- `/exit` is not a reliable product control;
- Conversation, CLI, daemon, storage, transport, and projection concerns are
  spread across large coupled modules;
- the project spent disproportionate effort hardening one M2c live harness
  before proving a simple repeatable product journey.

The rewrite therefore changes the order of work. It first makes AgentDeck
behave like a product, then hardens the boundaries proven important by that
product. Historical work is retained as evidence and a source of adapter and
test assets, but it does not dictate the new module boundaries.

## 3. Goals

### 3.1 Product goals

1. `agentdeck` opens a continuous natural-language product conversation.
2. AgentDeck discovers authenticated Codex CLI, Claude CLI, and their ACP
   adapters from the user's actual environment.
3. The user explicitly selects a Leader: Codex CLI, Claude CLI, or an
   OpenAI-compatible API provider such as DeepSeek, Kimi, GLM, or Custom.
4. The Leader proposes a structured Mission Draft; AgentDeck validates and
   renders a human-readable Mission Preview.
5. One natural-language confirmation binds the exact Preview hash.
6. The default coding flow is Codex implementation, Claude review, Codex
   revision, and Claude acceptance.
7. All automatic Leader/Worker task, progress, permission, result, review,
   revision, and acceptance communication uses ACP.
8. tmux displays each Agent's real decoded ACP work stream in separate panes.
9. SQLite persists the ProductSession, Mission, Task, Attempt, permission,
   handoff, evidence, diagnostic, and event lineage.
10. Errors are understandable without reading Python, JSON-RPC, or internal
    schema terminology.

### 3.2 Architecture goals

1. A pure domain kernel owns Mission and permission invariants.
2. Application services own use-case sequencing.
3. Ports isolate external capabilities.
4. Adapters are the only place allowed to wrap existing implementation.
5. Product presentation is replaceable and does not contain domain rules.
6. Workers never write AgentDeck state or schedule peers directly.
7. tmux is observation, not communication or completion authority.
8. One project-local SQLite database and one foreground writer form the MVP
   structured-state authority.
9. The new kernel is testable entirely with Fake Ports and a Fake ACP server.
10. The old and new kernels remain side by side until cutover.

## 4. Non-goals

The MVP does not include:

- background ProjectDaemon execution after the foreground terminal closes;
- arbitrary multi-Agent graphs, broadcast, callbacks, or dynamic auto-staff;
- direct Worker-to-Worker scheduling;
- CLI/PTY or tmux screen injection as an automatic transport fallback;
- cross-machine A2A;
- a web, mobile, or desktop GUI;
- a WispTerm-class terminal emulator;
- automatic ACP adapter installation or authentication;
- arbitrary API providers acting as local file-editing Workers;
- automatic git commit, push, merge, reset, or destructive checkout;
- silent skill installation, memory writes, or learning changes;
- Hermes-style self-modification before the MVP authority is proven;
- public deployment of the website-reproduction acceptance artifact.

## 5. User experience

### 5.1 First launch

`agentdeck` performs read-only discovery and presents human-readable choices:

```text
AgentDeck

Project     /path/to/project
Codex CLI   ready
Claude CLI  ready
Codex ACP   ready
Claude ACP  ready
tmux        ready

Select Leader
  1. Codex CLI
  2. Claude CLI
  3. API Provider

Permission
  Approve for me
```

If no Leader has been configured, deterministic controls remain usable. An
open-ended goal is durably retained while setup completes and automatically
resumes afterward.

### 5.2 Continuous conversation

The product state machine is:

```text
setup
ready
drafting
awaiting_confirmation
running
awaiting_approval
paused
needs_attention
completed
failed
cancelled
```

Natural language is the primary interface. Deterministic slash commands are:

```text
/help
/status
/setup
/leader
/model
/agents
/permissions
/mission
/pause
/resume
/takeover
/diagnose
/exit
```

`/exit` safely pauses the whole executing project, not merely one Worker. Its
durable request closes the dispatch gate before another stage may start. If a
Worker is active, the foreground MVP explains that project exit will interrupt
the exact Attempt and requires `/exit confirm <request-id> <attempt-hash>`
before stopping it. Successful confirmation cancels the exact Worker, records
the Attempt interruption, and changes the ProductSession to `paused` in one
command transaction. Between stages, `/exit` pauses without sending ACP.
`/exit decline <request-id> <attempt-hash>` consumes the same request without
stopping work. A stale identity, changed Attempt snapshot, or missing real
cancellation capability fails closed.

Re-entry reconstructs the latest nonterminal ProductSession for the current
project only and never starts paused work. Explicit `/resume` derives the first
unclosed stage from committed Mission, Attempt, Handoff, and Evidence facts,
then creates a higher-ordinal Attempt for that stage. Closed stages never run
again, and `outcome_unknown` blocks resume. Setup, drafting, and
awaiting-confirmation sessions have no executing Worker and may close without a
synthetic paused transition.

### 5.3 Mission Preview

The Preview is human-readable and includes:

- objective and scope;
- Leader backend and resolved model identity;
- Worker Instances and roles;
- ordered Tasks and dependencies;
- ACP routes;
- permission profile and project boundary;
- acceptance criteria;
- retry and revision budgets;
- explicit non-goals and risks;
- Preview ID, version, and canonical content hash.

The user can revise assignments, scope, criteria, or permissions in natural
language. Every semantic change creates a new Preview. Confirmation consumes
only the current exact Preview. An old or drifted Preview cannot start work.

## 6. Architecture and dependency rules

The permanent new modules are:

```text
src/agentdeck/
  kernel/
    session.py
    mission.py
    permissions.py
    agents.py
    execution.py
    diagnostics.py
    events.py
  application/
    session_service.py
    leader_service.py
    mission_service.py
    execution_service.py
    approval_service.py
    recovery_service.py
  ports/
    leader.py
    worker.py
    transport.py
    store.py
    approval.py
    runtime.py
    clock.py
  adapters/
    discovery.py
    sqlite.py
    sqlite_approval.py   # canonical approval row persistence
    acp.py              # ACP Worker mapping and outcome authority
    acp_transport.py    # official SDK and bounded stdio lifecycle
    acp_leader.py       # structured Leader proposal projection
    tmux_observer.py
    providers.py
  product/
    shell.py
    renderer.py
    slash_commands.py
    presenter.py
    bootstrap.py
```

The dependency direction is:

```text
Product -> Application -> Kernel
                   \-> Ports <- Adapters
Composition Root -> Product + Adapters
Legacy code -> only through Adapters
```

Hard rules:

- `kernel/` performs no filesystem, database, environment, subprocess, tmux,
  network, provider, or clock I/O;
- `kernel/` cannot import legacy AgentDeck modules;
- `application/` depends only on Kernel and Ports;
- `product/` renders state and invokes application use cases but does not
  decide Mission validity or permissions;
- adapters cannot mutate domain objects outside application service commands;
- the logical ACP adapter boundary is physically split so Worker outcome
  classification, stdio lifecycle, and Leader projection remain independent
  and each Product Kernel source file stays within the 500-line limit;
- an architecture test enforces the import boundary;
- the new Product Shell cannot call old CLI commands as an internal API.

## 7. Existing-code reuse policy

### 7.1 Candidate direct reuse behind adapters

The following are candidates, subject to characterization and Port contract
tests:

- ACP transport/client/mapping primitives;
- OpenAI-compatible provider transport;
- CLI subprocess helpers used only for read-only discovery, version checks,
  and explicit manual compatibility entrypoints;
- tmux process and session helpers that do not infer Mission truth;
- bounded timeout, cancellation, redaction, and Fake Provider assets.

### 7.2 Reuse knowledge and tests, reimplement API

The following contain valuable invariants but are too coupled to become the
new public domain model directly:

- existing and P1 Mission/Authorization models;
- Mission service and SQLite mutation machinery;
- preview binding and conversation lifecycle;
- ProjectView projections;
- event, handoff, verification, and recovery semantics.

Their useful scenarios become new Kernel or contract tests. Their storage and
service APIs are not inherited automatically.

### 7.3 Prohibited dependencies

The new main path cannot depend on:

- the large legacy `cli.py` control flow;
- the existing `ConversationSession` and Router branch graph;
- unavailable-provider default selection;
- raw JSON as the interactive UI;
- terminal screen text as task or completion authority;
- silent transport fallback;
- Worker self-approval or peer scheduling;
- concurrent JSON/JSONL and SQLite write authorities;
- M2c harness-specific cardinality and one-shot assumptions;
- the old daemon as an MVP prerequisite.

### 7.4 Rewrite Context Firewall

The active implementation-authority order is:

```text
this Product Kernel Rewrite Design
  -> Product North Star for long-term invariants
  -> separately approved Rewrite TDD plan
  -> current Task acceptance criteria
  -> real validation evidence
  -> explicitly admitted legacy Adapter evidence
```

HISTORY, old source, old tests, validation prose, and legacy contracts cannot
add an MVP requirement or change the R0-R8 sequence. Existing code is
`not admitted` by default. A task may inspect or reuse it only when the plan
names the evidence and the reuse is protected by a new Port, characterization
test, Adapter-only integration, reuse-register entry, and architecture test.

Root coding-agent instructions must remain compact and point here. They must
not embed the removed P1/M2c/daemon/Skill/Memory implementation backlog.
Implementation tasks must name their authority sections, allowed files,
forbidden legacy imports, approved evidence, RED reason, minimal GREEN
behavior, regression commands, and commit boundary. General repository-wide
legacy exploration is not part of normal task context.

Architecture and documentation checks must fail when:

- Kernel imports legacy modules or performs I/O;
- Application bypasses Ports;
- Product calls the old CLI as an internal API;
- a non-admitted Adapter imports legacy implementation;
- the new path reads legacy JSON/JSONL as authority;
- automatic Codex/Claude transport falls back to CLI/PTY;
- tmux is treated as communication or completion truth;
- active documents restore a removed design as current work;
- README, handoff, North Star, and this spec disagree on the MVP boundary.

## 8. Domain model

### 8.1 Project and ProductSession

A Project owns a root, project identity, local database, configuration, Agent
inventory, and Mission history. A ProductSession owns the current Leader,
permission default, conversation, current Draft, active Mission, and UI state.

### 8.2 Backend, Agent Instance, and Role

An Agent Backend is a callable implementation such as `codex-cli`,
`claude-cli`, or an OpenAI-compatible API provider. An Agent Instance is one
independent session within one Mission. A Role is a Mission responsibility such
as Leader, implementer, reviewer, reviser, or acceptance reviewer.

The same backend may fill multiple roles only through separate Agent Instances
and separate ACP Sessions. Backend identity never substitutes for Instance or
Attempt identity.

### 8.3 Leader

The Leader interprets the user goal and proposes a Mission. It does not confirm
the Mission, increase permissions, schedule a peer directly, forge evidence,
or declare acceptance. Its output is untrusted proposal data until AgentDeck
validates it.

### 8.4 Mission lifecycle

- `MissionDraft` is mutable proposal state.
- `MissionPreview` is an exact confirmable projection of a Draft.
- `ConfirmedMissionVersion` is immutable.
- scope, assignment, route, permission, or acceptance changes require a new
  Preview and confirmation.
- bounded retry and a revision already declared in the confirmed Mission do
  not require repeated human confirmation.

### 8.5 Task and Attempt

A Task is a schedulable definition with role, Agent, dependencies, allowed
effects, expected outputs, and acceptance criteria. An Attempt is one execution
of that Task. Retries create new Attempts; old failure evidence is immutable.

Attempt states are:

```text
pending
running
awaiting_approval
human_controlled
completed
failed
cancelled
interrupted
outcome_unknown
```

### 8.6 Handoff and Evidence

A Handoff is an AgentDeck-owned structured transfer from one completed Attempt
to a dependent Task. It includes source and target lineage, result summary,
artifact references, verification evidence, known issues, and a content hash.

Evidence is a typed fact such as a test exit status, diff identity, artifact
hash, review finding, acceptance result, or explicit human decision. Worker
prose alone cannot satisfy a required acceptance criterion.

### 8.7 Permission hierarchy

Effective permissions can only narrow:

```text
ProductSession default
  -> Mission permission ceiling
    -> Task allowed effects
      -> Attempt effective permission
        -> one action or ACP permission request
```

Every approval records Mission, Task, Attempt, Agent, requested effect, risk,
reviewer, decision, scope, timestamp, and lineage.

## 9. Codex-style permission profiles

AgentDeck exposes exactly three built-in profiles:

### 9.1 Ask for approval

- read-only inspection is allowed;
- edits, commands, network, and other side effects require the user;
- the reviewer is the user;
- the native backend receives the closest enforceable sandbox and approval
  configuration.

### 9.2 Approve for me

- this is the default;
- routine project-local work proceeds inside the workspace boundary;
- eligible boundary requests go to an independent approval reviewer;
- the executing Leader or Worker cannot review itself;
- credential, destructive, project-external, publishing, and user-authority
  requests fail closed or surface to the user.

### 9.3 Full access

- filesystem and network sandbox boundaries are removed;
- ordinary approval prompts do not pause the Mission;
- the UI shows an explicit high-risk warning;
- ACP permission requests and decisions remain auditable even when automatically
  accepted under the confirmed profile.

If an Agent adapter cannot prove the selected profile is enforceable, the
route is unavailable. Prompt instructions do not count as permission
enforcement.

## 10. Persistence and recovery

### 10.1 One database per project

The new kernel uses:

```text
<project>/.agentdeck/agentdeck.db
```

It never searches other projects for Mission state. Global configuration can
store user preferences and a future project index, not project authority.

### 10.2 One foreground writer

The MVP has one AgentDeck foreground writer. Workers never open the database.
A second AgentDeck process for the same project can attach to the existing
session or open read-only status, but cannot become a second writer.

The MVP uses ordinary SQLite rollback journaling, foreign keys, full
synchronous durability, and bounded busy handling. WAL/SHM and daemon
multi-writer concerns are deferred until background execution is in scope.

### 10.3 Minimal tables

The initial database contains:

```text
schema_metadata
projects
product_sessions
conversation_turns
agent_instances
missions
mission_versions
tasks
attempts
handoffs
approvals
evidence
commands
events
```

Current state rows and their audit event commit in the same transaction. The
events table is an audit timeline, not a requirement to rebuild the entire
database through full event sourcing.

### 10.4 Idempotency and restart

Every side-effecting application command has a stable `command_id`. Repeated
confirmation, dispatch, approval, completion, or handoff delivery returns the
first outcome instead of duplicating effects.

On restart, any Attempt left `running` becomes `interrupted` unless the same ACP
Session can be safely reconciled. In the MVP schema, a fresh process has no
durable raw ACP resume authority, so an old running Attempt has no provable live
binding: no observed effect becomes `interrupted`, while any observed effect
becomes `outcome_unknown`. In either case the executing ProductSession converges
to `paused`. AgentDeck never treats a surviving tmux pane, backend name, role,
process, or latest Worker as reconciliation evidence. An uncertain disconnect
cannot be blindly retried. A running session found without a live Attempt also
converges to paused, including a crash after resume commit but before Worker
start. True cross-process ACP resume requires a later explicit
schema-and-adapter design.

#### 10.4.1 Schema-v2 session authority and compatibility

The first post-v1 migration is an explicit schema-v2 authority change. A fresh
database is created directly at v2. An existing database may migrate only when
all of the following are true:

- the metadata row declares version 1;
- its project root equals the current resolved project root;
- its table set and live schema fingerprint equal the exact known v1 shape;
- its stored schema digest equals that live fingerprint.

The v1-to-v2 migration runs in one `BEGIN IMMEDIATE` transaction. It applies an
exact ordered v2 DDL sequence: add `leader_backend`, `leader_model`, and the
nullable pending-exit columns to `product_sessions`; create fixed insert/update
triggers for the two closed nullable groups; backfill configured identities;
validate foreign keys, rows, and the exact known v2 live fingerprint; then
update the metadata version and digest. A fresh database applies the same v1
base DDL plus the same v2 DDL sequence before its metadata row is committed, so
fresh and migrated databases have byte-equivalent schema authority. Any DDL,
backfill, validation, identity, or commit failure rolls the entire transaction
back. Unknown versions, self-consistent but non-v1 schemas, partial v2 columns,
and damaged metadata are blockers rather than repair targets. Reopening a
valid v2 database verifies both the exact known-v2 fingerprint and its stored
digest and performs no migration write.

`leader_backend` and `leader_model` are nullable `TEXT` columns and form one
closed pair. The Adapter additionally enforces strict UTF-8, nonblank content,
and at most 4096 encoded bytes per value. A v1 `setup` session must have no
completed `configure_product_session` command and migrates with both fields
null. Every non-setup v1 session must have the one completed command
`session:configure:<session-id>` with the exact command kind, closed result
fields, matching session/permission/goal lineage, and bounded Leader/model;
the migration backfills that validated pair. A missing, malformed, or
conflicting command is a migration blocker and rolls back the whole database,
not a partially migrated re-entry warning. In v2, setup requires both fields
null and every non-setup session requires both non-null. The persisted pair and
the completed configuration command must agree on every load.

The pending-exit fields are one closed nullable group:

```text
pending_exit_id
pending_exit_attempt_id
canonical_pending_exit_attempt_facts
pending_exit_attempt_hash
pending_exit_requested_at
```

Either all five fields are null or all five are present. The canonical Attempt
snapshot is a plain JSON object with exactly these keys and types:

```text
attempt_id          nonempty `att_` string, at most 255 UTF-8 bytes
task_id             nonempty `tsk_` string, at most 255 UTF-8 bytes
agent_instance_id   nonempty `agt_` string or null, at most 255 UTF-8 bytes
ordinal             integer, 1 through SQLite signed-64 maximum
state               running | awaiting_approval | human_controlled
acp_session_id      nonblank string or null, at most 255 UTF-8 bytes
effect_observed     JSON boolean
durable_fingerprint 64 lowercase hexadecimal characters or null
```

Unknown or missing keys are invalid. Canonical JSON uses sorted keys, compact
separators, strict UTF-8, and no NaN-like values; the encoded object is bounded
to 4096 bytes. `pending_exit_attempt_hash` is SHA-256 over those exact bytes.
The SQL columns are nullable `TEXT`; when present, the request ID is exactly 36
ASCII characters (`xrt_` plus 32 lowercase hex), the Attempt ID is bounded as
above, the hash is 64 lowercase hex, the canonical object is nonempty, and the
timestamp is a normalized bounded Product clock value. Exact v2 insert/update
triggers reject a partially null group, while Adapter validation enforces the
closed JSON and encoded-byte constraints before SQL. These facts never contain
a prompt, protocol frame, terminal output, credential, or Worker prose.

#### 10.4.2 Deterministic session selection

A new foreground launch asks the Store for the latest nonterminal
ProductSession belonging to the current project identity. Nonterminal means
every declared ProductSession state except `completed`, `failed`, and
`cancelled`. Selection is stable by `updated_at DESC`, `created_at DESC`, then
`session_id DESC`. It never searches another project database or global state.

If a matching session exists, bootstrap reuses its persisted identity and
configuration. If none exists, bootstrap creates a new typed session identity
through an injectable identity factory. The old project-root-derived fixed
session ID is not the re-entry authority; an existing v1 session keeps its
identity through migration and is found by the same query. Multiple historical
terminal sessions remain immutable history. Multiple nonterminal rows are not
silently merged; deterministic selection chooses one while diagnostics expose
the unexpected count.

#### 10.4.3 Exact exit request and fail-closed interruption

Attempt states `running`, `awaiting_approval`, and `human_controlled` are active
for exit purposes. The foreground execution model permits at most one active
Attempt. More than one is an authority inconsistency: `/exit` returns a
diagnostic and creates no request.

With no active Attempt and no executing confirmed Mission, explicit `/exit`
closes the writer and exits without a confirmation request. With an executing
Mission between stages, `/exit` atomically changes the ProductSession to
`paused` without sending ACP. With one active Attempt, `/exit`
command-atomically persists a typed request identity, the exact canonical
Attempt snapshot, its hash, timestamp, and an audit event. That persisted group
is also the durable project dispatch blocker. Repeating `/exit` while that
snapshot is unchanged returns the same request. If the durable Attempt changes,
the old request cannot cancel it and no next stage may start; a later `/exit`
revalidates the new state and pauses the project.

An exit request identity is `xrt_` plus 32 lowercase hexadecimal characters
from an injectable cryptographic-random identity factory. An all-null pending
group permits creation. A well-formed pending request whose Attempt snapshot
still matches is returned unchanged. A well-formed but drifted request may be
atomically superseded by `/exit` only after the old request and current Attempt
are both revalidated; a malformed or partially null group returns a Diagnostic
with zero writes and is never silently overwritten. Consequently, repeated
reads of one pending request are stable, while declining and later requesting
exit for the same unchanged Attempt produces a new command lineage rather than
replaying the consumed decision.

Decline and confirm accept only the current request ID and Attempt hash. In the
same write transaction, both operations reload the Attempt, rebuild the exact
canonical object, and compare its hash and lineage with the pending request.
Any drift, missing Attempt, stale identity, or malformed persisted snapshot
returns a content-free Diagnostic with zero writes and leaves the request
present. A valid decline then clears the complete pending group and leaves the
Attempt untouched.

A valid confirm may consume the request and exit only after the exact bound
Worker has written the ACP cancel notification within a deadline and completed
bounded local connection/process shutdown. ACP cancel is a notification, so
this success does not claim a separate remote business acknowledgement or
prove that no earlier effect occurred. Task 15B then executes one Store command
transaction that compare-and-swaps the same request and snapshot, changes the
Attempt to `interrupted`, changes the ProductSession to `paused`, clears all
five pending fields, appends the Attempt, project-pause, and ProductSession
audit events, and saves the completed command result. Any failure rolls back
all of those database effects. Missing cancellation
capability, rejection, timeout, disconnect, shutdown uncertainty, changed
lineage, or uncertain outcome leaves the request and Attempt authoritative,
keeps an interactive foreground shell open when input remains available, and
returns a content-free Diagnostic. That first failure or post-cancel authority
drift is stored as a closed replay result so confirmation never sends a second
cancel.

`/resume` is the sole paused-to-running command. It first validates a read-only
resume snapshot derived from existing SQLite Mission, ordered Task, Attempt,
Handoff, Evidence, and completed-command facts; there is no cursor table or
schema migration. The snapshot identifies the first stage without a closed
terminal bundle and its next Attempt ordinal. One transaction revalidates its
canonical hash, changes the ProductSession to `running`, records
`project_resumed`, and closes the resume command. Only after commit may the
same foreground loop create the Mission child task. Interrupted stages receive
a new Attempt; closed stages are immutable context; unknown outcomes and
lineage drift block with zero Worker I/O.

Task 15A implements the durable request, validation, decline, re-entry, and
fail-closed blocker but cannot claim Worker cancellation. Task 15B binds the
real async Worker lifecycle and is the first slice allowed to make active
confirm exit successfully. Ctrl-C is converted to the same safe exit surface
when input remains available. EOF with active work closes only the foreground
input/store boundary, records no false cancellation or completion, and reports
that recovery is required; it never prints `Session saved` or `safely
cancelled` for active work.

### 10.5 Secrets

The database does not store API keys, CLI login tokens, full environment
variables, hidden reasoning, unbounded terminal output, or raw unsanitized
protocol frames. Provider configuration stores only credential source names.

## 11. Leader and provider setup

### 11.1 Discovery

Codex and Claude discovery begins with the real process PATH and records the
resolved executable. Readiness is classified as missing, discovered,
authenticated, ACP available, and ready. A missing ACP adapter leaves the
native CLI available for manual use but makes it unavailable for automatic
MVP orchestration.

Discovery does not install software, modify PATH, authenticate accounts, write
project source, or send a model prompt merely to test readiness.

### 11.2 Leader Port

Every Leader implements the conceptual operation:

```text
propose_mission(user_goal, project_context, available_agents,
                permission_profile) -> MissionDraft
```

The MVP includes Codex CLI, Claude CLI, and OpenAI-compatible API Leader
adapters. Codex and Claude CLI Leaders communicate through their ACP adapters;
they are not driven by prompt injection or scraped subprocess output. API
presets cover DeepSeek, Kimi, GLM, and Custom over their declared HTTP API, but
the user must provide an exact model and credential source. API providers are
Leader-only in the MVP.

### 11.3 Model selection

`/leader` selects the reasoning backend. `/model` selects its model. CLI
backends default to `native-default` and use ACP-reported model/config options
when available. AgentDeck does not maintain a stale hard-coded model catalog.
The resolved backend, adapter, model, and version are frozen into the Mission.

## 12. ACP-only automatic orchestration

All automatic orchestration communication with Codex or Claude, whether the
Agent is a Leader or Worker, uses ACP:

```text
AgentDeck -> ACP -> Agent task
Agent -> ACP -> progress/tool/permission/result
```

The Worker Port provides:

```text
start_task
stream_events
respond_permission
cancel_task
collect_result
```

Adapters map ACP updates into stable Worker Events such as started, progress,
tool started/completed, permission requested, artifact changed, message,
completed, failed, and cancelled.

A stable `permission_requested` event carries the exact request identity plus a
conservative normalized effect and bounded risk classification derived by the
ACP Worker adapter. Raw tool input, protocol frames, credentials, and model
prose never become approval authority. An unclassifiable effect fails closed;
the Approval Service must not infer permission from prompt text.

The automatic MVP does not fall back to CLI/PTY prompt injection. If required
ACP is unavailable, the Mission does not start. Native CLI and tmux remain
manual observation, takeover, and diagnostic surfaces.

## 13. Deterministic four-stage execution

After confirmation, a non-LLM Execution Coordinator advances the frozen graph:

```text
Codex implementation
  -> AgentDeck validates result and creates implementation handoff
Claude review
  -> AgentDeck validates findings and creates authoritative revision task
Codex revision
  -> AgentDeck validates resolved findings and creates revision handoff
Claude acceptance
  -> AgentDeck maps evidence to every acceptance criterion
```

Claude returns a Review Result and Revision Proposal. It never dispatches Codex
directly. AgentDeck materializes the authoritative Revision Task only from the
confirmed Mission and accepted, in-scope, evidence-backed findings.

The default budgets are bounded and configurable in the Preview:

- one Leader schema repair;
- at most two Attempts per Task;
- one ACP reconnect for a clearly recoverable transport interruption;
- one revision cycle;
- one final acceptance Attempt.

Known test failures, denied permission, scope insufficiency, login loss,
unexplained project drift, and outcome unknown are not blind-retry conditions.

## 14. tmux real Agent observation

tmux is a human transparency surface. It does not send automatic tasks, decide
completion, or store authority.

The default project workspace contains an Overview window and a Workers
window. The Workers window can show four independent Agent Instances:

```text
Codex implementer | Claude reviewer
Codex reviser     | Claude acceptance
```

Each pane runs an AgentDeck Observer. It renders the real decoded ACP stream,
including Agent messages, progress, tool activity, permission requests,
artifacts, completion, and errors. AgentDeck-generated text is explicitly
labeled and never impersonates an Agent.

Every displayed record retains Agent, Task, Attempt, ACP event, timestamp,
transport, and sequence identity. Cursor-based reconnect prevents cross-Agent
mixing, loss, or duplication. Hidden reasoning and sensitive data are not
displayed.

`/takeover` is an explicit ownership transition. Automatic input stops, human
control is recorded, and return-control requires runtime and project-state
revalidation before scheduling continues.

## 15. Diagnostics

`BLOCKED`, `backend_failure`, and timeout names are never complete user-facing
explanations. Every non-success terminal or attention state links a Diagnostic
with:

```text
code
stage
severity
actor
summary
cause
impact
protection
recovery_actions
retryable
outcome_known
mission/task/attempt/trace identity
occurred_at
```

The default renderer answers what happened, why, what completed, what did not,
what AgentDeck protected, and what the user can do. `/diagnose` exposes stable
technical fields and `--json` exposes a redacted machine contract.

Important distinct categories include discovery/authentication, ACP
initialization/session/protocol, Leader/model/schema, Mission preview/drift,
Worker start/result/outcome, review/revision scope, acceptance evidence,
permission, storage/recovery, and tmux Observer degradation.

Observer failure does not imply Worker failure. In ordinary operation it is a
recoverable warning; in the Golden Product Gate, inability to display the real
work stream fails the observability requirement.

## 16. Project configuration

Configuration precedence is:

```text
explicit current-session selection
  -> project configuration
    -> user global preference
      -> read-only discovery
```

Project configuration lives at `.agentdeck/config.toml`. Global configuration
stores only user-level preferences. The first-run minimum includes language,
default permission, Leader backend/model, exact CLI paths, and tmux observation
preference. Ordinary CLI users do not need to write TOML manually.

Deterministic setup, status, Leader, model, Agent, permission, doctor, and exit
controls remain available without an LLM.

## 17. Verification strategy

### 17.1 Test layers

1. Pure Kernel unit tests.
2. Application service tests using Fake Ports.
3. shared adapter contract tests.
4. a real stdio/JSON-RPC Fake ACP server.
5. Product Shell and renderer tests.
6. tmux Observer fidelity/cursor tests.
7. deterministic Fake four-stage end-to-end.
8. read-only real adapter preflight.
9. real Coding Golden Demo.
10. human product acceptance.

The Fake ACP server covers initialization, capabilities, session creation,
streamed progress, tool events, multiple permissions, completion,
cancellation, invalid results, disconnect-before-work,
disconnect-after-side-effect, duplicate and out-of-order events, oversized
output, protocol mismatch, and secret-bearing output.

### 17.2 Product regression tests

The current user-visible failures become explicit RED tests:

- original goal survives setup;
- unavailable DeepSeek is not silently selected;
- interactive mode does not print raw JSON;
- Leader failures retain exact diagnostic categories;
- a known v1 database migrates atomically to v2 and damaged/unknown sources do
  not migrate;
- `/exit` without an active Attempt safely persists and exits;
- active `/exit` persists an exact request and stale confirm/decline inputs
  perform zero mutation;
- active confirm cannot report success before real ACP cancellation is bound;
- Ctrl-C and EOF never claim active work was safely cancelled or saved;
- re-entry restores the latest nonterminal ProductSession, Leader, model,
  permission, pending goal, Preview, and pending exit request for this project.

### 17.3 Real preflight

Preflight records the frozen commit/build, Python environment, CLI paths and
versions, login readiness, ACP capabilities, tmux readiness, database
readiness, permission profile, and resolved model identity. It is read-only
with respect to the project and does not install, authenticate, select a
fallback, or generate source code.

Live runs are repeatable after diagnosis and deterministic regression repair.
They are not constrained by a ceremonial one-shot authorization. Every run
uses a new disposable project and a frozen evidence identity.

## 18. Real Golden Product Gate

The real acceptance target is a local-only reproduction of the frozen home
page of `https://www.iae.cas.cn/`.

The target capture freezes desktop, full-page, and mobile screenshots, page
structure, asset manifest, interaction checklist, timestamp, viewport, and
allowed visual tolerances. The scope is the home page only. It does not copy a
backend, implement real login, collect credentials, deploy publicly, or claim
to be the official website.

The four Worker Instances are:

```text
codex-implementer
claude-reviewer
codex-reviser
claude-acceptance
```

The Leader is separate from those four Workers.

PASS requires:

- a working local frontend build;
- fixed desktop and mobile browser checks and screenshots;
- a visual difference report against the frozen reference;
- all required home-page modules and interactions;
- real Codex and Claude CLIs through real ACP Sessions;
- implementation, review, revision, and acceptance lineage;
- evidence-backed resolution of blocking findings;
- a complete SQLite authority record;
- tmux panes that display each real Agent ACP stream without mixing, loss, or
  fabricated progress;
- human-readable final Mission results;
- `/exit` followed by successful re-entry and history inspection;
- a human operator watching and accepting the product journey.

Reference captures and copyrighted assets remain local acceptance material by
default. The repository stores only the rules, hashes, sanitized evidence, and
lawfully redistributable fixtures.

## 19. Rewrite and cutover phases

### R0: rewrite boundary

Create the new directories, architecture tests, hidden development entry, and
legacy capability inventory. No old user behavior changes.

### R1: Kernel and SQLite

Implement domain models, permission invariants, Diagnostics, one-writer store,
idempotency, and recovery using Fake Ports only.

### R2: ProductSession and first-run shell

Implement natural conversation, deterministic slash controls, PATH discovery,
Leader/model/permission setup, human rendering, exit, and re-entry.

### R3: Leader and Mission Preview

Implement Codex, Claude, and OpenAI-compatible Leader Ports, exact Preview
revision/confirmation, and drift checks.

### R4: ACP Workers and execution

Implement Fake ACP, real Codex/Claude ACP adapters, Worker Events, permission
bridge, four-stage execution, handoffs, evidence, bounded retry, and recovery.

### R5: tmux observation

Implement Overview and four-Worker windows, event cursors, fidelity, reconnect,
redaction, and takeover ownership.

### R6: diagnostics and recovery closure

Complete human Error Cards, trace, outcome-unknown reconciliation, ACP and
Observer recovery, and sanitized support evidence.

### R7: website reproduction Golden Demo

Run deterministic gates, real preflight, real four-Agent acceptance, and human
product review in a disposable project.

### R8: bare-entry cutover

Switch no-subcommand `agentdeck` to the new Product Shell. Existing structured
subcommands remain script/debug contracts. Preserve a bounded, non-authoritative
legacy-shell entry for rollback and migrate old state only through explicit
preview and confirmation.

## 20. Migration and deletion

The new database lives beside legacy state during development. Old state is
never silently imported and old JSON/JSONL cannot become a second write
authority. Existing projects receive an explicit migration preview, backup,
confirmation, verification, and report after the new Golden Gate is proven.

Old modules are not deleted before cutover. After cutover, deletion proceeds
in isolated commits only when the new route and contract tests cover the
behavior. Likely deletion candidates include the old bare ConversationSession,
router branches, M2c live-harness special paths, duplicate orchestration glue,
and legacy structured-state write surfaces.

The incomplete P1 branch remains an architecture reference, not the parent of
the rewrite. Useful P1 invariants and tests can migrate; P1 TOCTOU/WAL/SHM and
daemon hardening remain deferred until a real post-MVP need exists.

### 20.1 Active-document reset

The working tree contains one current construction design: this document.
Historical specs, plans, architecture proposals, the old V1 PRD, M2c migration
plan, obsolete live SOPs, and legacy walkthroughs are removed from the active
tree. Git history remains their archive.

Real validation results, reference analysis, and the legacy capability
inventory remain as evidence. `HISTORY.md` remains an audit timeline, but its
historical paths do not imply current files or authority. Legacy structured
CLI contract documents remain temporarily for compatibility and carry a
directory-level non-authority notice.

The root README files, `AGENTS.md`, `AGENT.md`, `CLAUDE.md`, and current
handoff must describe only the rewrite, current gate, retained compatibility
boundary, and approved post-MVP sequence. The documentation reset is an atomic
docs-only commit; it cannot modify product source, tests, runtime state,
environment definitions, authentication, or installed tools.

## 21. Development and commit discipline

Every implementation slice must:

1. reread this spec and the Product North Star;
2. write a deterministic RED test;
3. confirm the RED failure reason;
4. implement the smallest GREEN behavior;
5. refactor without changing the contract;
6. run focused and proportional full verification;
7. update HISTORY and affected durable docs;
8. self-review scope, security, and product behavior;
9. create one local commit with no unrelated files.

Every task in the Rewrite TDD plan must additionally identify the exact
authority sections, allowed files, forbidden legacy imports, approved legacy
evidence, expected RED reason, smallest GREEN outcome, regression scope, and
commit boundary. Old regression tests protect compatibility only; when an old
expectation conflicts with this design, the expectation is retired or
reclassified rather than imposed on the new Kernel.

The rewrite occurs in an isolated worktree. No push, merge, global auth change,
adapter installation, real provider run, or live acceptance is implied by this
design approval. Those actions require their normal explicit scope when the
implementation plan reaches them.

## 22. Post-MVP product sequence

Only after the Golden Product Gate and bare-entry cutover may the project begin
the next product enhancements:

1. background daemon execution and disconnect-safe continuation;
2. memory suggestions and explicit durable memory;
3. built-in/project/external Skill Registry integration;
4. Hermes-inspired learning review and governed self-improvement proposals;
5. Hive-inspired browser workbench and richer information architecture;
6. broader Agent/provider support;
7. A2A interoperability with other Agent systems;
8. remote, mobile, and WispTerm-class workspace clients.

Memory, skills, and self-evolution remain suggestions, previews, and explicit
human-controlled changes. They never become permission grants or a hidden
execution authority.

## 23. Final completion definition

The rewrite is complete only when:

- bare `agentdeck` launches the new continuous product session;
- local Codex/Claude and their ACP readiness are discoverable;
- the user can select Leader, model, and one of three permission profiles;
- a natural-language goal becomes a human Mission Preview;
- one confirmation starts an automatic ACP-only four-stage coding flow;
- tmux shows the four real Agent work streams;
- SQLite preserves complete Mission and approval lineage;
- errors are specific and recoverable;
- exit and re-entry work;
- the four-Agent website reproduction Golden Demo passes;
- a human operator accepts the experience;
- README, North Star, HISTORY, handoff, contracts, and implementation agree.

This design approval authorizes documentation and planning only. Implementation
starts only after the written spec is reviewed and a separate detailed TDD
plan is approved.
