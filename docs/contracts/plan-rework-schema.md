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
| `refined` | `true` only when the appended rework task text came from the Leader provider via `--refine`; `false` on every template append (including every fallback) |
| `next_command` | always `agentdeck approval list` |
| `requires_explicit_user` | always `true` |
| `safety` | always `explicit_user` |
| `refine_skipped_reason` | optional, present only when a refinement was attempted and did not land — one of the closed `refine_skip_reasons` enum below |

## Optional Leader refinement (`--refine`)

`agentdeck plan rework --plan-id <id> --confirm --refine` asks the configured
`[leader]` provider to distill the review feedback into one focused rework
task. It is an **explicit-only** enhancement:

- `--refine` must accompany `--confirm`; `--refine` alone refuses, mutates
  nothing and never constructs a provider.
- The trigger conditions are evaluated **first**, lock-free: a refusal
  (`verdict_pass`, `no_plan`, …) is reported exactly as on the template path
  and costs zero provider calls.
- The provider is called **once** — no retries, so the command never becomes
  an open-ended wait. The call happens entirely **outside** the state mutation
  lock; the locked writer re-derives the iteration itself and adopts the
  refined text only when it reproduces the same `triggered_by_reply`.
- The refined text only ever becomes the rework step's `task`. The step's
  agent, role, numbering and provenance (`origin` / `round` /
  `triggered_by_reply` / `iteration_kind`) are byte-identical to the template
  path, the fixed "commit to the task branch" instruction is appended by the
  program (never trusted to the model), and the re-review step's task is still
  a verbatim reuse of the original review step.
- A landed refinement marks the rework step with `task_source:
  leader_refined` (the template path omits the key, i.e. absent == template).
  This is read-only provenance, never authorization.
- **Provider failure falls back to the deterministic template and the command
  still succeeds (exit 0).** An iteration must not be blocked by provider
  flakiness — the template rework task is itself a usable product. Provider
  exceptions are still audited via `leader_errors[]` and a
  `leader_provider_failed` event with `mode=plan_rework_refine`; only the
  closed reason code is reported, never the provider's raw output.
- **run-loop never refines.** `run-loop`, `run-loop --all`, `run-loop
  --follow` and `run-loop-host` reach the same iteration hook without any
  refine entry point — the live-verified "run-loop never calls a Leader
  provider" invariant is fully preserved, so the walk-away autonomous segment
  gains no new failure surface.

### Closed `refine_skip_reasons` enum

| Reason | Meaning |
| --- | --- |
| `unsupported_provider` | the configured Leader provider cannot be constructed, or does not implement `refine_rework_task` |
| `provider_error` | the provider raised / timed out (audited as `leader_provider_failed`, `mode=plan_rework_refine`) |
| `invalid_output` | the returned text was not a usable rework task (empty, non-string, or over `MAX_REWORK_TASK_CHARS` once the fixed footer is appended) |
| `state_changed` | state drifted between the lock-free preview and the in-lock derivation (a newer verdict arrived); a refinement of a superseded verdict is never pinned onto the new round |

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
- Never reads or writes tmux panes, never captures pane output. Without
  `--refine` it never calls any provider either; with the explicit `--refine`
  flag it calls the configured Leader provider exactly once, outside the state
  mutation lock, only to rewrite the rework task text (see "Optional Leader
  refinement" above).
- Idempotent per triggering reply: a second call against the same
  already-consumed reviewer reply refuses with `already_triggered` rather than
  appending a duplicate pair.
- Bounded by `[autonomous] max_review_rounds` (see
  `docs/contracts/run-loop-schema.md` for the sibling autonomous-mode
  boundaries); exceeding the budget is a hard refusal (`rounds_exhausted`),
  never a silent skip or override — only a human editing config can raise it.
- Provenance fields recorded on the appended steps (`origin`, `round`,
  `triggered_by_reply`, `iteration_kind`) and surfaced via ProjectView
  `plans.items[].review_rounds` are audit data only, never an authorization
  signal.
- Rework self-verdict exclusion (2026-08-01): appended steps carry
  `iteration_kind` (`rework` | `review`). A rework step's dispatch prompt never
  receives the acceptance-criteria / verdict-format injection, and a `verdict:`
  line in a rework step's reply is excluded — via the same
  `rework_step_numbers` source — from BOTH the iteration trigger and
  `plan_verdict_summary`. The implementing agent's self-assessment can neither
  spawn a phantom iteration round nor become the plan's review verdict; only
  review-side replies judge the plan.

## Related contracts

- `agentdeck contract run-loop` — the wave engine this rework pair feeds into
  once approved and dispatched.
- `agentdeck contract approvals` — the queue the two new approvals land in.
