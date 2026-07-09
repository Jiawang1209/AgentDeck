# Skill dependency version pinning (content-hash) — Design

- **Date**: 2026-07-09
- **Status**: Approved (the human chose "再 B-ver"; content-hash pinning is the local-first default)

## Context

B-auto lets a skill load its dependency chain (preview + confirm, blocking on missing/cycle). B-ver adds **version constraints** — but local-first, deterministic, no network and no semver registry: a `depends_on` entry may **pin a content hash**, `name@sha256:<hex>`. The resolver checks the present dependency's `content_hash` against the pin; a mismatch is a new blocker category `version_mismatch` (distinct from `missing`), and `skills deps` / `load-plan` / `load --with-deps` all treat it as a hard blocker. Plain `name` (no pin) means "any version" (unchanged).

## Goal

- `depends_on: [b@sha256:<hex>, c]` — `b` pinned to a content hash, `c` unpinned.
- `resolve_skill_dependencies` reports `version_mismatch[]` (entries `{name, expected, actual}`) when a pinned dep is present but its hash differs; such a dep is neither `resolved` nor `missing`.
- `skills deps`, `skills load-plan`, and `skills load --with-deps --confirm` treat `version_mismatch` as a blocker (load rejects, writes nothing) — identical handling to `missing`/cycle.

## Non-goals

- No semver ranges / version intervals (a later fork).
- No lockfile generation / lock strategy (a later fork).
- No remote/network resolution.

## Design

### 1. Parse `name@pin`

Add a pure `_parse_dep(entry: str) -> tuple[str, str | None]` in `skills.py`: split on the first `@`; if there is an `@` and the suffix is non-empty, return `(name, pin)` (pin = the raw suffix, e.g. `sha256:abcd…`); else `(entry, None)`. `depends_on` on `SkillSnapshot` stays a tuple of the raw entries (unchanged parsing); the resolver interprets each entry.

### 2. Extend `resolve_skill_dependencies`

When iterating a node's `depends_on` entries, for each raw entry:
- `(dep_name, pin) = _parse_dep(entry)`.
- If `dep_name` not in snapshots → `missing` (as today; record `dep_name`).
- Else if `pin` is set and `snapshots[dep_name].content_hash != pin` → record `version_mismatch` as `{name: dep_name, expected: pin, actual: snapshots[dep_name].content_hash}`; do NOT recurse into it (it is a blocker leaf).
- Else recurse via `visit(dep_name)` (existing behaviour; the topo/cycle logic is unchanged and keyed on `dep_name`).

Return gains `version_mismatch: list[dict]`. `resolved`/`missing`/`has_cycle`/`cycle`/`order` semantics are otherwise unchanged (a version-mismatched dep is excluded from `resolved` and from `order`).

### 3. Thread `version_mismatch` as a blocker

- `skills deps`: output gains `version_mismatch` (add to `SKILLS_DEPS_RESPONSE_FIELDS` + validator). Read-only.
- `_skill_load_plan` / `skills load-plan`: add `version_mismatch` to the payload; add `f"version mismatch: {m['name']} expected {m['expected']}"` to `blockers`; `can_load` already `= not blockers and ...`, so a mismatch blocks. Add the field to `SKILL_LOAD_PLAN_RESPONSE_FIELDS` + validator.
- `skills load --with-deps --confirm`: since it rejects when `not can_load`, a version mismatch already blocks it (writes nothing). No extra logic — just ensure the blocker message surfaces.

### 4. Contract + docs

Update the skills contract discovery + `docs/contracts/skills-schema.md` (document `name@sha256:` pins + `version_mismatch`), `CLAUDE.md`, `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.

### 5. Safety boundary

- Pins are content-hash equality — deterministic, local, no network. A mismatch is a hard blocker (load writes nothing), exactly like a missing dep or a cycle.
- No behaviour change for unpinned deps (`name` = any version). No auto-import, no auto-fix, no silent load.

## Testing

- `_parse_dep`: `"b@sha256:ab"` → `("b", "sha256:ab")`; `"b"` → `("b", None)`; `"b@"` → `("b", None)` (empty suffix ignored).
- `a depends_on [b@<correct-hash>]`, `b` present with that hash → `resolved=[b]`, `version_mismatch=[]`, `can_load` true.
- `a depends_on [b@sha256:wrong]`, `b` present → `version_mismatch=[{name:b, expected:sha256:wrong, actual:<b hash>}]`, `b` not in `resolved`, `skills deps`/`load-plan` show it, `load-plan.can_load=false`.
- `skills load --name a --agent <id> --with-deps --confirm` with a version mismatch → rejected, no writes, stderr mentions the mismatch.
- Unpinned `depends_on [b]` → unchanged behaviour.
- Full suite green.

## Resolved decisions

- Content-hash pins (`name@sha256:<hex>`); plain `name` = any. Deterministic, local, no network.
- `version_mismatch` is a new blocker category alongside `missing` / cycle; `skills deps` / `load-plan` / `load --with-deps` all treat it as a hard blocker.
- No semver ranges, no lockfiles, no remote — those are later forks (STOP + ask).
