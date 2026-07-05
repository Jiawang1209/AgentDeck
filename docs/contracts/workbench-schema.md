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
  "lineage_card_fields": [],
  "lineage_path_fields": [],
  "queue_card_fields": [],
  "operator_card_fields": [],
  "audit_card_fields": [],
  "contracts_card_fields": [],
  "change_summary_fields": [],
  "control_registry_item_fields": []
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
  "lineage_card": {},
  "queue_card": {},
  "operator_card": {},
  "audit_card": {},
  "contracts_card": {},
  "control_mode_card": {},
  "recovery": {},
  "next_command": "agentdeck continue",
  "continue_card": {},
  "active_queue_source": "none",
  "inbox_card": null,
  "leader_inbox_card": {},
  "approval_card": null,
  "leader_action": null,
  "control_registry": [],
  "change_summary": {}
}
```

`project_view` remains the source of truth and must pass `validate_project_view_contract()`.
`leader_actions` must equal `project_view.leader_actions`.
`leader_card` is derived from `project_view.leader`.
`provider_health` is derived from `project_view.leader.provider` and local environment availability.
`runtime_card` is derived from `project_view.runtime_backend` and `project_view.agents[]`.
`role_card` is derived from `project_view.agents[]` role configuration.
`ledger_card` is derived from `project_view.messages`, `project_view.jobs`, `project_view.replies`, `project_view.artifacts`, and `project_view.inbox`.
`lineage_card` is a read-only path projection derived from the same ledger summaries plus visible inbox cards.
`queue_card` is derived from `project_view.leader_actions`, `project_view.approvals`, `project_view.inbox`, and the recovery-driven next command.
`operator_card` is derived from `recovery.recommended_action` and the active queue card. It is a renderable human-control descriptor, not an execution result.
`audit_card` is derived from `recovery.latest_event` and `recovery.recent_events`.
`contracts_card` is the stable pointer to contract discovery surfaces and the local contract index schema, including the Leader chat and Leader review contracts.
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
  "review_command_template": "agentdeck leader review --plan-id <plan_id>",
  "actions_command": "agentdeck leader actions",
  "status_command": "agentdeck status",
  "controls": [
    {
      "kind": "chat",
      "label": "Ask Leader",
      "command": "agentdeck leader chat --message <text>",
      "safety": "explicit_user",
      "enabled": false,
      "blocker": "requires message text"
    }
  ]
}
```

The card never exposes API keys and does not call the provider. `api_backed` only indicates that the configured provider is not the local `fake` provider. `controls[]` uses `kind`, `label`, `command`, `safety`, `enabled`, and `blocker` so GUI clients can render Leader entry points without deriving buttons from command strings. `chat` and `review` controls are disabled templates until a GUI or user supplies message text or a plan id; `continue`, `actions`, and `status` are read-only inspect controls. `review_command_template` requires a concrete plan id and must not be treated as permission to review, approve, dispatch, or capture automatically.

## Control Mode

`control_mode_card` is a read-only policy projection for Codex-like control levels:

```json
{
  "mode": "control_mode",
  "current_mode": "ask",
  "approval_mode": "confirm",
  "default_safety": "inspect",
  "available_modes": [],
  "active_controls": []
}
```

`current_mode=ask` means AgentDeck may plan, inspect, and suggest commands without mutating runtime state. `approve` describes the existing approval-gated safe-apply path. `autonomous` is advertised as disabled until scoped delegation, budgets, allowlists, and audit gates exist. `active_controls[]` includes concrete `set_mode` controls for `ask`, `approve`, and `autonomous`; the current mode is disabled with `blocker=already current mode`, `approve` uses `safety=explicit_user`, and `autonomous` remains disabled with an implementation blocker. `set_mode_command_template` is only a form helper for clients that need to build their own mode selector. This card does not grant permission by itself and does not execute, approve, dispatch, acknowledge, or send tmux input. The explicit mutation path is `agentdeck policy set-mode --mode ask|approve`; it only updates `.agentdeck/config.toml:leader.approval_mode` and appends an audit event. `agentdeck policy set-mode --mode autonomous` must fail, leave config unchanged, and append a rejection audit event.

## Control Registry

`control_registry[]` is a flattened, read-only command palette index derived from existing card controls. Each item uses `scope`, `card`, `kind`, `label`, `command`, `safety`, `enabled`, `blocker`, and `agent_id`. It currently indexes `leader_card.controls[]`, `provider_health.controls[]`, `control_mode_card.active_controls[]`, every `runtime_card.agents[].controls[]`, `role_card.agents[].controls[]`, visible `inbox_card.items[].controls[]`, `leader_inbox_card.items[].controls[]`, and `operator_card.controls[]`. Provider switch controls must be preserved as `scope=provider` / `kind=set_provider` so clients can render DeepSeek, OpenAI-compatible, Codex CLI, Claude CLI, and fake-provider choices without hard-coding a menu. Inbox controls must be preserved as `scope=inbox`; active recovery inbox items use `card=inbox_card`, while worker replies flowing back to Leader use `card=leader_inbox_card` and `agent_id=leader`. Inbox `kind=ack` controls are explicit runtime commands and must not be executed by workbench or controls. Runtime terminal controls must be preserved as `kind=terminal` so clients can render a direct "Open terminal" affordance without parsing `agentdeck agent terminal --agent <id>`. When the operator exposes batch approval dispatch, the registry must preserve the operator control as `kind=dispatch_ready` for `agentdeck approval dispatch-ready --confirm` so clients can identify it without parsing labels or command strings. Clients may render this as a command palette or toolbar, but it is not a second state source and does not grant permission beyond each item's `safety`, `enabled`, and `blocker`.

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
  "command_path": null,
  "doctor_command": "agentdeck doctor",
  "doctor_contract": "agentdeck contract doctor",
  "setup_commands": [
    "export DEEPSEEK_API_KEY=\"<your-deepseek-api-key>\"",
    "export DEEPSEEK_BASE_URL=\"https://api.deepseek.com/v1\"",
    "export DEEPSEEK_MODEL=\"deepseek-chat\""
  ],
  "controls": [
    {
      "kind": "set_provider",
      "label": "Use Codex CLI",
      "command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
      "safety": "explicit_user",
      "enabled": true,
      "blocker": null
    },
    {
      "kind": "set_provider",
      "label": "Use DeepSeek",
      "command": "agentdeck leader set-provider --provider deepseek --model deepseek-chat",
      "safety": "explicit_user",
      "enabled": false,
      "blocker": "already current provider"
    }
  ]
}
```

The card never exposes API key values and never calls the provider. It includes the configured Leader identity and model so GUI clients can render provider setup next to the Leader card without joining another state source. For API-backed providers such as `deepseek` and `openai-compatible`, readiness is based on the required local environment variable and `command_path` is `null`. For CLI-backed providers such as `codex-cli` and `claude-cli`, readiness is based on whether the local command is available on PATH, and `command_path` exposes the resolved executable path when available. GUI clients can render `doctor_command` as the next diagnostic action, read `doctor_contract` for the doctor diagnostics schema, and show `setup_commands` as copyable placeholder commands; placeholders must never be replaced with real secret values in AgentDeck output. `controls[]` exposes explicit `agentdeck leader set-provider` commands for supported Leader backends: `kind=set_provider` is the normal explicit switch, while `kind=guarded_set_provider` appends `--require-ready` so the later explicit command refuses to write config when the target backend is not ready. The current provider controls are disabled with `already current provider`; other controls require `safety=explicit_user` and only change the default Leader provider/model after the human runs the command. `validate_workbench_contract()` rejects provider controls that do not use `safety=explicit_user`, do not point at `agentdeck leader set-provider --provider ...`, omit `--require-ready` for `guarded_set_provider`, or are disabled without a blocker.

## Runtime Card

`runtime_card` is a GUI-ready projection of visible tmux runtime bindings:

```json
{
  "backend": "tmux",
  "count": 3,
  "by_status": {"running": 1, "configured": 2},
  "refresh_command": "agentdeck agent refresh",
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
      "terminal_command": "agentdeck agent terminal --agent planner",
      "capture_command": "agentdeck agent capture --agent planner --lines 200",
      "send_command_template": "agentdeck agent send --agent planner --text <text>",
      "inbox_command": "agentdeck inbox --agent planner",
      "controls": [
        {
          "kind": "terminal",
          "label": "Open terminal",
          "command": "agentdeck agent terminal --agent planner",
          "safety": "inspect",
          "enabled": true,
          "blocker": null
        },
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

The card does not capture pane output and does not prove task completion. It only surfaces the configured agent identity, role, provider, workspace mode, current runtime binding already present in ProjectView, and explicit runtime commands a GUI can render. `refresh_command` is an explicit reconciliation command that checks recorded `running` panes against tmux and marks missing panes as `stale`; `agentdeck workbench` does not run it automatically. `terminal_command` opens the read-only terminal card for the agent; it does not attach tmux by itself. `capture_command` is a read-only observation command. `send_command_template` is an explicit runtime input template and must not be executed automatically. GUI clients should prefer `controls[]` for rendering buttons; disabled controls include a `blocker` such as `agent is not running`.

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
      "assign_command": "agentdeck agent assign-role --agent planner --role planning --role-prompt 'Break down goals and prepare implementation steps.'",
      "controls": [
        {
          "kind": "assign_role",
          "label": "Assign role",
          "command": "agentdeck agent assign-role --agent planner --role <role> --role-prompt <role_prompt>",
          "safety": "explicit_user",
          "enabled": false,
          "blocker": "requires role and role_prompt"
        }
      ]
    }
  ]
}
```

The card is configuration-only. It does not dispatch work or mutate roles; GUI clients must run `assign_command` or a completed `assign_role` control explicitly when a human changes a role. `controls[]` are intentionally disabled templates until the GUI supplies concrete `role` and `role_prompt` values, and they must be preserved as `scope=role` / `kind=assign_role` items in `control_registry[]`.

## Ledger Card

`ledger_card` is a GUI-ready projection of the communication ledger:

```json
{
  "messages": {},
  "jobs": {},
  "replies": {},
  "artifacts": {},
  "inbox": {},
  "trace_commands": [
    "agentdeck trace --id msg_xxx",
    "agentdeck trace --id job_xxx",
    "agentdeck trace --id rep_xxx",
    "agentdeck trace --id inb_xxx"
  ]
}
```

`messages`, `jobs`, `replies`, and `artifacts` reuse the ProjectView summary shapes and must retain `trace_command` on each item. `artifacts` is the recoverable output summary for files or other deliverables produced by worker agents; it carries `artifact_id`, linked message/job/reply ids, `from_agent`, `path`, `kind`, `status`, and `created_at` without reading file contents. `inbox` reuses the ProjectView inbox summary so GUI clients can show mailbox heads without scanning per-agent inbox arrays. `trace_commands` is a de-duplicated convenience list for quick trace navigation; the detail source remains `agentdeck trace --id <id>`.

## Lineage Card

`lineage_card` turns ledger summaries into GUI-ready communication paths:

```json
{
  "mode": "lineage",
  "title": "Communication lineage",
  "message_count": 1,
  "job_count": 1,
  "reply_count": 1,
  "inbox_count": 1,
  "trace_command_template": "agentdeck trace --id <id>",
  "recent_paths": [
    {
      "message_id": "msg_xxx",
      "job_id": "job_xxx",
      "reply_id": "rep_xxx",
      "inbox_id": "inb_xxx",
      "from_actor": "leader",
      "to_agent": "planner",
      "from_agent": "planner",
      "to_actor": "leader",
      "task": "Prepare an implementation plan",
      "status": "reply_pending_ack",
      "trace_command": "agentdeck trace --id msg_xxx"
    }
  ]
}
```

`recent_paths[]` is a convenience projection, not a new ledger. It links message, job, reply, and inbox ids when they are visible in ProjectView or embedded inbox cards. `trace_command` is always the detail entry point; clients must still use `agentdeck trace --id <id>` for full lineage details. The card does not create messages, acknowledge inbox items, capture replies, read tmux panes, or send tmux input.

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

For approval dispatch recovery, `operator_card` derives runtime readiness from the same ProjectView snapshot. If the approved approval targets an agent without a running pane, `operator_card.blocker` and the explicit control blocker should explain the runtime problem, and the explicit control must be disabled. When more than one approval is already approved, the operator promotes the main explicit action to `agentdeck approval dispatch-ready --confirm` with `action_kind=approval_dispatch_ready`; the corresponding control must use `kind=dispatch_ready` and label `Dispatch ready approvals`. It is enabled when at least one approved target has a running pane, while blocked targets remain visible through the later dispatch-ready result. The card still shows the explicit command so the human can see what will run after fixing runtime, but the workbench must not dispatch, spawn, refresh, or send tmux input.

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
  "controls_contract": "agentdeck contract controls",
  "agent_runtime_contract": "agentdeck contract agent-runtime",
  "leader_chat_contract": "agentdeck contract leader-chat",
  "leader_review_contract": "agentdeck contract leader-review",
  "project_view_contract": "agentdeck contract project-view",
  "events_contract": "agentdeck contract events",
  "doctor_contract": "agentdeck contract doctor",
  "artifacts_contract": "agentdeck contract artifacts"
}
```

This card lets GUI/TUI clients bootstrap from a single workbench snapshot and then discover the full machine-readable contract index on demand, including the dedicated `controls` command palette contract, the `agent-runtime` command contract for visible tmux pane controls, the `leader-chat` response contract for natural-language Leader interactions, and the `artifacts` contract for the read-only worker output index. It is static metadata and does not read state, inspect tmux panes, call providers, or execute any contract command.

When `recovery.recommended_action.source` is:

- `leader_action`: `active_queue_source` is `leader_action`, and `leader_action` contains the current recovery action detail when available.
- `inbox`: `active_queue_source` is `inbox`, and `inbox_card` reuses the `agentdeck inbox --agent <id>` queue contract.
- `leader_inbox_card` always reuses the `agentdeck inbox --agent leader` queue contract so GUI clients can see worker `task_reply` items returning to Leader even when the active recovery queue points elsewhere.
- `approval`: `active_queue_source` is `approval`, and `approval_card` reuses the `agentdeck approval list` queue contract. If the active approved approval cannot be dispatched because the target runtime is not running, `operator_card.blocker` and the explicit control blocker must expose that runtime blocker. If multiple approvals are approved, `operator_card.action_kind` may be `approval_dispatch_ready`, both `command` and `explicit_command` must be `agentdeck approval dispatch-ready --confirm`, and the matching operator control must use `kind=dispatch_ready`.
- `provider_health`: `active_queue_source` is `provider_health`, and `operator_card` points at `agentdeck doctor` so GUI clients can surface missing Leader provider setup before users trigger a failing plan/chat call.
- `runtime`: `active_queue_source` is `runtime`, and `operator_card` points at `agentdeck agent refresh` so GUI clients can surface stale pane bindings before users continue runtime-dependent work.
- `reply`: `active_queue_source` is `reply`, and `operator_card` points at explicit `agentdeck capture-reply --agent <id> --message-id <id>` with a trace preview command. The explicit operator control must use `kind=capture_reply`, `label=Capture reply`, and `safety=explicit_runtime`.
- absent or unknown: `active_queue_source` is `none`.

## Invariants

- The command is read-only.
- The command must pass `validate_workbench_contract()` before printing JSON.
- GUI clients should treat this response as a single-screen projection of ProjectView, not a second state source.
- Runtime actions still require explicit commands or approval flow.
