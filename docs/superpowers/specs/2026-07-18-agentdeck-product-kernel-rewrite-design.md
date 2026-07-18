# AgentDeck Product Kernel Rewrite Design

**Status:** Approved design; implementation remains locked pending written-spec review and a separate `writing-plans` TDD plan

**Date:** 2026-07-18

**Authority:** Product North Star first; this document supersedes the active P1-first implementation order but preserves P0/P1/M2/M2c as historical evidence

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

`/exit` safely persists the session. If a Worker is active, the foreground MVP
explains that exit will interrupt the Attempt and requires confirmation before
stopping it. Re-entry reconstructs the same ProductSession.

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
    acp.py
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
Session can be safely reconciled. AgentDeck never treats a surviving tmux pane
as completion. An uncertain disconnect produces `outcome_unknown` and cannot
be blindly retried.

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
- `/exit` safely persists and exits;
- re-entry restores the ProductSession.

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

## 21. Development and commit discipline

Every implementation slice must:

1. reread the Product North Star and this spec;
2. write a deterministic RED test;
3. confirm the RED failure reason;
4. implement the smallest GREEN behavior;
5. refactor without changing the contract;
6. run focused and proportional full verification;
7. update HISTORY and affected durable docs;
8. self-review scope, security, and product behavior;
9. create one local commit with no unrelated files.

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
