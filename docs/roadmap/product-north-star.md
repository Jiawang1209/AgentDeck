# AgentDeck Product North Star

Updated: 2026-07-18

## Active product-kernel rewrite route

New development follows the approved side-by-side product-kernel rewrite:

1. R0 rewrite boundary
2. R1 pure Kernel and one-writer SQLite
3. R2 continuous ProductSession and first-run shell
4. R3 Codex/Claude/API Leader and exact Mission Preview
5. R4 ACP-only four-stage Worker execution
6. R5 tmux real Agent observation
7. R6 diagnostics and recovery closure
8. R7 real four-Agent website-reproduction Golden Demo
9. R8 bare `agentdeck` cutover

The authoritative approved design is
[AgentDeck Product Kernel Rewrite](../superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md).
The old implementation and incomplete P1 branch remain preserved as historical
capability, test, and adapter evidence, but the rewrite does not inherit their
module boundaries or phase order. Old M2c status is not a release veto or live
retry authority. Implementation remains locked until the written spec is
reviewed and a separate `writing-plans` TDD plan is approved.

## Historical Phase 3 M2 delivery evidence

The project-local authoritative daemon, disconnect-safe frozen Mission
scheduler, explicit ACP/tmux supervision, exact governance pauses, crash
reconciliation, deterministic reconnect, and explicit migration are delivered.
The deterministic M2 acceptance and nine-point crash matrix pass. The real
component rehearsal remains honestly blocked at strict CLI Leader plan JSON and
the real Codex tmux correlated-reply boundary; see
`docs/validation/2026-07-13-phase3-m2-project-daemon.md`. Remote/global clients,
A2A, notifications, and transcript persistence remain future work.

## Product promise

AgentDeck is a local-first multi-agent workbench. A user should be able to enter
a project, select a real Leader and permission profile, describe one goal in
ordinary language, review one frozen Mission, confirm it once, and watch a
governed team of heterogeneous agents execute it through ACP. The foreground
MVP preserves state on exit and resumes deterministically on re-entry;
background execution after terminal close is a post-MVP enhancement.

The product north star is:

> AgentDeck combines Hermes-like natural interaction, an AgentDeck-owned
> communication and orchestration kernel, ACP-native structured transport,
> CCB-like visible and controllable multi-agent collaboration, Hive-like
> browser-workbench clarity, and a future WispTerm-class workspace experience
> with stronger Mission governance, audit, and recovery.

AgentDeck is not a terminal multiplexer, a thin multi-provider chat UI, or a wrapper around one model. Its durable value is the control plane that coordinates several real agents as a recoverable team.

## Primary experience

The primary entrypoint will be:

```bash
agentdeck
```

Inside a project, this opens or resumes a persistent natural-language conversation scoped to that project. Outside a project, it opens a global Frontdesk that can find, create, and switch projects.

The ordinary user should be able to say:

```text
帮我完成这个功能，让 Codex 实现、Claude 审查。
批准执行。
现在进展怎么样？
打开实时工作区。
```

Deterministic commands such as `agentdeck mission status`, `agentdeck approval`, `agentdeck trace`, and `agentdeck leader chat --message ...` remain available for automation, GUI/TUI clients, audit, debugging, and exact recovery. Natural language must compose these authoritative primitives rather than create a second execution system.

## Product identity

### From Hermes

- A natural, continuous CLI conversation instead of command memorization.
- Project and user memory that makes future work easier.
- Learning suggestions after useful work.
- Provider choice, resumable sessions, and low-friction setup.

AgentDeck keeps a stricter boundary: learning may automatically propose, preview, and explain, but external skills, durable memory, and new permissions are never silently enabled.

### From ACP

- Standard session lifecycle, prompts, streamed updates, tool calls, and permission requests.
- Structured transport instead of terminal-screen inference.
- Portable agent integrations across clients and providers.
- A path for AgentDeck to act both as an ACP client and, later, as an ACP agent that exposes an entire governed Mission team.

ACP is the required automatic orchestration transport for the MVP. It does not
own Mission identity, task state, permission authority, handoff validity,
scheduling, governance, or audit. Those facts remain authoritative in
AgentDeck's own communication ledger and orchestration kernel.

### From CCB

- Real heterogeneous agents with parity, independent sessions, and independent context.
- Project-scoped multi-agent workspaces.
- Asynchronous delegation: submission, acknowledgement, progress, and completion are distinct states.
- Visible terminals and human takeover when needed.
- Dynamic role and Worker lifecycle management.

AgentDeck keeps stronger frozen-Mission, approval, lineage, and recovery semantics. Terminal state is an observation surface, not the protocol authority.

### From Hive

- A browser-native local workspace that makes one Orchestrator and many real
  CLI Workers immediately understandable.
- Visible Leader and Worker terminals, team cards, role presets, task graphs,
  dispatch/report timelines, notifications, restart, and reconnect affordances.
- A product experience in which users can watch work, inspect a Worker, and
  intervene without reducing the system to a terminal multiplexer.
- A durable local runtime that keeps PTY-backed work available when the browser
  view disconnects.

Hive is an experience and information-architecture reference, not AgentDeck's
communication authority. AgentDeck does not adopt Hive's private `team`
protocol, bypass-oriented permission defaults, task Markdown as scheduler
truth, or PTY output as completion proof. Any similar surface is independently
implemented over AgentDeck contracts, ProjectView, event ledger, permission
lineage, and recovery state.

### From WispTerm

- A future cross-platform workspace with terminals, tabs, splits, files, diffs, artifacts, Markdown, images, PDFs, browser panels, remote projects, and history recovery.
- A product-quality command center and session browser.

This is a future client experience, not the current core implementation. AgentDeck Core remains headless, protocol-driven, and independently testable. The current roadmap does not include building or forking a Zig terminal emulator.

## Architecture principles

```text
Human / CLI / TUI / Browser Workspace / Desktop / future ACP client
                              |
                     AgentDeck Frontdesk
                              |
               Mission / Planner / Orchestrator
                              |
       AgentDeck Communication Ledger / Governance / Recovery
                              |
                  Protocol-Native Runtime Kernel
                              |
                     ACP Client Layer
                              |
                Codex ACP / Claude ACP / future agents

        tmux = real ACP event observation and human takeover
```

1. **Project first, globally navigable.** Project state is isolated; the global Frontdesk stores only user-level preferences and a project index.
2. **One foreground writer for the MVP.** One project-local SQLite database is
   authoritative. Exit preserves state and re-entry recovers it. A daemon and
   background continuation are post-MVP work.
3. **One confirmation covers one frozen scope.** Ordinary steps do not repeatedly interrupt the user. New permission, plan drift, risk escalation, or runtime failure pauses the Mission.
4. **Sessions, not panes, identify Workers.** A stable `session_id` survives transport and UI changes. A `pane_id` is an optional observation binding.
5. **AgentDeck owns communication truth.** Mission, task, attempt, permission,
   reply, handoff, and acceptance facts belong to one AgentDeck ledger. No
   transport adapter may create a parallel authority system.
6. **ACP is the automatic transport.** All automatic task, progress,
   permission, result, review, revision, and acceptance communication uses ACP.
   CLI/PTY is manual-only compatibility and takeover; tmux is observation.
7. **All facts enter one ledger.** Prompts, updates, tools, permissions, replies, artifacts, and failures are append-only evidence projected through ProjectView.
8. **Context is not authority.** Prompts, skills, memory, role packs, and ACP metadata cannot expand permissions.
9. **Headless core, replaceable clients.** CLI, TUI, browser, desktop, remote, and IDE clients consume the same contracts and event stream. The GUI renders authority; it does not infer it from terminal pixels.
10. **Clean core, reversible cutover.** The new Kernel is a side-by-side rewrite
    with strict Ports. Existing behavior is reused only through admitted
    adapters. User projects, history, and commands receive previewed, backed-up,
    reversible migration after the Golden Gate.
11. **Evidence before product claims.** Every phase ends with contracts, failure tests, crash recovery, and a real multi-agent Golden Demo.

## LLM setup

`agentdeck` must always start. Deterministic status, audit, approval, and recovery commands do not require an LLM.

On first use AgentDeck detects available ACP agents, logged-in CLIs, API
providers, and local models. It never silently selects unavailable DeepSeek or
another provider. The user may choose Codex CLI, Claude CLI, an
OpenAI-compatible API Leader, or postpone setup. Open-ended natural-language
requests remain durably pending when no Leader is ready.

The MVP uses one explicitly selected Leader for open-ended planning and a
deterministic local router for slash commands and setup intents. A separately
configurable lightweight Frontdesk and stronger Planner remain a post-MVP
optimization; they cannot complicate the first product path.

## Foreground MVP and future background execution

The MVP runs the confirmed Mission in the foreground. `/exit` safely
interrupts an active Attempt, preserves state, and allows deterministic
re-entry. It may advance only within the frozen scope and permission snapshot.
It pauses on:

- a new permission class;
- destructive, publishing, credential, external-send, or out-of-scope activity;
- plan or artifact drift;
- budget or policy limits;
- Worker loss, protocol inconsistency, or unhandled failure.

When the user returns, AgentDeck summarizes completed turns, current work,
failures, pending permissions, and the exact next decision. After the MVP
passes, a separate design may add a ProjectDaemon that continues after the
terminal closes without changing the Mission authority.

## Delivery sequence

1. **R0-R1 foundation:** isolate the rewrite, enforce architecture boundaries,
   and implement the pure Kernel plus one-writer SQLite using Fake Ports.
2. **R2-R3 product conversation:** deliver first-run discovery, Leader/model/
   permission choice, continuous conversation, and exact Mission Preview.
3. **R4 ACP execution:** deliver Codex implementation, Claude review, Codex
   revision, and Claude acceptance through ACP only.
4. **R5-R6 transparency and recovery:** render real Agent ACP streams in tmux
   and close human-readable diagnostics, interruption, and recovery.
5. **R7 Golden Product Gate:** reproduce the frozen `https://www.iae.cas.cn/`
   home page with four real Worker Instances and human acceptance.
6. **R8 cutover:** switch bare `agentdeck`, retain structured debug commands,
   and migrate old state only through explicit preview and confirmation.
7. **Post-MVP:** background execution, memory, skills, Hermes-inspired governed
   self-improvement, Hive-inspired GUI, broader agents, A2A, and remote clients.

Historical P0/P1, M1, M2, M2c, and M3 labels remain useful capability and test
evidence, but they do not reorder or bypass this R0-R8 sequence.

## Non-goals for the current product line

- Building a terminal emulator or forking WispTerm.
- Replacing AgentDeck with a thin ACP chat client.
- Copying CCB or Hive communication protocols into a second execution system.
- Treating a private `team` command, task Markdown, or PTY text as Mission
  authority.
- Removing deterministic CLI primitives.
- Silent login, trust, permission escalation, skill installation, or memory writes.
- Depending on one model provider.
- Treating tmux output as authoritative ACP state.
- Shipping remote execution or a marketplace before local governance and migration are proven.
- Adding memory, skill learning, self-evolution, or a Hive-style GUI before the
  ACP-native MVP and Golden Product Gate pass.

## Product success test

From a fresh project, a user can run `agentdeck`, select Codex, Claude, or an
API Leader, choose one of three Codex-style permission profiles, describe a
development objective, review one Mission, and confirm once. Four independent
Worker Instances then complete Codex implementation, Claude review, Codex
revision, and Claude acceptance through ACP. tmux displays every Agent's real
decoded ACP work stream. SQLite preserves the full lineage, `/exit` is safe,
and re-entry restores the result. The release proof is a local-only four-Agent
reproduction of the frozen `https://www.iae.cas.cn/` home page plus human
product acceptance.
