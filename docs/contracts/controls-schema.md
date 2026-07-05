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

`source_command` stays `agentdeck workbench` because the registry is derived from the workbench snapshot. `default_command` is `agentdeck controls`, the dedicated command palette entrypoint GUI/TUI clients can refresh directly.

Each `items[]` entry uses the workbench `control_registry_item_fields`. The registry includes Leader controls, concrete control-mode policy controls, runtime controls, and operator controls from the same workbench snapshot. Policy items are derived from `control_mode_card.active_controls[]`: GUI clients should render the concrete `agentdeck policy set-mode --mode ask|approve|autonomous` items directly, keep the current mode disabled, and keep the blocked autonomous item visible but unavailable. When the operator action is batch approval dispatch, the registry preserves `kind=dispatch_ready` for the `agentdeck approval dispatch-ready --confirm` item so clients can identify it without parsing labels or command strings.

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
