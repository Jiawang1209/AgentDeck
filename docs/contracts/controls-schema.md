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
agentdeck controls --scope runtime --enabled-only
agentdeck controls --card terminal_session_card
agentdeck controls --query terminal
```

Top-level discovery fields:

- `schema_version`
- `controls_command`
- `contract_path`
- `contract_exists`
- `control_registry_card_fields`
- `control_registry_item_fields`
- `control_registry_group_fields`
- `control_registry_filter_fields`
- `workbench_contract`
- `leader_chat_contract`

`control_registry_card_fields`:

- `mode`
- `title`
- `source_command`
- `default_command`
- `filters`
- `item_count`
- `items`
- `group_count`
- `groups`

`control_registry_filter_fields`:

- `scope`
- `card`
- `query`
- `enabled_only`
- `item_count_before_filter`

`control_registry_group_fields`:

- `group_id`
- `scope`
- `card`
- `label`
- `item_count`
- `enabled_count`
- `disabled_count`
- `items`

`source_command` stays `agentdeck workbench` because the registry is derived from the workbench snapshot. `default_command` is `agentdeck controls`, the dedicated command palette entrypoint GUI/TUI clients can refresh directly. `filters` records the read-only projection requested by `--scope`, `--card`, `--query`, and `--enabled-only`; `item_count_before_filter` records the unfiltered source registry size so clients can show a scoped view without losing the full-palette context.

Each `items[]` entry uses the workbench `control_registry_item_fields`. Every item includes a deterministic `control_id` derived from its scope, card, kind, agent identity, label, and command; GUI/TUI clients may use it as a stable render key or audit correlation key, but it is not an authorization token and does not override `enabled`, `safety`, or `blocker`. Filtering is applied before `item_count`, `items[]`, `group_count`, and `groups[]` are derived, so filtered groups always match the filtered flat item list. `groups[]` is a derived view over the same filtered items, grouped by `scope` and `card`, with a stable `group_id`, GUI label, item count, enabled count, disabled count, and the matching item list. Clients may render `groups[]` as command palette sections or ignore it and scan `items[]`; either way, `items[]` remains the flat source projection. The registry includes Leader controls, provider switch controls, concrete control-mode policy controls, terminal session controls, role assignment controls, runtime controls, inbox controls, Leader inbox controls, and operator controls from the same workbench snapshot. Provider items are derived from `provider_health.controls[]`: GUI clients should render the concrete `agentdeck leader set-provider --provider <provider> --model <model>` items directly, keep the current provider disabled with `already current provider`, and treat every enabled provider switch as `safety=explicit_user`. Provider `kind=set_provider` is the normal explicit switch; `kind=guarded_set_provider` appends `--require-ready` so the explicit command refuses to write config when the target backend is not ready. `validate_control_registry_card_contract()` rejects provider switch items that do not use `safety=explicit_user`, do not point at `agentdeck leader set-provider --provider ...`, omit `--require-ready` for `guarded_set_provider`, or are disabled without a blocker. Policy items are derived from `control_mode_card.active_controls[]`: GUI clients should render the concrete `agentdeck policy set-mode --mode ask|approve|autonomous` items directly, keep the current mode disabled, and keep the blocked autonomous item visible but unavailable. `validate_control_registry_card_contract()` rejects policy `kind=set_mode` items that do not point at `agentdeck policy set-mode --mode ...`, enabled policy set-mode items that do not use `safety=explicit_user`, or disabled policy set-mode items without a blocker. Terminal session items are derived from `terminal_session_card.controls[]`: GUI clients should render `kind=attach_session` as the project tmux attach action, `kind=open_controls` as the full command palette jump, and `kind=refresh_runtime` as an explicit runtime refresh. `validate_control_registry_card_contract()` rejects terminal session attach items that do not use a `tmux ...` command, open-controls items that do not point at `agentdeck controls`, refresh-runtime items that do not point at `agentdeck agent refresh` or use `safety=explicit_runtime`, and disabled terminal session controls without a blocker. Role items are derived from `role_card.agents[].controls[]`: GUI clients should render `kind=assign_role` as an explicit configuration form, keep the template disabled until concrete `role` and `role_prompt` values are supplied, and never infer role edits from labels. `validate_control_registry_card_contract()` rejects role `kind=assign_role` items that do not point at `agentdeck agent assign-role --agent ...` or are disabled without a blocker. Inbox items are derived from visible `inbox_card.items[].controls[]` and fixed `leader_inbox_card.items[].controls[]`: GUI clients should render `kind=preview` as lineage inspection and `kind=ack` as an explicit acknowledgement command, preserving `card=inbox_card` for active recovery mailbox items and `card=leader_inbox_card` for worker replies flowing back to Leader. `validate_control_registry_card_contract()` rejects inbox `kind=preview` items that do not point at `agentdeck trace --id ...` and inbox `kind=ack` items that do not point at `agentdeck ack --agent ...`. Runtime terminal items preserve `kind=terminal` for `agentdeck agent terminal --agent <id>` so clients can render an "Open terminal" action without parsing labels or command strings. When the operator action is batch approval dispatch, the registry preserves `kind=dispatch_ready` for the `agentdeck approval dispatch-ready --confirm` item so clients can identify it without parsing labels or command strings.

- `scope`
- `card`
- `kind`
- `label`
- `command`
- `safety`
- `enabled`
- `blocker`
- `agent_id`
- `control_id`

Safety rules:

- The command is read-only and does not write AgentDeck state.
- It does not create chat turns, plans, approvals, messages, jobs, replies, or inbox items.
- It does not call a provider, read tmux pane output, send tmux input, or execute any control.
- It is a projection of workbench `control_registry[]`, not a second source of control state.
- `groups[]` is derived from `items[]`; `agentdeck controls` never uses it to execute or authorize commands.
- Filter and query arguments only narrow the read-only projection; they never authorize, execute, or change any control command.
- Provider `kind=set_provider` controls are explicit configuration commands only; `agentdeck controls` never runs them and never calls the selected provider.
- Role `kind=assign_role` controls are explicit configuration templates only; `agentdeck controls` never fills placeholders or writes `.agentdeck/config.toml`.
- Inbox `kind=ack` controls are explicit acknowledgement commands only; `agentdeck controls` never acknowledges inbox items.
- Runtime `kind=terminal` controls are inspect-only terminal card entry points; they do not attach tmux, capture pane output, send input, or mutate state.
