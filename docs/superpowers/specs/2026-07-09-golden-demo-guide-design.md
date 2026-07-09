# Golden demo guide (`agentdeck demo golden`) — Design

- **Date**: 2026-07-09
- **Status**: Approved (pending spec review)

## Context

AgentDeck now has the local-first control-plane pieces for a real multi-agent round: provider readiness checks, tmux worker runtime, Leader planning, approvals, dispatch, inbox/reply/capture-reply, review gate, release, ProjectView, workbench, dashboard, TUI, and contract discovery. The remaining product risk is no longer "can the pieces exist?" but "can an operator see and run the whole path without reverse-engineering dozens of JSON commands?"

The golden demo should prove AgentDeck is more than a pile of contracts. It should give a first-time operator a single, read-only runway from current state to a complete round:

```text
doctor/provider readiness
-> worker readiness
-> leader plan
-> approval create/list/approve
-> dispatch
-> worker reply/capture-reply
-> review gate
-> release
-> dashboard/tui/workbench inspection
```

This first slice is deliberately a guide, not an executor. It should surface the path and current blockers without spawning panes, calling providers, creating plans, approving, dispatching, capturing, or releasing.

## Goal

Add `agentdeck demo golden`, a read-only, contract-validated JSON guide that describes the recommended golden-demo flow and the next explicit command for each step. It should be safe to run in any AgentDeck project, including one where the default Leader provider is not ready. It should make the demo path discoverable from the CLI today and consumable by a future GUI tomorrow.

## Non-goals

- No automatic execution of the demo.
- No provider calls, no tmux reads/writes, no spawn, no approval, no dispatch, no capture, no reply, no release.
- No new worker lifecycle semantics or worktree cleanup.
- No remote skill or marketplace work.
- No GUI implementation in this slice.
- No attempt to perfectly infer every possible project state; the guide may be conservative and recommend existing inspection commands when state is ambiguous.

## Design

### 1. New command surface

Add a new top-level `demo` command group:

```bash
agentdeck demo golden
```

The command returns a JSON payload with:

- `ok`
- `mode = "golden_demo"`
- `demo_name = "golden"`
- `summary`
- `current_status`
- `next_command`
- `recommended_task`
- `steps[]`
- `inspection_commands[]`
- `safety`
- `source_command = "agentdeck demo golden"`

`recommended_task` should be a small code-task template, for example:

```text
Add a tiny read-only dashboard or CLI affordance, update tests, and report files changed plus verification.
```

The guide should not create that task automatically. Commands that need real input should keep placeholders such as `<task>`, `<plan_id>`, `<approval_id>`, `<agent_id>`, and `<message_id>` and mark the step disabled until the operator fills them or until state provides a concrete id.

### 2. Step model

Each step is a GUI-ready item:

- `step_id`
- `title`
- `status`
- `command`
- `enabled`
- `blocker`
- `safety`
- `description`
- `checks[]`

Suggested `step_id` sequence:

1. `doctor`
2. `leader_provider`
3. `workers`
4. `plan`
5. `approval`
6. `dispatch`
7. `reply`
8. `review_gate`
9. `release`
10. `inspect`

Statuses should stay simple and deterministic:

- `ready`: command can be run as-is.
- `blocked`: the guide knows a prerequisite is missing.
- `waiting_for_input`: command contains placeholders or requires the operator to choose an id.
- `done`: existing state shows this step has already happened.
- `inspect`: read-only inspection step.

Safety values should reuse existing vocabulary:

- `inspect`
- `explicit_user`
- `explicit_runtime`

### 3. State awareness

`agentdeck demo golden` should reuse existing project facts rather than becoming a second state source:

- ProjectView from the same helper used by `agentdeck status`.
- Provider readiness facts from the existing doctor/provider-health helpers where practical.
- Runtime readiness from ProjectView/workbench-derived agent summaries.
- Plans/approvals/messages/replies/releases from ProjectView summaries.

The guide should be conservative:

- If the configured Leader provider is not ready, `current_status` should indicate `provider_setup_required`, and `next_command` should be `agentdeck doctor`.
- If no workers are running, the worker step should point at `agentdeck agent spawn-ready --confirm`.
- If no plan exists, the plan step should point at `agentdeck leader plan --task <task>` and be `waiting_for_input`.
- If approvals exist, the approval step can point at `agentdeck approval list` or a concrete approve command only when an approval id is available.
- If review gate is blocked, the review/release steps should point at `agentdeck workbench` or `agentdeck leader chat --message "查看验收门"` rather than pretending release is ready.
- If a release already exists, the release step can be `done` and the inspect step should point at `agentdeck workbench` / `agentdeck dashboard`.

### 4. Contract discovery

Add demo contract discovery:

```bash
agentdeck contract demo
agentdeck contract demo --example
```

Add:

- `docs/contracts/demo-schema.md`
- `DEMO_GOLDEN_RESPONSE_FIELDS`
- `DEMO_GOLDEN_STEP_FIELDS`
- `demo_contract_payload(...)`
- `demo_contract_response(..., include_example=True)`
- `validate_demo_golden_contract(payload)`
- contract-index registration as `demo`

The live command should validate before printing. If validation fails, return non-zero and do not print half-baked JSON.

### 5. Documentation

Update:

- `README.md`: add a concise "Golden demo" section near current commands.
- `docs/contracts/demo-schema.md`: explain fields, safety, and read-only boundary.
- `HISTORY.md`: add the development record in the same style as existing entries.
- `docs/handoff/current-development-state.md`: mark the golden-demo lane as opened and identify the next implementation slice if needed.

## Safety Boundary

`agentdeck demo golden` is read-only:

- It must not write `.agentdeck/state`.
- It must not append events.
- It must not call a Leader provider.
- It must not read, write, or send input to tmux.
- It must not create plan/action/approval/message/job/inbox/reply/release records.
- It must not execute any command it recommends.

Rendering the guide is not authorization. Every mutating action remains an explicit human command.

## Testing

- Empty/new project with default DeepSeek and no key: payload validates, `current_status=provider_setup_required`, `next_command=agentdeck doctor`, provider step blocked or setup-oriented.
- Project with configured fake or ready CLI Leader and no plan: plan step points at `agentdeck leader plan --task <task>`, marked `waiting_for_input`.
- Project with configured agents not running: worker step points at `agentdeck agent spawn-ready --confirm`, safety `explicit_runtime`.
- Seeded plan + pending approval: approval step points at `agentdeck approval list` or concrete approve command, dispatch/reply/release remain blocked or waiting.
- Seeded review-gate-ready state: release step exposes `agentdeck release --confirm` with safety `explicit_user`.
- Already released state: release step is `done`, inspect step recommends `agentdeck workbench` / `agentdeck dashboard`.
- Read-only test: state files and event count unchanged after `agentdeck demo golden`.
- Contract tests: `agentdeck contract demo`, `--example`, contract index, validator rejection cases.
- Full suite and `python -m compileall src tests -q` remain green.

## Resolved Decisions

- First slice is `agentdeck demo golden`, not a workbench card.
- Output is JSON, not a human text script, so GUI/TUI can consume it later.
- The guide is state-aware but conservative; it recommends existing explicit commands and does not infer risky transitions.
- Add a contract entry now to keep the golden demo aligned with the project's GUI-ready architecture.
- The recommended task is a small code-task template, but the guide never creates it automatically.
