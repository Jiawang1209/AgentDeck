# Run-loop-all (parallel scheduler) contract — `run-loop-all/v1`

Discovery entry: `agentdeck contract run-loop-all` (`--example` appends a stable GUI-ready example).
Source of truth: `src/agentdeck/contracts.py` (`RUN_LOOP_ALL_RESPONSE_FIELDS`, `RUN_LOOP_ALL_PLAN_FIELDS`, `run_loop_all_example`, `run_loop_all_contract_payload/response`, `validate_run_loop_all_contract`). Command: `src/agentdeck/cli.py::_run_loop_all` (`agentdeck run-loop --all --confirm`).

## What it is

`agentdeck run-loop --all --confirm` is the **parallel scheduler** — the write counterpart to `run-loop --plan-id`, applied across every active plan in **one round-robin wave**. It is the first write-capable multi-plan command. Single-plan `run-loop --plan-id` behavior is unchanged; the scheduler is additive code reusing the same wave primitives (`select_auto_approvals`, `_approval_dispatch_preview_card`, `_dispatch_approved_approval`, `run_loop_gate`).

## Behavior (resolved product decisions)

- **Round-robin**: iterate active plans (gate `!= complete`) in **creation order**, advancing each once.
- **Shared budget**: one `[autonomous].max_approvals` budget is shared across the whole wave (total auto-approvals ≤ budget); `budget = {max_approvals, used, remaining}`.
- **Skip-on-contention**: an agent with a **dispatched-but-unreplied** step (across all plans) is "busy"; the wave does not send it another task — such steps are recorded in the plan's `skipped_contention[]` (left approved), not queued. The busy set is seeded before the wave and grows as the wave dispatches.
- **Sequential steps within a plan**: each plan's wave dispatches approved approvals for that plan's earliest incomplete step only (complete = rejected, or dispatched with a recorded reply). Later approved steps stay `approved` and are reported in the plan's `skipped[]` with `reason="awaiting earlier step completion"`. **This is deliberately *not* the single-plan guard any more**: the 2026-08-03 DAG slice moved `run-loop --plan-id` to dependency satisfaction (so a review group fans out in one wave) and scoped itself to the single-wave engine — cross-plan parallelism is an explicit non-goal of that spec, and `_run_loop_all` is untouched. The consequence to know: under `--all` a review group still runs one member per wave. The two guards agree on every plan without review groups.
- **One wave then stop**: exactly one round-robin pass per invocation; the human re-runs for another wave.
- **File-channel ingestion (parity with single-plan waves)**: each per-plan wave ingests explicit file-channel replies before dispatch, bounded by that plan's awaiting set (dispatched-but-unreplied messages), via the same shared `_ingest_plan_reply_files` path as single-plan `run-loop` (`record_reply` + `reply_captured` `captured_from=file` + one `run_loop_reply_captured` event per reply; never reads the pane). Ingested replies appear in the plan item's optional `captured_replies[]` field, and a completed step can unlock that plan's next step within the same wave.

## Response fields (`RUN_LOOP_ALL_RESPONSE_FIELDS`)

`ok`, `mode` (`run_loop_all`), `requires_explicit_user` (`true`), `safety` (`delegated`), `plan_count`, `active_count` (== `len(plans)`), `budget` (`{max_approvals, used, remaining}`, with `used + remaining == max_approvals`), `totals` (`{auto_approved, dispatched, blocked, skipped_contention}`), `plans[]`.

## Per-plan item (`RUN_LOOP_ALL_PLAN_FIELDS`)

`plan_id`, `task`, `auto_approved` (count), `dispatched[]` (approval_id/agent_id/message_id/trace_command), `blocked[]` (approval_id/agent_id/blocker), `skipped[]` (approval_id/agent_id/reason — non-allowlisted / over-budget), `skipped_contention[]` (approval_id/agent_id/blocker=`agent busy this wave`), `gate` (one of the run-loop gates: error/blocked/needs_human_approval/waiting_for_reply/complete/idle), `next_command` (explicit per-plan next step).

### Optional per-plan `review_iterations[]`

Mirrors the single-plan `run-loop --plan-id` review-iteration hook (see `docs/contracts/run-loop-schema.md`): after that plan's file-channel reply ingestion and before its dispatch step, the scheduler calls the same single writer `StateStore.append_review_iteration(plan_id, effective_rounds, source="run_loop")` for **each** active plan in the wave. `effective_rounds` defaults to `config.autonomous.max_review_rounds`, overridable for the whole invocation with `--max-review-rounds <n>` (`0` disables it for every plan this run; negative values are rejected before any state effect). The field is present on a plan item only when that plan's hook appended a round (`{"round", "steps", "approval_ids", "triggered_by_reply"}`) or was refused with `rounds_exhausted` (`{"skipped": "rounds_exhausted"}`); any other refusal leaves the field absent for that plan, matching current behavior byte-for-byte. As with the single-plan hook, appended approvals from this per-plan-wave call stay **pending** this wave — auto-approve/dispatch semantics for this scheduler are otherwise unchanged.

**Pre-gate exception (2026-07-31 important-fix).** A plan whose gate was already `complete` *before* the wave starts (every step dispatched and replied, nothing pending) used to be skipped outright — `continue`d before any hook logic ran — so an already-ingested fail/needs_changes verdict on such a plan would never trigger rework via `--all`, diverging from the single-plan engine (which never pre-gate-skips). Now, for a plan whose pre-wave gate is `complete` and `effective_rounds > 0`, the scheduler calls `append_review_iteration` **first**, before deciding whether to skip: if it returns `ok`, the plan is **not** skipped — it proceeds through the plan's normal per-plan wave (auto-approve selection, ingestion, dispatch) with that round already recorded in `review_iterations[]`, and because the hook ran before selection this time, the newly appended rework/re-review approvals **can** be auto-approved and dispatched in this same wave (unlike the mid-wave case, where selection already happened before the hook runs). The hook is never invoked twice for the same plan in the same wave. Any refusal (`no_verdict`, `verdict_pass`, `already_triggered`, `rounds_exhausted`, `no_implementation_step`, or `effective_rounds <= 0`) skips the plan exactly as before — silent, no `plans[]` entry — so genuinely complete plans are unaffected byte-for-byte.

## Safety boundary (identical to `run-loop`)

- Requires `--confirm` **and** `config.leader.approval_mode == "autonomous"`; else rejects, writes nothing. Requires exactly one of `--plan-id` / `--all`.
- Only auto-approves allowlisted approvals within the shared budget; only dispatches to agents with a running pane AND not already busy; never force-spawns; never captures replies or infers completion; one wave then stops.
- Every auto-approve/dispatch is its own audit event; a `run_loop_all_advanced` summary event feeds `agentdeck history`. The program kernel enforces allowlist/budget/contention/gates — no LLM in the loop.

Output is self-validated by `validate_run_loop_all_contract()` before printing.
