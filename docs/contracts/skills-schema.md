# Skills Contract

`agentdeck contract skills` is the read-only discovery surface for the Skill Registry commands that future GUI/TUI clients can render without hard-coding command strings.

It does not read `.agentdeck/state`, does not inspect tmux panes, does not call any Leader provider, and does not import, load, install, rewrite, or enable skills.

## Commands

```bash
agentdeck contract skills
agentdeck contract skills --example
agentdeck skills list
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
- `load_preview_response_fields`: ordered fields returned by `agentdeck skills load-preview`.
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

The catalog response also carries a top-level `source_allowlisted` (bool). It is `True` when the resolved `--source` directory equals, or sits under, one of the configured trusted sources in `[skills] allowed_sources`. This marker is **non-enforcing**: any directory is still fully browsable regardless of the flag, and the catalog still lists every skill. (Enforcement — blocking imports from non-allowlisted sources — is a deliberately deferred product fork and is not implemented.)

## Trusted skill sources (`[skills] allowed_sources` + `skills sources`)

`[skills] allowed_sources` is a hand-edited list of trusted local skill source directories in `.agentdeck/config.toml`, e.g.:

```toml
[skills]
allowed_sources = ["/path/to/skill-source", "/another/source"]
```

It is parsed into `config.skills["allowed_sources"]` (default empty) and round-trips through `_dump_config`, so config writers (`update_leader_approval_mode`, `update_autonomous_policy`, …) never drop a hand-added `[skills]` section. There is no mutation command this slice — the allowlist is hand-edited like the other config, so future workbench/NL surfaces can browse the configured sources with no argument.

`agentdeck skills sources` is a read-only listing of the configured trusted sources. The response fields are `SKILLS_SOURCES_RESPONSE_FIELDS` (`ok`, `mode=skills_sources`, `source_count`, `sources`, `controls`); each `sources[]` item carries `{path, exists (bool), catalog_command}` where `catalog_command = agentdeck skills catalog --source <path>`, and each `controls[]` entry is an inspect-only control pointing at the same catalog command. It writes no state/config, appends no event, calls no provider, and touches no tmux. An empty/absent allowlist returns `source_count=0`, `sources=[]`.

## Safety

`show` controls use `safety=inspect`. `import-preview` is read-only: it parses a concrete external `SKILL.md`, returns the target project path, hash, overwrite state, and GUI-ready `import` / `force_import` / `show_after_import` controls, but it does not copy files, append events, load skills, or mutate state. `load-preview` is also read-only: it returns the target agent, purpose, skill summary, explicit `agentdeck skills load ...` command, and show/load controls, but it does not write `skill_loads[]`, append `skill_loaded`, call a provider, inspect tmux, or create plan/action/approval/message/job/inbox state. `suggestions` is read-only and lists the skill suggestion queue; pending items include a draft-preview inspect control. `draft-preview` is read-only: it turns a pending suggestion into proposed `SKILL.md` content, hash, target path, and an explicit create control, but it does not create files, update suggestion status, append events, import, load, call a provider, inspect tmux, or mutate runtime/approval state. `create` requires `--confirm`; it writes the proposed `SKILL.md`, marks the suggestion as `created`, and appends `skill_created`, but it does not load the skill, call a provider, inspect tmux, or alter runtime/approval state. `suggest` records a pending suggestion and audit event, but it does not create `SKILL.md`, import, load, install, rewrite, call a provider, inspect tmux, or alter runtime/approval state. `import`, `create`, and `load` controls use `safety=explicit_user`; import controls are templates and disabled until a GUI supplies a concrete `SKILL.md` path. Import copies a local skill into the project registry, create writes a reviewed suggestion draft into the project registry, and load is the separate action that records replayable context for a Leader or Worker.
