# Workbench Snapshot Contract

`agentdeck workbench` is the read-only, GUI-ready snapshot for the local AgentDeck control plane.

It does not create plans, record chat turns, acknowledge inbox items, approve approvals, dispatch work, capture replies, or send tmux input. It composes already validated surfaces into one response so a terminal UI or GUI can render the current workspace without issuing several commands.

For GUI/TUI clients that need a live local state feed, `agentdeck workbench --watch --interval <seconds>` emits the same validated snapshot shape as newline-delimited JSON. Each line is a complete workbench snapshot. Use `--iterations <n>` for bounded scripts, tests, and smoke checks. Use `--since-event <event_id>` when a client wants a lightweight audit-event cursor summary on each snapshot.

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
  "leader_card_fields": [],
  "provider_health_fields": [],
  "runtime_card_fields": [],
  "runtime_agent_fields": [],
  "runtime_control_fields": [],
  "role_card_fields": [],
  "role_agent_fields": [],
  "ledger_card_fields": [],
  "queue_card_fields": [],
  "operator_card_fields": [],
  "audit_card_fields": [],
  "contracts_card_fields": [],
  "change_summary_fields": []
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
  "leader_card": {},
  "provider_health": {},
  "runtime_card": {},
  "role_card": {},
  "ledger_card": {},
  "queue_card": {},
  "operator_card": {},
  "audit_card": {},
  "contracts_card": {},
  "recovery": {},
  "next_command": "agentdeck continue",
  "continue_card": {},
  "active_queue_source": "none",
  "inbox_card": null,
  "approval_card": null,
  "leader_action": null,
  "change_summary": {}
}
```

`project_view` remains the source of truth and must pass `validate_project_view_contract()`.
`leader_actions` must equal `project_view.leader_actions`.
`leader_card` is derived from `project_view.leader`.
`provider_health` is derived from `project_view.leader.provider` and local environment availability.
`runtime_card` is derived from `project_view.runtime_backend` and `project_view.agents[]`.
`role_card` is derived from `project_view.agents[]` role configuration.
`ledger_card` is derived from `project_view.messages`, `project_view.jobs`, `project_view.replies`, and `project_view.inbox`.
`queue_card` is derived from `project_view.leader_actions`, `project_view.approvals`, `project_view.inbox`, and the recovery-driven next command.
`operator_card` is derived from `recovery.recommended_action` and the active queue card. It is a renderable human-control descriptor, not an execution result.
`audit_card` is derived from `recovery.latest_event` and `recovery.recent_events`.
`contracts_card` is the stable pointer to contract discovery surfaces and the local contract index schema.
`recovery` must equal `project_view.recovery`.
`continue_card` must pass `validate_continue_contract()`.
`next_command` must equal `continue_card.next_command`.
`change_summary` is computed from the audit event ledger and is never persisted as a cursor.

## Watch Stream

```bash
agentdeck workbench --watch --interval 1
agentdeck workbench --watch --iterations 3 --interval 0
agentdeck workbench --watch --since-event evt_xxx --interval 1
```

Watch mode emits compact JSONL:

```json
{"mode":"workbench","ok":true}
{"mode":"workbench","ok":true}
```

Each emitted line must pass `validate_workbench_contract()` before printing. Watch mode reloads ProjectView on every iteration, so clients can render state changes without reading `.agentdeck/state/` directly. It remains read-only and must not acknowledge inbox items, approve or dispatch work, apply leader actions, capture pane output, or send tmux input.

## Change Summary

When `--since-event <event_id>` is provided, every snapshot includes a compact event-cursor summary:

```json
{
  "since_event_id": "evt_old",
  "latest_event_id": "evt_new",
  "has_new_events": true,
  "new_event_count": 1,
  "new_events": [
    {
      "event_id": "evt_new",
      "event_type": "leader_plan_created",
      "created_at": "2026-07-05T00:00:00+00:00"
    }
  ]
}
```

Without `--since-event`, `since_event_id` is `null`, `latest_event_id` still reflects the current latest audit event, and `has_new_events` is `false`. This lets GUI clients hold their own cursor, compare it with `latest_event_id`, and decide whether to re-render or fetch `agentdeck events --limit <n>`.

## Leader Card

`leader_card` is a GUI-ready projection of the configured Leader LLM:

```json
{
  "agent_id": "leader",
  "provider": "deepseek",
  "model": "deepseek-chat",
  "approval_mode": "confirm",
  "api_backed": true,
  "chat_command": "agentdeck leader chat --message <text>",
  "continue_command": "agentdeck continue",
  "actions_command": "agentdeck leader actions",
  "status_command": "agentdeck status"
}
```

The card never exposes API keys and does not call the provider. `api_backed` only indicates that the configured provider is not the local `fake` provider.

## Provider Health

`provider_health` is a GUI-ready readiness projection for the configured Leader provider:

```json
{
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
```

The card never exposes API key values and never calls the provider. It includes the configured Leader identity and model so GUI clients can render provider setup next to the Leader card without joining another state source. It only reports whether AgentDeck recognizes the provider and whether the required local environment variable is present. GUI clients can render `doctor_command` as the next diagnostic action, read `doctor_contract` for the doctor diagnostics schema, and show `setup_commands` as copyable placeholder commands; placeholders must never be replaced with real secret values in AgentDeck output.

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
      "capture_command": "agentdeck agent capture --agent planner --lines 200",
      "send_command_template": "agentdeck agent send --agent planner --text <text>",
      "inbox_command": "agentdeck inbox --agent planner",
      "controls": [
        {
          "kind": "capture",
          "label": "Capture pane output",
          "command": "agentdeck agent capture --agent planner --lines 200",
          "safety": "inspect",
          "enabled": true,
          "blocker": null
        },
        {
          "kind": "send",
          "label": "Send input",
          "command": "agentdeck agent send --agent planner --text <text>",
          "safety": "explicit_runtime",
          "enabled": true,
          "blocker": null
        }
      ]
    }
  ]
}
```

The card does not capture pane output and does not prove task completion. It only surfaces the configured agent identity, role, provider, workspace mode, current runtime binding already present in ProjectView, and explicit runtime commands a GUI can render. `capture_command` is a read-only observation command. `send_command_template` is an explicit runtime input template and must not be executed automatically. GUI clients should prefer `controls[]` for rendering buttons; disabled controls include a `blocker` such as `agent is not running`.

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

## Queue Card

`queue_card` is a compact queue overview for GUI status bars:

```json
{
  "active_queue_source": "inbox",
  "next_command": "agentdeck inbox --agent planner",
  "leader_actions": {
    "count": 0,
    "pending": 0,
    "recommended_action_id": null,
    "command": "agentdeck leader actions"
  },
  "approvals": {
    "count": 0,
    "pending": 0,
    "approved": 0,
    "command": "agentdeck approval list"
  },
  "inbox": {
    "total": 1,
    "by_agent": {"planner": 1},
    "command_template": "agentdeck inbox --agent <agent_id>"
  },
  "refresh_command": "agentdeck workbench"
}
```

The card summarizes queues only. GUI clients should use `leader_action`, `approval_card`, `inbox_card`, or the listed commands for details and explicit execution.

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
  "controls": [
    {
      "kind": "preview",
      "label": "Preview",
      "command": "agentdeck leader action --action-id act_xxx",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    },
    {
      "kind": "apply",
      "label": "Apply",
      "command": "agentdeck leader action --action-id act_xxx --apply",
      "safety": "safe_apply",
      "enabled": true,
      "blocker": null
    },
    {
      "kind": "explicit",
      "label": "Run explicit command",
      "command": "agentdeck approval create --plan-id plan_xxx --step-id step_001",
      "safety": "safe_apply",
      "enabled": true,
      "blocker": null
    }
  ],
  "active_queue_source": "leader_action",
  "action_kind": "leader_action",
  "can_apply": true,
  "apply_command": "agentdeck leader apply-action --action-id act_xxx",
  "explicit_command": "agentdeck approval create-from-plan --plan-id pln_xxx",
  "blocker": null
}
```

GUI clients should prefer `controls[]` as the renderable button list, while keeping `preview_command`, `command`, `apply_command`, and `explicit_command` as compatibility fields. Execution still belongs to the user or a later explicit approval flow. The card must not be treated as permission to auto-dispatch, auto-ack, auto-approve, or send tmux input.

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

## Contracts Card

```json
{
  "contracts_command": "agentdeck contract list",
  "contract_index_contract": "docs/contracts/contract-index-schema.md",
  "workbench_contract": "agentdeck contract workbench",
  "agent_runtime_contract": "agentdeck contract agent-runtime",
  "project_view_contract": "agentdeck contract project-view",
  "events_contract": "agentdeck contract events",
  "doctor_contract": "agentdeck contract doctor"
}
```

This card lets GUI/TUI clients bootstrap from a single workbench snapshot and then discover the full machine-readable contract index on demand, including the dedicated `agent-runtime` command contract for visible tmux pane controls. It is static metadata and does not read state, inspect tmux panes, call providers, or execute any contract command.

When `recovery.recommended_action.source` is:

- `leader_action`: `active_queue_source` is `leader_action`, and `leader_action` contains the current recovery action detail when available.
- `inbox`: `active_queue_source` is `inbox`, and `inbox_card` reuses the `agentdeck inbox --agent <id>` queue contract.
- `approval`: `active_queue_source` is `approval`, and `approval_card` reuses the `agentdeck approval list` queue contract.
- `provider_health`: `active_queue_source` is `provider_health`, and `operator_card` points at `agentdeck doctor` so GUI clients can surface missing Leader provider setup before users trigger a failing plan/chat call.
- absent or unknown: `active_queue_source` is `none`.

## Invariants

- The command is read-only.
- The command must pass `validate_workbench_contract()` before printing JSON.
- GUI clients should treat this response as a single-screen projection of ProjectView, not a second state source.
- Runtime actions still require explicit commands or approval flow.
