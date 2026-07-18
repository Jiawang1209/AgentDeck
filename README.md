# AgentDeck

**A local-first product for governed Codex and Claude collaboration.**

[中文](README.zh-CN.md)

AgentDeck is being rebuilt around one simple product journey:

> Run `agentdeck`, choose a real Leader and permission profile, describe a
> development goal, review one human-readable Mission, confirm it once, and
> watch Codex and Claude implement, review, revise, and accept the result
> through ACP while tmux shows their real work streams.

## Current status

The Product Kernel Rewrite is the only active development route. The existing
structured CLI remains available as a legacy compatibility and debugging
surface while the new Kernel is developed side by side. It is not the
architecture of the new product.

The foreground MVP will provide:

- Codex CLI, Claude CLI, or an OpenAI-compatible API Leader;
- explicit model selection and three permission profiles;
- natural-language goals and an exact Mission Preview;
- Codex implementation, Claude review, Codex revision, and Claude acceptance;
- ACP-only automatic Codex/Claude communication;
- tmux panes showing decoded real Agent events;
- one project-local SQLite database and deterministic exit/re-entry;
- plain-language diagnostics instead of opaque internal failure labels.

Background-after-exit execution, Memory, Skills, self-improvement, a browser
GUI, A2A, and remote clients are post-MVP work.

## Architecture

```text
Product -> Application -> Kernel
                   \-> Ports <- Adapters

Legacy code -> admitted Adapters only
```

The domain Kernel owns Mission, permission, scheduling, handoff, evidence, and
recovery invariants. ACP is the automatic transport. tmux is an observation and
manual-takeover surface, never task or completion authority.

## Golden Product Gate

The bare `agentdeck` entrypoint will switch only after a real four-Worker
acceptance reproduces a frozen local copy of the IAE homepage:

1. Codex implements.
2. Claude reviews.
3. Codex revises.
4. Claude accepts.

The gate requires real ACP lineage, tmux visibility, browser/visual evidence,
SQLite recovery, safe exit/re-entry, and human product acceptance.

## Development

```bash
conda activate agentdeck
python -m pip install -e .
pytest -q
```

All development commands must run in the `agentdeck` conda environment.
Product implementation is locked until the Rewrite Design and its separate TDD
plan have both passed human review.

## Authoritative documents

- [Product Kernel Rewrite Design](docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md)
- [Product North Star](docs/roadmap/product-north-star.md)
- [Current development state](docs/handoff/current-development-state.md)
- [Ultimate goal roadmap](docs/roadmap/ultimate-goal-roadmap.md)
- [Legacy capability inventory](docs/migrations/2026-07-17-legacy-capability-inventory.md)

Historical designs and plans were removed from the active working tree to
prevent them from becoming accidental implementation authority. They remain
recoverable from Git history. Existing contract documents describe the legacy
compatibility surface unless the Rewrite Design explicitly adopts them.
