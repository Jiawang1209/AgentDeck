# Plan rework contract

Discovery entrypoint:

```bash
agentdeck contract plan-rework
agentdeck contract plan-rework --example
```

`agentdeck plan rework --plan-id <id> --confirm` is the explicit, human-triggered
entrypoint into the review-driven iteration loop. It is the write-path CLI
wrapper around the pure derivation module `src/agentdeck/review_iteration.py`
and the locked writer `StateStore.append_review_iteration()`; it does not
duplicate their logic. See
`docs/superpowers/specs/2026-07-30-review-iteration-loop-design.md` for the
loop design this command participates in.

When a plan's most recent review verdict is `fail` or `needs_changes`, this
command appends exactly one deterministic rework step and one re-review step
to the plan (as ordinary pending approvals) so a human — or later, the
run-loop engine — can approve and dispatch the next round.

## Response (`mode=plan_rework`)

| Field | Meaning |
| --- | --- |
| `ok` | `true` on success |
| `mode` | fixed `plan_rework` |
| `plan_id` | the plan the rework pair was appended to |
| `round` | 1-based iteration round number this append represents |
| `steps` | the two new plan step numbers, `[rework_step, review_step]` |
| `approval_ids` | the two new pending approval ids, aligned with `steps` |
| `triggered_by_reply` | the reviewer reply id whose verdict triggered this append |
| `next_command` | always `agentdeck approval list` |
| `requires_explicit_user` | always `true` |
| `safety` | always `explicit_user` |

## Closed `skip_reasons` enum

Refusals are reported on stderr as `plan rework refused: <reason>` and are
always zero-write (no state mutation, no event appended, non-zero exit).

| Reason | Meaning | Follow-up |
| --- | --- | --- |
| `no_plan` | unknown `--plan-id`, or the plan record has no well-formed `plan.steps` | check `agentdeck plan list` / `agentdeck plan show` |
| `no_verdict` | no reviewer reply with a structured `verdict` exists for this plan yet | wait for / capture the review reply first |
| `verdict_pass` | the latest verdict's `overall` is not in `trigger_overalls` (e.g. it is `pass`) | nothing to rework — proceed normally |
| `already_triggered` | the triggering reply has already produced a rework/review step pair | inspect `agentdeck plan status --plan-id <id>` for the existing pair |
| `rounds_exhausted` | appending would exceed `[autonomous] max_review_rounds` | a human must raise the budget in `.agentdeck/config.toml`, never silently overridden |
| `no_implementation_step` | no prior dispatched implementation step could be identified to target for rework | inspect the plan's approval/message history |

## `trigger_overalls`

`["fail", "needs_changes"]` — any other `overall` value (e.g. `pass`) refuses
with `verdict_pass`.

## Safety boundaries

- Requires explicit `--confirm`; omitting it refuses with `plan rework
  requires --confirm` on stderr and mutates nothing.
- Appends exactly one rework step and one re-review step, both as ordinary
  `status=pending` approvals — this command never auto-approves and never
  dispatches.
- Never calls a Leader/worker provider, never reads or writes tmux panes,
  never captures pane output.
- Idempotent per triggering reply: a second call against the same
  already-consumed reviewer reply refuses with `already_triggered` rather than
  appending a duplicate pair.
- Bounded by `[autonomous] max_review_rounds` (see
  `docs/contracts/run-loop-schema.md` for the sibling autonomous-mode
  boundaries); exceeding the budget is a hard refusal (`rounds_exhausted`),
  never a silent skip or override — only a human editing config can raise it.
- Provenance fields recorded on the appended steps (`origin`, `round`,
  `triggered_by_reply`) and surfaced via ProjectView `plans.items[].review_rounds`
  are audit data only, never an authorization signal.

## Related contracts

- `agentdeck contract run-loop` — the wave engine this rework pair feeds into
  once approved and dispatched.
- `agentdeck contract approvals` — the queue the two new approvals land in.
