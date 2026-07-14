# ProjectView Contract

`missions.items[].mission_id` values must be unique. Duplicate ids are invalid split-brain state: ProjectView validation returns a stable field-only error, and workbench/status refuse to print a partial snapshot rather than silently selecting one duplicate.

`agentdeck status` is the canonical read-only ProjectView for CLI, natural-language Leader chat, recovery tooling, and future GUI clients. The additive M1 `conversation` summary is compact lifecycle truth only; it never contains a transcript or raw prompt/response text.

GUI clients should consume ProjectView first. They should not scan `.agentdeck/state/state.json`, parse tmux panes, or infer workflow state from command strings when ProjectView already exposes the same fact.

The source-of-truth schema version constant is `PROJECT_VIEW_SCHEMA_VERSION` in `src/agentdeck/models.py`. Current value: `project-view/v1`. The protocol-lineage summaries described below are an additive-v1 extension: the version remains `project-view/v1`, while current producers and validators require the new top-level fields.

Reusable contract response, payload, and example fixture helpers live in `src/agentdeck/contracts.py`. The CLI discovery command uses `project_view_contract_response()` directly so command output and reusable module output stay identical.

Field list constants are also defined in `src/agentdeck/contracts.py`: `PROJECT_VIEW_TOP_LEVEL_FIELDS`, `PROJECT_VIEW_LEADER_FIELDS`, `PROJECT_VIEW_COORDINATION_ROLE_FIELDS`, `PROJECT_VIEW_MISSIONS_FIELDS`, `PROJECT_VIEW_MISSION_ITEM_FIELDS`, `PROJECT_VIEW_PLAN_ITEM_FIELDS`, `PROJECT_VIEW_RECOVERY_FIELDS`, `PROJECT_VIEW_RECOMMENDED_ACTION_FIELDS`, `PROJECT_VIEW_SKILLS_FIELDS`, `PROJECT_VIEW_SKILL_ITEM_FIELDS`, `PROJECT_VIEW_MEMORY_FIELDS`, `PROJECT_VIEW_MEMORY_ITEM_FIELDS`, `PROJECT_VIEW_MESSAGE_ITEM_FIELDS`, `PROJECT_VIEW_JOB_ITEM_FIELDS`, `PROJECT_VIEW_REPLY_ITEM_FIELDS`, and `PROJECT_VIEW_ARTIFACT_ITEM_FIELDS`.

Use `validate_project_view_contract(payload)` from `src/agentdeck/contracts.py` to check any ProjectView-like payload against the v1 baseline contract.

`agentdeck status` self-validates its generated ProjectView with `validate_project_view_contract()` before printing JSON. If validation fails, it exits non-zero, writes the contract errors to stderr, and does not print a partial ProjectView.

Leader decision commands use the same validation gate before they plan from chat, review a plan, persist a next action, apply a safe action, or embed ProjectView in a response. If the ProjectView contract is invalid, these commands exit non-zero before creating plans, chat turns, or leader actions.

`agentdeck leader chat` responses expose top-level `leader_actions` as a convenience copy of the embedded `project_view.leader_actions`. Natural-language shells and GUI clients can render the action queue from one chat response while still treating ProjectView as the source of truth.

They also expose a top-level `leader_explanation` block. This is not a second state source; it is a GUI-ready explanation derived from the same ProjectView, review, action, and result payloads.

Leader chat responses are covered by `docs/contracts/leader-chat-schema.md` and self-validate before JSON is printed.

## Top-Level Shape

```json
{
  "schema_version": "project-view/v1",
  "project": "repo-name",
  "root": "/absolute/project/root",
  "runtime_backend": "tmux",
  "leader": {
    "agent_id": "leader",
    "provider": "deepseek",
    "model": "deepseek-chat",
    "approval_mode": "confirm",
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
    ]
  },
  "agents": [],
  "state_path": "/absolute/project/root/.agentdeck/state/state.json",
  "missions": {},
  "plans": {},
  "approvals": {},
  "messages": {},
  "jobs": {},
  "replies": {},
  "artifacts": {},
  "releases": {},
  "chat_turns": {},
  "leader_errors": {},
  "leader_actions": {},
  "skills": {},
  "memory": {},
  "agent_sessions": {},
  "protocol_turns": {},
  "transport_updates": {},
  "permission_requests": {},
  "protocol_state_transitions": {},
  "conversation": {},
  "inbox": {},
  "recovery": {},
  "mission_recovery": {}
}
```

All ProjectView fields are read-only summaries. Commands that mutate state, send tmux input, dispatch work, or apply approvals must remain explicit commands with approval semantics.

## Protocol lifecycle summaries

ProjectView exposes compact `agent_sessions`, `protocol_turns`, `transport_updates`, `permission_requests`, and `protocol_state_transitions` summaries. The transition summary contains `count`, `by_entity_type`, and the stable latest 20 items ordered by `created_at` then `transition_id`. Each item is restricted to `transition_id`, `entity_type`, `entity_id`, `from_state`, `to_state`, `reason`, and `created_at`; persisted `details`, native credentials, update payloads, permission options, and targets are never projected.

Before projection, the complete transition history is validated in O(n), including identities, entity references, ordering-dependent `from_state` continuity, legal edges, and duplicate rejection. Session `state`, turn `state`, and permission `status` in their base summaries are derived from the immutable base row plus the full validated transition history. Only these compact copies change; ProjectView rendering performs no writes. Legacy state with no transitions retains its base state/status. This additive contract remains `project-view/v1` under the repository's existing additive-v1 compatibility policy.

## Foreground conversation summary

`conversation` exposes `session_count`, `turn_count`, `preview_count`, `transition_count`, latest conversation/turn ids and derived states, one compact `pending_preview`, per-Agent `ownership`, `outbox_count`, and `blockers`. Current states come only from the validated append-only `conversation_state_transitions[]` history. A pending preview contains only `preview_id`, `preview_kind`, and `expires_at`; execution digests and full preview content are not projected here.

The summary is read-only. Rendering ProjectView never creates a conversation, consumes a preview, flushes the event outbox, calls a Leader, invokes ACP, reads a tmux pane, or sends terminal input. Full user/Leader text stays outside durable M1 conversation records.

`leader` includes the configured Leader identity and `leader_backend`, a normalized logical Leader identity for the current provider/model. This lets GUI and natural-language shells render fake/API-backed/CLI-backed Leader provenance before any plan exists. It is not a tmux pane binding, provider readiness proof, dispatch permission, or execution authorization.

`leader.coordination_roles[]` exposes the logical north-star split `frontdesk`, `planner`, and `orchestrator` for GUI clients. These are Leader-side coordination roles, not worker agents and not tmux panes: every item must keep `runtime_kind=logical_role`, `pane_backed=false`, `pane_id=null`, and `dispatch_ready=false`. `frontdesk` is local-rule/deterministic and does not require approval because it only routes intake; `planner` and `orchestrator` inherit the configured Leader provider/model and require approval before their output can become execution. `validate_project_view_contract()` rejects missing role fields, wrong order, pane-backed role claims, dispatch-ready roles, or incorrect approval flags.

`plans.items[]` includes the configured provider name plus normalized provenance labels: `provider_backend` is `local` for the fake dry-run provider, `api` for API-backed Leader providers such as DeepSeek or OpenAI-compatible backends, `cli` for local CLI-backed Leader providers such as Codex CLI or Claude Code CLI, and `unknown` for unrecognized legacy records; `provider_transport` is `local`, `http`, `subprocess`, or `unknown`. Each plan item also includes `leader_backend`, a normalized identity card that keeps `agent_id=leader`, provider/model, backend/transport, `reasoning_backend`, `runtime_kind=logical_leader`, `pane_backed=false`, `pane_id=null`, `approval_required=true`, and `dispatch_ready=false` together for GUI/audit rendering. `skill_context` is the compact loaded-skill provenance snapshot that was visible when the Leader created the plan; it mirrors ProjectView `skills` summary shape and intentionally excludes full `content_snapshot`. `validate_project_view_contract()` checks every plan item and rejects `leader_backend` payloads that claim a worker agent, tmux pane, or dispatch-ready Leader. GUI clients may render these as plan origin metadata, but they are not separate state sources or execution permissions.

`messages.items[]` includes `prompt_skill_context`, the compact worker skill provenance snapshot captured when `agentdeck dispatch` or `agentdeck approval dispatch` injected loaded skill content into the worker prompt. It uses the same compact shape as `plans.items[].skill_context`, intentionally excludes full `content_snapshot`, and exists so GUI clients can show worker skill context without parsing prompt text. Old or manually seeded messages without skill provenance are normalized to an empty summary.

## Missions

`missions` is the compact, read-only projection of persisted natural-language Mission records. It has the summary fields `count`, `by_status`, `latest_id`, and `items`. Item order follows persisted state order and `latest_id` is the final safely projected item id, or `null` when no safe Mission exists. In healthy state, `count == len(items)`. If persisted `missions[]` contains a non-object or unsafe-id row, `count` retains the raw row count while `items`, `by_status`, and `latest_id` remain limited to safe rows; contract validation then fails with a controlled count mismatch instead of crashing or hiding corruption. If the top-level `missions` container itself is not a list, the projection emits only `{count: -1, by_status: {}, latest_id: null, items: []}`; the negative sentinel leaks none of the corrupt value and produces the explicit validator error `missions.count must be a non-negative integer`.

Each `missions.items[]` row contains `mission_id`, `schema_version`, `user_message`, `status`, `stop_reason`, `can_start`, `can_resume`, `blockers`, `provider`, `model`, `leader_backend`, `plan_id`, `plan_hash`, `workflow_run_id`, `current_step`, `step_count`, `timeout_seconds`, compact `selected_agents` and `startup_actions`, timestamps, `daemon_admission`, and deterministic status/confirmation/resume commands. `daemon_admission` is the durable project-daemon admission projection with exact fields `state`, `snapshot_hash`, `blocker`, `recovery_command`, and `updated_at`: unconfirmed Missions project `not_confirmed`, confirmed but unreachable/rejected daemon submission projects `confirmed_not_admitted` with an explicit recovery command, and successful exact-digest admission projects `admitted` with no blocker or recovery command. Projection rebuilds only that five-field allowlist and validates the complete type/state combination. A malformed or extra-field admission row is replaced by a deterministic `not_confirmed` sentinel and a fixed Mission blocker; raw values, unknown fields, and credentials are never forwarded. This fail-closed normalization is read-only, so status/workbench remain contract-valid without repairing state or appending events. It is scheduling provenance, not transport readiness or execution authorization. `can_resume` is true only for `stopped` or `interrupted` Mission state; commands are affordances and do not authorize execution.

The projection uses allowlisted fields for nested selected-agent and startup-action rows, requires their domain-produced worker/runtime/model/action scalars, and rebuilds `leader_backend` from the top-level provider/model using the existing compact logical-Leader identity instead of copying arbitrary state. It excludes raw Worker launch commands, full prompts, credentials, environment values, and other execution secrets even if a legacy or manually seeded state record hides them under an otherwise allowlisted key. Invalid nested rows are never rendered as `{}`; safely recoverable fields may remain, while the Mission is forced to `can_start=false` with `invalid mission worker summaries`. `validate_project_view_contract()` verifies summary counts/statuses/latest id, canonical `mis_<12 lowercase hex>` ids, exact status/run/resume commands derived from that id, progress bounds, required nested fields and scalar types, provider/model coherence, known Mission statuses, the existing logical-Leader backend contract, and recursively rejects raw command/prompt/credential semantic keys without inspecting ordinary string content. Rendering `agentdeck status` neither changes Mission state nor appends events.

Mission identity and frozen planning fields are immutable after creation. `update_mission()` only accepts `status`, `stop_reason`, `can_start`, `blockers`, `workflow_run_id`, monotonic bounded `current_step`, and one-time `confirmed_at`; `updated_at` and first completion time are store-owned. `can_start=true` always requires an empty blocker list: create/update reject contradictions before writing, legacy contradictory rows preserve compact blockers but project `can_start=false`, and the contract validator rejects external contradictory payloads. If a legacy blockers container is not a list or contains any non-string item, projection retains every valid string, appends the fixed `invalid mission blockers` marker, discards the malformed values without exposing them, and forces `can_start=false`. Legal transitions are `pending_confirmation -> preparing|stopped`, `preparing -> running|stopped`, `running -> completed|stopped|interrupted`, and explicit resume from `stopped|interrupted -> preparing|running`. Same-state updates are idempotent where otherwise valid, and `completed` is terminal.

## Background Mission recovery

`mission_recovery` is the canonical compact reconnection card for an existing
project client. It is derived from the same validated ProjectView and compact
Mission ledger facts as `agentdeck status`; `reconnect_conversation()` and the
workbench `mission_recovery_card` return that exact object rather than rebuilding
state from a transcript, tmux pane, or provider call.

The stable fields are `mode`, `mission_id`, `classification`, `progress`,
`completed_steps`, `recent_results`, `active_step`, `wait_reason`, `decision`,
`trace_commands`, and `workspace_control`. Completed/active steps expose only
`step_id`, `position`, `agent_id`, and `role`. Recent validated results expose
only Mission/attempt lineage plus hashes and an artifact count; raw Worker
summaries, verification text, artifact contents, native trace ids, prompts,
credentials, and full conversation text are excluded. Invalid recovery,
attempt, or reply records are ignored rather than projected.

The validator requires exact field sets and scalar types throughout the card.
`decision` is exactly `{kind, attempt_id, controls}` and its kind must match the
classification: `resumable -> resume`, `waiting_human -> permission|inspect`,
`ambiguous|blocked -> inspect`, and `terminal -> none`. Mission, attempt, and
step lineage must agree with the compact step/result facts. Controls and trace
commands are derived exactly from those canonical ids; the only workspace
control is the inspect-only `agentdeck workbench`. Terminal cards expose no
resume or other dangerous control. ProjectView, workbench, reconnect
conversation, and bare `agentdeck` validate this contract before printing, so
an invalid card cannot produce partial JSON.
`completed_steps` must exactly cover contiguous positions
`1..progress.completed`; an active step, when present, is exactly the next
position and stays within `progress.total`.
Recent results are unique, ordered, and tied to the same completed step/Worker
lineage. Terminal and no-Mission cards retain no active step and obey the same
progress consistency rules.

When multiple Missions exist, the latest valid persisted recovery decision
selects its exact Mission; without one, the latest non-terminal Mission is used
before a terminal fallback. `classification` remains one of the deterministic
reconciliation results (`resumable`, `waiting_human`, `ambiguous`, `blocked`, or
`terminal`). Controls are affordances only: rendering this card performs no
provider call, tmux read/input, scheduling transition, permission decision, or
state write.

Existing-project migration is a separate explicit command surface. `agentdeck
project migration-preview` hashes the exact current `state.json` bytes, reports
additive target changes, expiry, digest, consume-once confirmation command, and
marks snapshot-incomplete historical Missions `inspect_only`; it performs zero
writes. `agentdeck project migrate ... --confirm` accepts only that exact,
unexpired, unchanged preview, writes a project-local sanitized backup of the
affected additive paths' prior absence before atomically replacing state, and records only
additive M2b metadata. Exact source revalidation, backup installation, and the
state replacement occur under the protocol mutation lock. Backup directories
are opened relative to the project with no-follow semantics, the final backup
and parent directories are fsynced, and rollback replacement fsyncs the state
parent. It never backs up runtime credentials or external files,
never upgrades old history into apparent frozen authority, and reconfirmation
starts through a new Mission preview. Drift, replay, expiry, backup failure, and
state-save failure leave the original state in place; a rollback-success path
removes its unused backup, while a rollback failure retains the backup.

## Recovery

`recovery` is the canonical next-step surface for humans, natural-language shells, and GUI clients. It prioritizes pending Leader actions, approved approvals, pending approvals, stale runtime bindings, pending inbox items, waiting dispatched replies, and Leader errors before returning idle.

When no state queue needs attention but the configured API-backed Leader provider is missing required local environment, `recovery.status` is `provider_setup_required`, `recovery.next_command` is `agentdeck doctor`, and `recovery.recommended_action.source` is `provider_health`. This is still read-only; it only guides the user toward setup diagnostics before a plan/chat call fails.

## Discovery Command

Use `agentdeck contract project-view` to discover this contract from tools or GUI clients:

```json
{
  "schema_version": "project-view/v1",
  "status_command": "agentdeck status",
  "contract_path": "/absolute/repo/docs/contracts/project-view-schema.md",
  "contract_exists": true,
  "top_level_fields": [],
  "leader_fields": [],
  "coordination_role_fields": [],
  "missions_fields": [],
  "mission_item_fields": [],
  "plan_item_fields": [],
  "mission_recovery_fields": [],
  "mission_recovery_step_fields": [],
  "mission_recovery_result_fields": [],
  "mission_recovery_control_fields": [],
  "recovery_fields": [],
  "recovery_pending_fields": [],
  "recommended_action_fields": [],
  "leader_actions_fields": [],
  "leader_action_item_fields": [],
  "skill_summary_fields": [],
  "skill_item_fields": [],
  "memory_summary_fields": [],
  "memory_item_fields": [],
  "message_item_fields": [],
  "job_item_fields": [],
  "reply_item_fields": [],
  "artifact_item_fields": []
}
```

The discovery command is read-only. It does not require a project to be initialized and does not read or mutate `.agentdeck/state`.

Use `agentdeck contract project-view --example` to include a stable GUI-ready ProjectView fixture:

```json
{
  "schema_version": "project-view/v1",
  "example": true,
  "example_top_level_fields": [],
  "example_leader_fields": [],
  "example_coordination_role_fields": [],
  "example_missions_fields": [],
  "example_mission_item_fields": [],
  "example_plan_item_fields": [],
  "example_mission_recovery_fields": [],
  "example_recovery_fields": [],
  "example_recommended_action_fields": [],
  "example_message_item_fields": [],
  "example_job_item_fields": [],
  "example_reply_item_fields": [],
  "example_artifact_item_fields": [],
  "example_project_view": {
    "schema_version": "project-view/v1",
    "leader_actions": {
      "recommended_action_id": "act_example",
      "items": [
        {
          "action_id": "act_example",
          "is_recommended": true
        }
      ]
    },
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

The example fixture is deterministic and does not represent live project state. The `example_*_fields` arrays are derived from the fixture and should match the discovery field lists; they exist to catch drift between discovery metadata, documentation, and the example payload.

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
- `artifacts`: `count`, `by_status`, `by_kind`, `items[]`
- `releases`: `count`, `items[]`
- `skills`: `count`, `by_agent`, `by_source`, `items[]`
- `memory`: `count`, `by_scope`, `items[]`
- `agent_sessions`: `count`, sorted `by_state`, compact `items[]`
- `protocol_turns`: `count`, sorted `by_state`, compact `items[]`
- `transport_updates`: `count`, sorted `by_kind`, compact `items[]`
- `permission_requests`: `count`, `pending_count`, sorted `by_status`, compact `items[]`

The four protocol summaries are always non-null, including on a fresh project. Their `count` and `by_*` maps describe the complete stored collection, while `items[]` is bounded to the latest 20 records. Items are deterministically ordered by `created_at` and then their domain id (`session_id`, `turn_id`, `update_id`, or `permission_id`); after sorting, only the last 20 are retained.

`agent_sessions.items[]` exposes `session_id`, `agent_id`, `provider`, `transport`, `state`, the boolean capability summary, `native_session_present`, `workspace`, `created_at`, and `updated_at`. It never exposes `native_session_id` or private observation bindings. `protocol_turns.items[]` exposes `turn_id`, `session_id`, `message_id`, `state`, `created_at`, and `updated_at`, never a prompt. `transport_updates.items[]` exposes only `update_id`, `session_id`, `turn_id`, `sequence`, `kind`, and `created_at`, never `payload`. `permission_requests.items[]` exposes `permission_id`, `session_id`, `turn_id`, `tool_name`, `risk`, `status`, `decision`, and `created_at`, never `target`.

Discovery exposes matching `agent_sessions_fields` / `agent_session_item_fields`, `protocol_turns_fields` / `protocol_turn_item_fields`, `transport_updates_fields` / `transport_update_item_fields`, and `permission_requests_fields` / `permission_request_item_fields`, plus same-shaped `example_*` lists. The deterministic example includes one linked session, turn, update, and pending permission rather than empty placeholders.

Protocol summary and item shapes are exact allowlists in additive v1. Counts must be non-negative integers (booleans are rejected), `by_*` maps must have non-empty string keys and non-negative integer values, and `items` must contain at most 20 objects with exact fields and valid domain enum values. Capability summaries contain exactly the six boolean transport capability keys. Unexpected fields are rejected, including `native_session_id`, `observation_bindings`, `prompt`, `payload`, and `target`; future additions require an explicit contract update.

Projection is fail-safe: a corrupt collection or malformed protocol row produces a validation/projection error instead of silently omitting it and presenting misleading counts. These summaries are read-only facts. Rendering `agentdeck status`, ProjectView, or a consumer such as workbench does not create protocol records, append events, inspect tmux, call a provider, grant a permission, or send terminal input.

`skills.items[]` is the ProjectView summary of explicit `agentdeck skills load` records. Each item includes `load_id`, `agent_id`, `purpose`, `name`, `source`, `path`, `content_hash`, `description`, `required_tools`, bounded `planning_guidance`, `risk`, `created_at`, `show_command`, and `reload_command`. ProjectView intentionally keeps the full `content_snapshot` out of the summary so status/workbench payloads stay compact; use `agentdeck skills list` for the available skill registry with GUI-ready show/load controls, `agentdeck skills show --name <name>` for current content, and the persisted `skill_loads[]` record for replay. `planning_guidance` is an ordered list (maximum eight entries, 240 characters each); only guidance from an explicit `agent_id=leader` load enters the provider planning prompt, and it never grants execution permission. External skills must first be copied into the project with `agentdeck skills import --path <SKILL.md>` and still do not appear here until a human explicitly runs `agentdeck skills load`. The summary is read-only and does not load, install, rewrite, or enable skills.

`memory.items[]` is the ProjectView summary of applied long-term memory files under `.agentdeck/memory/`. Each item includes `scope`, relative `path`, `exists`, `line_count`, `byte_count`, `content_hash`, and a first non-empty-line `preview`. ProjectView intentionally excludes full memory content and never injects this summary into Leader or Worker prompts. Use `agentdeck memory suggestions` and `agentdeck memory apply-preview --suggestion-id <id>` to review pending memory changes, and use explicit `agentdeck memory apply --suggestion-id <id> --confirm` as the only write path.
- `chat_turns`: `count`, `by_mode`, `items[]`
- `leader_errors`: `count`, `by_mode`, `items[]`
- `leader_actions`: `count`, `by_kind`, `by_status`, `items[]`

Summary items intentionally omit long prompts and pane output. Use detail commands such as `agentdeck plan show --plan-id <id>`, `agentdeck plan status --plan-id <id>`, `agentdeck trace --id <id>`, and `agentdeck events --limit <n>` when a client needs more context.

`releases.items[]` is the ProjectView summary of explicit `agentdeck release --confirm` round releases. Each item includes `release_id`, `round`, `status`, `review_gate_status`, `artifact_count`, `review_reply_count`, `code_reviewer_id`, `round_reviewer_id`, `code_review_reply_id`, `round_review_reply_id`, `created_at`, and a `trace_command` pointing at the round-review reply lineage. It is a read-only audit history: rendering it never releases, merges, acks inbox items, or dispatches follow-up work, and the only write path stays the explicit `agentdeck release --confirm` command.

Every item in `messages.items[]`, `jobs.items[]`, `replies.items[]`, `artifacts.items[]`, and `releases.items[]` must include `trace_command`. Every message item must also include compact `prompt_skill_context`. `validate_project_view_contract()` checks every summary item, not only the first row. This gives GUI clients, natural-language shells, and humans a stable one-click path from summary rows to the full communication lineage while keeping `agentdeck trace --id <id>` as the detail source. Artifact trace commands should point at the closest linked message/job/reply lineage id, so the artifact remains recoverable without making filesystem paths a workflow source of truth.

## Leader Actions

Every item in `leader_actions.items[]` exposes GUI-safe action affordance fields. `validate_project_view_contract()` checks every Leader action item, not only the first row:

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
  "preview_command": "agentdeck leader action --action-id act_xxx",
  "controls": [
    {
      "kind": "preview",
      "label": "Preview Leader action",
      "command": "agentdeck leader action --action-id act_xxx",
      "safety": "inspect",
      "enabled": true,
      "blocker": null
    }
  ],
  "can_apply": true,
  "apply_command": "agentdeck leader apply-action --action-id act_xxx",
  "explicit_command": "agentdeck approval create-from-plan --plan-id pln_xxx",
  "apply_blocker": null,
  "is_recommended": true,
  "created_at": "2026-07-04T00:00:00+00:00"
}
```

ProjectView `leader_actions` and `agentdeck leader actions` both return a top-level `recommended_action_id`, derived from `recovery.recommended_action.target_id`, so GUI queues can highlight the active recovery affordance without opening every detail view.

`preview_command` is the safe read-only detail view for the action. `controls[]` is the GUI-ready button list; each control has `kind`, `label`, `command`, `safety`, `enabled`, and `blocker`. `can_apply=true` is currently limited to safe `create_approvals` actions. Runtime actions such as dispatch or capture stay explicit and should be shown with their blocker text.

`agentdeck leader action --action-id <id>` returns the same action detail plus the current `recovery`, the current `recommended_action`, and `matches_recommended_action`. GUI clients can use this to tell whether a selected action is the active recovery affordance before rendering an apply button.

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

Live `agentdeck leader chat` responses also include top-level `leader_actions`, identical to `project_view.leader_actions`, so a chat surface can render the current action queue without issuing a separate status call.

Live chat responses include `leader_explanation`:

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

For plan creation, `safety` is `plan_only` and `recommended_action_id` is `null`. For explicit safe apply, `safety` is `safe_apply_completed` and `result_count` records the number of approval records created.

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
    "inbox_items": 0,
    "leader_errors": 0,
    "runtime_stale": 0,
    "reply_waiting": 0
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
| `runtime_stale` | `agentdeck agent refresh` | `inspect` | first stale `agent_id` |
| `inbox_pending` | `agentdeck inbox --agent <id>` | `inspect` | first pending `inbox_id` |
| `reply_waiting` | `agentdeck capture-reply --agent <id> --message-id <id>` | `explicit_runtime` | waiting `message_id` |
| `leader_error` | `agentdeck status` | `inspect` | latest `error_id` |
| `idle` | `null` | none | none |

`recommended_action` is descriptive metadata. It never executes by itself. GUI clients must still call explicit AgentDeck commands and preserve approval boundaries.

`pending.leader_errors` counts stored Leader errors. `pending.runtime_stale` counts agent runtime bindings whose tmux pane is no longer trusted. `pending.reply_waiting` is `1` when the latest plan review is waiting for a dispatched reply and no higher-priority queue masks it. These counts do not execute anything; they help GUI clients show unresolved Leader diagnostics, reply capture work, and runtime reconciliation work alongside approvals, inbox items, and action queue work.

`agentdeck contract project-view` exposes the required pending keys as `recovery_pending_fields`, and `validate_project_view_contract()` rejects ProjectView payloads missing any of them.

## Event Timeline

`agentdeck events --limit <n>` reads `.agentdeck/state/events.jsonl` and returns recent raw audit events:

```json
{
  "count": 2,
  "limit": 20,
  "events": []
}
```

`agentdeck events --since <event_id>` returns raw audit events after a GUI-held cursor, plus cursor metadata:

```json
{
  "count": 1,
  "limit": 20,
  "since_event_id": "evt_old",
  "latest_event_id": "evt_new",
  "cursor_found": true,
  "events": []
}
```

If `cursor_found` is false, the cursor is stale or unknown and the response falls back to the limited event tail. ProjectView embeds compact `latest_event` and `recent_events` summaries for recovery. Workbench embeds `change_summary` for cheap change detection. Use `events --since` when a GUI needs timeline details after detecting new events.

## Consumer Rules

## M2 daemon and scheduler summaries

ProjectView includes top-level `daemon` and `scheduler` compact summaries.
`daemon` contains state, health, client count, controller presence, idle-exit
state, protocol compatibility, and blockers without PID, socket, nonce, or home
paths. `scheduler` contains active Mission/step/next-transition facts and
blockers. It is derived from the same immutable `SchedulerFacts` and pure
scheduler gate used by the project daemon: active work projects `running`,
permission or ambiguity waits project `waiting_human`, fail-closed authority
projects `blocked`, and a finished admitted Mission projects `terminal`. With
no daemon-managed Mission it is `inactive` with no obsolete implementation
blocker. ProjectView uses only the already loaded durable snapshot and current
project config; it never probes tmux, checks live process readiness, writes
state, or recursively renders another ProjectView. The daemon may add bounded
live readiness probes before applying the same gate. These summaries are the
source for the corresponding workbench cards.
`daemon.controller_present` is a time-aware read-only projection: the exact
persisted lease must parse, use the active `lse_` namespace, and have an aware
expiry later than the current UTC instant. Expired, terminal, naive, or malformed
lease facts project `false`; rendering ProjectView never expires, repairs, or
writes the lease.

Mission resume authority is also projected fail-closed. `can_resume` requires
either a snapshot-less legacy M1 Mission or a complete
`daemon_admission.state=admitted` frozen Mission. A partial snapshot/admission
lineage adds `daemon-managed Mission requires daemon governance resume preview`
and disables foreground resume; admitted resume enters the controller-lease-
bound daemon preview flow.

- Treat ProjectView as the default state source.
- Treat `recovery.recommended_action` as the default next-step affordance.
- Treat `requires_explicit_user=true` as a hard UI gate.
- Do not dispatch, capture, stop panes, write files, or commit git changes from GUI affordances without an explicit user command.
- Do not parse tmux pane output as workflow state when ProjectView or `trace` can answer the question.
- Keep long prompt, reply, and pane text out of ProjectView summaries.
