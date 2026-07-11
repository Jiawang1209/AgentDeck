# AgentDeck Product North Star

Updated: 2026-07-11

## Product promise

AgentDeck is a local-first multi-agent workbench. A user should be able to enter a project, describe one goal in ordinary language, review one frozen Mission, confirm it once, and leave while a governed team of heterogeneous agents continues the work. The user can return later, understand exactly what happened, inspect or take over any Worker, approve newly introduced risk, and resume without losing provenance.

The product north star is:

> AgentDeck combines Hermes-like natural interaction, ACP-native agent communication, CCB-like visible and controllable multi-agent collaboration, and a future WispTerm-class workspace experience with AgentDeck's stronger Mission orchestration, governance, audit, and recovery core.

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
把这次经验整理成 skill 建议。
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
- Structured communication instead of terminal-screen inference.
- Portable agent integrations across clients and providers.
- A path for AgentDeck to act both as an ACP client and, later, as an ACP agent that exposes an entire governed Mission team.

ACP replaces fragile Worker communication mechanisms. It does not replace Mission planning, scheduling, governance, or audit.

### From CCB

- Real heterogeneous agents with parity, independent sessions, and independent context.
- Project-scoped multi-agent workspaces.
- Asynchronous delegation: submission, acknowledgement, progress, and completion are distinct states.
- Visible terminals and human takeover when needed.
- Dynamic role and Worker lifecycle management.

AgentDeck keeps stronger frozen-Mission, approval, lineage, and recovery semantics. Terminal state is an observation surface, not the protocol authority.

### From WispTerm

- A future cross-platform workspace with terminals, tabs, splits, files, diffs, artifacts, Markdown, images, PDFs, browser panels, remote projects, and history recovery.
- A product-quality command center and session browser.

This is a future client experience, not the current core implementation. AgentDeck Core remains headless, protocol-driven, and independently testable. The current roadmap does not include building or forking a Zig terminal emulator.

## Architecture principles

```text
Human / CLI / TUI / Desktop / future ACP client
                       |
              AgentDeck Frontdesk
                       |
        Mission / Planner / Orchestrator
                       |
 Approval / Policy / Skill / Memory / Ledger / Recovery
                       |
          Protocol-Native Runtime Kernel
        /              |              \
   ACP native      ACP adapter      tmux fallback
        \              |              /
       Codex / Claude / Gemini / OpenCode / future agents
```

1. **Project first, globally navigable.** Project state is isolated; the global Frontdesk stores only user-level preferences and a project index.
2. **One project daemon is authoritative.** Interactive clients may disconnect. Confirmed Missions continue in the background.
3. **One confirmation covers one frozen scope.** Ordinary steps do not repeatedly interrupt the user. New permission, plan drift, risk escalation, or runtime failure pauses the Mission.
4. **Sessions, not panes, identify Workers.** A stable `session_id` survives transport and UI changes. A `pane_id` is an optional observation binding.
5. **ACP is the preferred transport.** ACP adapters bridge compatible CLIs during migration. tmux remains a visible fallback and takeover surface.
6. **All facts enter one ledger.** Prompts, updates, tools, permissions, replies, artifacts, and failures are append-only evidence projected through ProjectView.
7. **Context is not authority.** Prompts, skills, memory, role packs, and ACP metadata cannot expand permissions.
8. **Headless core, replaceable clients.** CLI, TUI, desktop, remote, and IDE clients consume the same contracts and event stream.
9. **No big-bang rewrite.** Internal V2 refactoring is allowed; user projects, history, and commands receive previewed, backed-up, reversible migration.
10. **Evidence before product claims.** Every phase ends with contracts, failure tests, crash recovery, and a real multi-agent Golden Demo.

## LLM setup

`agentdeck` must always start. Deterministic status, audit, approval, and recovery commands do not require an LLM.

On first use AgentDeck detects available ACP agents, logged-in CLIs, API providers, and local models. It recommends but never silently selects a Frontdesk model. The user may choose Codex, Claude, another provider, or skip setup. Open-ended natural-language requests explain that an LLM is required when none is configured.

Frontdesk and Planner are separately configurable. A lightweight Frontdesk can route ordinary intent, while a stronger Planner handles difficult decomposition and acceptance criteria.

## Background execution

After explicit Mission confirmation, the project daemon continues when the user closes the terminal. It may advance only within the frozen scope and policy snapshot. It pauses on:

- a new permission class;
- destructive, publishing, credential, external-send, or out-of-scope activity;
- plan or artifact drift;
- budget or policy limits;
- Worker loss, protocol inconsistency, or unhandled failure.

When the user returns, AgentDeck summarizes completed turns, current work, failures, pending permissions, and the exact next decision.

## Delivery sequence

1. Freeze the current natural-language Mission Golden Demo as the compatibility baseline.
2. Introduce transport, agent-session, turn, update, and permission contracts without changing user behavior.
3. Complete one real ACP vertical slice.
4. Add the project daemon and default `agentdeck` conversation.
5. Run a confirmed Codex-and-Claude Mission over ACP with permission bridging and recovery.
6. Add global project roaming and opt-in notifications.
7. Build a CCB/WispTerm-class observable workspace client after the headless core is stable.

## Non-goals for the current product line

- Building a terminal emulator or forking WispTerm.
- Replacing AgentDeck with a thin ACP chat client.
- Removing deterministic CLI primitives.
- Silent login, trust, permission escalation, skill installation, or memory writes.
- Depending on one model provider.
- Treating tmux output as authoritative ACP state.
- Shipping remote execution or a marketplace before local governance and migration are proven.

## Product success test

From a fresh project, a user can run `agentdeck`, select or skip a detected model, describe a multi-agent objective, review one Mission, confirm once, close the terminal, and later return to a complete or safely paused result. Codex and Claude communicate through structured sessions, every step is auditable, no new authority is inferred, and the same state is visible through natural language, CLI contracts, and the optional live workspace.
