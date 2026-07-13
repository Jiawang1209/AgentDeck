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
- Mission planning, approval, dispatch, inbox/reply/ack, trace, workflow, and recovery primitives;
- ACP Worker routing when configured and ready, with no silent transport fallback;
- visible read-only tmux mirrors, explicit reroute/takeover, and single-writer ownership;
- ProjectView and versioned GUI-ready contracts for conversation, Leader, and Worker transport facts;
- governed Skill and Memory provenance.

Useful observation commands:

```bash
agentdeck status
agentdeck workbench
agentdeck controls
agentdeck events --limit 20
agentdeck contract conversation-runtime --example
agentdeck contract leader-backend --example
agentdeck contract worker-transport --example
```

## Safety boundary

Natural language is never execution authority. AgentDeck binds confirmation to exact execution facts, does not silently change ACP to tmux, and keeps permission, approval, runtime-safety, and ownership gates independent. Common inline credential assignments are redacted from durable Mission provenance.

Phase 3 M2a now provides the verified one-per-project daemon foundation, `agentdeck daemon status/start/stop/logs`, and compact ProjectView/workbench discovery contracts. `daemon status` is strictly zero-write and reports durable state as last-known/unverified without connecting to the socket. Offline ProjectView derives `controller_present` from a strictly parsed, active-namespace, currently unexpired lease; an expired, terminal, naive, or malformed lease never appears active and the check writes nothing. The daemon's own idle loop reloads keepalive facts on every poll: connected clients keep it ready, non-client Mission/Worker/approval/permission/reply/decision/recovery/outbox/shutdown/write work keeps it busy, and idle grace starts only when no reason remains. A monotonic in-process activity generation advances on every accepted connection and every protocol-valid request, so even a client that connects and closes entirely between polls restarts a full grace window; close itself does not double-count. `agentdeck daemon stop --confirm` opens a verified client, uses the sole lease-exempt `controller.acquire` bootstrap RPC to obtain a temporary controller when needed, then sends a lease-gated stop RPC; callers that already hold the controller may instead add `--lease-id <lease_id> --lease-generation <generation>`. The daemon durably flushes grant/renew/release/expiry audit events, derives `controller_present` from the current unexpired lease, and revalidates the controller lease, endpoint/durable identity, other clients, and keepalive work. If a temporary-controller stop is rejected, the client invokes lease-gated `controller.release` before reporting the blocker; explicit user-provided credentials are never auto-released. Accepted stop still releases before acknowledgement and exits only after response drain. It never exposes lease credentials through ProjectView or workbench and never sends a client-side process signal. M2a does not yet advance Missions in the background: the scheduler surface is explicitly inactive until frozen execution snapshots, deterministic scheduling, supervision, and recovery land in M2b. Full transcript recovery, global project roaming, a Desktop/IDE Workspace Client, automatic adapter installation/authentication, and native same-session TUI attachment also remain future work.

Task 12 update: background Mission scheduling is now active for daemon-admitted frozen Missions. Resume uses a controller-lease-bound two-call preview/confirm flow and never falls back to the foreground runner; incomplete frozen authority is inspect-only. Accepted stop/force-stop now signals shutdown immediately after the durable release/stop commit, independently of acknowledgement delivery. A daemon ACP prompt may bind and consume multiple sequential permissions, and closing its process persists the AgentSession as disconnected.

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
