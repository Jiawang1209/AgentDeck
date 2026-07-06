# Leader Status Contract

`agentdeck leader status` returns a read-only, GUI-ready Leader status snapshot. It is a narrow view derived from the validated ProjectView plus the same provider health projection used by `agentdeck workbench`.

## Command

```bash
conda activate agentdeck
agentdeck leader status
```

The command does not call the configured Leader provider, read tmux pane output, send tmux input, create plans, create approvals, acknowledge inbox items, or write state.

## Response Fields

- `ok`: always `true` when the response is emitted.
- `mode`: always `leader_status`.
- `schema_version`: ProjectView schema version.
- `source_command`: command that produced this card, exactly `agentdeck leader status`.
- `refresh_command`: command GUI/TUI clients can run to refresh this card, exactly `agentdeck leader status`.
- `project_view_command`: read-only source command, `agentdeck status`.
- `workbench_command`: read-only full workbench command, `agentdeck workbench`.
- `leader`: the logical Leader identity from ProjectView, including normalized `leader_backend`.
- `provider_health`: workbench-compatible provider readiness and setup card for the configured Leader provider.
- `latest_plan`: the latest ProjectView plan item, or `null`.
- `queues`: compact pending queue counts for the Leader operator.
- `recovery`: ProjectView recovery object.
- `next_command`: `recovery.next_command`.
- `controls`: inspect and explicit next-step controls.

## Queue Fields

- `leader_actions_pending`
- `approvals_pending`
- `approvals_approved`
- `leader_inbox_pending`
- `leader_errors`

## Safety

All controls are projections only. `agentdeck leader status` may recommend `agentdeck doctor`, `agentdeck continue`, `agentdeck status`, or `agentdeck workbench`, but it never executes those commands.

Use `agentdeck contract leader-status --example` for a stable example payload.
