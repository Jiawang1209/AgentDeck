# Mission Contract

The Mission contract describes the GUI-ready, audit-friendly boundary for turning one natural-language multi-agent request into a frozen serial plan, one explicit confirmation, and a resumable status projection.

Task 4 provides discovery and examples only. It does not implement `mission run`, `mission status`, or `mission resume`, create Mission state, call a provider, inspect runtime/tmux, send input, or change approval state.

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
agentdeck mission run --mission-id <id> --confirm
agentdeck mission status --mission-id <id>
agentdeck mission resume --mission-id <id> --confirm
```

Mission ids use `mis_<12 lowercase hex>`. Status, confirmation, and resume commands must contain the same canonical id. Confirmation and resume always require `--confirm`.

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

## Plan and control rows

The exact plan fields are `goal`, `summary`, and `steps`. Each step has `step`, `agent_id`, `role`, and `task`.

Every control has the exact fields:

```text
kind, label, command, safety, enabled, blocker
```

Inspect commands use `kind=inspect` and `safety=inspect`. Confirmation/resume commands use `kind=execute` and `safety=delegated`. Controls may reference only the commands declared in the enclosing payload, must expose every declared command once or more without omissions, and cannot use unrestricted safety claims.

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

`MISSION_RUN_RESPONSE_FIELDS` is the exact status field set plus `confirmed`. A run response uses `mode=mission_run` (or `mission_resume` for resume), `safety=delegated`, `requires_explicit_user=true`, and `confirmed=true`. It must also remain a valid status projection after changing only mode/safety and removing `confirmed`.

## Provenance and safety validation

Preview `leader_backend` uses the shared logical-Leader field shape. Its provider/model must match the top-level provider/model; it must remain pane-less, approval-required, and non-dispatch-ready. The contract rejects unknown/extra top-level or compact item fields, invalid Mission ids, command/id drift, status drift, selected/startup count drift, step-count drift, unsafe controls, or a start/resume control that contradicts blockers/status.

Discovery never writes `.agentdeck/`, creates a plan/Mission/workflow, calls a Leader provider, reads a pane, starts a worker, attaches tmux, sends input, or grants execution authority. The command strings and controls are discoverable affordances, not authorization tokens.
