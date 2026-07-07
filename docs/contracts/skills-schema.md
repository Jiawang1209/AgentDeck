# Skills Contract

`agentdeck contract skills` is the read-only discovery surface for the Skill Registry commands that future GUI/TUI clients can render without hard-coding command strings.

It does not read `.agentdeck/state`, does not inspect tmux panes, does not call any Leader provider, and does not import, load, install, rewrite, or enable skills.

## Commands

```bash
agentdeck contract skills
agentdeck contract skills --example
agentdeck skills list
agentdeck skills import-preview --path <SKILL.md>
agentdeck skills import --path <SKILL.md>
agentdeck skills show --name <name>
agentdeck skills load-preview --name <name> --agent <agent_id> --purpose <purpose>
agentdeck skills load --name <name> --agent <agent_id> --purpose <purpose>
agentdeck skills suggest --name <name> --summary <summary> --rationale <rationale> --source <source>
agentdeck skills suggestions
```

## Discovery Fields

- `schema_version`: current ProjectView schema version.
- `skills_list_command`: read-only registry listing command.
- `skills_show_command_template`: read-only skill detail command template.
- `skills_import_preview_command_template`: read-only external skill import preview command template.
- `skills_import_command_template`: explicit external skill import command template.
- `skills_load_preview_command_template`: read-only skill load preview command template.
- `skills_load_command_template`: explicit skill load command template.
- `skills_suggestions_command`: read-only skill suggestion queue command.
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
- `skill_item_fields`: ordered fields for available skill summaries.
- `suggestion_item_fields`: ordered fields for pending skill suggestions.
- `detail_skill_fields`: available skill summary fields plus `content`.
- `load_skill_fields`: available skill summary fields plus `content_snapshot`.
- `skill_control_fields`: ordered fields for GUI controls.

## Safety

`show` controls use `safety=inspect`. `import-preview` is read-only: it parses a concrete external `SKILL.md`, returns the target project path, hash, overwrite state, and GUI-ready `import` / `force_import` / `show_after_import` controls, but it does not copy files, append events, load skills, or mutate state. `load-preview` is also read-only: it returns the target agent, purpose, skill summary, explicit `agentdeck skills load ...` command, and show/load controls, but it does not write `skill_loads[]`, append `skill_loaded`, call a provider, inspect tmux, or create plan/action/approval/message/job/inbox state. `suggestions` is read-only and lists the skill suggestion queue. `suggest` records a pending suggestion and audit event, but it does not create `SKILL.md`, import, load, install, rewrite, call a provider, inspect tmux, or alter runtime/approval state. `import` and `load` controls use `safety=explicit_user`; import controls are templates and disabled until a GUI supplies a concrete `SKILL.md` path. Import copies a local skill into the project registry, while load is the separate action that records replayable context for a Leader or Worker.
