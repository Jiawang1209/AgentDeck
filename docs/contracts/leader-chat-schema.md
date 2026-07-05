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
  "runtime_card_fields": [],
  "queue_card_fields": [],
  "operator_card_fields": [],
  "role_card_fields": [],
  "role_agent_fields": [],
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
  "inbox_card": null,
  "approval_card": null,
  "runtime_card": null,
  "queue_card": null,
  "operator_card": null,
  "role_card": null
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
When continue-mode recovery points at a pending inbox item, the response also includes that agent's `inbox_card`; when recovery points at approvals, the response also includes `approval_card`; when recovery points at stale runtime, the response also includes `runtime_card`. Continue-mode remains read-only: embedded cards are display/action affordances, not automatic ack, approve, dispatch, refresh, spawn, stop, or tmux input.

Runtime recovery responses include `runtime_card`, which reuses the same runtime projection as `agentdeck workbench`:

```json
{
  "backend": "tmux",
  "count": 3,
  "by_status": {"stale": 1, "configured": 2},
  "refresh_command": "agentdeck agent refresh",
  "agents": []
}
```

When `runtime_card` is present, `validate_leader_chat_contract()` checks the same runtime card field lists exposed by `agentdeck contract workbench`. Runtime-mode is read-only: it may recommend `agentdeck agent refresh`, but it must not refresh, spawn, stop, capture pane output, or send tmux input by itself.

Runtime-mode responses are returned when the human asks to inspect `runtime`, `tmux`, terminal panes, or the visible agent list. They return the same `runtime_card`, but default `next_command` to `agentdeck agent list` so a chat surface can show the visible terminal bindings before any explicit runtime operation:

```json
{
  "mode": "runtime",
  "next_command": "agentdeck agent list",
  "runtime_card": {
    "refresh_command": "agentdeck agent refresh",
    "agents": []
  }
}
```

Runtime-mode records a chat turn for history, but it must not create a plan, leader action, approval, message, job, inbox item, refresh stale panes, spawn/stop agents, capture pane output, or send tmux input.

Role-mode responses are returned when the human asks to inspect roles, role prompts, responsibilities, or assign-role commands. They return `role_card`, reusing the same role projection as `agentdeck workbench`:

```json
{
  "mode": "role",
  "next_command": "agentdeck workbench",
  "role_card": {
    "count": 3,
    "assign_command_template": "agentdeck agent assign-role --agent <agent_id> --role <role> --role-prompt <role_prompt>",
    "agents": []
  }
}
```

When `role_card` is present, `validate_leader_chat_contract()` checks the same role card and role agent field lists exposed by `agentdeck contract workbench`. Role-mode records a chat turn for history, but it must not edit `.agentdeck/config.toml`, create plans/actions/approvals/messages/jobs/inbox items, or send tmux input.

Queue-mode responses are returned when the human asks to inspect the queue, Leader actions, operator controls, or next-step buttons. They return `queue_card` and `operator_card`, reusing the same control projection as `agentdeck workbench`:

```json
{
  "mode": "queue",
  "next_command": "agentdeck leader apply-action --action-id act_xxx",
  "queue_card": {
    "active_queue_source": "leader_action",
    "next_command": "agentdeck leader apply-action --action-id act_xxx",
    "refresh_command": "agentdeck workbench"
  },
  "operator_card": {
    "source": "leader_action",
    "preview_command": "agentdeck leader action --action-id act_xxx",
    "apply_command": "agentdeck leader apply-action --action-id act_xxx",
    "controls": []
  }
}
```

When `queue_card` or `operator_card` is present, `validate_leader_chat_contract()` checks the same field lists exposed by `agentdeck contract workbench` and requires `next_command` to match the card `next_command`. Queue-mode records a chat turn for history, but it must not create or apply Leader actions, approve/reject/dispatch work, acknowledge inbox items, refresh runtime, or send tmux input.

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

Approval-mode responses include `approval_card`, which reuses the same queue shape as `agentdeck approval list`:

```json
{
  "count": 1,
  "approvals": [
    {
      "approval_id": "apv_xxx",
      "status": "pending",
      "approve_command": "agentdeck approval approve --approval-id apv_xxx",
      "reject_command": "agentdeck approval reject --approval-id apv_xxx --reason <reason>",
      "dispatch_command": "agentdeck approval dispatch --approval-id apv_xxx",
      "can_dispatch": false,
      "dispatch_blocker": "approval is not approved"
    }
  ]
}
```

When `approval_card` is present, `validate_leader_chat_contract()` reuses `validate_approval_contract()` and prefixes nested errors with `approval_card:`. Approval-mode is read-only: it may recommend `agentdeck approval list`, the first pending approval's `approve_command`, or the first approved approval's `dispatch_command`, but it must not approve, reject, dispatch work, or send tmux input.

Setup-mode responses are returned when the human asks to inspect `doctor`, provider setup, API key, or local environment readiness. They are read-only and do not call the configured Leader provider:

```json
{
  "mode": "setup",
  "next_command": "agentdeck doctor",
  "provider_health": {
    "agent_id": "leader",
    "provider": "deepseek",
    "model": "deepseek-chat",
    "approval_mode": "confirm",
    "api_backed": true,
    "supported": true,
    "ready": false,
    "missing_env": ["DEEPSEEK_API_KEY"],
    "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
    "doctor_command": "agentdeck doctor",
    "doctor_contract": "agentdeck contract doctor",
    "setup_commands": [
      "export DEEPSEEK_API_KEY=\"<your-deepseek-api-key>\"",
      "export DEEPSEEK_BASE_URL=\"https://api.deepseek.com/v1\"",
      "export DEEPSEEK_MODEL=\"deepseek-chat\""
    ]
  }
}
```

`provider_health` is a GUI-ready convenience field for setup-mode responses. It mirrors the workbench provider health card and never exposes API key values. `doctor_contract` points GUI clients to the doctor diagnostics schema, while `setup_commands` must only contain placeholder commands that a human can copy and edit outside AgentDeck. Setup-mode records a chat turn for history, but it must not create a plan, leader action, approval, message, job, inbox item, or tmux input.

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

`safety=plan_only` means the Leader only created a plan record. `safety=safe_apply` means the action can be applied through `agentdeck leader apply-action`. `safety=explicit_runtime` means the user must run the explicit command, such as dispatch, capture, inbox ack, approval approve, or approval dispatch. `safety=safe_apply_completed` means a safe apply action already completed and the response may include `result_count`. `safety=inspect` means the response is only recommending a read-only inspection command.

## Boundaries

- The contract command is read-only.
- Chat responses must not auto-dispatch runtime work.
- Chat responses must pass `validate_leader_chat_contract()` before printing JSON.
- Chat response contract failures must be auditable through ProjectView `leader_errors` and `agentdeck events`.
- Chat inbox-mode responses must reuse the `agentdeck inbox` queue contract through `inbox_card`.
- Chat approval-mode responses must reuse the `agentdeck approval list` queue contract through `approval_card`.
- Chat runtime-mode responses must reuse the workbench runtime card through `runtime_card`.
- Chat queue-mode responses must reuse the workbench queue and operator cards through `queue_card` and `operator_card`.
- Chat role-mode responses must reuse the workbench role card through `role_card`.
- Chat continue-mode responses may embed `inbox_card`, `approval_card`, or `runtime_card` when `recovery.recommended_action.source` points at those queues or runtime recovery.
- Chat setup-mode responses may include `provider_health` and must recommend `agentdeck doctor` without calling the provider.
- Runtime actions still require explicit commands or approval flow.
- GUI clients should treat `project_view` and `leader_actions` as state, `continue_card` as a recovery affordance, `inbox_card` as the mailbox queue surface, `approval_card` as the human approval queue surface, `runtime_card` as the visible tmux runtime surface, `role_card` as the role assignment surface, `queue_card` as the queue status surface, `operator_card` as the explicit control surface, setup-mode `provider_health` as provider diagnostics, and `leader_explanation` as explanation.
