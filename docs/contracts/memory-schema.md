# Memory Contract

`agentdeck contract memory` is the read-only discovery surface for long-term memory suggestion commands that future GUI/TUI clients can render without hard-coding command strings.

It does not read `.agentdeck/state`, does not inspect tmux panes, does not call any Leader provider, and does not create, modify, or inject long-term memory.

## Commands

```bash
agentdeck contract memory
agentdeck contract memory --example
agentdeck memory suggest --summary <summary> --rationale <rationale> --source <source>
agentdeck memory suggestions
agentdeck memory apply-preview --suggestion-id <id>
agentdeck memory apply --suggestion-id <id> --confirm
```

## Discovery Fields

- `schema_version`: current ProjectView schema version.
- `memory_suggest_command_template`: explicit command template for adding a pending memory suggestion.
- `memory_suggestions_command`: read-only queue listing command.
- `memory_apply_preview_command_template`: read-only preview command template for a pending suggestion.
- `memory_apply_command_template`: explicit confirmation command template for writing long-term memory.
- `contract_path`: absolute path to this document.
- `contract_exists`: whether this document exists in the local checkout.
- `suggest_response_fields`: ordered fields returned by `agentdeck memory suggest`.
- `suggestions_response_fields`: ordered fields returned by `agentdeck memory suggestions`.
- `apply_preview_response_fields`: ordered fields returned by `agentdeck memory apply-preview`.
- `apply_response_fields`: ordered fields returned by `agentdeck memory apply`.
- `suggestion_item_fields`: ordered fields for memory suggestion items.
- `control_fields`: ordered fields for GUI controls.

## Safety

`memory suggestions` and `memory apply-preview` are read-only. They may expose `apply_preview` and `apply_memory` controls for GUI clients, but they must not write `.agentdeck/memory/*.md`, update suggestion status, append `memory_applied`, inspect tmux panes, call a provider, create plans/actions/approvals/messages/jobs/inbox items, or inject memory into prompts.

Only `agentdeck memory apply --suggestion-id <id> --confirm` may write long-term memory. It must require `--confirm`, append the previewed Markdown to the target memory file, update the suggestion to `status=applied`, record `applied_at` and `applied_path`, and append a `memory_applied` audit event. Unknown, missing-confirmation, or non-pending suggestions must be rejected before writing.
