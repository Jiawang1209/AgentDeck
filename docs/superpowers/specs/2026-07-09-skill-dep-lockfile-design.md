# Skill dependency lockfile — generate + read-only verify — Design

- **Date**: 2026-07-09
- **Status**: Approved (the human chose lockfile after semver)

## Context

Dependency resolution is deterministic (hash pins, semver ranges). A **lockfile** freezes the currently-resolved dependency tree of a skill — each dep's `name` + `content_hash` + `version` — so later you can detect drift (a dep changed hash/version, or appeared/disappeared). This slice does **generate + read-only verify** only; enforcing the lock during resolution (making `resolve`/`load` use the lock) is a bigger, later slice (it would change default resolution behaviour). Local, explicit, no network.

## Goal

- `agentdeck skills lock --name <name>` — resolve `<name>`'s dependency tree; if fully resolvable (no missing / cycle / version_mismatch) write a lockfile capturing each resolved dep's `{name, content_hash, version}`; append a `skill_locked` audit event. Refuses (writes nothing) if the tree has blockers.
- `agentdeck skills lock-verify --name <name>` — read-only: compare the current resolution against the lockfile and report drift (`changed` / `added` / `removed`); does not modify anything.

## Non-goals

- No enforcing the lock during `skills deps` / `load` resolution (default resolution is unchanged; the lock is advisory drift-detection). That is a later slice.
- No project-wide lock (per-skill lock this slice).
- No network / remote.

## Design

### 1. Lock content + location

Lockfile: `.agentdeck/skill-locks/<name>.json` (a dedicated dir, so it is NOT picked up by `discover_skills` which globs `.agentdeck/skills/*/SKILL.md`). Content:

```json
{"name": "<name>", "locked_at": "<utc>", "dependencies": [{"name": "b", "content_hash": "sha256:…", "version": "1.5.0"}, …]}
```

`dependencies` = each name in the resolver's `order` **excluding the root `name`** (i.e. the resolved deps), each with its current `content_hash` + `version` from `discover_skills`.

### 2. `agentdeck skills lock --name <name>` (explicit write)

- `--name` required; unknown skill → error, no writes.
- Resolve via `resolve_skill_dependencies`. If `missing` or `has_cycle` or `version_mismatch` non-empty → reject with the blockers, write nothing (can't lock an unresolvable tree).
- Else build the lock record, write `.agentdeck/skill-locks/<name>.json`, append a `skill_locked` event `{name, dependency_count}`. Output `mode=skill_locked`, name, lock_path, dependencies[]. (Overwrites an existing lock — re-locking is how you update it; the event records it.)

### 3. `agentdeck skills lock-verify --name <name>` (read-only)

- If no lockfile → output `mode=skill_lock_verify`, `locked=false`, a hint to run `skills lock`. exit 0 (not an error; just unlocked).
- Else re-resolve; compute drift by comparing the locked `{name: {hash, version}}` map to the current resolved deps:
  - `changed[]`: dep present in both but `content_hash` or `version` differs (`{name, locked, current}`).
  - `added[]`: currently-resolved dep not in the lock.
  - `removed[]`: locked dep no longer resolved.
  - `in_sync` = no changed/added/removed AND the current tree still fully resolves (no new blockers).
- Output `mode=skill_lock_verify`, `locked=true`, `in_sync`, `changed`/`added`/`removed`, plus current `blockers` (missing/cycle/version_mismatch) if the tree no longer resolves. Read-only — no writes.

### 4. Contract + docs

`agentdeck contract skills`: add `SKILL_LOCK_RESPONSE_FIELDS` / `SKILL_LOCK_VERIFY_RESPONSE_FIELDS` + validators + discovery (`lock_command` / `lock_verify_command`). Update `docs/contracts/skills-schema.md`, `CLAUDE.md`, `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.

### 5. Safety boundary

- `lock` is an explicit write (a lockfile + `skill_locked` event); it refuses to lock an unresolvable tree. `lock-verify` is fully read-only.
- Local only, no network. The lock is **advisory** (drift detection) this slice — it does not change how `deps`/`load` resolve.

## Testing

- `skills lock --name a` (deps `b` present, resolvable) → writes `.agentdeck/skill-locks/a.json` with `b`'s hash+version; `skill_locked` event; output lists `b`.
- `skills lock --name a` when a dep is missing / cyclic / version-mismatched → rejected, no lockfile, no event.
- Unknown `--name` → error, no writes.
- `lock-verify --name a` before locking → `locked=false`, exit 0, read-only.
- `lock-verify` after lock, no change → `in_sync=true`, empty drift.
- Change `b`'s content (new hash) then `lock-verify` → `changed` lists `b` (locked vs current hash), `in_sync=false`. Add a new dep to `a` → `added`. Remove a dep → `removed`. `lock-verify` writes nothing (state + lockfile unchanged).
- `agentdeck contract skills` exposes the lock fields; full suite green.

## Resolved decisions

- Generate (`skills lock`, explicit write + audit, refuses unresolvable) + read-only verify (`skills lock-verify`, drift report). Per-skill lockfile at `.agentdeck/skill-locks/<name>.json`.
- The lock is advisory drift-detection; enforcing it in resolution is a later slice.
- Next after lockfile: **remote/C** — STOP and hold a dedicated design conversation (network / signing / offline / registry format).
