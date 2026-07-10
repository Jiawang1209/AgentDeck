# Controls Contract

Mission controls use `scope=mission`. Leader responses use `card=mission_preview_card`, `mission_status_card`, or `mission_run_card`; the workbench uses `card=mission_card`. Deterministic control ids preserve the Mission confirm/resume/status/attach/workbench affordances, while `selection.next_command` remains `null` for disabled confirmation or resume controls and never acts as authorization.

The controls discovery example includes the five-item workbench Mission group. `--scope mission`, `--card mission_card`, `--query`, `--control-id`, and `--enabled-only` are read-only projections over those same items; filtering never executes the selected command or changes Mission state.

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
agentdeck controls --control-id leader:leader_card:chat:leader:6fd87159ea
```

Top-level discovery fields:

- `schema_version`
- `controls_command`
- `contract_path`
- `contract_exists`
- `control_registry_card_fields`
- `control_registry_item_fields`
- `control_registry_group_fields`
- `control_registry_selection_fields`
- `control_registry_filter_fields`
- `workbench_contract`
- `leader_chat_contract`

`control_registry_card_fields`:

- `mode`
- `title`
- `source_command`
- `default_command`
- `filters`
- `selection`
- `item_count`
- `items`
- `group_count`
- `groups`

`control_registry_filter_fields`:

- `scope`
- `card`
- `query`
- `control_id`
- `enabled_only`
- `active_filter_keys`
- `item_count_before_filter`

`control_registry_selection_fields`:

- `requested_control_id`
- `matched`
- `matched_count`
- `selected_control`
- `blocker`
- `next_command`

`control_registry_group_fields`:

- `group_id`
- `scope`
- `card`
- `label`
- `item_count`
- `enabled_count`
- `disabled_count`
- `items`

`source_command` stays `agentdeck workbench` because the registry is derived from the workbench snapshot. `default_command` is `agentdeck controls`, the dedicated command palette entrypoint GUI/TUI clients can refresh directly. `filters` records the read-only projection requested by `--scope`, `--card`, `--query`, `--control-id`, and `--enabled-only`; `active_filter_keys` lists the active filter names in stable order (`scope`, `card`, `query`, `control_id`, `enabled_only`) so clients can render filter chips without inferring them from raw values; `item_count_before_filter` records the unfiltered source registry size so clients can show a scoped view without losing the full-palette context. When no filters are active, `item_count_before_filter` must match `item_count`; only filtered projections may show a larger before-filter count.

Each `items[]` entry uses the workbench `control_registry_item_fields`. Every item includes a deterministic `control_id` derived from its scope, card, kind, agent identity, label, and command; GUI/TUI clients may use it as a stable render key or audit correlation key, but it is not an authorization token and does not override `enabled`, `safety`, or `blocker`. Filtering is applied before `item_count`, `items[]`, `group_count`, and `groups[]` are derived, so filtered groups always match the filtered flat item list. `selection` is derived from `filters.control_id`: it records the requested id, whether exactly one item matched, the match count, the selected item for GUI detail panes, a selection-level blocker, and an optional `next_command`. If no control id is requested, `selection.selected_control` is `null`, `matched=false`, `blocker=null`, and `next_command=null`. If a requested control id does not exist in the source registry, `selection.selected_control` is `null`, `matched=false`, `blocker="control_id not found"`, and `next_command=null`. If the id exists in the source registry but the current scope/card/query/enabled-only filters exclude it, `selection.selected_control` is `null`, `matched=false`, `blocker="control_id filtered out"`, and `next_command=null`. A matched selection must keep `blocker=null`; `next_command` equals the selected item command only when that item is `enabled=true`, and remains `null` for disabled selections so clients keep rendering the selected item's own blocker. `selection` is a projection over `items[]`, not a second source of control state. `groups[]` is a derived view over the same filtered items, grouped by `scope` and `card`, with a stable `group_id`, GUI label, item count, enabled count, disabled count, and the matching item list. Clients may render `groups[]` as command palette sections or ignore it and scan `items[]`; either way, `items[]` remains the flat source projection. The registry includes Leader controls, provider switch/setup controls, concrete control-mode policy controls, terminal session controls, role assignment controls, runtime controls, inbox controls, Leader inbox controls, communication ledger controls, artifact index controls, audit timeline controls, and operator controls from the same workbench snapshot. Leader items are derived from `leader_card.controls[]`: GUI clients should preserve `kind=refresh` and `kind=leader_status` for `agentdeck leader status`; `refresh` is the narrow status-card refresh action, `leader_status` is the narrow status view entry, and `kind=status` remains reserved for full ProjectView status instead of inferring meaning from labels. Provider items are derived from `provider_health.controls[]`: GUI clients should render the concrete `agentdeck leader set-provider --provider <provider> --model <model>` items directly, keep the current provider disabled with `already current provider`, and treat every enabled provider switch as `safety=explicit_user`. Provider `kind=set_provider` is the normal explicit switch; `kind=guarded_set_provider` appends `--require-ready` so the explicit command refuses to write config when the target backend is not ready; `kind=setup_provider` exposes allowlisted setup commands such as placeholder API exports, `codex login`, and `claude auth`. `validate_control_registry_card_contract()` rejects provider switch items that do not use `safety=explicit_user`, do not point at `agentdeck leader set-provider --provider ...`, omit `--require-ready` for `guarded_set_provider`, use a `setup_provider` command outside the provider setup command allowlist, or are disabled without a blocker. Policy items are derived from `control_mode_card.active_controls[]`: GUI clients should render the concrete `agentdeck policy set-mode --mode ask|approve|autonomous` items directly, keep the current mode disabled, and keep the blocked autonomous item visible but unavailable. `validate_control_registry_card_contract()` rejects policy `kind=set_mode` items that do not point at `agentdeck policy set-mode --mode ...`, enabled policy set-mode items that do not use `safety=explicit_user`, or disabled policy set-mode items without a blocker. Autonomous items are derived from `control_mode_card.autonomous_actions[]` under `scope=autonomous`, `card=control_mode_card`: `kind=approval_auto` (`agentdeck approval auto --confirm`, `safety=delegated`) is enabled only when the project is in autonomous mode, otherwise disabled with `autonomous mode is not enabled`; `kind=run_loop` (`agentdeck run-loop --plan-id <id> --confirm`, `safety=delegated`) is always a disabled template blocked by `requires --plan-id`. `agentdeck controls --scope autonomous` filters to exactly these two, and `--enabled-only` in autonomous mode returns just `approval_auto`. Rendering an enabled autonomous control is inspect-only surfacing, not execution authorization — both commands still require the human to run them explicitly with `--confirm`. Terminal session items are derived from both `terminal_session_card.controls[]` and `terminal_session_card.terminals[].controls[]`: GUI clients should render `kind=attach_session` as the project tmux attach action, `kind=open_controls` as the full command palette jump, `kind=refresh_runtime` as an explicit runtime refresh, and `kind=select_pane` with `agent_id` as the single-agent pane focus action. `validate_control_registry_card_contract()` rejects terminal session attach items that do not use a `tmux ...` command, open-controls items that do not point at `agentdeck controls`, refresh-runtime items that do not point at `agentdeck agent refresh` or use `safety=explicit_runtime`, select-pane items that do not use `safety=inspect`, enabled select-pane items that do not point at a tmux `select-pane -t` command, disabled select-pane items that keep a command, and disabled terminal session controls without a blocker. Role items are derived from `role_card.agents[].controls[]`: GUI clients should render `kind=assign_role` as an explicit configuration form, keep the template disabled until concrete `role` and `role_prompt` values are supplied, and never infer role edits from labels. `validate_control_registry_card_contract()` rejects role `kind=assign_role` items that do not point at `agentdeck agent assign-role --agent ...` or are disabled without a blocker. Inbox items are derived from visible `inbox_card.items[].controls[]` and fixed `leader_inbox_card.items[].controls[]`: GUI clients should render `kind=preview` as lineage inspection and `kind=ack` as an explicit acknowledgement command, preserving `card=inbox_card` for active recovery mailbox items and `card=leader_inbox_card` for worker replies flowing back to Leader. `validate_control_registry_card_contract()` rejects inbox `kind=preview` items that do not point at `agentdeck trace --id ...` and inbox `kind=ack` items that do not point at `agentdeck ack --agent ...`. Communication ledger items are derived from `ledger_card.controls[]`: GUI clients should preserve `scope=ledger`, `card=ledger_card`, `kind=inspect`, `command=agentdeck workbench`, and `safety=inspect` so the command palette can open or refresh the ledger card without parsing labels. `validate_control_registry_card_contract()` rejects ledger inspect items that do not point at `agentdeck workbench` or do not use `safety=inspect`. Artifact index items are derived from `artifacts_card.controls[]`: GUI clients should preserve `scope=artifacts`, `card=artifacts_card`, `kind=inspect`, `command=agentdeck artifacts`, and `safety=inspect` so the command palette can open or refresh the worker-output index without parsing labels. `validate_control_registry_card_contract()` rejects artifacts inspect items that do not point at `agentdeck artifacts` or do not use `safety=inspect`. Audit timeline items are derived from `audit_card.controls[]`: GUI clients should preserve `scope=audit`, `card=audit_card`, `kind=inspect`, `command=agentdeck events --limit 20`, and `safety=inspect` so the command palette can open recent audit events without parsing labels. `validate_control_registry_card_contract()` rejects audit inspect items that do not point at `agentdeck events --limit 20` or do not use `safety=inspect`. Runtime terminal items preserve `kind=terminal` for `agentdeck agent terminal --agent <id>` so clients can render an "Open terminal" action without parsing labels or command strings. Dispatch preview items preserve `scope=dispatch_preview` for approval dispatch confirmation controls: inspect items must use `agentdeck approval list` with `safety=inspect`, dispatch items must use `agentdeck approval dispatch --approval-id ...` with `safety=explicit_runtime`, and disabled dispatch items must carry their runtime blocker. Batch dispatch preview items preserve `scope=dispatch_batch_preview` for the top-level `dispatch_ready` control: it must point at `agentdeck approval dispatch-ready --confirm` with `safety=explicit_runtime`, while inspect still points at `agentdeck approval list`. When the operator action is batch approval dispatch, the registry preserves `kind=dispatch_ready` for the `agentdeck approval dispatch-ready --confirm` item so clients can identify it without parsing labels or command strings.

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
- `selection` is derived from `filters.control_id` and `items[]`; `selection.blocker` and `selection.next_command` only explain or project the selected item, and `agentdeck controls` never uses them to execute or authorize commands.
- `groups[]` is derived from `items[]`; `agentdeck controls` never uses it to execute or authorize commands.
- Filter and query arguments only narrow the read-only projection; `active_filter_keys` is derived from those arguments and must match them; neither filters nor active filter keys authorize, execute, or change any control command.
- Provider `kind=set_provider` controls are explicit configuration commands only; `agentdeck controls` never runs them and never calls the selected provider.
- Role `kind=assign_role` controls are explicit configuration templates only; `agentdeck controls` never fills placeholders or writes `.agentdeck/config.toml`.
- Inbox `kind=ack` controls are explicit acknowledgement commands only; `agentdeck controls` never acknowledges inbox items.
- Agent-ready `kind=spawn_ready`, `kind=refresh_runtime`, and `kind=dispatch_ready` controls are explicit runtime or approval commands only; `agentdeck controls` never spawns panes, refreshes bindings, or dispatches approvals.
- Terminal session `kind=select_pane` controls are inspect-only pane focus commands; `agentdeck controls` never selects panes or attaches tmux by itself.
- Runtime `kind=terminal` controls are inspect-only terminal card entry points; they do not attach tmux, capture pane output, send input, or mutate state.
- Artifacts `kind=inspect` controls are inspect-only artifact index entry points; they do not read artifact file contents, call providers, inspect panes, or mutate state.
- Audit `kind=inspect` controls are inspect-only event timeline entry points; they do not read panes, call providers, mutate state, or execute audit commands.
