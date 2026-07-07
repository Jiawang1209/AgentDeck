# Skills Contract

`agentdeck contract skills` is the read-only discovery surface for the Skill Registry commands that future GUI/TUI clients can render without hard-coding command strings.

It does not read `.agentdeck/state`, does not inspect tmux panes, does not call any Leader provider, and does not import, load, install, rewrite, or enable skills.

## Commands

```bash
agentdeck contract skills
agentdeck contract skills --example
agentdeck skills list
agentdeck skills import --path <SKILL.md>
agentdeck skills show --name <name>
agentdeck skills load --name <name> --agent <agent_id> --purpose <purpose>
```

## Discovery Fields

- `schema_version`: current ProjectView schema version.
- `skills_list_command`: read-only registry listing command.
- `skills_show_command_template`: read-only skill detail command template.
- `skills_import_command_template`: explicit external skill import command template.
- `skills_load_command_template`: explicit skill load command template.
- `contract_path`: absolute path to this document.
- `contract_exists`: whether this document exists in the local checkout.
- `list_response_fields`: ordered fields returned by `agentdeck skills list`.
- `detail_response_fields`: ordered fields returned by `agentdeck skills show`.
- `import_response_fields`: ordered fields returned by `agentdeck skills import`.
- `load_response_fields`: ordered fields returned by `agentdeck skills load`.
- `skill_item_fields`: ordered fields for available skill summaries.
- `detail_skill_fields`: available skill summary fields plus `content`.
- `load_skill_fields`: available skill summary fields plus `content_snapshot`.
- `skill_control_fields`: ordered fields for GUI controls.

## Safety

`show` controls use `safety=inspect`. `import` and `load` controls use `safety=explicit_user`; import controls are templates and disabled until a GUI supplies a concrete `SKILL.md` path. Import copies a local skill into the project registry, while load is the separate action that records replayable context for a Leader or Worker.
