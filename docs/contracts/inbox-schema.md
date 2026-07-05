# Inbox Queue Contract

`agentdeck inbox --agent <id>` is the read-only mailbox view for one AgentDeck mailbox owner.

It does not acknowledge messages and does not replace ProjectView or trace. It lists one mailbox owner's inbox items, derives head-only ack metadata, links each item to trace, and validates the queue payload before printing JSON.

`<id>` may be a configured worker agent id, such as `planner`, or the logical Leader mailbox owner `leader`. The logical Leader mailbox is for worker replies flowing back to the Leader; it is not a tmux/runtime agent and must not be treated as valid for spawn, send, capture, stop, or terminal commands.

Use `agentdeck contract inbox` to discover this contract:

```json
{
  "schema_version": "project-view/v1",
  "inbox_command": "agentdeck inbox --agent <id>",
  "contract_path": "/absolute/repo/docs/contracts/inbox-schema.md",
  "contract_exists": true,
  "queue_fields": [],
  "inbox_item_fields": [],
  "project_view_schema_version": "project-view/v1",
  "project_view_contract": "agentdeck contract project-view",
  "trace_contract": "agentdeck contract trace"
}
```

Use `agentdeck contract inbox --example` to include a stable GUI-ready inbox fixture.

## Queue Shape

```json
{
  "agent_id": "planner",
  "count": 1,
  "head_inbox_id": "inb_xxx",
  "items": [
    {
      "inbox_id": "inb_xxx",
      "event_type": "task_request",
      "message_id": "msg_xxx",
      "attempt_id": "att_xxx",
      "job_id": "job_xxx",
      "reply_id": null,
      "from_actor": "leader",
      "from_agent": null,
      "to_agent": "planner",
      "task": "Review the implementation plan",
      "status": "pending",
      "created_at": "2026-07-04T00:00:00+00:00",
      "preview_command": "agentdeck trace --id inb_xxx",
      "controls": [
        {
          "kind": "preview",
          "label": "Trace inbox item",
          "command": "agentdeck trace --id inb_xxx",
          "safety": "inspect",
          "enabled": true,
          "blocker": null
        }
      ],
      "trace_command": "agentdeck trace --id inb_xxx",
      "ack_command": "agentdeck ack --agent planner --inbox-id inb_xxx",
      "is_head": true,
      "can_ack": true,
      "ack_blocker": null
    }
  ]
}
```

`preview_command` is the safe read-only lineage view for the item. `controls[]` is the GUI-ready button list; each control has `kind`, `label`, `command`, `safety`, `enabled`, and `blocker`. Only the earliest pending item is the actionable inbox head. Non-head items keep `can_ack=false` and include `ack_blocker`.

## Boundaries

- The contract command is read-only.
- `agentdeck inbox --agent <id>` is read-only.
- `<id>` may be a configured worker agent id or the logical Leader mailbox owner `leader`.
- `agentdeck inbox --agent <id>` must pass `validate_inbox_contract()` before printing JSON.
- It must not create plans, create approvals, apply actions, dispatch work, capture replies, ack inbox items, or send tmux input.
- GUI clients should prefer `controls[]`, while retaining `head_inbox_id`, `is_head`, `can_ack`, `preview_command`, `ack_command`, `ack_blocker`, and `trace_command` for compatibility.
