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

Phase 3 M1 is foreground and project-local. It does not yet provide the M2 daemon, background continuation after the client exits, full transcript recovery, global project roaming, a Desktop/IDE Workspace Client, automatic adapter installation/authentication, or native same-session TUI attachment.

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
