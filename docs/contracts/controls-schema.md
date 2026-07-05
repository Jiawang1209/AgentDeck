# Controls Contract

`agentdeck controls` returns a GUI-ready command palette card derived from the same workbench snapshot used by `agentdeck workbench`.

Discovery:

```bash
conda activate agentdeck
agentdeck contract controls
agentdeck contract controls --example
```

Live payload:

```bash
conda activate agentdeck
agentdeck controls
```

Top-level discovery fields:

- `schema_version`
- `controls_command`
- `contract_path`
- `contract_exists`
- `control_registry_card_fields`
- `control_registry_item_fields`
- `workbench_contract`
- `leader_chat_contract`

`control_registry_card_fields`:

- `mode`
- `title`
- `source_command`
- `default_command`
- `item_count`
- `items`

Each `items[]` entry uses the workbench `control_registry_item_fields`:

- `scope`
- `card`
- `kind`
- `label`
- `command`
- `safety`
- `enabled`
- `blocker`
- `agent_id`

Safety rules:

- The command is read-only and does not write AgentDeck state.
- It does not create chat turns, plans, approvals, messages, jobs, replies, or inbox items.
- It does not call a provider, read tmux pane output, send tmux input, or execute any control.
- It is a projection of workbench `control_registry[]`, not a second source of control state.
