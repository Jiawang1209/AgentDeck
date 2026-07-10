# Sequential Workflow Engine — Design

- **Date**: 2026-07-10
- **Status**: Approved

## Context

AgentDeck can create approval-gated Leader plans, dispatch approved steps to visible tmux workers, capture structured replies, and stop at a human gate. Its current `run-loop` deliberately never captures replies and never infers completion. That boundary is correct for ordinary plans, but it means a human must remain present to capture Worker 1's reply and dispatch Worker 2.

The target capability is a separately authorized, bounded sequential workflow:

```text
Worker 1 completes
-> AgentDeck captures and validates the correlated reply
-> Worker 2 becomes ready
-> AgentDeck dispatches Worker 2 with a compact handoff
```

The motivating demo is a Codex/Claude alternating recitation, but the production abstraction is a general linear handoff engine. No recitation content belongs in the core engine.

## Goal

Add an additive `agentdeck workflow` command family that interprets an existing Leader plan's ordered `steps[]` as a fixed linear dependency chain only after one explicit human confirmation. The foreground runner automatically dispatches each step, waits for a correlated structured reply, records the reply and artifacts, builds a compact upstream handoff, and dispatches the next step until completion or a bounded stop condition.

## Non-goals

- No DAG dependencies, fan-out, fan-in, or cycles in this slice.
- No background daemon or tmux-hosted controller; the runner is a foreground command.
- No `sequential-handoff` skill in this slice.
- No Codex/Claude live recitation in this slice.
- No changes to ordinary `run-loop`, approval, dispatch, or capture-reply semantics.
- No automatic expansion of Leader plans, agents, tasks, permissions, or step count.
- No provider call during workflow preview/run/resume.

## Command Surface

### Read-only preview

```bash
agentdeck workflow preview --plan-id <id> [--timeout <seconds>]
```

The preview validates the plan and runtime prerequisites and returns a GUI-ready payload containing:

- `mode=workflow_preview`
- `plan_id`
- ordered step summaries
- a stable `plan_hash`
- `timeout_seconds`
- blockers and `can_run`
- the explicit confirm command
- inspect controls
- `safety=inspect`

Preview never writes state, appends events, calls a provider, reads a pane, or sends tmux input.

### Foreground execution

```bash
agentdeck workflow run --plan-id <id> [--timeout <seconds>] --confirm
```

`run` requires `--confirm`. It creates a workflow record and session-level authorization that freezes:

- plan id and hash
- ordered step numbers, agents, roles, and task hashes
- timeout
- maximum step count (the frozen plan length)

It then processes the chain synchronously. The final response reports `completed` or `stopped`, completed turns, current step, stop reason, trace commands, and status/resume controls.

### Read-only status

```bash
agentdeck workflow status --run-id <id>
```

Status reads the persisted workflow record and projects its current state without runtime access or writes.

### Explicit resume

```bash
agentdeck workflow resume --run-id <id> --confirm
```

Resume validates the frozen plan hash and participant bindings. If the current step already has a dispatched message, resume waits for that same correlated reply and never dispatches it again. Completed steps are never repeated.

## Linear State Model

A workflow record has:

- `run_id`
- `plan_id`
- `plan_hash`
- `status`: `running | completed | stopped | interrupted`
- `current_step`
- `step_count`
- `timeout_seconds`
- frozen `authorized_steps[]`
- `turns[]`
- `stop_reason`
- `created_at` / `updated_at` / `completed_at`

Each authorized step stores only the frozen authorization facts needed to detect drift:

- `step`
- `agent_id`
- `role`
- `task`
- `task_hash`

Each completed or active turn stores:

- `step`
- `agent_id`
- `handoff_token`
- `status`: `pending | dispatched | completed | blocked | failed | timed_out`
- `message_id` / `job_id` / `reply_id`
- compact handoff fields
- artifact ids/paths
- trace command
- timestamps

The state lives inside the existing `.agentdeck/state/state.json` under `workflow_runs[]`. Events remain in the existing JSONL audit ledger.

## Plan Hash and Authorization

The engine computes a deterministic SHA-256 hash over a canonical JSON representation of the plan id and ordered step authorization fields. `run` saves it. `resume` recomputes it from the current plan record and refuses on drift before reading or writing tmux.

The single `--confirm` authorizes only the frozen chain. It does not authorize:

- a new plan
- a changed task or role
- a new agent
- a larger step count
- a different timeout
- arbitrary direct dispatch outside the workflow

## Dispatch and Correlated Completion

Each step receives a workflow-specific prompt that includes:

- its original Leader task and role prompt
- the compact handoff from the previous completed step, when present
- a unique `handoff_token` derived from run id and step number
- a strict structured reply schema

Required reply fields:

```text
handoff_token: <token>
status: completed | blocked | failed
summary: <text>
verification: <text>
risks: <text>
next_steps: <text>
full_output_path: <optional path>
```

The runner captures pane output and accepts only the newest structured block whose token exactly matches the active turn. This prevents stale `status:` output from satisfying a later step.

On a matching reply:

- `completed`: record the reply/artifacts, mark the turn completed, build compact handoff, and dispatch the next step.
- `blocked`: record the reply, stop the workflow with `worker_blocked`.
- `failed`: record the reply, stop the workflow with `worker_failed`.

The compact downstream handoff contains the upstream agent/step, summary, verification, risks, next steps, artifact summaries, and trace command. It excludes full pane history and unrelated state.

## Runtime Loop

The foreground loop uses short condition-based polling until the per-step timeout. It does not use a fixed long sleep. Runtime access is restricted to the active step's configured running pane.

For testability, pure state-transition and parsing helpers remain separate from I/O. The runner accepts the existing runtime backend at the CLI boundary, while unit tests use `FakeTmuxBackend` and an injected clock/poll function.

## Stop Conditions

The engine stops immediately and persists evidence when:

- the target agent is unknown or not running
- the pane disappears
- the active reply times out
- output has a matching token but invalid/missing required fields
- the Worker reports `blocked` or `failed`
- plan hash or authorized step facts drift
- an internal contract validation fails

No later step is dispatched after a stop. The response always identifies the stopped step, agent, reason, status command, and whether resume is permitted.

## Audit Events

At minimum:

- `workflow_started`
- `workflow_step_dispatched`
- `workflow_step_completed`
- `workflow_stopped`
- `workflow_resumed`
- `workflow_completed`
- `workflow_contract_failed`

Events include ids and compact provenance, never full prompts or secrets.

## Contracts and Documentation

Add:

- `agentdeck contract workflow`
- `agentdeck contract workflow --example`
- `docs/contracts/workflow-schema.md`
- contract-index registration
- validators for preview, status, and run/resume final responses

Update:

- `README.md`
- `CLAUDE.md`
- `AGENT.md`
- `HISTORY.md`
- `docs/handoff/current-development-state.md`

## Safety Boundary

- Existing commands are unchanged.
- Preview/status are strictly read-only.
- Run/resume require explicit confirmation.
- The workflow authorization is plan-scoped, hash-pinned, agent-scoped, step-count-bounded, and timeout-bounded.
- The engine never spawns or force-spawns agents.
- The engine never calls a Leader provider.
- The engine never acknowledges inbox items automatically.
- Worker tool/file permissions remain enforced by the configured worker runtime; the workflow authorization itself grants no new tool permission.
- The engine records provenance but does not treat provenance as authorization.

## Testing

### Pure helpers

- deterministic plan hash
- authorization drift detection
- correlated reply parsing ignores stale/wrong tokens
- compact handoff construction
- linear next-step transition

### CLI and state

- preview is read-only and reports blockers
- run refuses without `--confirm`
- two-step fake-runtime chain dispatches Step 1, captures a matching completed reply, then dispatches Step 2
- Step 2 prompt contains only the compact Step 1 handoff
- blocked/failed/timeout/pane-loss stop without dispatching later steps
- matching token with invalid structure stops safely
- resume waits on an existing dispatched message without duplicate dispatch
- plan drift blocks resume before runtime access
- final state and audit events are consistent
- live responses validate before printing
- ordinary run-loop/capture-reply behavior remains unchanged

### Verification

Run focused workflow/contract tests, existing run-loop/capture-reply regression tests, the full suite, compileall, CLI contract smoke, and `git diff --check` in the `agentdeck` conda environment.

## Delivery Slices

This spec covers only slice 1: the core sequential workflow engine, contract, documentation, and deterministic fake-runtime tests.

Deferred follow-ups require their own design/plan cycles:

1. Built-in `sequential-handoff` skill for Leader planning guidance.
2. Real Codex/Claude unattended recitation acceptance test.
3. DAG and cyclic/repeat workflow semantics.

## Resolved Decisions

- The product abstraction is a linear dependency workflow, not a recitation-specific relay.
- Human authorization occurs once before the run.
- Machine validation happens every step; humans are involved only before start or after stop/completion.
- The MVP is foreground and resumable.
- Existing ordered plan steps become a dependency chain only through explicit workflow commands.
- Correlation tokens are mandatory to prevent stale pane output from advancing the chain.
