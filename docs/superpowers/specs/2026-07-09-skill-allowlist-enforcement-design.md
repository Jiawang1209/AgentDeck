# Skill allowlist enforcement (`skills import` gated by trusted sources) — Design

- **Date**: 2026-07-09
- **Status**: Approved (the human chose "先A" — do allowlist enforcement first)

## Context

The skill-marketplace lane's read-only visibility is done: `skills catalog`, config `[skills] allowed_sources`, `skills sources`, `source_allowlisted` marker, workbench `skills_catalog_card`, and the NL `mode=skills_catalog`. The allowlist is currently a **read-only marker** — any directory is browsable AND importable. This slice makes it **enforcing**: `agentdeck skills import` refuses to import a `SKILL.md` from outside the trusted `allowed_sources`, unless the human passes an explicit `--allow-unlisted` escape hatch. This turns "an ecosystem" into "a *trusted* ecosystem" while keeping an explicit override.

## Goal

`agentdeck skills import --path <SKILL.md>` rejects (writes nothing) when `[skills] allowed_sources` is configured and the source is not under any allowed source, unless `--allow-unlisted` is given. `skills import-preview` surfaces the allowlist status so the human sees the block before running import. Enforcement is **opt-in**: with no `allowed_sources` configured, behavior is unchanged (backward compatible).

## Non-goals

- No change to `skills catalog` / `skills sources` (they stay read-only, non-blocking browse).
- No skill dependencies (that is the next lane slice, "B").
- No network/remote sources.
- No new config mutation command (allowlist is still hand-edited in config.toml).

## Design

### 1. Allowlist gate (opt-in)

Reuse the existing `_source_is_allowlisted(source_dir, config)` (cli.py) — it resolves the dir and checks equality-or-under against the configured `allowed_sources`. A `SKILL.md` at `<root>/<name>/SKILL.md` has parent `<root>/<name>`, which is under the allowed source `<root>` → allowlisted. (The subagent must confirm the helper's "under" semantics cover nested skill dirs; if it only checks exact equality, extend it to check `is_relative_to` any allowed source.)

Enforcement rule in `skills_import_command`, computed BEFORE `import_project_skill`:
- `allowed = <config [skills] allowed_sources>` (via the existing accessor).
- If `allowed` is **empty** → no enforcement (import as today). Backward compatible.
- Else if the source is allowlisted → import as today.
- Else (configured allowlist, source not listed):
  - If `--allow-unlisted` → import proceeds; the `skill_imported` event records `allowlisted=false, allow_unlisted=true`.
  - Else → **reject**: print `skill source is not in the trusted allowlist: <dir>; add its directory to [skills] allowed_sources, or rerun with --allow-unlisted` to stderr, return non-zero, write nothing (no copy, no event).

The `skill_imported` event gains `allowlisted` (bool) and `allow_unlisted` (bool) fields for auditability. On an allowlisted import, `allowlisted=true, allow_unlisted=false`; with no allowlist configured, `allowlisted=false, allow_unlisted=false` (enforcement inactive).

### 2. `--allow-unlisted` flag

Add `skills_import.add_argument("--allow-unlisted", action="store_true", ...)`. It only matters when a non-empty allowlist would otherwise block; it is the single explicit override.

### 3. `skills import-preview` surfaces the status (read-only)

`skills_import_preview_command` output gains:
- `source_allowlisted` (bool): is the source under a configured allowed source?
- `enforcement_active` (bool): is `allowed_sources` non-empty?
- `import_blocked` (bool): would `skills import` (without `--allow-unlisted`) reject it? (= `enforcement_active and not source_allowlisted`).
- When `import_blocked`, the preview's `import_command` control notes the block / suggests `--allow-unlisted` (keep the existing import command; add the allow-unlisted variant as a separate control or in a blocker note).

This is read-only (preview never imports); it just lets the human see the gate before running import.

### 4. Contract + docs

- Extend the skills contract (`agentdeck contract skills`): add the new import-preview fields (`source_allowlisted`, `enforcement_active`, `import_blocked`) and the `--allow-unlisted` import command variant to the discovery payload + `SKILLS_IMPORT_PREVIEW_RESPONSE_FIELDS` / `SKILLS_IMPORT_RESPONSE_FIELDS` as needed.
- `docs/contracts/skills-schema.md`, `CLAUDE.md` (the skills import rule now enforces the allowlist opt-in with `--allow-unlisted`), `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.

### 5. Safety boundary

- Enforcement is opt-in (empty allowlist = no change) and has a single explicit escape hatch (`--allow-unlisted`), both audited. It does not silently install or auto-enable anything — it only *tightens* the already-explicit, already-audited import.
- Preview stays read-only; catalog/sources stay read-only, non-blocking.
- Import still defaults to no-overwrite (`--force` unchanged); load still requires a separate explicit step.

## Testing

- No `allowed_sources` configured → import from any path succeeds (backward compatible), `skill_imported.allowlisted=false, allow_unlisted=false`.
- `allowed_sources = [<root>]`, import a `SKILL.md` under `<root>` → succeeds, `allowlisted=true`.
- `allowed_sources = [<root>]`, import from a DIFFERENT dir → rejected, non-zero, no file copied, no `skill_imported` event; stderr mentions the allowlist + `--allow-unlisted`.
- Same case + `--allow-unlisted` → imports, `skill_imported.allow_unlisted=true`.
- `skills import-preview` for a non-allowlisted source (allowlist configured) → `source_allowlisted=false`, `enforcement_active=true`, `import_blocked=true`; for an allowlisted source → `import_blocked=false`.
- `agentdeck contract skills` exposes the new fields; full suite green.

## Resolved decisions

- Opt-in enforcement: empty `allowed_sources` = no change; non-empty = block unlisted unless `--allow-unlisted`.
- Single explicit escape hatch `--allow-unlisted`, audited via `skill_imported.allow_unlisted`.
- `import-preview` surfaces `source_allowlisted`/`enforcement_active`/`import_blocked` read-only.
- Reuse `_source_is_allowlisted`; catalog/sources unchanged (read-only browse).
- Next after this: **B — skill dependencies/composition** (its own brainstorm/spec).
