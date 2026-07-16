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
- `coordination_roles`: the ProjectView `frontdesk`, `planner`, and `orchestrator` logical roles. These are not worker panes; each item keeps `runtime_kind=logical_role`, `pane_backed=false`, `pane_id=null`, and `dispatch_ready=false`.
- `latest_plan`: the latest ProjectView plan item, or `null`.
- `queues`: compact pending queue counts for the Leader operator.
- `recovery`: ProjectView recovery object.
- `next_command`: `recovery.next_command`.
- `controls`: GUI-ready controls. The first item is `kind=refresh`, uses `safety=inspect`, and its `command` matches `refresh_command`; additional items expose project status, workbench, provider setup, and explicit next-step controls.

## Queue Fields

- `leader_actions_pending`
- `approvals_pending`
- `approvals_approved`
- `leader_inbox_pending`
- `leader_errors`

## Safety

All controls are projections only. `agentdeck leader status` may recommend `agentdeck doctor`, `agentdeck continue`, `agentdeck status`, or `agentdeck workbench`, but it never executes those commands.

`coordination_roles` is also a projection. It lets GUI/TUI clients show the layered Leader topology from a narrow status card, but it does not grant dispatch permission, start tmux panes, or split the Leader into separate runtime agents.

Use `agentdeck contract leader-status --example` for a stable example payload.

## Contract Discovery

`agentdeck contract leader-status` preserves
`leader_generation_fields` as the ordinary nine-field projection for existing
clients and additionally exposes `semantic_leader_generation_fields` as the
strict eleven-field semantic projection. `latest_plan`, when present, follows
the same ProjectView generation-shape rules: native ordinary provenance uses
`leader-plan/v1`, native semantic provenance uses
`leader-semantic-plan/v1`, and non-native schema fields are null. The semantic
generation authority hash identifies proposal-stripped required/input
authority; it must not be compared directly with the latest plan's compact
full-output semantic authority hash when legal Leader proposals exist.
