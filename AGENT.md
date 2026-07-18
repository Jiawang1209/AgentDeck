# AGENT.md

## AgentDeck Product Kernel agent contract

The active implementation authority is:

`docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md`

Read `AGENTS.md` for coding rules. This file defines the product meaning of an
Agent in the rewrite.

## Agent identity

An Agent Backend is a callable implementation such as Codex CLI, Claude CLI,
or an OpenAI-compatible API. An Agent Instance is one independent session in
one Mission. A Role is a responsibility such as Leader, implementer, reviewer,
reviser, or acceptance reviewer.

Backend, Instance, Role, Task, Attempt, ACP Session, and tmux pane identities
must remain distinct and auditable.

## Leader

The selected Leader interprets the user's goal and proposes a Mission Draft.
It may not:

- confirm the Mission;
- increase permissions;
- schedule Workers directly;
- forge evidence;
- declare acceptance;
- turn a proposal into execution authority.

AgentDeck validates the proposal and renders the exact human Mission Preview.

## Worker

A Worker receives one AgentDeck-owned Task and reports progress, tool use,
permission requests, artifacts, and results through ACP. It may not:

- write AgentDeck's SQLite database;
- schedule or directly notify another Worker;
- approve itself;
- treat tmux text as a handoff;
- declare a dependent Task ready.

AgentDeck validates the result, writes the handoff, and starts the next Task.

## Communication

Automatic Codex/Claude communication is:

```text
AgentDeck -> ACP -> Agent
Agent -> ACP -> AgentDeck
```

tmux receives a redacted, cursor-safe projection of real decoded ACP events.
It is an observation and explicit human-takeover surface only.

## Permission

The product exposes:

- Ask for approval;
- Approve for me;
- Full access.

Effective permission can only narrow through ProductSession, Mission, Task,
Attempt, and action. Prompts are not enforcement. Every permission decision
retains lineage.

## Evidence and completion

Worker prose is not sufficient completion authority. Acceptance requires typed
facts such as test status, diff or artifact identity, review findings,
acceptance result, or an explicit human decision.

The default coding flow is:

```text
Codex implementation
-> Claude review
-> Codex revision
-> Claude acceptance
```

Each arrow is an AgentDeck-owned validated handoff, not direct peer
communication.

Historical Agent behavior and removed design documents are not active product
requirements. Reuse from the old implementation is permitted only through the
Adapter admission process in `AGENTS.md`.
