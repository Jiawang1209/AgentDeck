# Skill dependencies — read-only resolution (`agentdeck skills deps`) — Design

- **Date**: 2026-07-09
- **Status**: Approved (the human chose "先A再B"; this is B's first, read-only slice)

## Context

Decision B of the skill ecosystem: let a skill declare dependencies on other skills. This is the largest area, so it starts with a **read-only** slice: parse a `depends_on` frontmatter list and expose a read-only dependency resolution (`agentdeck skills deps --name <name>`) that shows the transitive dependency set, which deps are present vs missing, and whether there is a cycle — without loading, importing, or writing anything. Later slices (behind their own decisions) may surface unmet deps in `load-preview`; **auto-loading/auto-importing dependencies is explicitly a later product fork**, not this slice.

## Goal

`agentdeck skills deps --name <name>` returns a read-only dependency view for a discovered skill: its declared `depends_on`, the transitive resolution among discovered skills (built-in + project), the `missing` declared deps not found, `has_cycle` (+ the cycle path), and a topological `order` when acyclic. It loads nothing, imports nothing, writes no state, calls no provider, touches no tmux.

## Non-goals

- No auto-load / auto-import of dependencies (a later product fork).
- No version constraints / lockfiles (later fork).
- No remote/network dependency fetch.
- No change to `skills load` / `import` behavior in this slice (a later slice may add a read-only "unmet deps" note to `load-preview`).

## Design

### 1. Parse `depends_on` (frontmatter)

`SkillSnapshot` gains `depends_on: tuple[str, ...] = ()`. In `_snapshot_from_content`, parse `depends_on=tuple(_metadata_list(metadata.get("depends_on")))` (reuse the existing `_metadata_list`, same as `required_tools`). This is additive — do NOT change `SkillSnapshot.summary()` in this slice (avoids touching every skill-summary validator/consumer); the deps command reads the attribute directly.

### 2. Pure resolver (new in `src/agentdeck/skills.py`)

`resolve_skill_dependencies(root: Path, name: str) -> dict` (pure over `discover_skills(root)`):
- Build `{name: snapshot}` from `discover_skills(root)`.
- If `name` not found → raise `KeyError`.
- DFS from `name` over `depends_on`:
  - `resolved`: transitive dep names that exist in the map (excluding `name` itself), in a stable deterministic order.
  - `missing`: declared dep names (direct or transitive) not in the map.
  - `has_cycle` + `cycle` (the offending path) via DFS colouring; when a cycle is found, still return `has_cycle=true` and the cycle path (don't crash).
  - `order`: a topological order of `name` + resolved deps (deps before dependents) when acyclic; `[]` (or the DFS order) when `has_cycle`.
- Returns `{name, depends_on: [...direct...], resolved: [...], missing: [...], has_cycle: bool, cycle: [...], order: [...]}`. Pure, no I/O beyond `discover_skills`.

### 3. `agentdeck skills deps --name <name>` (read-only)

- `--name` required; unknown skill → error, non-zero, no output.
- Calls the resolver, wraps as `mode=skills_deps` with the resolver fields + a `controls[]` of inspect commands (`agentdeck skills show --name <dep>` for each resolved/missing dep). Read-only.
- Self-validate via `validate_skills_deps_contract` before printing (project convention).

### 4. Contract + docs

Extend the skills contract (`agentdeck contract skills`): add `SKILLS_DEPS_RESPONSE_FIELDS`, expose `deps_command` / `deps_response_fields` in the discovery payload; add `validate_skills_deps_contract`. Update `docs/contracts/skills-schema.md`, `README.md`, `CLAUDE.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.

### 5. Safety boundary

- Read-only: parses frontmatter + reads discovered skills; loads/imports/writes nothing.
- Declares dependencies but does NOT act on them — no auto-load, no auto-import. Cycle detection prevents pathological input from crashing; missing deps are reported, not fetched.

## Testing

- A project skill `a` with `depends_on: [b]`, and `b` present → `deps --name a` → `depends_on=[b]`, `resolved=[b]`, `missing=[]`, `has_cycle=false`, `order` has `b` before `a`.
- `a` depends on missing `z` → `missing=[z]`, `resolved` excludes it, `has_cycle=false`.
- Transitive: `a→b→c` all present → `resolved=[b,c]`, `order=[c,b,a]`.
- Cycle: `a→b→a` → `has_cycle=true`, `cycle` shows the loop, no crash, exit 0 (it's a valid read-only report of a bad graph).
- Unknown `--name` → non-zero, no output.
- Read-only: project `.agentdeck/skills/` and state unchanged after `deps`.
- `agentdeck contract skills` exposes the deps fields; full suite green.

## Resolved decisions

- `depends_on` frontmatter list (reuse `_metadata_list`); parsed onto `SkillSnapshot.depends_on` without changing `summary()` this slice.
- Read-only `skills deps --name <name>` resolution with transitive resolve / missing / cycle detection / topo order.
- No auto-load / auto-import / versions / remote — those are later forks (STOP + ask).
