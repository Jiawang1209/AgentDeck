# AGENTS.md

This file is the coding-agent entrypoint for the AgentDeck Product Kernel
Rewrite.

## Active authority

Read these files before planning or changing anything:

1. `docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md`
2. `docs/roadmap/product-north-star.md`
3. the approved Product Kernel Rewrite TDD plan, once it exists
4. `docs/handoff/current-development-state.md`
5. the top of `HISTORY.md`

The Rewrite Design is the active implementation authority. The North Star
protects long-term product intent. HISTORY, validation evidence, legacy code,
legacy tests, and legacy contracts cannot create requirements or change the
R0-R8 order.

## Current gate

Implementation is locked until the cleaned written Rewrite Design and a
separate `writing-plans` TDD plan are reviewed and approved. Do not infer
approval from this file.

## Rewrite boundaries

New product code belongs only in:

```text
src/agentdeck/kernel/
src/agentdeck/application/
src/agentdeck/ports/
src/agentdeck/adapters/
src/agentdeck/product/
```

Dependency direction:

```text
Product -> Application -> Kernel
                   \-> Ports <- Adapters
Legacy code -> admitted Adapters only
```

- Kernel performs no filesystem, database, environment, subprocess, tmux,
  network, provider, or clock I/O.
- Kernel cannot import legacy AgentDeck modules.
- Application depends only on Kernel and Ports.
- Product renders state and calls use cases; it owns no domain rules.
- Only an explicitly admitted Adapter may wrap legacy code.
- The new Product Shell cannot call old CLI commands as an internal API.

## Product invariants

- Codex and Claude automatic Leader/Worker communication uses ACP only.
- CLI/PTY is manual compatibility, diagnosis, or takeover; never automatic
  fallback.
- tmux shows decoded real Agent events; it is not communication, scheduling,
  persistence, or completion authority.
- One project-local `.agentdeck/agentdeck.db` and one foreground writer own
  MVP structured state.
- Workers do not write AgentDeck state or schedule peers.
- Leader output is untrusted proposal data until AgentDeck validates it.
- One exact human confirmation binds one exact Mission Preview.
- Permission can only narrow from ProductSession to action.
- Worker prose alone cannot prove acceptance; typed evidence is required.
- Errors are rendered in plain language with safe next actions.

## Legacy context firewall

Legacy code is `not admitted` by default. Reading or reusing it requires a
task-specific legacy-admission step with:

1. a Rewrite Design requirement;
2. a new Port;
3. a characterization test;
4. an Adapter-only integration;
5. a reuse-register entry;
6. architecture-test coverage.

Do not resume P1, M2c, the old daemon, old ConversationSession, old Router,
Skill/Memory work, run-loop work, or removed specs/plans. Real validation files
are evidence only.

Existing `docs/contracts/` describe the legacy compatibility surface unless
the Rewrite Design explicitly adopts a contract. They are not new Kernel
models.

## Task discipline

Every implementation task must name:

- authoritative Rewrite Design sections;
- allowed files;
- forbidden legacy imports;
- approved legacy evidence, if any;
- deterministic RED test and expected failure;
- minimal GREEN behavior;
- regression commands;
- one commit boundary.

Use TDD for every behavior change. Update `HISTORY.md` in the same commit.
Keep unrelated user changes untouched.

## Environment and verification

Use the existing conda environment:

```bash
conda activate agentdeck
python -m pip install -e .
pytest -q
python -m compileall src tests -q
```

Prefer focused tests during development and proportional full verification
before completion. Do not claim PASS without fresh command output.

## Safety and Git

Design or planning approval does not authorize implementation, provider calls,
ACP/tmux live runs, adapter installation, authentication changes, global
configuration changes, merge, push, or destructive Git operations.

Develop in the isolated rewrite worktree. Preserve the user's main checkout
and its untracked files. Create local commits for approved slices; do not push
or merge without explicit authorization.
