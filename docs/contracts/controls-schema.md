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

Each `items[]` entry uses the workbench `control_registry_item_fields`. The registry includes Leader controls, provider switch controls, concrete control-mode policy controls, role assignment controls, runtime controls, inbox controls, Leader inbox controls, and operator controls from the same workbench snapshot. Provider items are derived from `provider_health.controls[]`: GUI clients should render the concrete `agentdeck leader set-provider --provider <provider> --model <model>` items directly, keep the current provider disabled with `already current provider`, and treat every enabled provider switch as `safety=explicit_user`. `validate_control_registry_card_contract()` rejects provider `kind=set_provider` items that do not use `safety=explicit_user` or do not point at `agentdeck leader set-provider --provider ...`. Policy items are derived from `control_mode_card.active_controls[]`: GUI clients should render the concrete `agentdeck policy set-mode --mode ask|approve|autonomous` items directly, keep the current mode disabled, and keep the blocked autonomous item visible but unavailable. Role items are derived from `role_card.agents[].controls[]`: GUI clients should render `kind=assign_role` as an explicit configuration form, keep the template disabled until concrete `role` and `role_prompt` values are supplied, and never infer role edits from labels. Inbox items are derived from visible `inbox_card.items[].controls[]` and fixed `leader_inbox_card.items[].controls[]`: GUI clients should render `kind=preview` as lineage inspection and `kind=ack` as an explicit acknowledgement command, preserving `card=inbox_card` for active recovery mailbox items and `card=leader_inbox_card` for worker replies flowing back to Leader. Runtime terminal items preserve `kind=terminal` for `agentdeck agent terminal --agent <id>` so clients can render an "Open terminal" action without parsing labels or command strings. When the operator action is batch approval dispatch, the registry preserves `kind=dispatch_ready` for the `agentdeck approval dispatch-ready --confirm` item so clients can identify it without parsing labels or command strings.

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
- Provider `kind=set_provider` controls are explicit configuration commands only; `agentdeck controls` never runs them and never calls the selected provider.
- Role `kind=assign_role` controls are explicit configuration templates only; `agentdeck controls` never fills placeholders or writes `.agentdeck/config.toml`.
- Inbox `kind=ack` controls are explicit acknowledgement commands only; `agentdeck controls` never acknowledges inbox items.
- Runtime `kind=terminal` controls are inspect-only terminal card entry points; they do not attach tmux, capture pane output, send input, or mutate state.
