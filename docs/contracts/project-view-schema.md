# ProjectView Contract

`agentdeck status` is the canonical read-only ProjectView for CLI, natural-language Leader chat, recovery tooling, and future GUI clients.

GUI clients should consume ProjectView first. They should not scan `.agentdeck/state/state.json`, parse tmux panes, or infer workflow state from command strings when ProjectView already exposes the same fact.

## Top-Level Shape

```json
{
  "schema_version": "project-view/v1",
  "project": "repo-name",
  "root": "/absolute/project/root",
  "runtime_backend": "tmux",
  "leader": {},
  "agents": [],
  "state_path": "/absolute/project/root/.agentdeck/state/state.json",
  "plans": {},
  "approvals": {},
  "messages": {},
  "jobs": {},
  "replies": {},
  "chat_turns": {},
  "leader_errors": {},
  "leader_actions": {},
  "inbox": {},
  "recovery": {}
}
```

All ProjectView fields are read-only summaries. Commands that mutate state, send tmux input, dispatch work, or apply approvals must remain explicit commands with approval semantics.

## Discovery Command

Use `agentdeck contract project-view` to discover this contract from tools or GUI clients:

```json
{
  "schema_version": "project-view/v1",
  "status_command": "agentdeck status",
  "contract_path": "/absolute/repo/docs/contracts/project-view-schema.md",
  "contract_exists": true,
  "top_level_fields": [],
  "recovery_fields": [],
  "recommended_action_fields": []
}
```

The discovery command is read-only. It does not require a project to be initialized and does not read or mutate `.agentdeck/state`.

Use `agentdeck contract project-view --example` to include a stable GUI-ready ProjectView fixture:

```json
{
  "schema_version": "project-view/v1",
  "example": true,
  "example_project_view": {
    "schema_version": "project-view/v1",
    "recovery": {
      "status": "action_required",
      "recommended_action": {
        "label": "Apply safe Leader action",
        "safety": "safe_apply",
        "source": "leader_action",
        "target_id": "act_example"
      }
    }
  }
}
```

The example fixture is deterministic and does not represent live project state.

## Agents

`agents[]` combines static role configuration with runtime binding:

```json
{
  "agent_id": "planner",
  "role": "planner",
  "provider": "codex",
  "command": "codex",
  "workspace_mode": "shared",
  "role_prompt": "...",
  "runtime": {
    "agent_id": "planner",
    "pane_id": "%42",
    "session_name": "agentdeck",
    "cwd": "/absolute/project/root",
    "status": "running"
  }
}
```

GUI clients can render terminal status from `runtime`, but tmux panes are not the source of workflow truth. Workflow truth comes from ProjectView summaries and trace commands.

## Summary Blocks

The following blocks use a consistent summary pattern:

- `plans`: `count`, `items[]`
- `approvals`: `count`, status counts, `items[]`
- `messages`: `count`, `by_status`, `items[]`
- `jobs`: `count`, `by_status`, `items[]`
- `replies`: `count`, `items[]`
- `chat_turns`: `count`, `by_mode`, `items[]`
- `leader_errors`: `count`, `by_mode`, `items[]`
- `leader_actions`: `count`, `by_kind`, `by_status`, `items[]`

Summary items intentionally omit long prompts and pane output. Use detail commands such as `agentdeck plan show --plan-id <id>`, `agentdeck plan status --plan-id <id>`, `agentdeck trace --id <id>`, and `agentdeck events --limit <n>` when a client needs more context.

## Leader Actions

`leader_actions.items[]` exposes GUI-safe action affordance fields:

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
  "can_apply": true,
  "apply_command": "agentdeck leader apply-action --action-id act_xxx",
  "explicit_command": "agentdeck approval create-from-plan --plan-id pln_xxx",
  "apply_blocker": null,
  "created_at": "2026-07-04T00:00:00+00:00"
}
```

`can_apply=true` is currently limited to safe `create_approvals` actions. Runtime actions such as dispatch or capture stay explicit and should be shown with their blocker text.

## Chat Turns

`chat_turns.items[]` connects natural-language Leader conversation to action queue items:

```json
{
  "turn_id": "cht_xxx",
  "mode": "review",
  "message": "继续",
  "plan_id": "pln_xxx",
  "next_command": "agentdeck leader apply-action --action-id act_xxx",
  "action_id": "act_xxx",
  "action_kind": "create_approvals",
  "created_at": "2026-07-04T00:00:00+00:00"
}
```

GUI clients can use `action_id` and `action_kind` to deep-link from a chat turn to `leader_actions.items[]`.

## Inbox

`inbox` exposes mailbox state without forcing clients to scan per-agent arrays:

```json
{
  "total": 2,
  "by_agent": {"planner": 1, "coder": 1},
  "by_status": {"pending": 1, "acked": 1},
  "heads": {
    "planner": {
      "inbox_id": "inb_xxx",
      "event_type": "task_request",
      "message_id": "msg_xxx",
      "reply_id": null,
      "from_actor": "leader",
      "from_agent": null,
      "to_agent": "planner",
      "task": "Break down the task",
      "status": "pending",
      "created_at": "2026-07-04T00:00:00+00:00"
    }
  }
}
```

Only the earliest pending item is the actionable mailbox head for an agent. Use `agentdeck ack --agent <id> --inbox-id <id>` only for the head item.

## Recovery

`recovery` is the default "what should I do next?" block for GUI and Leader chat:

```json
{
  "status": "action_required",
  "reason": "pending leader action: create_approvals",
  "next_command": "agentdeck leader apply-action --action-id act_xxx",
  "recommended_action": {
    "label": "Apply safe Leader action",
    "command": "agentdeck leader apply-action --action-id act_xxx",
    "safety": "safe_apply",
    "requires_explicit_user": false,
    "source": "leader_action",
    "target_id": "act_xxx"
  },
  "pending": {
    "leader_actions": 1,
    "approvals": 0,
    "approved_approvals": 0,
    "inbox_items": 0
  },
  "leader_action": {
    "action_id": "act_xxx",
    "kind": "create_approvals",
    "command": "agentdeck approval create-from-plan --plan-id pln_xxx",
    "can_apply": true,
    "apply_command": "agentdeck leader apply-action --action-id act_xxx",
    "apply_blocker": null
  },
  "latest_event": {
    "event_id": "evt_xxx",
    "event_type": "leader_chat_turn",
    "created_at": "2026-07-04T00:00:00+00:00"
  },
  "recent_events": []
}
```

### Recovery Status Matrix

| status | recommended action | safety | target_id |
| --- | --- | --- | --- |
| `action_required` with safe Leader action | `agentdeck leader apply-action --action-id <id>` | `safe_apply` | `action_id` |
| `action_required` with runtime action | explicit command from action | `explicit_runtime` | `action_id` |
| `dispatch_ready` | `agentdeck approval dispatch --approval-id <id>` | `explicit_runtime` | `approval_id` |
| `approval_required` | `agentdeck approval list` | `inspect` | first pending `approval_id` |
| `inbox_pending` | `agentdeck status` | `inspect` | first pending `inbox_id` |
| `idle` | `null` | none | none |

`recommended_action` is descriptive metadata. It never executes by itself. GUI clients must still call explicit AgentDeck commands and preserve approval boundaries.

## Event Timeline

`agentdeck events --limit <n>` reads `.agentdeck/state/events.jsonl` and returns recent raw audit events:

```json
{
  "count": 2,
  "limit": 20,
  "events": []
}
```

ProjectView embeds compact `latest_event` and `recent_events` summaries for recovery. Use `events` when a GUI needs a timeline panel.

## Consumer Rules

- Treat ProjectView as the default state source.
- Treat `recovery.recommended_action` as the default next-step affordance.
- Treat `requires_explicit_user=true` as a hard UI gate.
- Do not dispatch, capture, stop panes, write files, or commit git changes from GUI affordances without an explicit user command.
- Do not parse tmux pane output as workflow state when ProjectView or `trace` can answer the question.
- Keep long prompt, reply, and pane text out of ProjectView summaries.
