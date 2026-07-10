# Mission Contract

The Mission contract describes the GUI-ready, audit-friendly boundary for turning one natural-language multi-agent request into a frozen serial plan, one explicit confirmation, and a resumable status projection.

The preview path is available through Leader Chat. Explicit Mission commands now inspect, run, and resume the same frozen record; preview itself still does not inspect runtime/tmux, send input, or change approval state.

## Discovery

```bash
agentdeck contract mission
agentdeck contract mission --example
agentdeck contract list
```

Discovery is read-only. The `--example` response includes `example_preview`, `example_status`, and `example_run`; every example is validated before JSON is printed.

## Commands represented by the contract

```bash
agentdeck leader chat --message "让 Codex 和 Claude 一人一句接龙，共8轮"
agentdeck leader chat --message "批准执行 <id>"
agentdeck mission run --mission-id <id> --confirm
agentdeck mission status --mission-id <id>
agentdeck mission resume --mission-id <id> --confirm
```

Mission ids use `mis_<12 lowercase hex>`. Status, confirmation, and resume commands must contain the same canonical id. Preview confirmation is expressed as the natural-language Leader Chat command; resume retains explicit `--confirm`.

Plan ids use `pln_<12 lowercase hex>`. Plan hashes use exactly `sha256:<64 lowercase hex>`. These values are validated before a command or plan reference can be accepted.

## Preview response

`MISSION_PREVIEW_RESPONSE_FIELDS` is the exact top-level field set:

- identity/provenance: `schema_version`, `mission_id`, `user_message`, `provider`, `model`, `leader_backend`, `plan_id`, `plan_hash`
- result: `ok`, `mode=mission_preview`, `status=pending_confirmation`
- frozen scope: `plan`, `selected_agents`, `startup_actions`, `step_count`, `timeout_seconds`
- gate: `can_start`, `blockers`, `confirmation_command`
- navigation: `status_command`, `workbench_command`, `controls`
- safety: `safety=inspect`, `requires_explicit_user=true`

The example plan is a fixed serial eight-step sequence. `step_count` must equal `len(plan.steps)`, steps must be numbered 1 through N, only frozen selected agents may appear, and at least two selected agents must participate. Dynamic, parallel, DAG, or cycle metadata is rejected by the shared Mission plan validator.

`can_start` is true exactly when `blockers` is empty. The confirmation control is enabled exactly when `can_start` is true; a disabled control must carry a compact blocker.

## Selected agent and startup rows

Each `selected_agents[]` row has the exact fields:

```text
agent_id, provider, role, workspace_mode, runtime_status,
effective_model, model_source
```

Each `startup_actions[]` row has the exact fields:

```text
agent_id, action, runtime_status, effective_model, model_source
```

`action` is only `reuse` or `spawn`. Startup rows must match selected agents in order and count. `effective_model` may be null; all other values are non-empty strings. Raw commands, prompts, environment, credentials, and the state-only optional worker blocker are outside this compact contract.

For each row, `agent_id`, `runtime_status`, `effective_model`, and `model_source` must agree between `selected_agents[]` and `startup_actions[]`. Every plan step role must agree with the selected agent's role. Startup rows intentionally do not duplicate provider or role fields.

## Plan and control rows

The exact plan fields are `goal`, `summary`, and `steps`. Each step has `step`, `agent_id`, `role`, and `task`.

Every control has the exact fields:

```text
kind, label, command, safety, enabled, blocker
```

Inspect commands use `kind=inspect` and `safety=inspect`. Confirmation/resume commands use `kind=execute` and `safety=delegated`. Controls may reference only the commands declared in the enclosing payload, must expose every declared command once or more without omissions, and cannot use unrestricted safety claims.

Control `kind`, `label`, `command`, and `safety` are non-empty strings; `enabled` is a literal boolean; `blocker` is null or a non-empty string. Dict/list payloads, nested command/prompt/credential objects, and non-scalar substitutes are invalid rather than serialized or inspected.

## Status and run responses

`MISSION_STATUS_RESPONSE_FIELDS` projects the persisted facts: identity, one of the six statuses, plan/workflow ids and hash, progress bounds, selected agents, blockers/stop reason, timestamps, resume gate, terminal/workbench commands, controls, and safety metadata.

The six statuses and approved transitions are:

```text
pending_confirmation -> preparing | stopped
preparing            -> running | stopped
running              -> completed | stopped | interrupted
stopped              -> preparing | running
interrupted          -> preparing | running
completed            -> terminal
```

`can_resume` is true only for `stopped` and `interrupted`. `current_step` is an integer from zero through `step_count`. Completed status requires `completed_at`.

Lifecycle fields are validated together:

- `pending_confirmation`: `workflow_run_id`, `confirmed_at`, `completed_at`, and `stop_reason` are null.
- `preparing`: `confirmed_at` is non-empty; no workflow id is required yet; `stop_reason` and `completed_at` are null.
- `running`: `confirmed_at` and `workflow_run_id` are non-empty; `stop_reason` and `completed_at` are null.
- `completed`: `confirmed_at`, `workflow_run_id`, and `completed_at` are non-empty; `current_step == step_count`; `stop_reason` is null.
- `stopped`: `confirmed_at` and `stop_reason` are non-empty; `workflow_run_id` remains optional because setup may fail before a workflow exists; `completed_at` is null.
- `interrupted`: `confirmed_at`, `workflow_run_id`, and `stop_reason` are non-empty; `completed_at` is null.

For `stopped`/`interrupted`, `can_resume` is true only when blockers are empty. The resume control must use the same enabled value and must explain a disabled state with a non-empty blocker.

`MISSION_RUN_RESPONSE_FIELDS` is the exact status field set plus `confirmed`. A run response uses `mode=mission_run` (or `mission_resume` for resume), `safety=delegated`, `requires_explicit_user=true`, and `confirmed=true`. It must also remain a valid status projection after changing only mode/safety and removing `confirmed`.

Run/resume responses cannot report `pending_confirmation`: confirmation must be the literal JSON boolean `true`, and `confirmed_at` must be present through the lifecycle rules above.

## Confirmed runtime pipeline

`mission run --confirm` revalidates the canonical Mission id/schema, start gate, saved plan hash, exact selected ids and step count, Leader provider/model/backend, configured Worker provider/role/workspace, frozen effective model source, and startup rows before any runtime mutation. Plan or configuration drift is persisted as a non-resumable stopped Mission and creates no pane or workflow. A blocked preview is rejected without pretending that it ran.

Only frozen selected Workers may be reused or spawned. Reuse requires a running binding whose pane still exists; session creation happens at most once per preparation attempt, and a successful earlier pane/binding remains visible after a later spawn failure. No workflow is created or dispatched until provider-aware readiness reports every selected Worker ready. Setup/login/trust, pane loss, failure/model evidence, and timeout map to stable Mission stop reasons without embedding captured screen content.

The frozen startup action is authoritative. A frozen `reuse` whose pane disappeared and a frozen `spawn` that unexpectedly finds an externally-created running pane both stop as `worker_runtime_drift`; this drift is a blocker and cannot be resumed implicitly. A partial Mission spawn is recoverable only when the current binding and live pane exactly match that Mission's compact `agent_spawned` audit event (`mission_id`, `agent_id`, `pane_id`, `session_name`, `cwd`). This permits retrying the remaining Worker while preventing reuse of an unknown process or model.

After readiness, AgentDeck creates exactly one normal sequential `workflow_runs[]` record, uses the selected-only config view, and delegates all eight-turn message/job/reply/handoff semantics to the existing workflow engine. Resume reuses the same workflow id and its persisted turns, so completed or already-dispatched prompts are not sent again. Repeating run for a preparing/running/completed Mission is an idempotent status projection.

Foreground interruption is caught at the CLI boundary and persists both workflow and Mission as `interrupted`. Runtime exceptions are reduced to stable audited stop reasons; raw commands, prompts, pane output, exception details, credentials, and secrets are excluded from Mission responses and Mission audit events.

## Provenance and safety validation

Preview `leader_backend` uses the shared logical-Leader field shape. Its provider/model must match the top-level provider/model; it must remain pane-less, approval-required, and non-dispatch-ready. The contract rejects non-object roots without raising, unknown/extra top-level or compact item fields, invalid Mission/plan ids or plan hashes, command/id drift, status/lifecycle drift, selected/startup count or provenance drift, step-count drift, bool-as-int values, unsafe controls, nested sensitive objects, or a start/resume control that contradicts blockers/status.

Contract discovery never writes `.agentdeck/` or calls a provider. The separate Leader Chat preview may call the configured Leader provider and write only a validated plan, pending Mission, chat turn, and audit events; it never creates a workflow, reads a pane, starts a worker, attaches tmux, sends input, or grants execution authority. Command strings and controls are discoverable affordances, not authorization tokens.
