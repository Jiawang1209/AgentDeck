# Skill dependency semver ranges — Design

- **Date**: 2026-07-09
- **Status**: Approved (the human chose semver ranges next, after B-ver)

## Context

B-ver added exact content-hash pins (`name@sha256:<hex>`). This slice adds **semver version ranges** — `name@>=1.2.0`, `name@^1.0.0`, `name@>=1.2,<2.0` — matched against a skill's declared `version`. It reuses B-ver's `version_mismatch` blocker plumbing (already threaded through `skills deps` / `load-plan` / `load --with-deps`); the only new pieces are a `version` frontmatter field, a pure stdlib semver comparator, and range classification in the resolver. Deterministic, local, no network. No external libraries (stdlib only).

## Goal

- Skills declare `version: X.Y.Z` in frontmatter (default `0.0.0`).
- A `depends_on` entry `name@<spec>` where `<spec>` is NOT a `sha256:` pin is a **semver range**. A present dep whose `version` does not satisfy the range → `version_mismatch` (`{name, expected: <spec>, actual: <version>}`), a hard blocker (same as B-ver hash pins). Plain `name` = any version (unchanged). Hash pins (`@sha256:`) keep working (exact content match).

## Supported semver subset (precise — everything else is unsupported and treated as a blocker)

- **Version**: `MAJOR[.MINOR[.PATCH]]`, each a non-negative integer; missing parts default to 0 (`1.2` → `1.2.0`, `1` → `1.0.0`). No pre-release / build metadata.
- **Range** = one or more comma-separated comparators, ALL of which must hold (AND):
  - bare `X.Y.Z` or `==X.Y.Z` → exact equality
  - `>=X.Y.Z`, `>X.Y.Z`, `<=X.Y.Z`, `<X.Y.Z`
  - `^X.Y.Z` (caret) → `>=X.Y.Z` AND `< (X+1).0.0`
- **Unsupported** (document): `.x`/`*` wildcards, `~`, pre-release tags, `||` OR. A `depends_on` semver spec that cannot be parsed is a **blocker** (`version_mismatch` with `expected=<spec>`, `actual=<version>`, and reason `unsupported version spec`) — fail-safe, never silently pass.

## Design

### 1. `version` frontmatter

`SkillSnapshot` gains `version: str = "0.0.0"`, parsed in `_snapshot_from_content` as `str(metadata.get("version") or "0.0.0")`. Add `version` to `SkillSnapshot.summary()` — check that adding one field to the summary does not break skill-summary validators (they check required-fields-present, not exact-set; if any asserts an exact set, update it). This makes versions visible in `skills list`/`show`/catalog.

### 2. Pure semver comparator (new in `skills.py`, stdlib only)

- `parse_version(text) -> tuple[int,int,int] | None`: parse `MAJOR[.MINOR[.PATCH]]`; return the 3-tuple or `None` if unparseable.
- `version_satisfies(version: str, spec: str) -> bool`: parse `version`; split `spec` on `,`; for each comparator parse operator + operand version; ALL must hold (tuple comparison on the 3-tuples). Caret `^a.b.c` expands to two bounds. If `version` or any comparator is unparseable → `False` (fail-safe).

### 3. Range classification in the resolver

`_parse_dep(entry)` already returns `(name, spec)`. In `resolve_skill_dependencies`, when a dep is present and `spec` is set:
- If `spec.startswith("sha256:")` → the existing B-ver content-hash check.
- Else (semver range) → `if not version_satisfies(snapshots[dep_name].version, spec)`: record `version_mismatch` `{name, expected: spec, actual: snapshots[dep_name].version, reason: "version range not satisfied" | "unsupported version spec"}`; do NOT recurse (blocker leaf).
- Else (matches / no spec) → recurse as today.

`version_mismatch` already flows through `skills deps` / `load-plan` (blockers) / `load --with-deps` (rejects) from B-ver — no new threading needed beyond letting the semver branch populate it. (Add an optional `reason` key to the mismatch dict; keep `name/expected/actual` for back-compat with B-ver tests.)

### 4. Contract + docs

`agentdeck contract skills` discovery: note the `version` field and the semver range syntax (the `version_mismatch` fields already exist from B-ver; add `reason` if you include it). Update `docs/contracts/skills-schema.md`, `CLAUDE.md`, `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.

### 5. Safety boundary

- Deterministic, local, no network, no external libs. Unsatisfied or unparseable range = hard blocker (load writes nothing) — never silently pass.
- Hash pins (B-ver) and plain names unchanged. Adding `version` defaults to `0.0.0` for skills that don't declare one (backward compatible).

## Testing

- `parse_version`: `"1.2.3"`→(1,2,3); `"1.2"`→(1,2,0); `"1"`→(1,0,0); `"x"`→None.
- `version_satisfies`: `>=1.2.0` sat by `1.2.0`/`2.0.0`, not `1.1.9`; `<2.0.0` sat by `1.9.9`, not `2.0.0`; `^1.2.0` sat by `1.9.9`, not `2.0.0` and not `1.1.0`; `>=1.2,<2.0` (comma AND) sat by `1.5.0`, not `2.0.0`; exact `1.2.3` only by `1.2.3`; unparseable spec → False.
- Resolver: skill `b` version `1.5.0`; `a depends_on [b@>=1.2,<2]` → `b` resolved (no mismatch); `a depends_on [b@>=2.0]` → `version_mismatch` for `b`; `a depends_on [b@sha256:...wrong]` → hash mismatch (B-ver still works); `a depends_on [b]` → resolved (any).
- `skills deps`/`load-plan` show the semver `version_mismatch`; `load --with-deps --confirm` rejects an unsatisfied range writing nothing.
- Skills without a `version` frontmatter default to `0.0.0`; existing tests stay green.
- Full suite green.

## Resolved decisions

- `version` frontmatter (default `0.0.0`); precise supported subset (exact / `>= > <= <` / `^` / comma-AND); everything else unsupported → blocker.
- Semver ranges reuse the B-ver `version_mismatch` blocker; hash pins (`sha256:`) and plain names unchanged.
- Stdlib only, no network. Next after semver: **lockfile generation** (its own spec); then **remote/C** (STOP + a dedicated design conversation — network/signing/supply-chain).
