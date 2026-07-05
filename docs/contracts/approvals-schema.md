# Approvals Queue Contract

`agentdeck approval list` is the read-only queue view for human approval items.

It does not approve, reject, or dispatch work. It lists stored approvals, derives GUI action metadata for each item, and validates the queue payload before printing JSON.

Use `agentdeck contract approvals` to discover this contract:

```json
{
  "schema_version": "project-view/v1",
  "approvals_command": "agentdeck approval list",
  "contract_path": "/absolute/repo/docs/contracts/approvals-schema.md",
  "contract_exists": true,
  "queue_fields": [],
  "approval_item_fields": [],
  "project_view_schema_version": "project-view/v1",
  "project_view_contract": "agentdeck contract project-view"
}
```

Use `agentdeck contract approvals --example` to include a stable GUI-ready approval queue fixture.

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
      "approve_command": "agentdeck approval approve --approval-id apv_xxx",
      "reject_command": "agentdeck approval reject --approval-id apv_xxx --reason <reason>",
      "dispatch_command": "agentdeck approval dispatch --approval-id apv_xxx",
      "can_dispatch": false,
      "dispatch_blocker": "approval is not approved"
    }
  ]
}
```

`preview_command` is the safe read-only queue view for the item. `can_dispatch=true` means the approval is approved and can be dispatched by an explicit human command. Pending or rejected approvals keep `can_dispatch=false` and expose `dispatch_blocker`.

## Boundaries

- The contract command is read-only.
- `agentdeck approval list` is read-only.
- `agentdeck approval list` must pass `validate_approval_contract()` before printing JSON.
- It must not create plans, create approvals, approve, reject, dispatch work, capture replies, ack inbox items, or send tmux input.
- GUI clients should use `preview_command`, `approve_command`, `reject_command`, `dispatch_command`, `can_dispatch`, and `dispatch_blocker` to render approval controls while preserving explicit human approval.
