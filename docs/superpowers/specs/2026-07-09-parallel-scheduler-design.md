# Parallel scheduler (`agentdeck run-loop --all`) — Design

- **Date**: 2026-07-09
- **Status**: Approved (pending spec review)

## Context

Final slice of the multi-plan lane, and the first **write-capable** multi-plan command. The read-only visibility is done (`plan board`, workbench `plan_board_card`, dashboard Plans section, TUI plans view, NL `mode=plan_board`). Single-plan driving already exists: `agentdeck run-loop --plan-id <id> --confirm` performs one sanctioned autonomous wave (auto-approve allowlisted pending within budget + dispatch approved-and-ready to running panes) then stops at the plan's gate.

The human resolved the four core forks:
- **调度策略 = 轮转 (round-robin)**: iterate active plans in creation order, advancing each once per wave.
- **agent 冲突 = 跳过 (skip)**: an agent with a dispatched-but-unreplied step is "busy"; do not send it another task this wave — skip that step (leave it approved) and report it.
- **一次跑多远 = 一波 (one wave)**: one invocation makes a single round-robin pass, then stops; the human re-runs for another wave.
- **复用 = reuse**: each plan's advance reuses the existing wave primitives (`select_auto_approvals`, `_approval_dispatch_preview_card`, `_dispatch_approved_approval`, `run_loop_gate`).

Confirmed sub-decisions: budget is **shared across the wave** (total auto-approvals ≤ `max_approvals`); "busy" = **dispatched-but-unreplied**; command form is **`run-loop --all`** (not a new `schedule` command).

## Goal

`agentdeck run-loop --all --confirm` drives every active plan forward one round-robin wave — auto-approving allowlisted pending approvals within one shared budget and dispatching approved-and-ready steps to running panes, skipping any step whose target agent is already busy — then stops, returning a per-plan board report. Fully audited, approval-gated, no force-spawn, no auto-capture.

## Non-goals

- No change to single-plan `run-loop --plan-id` behavior (it keeps NO contention/no-shared-budget semantics; the scheduler layer is additive).
- Not a continuous loop: exactly one round-robin pass per invocation.
- No queueing on contention (the human chose skip); no agent fan-out within a plan.
- No provider calls, no tmux reads, no force-spawn, no reply capture. Same safety envelope as `run-loop`.

## Design

### 1. Command surface

Extend the existing `run-loop` subparser: `--plan-id` becomes optional, add `--all` (flag). Exactly one of `--plan-id` / `--all` is required (else error, no writes). `--confirm` and autonomous mode are required for both, identically to today.

`run_loop_command` branches: `if args.all: return _run_loop_all(config, store)` else the existing single-plan body (unchanged).

### 2. Pre-wave "busy agents"

`_busy_agents(store) -> set[str]`: scan every plan's `plan_status` steps; an `agent_id` is busy if it has a step with `approval_status == "dispatched"` and a `message_id` that has no reply yet (reuse the `replies`-by-message-id check `leader_review` already uses). This is the set of agents occupied at the START of the wave.

### 3. The round-robin wave (`_run_loop_all`)

1. Active plans = `store.list_plans()` in creation order, filtered to those whose gate (`run_loop_gate(leader_review(plan_id), False, plan_id)`) is `!= "complete"`. (Completed plans are skipped entirely.)
2. `busy = _busy_agents(store)`; `budget_remaining = config.autonomous.max_approvals`.
3. For each active plan, in order, run the shared wave primitive `_run_loop_plan_wave(...)`:
   - **auto-approve**: `selected, skipped = select_auto_approvals(pending_for_plan, allowed_agents, budget_remaining)`; approve each selected (`decide_approval(..., reason="autonomous")` + `approval_decided` event `source="autonomous"`); `budget_remaining -= len(selected)`.
   - **dispatch** approved-and-ready for the plan: for each approved approval, if its `agent_id` in `busy` → record `skipped_contention` (leave approved, no dispatch); elif `_approval_dispatch_preview_card` blocker → record `blocked`; else `_dispatch_approved_approval(...)`, record `dispatched`, and add the agent to `busy` (so later plans this wave skip it). Dispatch exceptions → `has_error` for that plan.
   - **diagnose**: `gate, next_command = run_loop_gate(leader_review(plan_id), has_error, plan_id)`.
   - return the per-plan result: `{plan_id, task, auto_approved, dispatched[], blocked[], skipped[], skipped_contention[], gate, next_command}`.
4. Append one `run_loop_all_advanced` summary event (totals: plans_advanced, auto_approved, dispatched, blocked, skipped_contention).
5. Output (self-validated by `validate_run_loop_all_contract` before printing; failure → non-zero, nothing half-baked):
   - `ok`, `mode="run_loop_all"`, `requires_explicit_user=true`, `safety="delegated"`
   - `plan_count`, `active_count`
   - `budget` `{max_approvals, used, remaining}`
   - `totals` `{auto_approved, dispatched, blocked, skipped_contention}`
   - `plans[]` — the per-plan result dicts above (reusing the run-loop result shapes for `dispatched`/`blocked`/`skipped`)

### 4. Contract (project convention)

Add to `src/agentdeck/contracts.py`: `RUN_LOOP_ALL_RESPONSE_FIELDS`, `RUN_LOOP_ALL_PLAN_FIELDS`, `run_loop_all_example()`, `run_loop_all_contract_payload/response`, `validate_run_loop_all_contract()`, a `CONTRACT_INDEX_SPECS` `run-loop-all` entry, `agentdeck contract run-loop-all` command, and `docs/contracts/run-loop-all-schema.md`.

### 5. Safety boundary (identical to `run-loop`, preserved)

- Requires `--confirm` AND `config.leader.approval_mode == "autonomous"`; else reject, write nothing.
- Only auto-approves allowlisted approvals within the shared `max_approvals` budget; only dispatches to agents with a running pane AND not already busy; never force-spawns; never captures replies or infers completion.
- One wave then stop. Every auto-approve/dispatch is its own audit event; a `run_loop_all_advanced` summary feeds `agentdeck history`.
- The kernel enforces allowlist/budget/contention/gates — no LLM in the loop.

## Testing

- Reject: missing `--confirm`; mode != autonomous; neither/both of `--plan-id`/`--all`. No writes.
- Two plans, both pending, allowlisted agents, both running panes, budget ≥ 2 → both auto-approved + dispatched; `totals.dispatched == 2`; per-plan `gate == waiting_for_reply`.
- **Contention**: two plans whose first step targets the SAME agent → only the first plan (creation order) dispatches to it; the second records `skipped_contention` for that agent (still approved). Assert the second plan's step is NOT dispatched and the agent got exactly one task this wave.
- **Shared budget**: `max_approvals = 1`, two plans each with a pending allowlisted approval → only one auto-approved across the wave; the other stays pending (`needs_human_approval`).
- Completed plan is excluded from `active_count` and untouched.
- Single-plan `run-loop --plan-id` behavior/tests unchanged.
- `validate_run_loop_all_contract` accepts the example + live output; `agentdeck contract run-loop-all` / `--example` reusable; `contract list` includes `run-loop-all`.
- `run_loop_all_advanced` humanizes in `agentdeck history`.
- Full suite green.

## Resolved decisions

- Round-robin over active plans in creation order; one wave per invocation.
- Skip-on-contention (busy = dispatched-unreplied); busy set seeded pre-wave and grows as the wave dispatches.
- One shared `max_approvals` budget across the wave.
- `run-loop --all` (extend the existing command), reusing all run-loop wave primitives; single-plan behavior untouched.
- New `run-loop-all` response contract + discovery, mirroring the run-loop contract.
