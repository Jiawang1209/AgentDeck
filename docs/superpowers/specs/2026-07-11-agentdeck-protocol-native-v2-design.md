# AgentDeck Protocol-Native V2 Design

Date: 2026-07-11
Status: Human-approved design
North star: `docs/roadmap/product-north-star.md`

## 1. Purpose

This design evolves AgentDeck from a tmux-first CLI control plane into a protocol-native multi-agent product. It preserves the existing Mission, approval, ledger, Skill, Memory, ProjectView, and recovery assets while replacing fragile Worker communication with structured agent sessions.

The primary user experience becomes a persistent `agentdeck` natural-language conversation. Deterministic subcommands remain the authoritative automation and debugging surface.

## 2. Decisions already approved

- Protocol-native core, not an ACP-only rewrite.
- Project-first conversations with a global project navigator.
- A project daemon continues explicitly confirmed Missions after clients disconnect.
- New permissions, plan drift, risk escalation, and failures pause execution.
- First-run LLM setup detects available agents/providers, requires user selection, and may be skipped.
- The default UI is one natural-language conversation; the live multi-pane workspace opens on demand.
- Internal V2 refactoring may be extensive, but state and command migration is previewed, backed up, compatible, and reversible.
- WispTerm informs future workspace UX; AgentDeck does not build or fork a Zig terminal emulator now.

## 3. Product surfaces

### 3.1 Interactive client

`agentdeck` starts an interactive Frontdesk client. In a project it connects to that project's daemon and resumes the project's conversation. Outside a project it connects to the global Frontdesk and offers project discovery, creation, and switching.

The client owns input, streaming presentation, navigation, and explicit controls. It does not own scheduler state or runtime authority. Closing the client never implies cancelling a confirmed Mission.

### 3.2 Deterministic CLI

Existing command families remain available. Every natural-language action resolves to an inspectable primitive or a frozen composite of primitives. The client must be able to show the exact commands and controls behind a proposal.

`agentdeck leader chat --message ...` remains a one-shot/scripted route. It is not the primary human experience after V2.

### 3.3 Future workspace clients

TUI, desktop, IDE, and remote clients consume ProjectView, contract discovery, and an event stream. A CCB/WispTerm-class workspace may show terminals, files, diffs, artifacts, media, remote surfaces, and Agent sessions. No client reads private runtime files or becomes a second control plane.

## 4. Project daemon

Each project has at most one authoritative daemon generation. The daemon owns:

- durable conversation sessions;
- Mission scheduling and locks;
- Agent Registry and transport selection;
- ACP connections and Agent session lifecycle;
- approval and policy evaluation;
- event ingestion and ProjectView projection;
- crash recovery and notification delivery.

CLI and UI clients connect through a local authenticated endpoint. Startup must distinguish daemon readiness, transport readiness, Agent session readiness, and optional UI observability.

The daemon is local-first. Remote access and external notification channels require explicit opt-in and are deferred until the local lifecycle is proven.

## 5. Runtime model

### 5.1 Stable identity

The durable lineage is:

```text
project -> conversation -> mission -> task/step -> worker_binding
        -> agent_session -> turn -> update/tool/permission/completion
```

`agent_session.session_id` is the Worker session identity. It records agent, provider, model, transport, transport-native session identifier, workspace, state, and recovery cursor. A pane may be attached through `observation_bindings[]`; it is never the primary identity.

### 5.2 Backends

The unified runtime interface supports:

- **ACP native:** preferred structured transport.
- **ACP adapter:** a supervised subprocess that translates an existing CLI to ACP semantics.
- **tmux fallback:** compatibility, observation, and human takeover for agents without a reliable ACP path.
- **API backend:** direct model or server-agent integrations.

Backend selection is explicit provenance. Switching or recovering a backend cannot silently replace a session or discard completed turns.

### 5.3 ACP mapping

ACP lifecycle and updates map into AgentDeck facts:

| ACP concept | AgentDeck record |
| --- | --- |
| initialize/capabilities | transport capability snapshot |
| session new/load | `agent_sessions[]` and runtime event |
| prompt | message, attempt, job, turn |
| streamed session update | append-only update event and progress projection |
| tool call | trace node and artifact provenance |
| permission request | AgentDeck approval request |
| completion | reply and terminal turn state |
| protocol/error/disconnect | stopped/failed state and recovery evidence |

Streaming text never implies completion. Duplicate updates and reconnect replay are idempotent by stable update identity or a persisted recovery cursor.

## 6. Mission execution

A Frontdesk may clarify and summarize a goal. A Planner produces validated steps and acceptance criteria. An Orchestrator binds tasks to Workers and transports. The resulting Mission is frozen with:

- user objective and normalized brief;
- ordered or dependency-aware tasks;
- selected Agent bindings;
- acceptance criteria;
- policy and budget snapshot;
- required skills and compact provenance;
- plan hash and confirmation scope.

One overall confirmation authorizes execution only inside this frozen scope. Normal task transitions proceed unattended. The daemon pauses when execution requires authority not represented by the snapshot.

Resume uses terminal turn state and never repeats a completed turn. A dispatched but ambiguous turn is reconciled through transport/session recovery before any resubmission.

## 7. Governance and permissions

ACP permission requests do not constitute authorization. Every request passes through AgentDeck policy with Mission, Agent, session, turn, tool, workspace, target, and risk context.

Decisions are:

- allow within confirmed scope;
- deny by policy;
- pause and request human approval.

Destructive actions, publication, external messaging, credentials, protected paths, authority expansion, and plan expansion require explicit policy support or new approval. Unknown and malformed requests fail closed.

Every decision emits an audit event. Prompt content, Skill content, Memory, Role Packs, ACP capability declarations, and provider metadata cannot grant permission.

## 8. Learning, Skill, and Memory

AgentDeck adopts a Hermes-like learning experience without silent mutation:

```text
completed work -> learning review -> suggestion -> preview/diff/provenance
               -> human confirmation -> create/apply -> explicit load when needed
```

The system may automatically generate suggestions and read-only previews. It may not silently install external skills, write durable memory, load a skill into an Agent, or promote a suggestion into policy.

Frontdesk, Planner, Worker, and Reviewer receive different compact contexts. Workers receive only the task, necessary workspace facts, explicitly loaded Skill snapshots, and compact handoffs. Reviewer context remains isolated from Coder reasoning.

## 9. LLM configuration

`agentdeck` always starts. Local deterministic routes cover status, history, approvals, audit, recovery, and setup without an LLM.

First run detects ACP agents, authenticated CLIs, API providers, and local models. It shows capability and readiness, recommends options, and asks the user to select a Frontdesk model or skip. It never silently chooses a provider or requires DeepSeek.

An open-ended request without a configured LLM produces a setup card rather than a startup failure. Frontdesk and Planner provider/model bindings are independent.

## 10. V2 data and migration

New first-class records include conversations, daemon generations, transports, agent sessions, turns, updates, permission requests, and recovery cursors. Existing Mission, message, reply, approval, artifact, Skill, Memory, and event records remain authoritative inputs.

Migration workflow:

1. Inspect old state without writing.
2. Validate consistency and calculate source hashes.
3. Show a migration preview, blockers, backup path, and resulting schema generation.
4. Require explicit confirmation.
5. Write a complete backup before migration.
6. Migrate atomically and validate the new ProjectView.
7. Preserve a supported rollback path to the previous generation.

New projects use V2 directly. Legacy commands route through a compatibility layer during a documented transition. No migration silently starts a daemon, connects a provider, or resumes a Mission.

## 11. Delivery phases

### Phase 0: baseline

Complete and document the current natural-language Mission Golden Demo. Treat its user-visible behavior and audit evidence as the compatibility baseline.

### Phase 1: protocol model

Add transport, session, turn, update, permission, and capability contracts behind current behavior. No default command behavior changes.

### Phase 2: ACP vertical slice

Prove initialize, session create/load, prompt, streamed update, permission bridge, completion, disconnect, and resume with one real Agent.

### Phase 3: daemon and interactive Frontdesk

Add project daemon lifecycle, default `agentdeck` REPL, first-run provider detection, reconnect, and background execution for bounded test Missions.

### Phase 4: multi-agent ACP Mission

Run the Codex-and-Claude sequential Golden Demo over ACP or supervised ACP adapters. Prove one confirmation, compact handoff, permission pause, daemon restart, and exact recovery.

### Phase 5: roaming and notifications

Add global project index, Global Frontdesk, away summaries, and local notifications. External channels remain opt-in.

### Phase 6: workspace clients

Build the on-demand observable workspace after the headless core is stable. Terminal, files, diffs, artifacts, and remote surfaces consume public contracts.

## 12. Testing and release gates

Each phase requires:

- unit tests for pure state transitions and validators;
- ACP conformance fixtures and malformed-message tests;
- permission fail-closed tests;
- duplicate update and idempotent replay tests;
- daemon lock, crash, restart, and reconnect tests;
- old-state migration, backup, rollback, and compatibility tests;
- real Codex/Claude smoke where relevant;
- a product-level Golden Demo;
- ProjectView and CLI contract agreement;
- README, HISTORY, handoff, and contract documentation updates.

No phase is complete if it works only through a private test helper or an unobservable daemon path.

## 13. Error handling

- Unsupported ACP capability: surface a blocker or select an explicitly compatible backend; never pretend support.
- Transport disconnect: persist the last recovery cursor, mark the session reconnecting, and stop new turns until reconciliation.
- Unknown completion: keep the turn ambiguous and require recovery inspection; do not resubmit automatically.
- Permission timeout: pause the Mission without losing the request.
- Daemon generation conflict: fail startup and report the authoritative generation.
- Corrupt state or failed migration: retain the source and backup, emit no partial V2 state, and provide repair guidance.

## 14. Explicit non-goals

- A terminal emulator or WispTerm fork.
- ACP-only operation with no fallback.
- Silent provider login or workspace trust.
- Silent Skill, Memory, Role Pack, or policy mutation.
- Remote marketplace and supply-chain installation in the first V2 slices.
- A desktop GUI before the daemon and public event contracts are stable.
- Removing exact CLI controls in favor of an opaque chat-only interface.

## 15. Acceptance scenario

In a fresh project, the user runs `agentdeck`, selects a detected authenticated Agent or skips setup, and asks Codex and Claude to complete a fixed eight-step collaborative task. AgentDeck returns one frozen Mission preview. After one confirmation, the project daemon prepares both Workers, executes the dependency chain through structured sessions, records every update and permission decision, and continues after the interactive terminal closes. On return, the same Mission/session/turn identities and evidence appear in natural language, ProjectView, CLI status, and the optional live workspace. Any new permission pauses safely, and a daemon restart does not repeat a completed turn.
