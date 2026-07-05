# Leader Chat Contract

`agentdeck leader chat --message <text>` is the natural-language Leader entrypoint for AgentDeck.

This contract describes the chat response surface. It does not replace ProjectView. Chat responses embed `project_view`, expose selected GUI-ready convenience fields, and preserve ProjectView as the state source of truth.

Use `agentdeck contract leader-chat` to discover this contract:

```json
{
  "schema_version": "project-view/v1",
  "chat_command": "agentdeck leader chat --message <text>",
  "contract_path": "/absolute/repo/docs/contracts/leader-chat-schema.md",
  "contract_exists": true,
  "response_fields": [],
  "explanation_fields": [],
  "continue_card_fields": [],
  "project_view_schema_version": "project-view/v1",
  "project_view_contract": "agentdeck contract project-view"
}
```

Use `agentdeck contract leader-chat --example` to include a stable GUI-ready response fixture.

Live `agentdeck leader chat` responses are validated against this contract before JSON is printed. If validation fails, the command exits non-zero, writes the contract errors to stderr, records the failure in `leader_errors[]`, appends a `leader_chat_contract_failed` event, and does not print a partial chat response.

## Response Shape

The review-mode response shape is:

```json
{
  "ok": true,
  "turn_id": "cht_xxx",
  "mode": "review",
  "message": "continue",
  "project_view": {},
  "leader_actions": {},
  "leader_explanation": {},
  "plan_id": "pln_xxx",
  "review": {},
  "recovery": {},
  "next_command": "agentdeck leader apply-action --action-id act_xxx",
  "leader_action": {},
  "continue_card": null,
  "inbox_card": null
}
```

`leader_actions` is identical to `project_view.leader_actions`. It is provided so a chat surface can render the queue without issuing a second status call.

Continue-mode responses include `continue_card`, which reuses the same recovery card shape as `agentdeck continue`:

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

`agentdeck contract leader-chat --example` exposes `example_continue_card_fields` and a stable continue-mode example so GUI clients can build recovery cards without guessing fields.
When `continue_card` is present, `validate_leader_chat_contract()` reuses `validate_continue_contract()` and prefixes nested errors with `continue_card:`.

Inbox-mode responses include `inbox_card`, which reuses the same queue shape as `agentdeck inbox --agent <id>`:

```json
{
  "agent_id": "planner",
  "count": 1,
  "head_inbox_id": "inb_xxx",
  "items": [
    {
      "inbox_id": "inb_xxx",
      "trace_command": "agentdeck trace --id inb_xxx",
      "ack_command": "agentdeck ack --agent planner --inbox-id inb_xxx",
      "is_head": true,
      "can_ack": true,
      "ack_blocker": null
    }
  ]
}
```

When `inbox_card` is present, `validate_leader_chat_contract()` reuses `validate_inbox_contract()` and prefixes nested errors with `inbox_card:`. Inbox-mode is read-only: it may recommend `agentdeck inbox --agent <id>`, `agentdeck trace --id <inbox_id>`, or the head item `ack_command`, but it must not execute ack, dispatch work, capture replies, or send tmux input.

## Explanation

`leader_explanation` is a GUI-ready explanation derived from the same ProjectView, review, action, and result payloads:

```json
{
  "mode": "review",
  "summary": "Leader recommends create_approvals because plan has no approval records.",
  "reason": "plan has no approval records",
  "next_command": "agentdeck leader apply-action --action-id act_xxx",
  "recommended_action_id": "act_xxx",
  "action_kind": "create_approvals",
  "action_status": "pending",
  "safety": "safe_apply",
  "requires_explicit_user": false
}
```

`safety=plan_only` means the Leader only created a plan record. `safety=safe_apply` means the action can be applied through `agentdeck leader apply-action`. `safety=explicit_runtime` means the user must run the explicit command, such as dispatch, capture, or inbox ack. `safety=safe_apply_completed` means a safe apply action already completed and the response may include `result_count`. `safety=inspect` means the response is only recommending a read-only inspection command.

## Boundaries

- The contract command is read-only.
- Chat responses must not auto-dispatch runtime work.
- Chat responses must pass `validate_leader_chat_contract()` before printing JSON.
- Chat response contract failures must be auditable through ProjectView `leader_errors` and `agentdeck events`.
- Chat inbox-mode responses must reuse the `agentdeck inbox` queue contract through `inbox_card`.
- Runtime actions still require explicit commands or approval flow.
- GUI clients should treat `project_view` and `leader_actions` as state, `continue_card` as a recovery affordance, `inbox_card` as the mailbox queue surface, and `leader_explanation` as explanation.
