# Sequential Workflow Contract

`agentdeck workflow` turns the ordered steps of an existing Leader plan into an explicitly authorized linear handoff chain. It is additive: ordinary approvals, `dispatch`, `capture-reply`, and `run-loop` keep their existing semantics.

## Discovery

```bash
agentdeck contract workflow
agentdeck contract workflow --example
```

The discovery response exposes the preview/status field lists, frozen step fields, turn fields, status enums, stop reasons, and examples. `agentdeck contract list` includes the same contract.

## Commands

```bash
agentdeck workflow preview --plan-id <id> [--timeout <seconds>]
agentdeck workflow run --plan-id <id> [--timeout <seconds>] --confirm
agentdeck workflow status --run-id <id>
agentdeck workflow resume --run-id <id> --confirm
```

`preview` and `status` are read-only and return `safety=inspect`. They do not call a provider, inspect pane output, send tmux input, append events, or modify state.

`run` and `resume` require `--confirm`. That confirmation delegates only the frozen plan id/hash, ordered step and agent set, original tasks, timeout, and step-count bound. It does not grant new tool permissions or authorize new agents, tasks, steps, plans, or timeouts.

Their final responses use `mode=workflow_run` or `mode=workflow_resume`, `safety=delegated`, `requires_explicit_user=true`, and `confirmed=true`. The remaining status/turn/control fields are the same persisted projection returned by `workflow status`.

## Preview response

The response uses `mode=workflow_preview` and contains:

- `plan_id`, deterministic `plan_hash`, positive `timeout_seconds`, and `step_count`
- ordered `steps[]` with `step`, `agent_id`, `role`, `task`, `task_hash`, `runtime_status`, `pane_id`, `ready`, and `blocker`
- `blockers[]`, `can_run`, an explicit `confirm_command`, and controls

When any blocker exists, `can_run` is false. Preview uses stored runtime bindings only and does not instantiate or query the tmux backend.

## Persisted status

A workflow record is stored in `.agentdeck/state/state.json` under `workflow_runs[]` and is projected as `mode=workflow_status`, `safety=inspect`. Run status is one of:

```text
running | completed | stopped | interrupted
```

Each turn records the correlated token and lineage ids with status:

```text
pending | dispatched | completed | blocked | failed | timed_out
```

`can_resume` is true only for `stopped` or `interrupted` records. Resume reuses an already dispatched active turn and never sends it twice. Completed turns are never repeated.

## Correlated worker reply

Each dispatched prompt contains a unique token derived from workflow run id and step number. Only the newest block with the exact active token is accepted:

```text
handoff_token: <token>
status: completed | blocked | failed
summary: <text>
verification: <text>
risks: <text>
next_steps: <text>
full_output_path: <optional path>
```

Stale or unrelated pane text cannot advance the chain. A matching malformed block stops the workflow. A completed reply produces a compact handoff for the next step; full pane history is never forwarded.

## Stop and audit semantics

The engine stops before dispatching any later step on agent/runtime loss, timeout, malformed reply, worker block/failure, plan drift, contract failure, or interruption. Stop reasons are discoverable through the contract.

Audit events include `workflow_started`, `workflow_step_dispatched`, `workflow_step_completed`, `workflow_stopped`, `workflow_resumed`, `workflow_completed`, and `workflow_contract_failed`. Events store compact provenance, not full prompts or secrets.

The foreground runner never spawns agents, calls the Leader provider, acknowledges inbox items, or changes worker tool/file permissions.
