# AgentDeck Current Development State

Updated: 2026-07-18

## Active goal

Complete human review of the cleaned
[AgentDeck Product Kernel Rewrite Design](../superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md)
and its Rewrite Context Firewall.

This is the only active development route. Historical P1, M2, M2c, daemon,
ConversationSession, autonomous, Skill, Memory, and GUI work is not an active
gate, implementation order, or release veto.

## Current state

- The ten-part Product Kernel Rewrite design was approved.
- The Rewrite Context Firewall and direct removal of old design authority were
  approved.
- Old specs, plans, architecture proposals, PRD, live SOPs, and walkthroughs
  have been removed from the active working tree.
- Root Agent instructions, README files, this handoff, and the Rewrite Design
  are aligned to the new authority.
- Existing source, tests, legacy contracts, real validation evidence,
  reference analysis, and the legacy capability inventory remain available.
- No product implementation has begun.

## Approved MVP

- bare `agentdeck` continuous natural-language ProductSession;
- Codex CLI, Claude CLI, or OpenAI-compatible API Leader selection;
- model selection and three Codex-style permission profiles;
- exact human-readable Mission Preview and one confirmation;
- Codex implementation, Claude review, Codex revision, Claude acceptance;
- ACP-only automatic Codex/Claude communication;
- tmux panes showing decoded real Agent events;
- one project-local SQLite database and one foreground writer;
- plain-language diagnostics, safe exit, and deterministic re-entry;
- real four-Worker website-reproduction Golden Product Gate.

The MVP excludes background-after-exit execution, arbitrary Agent graphs,
CLI/PTY automatic fallback, Memory, Skills, self-improvement, GUI, A2A,
remote/mobile clients, and terminal-emulator work.

## Authority order

1. Product Kernel Rewrite Design;
2. Product North Star for long-term product invariants;
3. approved Rewrite TDD plan, once it exists;
4. current Task acceptance;
5. real validation evidence;
6. explicitly admitted legacy Adapter evidence.

HISTORY, legacy code, legacy tests, and legacy contracts cannot create current
requirements.

## Verification baseline

Before the documentation rewrite, the isolated worktree passed:

```text
4461 passed, 3 skipped
```

After the final documentation cleanup, fresh verification passed:

```text
compileall: exit 0
pytest: 4461 passed, 3 skipped in 209.74s (final pre-commit rerun)
source/test/runtime/environment diff: empty
```

No provider, ACP/tmux live run, daemon, preflight, Mission, installation,
authentication change, merge, or push is part of this gate.

## Next gate

1. Create one local documentation commit for the verified context reset.
2. Ask the human to review the cleaned written Rewrite Design.
3. Only after explicit written-spec approval, use `writing-plans` to create
   the detailed per-file, per-test, per-commit Rewrite TDD plan.
4. Review that plan separately before R0 implementation.

## Canonical handoff inputs

Read only:

1. `AGENTS.md`
2. `CLAUDE.md` or `AGENT.md`
3. `docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md`
4. `docs/roadmap/product-north-star.md`
5. the top of `HISTORY.md`
6. this file

Then inspect:

```bash
git status --short
git log --oneline -5
git diff --name-only main...HEAD
```

Removed historical designs can be recovered from Git when a specifically
approved legacy-admission task requires them. They must not be loaded as
general project context.
