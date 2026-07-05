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
  "runtime_agent_fields": [],
  "role_card_fields": [],
  "role_agent_fields": [],
  "ledger_card_fields": [],
  "operator_card_fields": [],
  "audit_card_fields": []
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
  "role_card": {},
  "ledger_card": {},
  "operator_card": {},
  "audit_card": {},
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
`role_card` is derived from `project_view.agents[]` role configuration.
`ledger_card` is derived from `project_view.messages`, `project_view.jobs`, `project_view.replies`, and `project_view.inbox`.
`operator_card` is derived from `recovery.recommended_action` and the active queue card. It is a renderable human-control descriptor, not an execution result.
`audit_card` is derived from `recovery.latest_event` and `recovery.recent_events`.
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

## Role Card

`role_card` is a GUI-ready projection of configured agent roles:

```json
{
  "count": 3,
  "assign_command_template": "agentdeck agent assign-role --agent <agent_id> --role <role> --role-prompt <role_prompt>",
  "agents": [
    {
      "agent_id": "planner",
      "role": "planning",
      "provider": "codex",
      "workspace_mode": "shared",
      "role_prompt": "Break down goals and prepare implementation steps.",
      "assign_command": "agentdeck agent assign-role --agent planner --role planning --role-prompt 'Break down goals and prepare implementation steps.'"
    }
  ]
}
```

The card is configuration-only. It does not dispatch work or mutate roles; GUI clients must run `assign_command` explicitly when a human changes a role.

## Ledger Card

`ledger_card` is a GUI-ready projection of the communication ledger:

```json
{
  "messages": {},
  "jobs": {},
  "replies": {},
  "inbox": {},
  "trace_commands": [
    "agentdeck trace --id msg_xxx",
    "agentdeck trace --id job_xxx",
    "agentdeck trace --id rep_xxx",
    "agentdeck trace --id inb_xxx"
  ]
}
```

`messages`, `jobs`, and `replies` reuse the ProjectView summary shapes and must retain `trace_command` on each item. `inbox` reuses the ProjectView inbox summary so GUI clients can show mailbox heads without scanning per-agent inbox arrays. `trace_commands` is a de-duplicated convenience list for quick trace navigation; the detail source remains `agentdeck trace --id <id>`.

## Operator Card

`operator_card` is the GUI/TUI-ready human action surface for the current recovery state:

```json
{
  "status": "action_required",
  "reason": "pending leader action: create_approvals",
  "label": "Apply safe Leader action",
  "command": "agentdeck leader apply-action --action-id act_xxx",
  "next_command": "agentdeck leader apply-action --action-id act_xxx",
  "safety": "safe_apply",
  "requires_explicit_user": false,
  "source": "leader_action",
  "target_id": "act_xxx",
  "preview_command": "agentdeck leader action --action-id act_xxx",
  "active_queue_source": "leader_action",
  "action_kind": "leader_action",
  "can_apply": true,
  "apply_command": "agentdeck leader apply-action --action-id act_xxx",
  "explicit_command": "agentdeck approval create-from-plan --plan-id pln_xxx",
  "blocker": null
}
```

GUI clients may render `preview_command` as the safest first click before rendering `command`, `apply_command`, or `explicit_command` as explicit action buttons. Execution still belongs to the user or a later explicit approval flow. The card must not be treated as permission to auto-dispatch, auto-ack, auto-approve, or send tmux input.

## Audit Card

`audit_card` is the GUI/TUI-ready recent audit surface:

```json
{
  "latest_event": {
    "event_id": "evt_xxx",
    "event_type": "leader_chat_turn",
    "created_at": "2026-07-04T00:00:00+00:00"
  },
  "recent_events": [],
  "event_count": 1,
  "events_command": "agentdeck events --limit 20"
}
```

The card intentionally uses compact ProjectView recovery event summaries. Use `events_command` when a GUI needs the raw JSONL timeline.

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
