# AgentDeck

**A local-first, governable, protocol-native multi-agent workbench.**

AgentDeck turns a natural-language goal into a reviewable Mission, coordinates real Codex, Claude, and other Agents, and keeps execution visible, auditable, and recoverable.

> North star: Hermes-like conversation, ACP-native communication, CCB-style real multi-agent collaboration, and a stronger orchestration and governance kernel.

[中文](README.zh-CN.md)

## Start with a conversation

```bash
conda env create -f environment.yml
conda activate agentdeck
python -m pip install -e .
agentdeck
```

Running bare `agentdeck` in a terminal now opens the Phase 3 M1 foreground conversation. In an uninitialized directory it first shows an exact project-setup preview. In a project it can use the configured API-backed LLM or Agent CLI as Leader, turn an open request into a frozen Mission preview, and execute only after natural-language confirmation of that exact preview.

When a project has a background Mission recovery fact, bare `agentdeck` first
prints the validated ProjectView `mission_recovery` card and then enters the
normal conversation UI. A project with no Mission to recover remains quiet.
This reconnect rendering is deterministic and does not call an LLM, inspect
tmux, write state, or reconstruct a transcript. Semantic Missions expose only
their compact step hash, bound across the frozen step, attempt, and validated
result; legacy recovery cards keep their existing exact shape.

```text
You       › Let Codex implement this and Claude review it.
AgentDeck › Mission preview: 2 Workers, approval required.
You       › Confirm the current preview.
AgentDeck › Mission started. Use /status or open the workbench to inspect it.
```

`agentdeck leader chat --message "..."` remains available for scripts and debugging.

## What works today

- explicit API or Agent-CLI Leader identity and readiness;
- deterministic `/help`, `/status`, `/approvals`, `/trace`, setup, and exit intents without an LLM call;
- bounded foreground conversation context with compact, transcript-free conversation state;
- exact, expiring, consume-once preview confirmation;
- one authoritative on-demand project daemon that continues a confirmed Mission after the client disconnects;
- deterministic reconnect, crash reconciliation, and exact permission/ownership/safety pauses;
- Mission planning, approval, dispatch, inbox/reply/ack, trace, workflow, and recovery primitives;
- ACP Worker routing when configured and ready, with no silent transport fallback;
- visible read-only tmux mirrors, explicit reroute/takeover, and single-writer ownership;
- ProjectView and versioned GUI-ready contracts for conversation, Leader, and Worker transport facts;
- governed Skill and Memory provenance;
- optional G2 planner/orchestrator split: add `[leader.planner]` / `[leader.orchestrator]`
  sub-sections (each with optional `provider` / `model`, falling back to `[leader]`)
  and `leader plan` / `run --task` / natural-language plan requests run two reasoning
  stages — a planner macro brief with acceptance criteria, then an orchestrator step
  expansion — landing one plan whose record and ProjectView item carry
  `planner_backend`, `orchestrator_backend`, and the frozen `planner_brief` snapshot;
  explicit `--provider/--model` overrides and unconfigured projects keep the
  single-stage path byte-identical, and stage failures are audited as
  `leader_provider_failed` with `stage=planner|orchestrator`;
- G5 quantified review: a review worker may add one `verdict: <single-line JSON>`
  (`review-verdict/v1`: per-criterion `pass|fail|unknown`, `overall`, optional
  `score`) to its structured reply — valid verdicts land on the reply record and
  in ProjectView/trace, and `leader review` / `leader summary` / `run --plan-id`
  derive a read-only `verdict_summary` aligned with the plan's acceptance
  criteria (`unverified`/`extra` gaps included); invalid verdicts never block
  reply ingestion, replies without a verdict are byte-identical to before, and
  a verdict never changes any gate, approval, or merge behavior; review-step
  approval dispatches (a later step whose plan already has an earlier task
  branch) additionally embed the plan's acceptance criteria and the verdict
  output format in the worker prompt — prompt context only, never authority.

Useful observation commands:

```bash
agentdeck status
agentdeck workbench
agentdeck controls
agentdeck events --limit 20
agentdeck contract conversation-runtime --example
agentdeck contract leader-backend --example
agentdeck contract worker-transport --example
agentdeck contract migration --example
agentdeck project migration-preview
```

## Safety boundary

Natural language is never execution authority. AgentDeck binds confirmation to exact execution facts, does not silently change ACP to tmux, and keeps permission, approval, runtime-safety, and ownership gates independent. Common inline credential assignments are redacted from durable Mission provenance.

For semantic Missions, AgentDeck is the control plane around LLM reasoning, not
a replacement for it. The user supplies required authority; the Leader may add
separately visible proposals; ambiguous facts remain unresolved; and only the
exact confirmed preview becomes frozen authority. AgentDeck then compiles the
Worker tasks deterministically and binds confirmation to the authority,
compiled-task, policy, and preview-generation facts. That single Mission
confirmation does not grant later ACP tool permissions or bypass runtime
safety, ownership, or approval gates.

ProjectView exposes only compact semantic provenance: schema/state, hashes,
counts, compiled-step count, and blockers. It does not expose full effects,
before/after literals, prompts, or secrets. This slice does not add A2A, remote
execution, a GUI redesign, or a terminal emulator.

Phase 3 M2 now runs daemon-admitted frozen Missions in one verified, on-demand
project daemon. Closing the interactive client does not revoke the frozen
authority or stop the scheduler. AgentDeck mediates every Worker transition,
records compact handoffs before starting the next Worker, and uses the exact
configured ACP or tmux transport without fallback. New permission, ambiguity,
ownership conflict, drift, or safety escalation pauses for an exact human
decision. Bare `agentdeck` reconnects from compact ProjectView facts without an
LLM or transcript reconstruction. Existing projects use read-only migration
preview followed by an expiring explicit confirmation; incomplete historical
Missions remain inspect-only.

M2 is project-local. A2A, remote daemons, global roaming, notifications,
Desktop/IDE Workspace Clients, full transcript recovery, automatic adapter
installation/login, Windows IPC, and a terminal emulator remain future work.

## Architecture

```text
Human / CLI / future TUI or Desktop
              |
      ConversationSession
              |
 Mission / Approval / Ledger / Recovery
              |
   Protocol-native Runtime Kernel
       /                 \
     ACP            tmux visible plane
       \                 /
   Codex / Claude / other Agents
```

ACP standardizes Agent communication; it does not replace AgentDeck's Mission, policy, scheduler, audit, or recovery layers.

## Documentation

- [Product north star](docs/roadmap/product-north-star.md)
- [Phase 3 M1 design](docs/superpowers/specs/2026-07-13-agentdeck-foreground-conversation-design.md)
- [Current development state](docs/handoff/current-development-state.md)
- [Contract index](docs/contracts/contract-index-schema.md)
- [Architecture](docs/architecture/)

Run verification in the project environment:

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
```
