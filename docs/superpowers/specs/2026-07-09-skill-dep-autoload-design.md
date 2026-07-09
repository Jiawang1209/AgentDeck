# Skill dependency auto-load — preview + explicit confirm (`skills load --with-deps`) — Design

- **Date**: 2026-07-09
- **Status**: Approved (the human chose "先 B-auto"; the "preview + explicit confirm, never silent" default was accepted)

## Context

B's read-only dependency visibility is done (`skills deps`, `load-preview.unmet_dependencies`). This slice (B-auto) is the first that ACTS on dependencies: when loading a skill, also load its dependency chain — but as **preview + explicit confirm**, never silently. It loads only skills already **present** (built-in or project); a **missing** dependency is a blocker (it must first go through the existing explicit, allowlist-gated `skills import`). It never auto-imports and never bypasses the "dependencies must be explicit" boundary.

## Goal

- `agentdeck skills load-plan --name <name> --agent <id>` — read-only preview of the dependency load plan: the topological order (deps before the skill), each item's status (already_loaded / to_load / missing), blockers (missing deps, cycle), `can_load`, and the explicit confirm command.
- `agentdeck skills load --name <name> --agent <id> --with-deps --confirm` — execute the plan: load each not-yet-loaded skill in dependency order, each audited; reject (write nothing) if there is a missing dep or a cycle. Single-skill `skills load` (without `--with-deps`) is unchanged.

## Non-goals

- No auto-**import** of missing deps (missing = blocker; import stays the explicit allowlist-gated flow).
- No silent/implicit loading (always `--with-deps` + `--confirm`).
- No version constraints/lockfiles (that is B-ver, next).
- No remote deps.

## Design

### 1. Load plan (pure-ish helper)

`_skill_load_plan(config, store, name, agent)` reusing `resolve_skill_dependencies(root, name)` and the agent's existing `skill_loads`:
- Resolve deps. If `has_cycle` → plan has a cycle blocker, `can_load=false`.
- `order` = the resolver's topo order (deps first, `name` last). For each name in `order`:
  - `missing` (not in resolved graph, i.e. in resolver `missing`) → status `missing` (blocker).
  - already in the agent's `skill_loads` (by name) → status `already_loaded` (skip on execute).
  - else → status `to_load`.
- `blockers[]`: `"missing dependency: <x>"` for each missing dep; `"dependency cycle: <path>"` if cyclic.
- `can_load` = no blockers AND at least one `to_load`.
- Returns `{name, agent, order:[{name,status,source}], to_load:[names], already_loaded:[names], missing:[names], has_cycle, cycle, blockers, can_load, confirm_command}` where `confirm_command = f"agentdeck skills load --name {name} --agent {agent} --with-deps --confirm"`.

### 2. `skills load-plan` (read-only)

Wrap `_skill_load_plan` as `mode=skill_load_plan` with controls (inspect `skills show`/`skills deps`). Validates via `validate_skill_load_plan_contract`. No writes. Unknown skill/agent → error.

### 3. `skills load --with-deps --confirm` (execute, gated)

Extend the `skills load` subparser with `--with-deps` and `--confirm`.
- Without `--with-deps` → the existing single-skill load, UNCHANGED (no `--confirm` needed; backward compatible).
- With `--with-deps`:
  - Require `--confirm` (else reject, no writes).
  - Compute `_skill_load_plan`. If `not can_load` (missing dep or cycle) → reject with the blockers, write nothing.
  - Else load each `to_load` skill in `order` (deps first, then `name`), skipping `already_loaded`, via the existing `store.record_skill_load` + `skill_loaded` event per skill. Append one `skill_deps_loaded` summary event (loaded names, agent).
  - Output `mode=skill_deps_loaded`, agent, loaded[] (per skill: name/load_id/source), skipped_already_loaded[], and the plan echo.

### 4. Contract + docs

Extend `agentdeck contract skills`: add `SKILL_LOAD_PLAN_RESPONSE_FIELDS` + `validate_skill_load_plan_contract`, and the execute response fields; expose `load_plan_command` in discovery. Update `docs/contracts/skills-schema.md`, `README.md`, `CLAUDE.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.

### 5. Safety boundary

- Loading dependencies is **explicit** (`--with-deps` + `--confirm`) and **previewable** (`load-plan` shows exactly what will load before you confirm). Never silent.
- Only loads skills already present; a missing dep is a hard blocker (no auto-import — import stays the explicit, allowlist-gated, audited flow). A cycle is a hard blocker.
- Every dependency load is its own `skill_loaded` event + a `skill_deps_loaded` summary → visible in `agentdeck history`.
- Single-skill `skills load` behavior is unchanged (backward compatible).

## Testing

- `load-plan` for `a` (deps `b` present, `z` missing) → `order` deps-first, `b` status per its load state, `z` status `missing`, blockers include `z`, `can_load=false`.
- `load-plan` for `a` (deps all present, none loaded) → `to_load=[b, a]` (topo), `can_load=true`, `confirm_command` set. Read-only (state unchanged).
- `skills load --name a --agent <id> --with-deps` without `--confirm` → reject, no writes.
- ... `--with-deps --confirm` with all deps present → loads `b` then `a` (order), two `skill_loaded` events + one `skill_deps_loaded`; a second run skips already_loaded.
- Missing dep + `--with-deps --confirm` → reject, no writes, blockers mention the missing dep.
- Cycle + `--with-deps --confirm` → reject, no writes.
- Single-skill `skills load` (no `--with-deps`) → unchanged behavior (existing tests green).

## Resolved decisions

- Preview + explicit confirm (`load-plan` then `load --with-deps --confirm`); never silent.
- Missing dep = blocker (no auto-import); cycle = blocker. Loads only present skills, deps-first topo order, skipping already-loaded.
- Single-skill `skills load` unchanged (opt-in `--with-deps`).
- Next after B-auto: **B-ver** (version constraints/lockfiles) — its own brainstorm/spec.
