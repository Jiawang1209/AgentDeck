# Leader Action Detail Contract

`agentdeck leader action --action-id <id>` is the read-only detail view for one persisted Leader action.

It does not apply the action and does not replace ProjectView. It validates ProjectView first, reads the stored action, derives applyability fields, embeds the current `recovery`, and validates the detail payload before printing JSON.

Use `agentdeck contract leader-action` to discover this contract:

```json
{
  "schema_version": "project-view/v1",
  "action_command": "agentdeck leader action --action-id <id>",
  "contract_path": "/absolute/repo/docs/contracts/leader-action-schema.md",
  "contract_exists": true,
  "action_fields": [],
  "project_view_schema_version": "project-view/v1",
  "project_view_contract": "agentdeck contract project-view"
}
```

Use `agentdeck contract leader-action --example` to include a stable GUI-ready action detail fixture.

## Detail Shape

```json
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
  "created_at": "2026-07-04T00:00:00+00:00",
  "can_apply": true,
  "apply_command": "agentdeck leader apply-action --action-id act_xxx",
  "explicit_command": "agentdeck approval create-from-plan --plan-id pln_xxx",
  "apply_blocker": null,
  "recovery": {},
  "recommended_action": {},
  "matches_recommended_action": true
}
```

`can_apply=true` is currently limited to safe `create_approvals` actions. Runtime actions such as dispatch or capture remain explicit commands and should be shown with `apply_blocker`.

`matches_recommended_action` tells a GUI or natural-language shell whether this action is the same action currently recommended by ProjectView recovery.

## Boundaries

- The contract command is read-only.
- `agentdeck leader action` is read-only.
- `agentdeck leader action` must pass ProjectView validation before printing JSON.
- `agentdeck leader action` must pass `validate_leader_action_contract()` before printing JSON.
- It must not create plans, create approvals, apply actions, dispatch work, capture replies, ack inbox items, or send tmux input.
- GUI clients should use `can_apply`, `apply_command`, `explicit_command`, and `apply_blocker` to render controls while preserving human approval and explicit runtime boundaries.
