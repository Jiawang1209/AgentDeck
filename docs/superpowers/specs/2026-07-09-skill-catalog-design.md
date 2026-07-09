# Skill source catalog (`agentdeck skills catalog`) — Design

- **Date**: 2026-07-09
- **Status**: Approved (pending spec review)

## Context

First slice of the Skill marketplace/ecosystem lane (the human picked "浏览一个 skill 源/目录"). Today `discover_skills(root)` finds only **built-in** skills + **project-local** `.agentdeck/skills/*/SKILL.md`, and import is one-at-a-time from a single `--path <SKILL.md>` (`import-preview` → `import`). There is no notion of a **source/catalog** — a directory holding many available-but-not-yet-imported skills you can browse read-only before importing. This slice adds that "shop window": point at a local source directory, see every skill on offer with provenance/hash and whether it is already imported, and get the explicit import commands — without importing anything.

## Goal

`agentdeck skills catalog --source <dir>` returns a read-only catalog of the skills found under a source directory, each with name/description/content_hash/path, an `import_status` (`not_imported` / `imported_identical` / `imported_differs` vs the project's skills), and per-skill explicit `import-preview` / `import` commands. It copies nothing, writes no state, appends no event, calls no provider, touches no tmux.

## Non-goals

- No import/copy (browsing ≠ installing; the existing explicit `skills import` — already preview-gated + audited — remains the only install path).
- No trusted-source allowlist yet (any local dir is browsable read-only; the allowlist is a later slice).
- No network/remote fetch (local-first).
- No workbench/natural-language integration yet (later slice), no skill dependencies (later).

## Design

### 1. Source browse (new pure helper in `src/agentdeck/skills.py`)

`browse_skill_source(source_dir: Path) -> list[SkillSnapshot]`: if `source_dir` doesn't exist → raise `FileNotFoundError`. Scan `sorted(source_dir.glob("*/SKILL.md"))` (each subdir is one skill), snapshot each via the existing `_snapshot_from_content(content, source="catalog", path=<the SKILL.md path>, fallback_name=<subdir name>)`. Return the snapshots sorted by name. (Reuses the existing frontmatter/name/hash logic; no new parsing.)

### 2. `agentdeck skills catalog --source <dir>` command (read-only)

- `--source <dir>` required; missing dir → error, exit non-zero, no output.
- Compute the project's imported skills once via `discover_skills(root)` → a `{name: content_hash}` map (project-sourced only; built-ins are not "imported").
- For each catalog snapshot build an item = the snapshot `summary()` PLUS:
  - `import_status`: `imported_identical` if the name exists in the project map with the same `content_hash`; `imported_differs` if the name exists but the hash differs; else `not_imported`.
  - `import_preview_command` = `f"agentdeck skills import-preview --path {path}"`.
  - `import_command` = `f"agentdeck skills import --path {path}"`.
  - `controls[]`: reuse the snapshot's `show` control (inspect) + add `kind=import_preview` (inspect, the preview command) and `kind=import` (safety=`explicit_user`, the import command). (These are surfacing only — not authorization.)
- Output: `mode=skills_catalog`, `source` (str), `skill_count`, `imported_count` (items whose status != not_imported), `items[]`, and a top-level `controls[]` with the import-template control.
- Empty source dir (no `*/SKILL.md`) → `skill_count=0`, `items=[]`, valid, exit 0.

### 3. Contract (extend the existing skills contract)

Extend `agentdeck contract skills` (`skills_contract_*` in `src/agentdeck/contracts.py`) to expose the catalog: add `SKILLS_CATALOG_RESPONSE_FIELDS` and `SKILLS_CATALOG_ITEM_FIELDS`, surface them in the skills contract discovery payload, add a `skills catalog` example to the skills example fixture if a drift test requires it, and update `docs/contracts/skills-schema.md`. (No new top-level contract-index entry — catalog is part of the skills registry contract.)

### 4. Safety boundary (preserved)

- Read-only: scans the source dir + reads project skills for the status compare; no file copy, no state write, no event, no provider, no tmux.
- `import_status` and the per-item commands are **discovery only**. Installing still requires the human to run the explicit `skills import --path <...>`, which is itself preview-gated and audited (`skill_imported` event) and still defaults to no-overwrite.
- Any local directory is browsable (read-only is harmless); the trusted-source allowlist is a separate later slice.

## Testing

- A temp source dir with two `<name>/SKILL.md` files → `skills catalog --source <dir>` lists both with correct name/content_hash/path, `import_status=not_imported`, `skill_count=2`, `imported_count=0`, and per-item `import_preview_command`/`import_command`.
- After importing one into the project (or seeding a project skill with the same name+content) → that item's `import_status=imported_identical`; a project skill with the same name but different content → `imported_differs`.
- Missing `--source` dir → non-zero, no output. Empty source dir → `skill_count=0`, exit 0.
- Read-only: project `.agentdeck/skills/` and state unchanged after running catalog.
- `agentdeck contract skills` exposes the catalog fields; the skills contract example/validator (if any) stays green.
- Full suite green.

## Resolved decisions

- `skills catalog --source <dir>` (not `browse`); read-only browse of a local source directory of `<name>/SKILL.md`.
- Three-state `import_status` (not_imported / imported_identical / imported_differs) by name + content_hash vs project skills.
- No allowlist in this slice (any local dir browsable); allowlist, workbench/NL integration, and dependencies are later lane slices.
- Reuse `_snapshot_from_content` + `discover_skills`; extend the existing `agentdeck contract skills` rather than adding a new contract-index entry.
