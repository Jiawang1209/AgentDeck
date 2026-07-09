# Run-loop-all (parallel scheduler) contract — `run-loop-all/v1`

Discovery entry: `agentdeck contract run-loop-all` (`--example` appends a stable GUI-ready example).
Source of truth: `src/agentdeck/contracts.py` (`RUN_LOOP_ALL_RESPONSE_FIELDS`, `RUN_LOOP_ALL_PLAN_FIELDS`, `run_loop_all_example`, `run_loop_all_contract_payload/response`, `validate_run_loop_all_contract`). Command: `src/agentdeck/cli.py::_run_loop_all` (`agentdeck run-loop --all --confirm`).

## What it is

`agentdeck run-loop --all --confirm` is the **parallel scheduler** — the write counterpart to `run-loop --plan-id`, applied across every active plan in **one round-robin wave**. It is the first write-capable multi-plan command. Single-plan `run-loop --plan-id` behavior is unchanged; the scheduler is additive code reusing the same wave primitives (`select_auto_approvals`, `_approval_dispatch_preview_card`, `_dispatch_approved_approval`, `run_loop_gate`).

## Behavior (resolved product decisions)

- **Round-robin**: iterate active plans (gate `!= complete`) in **creation order**, advancing each once.
- **Shared budget**: one `[autonomous].max_approvals` budget is shared across the whole wave (total auto-approvals ≤ budget); `budget = {max_approvals, used, remaining}`.
- **Skip-on-contention**: an agent with a **dispatched-but-unreplied** step (across all plans) is "busy"; the wave does not send it another task — such steps are recorded in the plan's `skipped_contention[]` (left approved), not queued. The busy set is seeded before the wave and grows as the wave dispatches.
- **One wave then stop**: exactly one round-robin pass per invocation; the human re-runs for another wave.

## Response fields (`RUN_LOOP_ALL_RESPONSE_FIELDS`)

`ok`, `mode` (`run_loop_all`), `requires_explicit_user` (`true`), `safety` (`delegated`), `plan_count`, `active_count` (== `len(plans)`), `budget` (`{max_approvals, used, remaining}`, with `used + remaining == max_approvals`), `totals` (`{auto_approved, dispatched, blocked, skipped_contention}`), `plans[]`.

## Per-plan item (`RUN_LOOP_ALL_PLAN_FIELDS`)

`plan_id`, `task`, `auto_approved` (count), `dispatched[]` (approval_id/agent_id/message_id/trace_command), `blocked[]` (approval_id/agent_id/blocker), `skipped[]` (approval_id/agent_id/reason — non-allowlisted / over-budget), `skipped_contention[]` (approval_id/agent_id/blocker=`agent busy this wave`), `gate` (one of the run-loop gates: error/blocked/needs_human_approval/waiting_for_reply/complete/idle), `next_command` (explicit per-plan next step).

## Safety boundary (identical to `run-loop`)

- Requires `--confirm` **and** `config.leader.approval_mode == "autonomous"`; else rejects, writes nothing. Requires exactly one of `--plan-id` / `--all`.
- Only auto-approves allowlisted approvals within the shared budget; only dispatches to agents with a running pane AND not already busy; never force-spawns; never captures replies or infers completion; one wave then stops.
- Every auto-approve/dispatch is its own audit event; a `run_loop_all_advanced` summary event feeds `agentdeck history`. The program kernel enforces allowlist/budget/contention/gates — no LLM in the loop.

Output is self-validated by `validate_run_loop_all_contract()` before printing.
