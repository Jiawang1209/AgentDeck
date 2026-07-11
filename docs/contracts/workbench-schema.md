# Workbench Snapshot Contract

`mission_card` is `null` when ProjectView has no Mission; otherwise it is derived only from `project_view.missions.latest_id/items` plus the safe configured tmux session name. It reuses the Mission status projection and adds the canonical natural-language `confirmation_command`. Its five controls expose confirm, resume, status, attach, and workbench actions; confirm is enabled only for an unblocked pending Mission, resume only for an unblocked stopped/interrupted Mission. Rendering stays read-only: it does not inspect tmux, invoke a provider, execute a command, append an event, or mutate state.

The independent stable field list is exposed as `mission_card_fields`; it is the Mission status field tuple plus `confirmation_command`. A disabled confirmation uses the first compact Mission blocker when one exists, falling back to the current status only when there is no blocker. This preserves actionable, sanitized readiness evidence without accepting nested blocker values.

When `mission_card` is non-null, `validate_workbench_contract()` requires `project_view.missions.latest_id` to identify an item and compares every shared compact core field against that latest item, including identity, lifecycle, blockers, plan/workflow provenance, progress, selected Workers, timestamps, resumability, and canonical commands. Drift errors name only the field and never echo either value. A ProjectView with no Mission must keep `mission_card=null`; a ProjectView with a latest Mission must include the aligned card. The discovery example is constructed from the same Mission facts on both surfaces.

Provider/model/Leader-backend provenance remains on the ProjectView Mission item; it is not duplicated into `mission_card`. The card validator compares only fields actually present on both compact surfaces.

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
  "agent_ready_card_fields": [],
  "terminal_session_card_fields": [],
  "terminal_session_control_fields": [],
  "terminal_session_item_fields": [],
  "runtime_agent_fields": [],
  "runtime_control_fields": [],
  "role_card_fields": [],
  "role_agent_fields": [],
  "worker_lifecycle_card_fields": [],
  "worker_lifecycle_item_fields": [],
  "review_gate_card_fields": [],
  "review_gate_stage_fields": [],
  "release_preview_card_fields": [],
  "role_topology_card_fields": [],
  "role_topology_item_fields": [],
  "ledger_card_fields": [],
  "lineage_card_fields": [],
  "lineage_path_fields": [],
  "queue_card_fields": [],
  "operator_card_fields": [],
  "audit_card_fields": [],
  "artifacts_card_fields": [],
  "artifact_summary_fields": [],
  "artifact_item_fields": [],
  "skill_context_card_fields": [],
  "skill_suggestions_card_fields": [],
  "memory_context_card_fields": [],
  "memory_suggestions_card_fields": [],
  "skill_context_item_fields": [],
  "leader_summary_card_fields": [],
  "learning_review_card_fields": [],
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
  "agent_ready_card": {},
  "terminal_session_card": {},
  "role_card": {},
  "worker_lifecycle_card": {},
  "review_gate_card": {},
  "release_preview_card": {},
  "role_topology_card": {},
  "ledger_card": {},
  "lineage_card": {},
  "queue_card": {},
  "operator_card": {},
  "audit_card": {},
  "artifacts_card": {},
  "skill_context_card": {},
  "skill_suggestions_card": {},
  "memory_context_card": {},
  "memory_suggestions_card": {},
  "leader_summary_card": null,
  "learning_review_card": null,
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
`agent_ready_card` reuses the `agentdeck agent ready` response shape, is derived from the same `runtime_card`, and must pass the agent runtime ready card validator. Its `controls[]` expose readiness inspect, startup/dispatch next action, and runtime refresh commands for GUI clients.
`terminal_session_card` is derived from the same `runtime_card` and project tmux config so GUI/TUI clients can render a project terminal strip without calling each agent terminal command.
`role_card` is derived from `project_view.agents[]` role configuration.
`worker_lifecycle_card` is derived from `project_view.agents[]`, visible runtime status, messages, jobs, replies, artifacts, and inbox summary; it is a task-level status projection for worker roles and does not spawn, dispatch, capture, ack, release, or write state.
`review_gate_card` is derived from artifacts, reviewer replies, and configured reviewer roles; it exposes code-review and round-review readiness without releasing work, merging changes, acknowledging inbox items, dispatching follow-up work, or advancing the loop.
`release_preview_card` is derived from `review_gate_card` and the ProjectView `releases` summary; it stays blocked with the same reason until the review gate is ready. While blocked, `next_command` / `release_command` / `next_round_command` stay `null` and the release / next-round controls are disabled `explicit_user` controls without commands. Once the gate is ready, `release_command` and `next_command` must point at the explicit `agentdeck release --confirm` command, the `release_preview` control becomes an enabled `explicit_user` control with the same command, and `next_round_command` exposes the disabled `agentdeck leader plan --task <goal>` template (blocker `requires goal text`). When the current reply pair was already released, the card reports `status=released` / `already_released=true` with reason `round already released` and withdraws the release command, leaving the next-round plan template as the follow-up. Rendering the card never executes the release; only a human running `agentdeck release --confirm` records the round release.
`role_topology_card` is derived from `leader.coordination_roles[]` and the same `worker_lifecycle_card` items; it is the read-only Phase G6 role topology that unifies logical Leader roles (`frontdesk`, `planner`, `orchestrator`) and configured worker roles with per-role provider, lifecycle, status, blocker, and an inspect-only next-step control. Logical roles stay `runtime_kind=logical_role` / `pane_backed=false` / `pane_id=null` / `agent_id=null`; worker roles use `runtime_kind=worker_pane` and reuse the worker `lifecycle_stage`, except a worker whose `agent_id` owns a `review_gate_card` stage gets a review-aware `status`/`blocker` overlay (`reviewed` / `reviewing` / `blocked`). Rendering it never spawns, dispatches, captures, acks, releases, or writes state.
`ledger_card` is derived from `project_view.messages`, `project_view.jobs`, `project_view.replies`, `project_view.artifacts`, and `project_view.inbox`; its `messages.items[]` retains compact worker `prompt_skill_context` so GUI clients can render loaded skill provenance without parsing prompt text or storing full skill snapshots. Its `controls[]` expose the read-only workbench ledger entry for GUI command palettes.
`lineage_card` is a read-only path projection derived from the same ledger summaries plus visible inbox cards.
`queue_card` is derived from `project_view.leader_actions`, `project_view.approvals`, `project_view.inbox`, and the recovery-driven next command.
`operator_card` is derived from `recovery.recommended_action` and the active queue card. It is a renderable human-control descriptor, not an execution result.
`audit_card` is derived from `recovery.latest_event` and `recovery.recent_events`.
`artifacts_card` reuses the `agentdeck artifacts` response shape, is derived from `project_view.artifacts`, and must pass `validate_artifacts_contract()`.
`skill_context_card` is derived from `project_view.skills`; it exposes loaded skill summaries plus inspect controls for `agentdeck skills list` and `agentdeck status`, without embedding full content snapshots or loading/installing skills.
`skill_suggestions_card` is derived from pending `skill_suggestions[]`; it exposes count, pending_count, items, and inspect controls for `agentdeck skills suggestions` and `agentdeck status`, without creating, importing, or loading skills.
`memory_context_card` is derived from `project_view.memory`; it exposes applied long-term memory file summaries plus inspect controls for `agentdeck status` and `agentdeck memory suggestions`, without embedding full memory content, mutating memory files, calling a provider, or injecting memory into prompts.
`memory_suggestions_card` is derived from pending `memory_suggestions[]`; it exposes count, pending_count, items, `apply_preview_command_template`, item-level `apply_preview` / `apply_memory` controls, and inspect controls for `agentdeck memory suggestions` and `agentdeck status`. Rendering the card is read-only: it must not create or modify `.agentdeck/memory/*.md` or inject memory into prompts. Only a separate explicit `agentdeck memory apply --suggestion-id <id> --confirm` command may write long-term memory.
`leader_summary_card` is `null` until the latest plan's local Leader review returns `next_action=summarize`; then it reuses `agentdeck leader summary --plan-id <id>` and must pass `validate_leader_summary_contract()`.
`learning_review_card` is `null` under the same gate (latest plan review `next_action=summarize`); then it reuses the `agentdeck learn review --plan-id <id>` response shape and must pass `validate_learning_review_contract()`. It is the read-only Hermes-style learning reviewer: it surfaces suggested `agentdeck skills suggest ... --source learn-review` and `agentdeck memory suggest ... --source learn-review` follow-up commands (its `summary` / `suggest_skill` / `suggest_memory` controls enter `control_registry[]` under `scope=learning_review`), but rendering the workbench never writes `skill_suggestions[]` / `memory_suggestions[]`, calls a provider, or creates/loads skills — the explicit suggestion commands remain the only write path into the pending queues.
`contracts_card` is the stable pointer to contract discovery surfaces and the local contract index schema, including the run start, Skill Registry, memory suggestion/apply, ACP runtime preflight, Leader chat, and Leader review contracts. The flattened registry adds an `acp_runtime` group: enabled inspect entries for protocol status and ACP contract discovery, a disabled preflight template until a concrete ACP agent identity is supplied, and disabled `explicit_user` run/load/resume templates until their concrete identities, prompt/capability requirements, readiness, and confirmation are present. These controls are render metadata, not authorization tokens.
`recovery` must equal `project_view.recovery`.
`continue_card` must pass `validate_continue_contract()`.
`run_progress_card` is `null` when there is no plan; otherwise it reuses the latest plan's `agentdeck run --plan-id <id>` response shape and must pass `validate_run_start_contract()`. Its `leader_backend` field is the same normalized logical Leader identity card stored with the plan; it is not a tmux pane binding or execution permission.
`plan_board_card` is always present (never `null`). It reuses the read-only `agentdeck plan board` payload shape (`mode`, `board_command`, `plan_count`, `active_count`, `plans[]`) and must pass `validate_plan_board_contract()`. It is a multi-plan overview: every plan with its derived `gate` and explicit per-plan `next_command`. Empty projects render `plan_count=0` / `plans=[]`. Read-only aggregation only — rendering it never calls a provider, reads tmux, writes state, or appends events.
`skills_catalog_card` is always present (never `null`). It is a read-only overview of the configured skill sources (config `[skills] allowed_sources`) — no argument needed. Fields: `mode="skills_catalog"`, `source_count`, `total_skill_count` (sum of skills across existing sources), `imported_count` (sum of catalog items whose `import_status != not_imported`, compared against project `.agentdeck/skills/` via `discover_skills`), and `sources[]` where each item is `{path, exists, skill_count, imported_count, catalog_command}` and `catalog_command = agentdeck skills catalog --source <path>`. With no configured sources it renders `{mode:"skills_catalog", source_count:0, total_skill_count:0, imported_count:0, sources:[]}`. Read-only overview only — rendering it never copies files, imports/loads skills, calls a provider, reads tmux, writes state, or appends events. Discovery exposes `skills_catalog_card_fields` and `skills_catalog_source_fields`.
`next_command` must equal `continue_card.next_command`.
`change_summary` is computed from the audit event ledger and is never persisted as a cursor.

## Agent Ready Card

`agent_ready_card` is the workbench startup/readiness projection for multi-agent runtime state:

```json
{
  "ok": true,
  "mode": "agent_runtime_ready",
  "runtime_backend": "tmux",
  "total_count": 3,
  "running_count": 1,
  "not_running_count": 2,
  "all_running": false,
  "next_command": "agentdeck agent spawn-ready --confirm",
  "spawn_commands": [
    "agentdeck agent spawn --agent coder",
    "agentdeck agent spawn --agent reviewer"
  ],
  "spawn_ready_command": "agentdeck agent spawn-ready --confirm",
  "refresh_command": "agentdeck agent refresh",
  "dispatch_ready_command": "agentdeck approval dispatch-ready --confirm",
  "runtime_card": {}
}
```

The embedded `runtime_card` must match the same runtime projection shown at the top level. GUI/TUI clients can render `next_command` directly: multiple not-running agents use `agentdeck agent spawn-ready --confirm`, one not-running agent uses that agent's explicit spawn command, and an all-running workspace uses `agentdeck approval dispatch-ready --confirm`. Workbench only computes this card from ProjectView/runtime status; it must not inspect tmux, spawn panes, refresh runtime bindings, dispatch approvals, capture pane output, or send tmux input.

## Terminal Session Card

`terminal_session_card` is the workbench-level visible terminal overview:

```json
{
  "mode": "terminal_session",
  "runtime_backend": "tmux",
  "session_name": "agentdeck",
  "attach_command": "tmux -L agentdeck-example attach -t agentdeck",
  "running_count": 1,
  "agent_count": 3,
  "open_terminals_command": "agentdeck controls",
  "refresh_command": "agentdeck agent refresh",
  "controls": [
    {
      "kind": "attach_session",
      "label": "Attach session",
      "command": "tmux -L agentdeck-example attach -t agentdeck",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    },
    {
      "kind": "refresh_runtime",
      "label": "Refresh runtime",
      "command": "agentdeck agent refresh",
      "safety": "explicit_runtime",
      "enabled": true,
      "blocker": null
    }
  ],
  "terminals": [
    {
      "agent_id": "planner",
      "role": "planning",
      "status": "running",
      "pane_id": "%42",
      "terminal_command": "agentdeck agent terminal --agent planner",
      "select_pane_command": "tmux -L agentdeck-example select-pane -t %42",
      "enabled": true,
      "blocker": null,
      "controls": [
        {
          "kind": "select_pane",
          "label": "Select pane",
          "command": "tmux -L agentdeck-example select-pane -t %42",
          "safety": "inspect",
          "enabled": true,
          "blocker": null
        }
      ]
    }
  ]
}
```

`attach_command` opens the configured project tmux session. Each terminal item exposes its own `controls[]` with `kind=select_pane`, `label=Select pane`, `safety=inspect`, and a command that matches `select_pane_command`; running panes are enabled, while non-running agents stay visible with a disabled select-pane control and `blocker=agent is not running`. The card-level `controls[]` exposes render-ready project buttons: `attach_session` and `open_controls` use `safety=inspect`, while `refresh_runtime` uses `safety=explicit_runtime` and must match `refresh_command`. `open_terminals_command=agentdeck controls` lets GUI clients jump to the full command palette for broader runtime controls. This card is read-only: it does not attach tmux, select panes, inspect panes, capture output, send input, refresh bindings, spawn panes, stop panes, or write state.

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
  "leader_backend": {
    "agent_id": "leader",
    "provider": "deepseek",
    "model": "deepseek-chat",
    "provider_backend": "api",
    "provider_transport": "http",
    "reasoning_backend": "api-llm",
    "runtime_kind": "logical_leader",
    "pane_backed": false,
    "pane_id": null,
    "approval_required": true,
    "dispatch_ready": false
  },
  "coordination_roles": [
    {
      "role_id": "frontdesk",
      "label": "Frontdesk intake",
      "provider": "local-rule",
      "model": "deterministic",
      "lifecycle": "persistent",
      "responsibility": "Intake human requests and route them without provider planning.",
      "state_source": "chat_turns",
      "runtime_kind": "logical_role",
      "pane_backed": false,
      "pane_id": null,
      "dispatch_ready": false,
      "approval_required": false,
      "next_command": "agentdeck leader chat --message \"frontdesk <goal>\""
    }
  ],
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

The card never exposes API keys and does not call the provider. `api_backed` only indicates that the configured provider is not the local `fake` provider. `leader_backend` is the normalized logical Leader identity for the currently configured provider/model; GUI clients can render fake/API-backed/CLI-backed provenance without waiting for a plan, but it is not a tmux pane binding, readiness proof, dispatch permission, or execution authorization. `coordination_roles[]` mirrors ProjectView's `frontdesk`, `planner`, and `orchestrator` logical roles so GUI clients can render the north-star coordination topology directly in the workbench. These roles are not worker panes and must keep `pane_backed=false`, `pane_id=null`, and `dispatch_ready=false`; `planner` and `orchestrator` remain approval-gated. `controls[]` uses `kind`, `label`, `command`, `safety`, `enabled`, and `blocker` so GUI clients can render Leader entry points without deriving buttons from command strings. `chat` and `review` controls are disabled templates until a GUI or user supplies message text or a plan id; `continue`, `actions`, `refresh`, `leader_status`, and `status` are read-only inspect controls. `refresh` and `leader_status` both point at the narrow `agentdeck leader status` card; `refresh` is the status-card refresh affordance, while `leader_status` is the narrow status view entry. `status` points at the full ProjectView status. `review_command_template` requires a concrete plan id and must not be treated as permission to review, approve, dispatch, or capture automatically.

## Control Mode

`control_mode_card` is a read-only policy projection for Codex-like control levels:

```json
{
  "mode": "control_mode",
  "current_mode": "ask",
  "approval_mode": "confirm",
  "default_safety": "inspect",
  "available_modes": [],
  "active_controls": [],
  "autonomous_actions": []
}
```

`current_mode=ask` means AgentDeck may plan, inspect, and suggest commands without mutating runtime state. `approve` describes the existing approval-gated safe-apply path. `autonomous` is advertised as disabled until scoped delegation, budgets, allowlists, and audit gates exist. `active_controls[]` includes concrete `set_mode` controls for `ask`, `approve`, and `autonomous`; the current mode is disabled with `blocker=already current mode`, `approve` uses `safety=explicit_user`, and `autonomous` remains disabled with an implementation blocker. `set_mode_command_template` is only a form helper for clients that need to build their own mode selector. This card does not grant permission by itself and does not execute, approve, dispatch, acknowledge, or send tmux input. The explicit mutation path is `agentdeck policy set-mode --mode ask|approve`; it only updates `.agentdeck/config.toml:leader.approval_mode` and appends an audit event. `agentdeck policy set-mode --mode autonomous` must fail, leave config unchanged, and append a rejection audit event.

`autonomous_actions[]` surfaces the two already-shipped autonomous commands as read-only, GUI-renderable controls (each uses the standard `kind`/`label`/`command`/`safety`/`enabled`/`blocker` control fields, `safety=delegated`). It always carries exactly two items: `kind=approval_auto` (`command=agentdeck approval auto --confirm`) is `enabled` only when `approval_mode=autonomous`, otherwise disabled with `blocker="autonomous mode is not enabled"`; `kind=run_loop` (`command=agentdeck run-loop --plan-id <id> --confirm`) is always disabled with `blocker="requires --plan-id"` because of the `<id>` placeholder template. These flow into `control_registry[]` under `scope=autonomous`. Rendering an enabled control is inspect-only surfacing, not execution authorization: both commands still require the human to run them explicitly with `--confirm` (and `run-loop` additionally requires autonomous mode). `validate_workbench_contract()` validates each `autonomous_actions[]` item against the same control-field expectations as `active_controls[]` and requires a blocker on every disabled item.

## Control Registry

`control_registry[]` is a flattened, read-only command palette index derived from existing card controls. Each item uses `scope`, `card`, `kind`, `label`, `command`, `safety`, `enabled`, `blocker`, `agent_id`, and `control_id`. `control_id` is deterministic within the control's identity and command shape, so clients can use it as a stable render key or audit correlation key; it is not an authorization token and does not grant permission beyond each item's `safety`, `enabled`, and `blocker`. The registry currently indexes `leader_card.controls[]`, `provider_health.controls[]`, `control_mode_card.active_controls[]`, `agent_ready_card.controls[]`, `terminal_session_card.controls[]`, `terminal_session_card.terminals[].controls[]`, every `runtime_card.agents[].controls[]`, `role_card.agents[].controls[]`, `ledger_card.controls[]`, visible `inbox_card.items[].controls[]`, `leader_inbox_card.items[].controls[]`, `artifacts_card.controls[]`, `skill_context_card.controls[]`, `skill_suggestions_card.controls[]`, `audit_card.controls[]`, and `operator_card.controls[]`. Leader controls must preserve `kind=refresh` and `kind=leader_status` for `agentdeck leader status`, separate from `kind=status` for full ProjectView status, so clients can render a narrow Leader status refresh affordance and a narrow status view entry without parsing labels. Provider controls must be preserved as `scope=provider` with `kind=set_provider`, `kind=guarded_set_provider`, and `kind=setup_provider` so clients can render DeepSeek, OpenAI-compatible, Codex CLI, Claude CLI, and fake-provider choices plus provider setup commands without hard-coding a menu. Agent-ready controls must be preserved as `scope=agent_ready` with `kind=inspect`, `kind=spawn_ready`, `kind=refresh_runtime`, and optionally `kind=dispatch_ready` or single-agent `kind=spawn`; they are command surfaces for humans, not automatic startup. Terminal session controls must be preserved as `scope=terminal_session` with `kind=attach_session`, `kind=open_controls`, `kind=refresh_runtime`, and per-agent `kind=select_pane`; attach/open/select-pane controls are inspect-only, while refresh remains explicit runtime and must point at `agentdeck agent refresh`. Inbox controls must be preserved as `scope=inbox`; active recovery inbox items use `card=inbox_card`, while worker replies flowing back to Leader use `card=leader_inbox_card` and `agent_id=leader`. Inbox `kind=ack` controls are explicit runtime commands and must not be executed by workbench or controls. Ledger controls must be preserved as `scope=ledger`, `card=ledger_card`, and `kind=inspect` for `agentdeck workbench`, so clients can open or refresh the communication ledger card without parsing labels. Artifacts controls must be preserved as `scope=artifacts`, `card=artifacts_card`, and `kind=inspect` for `agentdeck artifacts`, so clients can render a worker-output index refresh/open affordance without parsing labels. Skill controls must be preserved as `scope=skills`, `card=skill_context_card`, and `kind=inspect` for `agentdeck skills list` / `agentdeck status`, plus `scope=skills`, `card=skill_suggestions_card`, and `kind=inspect` for `agentdeck skills suggestions` / `agentdeck status`, so clients can render both loaded workflow context and pending skill suggestions without parsing state files. Audit controls must be preserved as `scope=audit`, `card=audit_card`, and `kind=inspect` for `agentdeck events --limit 20`, so clients can render a recent-event timeline entry without parsing labels. Autonomous controls must be preserved as `scope=autonomous`, `card=control_mode_card`, derived from `control_mode_card.autonomous_actions[]`, with `kind=approval_auto` (`agentdeck approval auto --confirm`, `safety=delegated`, enabled only in autonomous mode) and a disabled `kind=run_loop` template (`agentdeck run-loop --plan-id <id> --confirm`, blocker `requires --plan-id`); rendering these is inspect-only surfacing and is not execution authorization. Runtime terminal controls must be preserved as `kind=terminal` so clients can render a direct "Open terminal" affordance without parsing `agentdeck agent terminal --agent <id>`. When the operator exposes batch approval dispatch, the registry must preserve the operator control as `kind=dispatch_ready` for `agentdeck approval dispatch-ready --confirm` so clients can identify it without parsing labels or command strings. `validate_workbench_contract()` rejects a registry that does not exactly match the controls derived from the same workbench cards. Clients may render this as a command palette or toolbar, but it is not a second state source.

## Provider Health

`provider_health` is a GUI-ready readiness projection for the configured Leader provider:

```json
{
  "agent_id": "leader",
  "provider": "deepseek",
  "model": "deepseek-chat",
  "approval_mode": "confirm",
  "api_backed": true,
  "provider_backend": "api",
  "provider_transport": "http",
  "leader_backend": {
    "agent_id": "leader",
    "provider": "deepseek",
    "model": "deepseek-chat",
    "provider_backend": "api",
    "provider_transport": "http",
    "reasoning_backend": "api-llm",
    "runtime_kind": "logical_leader",
    "pane_backed": false,
    "pane_id": null,
    "approval_required": true,
    "dispatch_ready": false
  },
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

The card never exposes API key values and never calls the provider. It includes the configured Leader identity and model so GUI clients can render provider setup next to the Leader card without joining another state source. `provider_backend` is `local`, `api`, `cli`, or `unknown`; `provider_transport` is `local`, `http`, `subprocess`, or `unknown`. `leader_backend` mirrors the normalized logical Leader identity used by ProjectView, plan/run/review/summary, and doctor diagnostics, so setup screens can distinguish API-backed, CLI-backed, and local fake Leader reasoning backends without parsing provider strings. For API-backed providers such as `deepseek` and `openai-compatible`, readiness is based on the required local environment variable and `command_path` is `null`. For CLI-backed providers such as `codex-cli` and `claude-cli`, readiness is based on whether the local command is available on PATH, and `command_path` exposes the resolved executable path when available. GUI clients can render `doctor_command` as the next diagnostic action, read `doctor_contract` for the doctor diagnostics schema, and show `setup_commands` as copyable placeholder commands; placeholders must never be replaced with real secret values in AgentDeck output. `controls[]` exposes explicit `agentdeck leader set-provider` commands for supported Leader backends: `kind=set_provider` is the normal explicit switch, while `kind=guarded_set_provider` appends `--require-ready` so the later explicit command refuses to write config when the target backend is not ready. It also exposes `kind=setup_provider` controls from the provider setup command allowlist, such as placeholder API exports and local CLI auth/doctor commands, so command palettes can surface setup without calling the provider. The current provider switch controls are disabled with `already current provider`; other controls require `safety=explicit_user` and only change the default Leader provider/model after the human runs the command. `validate_workbench_contract()` rejects missing provider provenance fields, missing or invalid `leader_backend`, non-string provenance values, provider controls that do not use `safety=explicit_user`, do not point at `agentdeck leader set-provider --provider ...`, omit `--require-ready` for `guarded_set_provider`, use a `setup_provider` command outside the provider setup command allowlist, or are disabled without a blocker.

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

The card does not capture pane output and does not prove task completion. It only surfaces the configured agent identity, role, provider, workspace mode, current runtime binding already present in ProjectView, and explicit runtime commands a GUI can render. `validate_workbench_contract()` validates every `runtime_card.agents[]` item and every nested `controls[]` item, not just the first visible terminal row, so GUI clients can trust each multi-agent runtime affordance has the same required command and safety fields. `refresh_command` is an explicit reconciliation command that checks recorded `running` panes against tmux and marks missing panes as `stale`; `agentdeck workbench` does not run it automatically. `terminal_command` opens the read-only terminal card for the agent; it does not attach tmux by itself. `capture_command` is a read-only observation command. `send_command_template` is an explicit runtime input template and must not be executed automatically. GUI clients should prefer `controls[]` for rendering buttons; disabled controls include a `blocker` such as `agent is not running`.

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

The card is configuration-only. It does not dispatch work or mutate roles; GUI clients must run `assign_command` or a completed `assign_role` control explicitly when a human changes a role. `validate_workbench_contract()` validates every `role_card.agents[]` item, not just the first role row, so each configured Agent must expose the same role, prompt, assign command, and GUI control surface. `controls[]` are intentionally disabled templates until the GUI supplies concrete `role` and `role_prompt` values, and they must be preserved as `scope=role` / `kind=assign_role` items in `control_registry[]`.

## Worker Lifecycle Card

`worker_lifecycle_card` is a GUI-ready projection of each worker's task lifecycle:

```json
{
  "mode": "worker_lifecycle",
  "title": "Worker lifecycle",
  "source_command": "agentdeck workbench",
  "count": 3,
  "by_stage": {
    "inbox_pending": 1,
    "idle": 2
  },
  "items": [
    {
      "agent_id": "planner",
      "role": "planning",
      "provider": "codex",
      "runtime_status": "running",
      "pane_id": "%42",
      "lifecycle_stage": "inbox_pending",
      "active_message_id": "msg_xxx",
      "active_job_id": "job_xxx",
      "latest_reply_id": "rep_xxx",
      "artifact_count": 1,
      "pending_inbox_count": 1,
      "trace_command": "agentdeck trace --id msg_xxx",
      "inbox_command": "agentdeck inbox --agent planner",
      "terminal_command": "agentdeck agent terminal --agent planner",
      "capture_command": "agentdeck agent capture --agent planner --lines 200",
      "controls": [
        {
          "kind": "trace",
          "label": "Trace active task",
          "command": "agentdeck trace --id msg_xxx",
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
        }
      ]
    }
  ],
  "controls": [
    {
      "kind": "inspect",
      "label": "Refresh worker lifecycle",
      "command": "agentdeck workbench",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    }
  ]
}
```

The card fuses runtime and ledger facts without creating a second state source. `lifecycle_stage` is derived in this order: pending inbox means `inbox_pending`; otherwise a recorded reply means `reply_recorded`; otherwise a job contributes its job status; otherwise a dispatched message means `task_dispatched`; otherwise the worker is `idle`. All item controls are inspect-only. Disabled trace controls use `blocker=no active task`; disabled capture controls use `blocker=agent is not running`. GUI clients must not treat this card as permission to spawn, dispatch, ack, capture, or release work automatically.

## Review Gate Card

`review_gate_card` is a GUI-ready projection of code-review and round-review readiness:

```json
{
  "mode": "review_gate",
  "title": "Review gate",
  "source_command": "agentdeck workbench",
  "status": "blocked",
  "reason": "round_reviewer is not configured",
  "can_release": false,
  "artifact_count": 1,
  "review_reply_count": 1,
  "code_review": {
    "stage": "code_review",
    "agent_id": "reviewer",
    "role": "review",
    "status": "ready",
    "latest_reply_id": "rep_xxx",
    "trace_command": "agentdeck trace --id rep_xxx",
    "inbox_command": "agentdeck inbox --agent reviewer",
    "blocker": null,
    "controls": [
      {
        "kind": "trace",
        "label": "Trace review",
        "command": "agentdeck trace --id rep_xxx",
        "safety": "inspect",
        "enabled": true,
        "blocker": null
      }
    ]
  },
  "round_review": {
    "stage": "round_review",
    "agent_id": null,
    "role": null,
    "status": "missing_reviewer",
    "latest_reply_id": null,
    "trace_command": null,
    "inbox_command": null,
    "blocker": "round_reviewer is not configured",
    "controls": []
  },
  "controls": [
    {
      "kind": "inspect",
      "label": "Inspect review gate",
      "command": "agentdeck workbench",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    },
    {
      "kind": "assign_code_reviewer",
      "label": "Assign code reviewer",
      "command": "agentdeck agent assign-role --agent <agent_id> --role code_reviewer --role-prompt <role_prompt>",
      "safety": "explicit_user",
      "enabled": false,
      "blocker": "requires agent_id and role_prompt"
    },
    {
      "kind": "assign_round_reviewer",
      "label": "Assign round reviewer",
      "command": "agentdeck agent assign-role --agent <agent_id> --role round_reviewer --role-prompt <role_prompt>",
      "safety": "explicit_user",
      "enabled": false,
      "blocker": "requires agent_id and role_prompt"
    }
  ]
}
```

The card is a gate projection, not a release action. `code_review` uses an agent whose `agent_id` or `role` is `code_reviewer` or `reviewer`; `round_review` requires an explicit `round_reviewer`. A gate is `ready` only when artifacts exist, code review has a recorded reply, and round review has a recorded reply. Missing reviewers, missing artifacts, and missing replies keep `status=blocked` with a concrete `reason`. Stage controls stay inspect-only. Top-level assignment controls are disabled `explicit_user` templates for GUI role-configuration forms and must not execute until a human fills `agent_id` and `role_prompt`; all controls must also appear in `control_registry[]` under `scope=review_gate`.

## Release Preview Card

`release_preview_card` is a GUI-ready projection for the future release / next-round decision:

```json
{
  "mode": "release_preview",
  "title": "Release / next-round preview",
  "source_command": "agentdeck workbench",
  "status": "blocked",
  "reason": "round_reviewer is not configured",
  "review_gate_status": "blocked",
  "can_release": false,
  "already_released": false,
  "release_count": 1,
  "latest_release_id": "rel_example",
  "next_command": null,
  "release_command": null,
  "next_round_command": null,
  "controls": [
    {
      "kind": "inspect_review_gate",
      "label": "Inspect review gate",
      "command": "agentdeck workbench",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    },
    {
      "kind": "release_preview",
      "label": "Preview release",
      "command": null,
      "safety": "explicit_user",
      "enabled": false,
      "blocker": "round_reviewer is not configured"
    },
    {
      "kind": "next_round_preview",
      "label": "Preview next round",
      "command": null,
      "safety": "explicit_user",
      "enabled": false,
      "blocker": "round_reviewer is not configured"
    }
  ]
}
```

The card has three states. While the gate is `blocked`, `can_release` mirrors the review gate, the card keeps the same reason, exposes no executable release or next-round command, and `release_preview` / `next_round_preview` stay disabled `explicit_user` placeholders with `command=null`; the only enabled control is the inspect jump back to `agentdeck workbench`. Once the gate is `ready`, `release_command` / `next_command` must both be the explicit `agentdeck release --confirm` command, the `release_preview` control becomes enabled with that same command, and `next_round_preview` stays disabled with the `agentdeck leader plan --task <goal>` template and blocker `requires goal text`. When the gate is ready but the current code-review / round-review reply pair already appears in `project_view.releases[]`, the card switches to `status=released`: `can_release` drops to `false`, `already_released` is `true`, the reason is `round already released`, `release_command` / `next_command` return to `null`, and only the disabled next-round plan template remains as the suggested follow-up. `already_released`, `release_count`, and `latest_release_id` are derived from the same ProjectView `releases` summary. The validator rejects an enabled release control whose card is not `can_release=true`, a command that drifts from `release_command`, or a released card that still exposes executable release commands, and a released card requires a ready review gate. Rendering the card never releases, merges, acks inbox items, dispatches follow-up work, or advances the loop; only a human explicitly running `agentdeck release --confirm` records the round release. All controls must appear in `control_registry[]` under `scope=release_preview`.

## Role Topology Card

`role_topology_card` is the Phase G6 read-only role topology projection: it unifies the logical Leader coordination roles (`frontdesk`, `planner`, `orchestrator`) and the configured worker roles into one ordered list so a GUI/TUI can show, at a glance, which role is thinking, waiting, executing, reviewing, or idle.

```json
{
  "mode": "role_topology",
  "title": "Role topology",
  "source_command": "agentdeck workbench",
  "count": 6,
  "logical_role_count": 3,
  "worker_role_count": 3,
  "by_status": {"ready": 1, "planning": 1, "coordinating": 1, "inbox_pending": 1, "idle": 1, "reviewed": 1},
  "blocked_count": 0,
  "roles": [
    {
      "role_id": "frontdesk",
      "label": "Frontdesk intake",
      "agent_id": null,
      "kind": "logical_role",
      "provider": "local-rule",
      "lifecycle": "persistent",
      "runtime_kind": "logical_role",
      "pane_backed": false,
      "pane_id": null,
      "status": "ready",
      "blocker": null,
      "next_command": "agentdeck leader chat-history",
      "controls": [
        {
          "kind": "inspect",
          "label": "Inspect intake history",
          "command": "agentdeck leader chat-history",
          "safety": "inspect",
          "enabled": true,
          "blocker": null
        }
      ]
    }
  ],
  "controls": [
    {
      "kind": "inspect",
      "label": "Inspect role topology",
      "command": "agentdeck workbench",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    }
  ]
}
```

The logical roles are projected from `leader.coordination_roles[]`: they keep `kind=logical_role`, `runtime_kind=logical_role`, `pane_backed=false`, `pane_id=null`, and `agent_id=null`. Their `status` is derived from ProjectView facts — `frontdesk` is always `ready`; `planner` is `planning` when at least one plan exists (else `idle`); `orchestrator` is `waiting_for_approval` (with blocker `waiting for human approval`) when any approval is pending, otherwise `coordinating` when a pending Leader action exists, otherwise `released` when at least one round has been released, otherwise `idle`. Only `orchestrator` carries a blocker (for `waiting_for_approval`); `frontdesk` and `planner` keep a `null` blocker. Each logical role's inspect control points at its own read-only state source: `frontdesk` → `agentdeck leader chat-history`, `planner` → `agentdeck plan list`, `orchestrator` → `agentdeck leader actions`.

The worker roles are projected from the same `worker_lifecycle_card` items: they use `kind=worker`, `runtime_kind=worker_pane`, carry the agent's `agent_id`/`provider`, set `pane_backed` only when the pane is running, reuse the worker `lifecycle_stage` as `status`, and expose an inspect control pointing at `agentdeck inbox --agent <id>`. Each role's single control command must equal that role's `next_command`.

When a worker role's `agent_id` matches a `review_gate_card` stage (`code_review` or `round_review`, code review taking precedence), the topology overlays the review stage onto that role so the reviewer's `status`/`blocker` reflects the gate: a `ready` stage becomes `status=reviewed`, a `waiting_for_review` stage becomes `status=reviewing` (its turn, no blocker), and any other stage (e.g. `waiting_for_artifacts` or `blocked`) becomes `status=blocked` with the stage's `blocker`. Non-reviewer worker roles keep their base `lifecycle_stage` status and a `null` blocker. The overlay is read-only and never advances the gate.

The card also carries an at-a-glance summary: `by_status` maps each role `status` to a count across all roles, and `blocked_count` is the number of roles carrying a non-null `blocker`. The validator requires `blocked_count` to equal the number of roles with a truthy blocker so GUI clients can trust the count without iterating the roles.

The validator rejects a logical role that claims to be pane-backed, has a pane id, or carries an agent id; a worker role that is not `runtime_kind=worker_pane`; and any role control that is not `safety=inspect` or whose command drifts from `next_command`. Rendering the card never spawns, dispatches, captures, acks, releases, or writes state. All controls appear in `control_registry[]` under `scope=role_topology` (card-level plus one per role, with worker rows carrying their `agent_id`).

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
  ],
  "controls": [
    {
      "kind": "inspect",
      "label": "Inspect communication ledger",
      "command": "agentdeck workbench",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    }
  ]
}
```

`messages`, `jobs`, `replies`, and `artifacts` reuse the ProjectView summary shapes and must retain `trace_command` on each item. `messages.items[]` also keeps compact `prompt_skill_context`, which is the worker skill provenance captured at dispatch time and intentionally excludes full `content_snapshot`. `artifacts` is the recoverable output summary for files or other deliverables produced by worker agents; it carries `artifact_id`, linked message/job/reply ids, `from_agent`, `path`, `kind`, `status`, and `created_at` without reading file contents. `inbox` reuses the ProjectView inbox summary so GUI clients can show mailbox heads without scanning per-agent inbox arrays. `trace_commands` is a de-duplicated convenience list for quick trace navigation and must include every summary item trace command from messages, jobs, replies, and artifacts; the detail source remains `agentdeck trace --id <id>`. `controls[]` is the stable card-level inspect affordance for GUI/TUI clients and must remain `safety=inspect`; it opens the workbench ledger source rather than executing any trace command.

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

`recent_paths[]` is a convenience projection, not a new ledger. It links message, job, reply, and inbox ids when they are visible in ProjectView or embedded inbox cards. The `message_count`, `job_count`, `reply_count`, and `inbox_count` values must cover the number of recent paths carrying each corresponding id, so GUI clients can trust that the visible path list never exceeds the summarized ledger counts. `trace_command` is always the detail entry point; clients must still use `agentdeck trace --id <id>` for full lineage details. The card does not create messages, acknowledge inbox items, capture replies, read tmux panes, or send tmux input.

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

The card summarizes queues only. Its `leader_actions`, `approvals`, and `inbox` summaries must match the same `project_view` snapshot, so GUI status bars do not drift from the detailed cards. GUI clients should use `leader_action`, `approval_card`, `inbox_card`, or the listed commands for details and explicit execution.

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

GUI clients should prefer `controls[]` as the renderable button list, while keeping `preview_command`, `command`, `apply_command`, and `explicit_command` as compatibility fields. The `preview`, `apply`, `explicit`, and `capture_reply` controls must match the corresponding top-level command fields, so a GUI button cannot drift away from the audit/compatibility fields. Their `enabled` flags must also match the same source fields: preview follows `preview_command`, apply follows `can_apply` plus `apply_command`, and explicit/capture-reply follows `explicit_command` plus the absence of `blocker`. Their `blocker` values must also stay aligned: preview uses `null`, apply mirrors `operator_card.blocker`, and explicit/capture-reply mirrors `operator_card.blocker` or reports `no explicit command available` when no explicit command exists. Execution still belongs to the user or a later explicit approval flow. The card must not be treated as permission to auto-dispatch, auto-ack, auto-approve, or send tmux input.

For approval dispatch recovery, `operator_card` derives runtime readiness from the same ProjectView snapshot. If the approved approval targets an agent without a running pane, `operator_card.blocker` and the explicit control blocker should explain the runtime problem, and the explicit control must be disabled. When more than one approval is already approved, the operator promotes the main explicit action to `agentdeck approval dispatch-ready --confirm` with `action_kind=approval_dispatch_ready`; the corresponding control must use `kind=dispatch_ready` and label `Dispatch ready approvals`. It is enabled only when `operator_card.blocker` is `null`, and its `blocker` must exactly match `operator_card.blocker`. Blocked targets remain visible through the later dispatch-ready result. The card still shows the explicit command so the human can see what will run after fixing runtime, but the workbench must not dispatch, spawn, refresh, or send tmux input.

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
  "events_command": "agentdeck events --limit 20",
  "controls": [
    {
      "kind": "inspect",
      "label": "Inspect audit events",
      "command": "agentdeck events --limit 20",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    }
  ]
}
```

The card intentionally uses compact ProjectView recovery event summaries. `validate_workbench_contract()` validates every `recent_events[]` item for `event_id`, `event_type`, and `created_at`, and `event_count` must match the list length, so GUI clients can render a consistent recovery timeline without reading the raw event log. Its `controls[]` is indexed into `control_registry[]` as `scope=audit` / `card=audit_card` / `kind=inspect` and must point at `agentdeck events --limit 20` with `safety=inspect`. Use `events_command` when a GUI needs the raw JSONL timeline.

## Artifacts Card

`artifacts_card` is the GUI/TUI-ready worker output index embedded directly in the workbench snapshot:

```json
{
  "ok": true,
  "mode": "artifacts",
  "schema_version": "project-view/v1",
  "source_command": "agentdeck artifacts",
  "project_view_contract": "agentdeck contract project-view",
  "artifacts_contract": "agentdeck contract artifacts",
  "artifacts_command": "agentdeck artifacts",
  "trace_command_template": "agentdeck trace --id <id>",
  "controls": [
    {
      "kind": "inspect",
      "label": "Inspect artifacts",
      "command": "agentdeck artifacts",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    }
  ],
  "artifacts": {
    "count": 1,
    "by_status": {"created": 1},
    "by_kind": {"file": 1},
    "items": []
  }
}
```

The card reuses the standalone `agentdeck artifacts` contract and `validate_artifacts_contract()`. Its `controls[]` is indexed into `control_registry[]` as `scope=artifacts` / `card=artifacts_card` / `kind=inspect` and must point at `agentdeck artifacts` with `safety=inspect`. It is a ProjectView artifact summary only: it does not read artifact file contents, inspect tmux panes, call providers, write state, acknowledge inbox items, approve, dispatch, or send tmux input.

## Leader Summary Card

`leader_summary_card` is the GUI/TUI-ready latest-run result surface. It is `null` when there is no latest plan or the latest plan is still waiting for approval, dispatch, or replies. When `agentdeck leader review --plan-id <latest>` returns `next_action=summarize`, workbench embeds the same response shape as `agentdeck leader summary --plan-id <latest>`:

```json
{
  "schema_version": "project-view/v1",
  "plan_id": "pln_xxx",
  "status": "ready",
  "reply_count": 1,
  "artifact_count": 1,
  "summary": "1 dispatched step has replies; 1 artifact recorded.",
  "steps": [],
  "controls": []
}
```

The card reuses `validate_leader_summary_contract()`. It only aggregates existing plan status, replies, artifacts, and trace commands; it does not call providers, read tmux panes, capture replies, create approvals, dispatch work, acknowledge inbox items, write state, or send tmux input.

## Contracts Card

```json
{
  "contracts_command": "agentdeck contract list",
  "contract_index_contract": "docs/contracts/contract-index-schema.md",
  "workbench_contract": "agentdeck contract workbench",
  "controls_contract": "agentdeck contract controls",
  "skills_contract": "agentdeck contract skills",
  "memory_contract": "agentdeck contract memory",
  "learning_review_contract": "agentdeck contract learning-review",
  "agent_runtime_contract": "agentdeck contract agent-runtime",
  "leader_chat_contract": "agentdeck contract leader-chat",
  "leader_review_contract": "agentdeck contract leader-review",
  "leader_summary_contract": "agentdeck contract leader-summary",
  "acp_runtime_contract": "agentdeck contract acp-runtime",
  "project_view_contract": "agentdeck contract project-view",
  "events_contract": "agentdeck contract events",
  "doctor_contract": "agentdeck contract doctor",
  "run_contract": "agentdeck contract run",
  "artifacts_contract": "agentdeck contract artifacts"
}
```

This card lets GUI/TUI clients bootstrap from a single workbench snapshot and then discover the full machine-readable contract index on demand, including the dedicated `run` start card contract, the `controls` command palette contract, the `skills` registry contract, the `memory` suggestion/apply contract, the `learning-review` contract for read-only skill/memory learning suggestions, the `agent-runtime` command contract for visible tmux pane controls, the `leader-chat` response contract for natural-language Leader interactions, the `leader-summary` response contract for deterministic reply/artifact aggregation, and the `artifacts` contract for the read-only worker output index. It is static metadata and does not read state, inspect tmux panes, call providers, or execute any contract command.

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
- `run_progress_card` is a read-only latest-run projection. It must not approve, dispatch, capture pane output, acknowledge inbox items, or send tmux input.
- `artifacts_card` is the same read-only artifact index as `agentdeck artifacts`. Its inspect control may be rendered through `control_registry[]`, but it must not read output files or become a second artifact state source.
- `leader_summary_card` is the same read-only final-result surface as `agentdeck leader summary --plan-id <id>` and appears only when the latest plan is ready to summarize.
- `learning_review_card` is the same read-only Hermes-style learning reviewer as `agentdeck learn review --plan-id <id>` and appears under the same gate; its `summary` / `suggest_skill` / `suggest_memory` controls enter `control_registry[]` under `scope=learning_review`, but rendering the workbench never queues suggestions or writes state.
- Runtime actions still require explicit commands or approval flow.
