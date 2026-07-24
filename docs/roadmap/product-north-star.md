# AgentDeck Product North Star

Updated: 2026-07-24

## 2026-07-24 route status: Line 1 pivot

2026-07-23 的路线转向（human 批准，见
`docs/superpowers/specs/2026-07-23-copilot-natural-line-1-design.md`）更新了
P0–P5 的**执行方式**，但不更改本文件的产品终点：

- **为什么转**：product-kernel-rewrite 在 leader-propose 环节的刚性协议
  （自定义 artifact、禁 agent 用工具）在真实 coding agent 上崩溃。复盘结论是
  规划顺序错——把最 risky 的"真实 agent 假设"推迟到最后验证。
- **新执行方式（Line 1 旋钮路线）**：先用真实 API Leader + 真实 coding-agent
  Worker 在已有自然循环（`leader plan/review` · `approval` · `dispatch` ·
  `capture-reply` · tmux 可见）上跑通最小真实线，再逐个拧旋钮泛化
  （确认粒度、自主度、Leader 插槽、SQLite 零件前向移植）。2026-07-24 Line 1
  round 2 已在真实 DeepSeek Leader + 3 个真实 coding agent 上完整一轮 PASS
  （`docs/validation/2026-07-24-copilot-line1-live-round2-iae-homepage.md`），
  加固切片 A–F 落地。
- **与 P0–P5 的关系**：Line 1 旋钮路线是 P2（Conversation Product）–P4
  （reliable multi-Agent closure）的**实证先行路径**——用真实 agent 逐档验证，
  避免再次先造齐内核。P1 的 durable Mission kernel / daemon 冻结路线**保留为
  收敛目标**：现有 daemon、frozen Mission、crash matrix、ACP client 等能力
  继续作为权威内核演进，最终两条路径在"确认一次即可走开"的产品成功测试上
  汇合。P0 exit 证据与下方 P0–P5 顺序仍然有效，但"每相位先造齐再验真"的
  节奏被"真实 agent 先行、切片验证"取代。
- **零件捐赠者分支**：`codex/product-kernel-rewrite`（sqlite store、
  sqlite_secrets、tmux_observer、分层架构）与 `codex/p1-durable-mission-kernel`
  （SQLite storage/migrations/ownership + daemon 加固，+8863 行）保留为按需
  前向移植的素材，不再是活跃开发线。
- **当前差距总账**：`docs/roadmap/2026-07-24-north-star-gap-review.md`。

## Active architecture-reset route

New development follows the approved, strictly ordered architecture-reset
program:

1. P0 Product Reset
2. P1 Durable Mission Kernel
3. P2 Conversation Product
4. P3 official Codex/Claude adapters
5. P4 reliable multi-Agent closure
6. P5 learning/V1 release

The authoritative program and current execution plan are
[AgentDeck V1 architecture-reset program](../superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md)
and [AgentDeck P0 Product Reset](../superpowers/plans/2026-07-17-agentdeck-p0-product-reset.md).
P0 is the sole current scope; later phases remain locked until their preceding
phase exits and receives review. Prior M2 and M2c work remains preserved as
historical capability and validation evidence. Old M2c status is not a release
veto or live retry authority, and it cannot authorize rerunning any consumed
preflight or live node.

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

AgentDeck is a local-first multi-agent workbench. A user should be able to enter a project, describe one goal in ordinary language, review one frozen Mission, confirm it once, and leave while a governed team of heterogeneous agents continues the work. The user can return later, understand exactly what happened, inspect or take over any Worker, approve newly introduced risk, and resume without losing provenance.

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
- Structured transport instead of terminal-screen inference.
- Portable agent integrations across clients and providers.
- A path for AgentDeck to act both as an ACP client and, later, as an ACP agent that exposes an entire governed Mission team.

ACP is the preferred standard transport for compatible agents. It does not
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
                     Transport Router
          /                   |                    \
      ACP native       CLI / PTY adapter       tmux fallback
          \                   |                    /
          Codex / Claude / Gemini / OpenCode / future agents
```

1. **Project first, globally navigable.** Project state is isolated; the global Frontdesk stores only user-level preferences and a project index.
2. **One project daemon is authoritative.** Interactive clients may disconnect. Confirmed Missions continue in the background.
3. **One confirmation covers one frozen scope.** Ordinary steps do not repeatedly interrupt the user. New permission, plan drift, risk escalation, or runtime failure pauses the Mission.
4. **Sessions, not panes, identify Workers.** A stable `session_id` survives transport and UI changes. A `pane_id` is an optional observation binding.
5. **AgentDeck owns communication truth.** Mission, task, attempt, permission,
   reply, handoff, and acceptance facts belong to one AgentDeck ledger. No
   transport adapter may create a parallel authority system.
6. **ACP is the preferred transport.** ACP adapters bridge compatible CLIs
   during migration. CLI/PTY and tmux remain governed fallbacks, observation
   surfaces, and takeover paths for agents without ACP support.
7. **All facts enter one ledger.** Prompts, updates, tools, permissions, replies, artifacts, and failures are append-only evidence projected through ProjectView.
8. **Context is not authority.** Prompts, skills, memory, role packs, and ACP metadata cannot expand permissions.
9. **Headless core, replaceable clients.** CLI, TUI, browser, desktop, remote, and IDE clients consume the same contracts and event stream. The GUI renders authority; it does not infer it from terminal pixels.
10. **No big-bang rewrite.** Internal V2 refactoring is allowed; user projects, history, and commands receive previewed, backed-up, reversible migration.
11. **Evidence before product claims.** Every phase ends with contracts, failure tests, crash recovery, and a real multi-agent Golden Demo.

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

1. **P0 Product Reset:** freeze the V1 product contract, inventory current
   capabilities, design SQLite and legacy-state migration, classify historical
   tests and evidence, and record a deterministic baseline. This phase is
   documentation, inventory, migration design, and baseline evidence only.
2. **P1 Durable Mission Kernel:** converge one authoritative project daemon and
   Mission/Task/Attempt/Permission/Handoff/Evidence model after P0 exits and is
   reviewed.
3. **P2 Conversation Product:** make the natural-language project conversation
   compose the durable kernel without creating a second authority system.
4. **P3 official Codex/Claude adapters:** integrate governed official adapters
   over the P1/P2 authority model, with ACP preferred and explicit fallbacks.
5. **P4 reliable multi-Agent closure:** prove bounded heterogeneous-agent
   execution, permission, recovery, handoff, and acceptance end to end.
6. **P5 learning/V1 release:** complete safe skill and memory learning,
   compatibility, release evidence, and the V1 product gate.

Historical M1, M2, M2c, and M3 labels below and elsewhere remain useful
capability evidence, but they do not reorder or bypass this P0-P5 sequence.

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

## Product success test

From a fresh project, a user can run `agentdeck`, select or skip a detected
model, describe a multi-agent objective, review one Mission, confirm once,
close the terminal, and later return to a complete or safely paused result.
Codex and Claude communicate through AgentDeck-governed structured sessions;
ACP is preferred and CLI/PTY or tmux remains an explicit fallback. Every step
is auditable, no new authority is inferred, and the same state is visible
through natural language, CLI contracts, and an optional live browser
workspace where the user can watch, inspect, take over, and return control.
