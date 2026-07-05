# Continue Card Contract

`agentdeck continue` is the read-only recovery card entrypoint for CLI users, natural-language shells, and future GUI clients.

It does not replace ProjectView. It validates `agentdeck status` ProjectView first, projects `status.recovery` into a smaller next-step card, then validates that card before printing JSON.

Use `agentdeck contract continue` to discover this contract:

```json
{
  "schema_version": "project-view/v1",
  "continue_command": "agentdeck continue",
  "contract_path": "/absolute/repo/docs/contracts/continue-card-schema.md",
  "contract_exists": true,
  "continue_card_fields": [],
  "project_view_schema_version": "project-view/v1",
  "project_view_contract": "agentdeck contract project-view"
}
```

Use `agentdeck contract continue --example` to include a stable GUI-ready continue card fixture.

## Card Shape

```json
{
  "ok": true,
  "mode": "continue",
  "project_view_schema_version": "project-view/v1",
  "project_view_command": "agentdeck status",
  "status": "action_required",
  "reason": "pending leader action: create_approvals",
  "next_command": "agentdeck leader apply-action --action-id act_xxx",
  "recommended_action": {},
  "pending": {},
  "leader_action": {},
  "action_detail_command": "agentdeck leader action --action-id act_xxx"
}
```

`pending` comes from `ProjectView.recovery`. `recommended_action` usually mirrors `ProjectView.recovery.recommended_action`; when recovery reports more than one approved approval, `agentdeck continue` promotes the card-level `next_command` and `recommended_action.command` to the explicit batch entrypoint `agentdeck approval dispatch-ready --confirm`. This keeps the recovery card aligned with workbench/operator batch controls while preserving ProjectView as the state source. `leader_action` is populated only when the recommended action source is `leader_action`; otherwise it is `null`.

## Boundaries

- The contract command is read-only.
- `agentdeck continue` is read-only.
- `agentdeck continue` must pass ProjectView validation before printing JSON.
- `agentdeck continue` must pass `validate_continue_contract()` before printing JSON.
- It must not create plans, create leader actions, apply actions, dispatch work, capture replies, ack inbox items, or send tmux input.
- GUI clients should use `next_command` and `recommended_action.safety` to render an affordance, not to auto-run runtime work.
- If `next_command` is `agentdeck approval dispatch-ready --confirm`, it is still an explicit human command and must not be run automatically.
