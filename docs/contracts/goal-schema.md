# Goal (one-shot walk-away) contract

Discovery entrypoint:

```bash
agentdeck contract goal
agentdeck contract goal --example
```

`agentdeck goal preview --task <text>` → `agentdeck goal start --plan-id <id>
--confirm` compresses the four commands and nine flags it used to take to climb
to the top of the autonomy ladder into **two steps and one information-complete
confirmation**. The principle is the user's: **compress the confirmations, do
not remove them**. Of the nine flags, the *ceremony* (hand-carrying `plan_id`,
guessing `interval`, confirming the same thing four times) is gone; every
*safety gate* (`--confirm`, the mandatory bounded `--max-waves`, the autonomous
allowlist, the approval budget, the review-round budget, the step-ordering
guard, file-channel replies, `human_gate` stops) is inherited unchanged.

Design spec: `docs/superpowers/specs/2026-08-01-goal-one-shot-walkaway-design.md`.

## `goal` adds no new kind of action

It is only a caller of three commands that already exist and are each already
gated:

| Stage | Reused command |
| --- | --- |
| `goal preview` | `agentdeck leader plan --task <text>` (same planning path) |
| `goal start` (0) | `agentdeck approval create-from-plan --plan-id <id>` — only when the plan has no approvals yet |
| `goal start` (1) | `agentdeck approval approve-plan --plan-id <id> --confirm` |
| `goal start` (2) | `agentdeck run-loop-host start --plan-id <id> --confirm --max-waves <n>` |

Stage 0 exists because the by-hand ladder is really five commands, not four:
`approve-plan` only approves *existing* pending approvals, so a plan straight
out of `leader plan` has nothing to approve until `create-from-plan` runs. When
the plan already has approvals, `goal start` touches nothing there — not even
an audit event.

Those three implementations are **called, never copied**, and their own
behaviour is untouched. Everything after `goal start` is carried by the
**unchanged** host wave engine (`agentdeck contract run-loop-host`).

## The most important boundary: `goal` never flips `approval_mode`

`approval_mode` is a standing policy decision, not a per-goal one. Trading a
long-lived policy switch for one goal's confirmation is exactly the slippery
slope this design exists to avoid. So when the project is not in `autonomous`
mode, `goal preview`:

- reports a non-empty `blocker` containing the explicit command a **human**
  must run: `agentdeck policy set-mode --mode autonomous --confirm
  --allow-agent <id> --max-approvals <N>`;
- sets `confirm_command` to `null` (the validator refuses any payload that
  carries a blocker *and* a confirm command);
- disables the `next` control and carries the same blocker on it;
- creates no approval, changes no config, and starts no host.

## Two steps, not one

The repository's standing boundary is *only the exact confirmed preview becomes
frozen authority*. So `goal start` binds to a `--plan-id` the human has seen in
a preview — never to a raw sentence. `goal preview` writes a plan (equivalent
to `leader plan`) but approves nothing, dispatches nothing and starts no host.

## Preview response (`mode=goal_preview`)

| Field | Meaning |
| --- | --- |
| `ok` | `true` on success |
| `mode` | fixed `goal_preview` |
| `task` | the goal sentence, echoed |
| `plan_id` | the plan just written by the configured Leader |
| `step_count` | must equal `len(steps)` |
| `steps[]` | compact `step` / `agent_id` / `role` / `task` |
| `budget` | `max_waves`, `max_waves_is_default`, `interval`, `max_review_rounds`, `max_approvals` |
| `delegations[]` | active delegations: `delegation_id` / `agent_id` / `kind` / `prefix` / `mcp_server` / `mcp_tool` |
| `merge_on_complete` | default `false` |
| `release_boxes` | default `true` |
| `stop_conditions[]` | closed list of `kind` / `summary` |
| `blocker` | `null`, or the explicit `policy set-mode` prompt |
| `confirm_command` | the exact `goal start …` line, or `null` behind a blocker |
| `requires_explicit_user` | always `true` |
| `safety` | always `explicit_user` |
| `controls[]` | `kind` / `label` / `command` / `safety` / `enabled` / `blocker` |

### Defaults (user-decided 2026-08-01)

1. **`--max-waves` defaults to 300 — but is never invisible.** The single
   source is `GOAL_DEFAULT_MAX_WAVES` in `src/agentdeck/contracts.py`.
   `budget.max_waves_is_default` is `true` only when the number came from the
   default, and the renderer then prints `↑ wave 上限为缺省值,可用 --max-waves
   改`. The host's frozen "mandatory bounded, no unbounded form" invariant is
   untouched: `goal` always enters the host with a concrete wave ceiling; the
   number may come from a default, it may never be hidden.
2. **`--release-boxes` defaults on, `--merge-on-complete` defaults off.**
   Releasing boxes defaults on because otherwise a delegation you explicitly
   granted is pointless — and it still only releases boxes matching an active
   delegation (anything unmatched still stops honestly at `human_gate`).
   Merging defaults off because **merging into main deserves its own separate
   nod**: the normal terminal state under defaults is "review passed, waiting
   for you to merge", leaving `agentdeck worktree merge-plan --confirm` to the
   human. `--no-release-boxes` and `--merge-on-complete` change these
   explicitly, and both are frozen into `confirm_command`.

### `delegations[]` is display, not authorization

It is a compact read-only projection of the same data source as `agentdeck
delegation list`, shown so the confirmation is information-complete. Rendering
it grants nothing: box release still only matches active delegations, and the
refusal/esc path is never automated.

### `stop_conditions[]` (closed set)

`review_passed_awaiting_merge`, `review_passed_merged`, `human_gate`,
`review_budget_exhausted`, `approval_outside_allowlist`,
`wave_budget_exhausted`. Exactly one `review_passed_*` terminal appears,
selected by `merge_on_complete`; the other four always appear.

## Start response (`mode=goal_start`)

| Field | Meaning |
| --- | --- |
| `ok` | `true` on success |
| `mode` | fixed `goal_start` |
| `plan_id` | the confirmed plan |
| `approved_count` | approvals approved by the reused `approve-plan` stage |
| `host_pid` | detached host pid from the reused `run-loop-host start` stage |
| `max_waves` / `interval` / `release_boxes` / `merge_on_complete` | budget passed through to the host |
| `status_command` | `agentdeck run-loop-host status` |
| `stop_command` | `agentdeck run-loop-host stop --confirm` |
| `next_command` | `agentdeck run-loop-host status` |
| `requires_explicit_user` | always `true` |
| `safety` | always `delegated` |

### Start gates (all five required; any failure ⇒ refuse, zero writes, zero spawn)

1. `--confirm` present.
2. `config.leader.approval_mode == "autonomous"` — and `goal` never flips it.
3. `--plan-id` names a known plan.
4. `--max-waves >= 1` (the 300 default must clear this gate too).
5. This project has **no live run-loop host**. The reused `run-loop-host start`
   refuses a second host on its own, but that refusal happens *after* the
   approve stage — which would leave the approvals approved and no host
   running. So the same liveness probe (`_host_liveness_or_none`, one source,
   not a second rule) runs up front with the other four, and a refusal is never
   preceded by a mutation. The stderr line names the running host's `plan_id`
   and `pid` and points at `agentdeck run-loop-host status` /
   `agentdeck run-loop-host stop --confirm`.
   **A stale record does not block.** A record whose pid is dead is exactly the
   case a fresh `goal start` should be allowed through, matching how
   `run-loop-host start` itself treats stale records; only `running` blocks.

Order is fixed: create (if needed), approve, then start the host. **If the
approve stage fails, the host is not spawned.** If the host fails to start, the already
approved approvals stay approved — identical to running the two commands by
hand — and the failure is reported honestly on stderr with a non-zero exit.

`goal start` appends one `goal_started` audit event (`plan_id`,
`approved_count`, `max_waves`, `interval`, `release_boxes`,
`merge_on_complete`); the host's own `run_loop_host_started` event is still
appended by the reused implementation and is never suppressed.

## Progressive disclosure

Both commands render a human-readable summary by default and print the full
contract payload only with `--json`; both paths are the same data, and the
payload passes its validator before printing. No existing command's default
output is changed by this slice.

Validators: `validate_goal_preview_contract()` and
`validate_goal_start_contract()` in `src/agentdeck/contracts.py`.
