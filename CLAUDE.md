# CLAUDE.md

This is the Claude coding entrypoint for the AgentDeck Product Kernel Rewrite.

## Read first

1. `AGENTS.md`
2. `docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md`
3. `docs/roadmap/product-north-star.md`
4. the approved Rewrite TDD plan, once it exists
5. `docs/handoff/current-development-state.md`
6. the top of `HISTORY.md`

The Rewrite Design is the only active implementation design. Removed designs
remain in Git history but are not requirements. Existing source, tests,
validation, and contracts are evidence or compatibility surfaces only.

## Non-negotiable boundaries

- Follow R0-R8 in order.
- Do not implement before written-spec and TDD-plan approval.
- New code uses Kernel/Application/Ports/Adapters/Product boundaries.
- Only admitted Adapters may wrap legacy modules.
- Codex/Claude automatic communication uses ACP only.
- tmux is observation and explicit takeover, never task or completion
  authority.
- One project-local SQLite database and one foreground writer own MVP state.
- Workers do not write state or schedule peers.
- Leader proposals are validated before becoming a confirmable Mission.
- Use deterministic TDD and update HISTORY in every behavior-changing commit.
- Use the `agentdeck` conda environment.

## Do not resume

Do not resume old P1, M2c, daemon, ConversationSession, Router, run-loop,
Skill, Memory, learning, or GUI implementation because a historical file,
test, or source module mentions it. Those directions are outside the active
MVP unless the Rewrite Design and an approved new plan explicitly introduce
them.

Do not call a provider, run ACP/tmux live acceptance, install adapters, change
authentication or global settings, merge, or push without explicit authority.

When legacy evidence is needed, read only the files named by the current task
and keep all reuse behind a new Port and admitted Adapter.
