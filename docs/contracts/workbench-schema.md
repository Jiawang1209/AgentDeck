# Workbench Snapshot Contract

`agentdeck workbench` is the read-only, GUI-ready snapshot for the local AgentDeck control plane.

It does not create plans, record chat turns, acknowledge inbox items, approve approvals, dispatch work, capture replies, or send tmux input. It composes already validated surfaces into one response so a terminal UI or GUI can render the current workspace without issuing several commands.

## Discovery

```bash
agentdeck contract workbench
agentdeck contract workbench --example
```

The contract command returns:

```json
{
  "schema_version": "project-view/v1",
  "workbench_command": "agentdeck workbench",
  "snapshot_fields": [],
  "runtime_card_fields": [],
  "runtime_agent_fields": []
}
```

Use `agentdeck contract workbench --example` to include a stable GUI-ready snapshot fixture.

## Snapshot

`agentdeck workbench` returns:

```json
{
  "ok": true,
  "mode": "workbench",
  "schema_version": "project-view/v1",
  "project_view": {},
  "leader_actions": {},
  "runtime_card": {},
  "recovery": {},
  "next_command": "agentdeck continue",
  "continue_card": {},
  "active_queue_source": "none",
  "inbox_card": null,
  "approval_card": null,
  "leader_action": null
}
```

`project_view` remains the source of truth and must pass `validate_project_view_contract()`.
`leader_actions` must equal `project_view.leader_actions`.
`runtime_card` is derived from `project_view.runtime_backend` and `project_view.agents[]`.
`recovery` must equal `project_view.recovery`.
`continue_card` must pass `validate_continue_contract()`.
`next_command` must equal `continue_card.next_command`.

## Runtime Card

`runtime_card` is a GUI-ready projection of visible tmux runtime bindings:

```json
{
  "backend": "tmux",
  "count": 3,
  "by_status": {"running": 1, "configured": 2},
  "agents": [
    {
      "agent_id": "planner",
      "role": "planning",
      "provider": "codex",
      "workspace_mode": "shared",
      "status": "running",
      "pane_id": "%42",
      "session_name": "agentdeck",
      "cwd": "/workspace/project",
      "spawn_command": "agentdeck agent spawn --agent planner",
      "stop_command": "agentdeck agent stop --agent planner",
      "inbox_command": "agentdeck inbox --agent planner"
    }
  ]
}
```

The card does not capture pane output and does not prove task completion. It only surfaces the configured agent identity, role, provider, workspace mode, and current runtime binding already present in ProjectView.

When `recovery.recommended_action.source` is:

- `leader_action`: `active_queue_source` is `leader_action`, and `leader_action` contains the current recovery action detail when available.
- `inbox`: `active_queue_source` is `inbox`, and `inbox_card` reuses the `agentdeck inbox --agent <id>` queue contract.
- `approval`: `active_queue_source` is `approval`, and `approval_card` reuses the `agentdeck approval list` queue contract.
- absent or unknown: `active_queue_source` is `none`.

## Invariants

- The command is read-only.
- The command must pass `validate_workbench_contract()` before printing JSON.
- GUI clients should treat this response as a single-screen projection of ProjectView, not a second state source.
- Runtime actions still require explicit commands or approval flow.
