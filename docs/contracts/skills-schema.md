# Skills Contract

`agentdeck contract skills` is the read-only discovery surface for the Skill Registry commands that future GUI/TUI clients can render without hard-coding command strings.

It does not read `.agentdeck/state`, does not inspect tmux panes, does not call any Leader provider, and does not import, load, install, rewrite, or enable skills.

## Commands

```bash
agentdeck contract skills
agentdeck contract skills --example
agentdeck skills list
agentdeck skills deps --name <name>
agentdeck skills load-plan --name <name> --agent <agent_id>
agentdeck skills load --name <name> --agent <agent_id> --with-deps --confirm
agentdeck skills catalog --source <dir>
agentdeck skills import-preview --path <SKILL.md>
agentdeck skills import --path <SKILL.md>
agentdeck skills show --name <name>
agentdeck skills load-preview --name <name> --agent <agent_id> --purpose <purpose>
agentdeck skills load --name <name> --agent <agent_id> --purpose <purpose>
agentdeck skills suggest --name <name> --summary <summary> --rationale <rationale> --source <source>
agentdeck skills suggestions
agentdeck skills draft-preview --suggestion-id <id>
agentdeck skills create --suggestion-id <id> --confirm
```

## Discovery Fields

- `schema_version`: current ProjectView schema version.
- `skills_list_command`: read-only registry listing command.
- `catalog_command`: read-only source-browse command (`agentdeck skills catalog --source <dir>`).
- `catalog_response_fields`: ordered fields returned by `agentdeck skills catalog` (`SKILLS_CATALOG_RESPONSE_FIELDS`, now including the non-enforcing top-level `source_allowlisted` marker).
- `catalog_item_fields`: ordered fields on each catalog item (`SKILLS_CATALOG_ITEM_FIELDS`).
- `sources_command`: read-only trusted-source listing command (`agentdeck skills sources`).
- `sources_response_fields`: ordered fields returned by `agentdeck skills sources` (`SKILLS_SOURCES_RESPONSE_FIELDS`).
- `deps_command`: read-only skill dependency resolution command (`agentdeck skills deps --name <name>`).
- `deps_response_fields`: ordered fields returned by `agentdeck skills deps` (`SKILLS_DEPS_RESPONSE_FIELDS`).
- `load_plan_command`: read-only dependency load-plan command template (`agentdeck skills load-plan --name <name> --agent <agent_id>`).
- `skill_load_plan_response_fields`: ordered fields returned by `agentdeck skills load-plan` (`SKILL_LOAD_PLAN_RESPONSE_FIELDS`).
- `skills_show_command_template`: read-only skill detail command template.
- `skills_import_preview_command_template`: read-only external skill import preview command template.
- `skills_import_command_template`: explicit external skill import command template.
- `skills_load_preview_command_template`: read-only skill load preview command template.
- `skills_load_command_template`: explicit skill load command template.
- `skills_suggestions_command`: read-only skill suggestion queue command.
- `skills_draft_preview_command_template`: read-only skill draft preview command template.
- `skills_create_command_template`: explicit skill creation command template.
- `skills_suggest_command_template`: explicit skill suggestion command template.
- `contract_path`: absolute path to this document.
- `contract_exists`: whether this document exists in the local checkout.
- `list_response_fields`: ordered fields returned by `agentdeck skills list`.
- `detail_response_fields`: ordered fields returned by `agentdeck skills show`.
- `import_preview_response_fields`: ordered fields returned by `agentdeck skills import-preview`.
- `import_response_fields`: ordered fields returned by `agentdeck skills import`.
- `load_preview_response_fields`: ordered fields returned by `agentdeck skills load-preview` (includes read-only `unmet_dependencies` / `has_dependency_cycle`).
- `load_response_fields`: ordered fields returned by `agentdeck skills load`.
- `suggest_response_fields`: ordered fields returned by `agentdeck skills suggest`.
- `suggestions_response_fields`: ordered fields returned by `agentdeck skills suggestions`.
- `draft_preview_response_fields`: ordered fields returned by `agentdeck skills draft-preview`.
- `create_response_fields`: ordered fields returned by `agentdeck skills create`.
- `skill_item_fields`: ordered fields for available skill summaries.
- `suggestion_item_fields`: ordered fields for pending skill suggestions.
- `detail_skill_fields`: available skill summary fields plus `content`.
- `load_skill_fields`: available skill summary fields plus `content_snapshot`.
- `skill_control_fields`: ordered fields for GUI controls.

## Source catalog (`skills catalog --source <dir>`)

`agentdeck skills catalog --source <dir>` is a read-only browse ("shop window") of a local skill source directory of `<name>/SKILL.md`. It reuses the same frontmatter/name/hash snapshot logic as the rest of the registry (`_snapshot_from_content`) and compares each source skill against the project's imported skills (`discover_skills`, `source == "project"` only — built-ins are not "imported"). The response fields are `SKILLS_CATALOG_RESPONSE_FIELDS` (`ok`, `mode=skills_catalog`, `source`, `skill_count`, `imported_count`, `controls`, `items`); each item carries `SKILLS_CATALOG_ITEM_FIELDS` — the standard skill summary fields plus a three-state `import_status`, `import_preview_command`, and `import_command`. `import_status` is one of:

- `not_imported`: no project skill with this name.
- `imported_identical`: a project skill with the same name and identical `content_hash`.
- `imported_differs`: a project skill with the same name but a different `content_hash`.

Catalog is discovery only: it copies no files, writes no state, appends no event, calls no provider, and touches no tmux. A missing `--source` directory exits non-zero with no output; an empty source directory returns `skill_count=0`, `items=[]`, exit 0. Browsing never installs — installing still goes through the explicit, preview-gated, audited `skills import --path <SKILL.md>` (still no-overwrite by default). The per-item `import_preview`/`import` controls surface those commands but are not authorization.

The catalog response also carries a top-level `source_allowlisted` (bool). It is `True` when the resolved `--source` directory equals, or sits under, one of the configured trusted sources in `[skills] allowed_sources`. This catalog marker is **non-enforcing**: any directory is still fully browsable regardless of the flag, and the catalog still lists every skill. Enforcement lives on `skills import` only (see "Allowlist enforcement on import" below); `catalog` and `sources` stay read-only, non-blocking browse.

## Trusted skill sources (`[skills] allowed_sources` + `skills sources`)

`[skills] allowed_sources` is a hand-edited list of trusted local skill source directories in `.agentdeck/config.toml`, e.g.:

```toml
[skills]
allowed_sources = ["/path/to/skill-source", "/another/source"]
```

It is parsed into `config.skills["allowed_sources"]` (default empty) and round-trips through `_dump_config`, so config writers (`update_leader_approval_mode`, `update_autonomous_policy`, …) never drop a hand-added `[skills]` section. There is no mutation command this slice — the allowlist is hand-edited like the other config, so future workbench/NL surfaces can browse the configured sources with no argument.

`agentdeck skills sources` is a read-only listing of the configured trusted sources. The response fields are `SKILLS_SOURCES_RESPONSE_FIELDS` (`ok`, `mode=skills_sources`, `source_count`, `sources`, `controls`); each `sources[]` item carries `{path, exists (bool), catalog_command}` where `catalog_command = agentdeck skills catalog --source <path>`, and each `controls[]` entry is an inspect-only control pointing at the same catalog command. It writes no state/config, appends no event, calls no provider, and touches no tmux. An empty/absent allowlist returns `source_count=0`, `sources=[]`.

## Allowlist enforcement on import (opt-in) + `--allow-unlisted`

`agentdeck skills import --path <SKILL.md>` enforces `[skills] allowed_sources` — but **only when the allowlist is non-empty** (opt-in, backward compatible):

- Empty/absent `allowed_sources` → no enforcement; import behaves exactly as before.
- Non-empty allowlist + source under a listed directory → import proceeds.
- Non-empty allowlist + source **not** under any listed directory → import is **rejected** (writes nothing: no copy, no `skill_imported` event; exits non-zero). stderr reads `skill source is not in the trusted allowlist: <dir>; add its directory to [skills] allowed_sources, or rerun with --allow-unlisted`.
- `--allow-unlisted` is the single explicit escape hatch: it overrides the block and imports anyway.

A source is "under" a listed directory when its resolved parent equals or `is_relative_to` a resolved `allowed_sources` entry (so `<root>/<name>/SKILL.md` counts as under `<root>`); this reuses `_source_is_allowlisted`. Both paths are audited: the `skill_imported` event gains `allowlisted` (bool: was the source under a trusted source) and `allow_unlisted` (bool: did `--allow-unlisted` override a block). On an allowlisted import → `allowlisted=true, allow_unlisted=false`; on an escape-hatch import → `allowlisted=false, allow_unlisted=true`; with no allowlist configured → `allowlisted=false, allow_unlisted=false` (enforcement inactive).

`agentdeck skills import-preview --path <SKILL.md>` surfaces the gate read-only (it still imports nothing): it adds `source_allowlisted` (bool), `enforcement_active` (bool: is the allowlist non-empty), and `import_blocked` (bool: `enforcement_active and not source_allowlisted` — would `skills import` without `--allow-unlisted` reject it).

## Skill dependencies (`depends_on` + `skills deps --name <name>`)

A skill's `SKILL.md` frontmatter may declare a `version: X.Y.Z` (default `0.0.0`; surfaced in `SkillSnapshot.summary()`, so it appears in `skills list`/`show`/`catalog`) and a `depends_on` list of other skill names (parsed with the same `_metadata_list` helper as `required_tools`; inline `depends_on: [a, b]` list syntax is tolerated). A dependency entry `name@<spec>` may pin the present dependency in one of two ways; plain `name` (no `@spec`) means "any version" (unchanged):

- **Content-hash pin** — `name@sha256:<hex>` (decision B-ver): requires the present dependency's `content_hash` to equal the pin exactly.
- **Semver range** — any `<spec>` that is not a `sha256:` pin (decision semver): matched against the dependency's declared `version` frontmatter. A range is one or more comma-separated comparators, ALL of which must hold (AND). Supported subset: bare `X.Y.Z` or `==X.Y.Z` (exact); `>=`, `>`, `<=`, `<`; caret `^X.Y.Z` (expands to `>=X.Y.Z` AND `< (X+1).0.0`); comma-AND like `>=1.2,<2.0`. Versions are `MAJOR[.MINOR[.PATCH]]` (missing parts default to 0). **Unsupported** (`.x`/`*` wildcards, `~`, pre-release tags, `||` OR) or any otherwise unparseable range is a **hard blocker** (fail-safe — never silently passes). Ranges are deterministic, local, no network, stdlib-only (`parse_version` + `version_satisfies` in `skills.py`).

Each raw entry is interpreted by the resolver via the pure `_parse_dep(entry)` (splits on the first `@`; an empty suffix, e.g. `name@`, is ignored → no pin). `SkillSnapshot.depends_on` stays a tuple of the raw entries. This is **parsed but not acted on** as metadata; the deps command reads `snapshot.depends_on` directly.

`agentdeck skills deps --name <name>` is a **read-only** dependency resolution over the discovered skills (built-in + project). It loads nothing, imports nothing, writes no state, calls no provider, and touches no tmux. Unknown `--name` → non-zero exit, no output. Response (`mode=skills_deps`) fields (`SKILLS_DEPS_RESPONSE_FIELDS`):

- `name`: the queried skill.
- `depends_on`: its declared direct dependency names.
- `resolved`: the transitive dependency names that exist among discovered skills (excluding `name`), sorted.
- `missing`: declared deps (direct or transitive) not found among discovered skills — reported, never fetched.
- `version_mismatch`: entries `{name, expected, actual, reason}` for a pinned/ranged dep that IS present but fails its spec — a content-hash pin whose `content_hash` differs (`reason="content hash mismatch"`, `actual` = the present hash) or a semver range the dep's `version` does not satisfy / an unparseable range (`reason="version range not satisfied"`, `actual` = the present version). `expected` is the raw spec. A mismatched dep is neither `resolved` nor `missing`; it is a distinct blocker category (a blocker leaf — the resolver does not recurse into it).
- `has_cycle` (bool) + `cycle`: if the dependency graph has a cycle reachable from `name`, `has_cycle=true` and `cycle` is the offending path (it is a valid read-only report of a bad graph — no crash, exit 0).
- `order`: a topological order (deps before dependents) of `name` + resolved deps when acyclic; `[]` when `has_cycle`.
- `controls`: inspect-only `agentdeck skills show --name <dep>` controls for each resolved/missing dep.

`depends_on` is metadata only in this slice: `skills deps` does **not** auto-load or auto-import dependencies. Cycle detection prevents pathological input from crashing; missing deps are reported, not resolved over the network.

## Dependency load plan (`skills load-plan` + `skills load --with-deps --confirm`)

This is the first slice (decision B-auto) that ACTS on dependencies — done as **preview + explicit confirm, never silent**.

`agentdeck skills load-plan --name <name> --agent <agent_id>` is a **read-only** preview of the dependency load plan. It reuses `resolve_skill_dependencies` and the agent's existing `skill_loads` to compute, for a deps-first topological `order`, what would be loaded. It writes no state, appends no event, imports/loads nothing, calls no provider, and touches no tmux. Unknown skill/agent → non-zero exit, no output. Response (`mode=skill_load_plan`) fields (`SKILL_LOAD_PLAN_RESPONSE_FIELDS`):

- `name` / `agent`: the queried skill and target agent.
- `order`: deps-first topo order; each item `{name, status, source}` where `status` is `to_load` or `already_loaded` (`[]` when cyclic).
- `to_load`: names not yet loaded for the agent, in load order.
- `already_loaded`: names already in the agent's `skill_loads` (skipped on execute).
- `missing`: declared deps not found among discovered skills — a hard blocker (never fetched, never auto-imported).
- `version_mismatch`: entries `{name, expected, actual, reason}` for a present-but-mismatched content-hash pin or unsatisfied/unparseable semver range — a hard blocker, identical handling to `missing`/cycle.
- `has_cycle` (bool) + `cycle`: a dependency cycle is a hard blocker.
- `blockers`: `"missing dependency: <x>"` per missing dep, `"version mismatch: <name> expected <pin>"` per version mismatch, and/or `"dependency cycle: <path>"`.
- `can_load` (bool): `true` only when there are no blockers AND at least one `to_load`.
- `confirm_command`: the explicit `agentdeck skills load --name <name> --agent <agent_id> --with-deps --confirm`.
- `controls`: inspect-only `agentdeck skills show --name <item>` controls per ordered item.

`agentdeck skills load --name <name> --agent <agent_id> --with-deps --confirm` executes the plan (`mode=skill_deps_loaded`): it loads each `to_load` skill deps-first via the existing `store.record_skill_load` + a `skill_loaded` event per skill, then appends one `skill_deps_loaded` summary event. It is **gated**: without `--with-deps` it is the unchanged single-skill load; with `--with-deps` it requires `--confirm` (else reject, no writes) and rejects (writing nothing) on a missing dependency, a version mismatch, or a dependency cycle. It never auto-imports — a missing dep stays a hard blocker routed through the separate explicit, allowlist-gated `skills import`. Single-skill `skills load` (no `--with-deps`) behavior is unchanged.

## Safety

`show` controls use `safety=inspect`. `import-preview` is read-only: it parses a concrete external `SKILL.md`, returns the target project path, hash, overwrite state, and GUI-ready `import` / `force_import` / `show_after_import` controls, but it does not copy files, append events, load skills, or mutate state. `load-preview` is also read-only: it returns the target agent, purpose, skill summary, explicit `agentdeck skills load ...` command, and show/load controls, plus a read-only dependency note — `unmet_dependencies` (the skill's declared `depends_on` names not present among discovered skills, from `resolve_skill_dependencies`) and `has_dependency_cycle` (bool). This note is purely informational: it does NOT block the preview and does NOT auto-load or auto-import any dependency. `load-preview` still does not write `skill_loads[]`, append `skill_loaded`, call a provider, inspect tmux, or create plan/action/approval/message/job/inbox state. `suggestions` is read-only and lists the skill suggestion queue; pending items include a draft-preview inspect control. `draft-preview` is read-only: it turns a pending suggestion into proposed `SKILL.md` content, hash, target path, and an explicit create control, but it does not create files, update suggestion status, append events, import, load, call a provider, inspect tmux, or mutate runtime/approval state. `create` requires `--confirm`; it writes the proposed `SKILL.md`, marks the suggestion as `created`, and appends `skill_created`, but it does not load the skill, call a provider, inspect tmux, or alter runtime/approval state. `suggest` records a pending suggestion and audit event, but it does not create `SKILL.md`, import, load, install, rewrite, call a provider, inspect tmux, or alter runtime/approval state. `import`, `create`, and `load` controls use `safety=explicit_user`; import controls are templates and disabled until a GUI supplies a concrete `SKILL.md` path. Import copies a local skill into the project registry, create writes a reviewed suggestion draft into the project registry, and load is the separate action that records replayable context for a Leader or Worker.
