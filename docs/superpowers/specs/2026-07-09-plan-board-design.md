# Multi-plan overview (`agentdeck plan board`) — Design

- **Date**: 2026-07-09
- **Status**: Approved (pending spec review)

## Context

First slice of the multi-plan-parallel lane (the human picked "多个计划同屏可见、分别推进" — see all active plans at once and drive any of them separately). The state layer already stores multiple plans (`list_plans`, `plan_by_id`, `plan_status`, `leader_review` are all per-plan), and the operator commands (`run-loop --plan-id`, `approval …`, `capture-reply …`) are already plan-scoped. The gap is purely **visibility**: nearly every read-only surface defaults to the single latest plan (`plans[-1]` in `run_progress`, `leader_summary`, `learning_review`, workbench `latest_plan`, recovery). This slice adds a read-only board that shows **every** plan with its current gate and the explicit next command, so the operator can glance at "A waits on my approval, B waits on a reply, C is done" and drive any of them.

## Goal

`agentdeck plan board` returns a read-only, contract-validated overview: one row per plan with its derived gate and explicit per-plan next command, plus active/done counts. It reuses the existing read-only `leader_review` and the pure `run_loop_gate` (sub-project 3) for each plan; it calls no provider, reads no tmux, and writes no state.

## Non-goals

- No parallel scheduler / auto-advance across plans (that is the next lane slice — "option B").
- No agent-contention logic.
- No mutation: it only aggregates existing read-only per-plan facts.
- No wiring into workbench/dashboard/TUI/recovery/natural-language yet — those are named follow-up slices (see below).

## Design

### 1. Per-plan row (reuse, don't reinvent)

For each plan in `store.list_plans()`:

```python
review = store.leader_review(plan_id)                 # read-only, already exists
gate, next_command = run_loop_gate(review, False, plan_id)   # pure, agentdeck.autonomy
```

`run_loop_gate` maps the plan's `leader_review.next_action` to the operator's next step, exactly what a board row needs:

| review next_action | gate | next_command |
|---|---|---|
| `dispatch_approved` | `blocked` | `agentdeck agent spawn --agent <id>` |
| `wait_for_approval` | `needs_human_approval` | `agentdeck approval list` |
| `wait_for_reply` | `waiting_for_reply` | `agentdeck capture-reply --agent <id> --message-id <msg_id>` |
| `summarize` | `complete` | `agentdeck leader summary --plan-id <id>` |
| else | `idle` | `agentdeck run --plan-id <id>` |

A plan is `active` iff `gate != "complete"`.

### 2. `agentdeck plan board` command (read-only)

Output payload (self-validated by `validate_plan_board_contract()` before printing; on failure return non-zero and print nothing half-baked):

- `ok`, `mode="plan_board"`
- `board_command = "agentdeck plan board"`
- `plan_count`, `active_count`
- `plans[]`, each item:
  - `plan_id`, `task`, `provider_backend`, `created_at`, `status` (from the plan record)
  - `gate`, `next_command` (from `run_loop_gate`)
  - `active` (bool), `counts` (from `leader_review.counts`)

Empty project (no plans) → `plan_count=0`, `plans=[]`, still valid (not an error).

### 3. Contract discovery (project convention)

- `docs/contracts/plans-schema.md`
- `agentdeck contract plans` (+ `--example`) in `src/agentdeck/contracts.py`: `PLAN_BOARD_RESPONSE_FIELDS`, `PLAN_BOARD_ITEM_FIELDS`, `plan_board_example()`, `plan_board_contract_payload/response`, `validate_plan_board_contract()`, registered in `CONTRACT_INDEX_SPECS` and discoverable via `agentdeck contract list`.

### 4. Safety boundary (preserved)

- Pure read-only aggregation of existing per-plan facts. No provider call, no tmux read/write, no state mutation, no chat turn/event.
- Every "next_command" is an explicit human command (the same ones the operator already runs); the board only surfaces them, it never executes.

## Named follow-up slices (NOT in this slice)

- Surface the board as a workbench `plan_board_card`; a dashboard **Plans** section; a TUI plans view (mirroring the approvals/runtime views).
- Make `recovery` multi-plan aware (recommend across plans, not just the latest).
- A natural-language `leader chat` "查看所有计划 / 计划看板" intent.
- Then the parallel scheduler (auto-advance across plans + agent contention) — the bigger lane slice.

## Testing

- `agentdeck plan board` with two plans at different gates → `plan_count=2`, correct per-plan `gate`/`next_command`, `active` flags, `active_count`; output passes `validate_plan_board_contract()`.
- Empty project → `plan_count=0`, `plans=[]`, valid, exit 0.
- Read-only: state (plans/approvals/events) unchanged after running the board.
- Contract: `validate_plan_board_contract()` accepts the example and the live payload; `agentdeck contract plans` / `--example` reusable without CLI; `agentdeck contract list` includes `plans`.
- A completed plan (all dispatched steps have replies) shows `gate=complete`, `active=false`, and is excluded from `active_count`.
- Full suite stays green.

## Resolved decisions

- Read-only "plan board" first; wiring into workbench/dashboard/TUI/recovery/NL and the parallel scheduler are later slices.
- Reuse `leader_review` + the pure `run_loop_gate` for per-plan gate + next command (no new mapping).
- New `agentdeck plan board` subcommand under the existing `plan` command; new `agentdeck contract plans` discovery entry.
