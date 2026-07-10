# Natural-Language Mission Orchestration Design

Date: 2026-07-10

## Problem

AgentDeck already has provider-backed Leader planning, configured Codex/Claude Workers, tmux runtime bindings, audited skill provenance, sequential workflow execution, resumable correlated handoffs, ProjectView, and GUI-ready controls. The user experience is still fragmented: a human must know agent ids, edit Worker commands, load a planning skill, extract a plan id, spawn panes, run a workflow preview, and finally invoke the workflow runner.

The product goal is a bounded natural-language Mission facade:

```bash
agentdeck leader chat --message "让 Codex 和 Claude 一人一句接龙百家姓，共8轮"
agentdeck leader chat --message "批准执行 mis_xxx"
```

The first message creates one complete, non-runtime preview. The second message is the only overall execution confirmation. After it, AgentDeck prepares exactly the frozen Workers and runs the complete fixed sequential workflow without per-step approval.

## Goals

- Turn one natural-language multi-Agent execution request into a persisted Mission preview.
- Select configured Codex/Claude Workers without requiring the human to know agent ids or roles.
- Generate and freeze a fixed sequential Leader plan with a plan id/hash, selected Worker set, step bound, and timeout.
- Require exactly one explicit human confirmation before spawning panes or sending Worker input.
- After confirmation, prepare only the selected tmux Workers, wait for provider-aware CLI readiness, and execute the existing sequential workflow engine.
- Persist Mission status, workflow linkage, compact progress, blockers, controls, and audit events into the authoritative state and ProjectView.
- Make confirmation, inspection, and recovery available through natural language while keeping explicit CLI commands for contract/GUI consumers.
- Prove the flow with deterministic tests and a real Codex/Claude eight-turn 百家姓 acceptance run.

## Non-goals

- Parallel, DAG, cyclic, repeated, or runtime-created Mission steps.
- Automatic `/login`, directory trust confirmation, or credential entry.
- Silent skill installation, import, load, or enablement.
- GUI implementation, remote relay, marketplace, daemon, or unbounded autonomy.
- Replacing the existing workflow engine, message/reply ledger, or ProjectView source of truth.
- Persistently rewriting Worker roles, commands, providers, or models as a side effect of a Mission.

## Chosen Approach

Add a stateful Mission orchestration layer that composes existing primitives. Do not implement a transient Leader Chat macro and do not expand the autonomous run-loop.

A Mission is a durable bounded authorization envelope. Its preview freezes what a later confirmation may do. The confirmation authorizes only the stored plan hash, selected agents, effective launch models, timeout, and ordered steps. It does not authorize new agents, plan changes, extra steps, new tools, or different timeouts.

## User Flow

### Create preview

The user sends an ordinary natural-language request:

```bash
agentdeck leader chat --message "让 Codex 和 Claude 一人一句接龙百家姓，共8轮"
```

AgentDeck:

1. Routes the message to Mission intake only when it contains an execution intent plus a multi-Agent signal.
2. Resolves a deterministic configured Worker set.
3. Calls the configured Leader provider with only that selected Worker subset and Mission-specific fixed-sequence instructions.
4. Validates and saves the provider plan.
5. Creates a `missions[]` record linked to the plan id/hash.
6. Returns `mode=mission_preview` with `mission_preview_card`, selected Workers, steps, blockers, `can_start`, and one confirmation control.

Preview may call the Leader planning provider and write plan/Mission/chat/audit state. It must not inspect pane output, spawn/stop panes, send tmux input, create a workflow run, or load a skill.

### Confirm once

The user confirms the frozen Mission:

```bash
agentdeck leader chat --message "批准执行 mis_xxx"
```

The same operation is available to non-chat consumers as:

```bash
agentdeck mission run --mission-id mis_xxx --confirm
```

AgentDeck atomically claims the pending Mission, revalidates plan/hash and setup blockers, prepares selected Workers, waits for readiness, creates the workflow run, and executes it in the foreground. The final response uses `mode=mission_run` and links the Mission, plan, workflow, turns, status, stop reason, attach command, status command, and controls.

Mission confirmation is its own bounded approval record. It does not auto-approve or consume unrelated entries in the ordinary `approvals[]` queue. Plan steps retain `requires_approval=true` as provider-plan provenance, while the confirmed Mission id/hash/agent/step envelope is the explicit authorization used by this execution path.

### Inspect and recover

Natural language:

```text
查看 mission mis_xxx
继续 mission mis_xxx
```

Explicit equivalents:

```bash
agentdeck mission status --mission-id mis_xxx
agentdeck mission resume --mission-id mis_xxx --confirm
```

An id-less `批准执行` or `继续 mission` is accepted only when exactly one matching Mission exists. Ambiguous selection returns a blocker and never guesses.

## Natural-Language Routing

Existing explicit help/status/skill/memory/trace/approval/runtime intents keep higher priority. Mission intake requires both:

- an execution token such as `让`, `执行`, `开始`, `协作`, `完成`, or `run`; and
- a multi-Agent signal: two or more configured agent ids, two or more provider names, or terms such as `多个智能体`, `两个 Agent`, `协作`, `交替`, `接龙`, or `依次`.

Mission confirmation/status/resume intents are matched before generic approval or plan intents and require a `mis_...` id unless the relevant queue contains exactly one candidate.

This deterministic router decides only the product lane. The configured Leader provider still performs semantic planning.

## Worker Selection

Selection is deterministic and previewable:

1. Explicit configured agent ids win.
2. Explicit provider names select one configured Worker for each provider family.
3. For multiple candidates in one provider family, prefer a currently running binding, then `workspace_mode=shared`, then configuration order.
4. If the user asks only for multiple Agents, choose the first two distinct available provider families using the same ranking.
5. A Mission requires at least two selected Workers. Missing provider coverage, duplicate-only candidates, unknown requested agents, or unsupported provider commands produce blockers.

The Leader provider receives a temporary ProjectConfig view containing only the selected Workers. The persisted project config is not modified. The normal provider-plan validator therefore prevents the provider from adding an unconfirmed Worker and still requires exact configured roles, consecutive steps, non-empty task/risk fields, and `requires_approval=true` on every step.

For fixed sequential Mission v1, the Mission validator additionally requires:

- at least two steps and at least two selected agents;
- step agent ids contained in the frozen selected set;
- no dynamic/parallel/DAG/cycle metadata;
- a bounded positive timeout and step count;
- a deterministic plan hash matching the saved plan at confirmation and resume.

The workflow engine itself provides the ordering guarantee: it dispatches only the current step and always passes the previous validated compact handoff to the next step.

## Skill Boundary

Mission orchestration does not silently load `sequential-handoff` or any other skill. Fixed sequential planning is a Mission core contract, not hidden skill context.

Already explicitly loaded Leader skills remain visible to the provider and remain frozen in normal plan provenance. They are optional and cannot grant execution permission. External/project skills retain the existing preview/import/load gates. This satisfies the user requirement that Mission not require a manual skill-load step without weakening Skill Registry provenance or introducing a second implicit load path.

## Mission State

Add authoritative `missions[]` records with these stable concepts:

- `mission_id`, `schema_version`, `user_message`;
- `status`, `stop_reason`, `can_start`, `blockers[]`;
- Leader provider/model/backend provenance;
- `plan_id`, `plan_hash`, `step_count`, `timeout_seconds`;
- `selected_agents[]` with agent id, provider, configured role/workspace mode, effective model, and model source;
- frozen `startup_actions[]` describing reuse or spawn for each selected Worker;
- `workflow_run_id` and compact workflow progress;
- confirmation/status/resume/attach controls;
- `created_at`, `updated_at`, `confirmed_at`, `completed_at`.

Mission statuses:

```text
pending_confirmation
preparing
running
completed
stopped
interrupted
```

Transitions:

```text
pending_confirmation -> preparing -> running -> completed
pending_confirmation -> stopped      (revalidation/setup failure)
preparing            -> stopped      (spawn/readiness failure)
running              -> stopped      (workflow stop)
running              -> interrupted  (foreground interruption)
stopped/interrupted  -> preparing/running through explicit resume
```

Repeated confirmation of a preparing/running/completed Mission returns current status and creates no duplicate workflow. A stopped/interrupted Mission resumes the same frozen plan and, when a workflow already exists, uses existing workflow resume semantics so dispatched or completed turns are not resent.

## Effective Worker Launch

Mission must not require the user to edit TOML merely to pin a compatible Worker model.

For each selected Worker, derive and freeze an effective launch model:

1. Preserve an explicit `--model`/`-m` already present in the configured Worker command.
2. Otherwise, when the Worker provider family matches the configured Leader CLI provider family, inherit the configured Leader model for this Mission.
3. Otherwise, leave the configured Worker command unchanged and mark the model source as `provider_default`.

The derived command is used only for a newly spawned Mission Worker and does not rewrite `.agentdeck/config.toml`. The Mission stores compact effective-model provenance, not credentials or shell environment values. Reused running Workers keep their existing process/model and are identified as `running_binding`; Mission must not claim that a reused process inherited a model it did not launch with.

## Runtime Preparation and Readiness

Preview uses configuration, provider doctor facts, and stored bindings. It does not inspect pane contents.

After confirmation:

1. Reconcile stored bindings with tmux.
2. Reuse selected running Workers.
3. Spawn only selected missing Workers using the frozen effective launch configuration.
4. Probe every selected pane until ready, setup-required, failed, pane-lost, or timeout.
5. Start no workflow until every selected Worker is ready.

Add provider-aware readiness adapters with a shared result shape:

```text
starting | ready | setup_required | failed
```

Codex and Claude adapters recognize stable CLI prompt/setup/error evidence, including login, directory trust, startup progress, and incompatible-model errors. Pane existence alone is never sufficient. Readiness probing is bounded and occurs only after explicit Mission confirmation.

If one Worker fails after another was spawned, AgentDeck keeps the visible panes and bindings for human repair, records every startup result, sets `stopped(worker_setup_required)` or the precise runtime reason, and sends no workflow task. Resume reuses or respawns only agents in the frozen selected set.

## Workflow and Audit Integration

Mission execution calls the existing workflow helpers rather than shelling out to CLI commands. It creates the normal `workflow_runs[]`, messages, attempts, jobs, replies, inbox items, artifacts, and workflow events.

New audit events:

```text
mission_preview_created
mission_confirmed
mission_worker_preparing
mission_worker_ready
mission_worker_blocked
mission_workflow_started
mission_stopped
mission_resumed
mission_completed
```

Events contain ids, compact provenance, statuses, and reasons, not full prompts, pane history, credentials, or secrets.

## ProjectView and Contracts

Add one contract source and reuse it everywhere:

- `agentdeck contract mission` / `--example`;
- `docs/contracts/mission-schema.md`;
- contract index registration;
- ProjectView top-level `missions` summary;
- workbench `mission_card` for the latest active/recent Mission;
- Leader Chat `mission_preview_card`, `mission_run_card`, and `mission_status_card`;
- filtered control-registry entries for Mission inspect/confirm/resume/attach controls.

All cards are projections of authoritative Mission/plan/workflow state. GUI/TUI consumers must not scan tmux or private state files to derive Mission status. A control is not an authorization token; command, safety, enabled, and blocker must remain aligned and validators must reject drift.

## Error and Safety Rules

- No pane mutation or Worker input before explicit Mission confirmation.
- No partial workflow dispatch: every selected Worker must pass readiness first.
- Plan drift, selected-agent drift, unknown Mission, non-pending confirmation, ambiguous id-less confirmation, provider failure, setup blocker, timeout, pane loss, invalid reply, or workflow failure stops safely and is audited.
- Confirmation authorizes one frozen bounded Mission only.
- Mission never approves arbitrary pre-existing approvals, executes unrelated controls, stops unrelated panes, loads skills, changes tool permissions, commits, pushes, or expands its Worker/step set.
- Login and directory trust remain human actions.

## Testing Strategy

Every implementation behavior follows RED -> GREEN -> REFACTOR.

### Pure/unit coverage

- Mission intent and confirmation/status/resume parsing.
- Deterministic Worker/provider selection and blockers.
- Effective model inheritance and command preservation.
- Mission plan validation, hash drift, state transitions, and idempotency.
- Codex/Claude readiness fixtures for ready, starting, trust/login, model error, timeout, and pane loss.

### State/contract coverage

- `missions[]` persistence and projection.
- ProjectView, workbench, Leader Chat, control registry, contract discovery, examples, and validators.
- Read-only status paths do not call providers, inspect tmux, or mutate state.

### Deterministic end-to-end coverage

With a fake Leader and fake tmux:

1. Send one natural-language multi-Agent task.
2. Assert a complete preview and zero runtime mutation.
3. Confirm once through natural language.
4. Assert only selected Workers are prepared.
5. Complete every correlated turn.
6. Assert Mission, plan, workflow, message/reply/trace, ProjectView, and audit consistency.

Negative end-to-end cases cover partial startup, readiness blocker, duplicate confirmation, multiple pending Missions, plan drift, interruption, resume, and no duplicate dispatch.

### Real acceptance

Use an isolated temporary git/AgentDeck project with already authenticated/trusted Codex and Claude CLIs and a configured working Leader provider. Do not manually assign roles, edit TOML, load a skill, spawn Workers, extract a plan id, or run workflow preview/run.

Only the following task/confirmation interaction may initiate the Mission:

```text
让 Codex 和 Claude 一人一句接龙百家姓，共8轮
批准执行 mis_xxx
```

Acceptance requires eight alternating completed turns, correct opening 32 surnames, visible tmux panes, completed Mission/workflow state, compact handoff lineage, ProjectView visibility, and complete audit evidence. Save the evidence under `docs/validation/`.

## Documentation and Commit Discipline

Every user-visible implementation slice updates `HISTORY.md` in the same commit. Contract/ProjectView/Leader Chat changes update their durable schema docs and discovery index. The final slice updates README and `docs/handoff/current-development-state.md`.

All development and verification run in the `agentdeck` conda environment. Each slice uses focused tests, compile checks where relevant, `git diff --check`, and a local commit. Final completion additionally requires the full pytest suite and real CLI acceptance. No push is performed.

## Completion Criteria

The feature is complete only when current repo/runtime evidence proves all of the following:

- One ordinary natural-language request produces a valid Mission preview.
- The human performs exactly one overall execution confirmation.
- No manual role assignment, TOML edit, skill load, Worker spawn, plan-id extraction, workflow preview, or workflow run is needed.
- AgentDeck selects and displays configured Codex/Claude Workers deterministically.
- Only selected Workers are prepared and all pass readiness before first dispatch.
- The frozen plan executes through the existing sequential workflow with no per-step human approval.
- Mission status/progress/recovery is visible through state, ProjectView, workbench, contracts, Leader Chat, tmux, and audit events.
- Failure and resume paths are bounded, idempotent, and do not duplicate dispatch.
- Deterministic tests, full regression, compileall, contract smoke, diff checks, and real eight-turn acceptance all pass.
