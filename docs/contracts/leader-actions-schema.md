# Leader Actions Queue Contract

`agentdeck leader actions` is the read-only queue view for persisted Leader action suggestions.

It does not execute actions and does not replace ProjectView. It validates ProjectView first, lists stored Leader actions, derives applyability fields for each item, marks the current recovery recommendation, and validates the queue payload before printing JSON.

Use `agentdeck contract leader-actions` to discover this contract:

```json
{
  "schema_version": "project-view/v1",
  "actions_command": "agentdeck leader actions",
  "contract_path": "/absolute/repo/docs/contracts/leader-actions-schema.md",
  "contract_exists": true,
  "list_fields": [],
  "action_item_fields": [],
  "project_view_schema_version": "project-view/v1",
  "project_view_contract": "agentdeck contract project-view"
}
```

Use `agentdeck contract leader-actions --example` to include a stable GUI-ready action queue fixture.

## Queue Shape

```json
{
  "count": 1,
  "recommended_action_id": "act_xxx",
  "actions": [
    {
      "action_id": "act_xxx",
      "kind": "create_approvals",
      "status": "pending",
      "requires_confirmation": true,
      "plan_id": "pln_xxx",
      "approval_id": null,
      "agent_id": null,
      "message_id": null,
      "command": "agentdeck approval create-from-plan --plan-id pln_xxx",
      "reason": "plan has no approval records",
      "preview_command": "agentdeck leader action --action-id act_xxx",
      "controls": [
        {
          "kind": "preview",
          "label": "Preview Leader action",
          "command": "agentdeck leader action --action-id act_xxx",
          "safety": "inspect",
          "enabled": true,
          "blocker": null
        }
      ],
      "can_apply": true,
      "apply_command": "agentdeck leader apply-action --action-id act_xxx",
      "explicit_command": "agentdeck approval create-from-plan --plan-id pln_xxx",
      "apply_blocker": null,
      "is_recommended": true,
      "created_at": "2026-07-04T00:00:00+00:00"
    }
  ]
}
```

`action_item_fields` matches ProjectView `leader_actions.items[]`, so GUI clients can render the same action queue from either `agentdeck status` or `agentdeck leader actions`.

`preview_command` is the safe read-only action detail view. `controls[]` is the GUI-ready button list; each control has `kind`, `label`, `command`, `safety`, `enabled`, and `blocker`. `can_apply=true` is currently limited to safe `create_approvals` actions. Runtime actions such as dispatch or capture stay explicit and should be shown with `apply_blocker`.

## Boundaries

- The contract command is read-only.
- `agentdeck leader actions` is read-only.
- `agentdeck leader actions` must pass ProjectView validation before printing JSON.
- `agentdeck leader actions` must pass `validate_leader_actions_contract()` before printing JSON.
- It must not create plans, create approvals, apply actions, dispatch work, capture replies, ack inbox items, or send tmux input.
- GUI clients should prefer `controls[]`, while retaining `recommended_action_id`, `is_recommended`, `preview_command`, `can_apply`, `apply_command`, `explicit_command`, and `apply_blocker` for compatibility.
