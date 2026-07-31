# Run Loop Contract

`agentdeck run-loop --plan-id <id> --confirm` is the **write** counterpart to the read-only `agentdeck loop`. It drives one plan forward within the pre-authorized autonomous policy: it auto-approves allowlisted pending approvals within budget, dispatches every approved-and-ready approval to a running pane, then diagnoses where the plan is now stuck and stops there with an explicit next human command. Every action is audited into `agentdeck history`.

`agentdeck run-loop` is distinct from two read-only siblings:

- `agentdeck loop` — a read-only advisor that recommends the next explicit command and mutates nothing.
- `agentdeck run --plan-id <id>` — a read-only `run_progress` card.

`run-loop` is the only one of the three that performs the sanctioned autonomous wave.

## Safety boundary

- Requires an explicit `--confirm` **and** `config.leader.approval_mode == "autonomous"`. Without either, it rejects and writes nothing.
- Only performs actions already sanctioned elsewhere: auto-approve within the stored allowlist + count budget (reusing `select_auto_approvals`), and dispatch approved items to running panes (reusing the `approval dispatch-ready` internals). It invents no new authority.
- Never force-spawns an agent. Approvals whose target agent has no running pane stay approved-but-`blocked` and are reported (the human is handed the explicit `agentdeck agent spawn --agent <id>` command).
- Never captures replies from panes and never infers task completion. The only completion signal run-loop accepts is the worker's own explicit file-channel announcement, and ingestion is decoupled from the gate: each wave computes the plan's awaiting set (dispatched approvals whose `message_id` has no recorded reply) and ingests every awaiting message whose `.agentdeck/replies/<message_id>.reply.txt` already exists with a structured reply (reusing the capture-reply file path: `record_reply`, artifacts, `reply_captured` event with `captured_from=file` and `pane_id=null`, plus one `run_loop_reply_captured` summary event per ingested reply), then re-diagnoses the gate once and stops there. The current `stopped_reason` never masks ingestion — a `blocked` or `needs_human_approval` gate does not prevent a ready reply from being ingested in the same wave. Ingestion is bounded by the awaiting set; it never reads pane output, never sends tmux input, and never guesses. Ingestion runs before dispatch, so a step completed via the file channel unlocks the next step within the same wave.
- Sequential plan semantics: a wave dispatches approved approvals for the earliest incomplete step only. A step counts as complete when its approval is rejected, or dispatched with a recorded reply. Later approved steps stay `approved` and are reported in `skipped[]` with `reason="awaiting earlier step completion"`. When a wave held later steps, nothing actually blocked, and the earliest step still awaits its reply, the gate reports `waiting_for_reply` for that earlier step (with its explicit capture command) instead of recommending a dispatch of an intentionally held approval. Ingested replies are reported in the optional `captured_replies[]` field (`message_id`, `reply_id`, `agent_id`, `captured_from`, `trace_command`). When the reply file does not exist (or holds no structured reply), behavior is unchanged: run-loop stops at `waiting_for_reply`, hands the explicit `agentdeck capture-reply --agent <id> --message-id <id>` command to the human, and carries the optional read-only `reply_file_ready` field (`false` in that state). `RUN_LOOP_RESPONSE_FIELDS`, the required set, is unchanged; `captured_replies` and `reply_file_ready` are optional derived fields. This file-channel ingestion boundary was explicitly human-approved on 2026-07-25 after the file channel passed live verification with both codex and Claude Code workers.
- One invocation performs at most one auto-approve + dispatch wave, then stops at the resulting human gate. The human re-runs it after clearing each gate.
- Every auto-approve and dispatch is its own audit event (`approval_decided` with `source=autonomous`, `approval_dispatched`); a `run_loop_advanced` summary event feeds `agentdeck history`. The program kernel enforces the gates — no LLM is in the loop.

## Discovery

```bash
agentdeck contract run-loop
agentdeck contract run-loop --example
```

Reusable helpers live in `src/agentdeck/contracts.py`:

- `run_loop_contract_payload()`
- `run_loop_contract_response()`
- `run_loop_example()`
- `validate_run_loop_contract()`

The pure gate-diagnosis function `run_loop_gate(review, has_error, plan_id)` lives in `src/agentdeck/autonomy.py`.

## Response Fields

`run_loop_response_fields` describes the live `agentdeck run-loop --plan-id <id> --confirm` response:

- `ok`
- `mode`
- `plan_id`
- `requires_explicit_user`
- `safety`
- `auto_approved`
- `dispatched`
- `blocked`
- `skipped`
- `stopped_reason`
- `next_command`
- `policy`

`mode` must be `run_loop`. `safety` must be `delegated`, and `requires_explicit_user` must be `true`. `auto_approved` is the count of pending approvals auto-approved this wave. `dispatched[]`, `blocked[]`, and `skipped[]` reuse the `agentdeck approval auto` result shapes (`dispatched` items carry `approval_id`/`agent_id`/`message_id`/`trace_command`; `blocked` items carry `approval_id`/`agent_id`/`blocker`; `skipped` items carry `approval_id`/`agent_id`/`reason`). `policy` echoes the stored `allowed_agents` and `max_approvals`.

## Review-iteration hook (optional `review_iterations[]`)

Each wave, right after file-channel reply ingestion and before dispatch, the engine calls the single writer `StateStore.append_review_iteration(plan_id, effective_rounds, source="run_loop")` (see `docs/contracts/plan-rework-schema.md` / `agentdeck contract plan-rework`). `effective_rounds` defaults to `config.autonomous.max_review_rounds` and can be overridden per invocation with `--max-review-rounds <n>` (an integer `>= 0`; `0` disables the hook entirely for that run — negative values are rejected before any state effect). When the plan's latest review reply carries a `fail`/`needs_changes` verdict that has not already triggered an iteration and the round budget is not exhausted, the hook appends a deterministic rework + re-review step pair and their **pending** approvals, then returns. The appended approvals are not auto-approved this same wave (selection already happened earlier in the wave) — the next wave's existing auto-approve + step-order guard picks them up like any other pending approval.

The optional `review_iterations[]` field is present only when the hook actually appended a round or was refused specifically for `rounds_exhausted`:

- On append: one item `{"round": <int>, "steps": [<int>, <int>], "approval_ids": [<str>, <str>], "triggered_by_reply": <reply_id>}`.
- On budget exhaustion: one item `{"skipped": "rounds_exhausted"}`.
- Any other refusal (`no_plan`, `no_verdict`, `verdict_pass`, `already_triggered`, `no_implementation_step`, or the hook being disabled via `--max-review-rounds 0`) is silent — `review_iterations` is absent from the payload, and the wave is byte-identical to a run with no review-iteration hook at all.

`RUN_LOOP_RESPONSE_FIELDS`, the required set, is unchanged; `review_iterations` is an optional derived field validated only when present (`validate_run_loop_contract()` requires it to be a list). `run-loop --all` carries the same optional field per plan item, including for a plan whose gate was already `complete` before the hook ran (2026-07-31 fix — see `docs/contracts/run-loop-all-schema.md`).

## Stop reasons

`stop_reasons` enumerates the `stopped_reason` values, each mapped to an explicit `next_command` for the human. Priority: `error` first, then the single `leader review` `next_action` determines the reason.

| `leader review` next_action | `stopped_reason` | `next_command` |
|---|---|---|
| a dispatch errored this run | `error` | `agentdeck plan status --plan-id <id>` |
| `dispatch_approved` still present (approved step could not dispatch — agent not running) | `blocked` | `agentdeck agent spawn --agent <id>` |
| `wait_for_approval` (pending, non-auto) | `needs_human_approval` | `agentdeck approval list` |
| `wait_for_reply` | `waiting_for_reply` | `agentdeck capture-reply --agent <id> --message-id <id>` |
| `summarize` / complete | `complete` | `agentdeck leader summary --plan-id <id>` |
| nothing actionable | `idle` | `agentdeck run --plan-id <id>` |

## Follow mode (bounded multi-wave)

`agentdeck run-loop --plan-id <id> --confirm --follow --max-waves <n>
--interval <seconds> [--release-boxes]` repeats single waves in the foreground
until the gate is anything other than `waiting_for_reply` (a human gate,
`complete`, `error`, or `idle`) or the `--max-waves` bound is reached — **with
one walk-away-chain exception (2026-07-31)**: if the wave that just finished
appended a review-iteration round (its `review_iterations[]` carries an item
with `round`, not merely a `{"skipped": "rounds_exhausted"}` entry), follow
keeps walking for one more wave even though that wave's own gate is honestly
reported as something other than `waiting_for_reply` (typically
`needs_human_approval`, since the newly appended rework/re-review approvals
start `pending`). This lets the sanctioned next wave's existing auto-approve +
dispatch pick up the appended rework approval itself, matching the frozen
spec chain (fail → append → next wave approves+dispatches rework → … →
`complete` → merge) instead of stranding the run at a gate a human would just
re-run past. Gate honesty is never altered by this: each wave's own
`stopped_reason` in `waves[]` is exactly what that wave produced; only
follow's continue-vs-stop decision differs, and it remains strictly bounded
by `--max-waves` as always (a round-appending wave still counts against the
budget like any other). `--follow` supports `--plan-id` only (`--all
--follow` is refused), inherits the same `--confirm` +
`approval_mode=autonomous` double gate, and refuses `--max-waves < 1`. Each
wave is the unchanged single-wave engine (same
auto-approve/ingest/dispatch/review-iteration semantics, same audit events);
between waves,
with `--release-boxes`, it runs one delegation scan at segment start (wave 0 — boxes that appeared between follow segments are otherwise missed; round 11 finding, 2026-07-29) and one delegation scan
(`_scan_release_delegated_boxes`) that releases only delegation-covered
authorization boxes — every release audited as `auth_box_released` with
`source=run_loop_follow`, non-covered boxes never touched (see
`docs/contracts/delegation-schema.md`).

The response (`RUN_LOOP_FOLLOW_RESPONSE_FIELDS`) is `mode=run_loop_follow`
with `max_waves`, `interval`, `release_boxes`, `merge_on_complete`, `waves[]`
(each item is a full single-wave `run_loop` payload plus its 1-based `wave`
number, revalidated by `validate_run_loop_contract()`), `wave_count`,
`released_boxes[]` / `released_box_count`, and the final wave's
`stopped_reason` / `next_command`. With `--merge-on-complete`, when the final
gate is `complete` the response also carries the optional `plan_merge` object
— the `worktree merge-plan` result for this plan (see
`docs/contracts/worktree-schema.md`); on any other gate no merge is attempted.
G5 verdict gate (human-approved 2026-07-28): if the plan carries a
`verdict_summary` whose `overall` is not `pass`, the automatic merge is
withheld and `plan_merge` is instead
`{mode: "verdict_blocked", ok: false, plan_id, blocker, next_command}` where
`next_command` is the explicit `agentdeck worktree merge-plan --plan-id <id>
--confirm` human override; plans without any verdict merge exactly as before.
The explicit human merge command itself is never verdict-gated.
Completion appends one `run_loop_follow_completed` summary event. Discovery
exposes `follow_command_template` and `follow_response_fields`.

## Validation

The live `run-loop` payload is validated by `validate_run_loop_contract()` before printing. On failure it returns non-zero, prints no half-baked JSON, and records a `run_loop_contract_failed` event. The `--follow` payload is validated by `validate_run_loop_follow_contract()` under the same rule (each nested wave revalidates against the single-wave contract).
