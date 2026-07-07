# Learning Review Contract

`agentdeck contract learning-review` is the read-only discovery surface for `agentdeck learn review --plan-id <id>`, which turns an existing plan's replies and artifacts into explicit skill and memory suggestion commands for GUI/TUI clients.

It does not read tmux panes, does not call any Leader provider, and does not mutate `.agentdeck/state` or `.agentdeck/memory`.

## Commands

```bash
agentdeck contract learning-review
agentdeck contract learning-review --example
agentdeck learn review --plan-id <id>
```

## Discovery Fields

- `schema_version`: current ProjectView schema version.
- `learn_review_command_template`: read-only learning review command template.
- `contract_path`: absolute path to this document.
- `contract_exists`: whether this document exists in the local checkout.
- `response_fields`: ordered fields returned by `agentdeck learn review`.
- `skill_suggestion_fields`: ordered fields in the generated skill suggestion command descriptor.
- `memory_suggestion_fields`: ordered fields in the generated memory suggestion command descriptor.
- `control_fields`: ordered fields for GUI controls.
- `leader_summary_contract`: discovery command for the source summary contract.
- `skills_contract`: discovery command for the Skill Registry contract.
- `memory_contract`: discovery command for the Memory suggestion contract.

## Safety

`agentdeck learn review --plan-id <id>` only reuses existing plan status, Leader summary, reply, and artifact facts. It may generate GUI-ready `agentdeck skills suggest ... --source learn-review` and `agentdeck memory suggest ... --source learn-review` commands, but it must not run them.

The command must not create, import, load, or modify skills; must not write `skill_suggestions[]` or `memory_suggestions[]`; must not create or modify `.agentdeck/memory/*.md`; must not call providers; must not inspect tmux panes; and must not create plans, actions, approvals, messages, jobs, replies, artifacts, or inbox items.
