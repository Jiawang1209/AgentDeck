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
  "intent_card_fields": [],
  "intent_control_fields": [],
  "leader_action_card_fields": [],
  "leader_summary_card_fields": [],
  "continue_card_fields": [],
  "run_start_card_fields": [],
  "run_progress_card_fields": [],
  "capture_card_fields": [],
  "dispatch_preview_card_fields": [],
  "dispatch_batch_preview_card_fields": [],
  "dispatch_batch_preview_item_fields": [],
  "runtime_action_card_fields": [],
  "startup_preview_card_fields": [],
  "startup_preview_item_fields": [],
  "agent_ready_card_fields": [],
  "runtime_card_fields": [],
  "terminal_session_card_fields": [],
  "terminal_session_control_fields": [],
  "terminal_session_item_fields": [],
  "queue_card_fields": [],
  "operator_card_fields": [],
  "role_card_fields": [],
  "role_agent_fields": [],
  "ledger_card_fields": [],
  "lineage_card_fields": [],
  "lineage_path_fields": [],
  "audit_card_fields": [],
  "artifacts_card_fields": [],
  "artifact_summary_fields": [],
  "artifact_item_fields": [],
  "trace_card_fields": [],
  "trace_message_fields": [],
  "trace_attempt_fields": [],
  "trace_job_fields": [],
  "trace_reply_fields": [],
  "trace_artifact_fields": [],
  "trace_inbox_item_fields": [],
  "workbench_card_fields": [],
  "workbench_control_registry_item_fields": [],
  "control_registry_card_fields": [],
  "capability_card_fields": [],
  "capability_item_fields": [],
  "capability_control_fields": [],
  "capability_placeholder_fields": [],
  "capability_placeholders": [],
  "project_view_schema_version": "project-view/v1",
  "project_view_contract": "agentdeck contract project-view"
}
```

Use `agentdeck contract leader-chat --example` to include a stable GUI-ready response fixture.

Live `agentdeck leader chat` responses are validated against this contract before JSON is printed. If validation fails, the command exits non-zero, writes the contract errors to stderr, records the failure in `leader_errors[]`, appends a `leader_chat_contract_failed` event, and does not print a partial chat response.

Provider-backed planning modes include the same schema rules in the API-backed or CLI-backed Leader prompt and validate the same provider plan schema before chat payloads are built: accepted plans must include non-empty string top-level `goal` and `summary` plus non-empty `steps[]`; every step must include a positive integer `step`, and the full step set must be numbered `1..n` without duplicates or gaps; every step must also include non-empty string `agent_id`, non-empty string `role`, non-empty string `task`, non-empty string `risk`, and `requires_approval`; every step `agent_id` must reference a configured worker agent, and every step `role` must match that worker agent's configured role; every step must keep `requires_approval=true`; and AgentDeck forces top-level `approval_required=true` and `dispatch_ready=false` even if an API-backed or CLI-backed backend returns different values.

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
  "intent_card": {},
  "plan_id": "pln_xxx",
  "review": {},
  "recovery": {},
  "next_command": "agentdeck leader apply-action --action-id act_xxx",
  "leader_action": {},
  "leader_action_card": {},
  "leader_summary_card": null,
  "continue_card": null,
  "run_start_card": null,
  "run_progress_card": null,
  "capture_card": null,
  "dispatch_preview_card": null,
  "dispatch_batch_preview_card": null,
  "runtime_action_card": null,
  "startup_preview_card": null,
  "agent_ready_card": null,
  "inbox_card": null,
  "trace_card": null,
  "approval_card": null,
  "runtime_card": null,
  "terminal_session_card": null,
  "queue_card": null,
  "operator_card": null,
  "role_card": null,
  "ledger_card": null,
  "lineage_card": null,
  "artifacts_card": null,
  "workbench_card": null,
  "control_mode_card": null,
  "capability_card": null
}
```

`leader_actions` is identical to `project_view.leader_actions`. It is provided so a chat surface can render the queue without issuing a second status call.

`leader_action_card` is a GUI-ready projection of the top-level `leader_action` when one is present:

```json
{
  "mode": "leader_action",
  "title": "Leader action",
  "action_id": "act_xxx",
  "kind": "create_approvals",
  "status": "pending",
  "reason": "plan has no approval records",
  "preview_command": "agentdeck leader action --action-id act_xxx",
  "can_apply": true,
  "apply_command": "agentdeck leader apply-action --action-id act_xxx",
  "explicit_command": "agentdeck approval create-from-plan --plan-id pln_xxx",
  "apply_blocker": null,
  "controls": []
}
```

The card is derived from the same action detail and does not introduce a second action state source. GUI and natural-language shells should render its `controls[]`, `preview_command`, `apply_command`, `explicit_command`, and `apply_blocker`, while still treating the underlying `leader_action` and ProjectView recovery as the authority.

`intent_card` is the stable routing card for GUI and natural-language shells:

```json
{
  "mode": "workbench",
  "matched_intent": "workbench",
  "route_source": "local_rule",
  "embedded_card": "workbench_card",
  "secondary_embedded_cards": [],
  "read_only": true,
  "next_command": "agentdeck continue",
  "requires_explicit_user": false,
  "controls": [
    {
      "kind": "inspect",
      "label": "Inspect workbench_card",
      "command": "agentdeck workbench",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    },
    {
      "kind": "next",
      "label": "Next command",
      "command": "agentdeck continue",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    }
  ]
}
```

`route_source` is `local_rule` for local intent routing, `provider_plan` for first-time provider-backed planning, and `state_review` for review of an existing plan. `embedded_card` names the primary top-level card the GUI should render for that route, or `null` when there is no embedded card. `secondary_embedded_cards[]` names same-response companion cards that should be rendered alongside the primary card without becoming a second state source; for example runtime inspection keeps `embedded_card=runtime_card` and lists `terminal_session_card` as a secondary card for the project terminal strip. `read_only=false` marks routes that create or apply state, such as `plan`, `review`, or `apply_action`. `controls[]` are renderable command descriptors only; they must not be executed automatically. GUI clients should render the provided `label` instead of parsing `command`; runtime explicit actions use action labels such as `Refresh runtime`, `Spawn planner`, `Send input to planner`, or `Stop planner`, policy mode actions use labels such as `Switch to ask mode`, `Switch to approval mode`, or `Request autonomous mode`, approval actions use labels such as `Approve approval`, `Reject approval`, or `Dispatch approval`, inbox ack actions use `Acknowledge inbox item`, and read-only observation actions use labels such as `Capture agent output`, `Open inbox`, or `Inspect trace`. When an embedded card has a safe read-only command, `controls[]` should include an `inspect` control before the `next` control. When `intent_card.next_command` is present, `controls[]` must include a `kind=next` control, and that control command must match `intent_card.next_command`; when `intent_card.requires_explicit_user=true`, that `kind=next` control must not use `safety=inspect`. `validate_leader_chat_contract()` rejects missing, drifting, or under-labelled next controls so GUI clients do not lose the primary button or render a button that runs a different or falsely read-only command from the card's next action. Template `next` commands that still require user input, such as `--reason <reason>`, must be disabled and include a blocker such as `requires reason`.

Help-mode responses include `capability_card`, a read-only capability map for natural-language shells and future GUI command palettes:

```json
{
  "mode": "help",
  "title": "Leader chat capabilities",
  "summary": "Read-only capability map for natural-language and GUI command surfaces.",
  "default_command": "agentdeck workbench",
  "capability_count": 1,
  "capabilities": [
    {
      "mode": "workbench",
      "label": "Open workbench",
      "description": "Inspect the full local control plane snapshot.",
      "example_messages": ["打开工作台", "workbench"],
      "command": "agentdeck workbench",
      "safety": "inspect",
      "requires_explicit_user": false,
      "card": "workbench_card",
      "controls": [
        {
          "kind": "inspect",
          "label": "Open workbench",
          "command": "agentdeck workbench",
          "safety": "inspect",
          "enabled": true,
          "blocker": null
        }
      ]
    }
  ]
}
```

The real card also includes Leader scheduling entries for `plan`, `review`, and `apply_action`, plus `policy` and `provider_switch` entries for explicit configuration commands. `plan` must use `safety=plan_only` and its control command should point at the explicit `agentdeck leader plan --task <goal>` planning entrypoint; `review` and `apply_action` must use `safety=safe_apply`, with review pointing at `agentdeck leader review --plan-id <plan_id>` and apply_action pointing at `agentdeck leader apply-action --action-id <action_id>`; `policy` uses `safety=explicit_user` and points at `agentdeck policy set-mode --mode <mode>`; `provider_switch` uses `safety=explicit_user` and points at `agentdeck leader set-provider --provider <provider> --model <model>`; read-only views such as `workbench`, `continue`, `runtime`, `capture`, `role`, `ledger`, `trace`, `queue`, `approval`, `inbox`, and setup diagnostics use `safety=inspect` unless the downstream mode explicitly recommends an explicit runtime or provider configuration command.

Every capability item includes `controls[]` using the same `kind`, `label`, `command`, `safety`, `enabled`, and `blocker` shape as intent controls; `agentdeck contract leader-chat` exposes the same shape as `capability_control_fields`. Direct read-only commands such as `agentdeck workbench` are enabled. Template commands may only use known placeholders: `<goal>`, `<plan_id>`, `<action_id>`, `<agent_id>`, or `<mode>`. `capability_placeholders[]` exposes that whitelist with the matching blocker for each placeholder so GUI clients can render template inputs without parsing this Markdown document. Template commands with those placeholders must be disabled and must include a matching blocker such as `requires goal text`, `requires plan_id`, `requires action_id`, `requires agent_id`, or `requires control mode`. Capability controls must keep `command` and `safety` aligned with their parent capability item.

Help-mode also includes `control_registry_card`, a read-only command palette snapshot derived from `agentdeck workbench`:

```json
{
  "mode": "control_registry",
  "title": "Command palette",
  "source_command": "agentdeck workbench",
  "default_command": "agentdeck controls",
  "filters": {
    "scope": null,
    "card": null,
    "query": null,
    "control_id": null,
    "enabled_only": false,
    "active_filter_keys": [],
    "item_count_before_filter": 1
  },
  "selection": {
    "requested_control_id": null,
    "matched": false,
    "matched_count": 0,
    "selected_control": null,
    "blocker": null,
    "next_command": null
  },
  "item_count": 1,
  "items": [
    {
      "scope": "leader",
      "card": "leader_card",
      "kind": "continue",
      "label": "Continue",
      "command": "agentdeck continue",
      "safety": "inspect",
      "enabled": true,
      "blocker": null,
      "agent_id": "leader",
      "control_id": "leader:leader_card:continue:leader:7b40ec8c28"
    }
  ],
  "group_count": 1,
  "groups": [
    {
      "group_id": "leader:leader_card",
      "scope": "leader",
      "card": "leader_card",
      "label": "Leader",
      "item_count": 1,
      "enabled_count": 1,
      "disabled_count": 0,
      "items": [
        {
          "scope": "leader",
          "card": "leader_card",
          "kind": "continue",
          "label": "Continue",
          "command": "agentdeck continue",
          "safety": "inspect",
          "enabled": true,
          "blocker": null,
          "agent_id": "leader",
          "control_id": "leader:leader_card:continue:leader:7b40ec8c28"
        }
      ]
    }
  ]
}
```

Help-mode is returned when the human asks `帮助`, `help`, `/help`, `你能做什么`, `有哪些能力`, `命令面板`, `commands`, or `capabilities`. Command-palette messages may include the same read-only filter intent as `agentdeck controls`; for example, `命令面板 runtime enabled only` returns a `control_registry_card` with `filters.scope=runtime`, `filters.enabled_only=true`, and `filters.active_filter_keys=["scope", "enabled_only"]`; `命令面板 搜索 terminal` returns `filters.query=terminal` and `filters.active_filter_keys=["query"]`; `命令面板 control_id <id>` returns `filters.control_id=<id>`, `filters.active_filter_keys=["control_id"]`, and a `selection` object pointing at the matching stable control item. If the requested id does not exist in the source registry, `selection.matched=false`, `selection.selected_control=null`, `selection.blocker="control_id not found"`, and `selection.next_command=null`; if the id exists but the current command-palette filters exclude it, `selection.blocker="control_id filtered out"`. If the requested id matches an enabled item, `selection.next_command` equals that item's command; disabled selections keep `next_command=null` and expose the disabled reason through `selected_control.blocker`. It records a chat turn, recommends `agentdeck workbench`, embeds `capability_card` and `control_registry_card`, and must not create a plan, leader action, approval, message, job, inbox item, inspect tmux panes, call the provider, or send tmux input. The capability entries, controls, registry items, filters, selection, and registry groups describe available commands; they are not permission to auto-run those commands. `control_registry_card.filters`, `control_registry_card.selection`, and `control_registry_card.groups[]` follow the same read-only projection contract as `agentdeck controls`: filters narrow the returned `items[]`, `active_filter_keys` is derived from the filter values, selection is derived from `filters.control_id` and `items[]`, then groups are derived from those filtered items.

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
When `continue_card` is present, `validate_leader_chat_contract()` reuses `validate_continue_contract()` and prefixes nested errors with `continue_card:`. When continue-mode recovery recommends a reply capture and `trace_card` is present, the validator rejects `intent_card.embedded_card` values other than `trace_card`, and the embedded trace inspect control must point at `agentdeck trace --id <trace_card.query_id>`.
When continue-mode recovery points at a pending inbox item, the response also includes that agent's `inbox_card`; when recovery points at approvals, the response also includes `approval_card`; when more than one approval is already approved, the embedded `continue_card.next_command` may be promoted to `agentdeck approval dispatch-ready --confirm`; when recovery points at stale runtime, the response also includes same-snapshot `runtime_card` and `terminal_session_card`, while `intent_card.embedded_card` remains `continue_card` and `intent_card.secondary_embedded_cards` lists `runtime_card` and `terminal_session_card`; when recovery points at `reply_waiting`, the response may include `trace_card` for the pending message lineage while still recommending the explicit `agentdeck capture-reply --agent <id> --message-id <id>` command. In that reply-waiting case, `intent_card.embedded_card` should prefer `trace_card` over `continue_card`, so GUI shells can render the actual communication evidence as the primary card and still use the next control for capture-reply. The top-level response `next_command`, `leader_explanation.next_command`, and persisted chat turn `next_command` must match `continue_card.next_command`. Continue-mode remains read-only: embedded cards are display/action affordances, not automatic ack, approve, dispatch, capture-reply, refresh, spawn, stop, attach/select-pane, or tmux input.

Summary-mode responses are returned when the human asks to summarize or inspect the current final summary, such as `总结当前计划`, `汇总结果`, `summary`, or `summarize`. They require a latest saved plan whose local review result is `next_action=summarize`; otherwise the command returns an explicit error instead of falling through to provider-backed planning. Summary-mode embeds `leader_summary_card`, reusing the same response shape as `agentdeck leader summary --plan-id <id>`:

```json
{
  "schema_version": "project-view/v1",
  "plan_id": "pln_xxx",
  "task": "Ship a multi-agent task",
  "status": "ready",
  "reply_count": 1,
  "artifact_count": 1,
  "summary": "1 dispatched step has replies; 1 artifact recorded.",
  "steps": [],
  "controls": []
}
```

When `leader_summary_card` is present, `validate_leader_chat_contract()` reuses `validate_leader_summary_contract()` and prefixes nested errors with `leader_summary_card:`. Summary-mode records a chat turn for history, but it must not create Leader actions, approvals, messages, jobs, replies, artifacts, or inbox items; it must not call a provider, inspect tmux panes, capture replies, dispatch work, acknowledge inbox items, or send tmux input. The top-level `next_command` remains `agentdeck leader summary --plan-id <id>`, and `intent_card.embedded_card` should be `leader_summary_card`.

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

Runtime chat responses also include a top-level `terminal_session_card`, derived from that same `runtime_card` and the project tmux configuration. It reuses the workbench terminal session card shape so GUI/TUI clients can render the project terminal strip directly, including the session attach command, refresh command, project controls, and each terminal item's `controls[]` select-pane button.

When `runtime_card` or `terminal_session_card` is present, `validate_leader_chat_contract()` checks the same runtime and terminal session field lists exposed by `agentdeck contract workbench`. Runtime-mode is read-only: it may recommend `agentdeck agent refresh` or show attach/select-pane commands, but it must not attach tmux, select panes, refresh, spawn, stop, capture pane output, or send tmux input by itself.

When the human asks to open or focus a specific visible agent terminal, terminal-mode embeds `terminal_card`, which reuses `agentdeck agent terminal --agent <id>` and includes tmux attach/select-pane commands plus capture/send/stop/inbox controls. The top-level `next_command` matches `terminal_card.attach_command`. This remains a command surface only; chat does not attach to tmux, capture output, send input, stop panes, or write runtime state.

When the human asks to prepare or start all configured agents, runtime-mode embeds `agent_ready_card`, which reuses `agentdeck agent ready` and includes total/running/not_running/all_running counts, explicit `spawn_commands`, `spawn_ready_command`, `refresh_command`, `dispatch_ready_command`, GUI-ready `controls[]`, and the same `runtime_card`. The response also includes `startup_preview_card`, a GUI-ready explicit-runtime checklist that expands the startup command into not-running agents, their roles, runtime status, pane id, single-agent spawn commands, terminal commands, blockers, and per-agent inspect/spawn controls. The intent surface keeps `embedded_card=agent_ready_card` and lists `startup_preview_card`, `runtime_card`, `terminal_session_card`, and `control_registry_card` in `secondary_embedded_cards`, so GUI clients can render readiness, the execution-before-confirmation checklist, runtime status, the project terminal strip, and the selected startup control from one response. When the human asks to start one configured agent, runtime-mode uses `runtime_action_card` as the primary card with `action=spawn`, includes `startup_preview_card` as a companion checklist filtered to that agent, and includes a `control_registry_card` filtered to `runtime_action_card` whose selection points at the top-level spawn control. The top-level `next_command` matches `agent_ready_card.next_command`, `startup_preview_card.next_command`, and `runtime_action_card.command`: `agentdeck agent spawn-ready --confirm` when multiple configured agents are not running, one explicit single-agent spawn command when exactly one agent is not running or one target agent is requested, or `agentdeck approval dispatch-ready --confirm` when every configured agent is running. This remains a suggestion surface only; it does not spawn panes or dispatch approvals.

When the human asks to start one configured agent, refresh runtime bindings, send input to a running agent, or stop a running agent, runtime-mode embeds `runtime_action_card` as the primary card. The card contains `action=spawn`, `action=refresh_runtime`, `action=send`, or `action=stop`, target metadata when there is a target agent, exact `command`, optional `preview_text`, `safety=explicit_runtime`, `requires_explicit_user=true`, and `controls[]` for inspecting the relevant runtime surface plus explicitly spawning, refreshing, sending input, or stopping the pane. Refresh is project-level and uses `agent_id=null`, `runtime_status=suggested`, and `command=agentdeck agent refresh`; spawn uses `agentdeck agent spawn --agent <id>` and is enabled only when the target is not already running. The same response also includes `runtime_card`, `terminal_session_card`, and a `control_registry_card` filtered to `runtime_action_card`, with `selection.selected_control` pointing at the spawn/refresh/send/stop control whose command matches the top-level `next_command`; `selection.next_command` must equal `runtime_action_card.command`, and the validator rejects runtime action responses whose registry selection points at a different enabled control. Spawn responses also include `startup_preview_card` as the execution checklist. This card is an execution-before-confirmation affordance only: chat does not spawn panes, refresh runtime, send tmux input, stop panes, create plans, create approvals, create messages, create jobs, or mutate runtime state.

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

Workbench-mode responses are returned when the human asks to open the full workbench, dashboard, overview, or local control plane. They return `workbench_card`, reusing the same validated snapshot shape as `agentdeck workbench`:

```json
{
  "mode": "workbench",
  "next_command": "agentdeck continue",
  "workbench_card": {
    "mode": "workbench",
    "project_view": {},
    "runtime_card": {},
    "role_card": {},
    "ledger_card": {},
    "lineage_card": {},
    "queue_card": {},
    "operator_card": {},
    "control_registry": [],
    "contracts_card": {}
  }
}
```

When `workbench_card` is present, `validate_leader_chat_contract()` reuses `validate_workbench_contract()` and prefixes nested errors with `workbench_card:`. `agentdeck contract leader-chat` exposes `terminal_session_card_fields`, `terminal_session_control_fields`, `terminal_session_item_fields`, and `workbench_control_registry_item_fields` so natural-language shells can render the embedded project-level terminal session bar and command palette without parsing the workbench schema separately. Workbench-mode records a chat turn for history, but it must not create plans/actions/approvals/messages/jobs/inbox items, acknowledge inbox items, approve approvals, dispatch work, refresh runtime, capture pane output, read pane output, attach tmux, select panes, or send tmux input.

Ledger-mode responses are returned when the human asks to inspect the communication ledger, message lineage, or trace commands. They return `ledger_card` and `lineage_card`, reusing the same projections as `agentdeck workbench`:

```json
{
  "mode": "ledger",
  "next_command": "agentdeck trace --id msg_xxx",
  "ledger_card": {
    "messages": {},
    "jobs": {},
    "replies": {},
    "inbox": {},
    "trace_commands": []
  },
  "lineage_card": {
    "mode": "lineage",
    "recent_paths": []
  }
}
```

When `ledger_card` or `lineage_card` is present, `validate_leader_chat_contract()` checks the same ledger and lineage field lists exposed by `agentdeck contract workbench`. Ledger-mode records a chat turn for history, but it must not create plans/actions/approvals/messages/jobs/inbox items, acknowledge inbox items, dispatch work, capture replies, read pane output, or send tmux input.

Artifacts-mode responses are returned when the human asks to inspect worker artifacts, outputs, deliverables, or `产物`. They return `artifacts_card`, reusing the same shape as `agentdeck artifacts`:

```json
{
  "mode": "artifacts",
  "next_command": "agentdeck artifacts",
  "artifacts_card": {
    "schema_version": "project-view/v1",
    "artifacts_command": "agentdeck artifacts",
    "project_view_contract": "agentdeck contract project-view",
    "trace_contract": "agentdeck contract trace",
    "trace_command_template": "agentdeck trace --id <id>",
    "artifacts": {}
  }
}
```

When `artifacts_card` is present, `validate_leader_chat_contract()` reuses `validate_artifacts_contract()` and prefixes nested errors with `artifacts_card:`. Artifacts-mode records a chat turn and audit event for history, but it must not create plans/actions/approvals/messages/jobs/replies/artifacts/inbox items, call a provider, read artifact file contents, read pane output, or send tmux input.

Capture-mode responses are returned when the human asks to inspect one spawned agent pane output. They return `capture_card`, a GUI-ready read-only terminal snapshot:

```json
{
  "mode": "capture",
  "next_command": "agentdeck agent capture --agent planner --lines 200",
  "capture_card": {
    "agent_id": "planner",
    "pane_id": "%42",
    "lines": 200,
    "capture_command": "agentdeck agent capture --agent planner --lines 200",
    "output": "status: running\n"
  }
}
```

When `capture_card` is present, `validate_leader_chat_contract()` checks `capture_card_fields`. Capture-mode records a chat turn and reads the requested tmux pane through the runtime backend, but it must not create plans/actions/approvals/messages/jobs/inbox items, acknowledge inbox items, dispatch work, capture replies into the ledger, or send tmux input. If the requested agent is not spawned, the response must fail with `agent is not spawned: <agent_id>` rather than falling through to provider-backed planning.

Natural-language capture-reply suggestions, such as `捕获 planner 对 msg_xxx 的回复` or `capture reply from planner for msg_xxx`, also return `mode=capture`, but they do not include `capture_card` and do not read the pane. They embed `trace_card` for the referenced message, set `next_command` to `agentdeck capture-reply --agent <agent_id> --message-id <message_id>`, and mark `leader_explanation.action_kind=capture_reply`, `safety=explicit_runtime`, and `requires_explicit_user=true`. Requests such as `捕获当前回复` or `回收当前结果` may resolve the current target from the latest `leader_review` only when that review is `wait_for_reply`; in that case the response should include the review and plan_id while still returning the same explicit `capture-reply` command. Unknown message ids must fail with `unknown trace id: <id>` rather than falling through to provider-backed planning.

Trace-mode responses are returned when the human asks to inspect one concrete communication id such as `msg_xxx`, `att_xxx`, `job_xxx`, `rep_xxx`, `art_xxx`, or `inb_xxx`. They return `trace_card`, reusing the same shape as `agentdeck trace --id <id>`:

```json
{
  "mode": "trace",
  "next_command": "agentdeck trace --id msg_xxx",
  "trace_card": {
    "query_id": "msg_xxx",
    "message": {},
    "attempts": [],
    "jobs": [],
    "replies": [],
    "artifacts": [],
    "inbox_items": []
  }
}
```

When `trace_card` is present, `validate_leader_chat_contract()` reuses `validate_trace_contract()` and prefixes nested errors with `trace_card:`. Trace-mode records a chat turn for history, but it must not create plans/actions/approvals/messages/jobs/inbox items, acknowledge inbox items, dispatch work, capture replies, read pane output, or send tmux input. Trace intent next labels must be action-specific so GUI shells can render communication lineage controls without command parsing. Unknown trace ids must fail with `unknown trace id: <id>` rather than falling through to provider-backed planning.

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

Natural-language role assignment, such as `把 planner 设为 架构师` or `set reviewer role to QA`, also returns `mode=role`; however, `next_command` is a concrete `agentdeck agent assign-role --agent <id> --role <role> --role-prompt <prompt>` command, `leader_explanation.action_kind` is `role_assign`, and the `intent_card.controls[]` next control uses label `Assign role`, `safety=explicit_user`, and `requires_explicit_user=true`. This intent suggests the explicit command only; it must not mutate role config automatically.

Each role agent may include disabled `controls[]` with `kind=assign_role`, pointing at `agentdeck agent assign-role --agent <agent_id> --role <role> --role-prompt <role_prompt>`. GUI clients should render this as an explicit role-edit form and keep it disabled until concrete `role` and `role_prompt` values replace the placeholders. Role-mode must not fill placeholders or execute the command automatically.

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

When `queue_card` or `operator_card` is present, `validate_leader_chat_contract()` checks the same field lists exposed by `agentdeck contract workbench` and requires `next_command` to match both cards' `next_command`. Queue-mode aligns `next_command` with the operator's primary command, so when the operator promotes multiple approved approvals to `action_kind=approval_dispatch_ready`, the natural-language response also recommends `agentdeck approval dispatch-ready --confirm`; the embedded `operator_card.controls[]` must expose the batch action as `kind=dispatch_ready` with label `Dispatch ready approvals`. Queue-mode also includes a filtered `control_registry_card` for `scope=operator` and `card=operator_card`; its `selection.selected_control` points at the operator control whose command matches the top-level `next_command`, and `selection.next_command` must match the same top-level `next_command` when that control is enabled. The validator rejects queue-mode payloads whose registry selection points at a different enabled operator control. `intent_card.secondary_embedded_cards[]` lists `control_registry_card` so GUI clients can render the command palette beside the queue/operator cards without treating it as another state source. Queue-mode records a chat turn for history, but it must not create or apply Leader actions, approve/reject/dispatch work, acknowledge inbox items, refresh runtime, or send tmux input.

Inbox-mode responses include `inbox_card`, which reuses the same queue shape as `agentdeck inbox --agent <id>`. The `<id>` may be a configured worker agent id or the logical Leader mailbox owner `leader`; the Leader mailbox is for worker replies flowing back to the Leader and must not be treated as a tmux/runtime agent. If the human asks for `当前 inbox` / `current inbox` without naming an agent, chat may resolve the mailbox from ProjectView recovery only when `recovery.recommended_action.source=inbox`; otherwise it must not guess a target agent:

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

When the inbox message asks to `trace`, `追踪`, `溯源`, or inspect `lineage`, the response may also include `trace_card`, which reuses the same shape as `agentdeck trace --id <id>` for the current pending inbox head:

```json
{
  "query_id": "inb_xxx",
  "message": {},
  "attempts": [],
  "jobs": [],
  "replies": [],
  "inbox_items": []
}
```

When `inbox_card` is present, `validate_leader_chat_contract()` reuses `validate_inbox_contract()` and prefixes nested errors with `inbox_card:`. When `trace_card` is present, the validator reuses `validate_trace_contract()` and prefixes nested errors with `trace_card:`. Inbox-mode is read-only: it may recommend `agentdeck inbox --agent <id>`, `agentdeck trace --id <inbox_id>`, or the head item `ack_command`, but it must not execute ack, dispatch work, capture replies, or send tmux input. Natural-language requests such as `查看 leader inbox` and `确认 leader 当前 inbox` may target the logical Leader mailbox and may recommend `agentdeck inbox --agent leader` or `agentdeck ack --agent leader --inbox-id <id>`. Requests such as `查看当前 inbox` or `确认当前 inbox` may target the current recovery inbox and recommend the matching worker or Leader mailbox command; they still only record a chat turn and require the human to run the explicit command. Inbox ack intent next labels must be action-specific so GUI shells can render acknowledgement controls without command parsing. A trace intent should set `intent_card.embedded_card` to `trace_card` when the card is available, so GUI and natural-language shells can render the actual communication evidence rather than only a command string.

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

When `approval_card` is present, `validate_leader_chat_contract()` reuses `validate_approval_contract()` and prefixes nested errors with `approval_card:`. Approval-mode normally stays read-only: it may recommend `agentdeck approval list`, the first pending approval's `approve_command`, the first pending approval's `reject_command`, or the first approved approval's `dispatch_command`, but it must not approve, reject, dispatch work, or send tmux input. Apply-action mode may also embed `approval_card` after a safe `create_approvals` action succeeds, so the same response can show the newly created human approval queue without approving or dispatching it.

Natural-language task assignment, such as `让 planner 规划 README 更新` or `ask reviewer to review docs`, also returns `mode=approval`. In this case AgentDeck creates exactly one pending approval with `source=leader_chat_task_assignment`, embeds the refreshed `approval_card`, sets `leader_explanation.action_kind` to `approval_create`, and points `next_command` at the new approval's `approve_command`. The response's `intent_card.read_only` is `false` because the approval queue changed, but runtime work is still gated: it must not approve, dispatch, create messages/jobs/inbox items, or send tmux input.

When approval-mode recommends dispatching an already approved item, it may also include `dispatch_preview_card`, a GUI-ready explicit-runtime preview:

```json
{
  "approval_id": "apv_xxx",
  "agent_id": "planner",
  "agent_role": "planning",
  "pane_id": "%42",
  "runtime_status": "running",
  "task": "Break down the goal",
  "dispatch_command": "agentdeck approval dispatch --approval-id apv_xxx",
  "approval_command": "agentdeck approval list",
  "inbox_command": "agentdeck inbox --agent planner",
  "requires_explicit_user": true,
  "safety": "explicit_runtime",
  "blocker": null,
  "controls": [
    {
      "kind": "inspect",
      "label": "Inspect approval",
      "command": "agentdeck approval list",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    },
    {
      "kind": "dispatch",
      "label": "Dispatch approval",
      "command": "agentdeck approval dispatch --approval-id apv_xxx",
      "safety": "explicit_runtime",
      "enabled": true,
      "blocker": null
    }
  ]
}
```

`dispatch_preview_card` is not dispatch execution. It is an execution-before-confirmation surface for humans and GUI clients: it shows the target agent, role, pane, task, dispatch command, mailbox command, and renderable `controls[]` before the human runs the explicit command. If runtime is missing, `blocker` should explain why the explicit command is not ready, and the card's dispatch control, the filtered `control_registry_card.selection.selected_control`, and the `intent_card.controls[]` `next` control must be disabled with the same blocker. Live approval-dispatch responses must also include `approval_card` and a `control_registry_card` filtered to `dispatch_preview_card`; the registry selection should point at the top-level dispatch control so GUI clients can render the same explicit command from the preview card, the intent card, or the command palette without parsing labels.

When approval-mode recommends dispatching all approved items, it may include `dispatch_batch_preview_card` instead of a single `dispatch_preview_card`:

```json
{
  "mode": "dispatch_batch_preview",
  "approval_command": "agentdeck approval list",
  "dispatch_ready_command": "agentdeck approval dispatch-ready --confirm",
  "count": 2,
  "ready_count": 1,
  "blocked_count": 1,
  "items": [],
  "requires_explicit_user": true,
  "safety": "explicit_runtime",
  "blocker": "some dispatch targets are blocked",
  "controls": [
    {
      "kind": "inspect",
      "label": "Inspect approvals",
      "command": "agentdeck approval list",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    },
    {
      "kind": "dispatch_ready",
      "label": "Dispatch ready approvals",
      "command": "agentdeck approval dispatch-ready --confirm",
      "safety": "explicit_runtime",
      "enabled": true,
      "blocker": null
    }
  ]
}
```

`items[]` must reuse `dispatch_preview_card_fields`, including per-item `controls[]`. The batch card does not execute or imply automatic fan-out; it is a GUI-ready checklist of explicit dispatch commands. The top-level `next_command` may point to `agentdeck approval dispatch-ready --confirm`, which is still an explicit human command and is not run by chat. Live batch-dispatch responses must also include `approval_card` and a `control_registry_card` filtered to `dispatch_batch_preview_card`; the registry selection should point at the top-level `dispatch_ready` control. `validate_leader_chat_contract()` checks that `count`, `ready_count`, and `blocked_count` match the item list, that item-level dispatch controls plus the batch-level `dispatch_ready` control match their command, safety, enabled state, and blocker, and that approval dispatch responses list `approval_card` plus `control_registry_card` in `intent_card.secondary_embedded_cards`.

Setup-mode responses are returned when the human asks to inspect `doctor`, provider setup, API key, local environment readiness, or asks to switch the Leader provider. They are read-only and do not call the configured Leader provider:

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
    "provider_backend": "api",
    "provider_transport": "http",
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

`provider_health` is a GUI-ready convenience field for setup-mode responses and a top-level leader-chat response field; non-setup modes may leave it as `null`. `agentdeck contract leader-chat` exposes `provider_health_fields`, reusing the workbench provider health field list, so GUI clients can discover `provider_backend`, `provider_transport`, and normalized `leader_backend` without parsing the workbench schema. The card never exposes API key values. `provider_health.leader_backend` mirrors the logical Leader identity used by workbench/provider diagnostics and is provenance only; it does not prove readiness, bind a tmux pane, or authorize execution. `doctor_contract` points GUI clients to the doctor diagnostics schema, while `setup_commands` must only contain placeholder commands that a human can copy and edit outside AgentDeck. Provider switch controls in the mirrored card must remain `kind=set_provider` with `safety=explicit_user` and a concrete `agentdeck leader set-provider --provider ...` command; disabled provider controls must include a blocker. When setup-mode is a provider setup intent such as `配置 Codex CLI Leader`, `登录 Codex CLI`, or `安装 Claude CLI`, `next_command` must be the first allowlisted setup command for the target provider, `leader_explanation.action_kind` is `provider_setup`, `safety` is `explicit_user`, and `requires_explicit_user=true`; the response also includes a structured `provider_setup_card`, a `control_registry_card` filtered to `scope=provider`, the provider query, and the recommended setup control id so `selection.selected_control.command` and `selection.next_command` match `next_command`, plus a target `provider_switch_card` whose command shows the follow-up explicit provider switch. `provider_setup_card` exposes target provider/model, setup commands, recommended command/control id, follow-up switch command, require-ready state, and explicit_user controls, but it never authorizes execution by itself. Live `provider_setup` responses must include `provider_setup_card`, `provider_switch_card`, and `control_registry_card`, and `intent_card.secondary_embedded_cards` must list all three so GUI clients can discover them from the intent surface. For live `provider_setup` responses, `provider_setup_card.recommended_command` must equal the top-level `next_command`; its `recommended_control_id` must point to the `setup_provider` control whose `command` equals `recommended_command`, and it must match `control_registry_card.selection.requested_control_id`, so GUI clients highlight the same setup action that the top-level `next_command` recommends. When both setup and switch cards are present, `provider_setup_card.target_provider`, `target_model`, `require_ready`, and `followup_switch_command` must match the corresponding `provider_switch_card` target and command fields so GUI clients never render contradictory setup and switch state from one response. If the setup text includes require-ready wording such as `必须可用` or `先预检`, the follow-up `provider_switch_card` must use `require_ready=true`, append `--require-ready` to its command, and disable the guarded switch control when target readiness is false while still exposing setup controls from `target_readiness.setup_commands`. Provider-setup intent cards must include `provider_setup_card`, `provider_switch_card`, and `control_registry_card` in `intent_card.secondary_embedded_cards`. When setup-mode is a provider switch intent, `next_command` may be a concrete `agentdeck leader set-provider --provider <provider> --model <model>` command, `leader_explanation.action_kind` is `provider_switch`, `safety` is `explicit_user`, and `requires_explicit_user=true`; the response also includes `provider_switch_card`, a structured confirmation card for GUI clients. The response still only suggests the command and must not mutate `.agentdeck/config.toml`. Setup-mode records a chat turn for history, but it must not create a plan, leader action, approval, message, job, inbox item, or tmux input.

`provider_switch_card` is present when setup mode is a provider-switch intent:

```json
{
  "mode": "provider_switch",
  "title": "Switch Leader provider",
  "current_provider": "deepseek",
  "current_model": "deepseek-chat",
  "target_provider": "codex-cli",
  "target_model": "codex-default",
  "target_leader_backend": {
    "agent_id": "leader",
    "provider": "codex-cli",
    "model": "codex-default",
    "provider_backend": "cli",
    "provider_transport": "subprocess",
    "reasoning_backend": "cli-subprocess",
    "runtime_kind": "logical_leader",
    "pane_backed": false,
    "pane_id": null,
    "approval_required": true,
    "dispatch_ready": false
  },
  "target_readiness": {
    "provider": "codex-cli",
    "ready": false,
    "supported": true,
    "missing_env": [],
    "detail": "codex is not found on PATH",
    "command_path": null
  },
  "require_ready": false,
  "command": "agentdeck leader set-provider --provider codex-cli --model codex-default",
  "diagnostics_command": "agentdeck doctor",
  "safety": "explicit_user",
  "requires_explicit_user": true,
  "mutates_config": false,
  "controls": []
}
```

`target_readiness` uses the same readiness shape as `agentdeck doctor` configured Leader diagnostics and the same readiness check used by `agentdeck leader set-provider --require-ready`. Its `provider` and `model` must match `target_provider` and `target_model`, and its nested `leader_backend` must match `target_leader_backend`, so GUI clients can trust that readiness and backend identity describe the same target Leader. Controls must also stay aligned: the inspect control points at `diagnostics_command`, and the provider switch control uses `kind=set_provider` normally or `kind=guarded_set_provider` when `require_ready=true`. When `require_ready=true` and `target_readiness.ready=false`, the guarded provider control must be disabled and use blocker `target provider is not ready`, and the card must include `kind=setup` controls whose commands mirror `target_readiness.setup_commands`, so GUI clients can render the suggested command without implying that the guarded switch is currently executable and still show the human the next repair commands. `mutates_config=false` means this card is a confirmation surface only; the config changes only after a human explicitly runs `command`. Provider-switch intent cards keep `embedded_card=provider_health` for the current configured provider and must include `provider_switch_card` in `secondary_embedded_cards` so GUI clients can render both current setup diagnostics and the target switch confirmation from one response.

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

`leader_explanation.next_command` must match the top-level response `next_command`, so GUI clients can render the explanation and primary action without reconciling two different recommendations. `leader_explanation.requires_explicit_user` must match `intent_card.requires_explicit_user`, keeping the explanation surface and control surface aligned on whether the human must explicitly run the suggested command. `safety=plan_only` means the Leader only created a plan record. `safety=safe_apply` means the action can be applied through `agentdeck leader apply-action`. `safety=explicit_runtime` means the user must run the explicit command, such as dispatch, capture-reply, inbox ack, approval approve, or approval dispatch. `safety=safe_apply_completed` means a safe apply action already completed and the response may include `result_count`. `safety=inspect` means the response is only recommending or performing a read-only inspection command such as pane capture.
`safety=approval_gated` means the Leader created a plan plus pending approvals, but runtime execution still waits for explicit human approval and dispatch.

## Boundaries

- The contract command is read-only.
- Chat responses must not auto-dispatch runtime work.
- Chat responses must pass `validate_leader_chat_contract()` before printing JSON.
- Chat response contract failures must be auditable through ProjectView `leader_errors` and `agentdeck events`.
- Chat responses must include `intent_card`, and `intent_card.next_command` must describe the same next action as the top-level response.
- Chat responses with a top-level `leader_action` must expose a matching `leader_action_card`; `leader_action_card.action_id` must match `leader_action.action_id`.
- Chat summary-mode responses must embed `leader_summary_card`, reusing `agentdeck leader summary --plan-id <id>` and `validate_leader_summary_contract()`; they must not create Leader actions or call the provider.
- Chat run-start responses must embed `run_start_card`, reusing the `agentdeck run --task <text>` response shape and `validate_run_start_contract()`. Natural-language run intents such as `开始运行 <goal>` may create a plan and pending approvals, but must not create Leader actions, auto-approve, dispatch, capture pane output, acknowledge inbox items, create message/job/inbox runtime records, or send tmux input.
- Chat run-progress responses must embed `run_progress_card`, reusing the `agentdeck run --plan-id <id>` response shape and `validate_run_start_contract()`. Natural-language progress intents such as `查看运行进度` use the latest plan by default; `查看运行进度 pln_xxx` inspects the specified plan. If no plan exists, the command must fail without creating a plan, action, approval, message, job, inbox item, or chat turn. Successful run-progress responses are read-only: they may record a chat turn, but must not mutate plan/approval/runtime state, create Leader actions, dispatch, capture pane output, acknowledge inbox items, create message/job/inbox runtime records, or send tmux input.
- Chat intent controls must include `kind`, `label`, `command`, `safety`, `enabled`, and `blocker`; `validate_leader_chat_contract()` rejects disabled controls without a blocker, rejects `kind=inspect` controls unless `safety=inspect`, and rejects enabled placeholder commands or placeholder blockers that do not match.
- Chat help-mode responses must include `capability_card`; `run_start` capability entries should point at `agentdeck leader chat --message "开始运行 <goal>"` and use `safety=approval_gated`. `validate_leader_chat_contract()` rejects capability cards whose `capability_count` does not match `capabilities[]`, rejects capability items or controls with missing fields, rejects capability controls whose `command` or `safety` drift from the parent capability item, rejects placeholder capability controls that use unknown placeholders, are enabled, or use the wrong blocker, rejects disabled capability controls without blockers, and rejects `plan`, `review`, or `apply_action` entries whose safety does not match their scheduling semantics.
- Chat inbox-mode responses must reuse the `agentdeck inbox` queue contract through `inbox_card`.
- Chat inbox trace responses may embed `trace_card`, reusing the `agentdeck trace` contract for the current pending inbox head.
- Chat direct trace responses must embed `trace_card`, reusing the `agentdeck trace` contract for the requested communication id.
- Chat capture-mode responses must embed `capture_card`, using the leader-chat `capture_card_fields` for the requested visible agent pane, and use an action-specific next label for recapturing visible output.
- Chat capture-reply responses may embed `trace_card` instead of `capture_card`; explicit `msg_xxx` requests use the referenced message, while current/latest reply requests may resolve from a latest `wait_for_reply` review and must not guess when no such review exists.
- Chat terminal-mode responses must embed `terminal_card`, using the leader-chat `terminal_card_fields` for the requested visible agent pane, and use `Open terminal` as the action-specific next label.
- Chat approval-mode responses and safe apply-action responses that create approvals must reuse the `agentdeck approval list` queue contract through `approval_card`.
- Chat approval dispatch recommendations may embed `dispatch_preview_card`, using `dispatch_preview_card_fields` to show the explicit runtime command target before any dispatch runs; they must also include `approval_card`, include a `control_registry_card` filtered to `dispatch_preview_card`, list `approval_card` / `control_registry_card` in `intent_card.secondary_embedded_cards`, and select the dispatch control in `control_registry_card`; when `dispatch_preview_card.blocker` is set, the preview dispatch control, registry selected control, and intent next control must be disabled with the same blocker. Batch approval dispatch recommendations may embed `dispatch_batch_preview_card`, using `dispatch_batch_preview_card_fields` and `dispatch_batch_preview_item_fields` to show all approved items with per-item runtime blockers before any dispatch runs; they must include a `control_registry_card` filtered to `dispatch_batch_preview_card`, list `approval_card` / `control_registry_card` in `intent_card.secondary_embedded_cards`, and select the dispatch_ready control in `control_registry_card`. Approval approve, reject, and dispatch intent next labels must be action-specific so GUI shells can render human approval controls without command parsing.
- Chat runtime-mode responses must reuse the workbench runtime card through `runtime_card` and expose a same-snapshot top-level `terminal_session_card` for the project terminal strip. Plain runtime inspection keeps `intent_card.embedded_card=runtime_card` and sets `intent_card.secondary_embedded_cards=["terminal_session_card"]`, then recommends `agentdeck agent list` with `safety=inspect`; explicit terminal-open intents such as `打开 planner 终端` must use terminal-mode and `terminal_card`; explicit refresh intents such as `刷新 runtime` may recommend `agentdeck agent refresh` with `safety=explicit_runtime` and `requires_explicit_user=true`, must embed project-level `runtime_action_card`, must list `runtime_card` / `terminal_session_card` / `control_registry_card` in `intent_card.secondary_embedded_cards`, and must select the refresh_runtime control in `control_registry_card`; multi-agent startup intents such as `启动所有 agent` must embed `agent_ready_card`, include `startup_preview_card`, list `startup_preview_card` / `runtime_card` / `terminal_session_card` / `control_registry_card` in `intent_card.secondary_embedded_cards`, and recommend that card's explicit next command, including `agentdeck agent spawn-ready --confirm` when multiple agents are not running; explicit spawn intents such as `启动 planner` may recommend `agentdeck agent spawn --agent <id>` with `safety=explicit_runtime` and `requires_explicit_user=true`, must embed `runtime_action_card`, include a single-agent `startup_preview_card`, include a `control_registry_card` filtered to `runtime_action_card`, list `runtime_card` / `startup_preview_card` / `terminal_session_card` / `control_registry_card` in `intent_card.secondary_embedded_cards`, and select the spawn control in `control_registry_card`; explicit send intents such as `发送给 planner：继续` may recommend `agentdeck agent send --agent <id> --text <text>` only when the target agent has a running pane, must embed `runtime_action_card`, must list `runtime_card` / `terminal_session_card` / `control_registry_card` in `intent_card.secondary_embedded_cards`, and must select the send control in `control_registry_card`; explicit stop intents such as `停止 planner` may recommend `agentdeck agent stop --agent <id>` only when the target agent has a running pane, must embed `runtime_action_card`, must list `runtime_card` / `terminal_session_card` / `control_registry_card` in `intent_card.secondary_embedded_cards`, and must select the stop control in `control_registry_card`. Runtime explicit `intent_card.controls[]` next labels must be action-specific so GUI shells can render controls without command parsing. Runtime-mode must not attach tmux, select panes, spawn panes, refresh runtime, stop panes, read pane output, send tmux input, or fall back to provider-backed planning when a targeted runtime command cannot run.
- Chat queue-mode responses must reuse the workbench queue and operator cards through `queue_card` and `operator_card`, and must align the top-level `next_command` with the operator card's primary command.
- Chat role-mode responses must reuse the workbench role card through `role_card`.
- Chat ledger-mode responses must reuse the workbench ledger and lineage cards through `ledger_card` and `lineage_card`.
- Chat audit-mode responses must reuse the workbench audit card through `audit_card`, recommend `agentdeck events --limit 20`, and remain read-only except for recording the chat turn and its audit event.
- Chat artifacts-mode responses must reuse the artifacts contract through `artifacts_card`, recommend `agentdeck artifacts`, and remain read-only without reading artifact file contents.
- Chat workbench-mode responses must reuse the complete workbench snapshot through `workbench_card`; the leader-chat contract must expose terminal session card fields for the embedded `workbench_card.terminal_session_card` and `workbench_control_registry_item_fields` for the embedded `workbench_card.control_registry[]` command palette.
- Chat policy-mode responses must reuse the workbench control mode projection through `control_mode_card`, recommend an explicit `agentdeck policy set-mode --mode <mode>` command, and use action-specific next labels for ask, approval, and autonomous requests. Policy-mode must not mutate `.agentdeck/config.toml`.
- Chat continue-mode responses may embed `inbox_card`, `approval_card`, `runtime_card`, `terminal_session_card`, or `trace_card` when `recovery.recommended_action.source` points at those queues, runtime recovery, or a pending reply capture; stale runtime recovery should keep `intent_card.embedded_card=continue_card` and list `runtime_card` / `terminal_session_card` in `secondary_embedded_cards`; reply capture recovery should set `intent_card.embedded_card=trace_card`.
- Chat setup-mode responses include `provider_health` with the same normalized `leader_backend` provenance as workbench; diagnostics intents recommend `agentdeck doctor`, provider setup intents such as `配置 Codex CLI Leader` recommend a concrete allowlisted setup command such as `codex login` and include `provider_setup_card`, a provider-filtered `control_registry_card`, and a target `provider_switch_card`, while provider switch intents recommend a concrete `agentdeck leader set-provider ...` command and include `provider_switch_card` for target readiness, target backend identity, require-ready state, and explicit controls. Provider-setup `intent_card.secondary_embedded_cards` must include `provider_setup_card`, `provider_switch_card`, and `control_registry_card`; provider-switch `intent_card.secondary_embedded_cards` must include `provider_switch_card`. These forms do not call the provider or mutate provider config. Non-setup responses may keep `provider_health=null`, `provider_setup_card=null`, `control_registry_card=null`, and `provider_switch_card=null`.
- Runtime actions still require explicit commands or approval flow.
- GUI clients should treat `project_view` and `leader_actions` as state, `intent_card` as the natural-language routing explanation and next-command control source, `capability_card` as the command discovery surface, `continue_card` as a recovery affordance, `run_start_card` as the approval-gated run start surface, `run_progress_card` as the read-only run progress surface, `leader_summary_card` as the deterministic reply/artifact summary surface, `capture_card` as the selected visible pane snapshot surface, `dispatch_preview_card` as the explicit dispatch confirmation preview, `dispatch_batch_preview_card` as the multi-approval dispatch checklist, `startup_preview_card` as the explicit multi-agent/single-agent startup checklist, `runtime_action_card` as the explicit runtime spawn/refresh/send/stop confirmation surface, `provider_setup_card` as the structured Leader provider setup checklist, `provider_switch_card` as the explicit Leader provider switch confirmation surface or setup follow-up switch surface, `agent_ready_card` as the multi-agent runtime readiness/startup surface, `inbox_card` as the mailbox queue surface, `trace_card` as the selected inbox/message lineage evidence surface, `approval_card` as the human approval queue surface, `runtime_card` as the visible tmux runtime surface, `role_card` as the role assignment surface, `ledger_card` as the communication ledger surface, `lineage_card` as the communication path surface, `audit_card` as the recent audit timeline surface, `artifacts_card` as the read-only artifact index surface, `workbench_card` as the full dashboard snapshot, `control_mode_card` as the explicit control-mode policy surface, `control_registry_card` as the filtered command palette surface, `workbench_card.control_registry[]` as the full-dashboard command palette index, `queue_card` as the queue status surface, `operator_card` as the explicit control surface, setup-mode `provider_health` as provider diagnostics and provider switch command context, and `leader_explanation` as safety/reason explanation.
