# Approvals Queue Contract

`agentdeck approval list` is the read-only queue view for human approval items.

It does not approve, reject, or dispatch work. It lists stored approvals, derives GUI action metadata for each item, and validates the queue payload before printing JSON.

Use `agentdeck contract approvals` to discover this contract:

```json
{
  "schema_version": "project-view/v1",
  "approvals_command": "agentdeck approval list",
  "dispatch_ready_command": "agentdeck approval dispatch-ready --confirm",
  "contract_path": "/absolute/repo/docs/contracts/approvals-schema.md",
  "contract_exists": true,
  "queue_fields": [],
  "approval_item_fields": [],
  "dispatch_ready_response_fields": [],
  "dispatch_ready_result_fields": [],
  "project_view_schema_version": "project-view/v1",
  "project_view_contract": "agentdeck contract project-view"
}
```

Use `agentdeck contract approvals --example` to include stable GUI-ready approval queue and dispatch-ready fixtures.

## Queue Shape

```json
{
  "count": 2,
  "approvals": [
    {
      "approval_id": "apv_xxx",
      "plan_id": "pln_xxx",
      "step": 1,
      "agent_id": "planner",
      "role": "planning",
      "task": "Prepare an implementation plan",
      "risk": "low",
      "status": "pending",
      "created_at": "2026-07-04T00:00:00+00:00",
      "reason": null,
      "preview_command": "agentdeck approval list",
      "controls": [
        {
          "kind": "preview",
          "label": "Preview approval queue",
          "command": "agentdeck approval list",
          "safety": "inspect",
          "enabled": true,
          "blocker": null
        }
      ],
      "approve_command": "agentdeck approval approve --approval-id apv_xxx",
      "reject_command": "agentdeck approval reject --approval-id apv_xxx --reason <reason>",
      "dispatch_command": "agentdeck approval dispatch --approval-id apv_xxx",
      "can_dispatch": false,
      "dispatch_blocker": "approval is not approved"
    }
  ]
}
```

`preview_command` is the safe read-only queue view for the item. `controls[]` is the GUI-ready button list; each control has `kind`, `label`, `command`, `safety`, `enabled`, and `blocker`. `can_dispatch=true` means the approval is approved and can be dispatched by an explicit human command. Pending or rejected approvals keep `can_dispatch=false` and expose `dispatch_blocker`.

`agentdeck approval dispatch --approval-id <id>` is an explicit runtime command, not part of the read-only queue contract. On success it returns `trace_command` for the created message lineage and embeds the target agent's `inbox_card`, reusing the `agentdeck inbox --agent <id>` queue shape so GUI clients can show the worker mailbox head without reading state directly.

`agentdeck approval dispatch-ready --confirm` is also an explicit runtime command. It batch-dispatches only approved approvals whose target agent has a ready runtime binding, reusing the same single-dispatch lineage path for each dispatched item. Blocked approvals stay approved and are returned as `results[]` items with `status=blocked`, `blocker`, and `dispatch_command`. Without `--confirm`, it must fail without mutating state or sending tmux input.

## Dispatch-Ready Shape

`dispatch_ready_response_fields` and `dispatch_ready_result_fields` let GUI clients discover the batch-dispatch response without parsing CLI help:

```json
{
  "ok": true,
  "mode": "dispatch_ready",
  "requires_explicit_user": true,
  "safety": "explicit_runtime",
  "dispatched_count": 1,
  "blocked_count": 1,
  "skipped_count": 1,
  "results": [
    {
      "approval_id": "apv_ready",
      "status": "dispatched",
      "agent_id": "planner",
      "pane_id": "%42",
      "message_id": "msg_ready",
      "trace_command": "agentdeck trace --id msg_ready",
      "blocker": null,
      "dispatch_command": "agentdeck approval dispatch --approval-id apv_ready"
    },
    {
      "approval_id": "apv_blocked",
      "status": "blocked",
      "agent_id": "coder",
      "pane_id": null,
      "message_id": null,
      "trace_command": null,
      "blocker": "agent is not spawned: coder",
      "dispatch_command": "agentdeck approval dispatch --approval-id apv_blocked"
    }
  ]
}
```

Every `results[]` item uses the same result field set. Dispatched items must include `message_id` and `trace_command`, while blocked items must include `blocker`. Before printing, `agentdeck approval dispatch-ready --confirm` validates the response with `validate_approval_dispatch_ready_contract()`.

## Boundaries

- The contract command is read-only.
- `agentdeck approval list` is read-only.
- `agentdeck approval list` must pass `validate_approval_contract()` before printing JSON.
- `agentdeck approval dispatch-ready --confirm` must pass `validate_approval_dispatch_ready_contract()` before printing JSON.
- The read-only queue path must not create plans, create approvals, approve, reject, dispatch work, capture replies, ack inbox items, or send tmux input.
- The dispatch-ready path may dispatch approved runtime-ready items only after `--confirm`; it must not dispatch blocked items, ack inbox items, capture replies, or bypass the single-dispatch lineage path.
- GUI clients should prefer `controls[]`, while retaining `preview_command`, `approve_command`, `reject_command`, `dispatch_command`, `can_dispatch`, and `dispatch_blocker` for compatibility.
